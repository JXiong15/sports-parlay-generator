"""Game-line factors the team-ratings model leaves out: offense-vs-defense matchups (passing and rushing), defensive
starters out, starting QB out, rest, weather, division games, and a prime-time hype discount on the favorite.
Each factor is measured on past games (what the ratings model missed by, in points), then added to the model's
expected margin and total. Coefficients are ridge fits, tested season by season on games they never saw
(`python -m betbot.nfl.game_factors`)."""
import sqlite3

import numpy as np
import pandas as pd

from betbot.nfl import game_model, model
from betbot.nfl.teams import HOME, TEAMS, local_hour

FIRST_FIT_SEASON = 2013      # snap counts (defensive starters) start here
RIDGE = 30.0
ROLL = 6                     # games in a team's recent offense / defense form
KEY = "_game_factors"
LEAGUE_WEEKS = 48            # the league average each form is measured against: the previous ~3 seasons of weeks
MARGIN = ["travel_diff", "body_clock_diff", "ref_home", "qb_diff", "ol_diff", "skill_diff", "rush_def_diff",
          "secondary_diff", "lb_diff", "pass_match", "rush_match", "epa_pass_match", "epa_rush_match",
          "pressure_match", "rest_diff", "div_game", "prime_favorite", "bets_home", "gap_home", "move_home"]
TOTAL = ["ref_total", "qb_total", "ol_total", "skill_total", "defense_total", "pass_pace", "rush_pace", "epa_total",
         "pace_total", "wind", "gust", "rain", "cold", "dome", "prime", "short_rest", "bets_over", "gap_over",
         "move_total"]


def _upcoming_rows(conn):
    """A (game_id, season, week, team) row with no stats for both teams of every unplayed game, so each team's
    rolling form carries forward to its next game instead of stopping at the last one played."""
    rows = conn.execute("SELECT game_id, season, week, home_team, away_team FROM nfl_games"
                        " WHERE home_score IS NULL").fetchall()
    return pd.DataFrame([(g, s, w, t) for g, s, w, h, a in rows for t in (h, a)],
                        columns=["game_id", "season", "week", "team"])


def _is_missing_table(error):
    return "no such table" in str(error)


def _team_form(conn, upcoming):
    """Per (game_id, team): rolling mean of the team's passing / rushing yards for and against over its previous
    ROLL games (carried across seasons), minus the league's average."""
    w = pd.read_sql_query(
        "SELECT game_id, team, opponent_team, season, week, sum(passing_yards) AS pass_for,"
        " sum(rushing_yards) AS rush_for FROM nfl_player_weeks WHERE season_type = 'REG'"
        " GROUP BY game_id, team, opponent_team, season, week", conn)
    against = w[["game_id", "team", "pass_for", "rush_for"]].rename(
        columns={"team": "opponent_team", "pass_for": "pass_against", "rush_for": "rush_against"})
    w = w.merge(against, on=["game_id", "opponent_team"], how="left")
    w = w.drop_duplicates(["game_id", "team"])   # a stray stat row listing another opponent for the same game
    cols = ["pass_for", "rush_for", "pass_against", "rush_against"]
    return _roll_adv(w, cols, upcoming).set_index(["game_id", "team"])[[c + "_adv" for c in cols]]


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


def _referees(conn):
    """Per game_id: the referee's earlier games' average miss of the betting line (total and home margin), shrunk
    toward 0 by games / (games + 40). Uses only games before this one."""
    r = pd.read_sql_query("SELECT game_id, gameday, referee, home_score - away_score AS margin, home_score + away_score AS tot,"
                          " spread_line, total_line FROM nfl_games WHERE referee IS NOT NULL ORDER BY gameday, game_id", conn)
    r["miss_total"] = r["tot"] - r["total_line"]
    r["miss_margin"] = r["margin"] - r["spread_line"]
    out = {}
    for col, name in (("miss_total", "ref_total"), ("miss_margin", "ref_home")):
        counted = r[col].notna()                 # a game with no betting line or no score says nothing about the miss
        x = r[col].fillna(0.0)
        g = x.groupby(r["referee"])
        n = counted.astype(float).groupby(r["referee"]).cumsum() - counted.astype(float)    # earlier counted games
        before = g.cumsum() - x
        out[name] = (before / n.clip(lower=1)) * (n / (n + 40))
    return pd.DataFrame({"game_id": r["game_id"], **out}).set_index("game_id")


