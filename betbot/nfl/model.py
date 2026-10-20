"""Player stat model: predicts a player's stat for a game and the probability it goes Over a line.

1. Baseline: recency-weighted average of the player's previous games, pulled toward his position's average
   from the previous season when he has few games.
2. Context: multiplicative adjustments fitted by least squares on past seasons (not hand-picked): opponent's
   defense vs. his position, team implied points, spread (game script), wind, cold, home, recent usage trend,
   his injury report / practice status, his team's starting QB out, opposing defensive starters out.
3. Probability: the empirical distribution of past standardized errors gives P(stat > line).

Every feature for a game uses only games played before it, so the same code serves live predictions and
backtests without peeking.
"""
import json
import math
import sqlite3
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from betbot.names import normalize

HALFLIFE = 6          # games; how fast older games fade from the baseline
SHRINK_GAMES = 2      # pseudo-games of the position average added to a player's baseline
MIN_PRIOR_GAMES = 4   # training rows need at least this many previous games
FIRST_SEASON = 2013   # snap counts start in 2013

# stat column -> (usage column whose recent trend matters, offset that keeps log ratios finite)
STATS = {
    "passing_yards": ("attempts", 20.0), "passing_tds": ("attempts", 0.5), "completions": ("attempts", 2.0),
    "attempts": ("attempts", 3.0), "passing_interceptions": ("attempts", 0.5),
    "rushing_yards": ("carries", 5.0), "carries": ("carries", 1.0), "rushing_tds": ("carries", 0.3),
    "receptions": ("target_share", 1.0), "receiving_yards": ("target_share", 5.0),
    "receiving_tds": ("target_share", 0.3),
}
FEATURES = ["def_vs_pos", "implied_points", "spread", "wind", "cold", "home", "usage_trend",
            "snap_pct", "starter_flag",
            "questionable", "limited_practice", "qb_out", "opp_def_starters_out", "rest", "prime", "div_game",
            "ol_out", "opp_secondary_out", "opp_rush_out"]
MISSING = ("Out", "Doubtful", "Injured Reserve")


# ---------- data ----------

def history(conn, stat, positions, first_season=FIRST_SEASON):
    usage = STATS[stat][0]
    return pd.read_sql_query(
        f'SELECT player_id, player_display_name AS player, position_group, season, week, game_id, team,'
        f' opponent_team, "{stat}" AS y, "{usage}" AS usage FROM nfl_player_weeks'
        f" WHERE season >= ? AND position_group IN ({', '.join('?' * len(positions))})",
        conn, params=(first_season, *positions))


def is_prime(weekday, gametime):
    """1.0 for Monday / Thursday games and anything kicking off 7pm or later (the hype games), else 0.0."""
    return (weekday.isin(["Monday", "Thursday"]) | (gametime.fillna("13:00") >= "19:00")).astype(float)


def game_context(conn):
    """Per (game_id, team): implied points, spread from the team's side (negative = favored), home, weather."""
    g = pd.read_sql_query(
        "SELECT game_id, season, week, home_team, away_team, home_implied_points, away_implied_points,"
        " home_spread, wind_mph, temp_f, roof, home_rest, away_rest, div_game, weekday, gametime"
        " FROM nfl_game_context", conn)
    prime = is_prime(g["weekday"], g["gametime"])
    outdoors = g["roof"].isin(["outdoors", "open"]) | g["roof"].isna()
    rows = []
    for side, other in (("home", "away"), ("away", "home")):
        rows.append(pd.DataFrame({
            "game_id": g["game_id"], "team": g[f"{side}_team"], "home": 1 if side == "home" else 0,
            "implied_points": g[f"{side}_implied_points"],
            "spread": g["home_spread"] if side == "home" else -g["home_spread"],
            "wind": np.where(outdoors, g["wind_mph"], 0.0), "temp": np.where(outdoors, g["temp_f"], 70.0),
            "rest": ((g[f"{side}_rest"] - g[f"{other}_rest"]).clip(-7, 7) / 7).fillna(0.0),
            "prime": prime, "div_game": g["div_game"].fillna(0).astype(float)}))
    return pd.concat(rows, ignore_index=True)


