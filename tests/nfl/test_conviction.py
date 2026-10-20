import pytest
from tests.nfl.test_endzone import Session, conn_with_players
import numpy as np
import pandas as pd

from betbot import db, parlay, predictions
from betbot.nfl import conviction, grader
from betbot.nfl.collectors.nflverse import replace_table


def row(**kw):
    base = dict(group="pass", sigma=40.0, spread=0.0, margin3=0.0, att3=34.4, league_att=34.4, y_recent3=240.0,
                y_prior_season=240.0, def_vs_pos=0.0, usage_trend=0.0, snap_pct=1.0, questionable=0.0,
                limited_practice=0.0, qb_out=0.0, opp_def_starters_out=0.0, wind=0.0, cold=0.0, implied_points=0.0,
                mu=250.0, opponent_team="LV", team="NO", prior_n=20, base=250.0, pos_mean=200.0)
    base.update(kw)
    return base


def sig(line=240.0, p_over=0.5, **kw):
    return conviction.signals(pd.DataFrame([row(**kw)]), [line], [p_over]).iloc[0]


def test_script_favors_passing_for_a_losing_underdog_and_rushing_for_a_favorite():
    s = dict(spread=1.0, margin3=-12.0, att3=46.0)             # underdog, losing by 12, 46 attempts a game
    assert sig(**s)["script"] > 0.8
    assert sig(group="rec", **s)["script"] == sig(**s)["script"] * 0.7
    assert sig(group="rush", **s)["script"] < -0.8
    assert abs(sig()["script"]) < 1e-9                          # a neutral team: nothing
    assert np.isnan(sig(spread=np.nan, margin3=np.nan, att3=np.nan)["script"])


def test_volume_and_stale_line():
    assert sig(y_recent3=300.0)["volume"] > 0.9 and sig(y_recent3=180.0)["volume"] < -0.9
    # the line sits at last season's 240 average while he has been throwing 300: the line is stale
    assert sig(y_recent3=300.0)["stale_line"] > 0.85
    assert sig(line=300.0, y_recent3=300.0)["stale_line"] == 0       # a line far from last season's: not stale
    assert np.isnan(sig(y_prior_season=np.nan)["stale_line"])        # rookie: not available


def test_matchup_usage_health_weather_and_model():
    assert sig(def_vs_pos=0.2)["matchup"] > 0.9 and sig(def_vs_pos=-0.2)["matchup"] < -0.9
    assert sig(usage_trend=0.5)["usage"] > 0.9 and sig(snap_pct=0.2)["usage"] <= -0.5
    assert sig(questionable=1.0)["health"] == -0.5
    assert sig(group="rec", qb_out=1.0)["health"] == -0.7 and sig(group="pass", qb_out=1.0)["health"] == 0
    assert sig(group="rec", opp_def_starters_out=2.0)["health"] == 0.15 * 2
    assert sig(wind=1.0)["weather"] < -0.9 and sig(group="rush", wind=1.0)["weather"] > 0
    assert sig(p_over=0.8)["model"] > 0.95 and sig(p_over=0.2)["model"] < -0.95


def test_aggregate_rewards_agreement_and_punishes_disagreement():
    agree = pd.DataFrame([dict.fromkeys(conviction.SIGNAL_NAMES, 1.0)])
    raw, sign = conviction.aggregate(agree)
    assert raw.iloc[0] == 1.0 and sign.iloc[0] == 1.0
    one_against = agree.copy()
    one_against["matchup"] = -1.0                                    # weight 1.0 of 9.5 total, counted twice against
    raw2, _ = conviction.aggregate(one_against)
    assert abs(raw2.iloc[0] - (8.5 - 2 * 1.0) / 9.5) < 1e-9
    assert raw2.iloc[0] < raw.iloc[0]
    under = pd.DataFrame([dict.fromkeys(conviction.SIGNAL_NAMES, -1.0)])
    raw3, sign3 = conviction.aggregate(under)
    assert sign3.iloc[0] == -1.0 and raw3.iloc[0] == 1.0             # all signals say Under: just as convinced
    sparse = pd.DataFrame([dict.fromkeys(conviction.SIGNAL_NAMES, np.nan) | {"model": 1.0, "volume": 1.0}])
    assert np.isnan(conviction.aggregate(sparse)[0].iloc[0])         # only 2 signals available: not scored