def _travel(g):
    """Away team's trip: distance (thousand km) and how early the kickoff is on its body clock, from team home
    cities and kickoff time (Eastern). Neutral-site games count as no travel difference."""
    def haversine(a, b):
        la1, lo1, la2, lo2 = map(np.radians, (a[0], a[1], b[0], b[1]))
        h = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
        return 6371 * 2 * np.arcsin(np.sqrt(h))
    km, early = [], []
    for home, away, day, time, loc in zip(g["home_team"], g["away_team"], g["gameday"], g["gametime"], g["location"]):
        if home not in HOME or away not in HOME or loc == "Neutral":
            km.append(0.0)
            early.append(0.0)
            continue
        km.append(haversine(HOME[away], HOME[home]) / 1000)
        body = local_hour(day, time, away)             # kickoff hour on the AWAY team's body clock
        early.append(float(np.clip((12 - body) / 3, 0, 1)))
    return np.array(km), np.array(early)


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


def _roll_adv(w, cols, upcoming):
    """Rolling mean of `cols` over each team's previous ROLL games, minus the league average over the previous
    LEAGUE_WEEKS weeks. `upcoming` rows (no stats) get the form of the games before them."""
    w = pd.concat([w, upcoming[~upcoming["game_id"].isin(w["game_id"])]], ignore_index=True)
    w = w.sort_values(["season", "week"], kind="stable")
    for col in cols:
        roll = w.groupby("team")[col].transform(lambda s: s.shift(1).rolling(ROLL, min_periods=3).mean())
        weekly = w.groupby(["season", "week"])[col].agg(["sum", "count"]).sort_index()
        league = (weekly["sum"].rolling(LEAGUE_WEEKS, min_periods=1).sum()
                  / weekly["count"].rolling(LEAGUE_WEEKS, min_periods=1).sum()).shift(1).rename("league")
        w = w.join(league, on=["season", "week"])
        w[col + "_adv"] = roll - w.pop("league")
    return w


def _pbp_form(conn, upcoming):
    """Per (game_id, team), from play-by-play: the offense's recent EPA per pass / rush, pressure allowed and pace,
    and the defense's (what opponents did against it), each minus the league average. Empty if pbp isn't loaded."""
    try:
        o = pd.read_sql_query("SELECT game_id, season, week, team, plays, epa_pass, epa_rush, hit_rate FROM nfl_team_game_pbp"
                              " WHERE season_type = 'REG'", conn)
    except (sqlite3.OperationalError, pd.errors.DatabaseError) as e:
        if _is_missing_table(e):
            return pd.DataFrame()
        raise
    d = o.merge(o[["game_id", "team", "epa_pass", "epa_rush", "hit_rate"]], on="game_id", suffixes=("", "_opp"))
    d = d[d["team"] != d["team_opp"]].drop(columns="team_opp")
    # the opponent's numbers in this game are what this team's DEFENSE allowed / generated
    d = d.rename(columns={"epa_pass_opp": "d_epa_pass", "epa_rush_opp": "d_epa_rush", "hit_rate_opp": "d_hit"})
    cols = ["epa_pass", "epa_rush", "hit_rate", "plays", "d_epa_pass", "d_epa_rush", "d_hit"]
    d = _roll_adv(d.drop_duplicates(["game_id", "team"]), cols, upcoming)
    return d.set_index(["game_id", "team"])[[c + "_adv" for c in cols]]