def injury_flags(conn):
    """Per (season, week, gsis_id): questionable and limited-practice flags; empty if reports aren't loaded."""
    try:
        r = pd.read_sql_query("SELECT season, week, gsis_id AS player_id, report_status, practice_status,"
                              " practice_primary_injury FROM nfl_injury_reports", conn)
    except Exception:  # table not loaded yet
        return pd.DataFrame(columns=["season", "week", "player_id", "questionable", "limited_practice", "status"])
    injured = ~r["practice_primary_injury"].fillna("").str.startswith("Not injury related")
    r["questionable"] = (r["report_status"] == "Questionable").astype(float)
    r["limited_practice"] = (injured & r["practice_status"].fillna("").str.contains("Limited|Did Not", regex=True)).astype(float)
    r["status"] = r["report_status"]
    return r.groupby(["season", "week", "player_id"], as_index=False).agg(
        questionable=("questionable", "max"), limited_practice=("limited_practice", "max"), status=("status", "first"))


def _recent_qb_out(conn, qbs, upcoming, games=8):
    """1 for each upcoming (season, week, team) whose QB with the most attempts over its last `games` games (the one
    the team's ratings reflect) is on ESPN's current Out / Doubtful / IR list. The previous-game check misses a QB
    who has already been out a game, since his backup is then the 'previous starter'."""
    placeholders = ", ".join("?" * len(MISSING))
    out = {n for (n,) in conn.execute(f"SELECT name_key FROM nfl_injuries WHERE position = 'QB' AND status IN"
                                      f" ({placeholders})", MISSING)}
    rows = []
    # today's list is a snapshot for each team's next game only, not for weeks further out
    nxt = upcoming[["season", "week", "team"]].drop_duplicates().sort_values(["season", "week"]).groupby("team").head(1)
    for season, week, team in nxt.itertuples(index=False):
        past = qbs[(qbs["team"] == team) & ((qbs["season"] < season) | ((qbs["season"] == season) & (qbs["week"] < week)))]
        last = past[["season", "week"]].drop_duplicates().sort_values(["season", "week"]).tail(games)
        recent = past.merge(last)
        if recent.empty:
            continue
        top = recent.groupby("name")["attempts"].sum().idxmax()
        rows.append((season, week, team, float(normalize(top) in out)))
    return pd.DataFrame(rows, columns=["season", "week", "team", "recent_qb_out"])


# position groups whose missing starters matter differently: (positions, snap column that defines a starter)
GROUPS = {"ol": (("T", "G", "C", "OL", "OT", "OG"), "offense_pct"), "wr_te": (("WR", "TE"), "offense_pct"),
          "rb": (("RB", "FB", "HB"), "offense_pct"), "pass_rush": (("DE", "DT", "NT", "DL"), "defense_pct"),
          "lb": (("LB", "ILB", "OLB", "MLB"), "defense_pct"), "cb": (("CB", "DB"), "defense_pct"),
          "safety": (("S", "FS", "SS"), "defense_pct")}
GROUP_COLUMNS = [f"{g}_out" for g in GROUPS]


