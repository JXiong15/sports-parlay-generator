import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    sport TEXT NOT NULL,
    home_team TEXT,
    away_team TEXT,
    commence_time TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lines (
    id INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id),
    market TEXT NOT NULL,
    player TEXT NOT NULL,
    side TEXT NOT NULL,
    line REAL,
    price INTEGER NOT NULL,
    book TEXT NOT NULL,
    book_updated_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    -- one row per book snapshot: re-polling an unchanged market adds nothing
    UNIQUE (event_id, market, player, side, book, book_updated_at)
);
CREATE TABLE IF NOT EXISTS nfl_game_lines (
    game_id TEXT PRIMARY KEY,  -- nflverse game_id
    book TEXT NOT NULL,
    open_total REAL,
    current_total REAL,
    close_total REAL,
    open_home_spread REAL,
    current_home_spread REAL,
    close_home_spread REAL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nfl_injuries (  -- latest ESPN report only, replaced each run
    team TEXT NOT NULL,       -- nflverse abbreviation
    player TEXT NOT NULL,
    name_key TEXT NOT NULL,   -- names.normalize(player)
    position TEXT,
    status TEXT NOT NULL,     -- Out, Doubtful, Questionable, Injured Reserve, ...
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS lines_history ON lines (event_id, market, player, book, book_updated_at);
"""


def connect(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def save_event(conn, event):
    conn.execute(
        "INSERT OR REPLACE INTO events VALUES (?, ?, ?, ?, ?)",
        (event["id"], event["sport_key"], event.get("home_team"), event.get("away_team"), event["commence_time"]),
    )


def save_lines(conn, rows):
    cur = conn.executemany(
        "INSERT OR IGNORE INTO lines (event_id, market, player, side, line, price, book, book_updated_at, fetched_at)"
        " VALUES (:event_id, :market, :player, :side, :line, :price, :book, :book_updated_at, :fetched_at)",
        rows,
    )
    return cur.rowcount


def save_game_line(conn, row):
    conn.execute(
        "INSERT OR REPLACE INTO nfl_game_lines VALUES (:game_id, :book, :open_total, :current_total, :close_total,"
        " :open_home_spread, :current_home_spread, :close_home_spread, :fetched_at)",
        row,
    )


def replace_injuries(conn, rows):
    conn.execute("DELETE FROM nfl_injuries")
    conn.executemany(
        "INSERT INTO nfl_injuries VALUES (:team, :player, :name_key, :position, :status, :fetched_at)", rows
    )
