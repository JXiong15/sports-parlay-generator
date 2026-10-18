import sqlite3
from datetime import datetime

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
    -- price columns (American odds) are added by MIGRATIONS below
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
CREATE TABLE IF NOT EXISTS nfl_venues (  -- geocoded once per city
    city TEXT NOT NULL,
    state TEXT,
    country TEXT,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS nfl_weather (  -- kickoff forecast, refreshed each run until kickoff
    game_id TEXT PRIMARY KEY,  -- nflverse game_id
    kickoff TEXT NOT NULL,
    venue TEXT,
    city TEXT,
    state TEXT,
    country TEXT,
    indoor INTEGER,            -- ESPN's flag; nfl_games.roof has dome / closed / open / outdoors
    espn_condition TEXT,
    espn_temp_f REAL,
    temp_f REAL,
    wind_mph REAL,
    gust_mph REAL,
    precip_pct REAL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS game_odds (  -- moneyline / spread / total at every US book, one row per book snapshot
    id INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id),
    market TEXT NOT NULL,      -- h2h (moneyline) / spreads / totals
    side TEXT NOT NULL,        -- team name, or Over / Under
    line REAL,                 -- NULL for moneylines
    price INTEGER NOT NULL,    -- American odds
    book TEXT NOT NULL,
    book_updated_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    UNIQUE (event_id, market, side, book, book_updated_at)
);
CREATE TABLE IF NOT EXISTS odds_pulls (  -- when each paid Odds API request was made, to skip repeats
    kind TEXT NOT NULL,        -- 'game_odds' or the prop market, e.g. 'player_pass_yds'
    event_id TEXT NOT NULL,    -- '' for game odds (one request covers every game)
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS archive_captures (  -- Internet Archive copies already imported
    source TEXT NOT NULL,
    timestamp TEXT NOT NULL,   -- when the archive saved the page (YYYYMMDDhhmmss, UTC)
    url TEXT NOT NULL,
    rows_read INTEGER NOT NULL,
    rows_matched INTEGER NOT NULL,  -- rows tied to an nflverse game
    imported_at TEXT NOT NULL,
    PRIMARY KEY (source, timestamp, url)
);
CREATE TABLE IF NOT EXISTS archive_splits (  -- past game % of bets / money from archived pages
    source TEXT NOT NULL,      -- action_network / vsin_dk / vsin_circa / dk_network
    timestamp TEXT NOT NULL,   -- when the page was saved (the numbers as of then)
    url TEXT NOT NULL,
    game_id TEXT,              -- nflverse game_id; NULL if no game matched
    away TEXT,                 -- team text as the page wrote it
    home TEXT,
    market TEXT NOT NULL,      -- spread / total / moneyline
    side TEXT NOT NULL,        -- home / away / over / under
    line REAL,
    price INTEGER,
    bets_pct REAL,
    money_pct REAL,            -- NULL when the page didn't show it for free
    start_time TEXT,
    UNIQUE (source, timestamp, url, away, home, market, side, line)
);
CREATE TABLE IF NOT EXISTS model_params (  -- fitted player-stat model, one row per stat
    stat TEXT PRIMARY KEY,
    trained_through INTEGER NOT NULL,  -- season * 100 + week of the latest game fitted on
    params TEXT NOT NULL,              -- JSON: coefficients, scale, error distribution
    fitted_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS predictions (  -- every pick as made, then graded after the game
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,           -- prop / game
    made_at TEXT NOT NULL,
    game_id TEXT NOT NULL,        -- nflverse game_id
    event_id TEXT,                -- Odds API event (props)
    player TEXT,
    player_id TEXT,
    market TEXT NOT NULL,         -- player_pass_yds ... / spread / total / moneyline
    side TEXT NOT NULL,           -- Over / Under / home / away / over / under
    line REAL,
    price INTEGER NOT NULL,       -- American odds of the side picked
    other_price INTEGER,          -- the opposite side's price at the same book
    book TEXT,
    model_prob REAL NOT NULL,     -- our probability this side wins
    implied_prob REAL NOT NULL,   -- the book's probability for this side, vig removed when possible
    vig_removed INTEGER NOT NULL,
    p_over REAL,                  -- props: model P(Over), whichever side was picked
    mu REAL,                      -- model's expected stat / margin / total
    actual REAL,                  -- filled by the grader
    result TEXT,                  -- win / loss / push / void
    closing_line REAL,
    closing_price INTEGER,
    closing_implied REAL,         -- no-vig probability of our side at the close
    clv REAL,                     -- closing_implied - implied_prob (positive = we beat the close)
    graded_at TEXT,
    UNIQUE (kind, game_id, player_id, market, side, book, line)
);
CREATE TABLE IF NOT EXISTS parlay_correlations (  -- same-game outcome correlations measured from history
    key TEXT PRIMARY KEY,      -- e.g. receiving_yards|passing_yards|teammates, passing_yards|T, H|T
    value TEXT NOT NULL,       -- JSON [rho, pairs]
    computed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS lines_history ON lines (event_id, market, player, book, book_updated_at);
"""


# columns added after a table first shipped; connect() adds any an existing database is missing
MIGRATIONS = {
    "nfl_game_lines": [f"{k}_{c} INTEGER" for k in ("open", "current", "close") for c in (
        "over_price", "under_price", "home_spread_price", "away_spread_price", "home_ml", "away_ml")],
    "splits": ["price INTEGER"],  # DraftKings' American odds for that side
}


def connect(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    for table, columns in MIGRATIONS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col in columns:
            if col.split()[0] not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
    conn.commit()
    return conn


def save_event(conn, event):
    conn.execute(
        "INSERT OR REPLACE INTO events VALUES (?, ?, ?, ?, ?)",
        (event["id"], event["sport_key"], event.get("home_team"), event.get("away_team"), event["commence_time"]),
    )


def save_lines(conn, rows):
    """Saves only lines whose line or price changed since that book's last saved one. Returns how many changed."""
    cur = conn.executemany(
        "INSERT OR IGNORE INTO lines (event_id, market, player, side, line, price, book, book_updated_at, fetched_at)"
        " SELECT :event_id, :market, :player, :side, :line, :price, :book, :book_updated_at, :fetched_at"
        " WHERE NOT EXISTS (SELECT 1 FROM (SELECT line, price FROM lines WHERE event_id = :event_id"
        "  AND market = :market AND player = :player AND side = :side AND book = :book"
        "  ORDER BY fetched_at DESC, id DESC LIMIT 1) last WHERE last.line IS :line AND last.price = :price)",
        rows,
    )
    return cur.rowcount


def _upsert(conn, table, row):
    cols = ", ".join(row)
    conn.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({', '.join(':' + c for c in row)})", row)


def save_game_line(conn, row):
    _upsert(conn, "nfl_game_lines", row)


def replace_injuries(conn, rows):
    conn.execute("DELETE FROM nfl_injuries")
    conn.executemany(
        "INSERT INTO nfl_injuries VALUES (:team, :player, :name_key, :position, :status, :fetched_at)", rows
    )


def save_splits(conn, rows):
    conn.executemany(
        "INSERT OR IGNORE INTO splits (league, game_id, start_time, home_team, away_team, market, side, line, bets_pct,"
        " money_pct, fetched_at, price) VALUES (:league, :game_id, :start_time, :home_team, :away_team, :market, :side,"
        " :line, :bets_pct, :money_pct, :fetched_at, :price)",
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


def save_weather(conn, row):
    # a game that has kicked off keeps its last forecast instead of being blanked
    if row["temp_f"] is None and conn.execute("SELECT temp_f FROM nfl_weather WHERE game_id = ?",
                                              (row["game_id"],)).fetchone():
        return
    _upsert(conn, "nfl_weather", row)


def save_game_odds(conn, rows):
    """Saves only prices that changed since that book's last saved one. Returns how many changed."""
    cur = conn.executemany(
        "INSERT OR IGNORE INTO game_odds (event_id, market, side, line, price, book, book_updated_at, fetched_at)"
        " SELECT :event_id, :market, :side, :line, :price, :book, :book_updated_at, :fetched_at"
        " WHERE NOT EXISTS (SELECT 1 FROM (SELECT line, price FROM game_odds WHERE event_id = :event_id"
        "  AND market = :market AND side = :side AND book = :book"
        "  ORDER BY fetched_at DESC, id DESC LIMIT 1) last WHERE last.line IS :line AND last.price = :price)",
        rows,
    )
    return cur.rowcount


def record_pull(conn, kind, event_id, fetched_at):
    conn.execute("INSERT INTO odds_pulls VALUES (?, ?, ?)", (kind, event_id, fetched_at))


def hours_since_pull(conn, kind, event_id, now):
    """Hours since this Odds API request was last made (None if never), so recent ones can be skipped."""
    last = conn.execute("SELECT max(fetched_at) FROM odds_pulls WHERE kind = ? AND event_id = ?",
                        (kind, event_id)).fetchone()[0]
    if last is None:
        return None
    return (now - datetime.fromisoformat(last)).total_seconds() / 3600


def save_archive_splits(conn, rows):
    conn.executemany(
        "INSERT OR IGNORE INTO archive_splits VALUES (:source, :timestamp, :url, :game_id, :away, :home, :market,"
        " :side, :line, :price, :bets_pct, :money_pct, :start_time)",
        rows,
    )


PREDICTION_COLUMNS = ("kind", "made_at", "game_id", "event_id", "player", "player_id", "market", "side", "line",
                      "price", "other_price", "book", "model_prob", "implied_prob", "vig_removed", "p_over", "mu")


def save_predictions(conn, rows):
    """Keeps the first logging of each pick (same game, player, market, side, book and line). Compared with IS so
    empty values (moneylines have no line) still count as the same pick."""
    cols = ", ".join(PREDICTION_COLUMNS)
    same = " AND ".join(f"{c} IS :{c}" for c in ("kind", "game_id", "player_id", "market", "side", "book", "line"))
    cur = conn.executemany(f"INSERT OR IGNORE INTO predictions ({cols}) SELECT"
                           f" {', '.join(':' + c for c in PREDICTION_COLUMNS)}"
                           f" WHERE NOT EXISTS (SELECT 1 FROM predictions WHERE {same})",
                           [{c: r.get(c) for c in PREDICTION_COLUMNS} for r in rows])
    return cur.rowcount
