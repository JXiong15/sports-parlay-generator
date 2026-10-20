"""Game-line factors the team-ratings model leaves out: offense-vs-defense matchups (passing and rushing), defensive
starters out, starting QB out, rest, weather, division games, and a prime-time hype discount on the favorite.
Each factor is measured on past games (what the ratings model missed by, in points), then added to the model's
expected margin and total. Coefficients are ridge fits, tested season by season on games they never saw
(`python -m betbot.nfl.game_factors`)."""
import numpy as np
import pandas as pd

from betbot.nfl import game_model, model
from betbot.nfl.teams import TEAMS

FIRST_FIT_SEASON = 2013      # snap counts (defensive starters) start here
RIDGE = 30.0
ROLL = 6                     # games in a team's recent offense / defense form
KEY = "_game_factors"
MARGIN = ["qb_diff", "def_out_diff", "pass_match", "rush_match", "rest_diff", "div_game", "prime_favorite",
          "bets_home", "gap_home", "move_home"]
TOTAL = ["qb_total", "def_out_total", "pass_pace", "rush_pace", "wind", "cold", "dome", "prime", "short_rest",
         "bets_over", "gap_over", "move_total"]


def _team_form(conn):
    """Per (game_id, team): rolling mean of the team's passing / rushing yards for and against over its previous
    ROLL games (carried across seasons), minus the league's average to date."""
    w = pd.read_sql_query(
        "SELECT game_id, team, opponent_team, season, week, sum(passing_yards) AS pass_for,"
        " sum(rushing_yards) AS rush_for FROM nfl_player_weeks WHERE season_type = 'REG'"
        " GROUP BY game_id, team, opponent_team, season, week", conn)
    against = w[["game_id", "team", "pass_for", "rush_for"]].rename(
        columns={"team": "opponent_team", "pass_for": "pass_against", "rush_for": "rush_against"})
    w = w.merge(against, on=["game_id", "opponent_team"], how="left").sort_values(["season", "week"])
    w = w.drop_duplicates(["game_id", "team"])   # a stray stat row listing another opponent for the same game
    for col in ("pass_for", "rush_for", "pass_against", "rush_against"):
        roll = w.groupby("team")[col].transform(lambda s: s.shift(1).rolling(ROLL, min_periods=3).mean())
        weekly = w.groupby(["season", "week"])[col].agg(["sum", "count"]).sort_index()
        league = (weekly["sum"].cumsum() / weekly["count"].cumsum()).shift(1).rename("league")   # earlier weeks only
        w = w.join(league, on=["season", "week"])
        w[col + "_adv"] = roll - w.pop("league")
    return w.set_index(["game_id", "team"])[[c + "_adv" for c in ("pass_for", "rush_for", "pass_against", "rush_against")]]


def _splits(conn):
    """Per game_id: the public's lean and where the money sits against the bets, from the earliest archived capture
    (past games) or the latest saved page (current games); 0 where unknown.
    bets_home / bets_over: (% of bets on that side - 50) / 50. gap_*: (% of money - % of bets) / 50, same side."""
    arch = pd.read_sql_query("SELECT game_id, market, side, bets_pct, money_pct, timestamp AS at FROM archive_splits"
                             " WHERE market IN ('spread', 'total') AND side IN ('home', 'over') AND bets_pct IS NOT NULL",
                             conn)
    live = pd.read_sql_query("SELECT home_team, away_team, start_time, market, side, bets_pct, money_pct, fetched_at AS at"
                             " FROM splits WHERE market IN ('spread', 'total') AND side IN ('home', 'over')"
                             " AND bets_pct IS NOT NULL", conn)
    if len(live):
        sched = pd.read_sql_query("SELECT game_id, home_team, away_team, gameday FROM nfl_games", conn)
        live["home_team"], live["away_team"] = live["home_team"].map(TEAMS), live["away_team"].map(TEAMS)
        live["gameday"] = pd.to_datetime(live["start_time"], utc=True).dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
        sched["gameday"] = sched["gameday"].astype(str).str[:10]
        live = live.merge(sched, on=["home_team", "away_team", "gameday"])[arch.columns]
        arch = pd.concat([arch.sort_values("at").drop_duplicates(["game_id", "market", "side"]),
                          live.sort_values("at").drop_duplicates(["game_id", "market", "side"], keep="last")])
        arch = arch.drop_duplicates(["game_id", "market", "side"], keep="last")   # a current page beats an old capture
    else:
        arch = arch.sort_values("at").drop_duplicates(["game_id", "market", "side"])
    arch["bets"] = (arch["bets_pct"] - 50) / 50
    arch["gap"] = ((arch["money_pct"] - arch["bets_pct"]) / 50).fillna(0)
    wide = arch.pivot(index="game_id", columns="market", values=["bets", "gap"])
    out = pd.DataFrame(index=wide.index)
    for stat in ("bets", "gap"):
        out[f"{stat}_home"] = wide[stat].get("spread")
        out[f"{stat}_over"] = wide[stat].get("total")
    return out.fillna(0)


