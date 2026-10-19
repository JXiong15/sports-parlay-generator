import numpy as np
import pandas as pd

from betbot.nfl import model

NO_FORM = pd.DataFrame(columns=model.FORM_COLUMNS)
CTX_COLS = ["game_id", "team", "home", "implied_points", "spread", "wind", "temp"]


def synthetic(players=40, games=12, wind_effect=-0.3, seed=1):
    """Players with steady averages; windy games cut the stat by exp(wind_effect * wind feature)."""
    rng = np.random.default_rng(seed)
    rows, ctx = [], []
    for p in range(players):
        avg = rng.uniform(150, 300)
        for g in range(games):
            game_id, wind_mph = f"2020_{g:02d}_P{p}", rng.choice([0, 0, 0, 20, 30])
            feature = max(wind_mph - 10, 0) / 10
            y = avg * np.exp(wind_effect * feature) * rng.normal(1, 0.08)
            rows.append({"player_id": f"p{p}", "player": f"P{p}", "position_group": "QB", "season": 2020,
                         "week": g + 1, "game_id": game_id, "team": f"T{p}", "opponent_team": f"O{g}",
                         "y": y, "usage": 35.0})
            ctx.append([game_id, f"T{p}", 1, 22.0, 0.0, wind_mph, 60.0])
    return pd.DataFrame(rows), pd.DataFrame(ctx, columns=CTX_COLS)


def build(df, ctx, upcoming=None):
    empty_flags = pd.DataFrame(columns=["season", "week", "player_id", "questionable", "limited_practice", "status"])
    empty_teams = pd.DataFrame(columns=["season", "week", "team", "qb_out", "def_starters_out"])
    no_snaps = pd.DataFrame(columns=["player_id", "season", "week", "offense_pct"])
    return model.features(None, "passing_yards", ("QB",), upcoming, df=df, context=ctx, flags=empty_flags,
                          absences=empty_teams, snaps=no_snaps, form=NO_FORM)


def test_features_only_use_earlier_games():
    df, ctx = synthetic(players=1, games=5)
    out = build(df, ctx).sort_values("week")
    ys = out["y"].tolist()
    assert out["prior_n"].tolist() == [0, 1, 2, 3, 4]
    # week 3's baseline average is built from weeks 1-2 only, never week 3 itself
    expected = pd.Series(ys[:2]).ewm(halflife=model.HALFLIFE).mean().iloc[-1]
    assert abs(out["ewm"].iloc[2] - expected) < 1e-9


def test_upcoming_row_uses_all_past_games():
    df, ctx = synthetic(players=1, games=5)
    up = pd.DataFrame([{"player_id": "p0", "player": "P0", "position_group": "QB", "season": 2020, "week": 6,
                        "game_id": "next", "team": "T0", "opponent_team": "O9"}])
    out = build(df, ctx, up)
    row = out[out["game_id"] == "next"].iloc[0]
    assert row["prior_n"] == 5 and np.isnan(row["y"])


def test_fit_recovers_a_known_effect_and_probabilities_are_sane():
    df, ctx = synthetic()
    feats = build(df, ctx)
    params = model.fit(feats, "passing_yards")
    assert abs(params["beta"]["wind"] - (-0.3)) < 0.08  # learned from data, close to the truth
    pred = model.predict(feats, params)
    p_at_mean = model.prob_over(params, pred["mu"], pred["sigma"], pred["mu"])
    assert np.all((p_at_mean > 0.3) & (p_at_mean < 0.7))
    p = model.prob_over(params, 250.0, 30.0, np.array([150.0, 250.0, 350.0]))
    assert p[0] > p[1] > p[2] and p[0] <= 0.99 and p[2] >= 0.01


def test_fit_refuses_too_little_data():
    df, ctx = synthetic(players=2, games=6)
    try:
        model.fit(build(df, ctx), "passing_yards")
    except ValueError as e:
        assert "training rows" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_negative_stats_dont_break_the_fit():
    df, ctx = synthetic()
    df.loc[df.index[::7], "y"] = -6.0  # e.g. a -6 yard rushing day
    params = model.fit(build(df, ctx), "passing_yards")
    assert np.all(np.isfinite(list(params["beta"].values())))


def test_recalibration_shrinks_overconfident_probabilities():
    rng = np.random.default_rng(3)
    true = rng.uniform(0.3, 0.7, 5000)
    won = (rng.uniform(size=5000) < true).astype(int)
    overconfident = 1 / (1 + np.exp(-3 * np.log(true / (1 - true))))  # says 0.85 when truth is 0.65
    a, b = model.fit_recalibration(overconfident, won)
    assert 0.2 < b < 0.5 and abs(a) < 0.1
    fixed = model.recalibrate(overconfident, (a, b))
    assert abs(np.mean(fixed[true > 0.6]) - np.mean(won[true > 0.6])) < 0.03