def test_score_is_a_percentile_of_past_props():
    q = np.linspace(0, 1, 1001)                  # a history whose raw values are spread evenly from 0 to 1
    assert abs(conviction.to_score(0.95, q) - 95) < 0.2
    assert conviction.to_score(1.5, q) == 100 and conviction.to_score(-1, q) == 0


def test_market_can_only_pull_a_score_down_and_by_at_most_15():
    assert conviction.market_penalty() == (0, [])
    pts, notes = conviction.market_penalty(bets_pct=70, money_pct=40)
    assert pts == 8 and "sharp money against" in notes[0]
    pts, notes = conviction.market_penalty(bets_pct=90, money_pct=40, line_move=-1.5, market_prob=0.40, fair=0.5)
    assert pts == conviction.MAX_MARKET_PENALTY and len(notes) == 3     # sharp money, line moved against, Kalshi
    assert conviction.market_penalty(line_move=2.0, market_prob=0.60, fair=0.5)[0] == 0   # agreement adds nothing
    # the bait rule needs a known line move, the same as decision.decide (so the backtest and live agree)
    assert conviction.market_penalty(bets_pct=90, money_pct=90)[0] == 0
    assert conviction.market_penalty(bets_pct=90, money_pct=90, line_move=0)[0] == 5


def test_hit_table_ranges():
    scores = [97, 96, 95, 94.9, 50, 52, 52.5]
    won = [1, 1, 0, 1, 1, 0, 0]
    t = conviction.hit_table(scores, won)
    assert t["95+"] == {"n": 3, "hit": round(2 / 3, 4)} and t["95-100"] == t["95+"]
    assert t["90-95"] == {"n": 1, "hit": 1.0} and t["50-55"] == {"n": 3, "hit": round(1 / 3, 4)}
    assert conviction.hit_table([10], [1])["95+"] == {"n": 0, "hit": None}


def scored(score=96.0, fair=0.50, price=-110, player="A B", **kw):
    return dict({"player": player, "player_id": player, "team": "NO", "market": "player_pass_yds",
                 "stat": "passing_yards", "game_id": "2026_06_NO_TB", "event_id": "e1", "line": 240.5,
                 "side": "Over", "book": "draftkings", "price": price, "other_price": -110, "fair": fair,
                 "score": score, "penalty": 0, "market_notes": [], "why": "x"}, **kw)


def tables(real_n=58, real_hit=0.62):
    return {"quantiles": {}, "history": {"95+": {"n": 15971, "hit": 0.777}},
            "real": {"95+": {"n": real_n, "hit": real_hit}}}


def test_only_95_plus_with_enough_real_history_and_a_hit_rate_above_the_book_is_shown():
    shown = conviction.select([scored(96), scored(94.9, player="C D"), scored(97, fair=0.70, player="E F")], tables())
    assert [p["player"] for p in shown] == ["A B"]                    # 94.9 is below the bar; 62% < the 70% book price
    p = shown[0]
    assert p["hit"] == 0.62 and p["hit_n"] == 58
    assert abs(p["ev100"] - (0.62 * (1 + 100 / 110) - 1) * 100) < 1e-6
    assert p["evidence"].startswith("Score 96 | hits 62% historically (58 props, 95% range 49%-73%, real DraftKings lines")
    assert conviction.select([scored(99)], tables(real_n=29)) == []     # not enough real-line props yet
    assert conviction.select([scored(99)], tables(real_hit=None)) == []
    assert conviction.select([scored(99)], tables(real_hit=0.50)) == []  # 50% doesn't beat a -110 price (52.4%)


def test_picks_sorted_by_ev_and_parlay_legs_use_the_hit_rate_not_the_score():
    picks = conviction.select([scored(95, price=-110, player="A B"), scored(99, price=+120, player="C D")], tables())
    assert [p["player"] for p in picks] == ["C D", "A B"]             # +120 pays more at the same hit rate
    legs = conviction.parlay_legs(picks)
    assert legs[0]["prob"] == 0.62 and legs[0]["prob"] != picks[0]["score"]
    assert legs[0]["ev"] > 0 and legs[0]["kind"] == "prop" and legs[0]["stat"] == "passing_yards"
    results = parlay.build(legs, lambda a, b: 0.0, lambda a, b: False, max_legs=2)
    assert len(results) == 1 and abs(results[0]["true_prob"] - 0.62 * 0.62) < 1e-12   # independent: hit rates multiplied