def _moves(conn):
    """Per game_id: how far the market moved since it opened, signed toward the home team / the Over (points / 10).
    ESPN's home spread is negative for a favorite, so a drop in it is the market liking the home team."""
    m = pd.read_sql_query("SELECT game_id, open_home_spread, current_home_spread, close_home_spread, open_total,"
                          " current_total, close_total FROM nfl_game_lines", conn).drop_duplicates("game_id")
    now_spread = m["close_home_spread"].fillna(m["current_home_spread"])
    now_total = m["close_total"].fillna(m["current_total"])
    out = pd.DataFrame({"game_id": m["game_id"], "move_home": (m["open_home_spread"] - now_spread) / 10,
                        "move_total": (now_total - m["open_total"]) / 10}).set_index("game_id")
    return out.fillna(0)


def features(conn, absences=None):
    """One row per game (played and upcoming) with every factor, signed for the home team."""
    g = game_model.games(conn)
    g = g[g["season"] >= FIRST_FIT_SEASON - 1].copy()
    ab = model.team_absences(conn, model.injury_flags(conn)) if absences is None else absences
    form = _team_form(conn)
    for side in ("home", "away"):
        g = g.merge(ab[["season", "week", "team", "qb_out", "def_starters_out"]].rename(
            columns={"team": f"{side}_team", "qb_out": f"{side}_qb", "def_starters_out": f"{side}_def_out"}),
            how="left", on=["season", "week", f"{side}_team"])
        f = form.reset_index().rename(columns={"team": f"{side}_team"})
        f = f.rename(columns={c: f"{side}_{c}" for c in f.columns if c.endswith("_adv")})
        g = g.merge(f, how="left", on=["game_id", f"{side}_team"])
    extra = pd.read_sql_query(
        "SELECT g.game_id, g.weekday, g.gametime, g.roof, coalesce(g.temp, w.temp_f) AS temp,"
        " coalesce(g.wind, w.wind_mph) AS wind, g.home_rest, g.away_rest, g.div_game, w.indoor AS forecast_indoor"
        " FROM nfl_games g LEFT JOIN nfl_weather w ON w.game_id = g.game_id", conn)   # forecast for upcoming games
    g = g.merge(extra, on="game_id", how="left")
    g[["home_qb", "away_qb", "home_def_out", "away_def_out"]] = g[["home_qb", "away_qb", "home_def_out", "away_def_out"]].fillna(0)
    out = pd.DataFrame({"game_id": g["game_id"]})
    out["qb_diff"] = g["home_qb"] - g["away_qb"]
    out["def_out_diff"] = (g["away_def_out"] - g["home_def_out"]).clip(-4, 4)   # opposing defenders out help the offense
    # matchup in hundreds of yards: our offense vs their defense, minus theirs vs ours
    out["pass_match"] = ((g["home_pass_for_adv"] + g["away_pass_against_adv"])
                         - (g["away_pass_for_adv"] + g["home_pass_against_adv"])).fillna(0) / 100
    out["rush_match"] = ((g["home_rush_for_adv"] + g["away_rush_against_adv"])
                         - (g["away_rush_for_adv"] + g["home_rush_against_adv"])).fillna(0) / 100
    out["rest_diff"] = (g["home_rest"] - g["away_rest"]).clip(-7, 7).fillna(0) / 7
    out["div_game"] = g["div_game"].fillna(0).astype(float) * np.sign(g["spread_line"].fillna(0))
    prime = model.is_prime(g["weekday"], g["gametime"])
    out["prime_favorite"] = prime * np.sign(g["spread_line"].fillna(0))          # hype: the favorite is overbet
    indoor = g["roof"].isin(["dome", "closed"]) | (g["forecast_indoor"].fillna(0) == 1)
    out["qb_total"] = g["home_qb"] + g["away_qb"]
    out["def_out_total"] = (g["home_def_out"] + g["away_def_out"]).clip(0, 8)
    out["pass_pace"] = (g["home_pass_for_adv"] + g["away_pass_for_adv"] + g["home_pass_against_adv"]
                        + g["away_pass_against_adv"]).fillna(0) / 100
    out["rush_pace"] = (g["home_rush_for_adv"] + g["away_rush_for_adv"] + g["home_rush_against_adv"]
                        + g["away_rush_against_adv"]).fillna(0) / 100
    out["wind"] = np.where(indoor, 0.0, g["wind"].fillna(0)) / 10
    out["cold"] = np.where(indoor, 0.0, (40 - g["temp"].fillna(60)).clip(lower=0)) / 10
    out["dome"] = indoor.astype(float)
    out["prime"] = prime
    out["short_rest"] = ((g["home_rest"].fillna(7) < 6) | (g["away_rest"].fillna(7) < 6)).astype(float)
    out = out.set_index("game_id")
    out = out.join(_splits(conn), how="left").join(_moves(conn), how="left")
    return out.fillna({c: 0.0 for c in ("bets_home", "gap_home", "bets_over", "gap_over", "move_home", "move_total")})


