"""Free NFL history from nflverse: every game since 1999 (scores, spread, total, moneylines) and weekly player stats."""
import csv
import io
import os
import sqlite3
import sys
from datetime import date

import requests

from betbot import db
from betbot.nfl import views

GAMES_URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"
INJURIES_URL = "https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{}.csv"
PLAYERS_URL = "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv"
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


# tables kept per season: (table, url, first season nflverse has)
SEASONAL = (
    ("nfl_player_weeks", PLAYER_WEEK_URL, 1999),
    ("nfl_snap_counts", SNAP_COUNTS_URL, 2013),  # the 2012 file is empty
    ("nfl_injury_reports", INJURIES_URL, 2009),  # official injury/practice reports, for the model to learn from
)


def _align(seasons):
    """[(header, rows)] per season -> one header and rows lined up by column name (files differ across years)."""
    header = []
    for h, _ in seasons:
        header += [c for c in h if c not in header]
    rows = []
    for h, season_rows in seasons:
        pos = [h.index(c) if c in h else None for c in header]
        rows += [[r[i] if i is not None and i < len(r) else None for i in pos] for r in season_rows]
    return header, rows


def _first_saved_season(conn, table):
    try:
        return conn.execute(f"SELECT min(season) FROM {table}").fetchone()[0]
    except sqlite3.OperationalError:  # never loaded
        return None


def load(conn, first_season=None):
    """Re-downloads from first_season (default: this season) for tables whose full history is already saved,
    and each table's full history otherwise. Games and the player ID table are re-downloaded whole every time."""
    first_season = first_season or current_season()
    header, games = _csv(GAMES_URL)
    players_header, players = _csv(PLAYERS_URL)
    downloads = []
    for table, url, since in SEASONAL:
        saved = _first_saved_season(conn, table)
        start = max(first_season, since) if saved is not None and saved <= since else since
        season_header, rows = _align([_csv(url.format(season)) for season in range(start, current_season() + 1)])
        print(f"{table}: {len(rows)} rows for {start}-{current_season()}")
        downloads.append((table, season_header, rows, start, start == since))
    # one transaction so a failed download never leaves a half-empty table
    with conn:
        replace_table(conn, "nfl_games", header, games)
        replace_table(conn, "nfl_players", players_header, players)
        for table, table_header, rows, start, full in downloads:
            if full:
                replace_table(conn, table, table_header, rows)
            else:
                have = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
                for col in table_header:
                    if col not in have:  # a new nflverse column: add it instead of failing
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {_cols([col])}")
                conn.execute(f"DELETE FROM {table} WHERE season >= ?", (start,))
                _insert(conn, table, table_header, rows)
    views.create(conn)
    print(f"{len(games)} games, {len(players)} players updated")


def update_practice_reports(conn):
    """This season's official injury/practice reports; small, and they change daily during the week, so refresh every run."""
    header, rows = _csv(INJURIES_URL.format(current_season()))
    if rows:
        with conn:
            replace_table(conn, "nfl_practice_reports", header, rows)
            if _first_saved_season(conn, "nfl_injury_reports") is not None:
                # the model reads the full history table; keep its current season as fresh as this one
                have = [r[1] for r in conn.execute("PRAGMA table_info(nfl_injury_reports)")]
                for col in header:
                    if col not in have:
                        conn.execute(f"ALTER TABLE nfl_injury_reports ADD COLUMN {_cols([col])}")
                conn.execute("DELETE FROM nfl_injury_reports WHERE season = ?", (current_season(),))
                _insert(conn, "nfl_injury_reports", header, rows)


def is_stale(conn, today=None):
    """True if a game from the last month (older than 2 days, so nflverse has posted it) has no player stats."""
    today = today or date.today().isoformat()
    try:
        for table in ("nfl_snap_counts", "nfl_players"):
            conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
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
