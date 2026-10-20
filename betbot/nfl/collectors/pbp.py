"""nflverse play-by-play (free), reduced to one row per team per game of what that offense did: EPA per pass and per
rush, success rate, sack and QB-hit rates (pressure), pass rate over expected and pace. The defense's numbers are the
opponent's row for the same game. Only these aggregates are stored, not the plays.

    .venv/bin/python -m betbot.nfl.collectors.pbp            # 2012 to now, skipping seasons already saved
"""
import io
import sys

import pandas as pd
import requests

from betbot.nfl.collectors.nflverse import current_season

URL = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{}.csv.gz"
FIRST_SEASON = 2012
COLUMNS = ["game_id", "season", "week", "season_type", "posteam", "defteam", "pass", "rush", "qb_dropback", "sack",
           "qb_hit", "epa", "success", "pass_oe", "yardline_100", "qb_kneel", "qb_spike"]
TABLE = "nfl_team_game_pbp"


def aggregate(plays):
    """One row per (game_id, team) from play-by-play rows."""
    p = plays[plays["posteam"].notna() & ((plays["pass"] == 1) | (plays["rush"] == 1))]
    p = p[(p["qb_kneel"].fillna(0) == 0) & (p["qb_spike"].fillna(0) == 0)].copy()
    if "pass_oe" not in p:
        p["pass_oe"] = float("nan")
    p["epa_pass"] = p["epa"].where(p["pass"] == 1)
    p["epa_rush"] = p["epa"].where(p["rush"] == 1)
    p["dropback"] = p["qb_dropback"].fillna(p["pass"]).astype(float)
    p["rz"] = (p["yardline_100"] <= 20).astype(float)
    g = p.groupby(["game_id", "season", "week", "season_type", "posteam"])
    out = g.agg(plays=("epa", "size"), pass_rate=("pass", "mean"), epa_pass=("epa_pass", "mean"),
                epa_rush=("epa_rush", "mean"), success_rate=("success", "mean"), dropbacks=("dropback", "sum"),
                sacks=("sack", "sum"), qb_hits=("qb_hit", "sum"), pass_oe=("pass_oe", "mean"),
                rz_plays=("rz", "sum")).reset_index().rename(columns={"posteam": "team"})
    out["sack_rate"] = out["sacks"] / out["dropbacks"].clip(lower=1)
    out["hit_rate"] = out["qb_hits"] / out["dropbacks"].clip(lower=1)    # includes sacks: how often the QB was pressured
    return out.drop(columns=["dropbacks", "sacks", "qb_hits"])


def fetch_season(season):
    resp = requests.get(URL.format(season), timeout=300)
    resp.raise_for_status()
    plays = pd.read_csv(io.BytesIO(resp.content), compression="gzip", low_memory=False,
                        usecols=lambda c: c in COLUMNS)
    return aggregate(plays)


def load(conn, first_season=FIRST_SEASON):
    """Saves every season not yet stored (and always the current one, which changes weekly)."""
    try:
        have = {s for (s,) in conn.execute(f"SELECT DISTINCT season FROM {TABLE}")}
    except Exception:
        have = set()
    now = current_season()
    for season in range(first_season, now + 1):
        if season in have and season != now:
            continue
        try:
            df = fetch_season(season)
        except requests.RequestException as e:      # HTTP error, timeout or dropped connection: go on to the next season
            print(f"pbp {season}: skipped ({e})")
            continue
        with conn:
            conn.execute(f"DELETE FROM {TABLE} WHERE season = ?", (season,))
            df.to_sql(TABLE, conn, if_exists="append", index=False)
        print(f"pbp {season}: {len(df)} team-games")


if __name__ == "__main__":
    import os
    from betbot import db
    load(db.connect(os.getenv("DB_PATH", "betbot.db")))
    sys.exit(0)