def _ridge(X, y):
    X = np.asarray(X, float)
    return np.linalg.solve(X.T @ X + RIDGE * np.eye(X.shape[1]), X.T @ np.asarray(y, float))


def fit(rows, until_season=None):
    """Coefficients for the margin and total factors, fit on games before `until_season` (None = all played)."""
    d = rows[rows["actual_margin"].notna() & (rows["season"] >= FIRST_FIT_SEASON)]
    if until_season is not None:
        d = d[d["season"] < until_season]
    if len(d) < 300:
        return {"margin": dict.fromkeys(MARGIN, 0.0), "total": dict.fromkeys(TOTAL, 0.0), "n": int(len(d))}
    bm = _ridge(d[MARGIN], d["actual_margin"] - d["pred_margin"])
    bt = _ridge(d[TOTAL], d["actual_total"] - d["pred_total"])
    return {"margin": dict(zip(MARGIN, map(float, bm))), "total": dict(zip(TOTAL, map(float, bt))), "n": int(len(d))}


def apply(g, feats, coefs):
    """pred_margin / pred_total plus the fitted factor effects. `g` is game_model.predict output (with game_id)."""
    g = g.merge(feats, how="left", left_on="game_id", right_index=True)
    cm, ct = coefs["margin"], coefs["total"]
    g["pred_margin"] = g["pred_margin"] + sum(cm[k] * g[k].fillna(0) for k in MARGIN)
    g["pred_total"] = g["pred_total"] + sum(ct[k] * g[k].fillna(0) for k in TOTAL)
    g["pred_home"], g["pred_away"] = (g["pred_total"] + g["pred_margin"]) / 2, (g["pred_total"] - g["pred_margin"]) / 2
    return g.drop(columns=[c for c in MARGIN + TOTAL if c in g.columns])