def group_absences(conn, out, team_weeks, upcoming=None):
    """Per (season, week, team): how many starters (60%+ of snaps over the previous 3 games) are Out / Doubtful / IR,
    by position group. `out` has (season, week, player_id) of players listed out; `team_weeks` is every
    (season, week, team) to report. For upcoming games, today's ESPN list (which includes players on IR, who never
    appear in practice reports) counts for each team's next game."""
    empty = pd.DataFrame(columns=["season", "week", "team"] + GROUP_COLUMNS)
    try:
        snaps = pd.read_sql_query(
            "SELECT s.season, s.week, s.team, p.gsis_id AS player_id, s.position, s.offense_pct, s.defense_pct"
            " FROM nfl_snap_counts s JOIN nfl_players p ON p.pfr_id = s.pfr_player_id", conn)
    except (sqlite3.OperationalError, pd.errors.DatabaseError) as e:
        if "no such table" in str(e):
            return empty
        raise
    if snaps.empty:
        return empty
    snaps["position"] = snaps["position"].fillna("").str.split("/").str[0]    # "C/G" counts as the first label
    snaps = snaps.sort_values(["player_id", "season", "week"])
    for column in ("offense_pct", "defense_pct"):       # each player's average over his previous 3 games' snaps
        played = snaps[column].where(snaps[column] > 0)
        snaps[f"recent_{column}"] = played.groupby(snaps["player_id"]).transform(
            lambda x: x.rolling(3, min_periods=2).mean())
    tw = team_weeks[["season", "week", "team"]].drop_duplicates().sort_values(["team", "season", "week"])
    tw["next_season"] = tw.groupby("team")["season"].shift(-1)
    tw["next_week"] = tw.groupby("team")["week"].shift(-1)
    out = out[["season", "week", "player_id"]].assign(out=1)
    espn = pd.DataFrame(columns=["season", "week", "player_id", "out"])
    if upcoming is not None and len(upcoming):
        nxt = upcoming[["season", "week", "team"]].drop_duplicates().sort_values(["season", "week"]).groupby("team").head(1)
        listed = pd.read_sql_query(
            f"SELECT i.team, p.gsis_id AS player_id FROM nfl_injuries i JOIN nfl_players p"
            f" ON lower(p.display_name) = lower(i.player) WHERE i.status IN ({', '.join('?' * len(MISSING))})",
            conn, params=list(MISSING))
        espn = listed.merge(nxt, on="team")[["season", "week", "player_id"]].assign(out=1)
    out = pd.concat([out, espn]).drop_duplicates(["season", "week", "player_id"])
    parts = []
    for group, (positions, column) in GROUPS.items():
        s = snaps[snaps["position"].isin(positions) & (snaps[column] > 0) & (snaps[f"recent_{column}"] >= 0.6)]
        s = s[["season", "week", "team", "player_id"]]
        # a starter as of week w is listed for his team's next game
        s = s.merge(tw, on=["season", "week", "team"]).dropna(subset=["next_week"])
        s = s.assign(season=s["next_season"].astype(int), week=s["next_week"].astype(int))
        c = s.merge(out, on=["season", "week", "player_id"]).groupby(["season", "week", "team"]).size()
        parts.append(c.rename(f"{group}_out"))
    return pd.concat(parts, axis=1).fillna(0).reset_index()


