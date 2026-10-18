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
CREATE TABLE IF NOT EXISTS splits (  -- public betting %, kept per fetch because they change
    league TEXT NOT NULL,
    game_id INTEGER NOT NULL,  -- Action Network id
    start_time TEXT NOT NULL,
    home_team TEXT,
    away_team TEXT,
    market TEXT NOT NULL,      -- spread / total / moneyline
    side TEXT,                 -- home / away / over / under
    line REAL,
    bets_pct INTEGER,
    money_pct INTEGER,         -- NULL when the free page doesn't show it
    fetched_at TEXT NOT NULL,
    UNIQUE (game_id, market, side, fetched_at)
);
CREATE TABLE IF NOT EXISTS dk_articles (  -- DraftKings Network prop-split articles already saved
    id INTEGER PRIMARY KEY,
    league TEXT NOT NULL,
    link TEXT NOT NULL,
    published TEXT NOT NULL,
    modified TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prop_splits (  -- % of handle (money) and % of bets on DraftKings player props
    article_id INTEGER NOT NULL REFERENCES dk_articles(id),
    game TEXT,                 -- NULL in single-game articles
    player TEXT NOT NULL,
    market TEXT NOT NULL,      -- Odds API key, e.g. player_pass_yds
    market_text TEXT NOT NULL, -- DraftKings' wording
    period TEXT,               -- 1H / 1Q; NULL = full game
    line REAL NOT NULL,
    over_handle REAL,
    under_handle REAL,
    over_bets REAL,
    under_bets REAL
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


def save_splits(conn, rows):
    conn.executemany(
        "INSERT OR IGNORE INTO splits VALUES (:league, :game_id, :start_time, :home_team, :away_team, :market, :side,"
        " :line, :bets_pct, :money_pct, :fetched_at)",
        rows,
    )


def save_prop_splits(conn, article, rows):
    """Replaces one article's rows, so an edited article never leaves duplicates."""
    conn.execute("DELETE FROM prop_splits WHERE article_id = ?", (article["id"],))
    conn.execute("INSERT OR REPLACE INTO dk_articles VALUES (:id, :league, :link, :published, :modified, :fetched_at)",
                 article)
    conn.executemany(
        "INSERT INTO prop_splits VALUES (:article_id, :game, :player, :market, :market_text, :period, :line, :over_handle,"
        " :under_handle, :over_bets, :under_bets)",
        [dict(r, article_id=article["id"]) for r in rows],
    )