def adjusted(conn, absences=None):
    """Every game with factor-adjusted predictions: seasons before 2017 as the ratings model gave them, each later
    season with factors fit only on earlier seasons, upcoming games with factors fit on every played game."""
    g = table(conn, absences)
    played = g[g["actual_margin"].notna()]
    parts = []
    for season, chunk in g.groupby("season"):
        upcoming = chunk["actual_margin"].isna()
        for flag, rows in ((False, chunk[~upcoming]), (True, chunk[upcoming])):
            if rows.empty or (not flag and season < 2017):
                parts.append(rows.drop(columns=MARGIN + TOTAL))
                continue
            c = fit(played, until_season=None if flag else season)
            parts.append(apply(rows.drop(columns=MARGIN + TOTAL), rows[["game_id"] + MARGIN + TOTAL].set_index("game_id"), c))
    return pd.concat(parts).sort_values(["gameday", "game_id"]).reset_index(drop=True)


def table(conn, absences=None):
    """Played and upcoming games with the base model's predictions and every factor."""
    g = game_model.predict(game_model.games(conn))
    g = g.merge(features(conn, absences), how="left", left_on="game_id", right_index=True)
    g["actual_margin"] = g["home_score"] - g["away_score"]
    g["actual_total"] = g["home_score"] + g["away_score"]
    return g


def walk_forward(conn, first_test=2017):
    """Held-out results: for each season, fit on earlier seasons only and compare errors and ATS hit rates."""
    g = table(conn)
    played = g[g["actual_margin"].notna()]
    rows = []
    for season in range(first_test, int(played["season"].max()) + 1):
        c = fit(played, until_season=season)
        test = played[played["season"] == season]
        if test.empty:
            continue
        adj = apply(test.drop(columns=MARGIN + TOTAL), test[["game_id"] + MARGIN + TOTAL].set_index("game_id"), c)
        rows.append(adj.assign(base_margin=test["pred_margin"].values, base_total=test["pred_total"].values))
    d = pd.concat(rows)
    hs = -d["spread_line"]  # home spread as bet (home -3 = -3)
    out = {"games": int(len(d))}
    for name, pm, pt in (("base", d["base_margin"], d["base_total"]), ("with factors", d["pred_margin"], d["pred_total"])):
        covers = (d["actual_margin"] + hs).astype(float)
        mask = covers != 0
        pick_home = (pm + hs) > 0
        out[name] = {
            "margin_rmse": float(np.sqrt(((d["actual_margin"] - pm) ** 2).mean())),
            "total_rmse": float(np.sqrt(((d["actual_total"] - pt) ** 2).mean())),
            "spread_hit": float((pick_home[mask] == (covers[mask] > 0)).mean()),
            "total_hit": float(((pt > d["total_line"])[(d["actual_total"] != d["total_line"])]
                                == (d["actual_total"] > d["total_line"])[(d["actual_total"] != d["total_line"])]).mean()),
        }
    out["market_margin_rmse"] = float(np.sqrt(((d["actual_margin"] - d["spread_line"]) ** 2).mean()))
    out["market_total_rmse"] = float(np.sqrt(((d["actual_total"] - d["total_line"]) ** 2).mean()))
    return out, fit(played)


if __name__ == "__main__":
    import os
    from betbot import db
    conn = db.connect(os.getenv("DB_PATH", "betbot.db"))
    res, coefs = walk_forward(conn)
    print(f"held-out games 2017+: {res['games']}")
    for k in ("base", "with factors"):
        print(f"  {k:13} margin error {res[k]['margin_rmse']:.3f}  total error {res[k]['total_rmse']:.3f}"
              f"  spread {res[k]['spread_hit']:.1%}  total {res[k]['total_hit']:.1%}")
    print(f"  the betting line: margin error {res['market_margin_rmse']:.3f}  total error {res['market_total_rmse']:.3f}")
    print("margin effects (points):", {k: round(v, 2) for k, v in coefs["margin"].items()})
    print("total effects (points):", {k: round(v, 2) for k, v in coefs["total"].items()})