def features(conn, absences=None):
    """One row per game (played and upcoming) with every factor, signed for the home team."""
    g = game_model.games(conn)
    g = g[g["season"] >= FIRST_FIT_SEASON - 1].copy()
    ab = model.team_absences(conn, model.injury_flags(conn)) if absences is None else absences
    upcoming_rows = _upcoming_rows(conn)
    form = _team_form(conn, upcoming_rows)
    pbp = _pbp_form(conn, upcoming_rows)
    for side in ("home", "away"):
        g = g.merge(ab[["season", "week", "team", "qb_out"] + model.GROUP_COLUMNS].rename(
            columns={"team": f"{side}_team", "qb_out": f"{side}_qb",
                     **{c: f"{side}_{c}" for c in model.GROUP_COLUMNS}}),
            how="left", on=["season", "week", f"{side}_team"])
        f = form.reset_index().rename(columns={"team": f"{side}_team"})
        f = f.rename(columns={c: f"{side}_{c}" for c in f.columns if c.endswith("_adv")})
        g = g.merge(f, how="left", on=["game_id", f"{side}_team"])
        if len(pbp):
            q = pbp.reset_index().rename(columns={"team": f"{side}_team"})
            q = q.rename(columns={c: f"{side}_p_{c}" for c in q.columns if c.endswith("_adv")})
            g = g.merge(q, how="left", on=["game_id", f"{side}_team"])
    extra = pd.read_sql_query(
        "SELECT g.game_id, g.weekday, g.gametime, g.roof, coalesce(g.temp, w.temp_f) AS temp,"
        " coalesce(g.wind, w.wind_mph) AS wind, g.home_rest, g.away_rest, g.div_game, w.indoor AS forecast_indoor,"
        " coalesce(h.gust_mph, w.gust_mph) AS gust, h.precip_mm, w.precip_pct"
        " FROM nfl_games g LEFT JOIN nfl_weather w ON w.game_id = g.game_id"
        " LEFT JOIN nfl_weather_history h ON h.game_id = g.game_id", conn)   # forecast for upcoming games
    g = g.merge(extra, on="game_id", how="left")
    counted = [f"{s}_{c}" for s in ("home", "away") for c in ["qb"] + model.GROUP_COLUMNS]
    g[counted] = g[counted].fillna(0)

    def grp(side, *names):   # starters out in the named groups for one side
        return sum(g[f"{side}_{n}_out"] for n in names)
    out = pd.DataFrame({"game_id": g["game_id"]})
    km, early = _travel(g)
    out["travel_diff"] = km / 2        # a long trip helps the home team
    out["body_clock_diff"] = early
    out["qb_diff"] = g["home_qb"] - g["away_qb"]
    # matchup in hundreds of yards: our offense vs their defense, minus theirs vs ours
    # a side's own missing offense hurts it; its missing defense helps the other side. Signed for the home team.
    out["ol_diff"] = (grp("home", "ol") - grp("away", "ol")).clip(-3, 3)
    out["skill_diff"] = (grp("home", "wr_te", "rb") - grp("away", "wr_te", "rb")).clip(-4, 4)
    out["rush_def_diff"] = (grp("home", "pass_rush") - grp("away", "pass_rush")).clip(-3, 3)
    out["secondary_diff"] = (grp("home", "cb", "safety") - grp("away", "cb", "safety")).clip(-4, 4)
    out["lb_diff"] = (grp("home", "lb") - grp("away", "lb")).clip(-3, 3)
    out["ol_total"] = (grp("home", "ol") + grp("away", "ol")).clip(0, 5)
    out["skill_total"] = (grp("home", "wr_te", "rb") + grp("away", "wr_te", "rb")).clip(0, 6)
    out["defense_total"] = (grp("home", "pass_rush", "lb", "cb", "safety") + grp("away", "pass_rush", "lb", "cb", "safety")).clip(0, 8)
    out["pass_match"] = ((g["home_pass_for_adv"] + g["away_pass_against_adv"])
                         - (g["away_pass_for_adv"] + g["home_pass_against_adv"])).fillna(0) / 100
    out["rush_match"] = ((g["home_rush_for_adv"] + g["away_rush_against_adv"])
                         - (g["away_rush_for_adv"] + g["home_rush_against_adv"])).fillna(0) / 100
    def pb(col):
        return g[col] if col in g else pd.Series(0.0, index=g.index)
    # EPA per play: our offense against their defense, minus theirs against ours (d_* = what that defense allowed)
    out["epa_pass_match"] = ((pb("home_p_epa_pass_adv") + pb("away_p_d_epa_pass_adv"))
                             - (pb("away_p_epa_pass_adv") + pb("home_p_d_epa_pass_adv"))).fillna(0)
    out["epa_rush_match"] = ((pb("home_p_epa_rush_adv") + pb("away_p_d_epa_rush_adv"))
                             - (pb("away_p_epa_rush_adv") + pb("home_p_d_epa_rush_adv"))).fillna(0)
    # pressure on each QB: his line's recent hit rate allowed plus the opposing rush's recent hit rate generated
    out["pressure_match"] = ((pb("home_p_hit_rate_adv") + pb("away_p_d_hit_adv"))
                             - (pb("away_p_hit_rate_adv") + pb("home_p_d_hit_adv"))).fillna(0)
    out["epa_total"] = (pb("home_p_epa_pass_adv") + pb("away_p_epa_pass_adv") + pb("home_p_d_epa_pass_adv")
                        + pb("away_p_d_epa_pass_adv") + pb("home_p_epa_rush_adv") + pb("away_p_epa_rush_adv")
                        + pb("home_p_d_epa_rush_adv") + pb("away_p_d_epa_rush_adv")).fillna(0)
    out["pace_total"] = (pb("home_p_plays_adv") + pb("away_p_plays_adv")).fillna(0) / 10
    out["rest_diff"] = (g["home_rest"] - g["away_rest"]).clip(-7, 7).fillna(0) / 7
    out["div_game"] = g["div_game"].fillna(0).astype(float) * np.sign(g["spread_line"].fillna(0))
    prime = model.is_prime(g["weekday"], g["gametime"])
    out["prime_favorite"] = prime * np.sign(g["spread_line"].fillna(0))          # hype: the favorite is overbet
    indoor = g["roof"].isin(["dome", "closed"]) | (g["forecast_indoor"].fillna(0) == 1)
    out["qb_total"] = g["home_qb"] + g["away_qb"]
    out["pass_pace"] = (g["home_pass_for_adv"] + g["away_pass_for_adv"] + g["home_pass_against_adv"]
                        + g["away_pass_against_adv"]).fillna(0) / 100
    out["rush_pace"] = (g["home_rush_for_adv"] + g["away_rush_for_adv"] + g["home_rush_against_adv"]
                        + g["away_rush_against_adv"]).fillna(0) / 100
    out["wind"] = np.where(indoor, 0.0, g["wind"].fillna(0)) / 10
    out["gust"] = np.where(indoor, 0.0, g["gust"].fillna(0)) / 10
    # rain: 0.5 mm or more in the three kickoff hours (past games); for a forecast, a 70%+ chance of rain
    # shortcut: the two are different measures (an amount seen vs. a probability); revisit once forecasts are saved
    # for a season and can be fit directly
    out["rain"] = np.where(indoor, 0.0, ((g["precip_mm"].fillna(0) >= 0.5) | (g["precip_pct"].fillna(0) >= 70)).astype(float))
    out["cold"] = np.where(indoor, 0.0, (40 - g["temp"].fillna(60)).clip(lower=0)) / 10
    out["dome"] = indoor.astype(float)
    out["prime"] = prime
    out["short_rest"] = ((g["home_rest"].fillna(7) < 6) | (g["away_rest"].fillna(7) < 6)).astype(float)
    out = out.set_index("game_id")
    out = out.join(_splits(conn), how="left").join(_moves(conn), how="left").join(_referees(conn), how="left")
    return out.fillna({c: 0.0 for c in ("bets_home", "gap_home", "bets_over", "gap_over", "move_home", "move_total",
                                         "ref_home", "ref_total")})


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