def team_absences(conn, flags, upcoming=None):
    """Per (season, week, team): qb_out (last game's QB listed Out/Doubtful) and def_starters_out (players with
    60%+ of defensive snaps over the team's previous 3 games listed Out/Doubtful)."""
    out = flags[flags["status"].isin(MISSING)][["season", "week", "player_id"]].assign(out=1)
    qbs = pd.read_sql_query("SELECT season, week, team, player_id, player_display_name AS name, attempts"
                            " FROM nfl_player_weeks WHERE position_group = 'QB' AND attempts > 0", conn)
    starter = qbs.sort_values("attempts").groupby(["season", "week", "team"]).tail(1)
    weeks = starter[["season", "week", "team"]]
    if upcoming is not None and len(upcoming):
        weeks = pd.concat([weeks, upcoming[["season", "week", "team"]]]).drop_duplicates()
    weeks = weeks.sort_values(["team", "season", "week"])
    weeks = weeks.merge(starter[["season", "week", "team", "player_id"]], how="left")
    weeks["prior_qb"] = weeks.groupby("team")["player_id"].shift(1)
    weeks = weeks.merge(out.rename(columns={"player_id": "prior_qb"}), how="left", on=["season", "week", "prior_qb"])
    qb = weeks.assign(qb_out=weeks["out"].fillna(0))[["season", "week", "team", "qb_out"]]
    if upcoming is not None and len(upcoming):  # today's ESPN list: the QB behind the team's recent results is out
        qb = qb.merge(_recent_qb_out(conn, qbs, upcoming), how="left", on=["season", "week", "team"])
        qb["qb_out"] = qb[["qb_out", "recent_qb_out"]].max(axis=1)
        qb = qb.drop(columns="recent_qb_out")

    try:
        snaps = pd.read_sql_query(
            "SELECT s.season, s.week, s.team, p.gsis_id AS player_id, s.defense_pct FROM nfl_snap_counts s"
            " JOIN nfl_players p ON p.pfr_id = s.pfr_player_id WHERE s.defense_pct > 0", conn)
    except Exception:
        snaps = pd.DataFrame(columns=["season", "week", "team", "player_id", "defense_pct"])
    if len(snaps):
        snaps = snaps.sort_values(["player_id", "season", "week"])
        snaps["recent"] = snaps.groupby("player_id")["defense_pct"].transform(lambda s: s.rolling(3, min_periods=2).mean())
        starters = snaps[snaps["recent"] >= 0.6][["season", "week", "team", "player_id"]]
        # a starter as of week w is listed for week w+1 (next team game)
        team_weeks = qb[["season", "week", "team"]].sort_values(["team", "season", "week"])
        team_weeks["next_season"] = team_weeks.groupby("team")["season"].shift(-1)
        team_weeks["next_week"] = team_weeks.groupby("team")["week"].shift(-1)
        starters = starters.merge(team_weeks, on=["season", "week", "team"]).dropna(subset=["next_week"])
        starters = starters.assign(season=starters["next_season"].astype(int), week=starters["next_week"].astype(int))
        d = starters.merge(out, on=["season", "week", "player_id"]).groupby(["season", "week", "team"]).size()
        d = d.rename("def_starters_out").reset_index()
    else:
        d = pd.DataFrame(columns=["season", "week", "team", "def_starters_out"])
    res = qb.merge(d, how="left", on=["season", "week", "team"]).fillna({"def_starters_out": 0})
    groups = group_absences(conn, out.drop(columns="out"), qb, upcoming)
    res = res.merge(groups, how="left", on=["season", "week", "team"])
    res[GROUP_COLUMNS] = res.reindex(columns=GROUP_COLUMNS).fillna(0)
    return res


# ---------- features ----------

def snap_shares(conn, first_season=FIRST_SEASON):
    """Each player's offensive snap share per game (player, season, week, offense_pct); empty if snaps aren't loaded."""
    cols = ["player_id", "season", "week", "offense_pct"]
    try:
        snaps = pd.read_sql_query("SELECT player_id, season, week, offense_pct FROM nfl_player_usage"
                                  " WHERE season >= ? AND offense_pct IS NOT NULL", conn, params=(first_season,))
    except (sqlite3.OperationalError, pd.errors.DatabaseError):  # snap counts not loaded
        snaps = pd.DataFrame(columns=cols)
    return snaps.drop_duplicates(["player_id", "season", "week"])


LONG_HALFLIFE = 16  # games; the slow average that stands in for how a book's line lags a player's recent form
FORM_COLUMNS = ["team", "season", "week", "margin3", "att3", "car3"]


def no_form():
    """An empty team_form with the right column types, so merging it into a frame is safe."""
    return pd.DataFrame({"team": pd.Series(dtype=object), "season": pd.Series(dtype="int64"),
                         "week": pd.Series(dtype="int64"), "margin3": pd.Series(dtype=float),
                         "att3": pd.Series(dtype=float), "car3": pd.Series(dtype=float)})


def team_form(conn, first_season=FIRST_SEASON):
    """Per team and scheduled game: the team's margin, pass attempts and carries averaged over its previous 3
    games (from the games before it only). Includes games not yet played, so upcoming games get their form."""
    try:
        games = pd.read_sql_query("SELECT game_id, season, week, home_team, away_team, home_score, away_score"
                                  " FROM nfl_games WHERE season >= ?", conn, params=(first_season - 1,))
        plays = pd.read_sql_query("SELECT team, game_id, sum(attempts) AS att, sum(carries) AS car"
                                  " FROM nfl_player_weeks WHERE season >= ? GROUP BY team, game_id",
                                  conn, params=(first_season - 1,))
    except (sqlite3.OperationalError, pd.errors.DatabaseError) as e:
        # without team form the "script" signal is missing and the conviction score can't be computed: say so
        warnings.warn(f"team form unavailable ({e}); game-script signals will be empty", RuntimeWarning)
        return no_form()
    home = games.assign(team=games["home_team"], margin=games["home_score"] - games["away_score"])
    away = games.assign(team=games["away_team"], margin=games["away_score"] - games["home_score"])
    t = pd.concat([home, away])[["team", "game_id", "season", "week", "margin"]].merge(plays, how="left",
                                                                                     on=["team", "game_id"])
    t = t.sort_values(["team", "season", "week"])
    for column, out in (("margin", "margin3"), ("att", "att3"), ("car", "car3")):
        t[out] = t.groupby("team")[column].transform(lambda s: s.shift(1).rolling(3, min_periods=1).mean())
    return t[FORM_COLUMNS]


