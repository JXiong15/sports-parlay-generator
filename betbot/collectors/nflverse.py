"""Free NFL history from nflverse: every game since 1999 (scores, spread, total, moneylines) and weekly player stats."""
import csv
import io
import os
import sys
from datetime import date

import requests

from betbot import db

GAMES_URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"
PLAYER_WEEK_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{}.csv"


def _value(s):
    if s == "":
        return None
    for cast in (int, float):
        try:
            return cast(s)
        except ValueError:
            pass
    return s


def _csv(url):
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    rows = list(csv.reader(io.StringIO(resp.text)))
    return rows[0], [[_value(v) for v in r] for r in rows[1:]]


def replace_table(conn, table, header, rows):
    cols = ", ".join(f'"{c}"' for c in header)
    conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.execute(f"CREATE TABLE {table} ({cols})")
    conn.executemany(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' * len(header))})", rows)


def load(conn, first_season=1999):
    header, games = _csv(GAMES_URL)
    rows = []
    # NFL seasons start in September, so before then the latest season is last year's
    last_season = date.today().year if date.today().month >= 9 else date.today().year - 1
    for season in range(first_season, last_season + 1):
        week_header, week_rows = _csv(PLAYER_WEEK_URL.format(season))
        rows += week_rows
        print(f"{season}: {len(week_rows)} player-weeks")
    # reload everything in one transaction so a failed download never leaves a half-empty table
    with conn:
        replace_table(conn, "nfl_games", header, games)
        replace_table(conn, "nfl_player_weeks", week_header, rows)
    print(f"{len(games)} games, {len(rows)} player-weeks loaded")


if __name__ == "__main__":
    load(db.connect(os.getenv("DB_PATH", "betbot.db")), int(sys.argv[1]) if len(sys.argv) > 1 else 1999)