def test_report_when_nothing_qualifies(capsys):
    conviction.print_report([], 118, [], tables(real_n=7))
    out = capsys.readouterr().out
    assert "No picks at score 95+ today (118 props checked)" in out
    assert "only 7 real-line props so far" in out


def test_report_for_a_pick_has_every_field(capsys):
    picks = conviction.select([scored(96)], tables())
    conviction.print_report(picks, 118, [], tables())
    out = capsys.readouterr().out
    for piece in ("A B", "pass_yds 240.5 Over", "draftkings -110", "Score 96 | hits 62% historically (58 props",
                  "book implied 50.0%", "avg return +", "per $100", "why: x"):
        assert piece in out


def test_score_picks_end_to_end_with_a_fake_board_pick():
    q = {"passing_yards": list(np.linspace(0, 0.8, 1001))}      # a scale on which this prop's raw 0.79 is the 98.5th percentile
    r = row()
    r.update(spread=1.0, margin3=-12.0, att3=46.0, y_recent3=300.0, def_vs_pos=0.15, usage_trend=0.4,
             implied_points=float(np.log(17.5 / 22)))
    side = lambda name, price, other: {"side": name, "price": price, "other_price": other, "bets_pct": None,
                                       "money_pct": None, "line_move": None, "market_prob": None}
    pick = {"market": "player_pass_yds", "row": r, "line": 240.0, "p_over_model": 0.72, "player": "A B",
            "player_id": "ab", "team": "NO", "game_id": "g", "book": "draftkings",
            "sides": [side("Over", 110, -130), side("Under", -130, 110)]}
    out = conviction.score_picks([pick, {"kind": "game"}, {"market": "player_pass_tds", "row": r}], {"quantiles": q})
    assert len(out) == 1                                              # games and unscored markets are skipped
    s = out[0]
    assert s["side"] == "Over" and s["price"] == 110 and s["score"] > 95
    assert 0.45 < s["fair"] < 0.46                                    # +110 against -130, margin removed: 45.7%
    assert "NO implied 17.5 pts" in s["why"] and "46 att/game last 3" in s["why"]
    # sharp money on the other side pulls the score down
    pick["sides"][0].update(bets_pct=70, money_pct=40)
    assert conviction.score_picks([pick], {"quantiles": q})[0]["score"] == s["score"] - 8


def test_shown_picks_are_logged_and_graded_like_props():
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_games", ["game_id", "gameday", "home_team", "away_team", "home_score", "away_score",
                                      "spread_line", "total_line", "home_spread_odds", "away_spread_odds",
                                      "over_odds", "under_odds", "home_moneyline", "away_moneyline"],
                  [["g1", "2099-01-01", "TB", "NO", 20, 17, 3.0, 44.5, -110, -110, -110, -110, -150, 130]])
    replace_table(conn, "nfl_player_weeks", ["player_id", "game_id", "passing_yards"], [["ab", "g1", 251]])
    db.save_event(conn, {"id": "e1", "sport_key": "americanfootball_nfl", "commence_time": "2099-01-01T17:00:00Z"})
    picks = conviction.select([scored(96, game_id="g1", player_id="ab")], tables())
    assert conviction.log_picks(conn, picks) == 1
    assert conviction.log_picks(conn, picks) == 0                      # same pick again: not a second bet
    assert conn.execute("SELECT kind, conviction, model_prob FROM predictions").fetchall() == [("conviction", 96.0, 0.62)]
    grader.grade(conn)
    assert conn.execute("SELECT result, actual FROM predictions").fetchall() == [("win", 251.0)]   # Over 240.5 hit


def test_team_form_uses_only_earlier_games():
    from betbot.nfl import model
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_games", ["game_id", "season", "week", "home_team", "away_team", "home_score", "away_score"],
                  [["g1", 2026, 1, "NO", "TB", 30, 10], ["g2", 2026, 2, "ATL", "NO", 20, 17],
                   ["g3", 2026, 3, "NO", "LV", None, None]])    # g3 is not played yet
    replace_table(conn, "nfl_player_weeks", ["team", "game_id", "season", "attempts", "carries"],
                  [["NO", "g1", 2026, 40, 20], ["NO", "g2", 2026, 30, 25]])
    f = model.team_form(conn).set_index(["team", "week"])
    assert pd.isna(f.loc[("NO", 1), "margin3"])                     # nothing before week 1
    assert f.loc[("NO", 2), "margin3"] == 20                        # week 1 win by 20
    assert f.loc[("NO", 3), "margin3"] == (20 + -3) / 2             # weeks 1 and 2; the unplayed game doesn't count
    assert f.loc[("NO", 3), "att3"] == 35