def features(conn, stat, positions, upcoming=None, first_season=FIRST_SEASON, df=None, context=None, flags=None,
             absences=None, snaps=None, form=None):
    """History rows (y known) plus optional upcoming rows (y NaN), each with features from earlier games only.
    upcoming: DataFrame with player_id, player, position_group, season, week, game_id, team, opponent_team."""
    df = history(conn, stat, positions, first_season) if df is None else df.copy()
    if upcoming is not None and len(upcoming):
        df = pd.concat([df, upcoming.assign(y=np.nan, usage=np.nan)], ignore_index=True)
    df = df.sort_values(["player_id", "season", "week"]).reset_index(drop=True)
    by_player = df.groupby("player_id")

    df["prior_n"] = by_player["y"].transform(lambda s: s.shift(1).notna().cumsum())
    df["ewm"] = by_player["y"].transform(lambda s: s.shift(1).ewm(halflife=HALFLIFE, ignore_na=True).mean())
    df["usage_recent"] = by_player["usage"].transform(lambda s: s.shift(1).rolling(3, min_periods=1).mean())
    df["usage_ewm"] = by_player["usage"].transform(lambda s: s.shift(1).ewm(halflife=HALFLIFE, ignore_na=True).mean())

    # recent offensive snap share (role): each game's share joined first, then shifted per player like every other
    # feature, so an upcoming game gets the share from his previous games (joining already-shifted values left
    # upcoming rows with none, i.e. every live prediction looked like a backup)
    snaps = snap_shares(conn, first_season) if snaps is None else snaps
    df = df.merge(snaps, how="left", on=["player_id", "season", "week"])
    by_player = df.groupby("player_id")
    df["snap_recent"] = by_player["offense_pct"].transform(lambda s: s.shift(1).rolling(3, min_periods=1).mean())
    df["snap_ewm"] = by_player["offense_pct"].transform(lambda s: s.shift(1).ewm(halflife=HALFLIFE, ignore_na=True).mean())
    # recent and slow-moving form of the stat itself (the conviction score compares them with the line)
    df["y_recent3"] = by_player["y"].transform(lambda s: s.shift(1).rolling(3, min_periods=1).mean())
    df["y_long"] = by_player["y"].transform(lambda s: s.shift(1).ewm(halflife=LONG_HALFLIFE, ignore_na=True).mean())

    # previous season's average for the position (players with 4+ games), the shrink target
    played = df[df["y"].notna()]
    pos_season = played.groupby(["position_group", "season"])["y"].mean().rename("pos_mean").reset_index()
    pos_season["season"] += 1
    df = df.merge(pos_season, how="left", on=["position_group", "season"])
    df["pos_mean"] = df["pos_mean"].fillna(played["y"].mean() if len(played) else 0.0)
    last_season = played.groupby(["player_id", "season"])["y"].mean().rename("y_prior_season").reset_index()
    last_season["season"] += 1  # his average over the whole previous season
    df = df.merge(last_season, how="left", on=["player_id", "season"])
    n = df["prior_n"]
    df["base"] = np.where(n > 0, (n * df["ewm"].fillna(0) + SHRINK_GAMES * df["pos_mean"]) / (n + SHRINK_GAMES),
                          df["pos_mean"])

    # what each defense allowed to this position group, over its previous 8 games, vs. the league
    allowed = (df.groupby(["season", "week", "game_id", "opponent_team"])["y"].sum(min_count=1)
               .rename("allowed").reset_index().sort_values(["opponent_team", "season", "week"]))
    allowed["def_prior"] = allowed.groupby("opponent_team")["allowed"].transform(
        lambda s: s.shift(1).rolling(8, min_periods=2).mean())
    allowed["def_games"] = allowed.groupby("opponent_team")["allowed"].transform(lambda s: s.shift(1).notna().cumsum().clip(upper=8))
    league = allowed.groupby(["season", "week"])["allowed"].mean().rename("league").reset_index()
    league["league_prior"] = league["league"].shift(1).rolling(8, min_periods=1).mean()
    allowed = allowed.merge(league[["season", "week", "league_prior"]], on=["season", "week"])
    ratio = np.log((allowed["def_prior"] + 1) / (allowed["league_prior"] + 1))
    allowed["def_vs_pos"] = (ratio * allowed["def_games"] / (allowed["def_games"] + 4)).fillna(0)
    df = df.merge(allowed[["game_id", "opponent_team", "def_vs_pos"]], how="left", on=["game_id", "opponent_team"])

    ctx = game_context(conn) if context is None else context
    df = df.merge(ctx, how="left", on=["game_id", "team"])
    form = team_form(conn, first_season) if form is None else form
    df = df.merge(form, how="left", on=["team", "season", "week"])
    # the league's average recent pass attempts as of that week (each team's att3 uses only its earlier games)
    if len(form):
        league_att = form.groupby(["season", "week"])["att3"].mean().rename("league_att").reset_index()
        df = df.merge(league_att, how="left", on=["season", "week"])
    else:
        df["league_att"] = np.nan
    flags = injury_flags(conn) if flags is None else flags
    df = df.merge(flags[["season", "week", "player_id", "questionable", "limited_practice"]], how="left",
                  on=["season", "week", "player_id"])
    both_sides = None
    if upcoming is not None and len(upcoming):
        both_sides = pd.concat([upcoming[["season", "week", "team"]],
                                upcoming[["season", "week", "opponent_team"]].rename(columns={"opponent_team": "team"})])
    teams = team_absences(conn, flags, both_sides) if absences is None else absences
    df = df.merge(teams[["season", "week", "team", "qb_out"]], how="left", on=["season", "week", "team"])
    df = df.merge(teams[["season", "week", "team", "def_starters_out"]].rename(
        columns={"team": "opponent_team", "def_starters_out": "opp_def_starters_out"}),
        how="left", on=["season", "week", "opponent_team"])

    if "ol_out" in teams:
        df = df.merge(teams[["season", "week", "team", "ol_out"]], how="left", on=["season", "week", "team"])
        opp = teams.assign(opp_secondary_out=teams["cb_out"] + teams["safety_out"], opp_rush_out=teams["pass_rush_out"])
        df = df.merge(opp[["season", "week", "team", "opp_secondary_out", "opp_rush_out"]].rename(columns={"team": "opponent_team"}),
                      how="left", on=["season", "week", "opponent_team"])
    df["implied_points"] = np.log(df["implied_points"].fillna(22.0).clip(lower=3) / 22.0)
    df["spread"] = df["spread"].fillna(0.0) / 7.0
    df["wind"] = (df["wind"].fillna(0.0) - 10).clip(lower=0) / 10.0
    df["cold"] = (df["temp"].fillna(70.0) < 32).astype(float)
    eps = 0.02 if STATS[stat][0] == "target_share" else 1.0
    df["usage_trend"] = np.log((df["usage_recent"].fillna(0) + eps) / (df["usage_ewm"].fillna(0) + eps)).fillna(0)
    # snap_pct feature: use recent then ewm, and a binary starter flag when recent share >= 0.6
    # a player with no earlier snap data (rookie, new team, snaps not loaded) gets his position's average share,
    # not 0 (which would read as a backup and bias the prediction down)
    # the position's average share in the PREVIOUS season, so no later data leaks into the filler
    typical = df.groupby(["position_group", "season"])["snap_recent"].mean().rename("typical_snap").reset_index()
    typical["season"] += 1
    df = df.merge(typical, how="left", on=["position_group", "season"])
    df["snap_pct"] = df["snap_recent"].fillna(df["snap_ewm"]).fillna(df["typical_snap"]).fillna(0.5)
    df["starter_flag"] = (df["snap_pct"] >= 0.6).astype(float)
    for col in ("rest", "prime", "div_game", "ol_out", "opp_secondary_out", "opp_rush_out"):   # missing = no effect
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0) if col in df else 0.0
    for col in ("questionable", "limited_practice", "qb_out", "opp_def_starters_out", "home"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.5 if col == "home" else 0.0)
    return df