def test_prob_over_applies_stored_calibration():
    params = {"z_quantiles": list(np.linspace(-3, 3, 401))}
    raw = model.prob_over(params, 250.0, 30.0, 200.0)
    shrunk = model.prob_over(dict(params, calib=[0.0, 0.5]), 250.0, 30.0, 200.0)
    assert 0.5 < shrunk < raw


def test_upcoming_row_gets_snap_share_from_previous_games():
    df, ctx = synthetic(players=1, games=4)
    up = pd.DataFrame([{"player_id": "p0", "player": "P0", "position_group": "QB", "season": 2020, "week": 5,
                        "game_id": "next", "team": "T0", "opponent_team": "O9"}])

    class FakeConn:  # nfl_player_usage: 90% of snaps in every earlier game
        pass
    snaps = pd.DataFrame({"player_id": "p0", "season": 2020, "week": [1, 2, 3, 4], "offense_pct": 0.9})
    import betbot.nfl.model as m
    orig = m.pd.read_sql_query
    m.pd.read_sql_query = lambda sql, conn, **kw: snaps if "nfl_player_usage" in sql else orig(sql, conn, **kw)
    empty_flags = pd.DataFrame(columns=["season", "week", "player_id", "questionable", "limited_practice", "status"])
    empty_teams = pd.DataFrame(columns=["season", "week", "team", "qb_out", "def_starters_out"])
    try:
        out = model.features(FakeConn(), "passing_yards", ("QB",), up, df=df, context=ctx, flags=empty_flags,
                             absences=empty_teams, form=NO_FORM).set_index("game_id")
    finally:
        m.pd.read_sql_query = orig
    assert abs(out.loc["next", "snap_pct"] - 0.9) < 1e-9 and out.loc["next", "starter_flag"] == 1.0  # not 0
    assert np.isnan(out.loc["2020_00_P0", "snap_recent"])  # first game: nothing earlier


def test_snap_shares_loader_filters_seasons_and_survives_missing_tables():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    assert len(model.snap_shares(conn)) == 0   # no nfl_player_usage view: empty, not an error
    conn.execute("CREATE TABLE nfl_player_usage (player_id, season, week, offense_pct)")
    conn.executemany("INSERT INTO nfl_player_usage VALUES (?, ?, ?, ?)",
                     [("a", 2010, 1, 0.5), ("a", 2020, 1, 0.9), ("a", 2020, 1, 0.9), ("b", 2020, 1, None)])
    out = model.snap_shares(conn, 2013)
    assert out.to_dict("records") == [{"player_id": "a", "season": 2020, "week": 1, "offense_pct": 0.9}]


def test_player_without_snap_history_gets_last_seasons_typical_share_not_zero():
    df20, ctx20 = synthetic(players=2, games=4)
    df19, ctx19 = df20.copy(), ctx20.copy()
    df19["season"] = 2019
    df19["game_id"] = df19["game_id"].str.replace("2020_", "2019_")
    ctx19["game_id"] = ctx19["game_id"].str.replace("2020_", "2019_")
    df, ctx = pd.concat([df19, df20], ignore_index=True), pd.concat([ctx19, ctx20], ignore_index=True)
    # p0 is a 90% starter in both seasons; p1 has no snap data at all
    snaps = pd.DataFrame([("p0", s_, w, 0.9) for s_ in (2019, 2020) for w in (1, 2, 3, 4)],
                         columns=["player_id", "season", "week", "offense_pct"])
    empty_flags = pd.DataFrame(columns=["season", "week", "player_id", "questionable", "limited_practice", "status"])
    empty_teams = pd.DataFrame(columns=["season", "week", "team", "qb_out", "def_starters_out"])
    out = model.features(None, "passing_yards", ("QB",), df=df, context=ctx, flags=empty_flags, absences=empty_teams,
                         snaps=snaps, form=NO_FORM)
    p1_2020 = out[(out["player_id"] == "p1") & (out["season"] == 2020)]
    assert (abs(p1_2020["snap_pct"] - 0.9) < 1e-9).all() and (p1_2020["starter_flag"] == 1.0).all()
    # in the first season there is no earlier data to use, so the neutral 0.5 (never a 0)
    p1_2019 = out[(out["player_id"] == "p1") & (out["season"] == 2019)]
    assert (p1_2019["snap_pct"] == 0.5).all()