def test_hit_rate_range_is_wider_for_fewer_props():
    low30, high30 = conviction._wilson(0.8, 30)
    low300, high300 = conviction._wilson(0.8, 300)
    assert low30 < low300 < 0.8 < high300 < high30
    assert abs(low30 - 0.627) < 0.01                                 # 80% on 30 props could really be ~63%


def test_only_established_players_are_scored():
    rookie = pd.DataFrame([row(prior_n=2), row(base=50.0), row()])
    assert conviction.eligible(rookie).tolist() == [False, False, True]   # 2 games; a backup (50 < half of 200)
    q = {"passing_yards": list(np.linspace(0, 0.8, 1001))}
    pick = {"market": "player_pass_yds", "row": row(prior_n=2), "line": 240.0, "p_over_model": 0.7, "player": "R K",
            "player_id": "rk", "team": "NO", "game_id": "g", "book": "dk", "sides": []}
    assert conviction.score_picks([pick], {"quantiles": q}) == []


def test_old_team_codes_become_todays():
    from betbot.nfl.collectors.nflverse import modern_team_codes
    header = ["game_id", "home_team", "away_team", "season"]
    rows = [["2016_01_SD_OAK", "OAK", "SD", 2016], ["2014_01_STL_DAL", "DAL", "STL", 2014], ["x", "NO", "TB", 2026]]
    assert modern_team_codes(header, rows) == [["2016_01_SD_OAK", "LV", "LAC", 2016], ["2014_01_STL_DAL", "DAL", "LA", 2014],
                                               ["x", "NO", "TB", 2026]]


def test_league_pass_attempts_baseline_uses_only_that_weeks_earlier_games():
    from betbot.nfl import model
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_games", ["game_id", "season", "week", "home_team", "away_team", "home_score", "away_score"],
                  [["g1", 2025, 1, "NO", "TB", 30, 10], ["g2", 2026, 1, "NO", "TB", 10, 10]])
    replace_table(conn, "nfl_player_weeks", ["team", "game_id", "season", "attempts", "carries"],
                  [["NO", "g1", 2025, 40, 20], ["TB", "g1", 2025, 30, 20], ["NO", "g2", 2026, 90, 20]])
    form = model.team_form(conn)
    league = form.groupby(["season", "week"])["att3"].mean()
    assert league.loc[(2026, 1)] == 35                                # 2025's attempts (40, 30); 2026's own 90 not used


def test_a_lean_needs_the_core_evidence_and_a_real_direction():
    full = pd.DataFrame([dict.fromkeys(conviction.SIGNAL_NAMES, 0.8)])
    assert not np.isnan(conviction.aggregate(full)[0].iloc[0])
    no_script = full.copy()
    no_script["script"] = np.nan                                       # no team form: nothing known about the game
    assert np.isnan(conviction.aggregate(no_script)[0].iloc[0])
    no_form_of_his_own = full.copy()
    no_form_of_his_own["volume"] = np.nan                              # no recent games of his own
    assert np.isnan(conviction.aggregate(no_form_of_his_own)[0].iloc[0])
    balanced = pd.DataFrame([dict.fromkeys(conviction.SIGNAL_NAMES, 0.0)])
    balanced["script"], balanced["volume"] = 0.0, 0.0                  # no lean at all: not defaulted to Over
    assert np.isnan(conviction.aggregate(balanced)[0].iloc[0])


def test_stand_in_lines_and_unplayed_props_never_count_as_evidence():
    scored = pd.DataFrame({"source": ["DraftKings line", "stand-in line", "DraftKings line"],
                           "won": [1.0, 1.0, np.nan], "score": [96, 97, 98]})
    assert conviction.real_evidence(scored)["score"].tolist() == [96]
    assert len(conviction.real_evidence(scored.iloc[0:0])) == 0