# ---------- fitting and probabilities ----------

def _design(df):
    return np.column_stack([np.ones(len(df))] + [df[c].to_numpy(dtype=float) for c in FEATURES])


def fit(df, stat):
    """Least-squares fit on rows with a known result and enough history. Returns a JSON-able params dict."""
    c = STATS[stat][1]
    train = df[df["y"].notna() & (df["prior_n"] >= MIN_PRIOR_GAMES)]
    if len(train) < 200:
        raise ValueError(f"only {len(train)} training rows for {stat}")
    X = _design(train)
    # yards can be negative (a -6 yard rushing day); the log-scale fit floors at 0, the error distribution doesn't
    base = train["base"].clip(lower=0).to_numpy()
    t = np.log((train["y"].clip(lower=0).to_numpy() + c) / (base + c))
    beta, *_ = np.linalg.lstsq(X, t, rcond=None)
    mu_raw = np.clip((base + c) * np.exp(X @ beta) - c, 0, None)
    scale = train["y"].sum() / max(mu_raw.sum(), 1e-9)
    mu = mu_raw * scale
    err = np.abs(train["y"].to_numpy() - mu)
    a, b = np.linalg.lstsq(np.column_stack([np.ones(len(mu)), mu]), err, rcond=None)[0]
    params = {"stat": stat, "beta": dict(zip(["intercept"] + FEATURES, map(float, beta))), "scale": float(scale),
              "sigma_a": float(a), "sigma_b": float(b), "offset": c, "rows": int(len(train))}
    sigma = _sigma(params, mu)
    params["z_quantiles"] = np.quantile((train["y"].to_numpy() - mu) / sigma, np.linspace(0, 1, 401)).tolist()
    return params


