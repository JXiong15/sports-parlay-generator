"""How NFL bet outcomes in the same game move together, measured from 2013-2025 results and cached.

Each leg is driven by one "surprise" (actual minus what was expected):
  spread / moneyline: the home team's margin minus the closing spread (+ for home, - for away)
  total:              total points minus the closing total (+ for over, - for under)
  player prop:        his stat minus the model's expectation, in standard deviations (+ for Over, - for Under)
The correlations between those surprises (by stat and by relation: same player, teammates, opponents, game total,
his team's margin) become the correlation between legs.
"""
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from betbot.nfl import model
from betbot.nfl.signals.regression import MARKETS

FIRST, LAST = 2013, 2025
MIN_PAIRS = 200
SPREAD_ML = 0.95  # spread and moneyline on one team are almost the same bet


def _games(conn):
    g = pd.read_sql_query("SELECT game_id, home_team, away_team, home_score, away_score, spread_line, total_line"
                          " FROM nfl_games WHERE season BETWEEN ? AND ? AND home_score IS NOT NULL"
                          " AND spread_line IS NOT NULL AND total_line IS NOT NULL", conn, params=(FIRST, LAST))
    g["H"] = (g["home_score"] - g["away_score"]) - g["spread_line"]  # nflverse spread_line = expected home margin
    g["T"] = (g["home_score"] + g["away_score"]) - g["total_line"]
    return g


def _surprises(conn, stat):
    positions = next(p for s, p in MARKETS.values() if s == stat)
    df = model.features(conn, stat, positions)
    params = model.fitted(conn, stat, positions, df)
    df = df[df["y"].notna() & (df["prior_n"] >= model.MIN_PRIOR_GAMES) & (df["season"].between(FIRST, LAST))]
    df = model.predict(df[df["base"] >= 0.5 * df["pos_mean"]], params)
    return df.assign(z=(df["y"] - df["mu"]) / df["sigma"])[["game_id", "team", "player_id", "z"]]


def _corr(a, b):
    return (float(np.corrcoef(a, b)[0, 1]), int(len(a))) if len(a) >= MIN_PAIRS else (0.0, int(len(a)))


def estimate(conn, stats):
    """Correlations for these stats, from history. Returns {key: [rho, n]}."""
    g = _games(conn)
    out = {"H|T": _corr(g["H"], g["T"])}
    z = {s: _surprises(conn, s).merge(g[["game_id", "home_team", "H", "T"]], on="game_id") for s in stats}
    for s, d in z.items():
        team_margin = np.where(d["team"] == d["home_team"], d["H"], -d["H"])
        out[f"{s}|T"] = _corr(d["z"], d["T"])
        out[f"{s}|own_margin"] = _corr(d["z"], team_margin)
    for i, a in enumerate(stats):
        for b in stats[i:]:
            da, db = z[a][["game_id", "team", "player_id", "z"]], z[b][["game_id", "team", "player_id", "z"]]
            same = da.merge(db, on=["game_id", "player_id"], suffixes=("_a", "_b"))
            if a != b:
                out[f"{a}|{b}|same_player"] = _corr(same["z_a"], same["z_b"])
            mates = da.merge(db, on=["game_id", "team"], suffixes=("_a", "_b"))
            mates = mates[mates["player_id_a"] != mates["player_id_b"]]
            out[f"{a}|{b}|teammates"] = _corr(mates["z_a"], mates["z_b"])
            opp = da.merge(db, on="game_id", suffixes=("_a", "_b"))
            opp = opp[opp["team_a"] != opp["team_b"]]
            out[f"{a}|{b}|opponents"] = _corr(opp["z_a"], opp["z_b"])
    return out


def cached(conn, stats):
    """Estimates for these stats, computing and storing any that aren't cached yet."""
    have = {k: json.loads(v) for k, v in conn.execute("SELECT key, value FROM parlay_correlations")}
    need = [s for s in stats if f"{s}|T" not in have]
    if need or "H|T" not in have:
        fresh = estimate(conn, sorted(set(stats) | {k.split("|")[0] for k in have if k.count("|") == 1 and k != "H|T"}))
        with conn:
            for k, v in fresh.items():
                conn.execute("INSERT OR REPLACE INTO parlay_correlations VALUES (?, ?, ?)",
                             (k, json.dumps(v), datetime.now(timezone.utc).isoformat(timespec="seconds")))
        have.update(fresh)
    return {k: v[0] for k, v in have.items()}


def _pair_key(table, a, b, relation):
    for x, y in ((a, b), (b, a)):
        key = f"{x}|{y}|{relation}"
        if key in table:
            return table[key]
    return 0.0


def rho(table):
    """Leg-pair correlation function for betbot.parlay, from cached estimates."""
    def sign(leg):
        return 1 if leg["side"] in ("Over", "home", "over") else -1

    def f(a, b):
        if a["kind"] == "prop" and b["kind"] == "prop":
            relation = ("same_player" if a["player_id"] == b["player_id"] else
                        "teammates" if a["team"] == b["team"] else "opponents")
            base = _pair_key(table, a["stat"], b["stat"], relation)
            return base * sign(a) * sign(b)
        if a["kind"] == "prop" or b["kind"] == "prop":
            p, g = (a, b) if a["kind"] == "prop" else (b, a)
            if g["market"] == "total":
                return table.get(f"{p['stat']}|T", 0.0) * sign(p) * sign(g)
            # the bet is on one team's margin; the prop correlates with the player's own team's margin
            bet_team = g["home"] if g["side"] == "home" else g["away"]
            same = 1 if bet_team == p["team"] else -1
            return table.get(f"{p['stat']}|own_margin", 0.0) * sign(p) * same
        markets = {a["market"], b["market"]}
        if markets == {"total"}:
            return 1.0 if a["side"] == b["side"] else -1.0
        if "total" in markets:
            g, t = (a, b) if b["market"] == "total" else (b, a)
            return table.get("H|T", 0.0) * sign(g) * sign(t)
        same_side = a["side"] == b["side"]
        return (SPREAD_ML if same_side else -SPREAD_ML) if markets == {"spread", "moneyline"} else (1.0 if same_side else -1.0)
    return f