def _market(conn, g):
    """The market's margin and total for every game: the closing line for played games, ESPN's current line for
    upcoming ones (nflverse has none yet); NaN where neither exists."""
    cur = pd.read_sql_query("SELECT game_id, -coalesce(close_home_spread, current_home_spread) AS cur_margin,"
                            " coalesce(close_total, current_total) AS cur_total FROM nfl_game_lines", conn
                            ).drop_duplicates("game_id")
    g = g.merge(cur, on="game_id", how="left")
    played = g["home_score"].notna()   # played games: the closing line; upcoming: ESPN's current line (nflverse's may be stale)
    g["market_margin"] = g["spread_line"].where(played, g["cur_margin"]).fillna(g["cur_margin"]).fillna(g["spread_line"])
    g["market_total"] = g["total_line"].where(played, g["cur_total"]).fillna(g["cur_total"]).fillna(g["total_line"])
    return g.drop(columns=["cur_margin", "cur_total"])


def trust(actual, pred, market, default=0.5):
    """How far to move from the market toward the model: the least-squares weight of (model - market) in predicting
    (actual - market), kept in [0, 1]. The model has not beaten the closing line, so this learns to stay near it."""
    d = pd.DataFrame({"y": actual - market, "x": pred - market}).dropna()
    if len(d) < 300 or (d["x"] ** 2).sum() == 0:
        return default
    return float(np.clip((d["x"] * d["y"]).sum() / (d["x"] ** 2).sum(), 0.0, 1.0))


def adjusted(conn, absences=None):
    """Every game with factor-adjusted predictions: seasons before 2017 as the ratings model gave them, each later
    season with factors fit only on earlier seasons, upcoming games with factors fit on every played game. Each
    game also gets `blend_margin` / `blend_total`: the model pulled toward the market by a weight learned from earlier
    games' results, so every graded game changes the next run's picks. `pred_*` stays the model alone, so backtests and
    CLV tests never grade a prediction that contains the line they are graded against."""
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
    g = _market(conn, pd.concat(parts).sort_values(["gameday", "game_id"]).reset_index(drop=True))
    done = g["actual_margin"].notna()
    for key, actual in (("margin", "actual_margin"), ("total", "actual_total")):
        g[f"blend_{key}"] = g[f"pred_{key}"]     # no market line yet: the model alone
        for season in sorted(g["season"][g["season"] >= 2017].unique()):
            here = g["season"] == season
            # played games use only earlier seasons; upcoming games use everything played so far
            for rows, before in ((here & done, done & (g["season"] < season)), (here & ~done, done)):
                w = trust(g.loc[before, actual], g.loc[before, f"pred_{key}"], g.loc[before, f"market_{key}"])
                ok = rows & g[f"market_{key}"].notna()
                g.loc[ok, f"blend_{key}"] = g.loc[ok, f"market_{key}"] + w * (g.loc[ok, f"pred_{key}"] - g.loc[ok, f"market_{key}"])
                g.loc[ok, f"trust_{key}"] = w
        scored = done & g["trust_" + key].notna()
        g[f"blend_sigma_{key}"] = float(np.sqrt(((g.loc[scored, actual] - g.loc[scored, f"blend_{key}"]) ** 2).mean()))
    return g


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