def _sigma(params, mu):
    return np.maximum(math.sqrt(math.pi / 2) * (params["sigma_a"] + params["sigma_b"] * np.asarray(mu)), 0.5)


def predict(df, params):
    """Adds mu (expected stat) and sigma to each row."""
    beta = np.array([params["beta"][k] for k in ["intercept"] + FEATURES])
    c = params["offset"]
    mu = np.clip((df["base"].clip(lower=0).to_numpy() + c) * np.exp(_design(df) @ beta) - c, 0, None) * params["scale"]
    return df.assign(mu=mu, sigma=_sigma(params, mu))


def _raw_prob_over(params, mu, sigma, line):
    z = (np.asarray(line, dtype=float) - np.asarray(mu, dtype=float)) / np.asarray(sigma, dtype=float)
    return np.clip(1 - np.interp(z, params["z_quantiles"], np.linspace(0, 1, 401)), 0.001, 0.999)


def prob_over(params, mu, sigma, line):
    """P(stat > line): fitted error distribution, then recalibrated by how earlier out-of-sample probabilities
    actually hit (when params has 'calib'); clipped to 1%-99% (the model is never that sure)."""
    p = _raw_prob_over(params, mu, sigma, line)
    if "calib" in params:
        p = recalibrate(p, params["calib"])
    # the backtest showed the raw model is overconfident: never more than 99% sure either way
    return np.clip(p, 0.01, 0.99)


def _logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def recalibrate(p, calib):
    a, b = calib
    return 1 / (1 + np.exp(-(a + b * _logit(p))))


