"""Free NFL history from nflverse: every game since 1999 (scores, spread, total, moneylines) and weekly player stats."""
import csv
import io
import os
import sqlite3
import sys
from datetime import date

import requests

from betbot import db

GAMES_URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"
INJURIES_URL = "https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{}.csv"
SNAP_COUNTS_URL = "https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{}.csv"
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


def current_season(today=None):
    today = today or date.today()
    # NFL seasons start in September, so before then the latest season is last year's
    return today.year if today.month >= 9 else today.year - 1


def _cols(header):
    # headers come from a downloaded file, so escape quotes rather than trust them
    return ", ".join('"' + c.replace('"', '""') + '"' for c in header)


def _insert(conn, table, header, rows):
    conn.executemany(f"INSERT INTO {table} ({_cols(header)}) VALUES ({', '.join('?' * len(header))})", rows)


def replace_table(conn, table, header, rows):
    conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.execute(f"CREATE TABLE {table} ({_cols(header)})")
    _insert(conn, table, header, rows)


def load(conn, first_season=None):
    """By default re-downloads only the current season once history exists; everything since 1999 the first time."""
    has_history = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'nfl_player_weeks'").fetchone()
    if first_season is None:
        first_season = current_season() if has_history else 1999
    header, games = _csv(GAMES_URL)
    rows = []
    for season in range(first_season, current_season() + 1):
        week_header, week_rows = _csv(PLAYER_WEEK_URL.format(season))
        rows += week_rows
        print(f"{season}: {len(week_rows)} player-weeks")
    # only the latest games matter for who is starting right now
    snap_header, snaps = _csv(SNAP_COUNTS_URL.format(current_season()))
    # one transaction so a failed download never leaves a half-empty table
    with conn:
        replace_table(conn, "nfl_games", header, games)
        replace_table(conn, "nfl_snap_counts", snap_header, snaps)
        if has_history and first_season > 1999:
            # shortcut: assumes nflverse keeps the same columns; if this errors, run a full reload with 1999
            conn.execute("DELETE FROM nfl_player_weeks WHERE season >= ?", (first_season,))
            _insert(conn, "nfl_player_weeks", week_header, rows)
        else:
            replace_table(conn, "nfl_player_weeks", week_header, rows)
    print(f"{len(games)} games, {len(rows)} player-weeks updated")


def update_practice_reports(conn):
    """This season's official injury/practice reports; small, and they change daily during the week, so refresh every run."""
    header, rows = _csv(INJURIES_URL.format(current_season()))
    if rows:
        with conn:
            replace_table(conn, "nfl_practice_reports", header, rows)


def is_stale(conn, today=None):
    """True if a game from the last month (older than 2 days, so nflverse has posted it) has no player stats."""
    today = today or date.today().isoformat()
    try:
        conn.execute("SELECT 1 FROM nfl_snap_counts LIMIT 1")
        return conn.execute(
            "SELECT EXISTS (SELECT 1 FROM nfl_games"
            " WHERE gameday BETWEEN date(?, '-30 days') AND date(?, '-2 days')"
            " AND game_id NOT IN (SELECT game_id FROM nfl_player_weeks))",
            (today, today),
        ).fetchone()[0] == 1
    except sqlite3.OperationalError:  # history never loaded
        return True


if __name__ == "__main__":
    load(db.connect(os.getenv("DB_PATH", "betbot.db")), int(sys.argv[1]) if len(sys.argv) > 1 else None)