def test_weeks_argument():
    assert list(conviction.parse_weeks("3")) == [3] and list(conviction.parse_weeks("1-5")) == [1, 2, 3, 4, 5]


def test_history_runs_through_the_last_season_with_every_game_played():
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_games", ["game_id", "season", "home_score"],
                  [["a", 2024, 20], ["b", 2025, 17], ["c", 2025, 10], ["d", 2026, 24], ["e", 2026, None]])
    assert conviction.last_completed_season(conn) == 2025             # 2026 still has an unplayed game
    replace_table(conn, "nfl_games", ["game_id", "season", "home_score"], [["d", 2026, 24]])
    assert conviction.last_completed_season(conn) == 2026             # in the off-season the finished season counts


def test_empty_history_does_not_crash(monkeypatch):
    empty = pd.DataFrame({"season": pd.Series(dtype="int64"), "y": pd.Series(dtype=float),
                          "prior_n": pd.Series(dtype=float), "base": pd.Series(dtype=float),
                          "pos_mean": pd.Series(dtype=float)})
    monkeypatch.setattr(conviction, "_frame", lambda conn, stat, positions, cache: empty)
    monkeypatch.setattr(conviction, "last_completed_season", lambda conn: 2025)
    assert len(conviction.history_rows(None)) == 0
    q = {"passing_yards": list(np.linspace(0, 0.8, 1001))}
    pick = {"market": "player_rush_yds", "row": row(), "line": 50.5, "p_over_model": 0.6, "player": "X",
            "player_id": "x", "team": "NO", "game_id": "g", "book": "dk", "sides": []}
    assert conviction.score_picks([pick], {"quantiles": q}) == []      # no scale for rushing yards: skipped, no KeyError


def test_a_shown_pick_is_logged_once_per_prop_as_first_shown():
    conn = db.connect(":memory:")
    base = dict(kind="conviction", made_at="t", game_id="g1", event_id="e1", player="A B", player_id="ab",
                market="player_pass_yds", side="Over", line=240.5, price=-110, other_price=-110, book="draftkings",
                model_prob=0.62, implied_prob=0.5, vig_removed=1, p_over=None, mu=None, conviction=96.0)
    assert db.save_predictions(conn, [base]) == 1
    assert db.save_predictions(conn, [base]) == 0                        # the identical pick again
    assert db.save_predictions(conn, [dict(base, side="Under", price=-105, line=243.5, book="fd")]) == 0   # same prop: first shown stays
    board = dict(base, kind="prop", conviction=None)
    assert db.save_predictions(conn, [board]) == 1 and db.save_predictions(conn, [dict(board, side="Under")]) == 0   # board picks: one per market
    assert conn.execute("SELECT count(*) FROM predictions").fetchone()[0] == 2


def test_tables_built_under_other_scoring_rules_are_rebuilt(monkeypatch):
    built = []
    monkeypatch.setattr(conviction.model, "latest_week", lambda conn: 5)
    monkeypatch.setattr(conviction, "build_tables", lambda conn: built.append(1) or {"version": 5, "scoring": conviction.scoring_key()})
    monkeypatch.setattr(conviction, "load_tables", lambda conn: {"version": 5, "scoring": "old"})
    conviction.ensure_tables(None)
    assert built == [1]
    monkeypatch.setattr(conviction, "load_tables", lambda conn: {"version": 5, "scoring": conviction.scoring_key()})
    conviction.ensure_tables(None)
    assert built == [1]                                                   # same rules and week: reused


def test_evidence_is_tagged_when_the_range_includes_the_books_price():
    scored = [dict(player="A", score=96.0, fair=0.52, price=-110)]
    tag = "range includes the book's price"
    thin = conviction.select(scored, {"real": {"95+": {"n": 30, "hit": 0.55}}, "history": {}})
    assert len(thin) == 1 and thin[0]["uncertain"] and tag in thin[0]["evidence"]
    solid = conviction.select(scored, {"real": {"95+": {"n": 3000, "hit": 0.60}}, "history": {}})
    assert not solid[0]["uncertain"] and tag not in solid[0]["evidence"]