def fit_public_discount(p, lean, gap, won, ridge=20.0):
    """Hype discount: how the public's side moves P(Over) beyond our probability p. logit(P) = logit(p) + a*lean +
    b*gap, where lean = (% of tickets on the Over - 50) / 50 and gap = (% of handle - % of tickets) / 50. A strong
    ridge keeps (a, b) at zero until the data says otherwise. Returns (a, b)."""
    off = _logit(p)
    X = np.column_stack([np.asarray(lean, float), np.asarray(gap, float)])
    y = np.asarray(won, dtype=float)
    w = np.zeros(2)
    for _ in range(50):
        mu = 1 / (1 + np.exp(-(off + X @ w)))
        grad = X.T @ (mu - y) + ridge * w
        hess = (X * (mu * (1 - mu))[:, None]).T @ X + ridge * np.eye(2)
        step = np.linalg.solve(hess, grad)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return float(w[0]), float(w[1])


def apply_public_discount(p, lean, gap, coef):
    a, b = coef
    return 1 / (1 + np.exp(-(_logit(p) + a * lean + b * gap)))


def fit_recalibration(p, won, ridge=1.0):
    """Platt scaling: logit(p_true) = a + b * logit(p). Newton steps with a small ridge toward (0, 1)."""
    x, y = _logit(p), np.asarray(won, dtype=float)
    X = np.column_stack([np.ones(len(x)), x])
    w = np.array([0.0, 1.0])
    for _ in range(50):
        q = 1 / (1 + np.exp(-(X @ w)))
        grad = X.T @ (q - y) + ridge * (w - [0.0, 1.0])
        hess = (X * (q * (1 - q))[:, None]).T @ X + ridge * np.eye(2)
        step = np.linalg.solve(hess, grad)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return [float(w[0]), float(w[1])]


def fit_calibrated(df, stat, folds=4):
    """fit(), plus a recalibration learned walk-forward on the last `folds` seasons: each season is predicted by
    a model fitted only on earlier seasons, against lines at the baseline and half a deviation either side."""
    params = fit(df, stat)
    seasons = sorted(df.loc[df["y"].notna(), "season"].unique())
    ps, wins = [], []
    for season in seasons[-folds:]:
        train = df[df["season"] < season]
        test = df[(df["season"] == season) & df["y"].notna() & (df["prior_n"] >= MIN_PRIOR_GAMES)]
        test = test[test["base"] >= 0.5 * test["pos_mean"]]
        if test.empty or (train["y"].notna() & (train["prior_n"] >= MIN_PRIOR_GAMES)).sum() < 200:
            continue
        inner = fit(train, stat)
        pred = predict(test, inner)
        for k in (-0.5, 0.0, 0.5):
            line = np.floor(pred["base"] + k * pred["sigma"]) + 0.5
            ps += list(_raw_prob_over(inner, pred["mu"], pred["sigma"], line))
            wins += list((pred["y"] > line).astype(int))
    if len(ps) >= 200:
        params["calib"] = fit_recalibration(ps, wins)
    return params


# ---------- stored params ----------

def save_params(conn, params, trained_through):
    with conn:
        conn.execute("INSERT OR REPLACE INTO model_params VALUES (?, ?, ?, ?)",
                     (params["stat"], trained_through, json.dumps(params),
                      datetime.now(timezone.utc).isoformat(timespec="seconds")))


def load_params(conn, stat):
    row = conn.execute("SELECT params FROM model_params WHERE stat = ?", (stat,)).fetchone()
    return json.loads(row[0]) if row else None


def latest_week(conn):
    """season * 100 + week of the latest game with stats: the data version the stored params were fitted on."""
    row = conn.execute("SELECT max(season * 100 + week) FROM nfl_player_weeks").fetchone()
    return row[0] or 0


def fitted(conn, stat, positions, df=None):
    """Stored params fitted on every finished game, refitting (and storing) them when new games arrive, or when
    they were fitted before a feature was added. df: an already-built features() frame to fit from (its upcoming
    rows are ignored), so callers don't build it twice."""
    version = latest_week(conn)
    row = conn.execute("SELECT params, trained_through FROM model_params WHERE stat = ?", (stat,)).fetchone()
    if row and row[1] >= version:
        params = json.loads(row[0])
        if set(FEATURES) <= set(params["beta"]):
            return params
    params = fit_calibrated(features(conn, stat, positions) if df is None else df, stat)
    save_params(conn, params, version)
    return params