def test_real_lines_take_the_earliest_article_per_player_and_week(monkeypatch):
    from betbot.nfl import prop_splits
    df = pd.DataFrame({"season": [2026, 2026], "week": [4, 4], "player": ["A B", "A B"], "prop_type": ["pass_yds"] * 2,
                       "period": [None, None], "line": [240.5, 243.5], "published": ["2026-10-01", "2026-10-03"],
                       "over_ticket_pct": [60, 70], "over_handle_pct": [55, 65]})
    monkeypatch.setattr(prop_splits, "dataframe", lambda conn: df.iloc[::-1])
    out = conviction.real_lines(db.connect(":memory:"))
    assert out["line"].tolist() == [240.5]


def test_team_form_warns_when_its_tables_are_missing():
    from betbot.nfl import model
    with pytest.warns(RuntimeWarning, match="team form unavailable"):
        model.team_form(db.connect(":memory:"))


def test_parlay_log_and_record_settle_from_leg_results():
    from betbot.nfl import parlays
    conn = db.connect(":memory:")
    def leg(player, side="Over"):
        return dict(game="g1", player_id=player, market="player_pass_yds", side=side, line=240.5)
    def pred(player, result, side="Over"):
        return dict(kind="prop", made_at="t", game_id="g1", event_id="e", player="x", player_id=player,
                    market="player_pass_yds", side=side, line=240.5, price=-110, other_price=-110, book="dk",
                    model_prob=0.55, implied_prob=0.5, vig_removed=1, p_over=None, mu=None, conviction=None,
                    result=result)
    result = lambda *legs: {"true_prob": 0.3, "price": 260, "legs": list(legs)}
    assert parlays.log(conn, [result(leg("a"), leg("b")), result(leg("c"), leg("d"))]) == 2
    assert parlays.log(conn, [result(leg("b"), leg("a"))]) == 0                    # same legs: kept once
    db.save_predictions(conn, [pred("a", None), pred("b", None), pred("c", None), pred("d", None)])
    assert parlays.record(conn)[0] == 0                                            # nothing graded: open
    for pid, res in (("a", "win"), ("b", "loss"), ("c", "win")):
        conn.execute("UPDATE predictions SET result = ? WHERE player_id = ?", (res, pid))
    assert parlays.record(conn) == (1, 0, 0.3)                                     # a+b lost; c+d still waiting on d
    conn.execute("UPDATE predictions SET result = 'win' WHERE player_id = 'd'")
    assert parlays.record(conn) == (2, 1, 0.3)


def test_parlay_with_a_game_leg_and_a_prop_leg_logs_and_a_moved_line_still_settles():
    from betbot.nfl import parlays
    conn = db.connect(":memory:")
    prop = dict(game="g1", player_id="a", market="player_pass_yds", side="Under", line=250.5)
    game = dict(game="g1", market="spread", side="home", line=-3.0)
    assert parlays.log(conn, [{"true_prob": 0.3, "price": 260, "legs": [prop, game]}]) == 1   # None vs str ids
    base = dict(kind="prop", made_at="t", game_id="g1", event_id="e", player="x", player_id="a",
                market="player_pass_yds", side="Over", line=240.5, price=-110, other_price=-110, book="dk",
                model_prob=0.55, implied_prob=0.5, vig_removed=1, p_over=None, mu=None, conviction=None)
    db.save_predictions(conn, [base])
    conn.execute("UPDATE predictions SET result = 'win', actual = 230")           # logged as Over 240.5; the leg is Under 250.5
    assert parlays._leg_result(conn, "g1", "a", "player_pass_yds", "Under", 250.5) == "win"
    assert parlays._leg_result(conn, "g1", None, "spread", "home", -3.0) is None
    conn.execute("UPDATE predictions SET result = 'unverified'")
    assert parlays.record(conn)[0] == 0                            # unverified: still open


def test_the_pull_stops_at_its_credit_cap_and_never_goes_negative():
    from betbot.nfl.collectors import endzone as ez
    conn = conn_with_players()
    ez.pull(conn, [("a", "Ann QB", "player_pass_yds"), ("c", "Cy QB", "player_pass_yds")], "key",
            Session({"a": {"player_name": "A", "games": []}, "c": {"player_name": "C", "games": []}}),
            sleep=lambda s: None, max_credits=10)
    assert conn.execute("SELECT count(*) FROM endzone_pulled").fetchone()[0] == 1
    assert ez.plan(conn, ["player_pass_yds"], max_credits=-10) == []
