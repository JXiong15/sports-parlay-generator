from betbot import db
from betbot.nfl.collectors.espn import _num, _price, parse, pick

# trimmed from a real ESPN response
ITEM = {
    "provider": {"name": "DraftKings"},
    "open": {"total": {"american": "43.5"}, "over": {"american": "-110"}, "under": {"american": "-110"}},
    "close": {"total": {"american": "40.5"}, "over": {"american": "-115"}, "under": {"american": "-105"}},
    "current": {"total": {"american": "40.5"}, "over": {"american": "-115"}, "under": {"american": "-105"}},
    "homeTeamOdds": {
        "open": {"pointSpread": {"american": "+2.5"}, "spread": {"american": "-115"}, "moneyLine": {"american": "+110"}},
        "current": {"pointSpread": {"american": "+3"}, "spread": {"american": "-120"}, "moneyLine": {"american": "+130"}},
        "close": {"pointSpread": {"american": "+3"}, "spread": {"american": "-120"}, "moneyLine": {"american": "+130"}},
    },
    "awayTeamOdds": {
        "open": {"pointSpread": {"american": "-2.5"}, "spread": {"american": "-105"}, "moneyLine": {"american": "-130"}},
        "close": {"pointSpread": {"american": "-3"}, "spread": {"american": "EVEN"}, "moneyLine": {"american": "-155"}},
    },
}


def test_parse():
    row = parse(ITEM)
    assert row["open_total"] == 43.5 and row["close_total"] == 40.5
    assert row["open_home_spread"] == 2.5 and row["current_home_spread"] == 3.0
    assert (row["open_home_ml"], row["open_away_ml"], row["close_home_ml"], row["close_away_ml"]) == (110, -130, 130, -155)
    assert (row["close_over_price"], row["close_under_price"]) == (-115, -105)
    assert (row["close_home_spread_price"], row["close_away_spread_price"]) == (-120, 100)  # EVEN = +100
    assert row["current_away_ml"] is None  # missing in the feed


def test_prices_saved_and_old_database_upgraded(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE nfl_game_lines (game_id TEXT PRIMARY KEY, book TEXT NOT NULL, open_total REAL,"
                " current_total REAL, close_total REAL, open_home_spread REAL, current_home_spread REAL,"
                " close_home_spread REAL, fetched_at TEXT NOT NULL)")  # the table before prices existed
    old.execute("INSERT INTO nfl_game_lines VALUES ('kept', 'DK', 40, 41, 42, 3, 3, 3, 't')")
    old.commit()
    old.close()
    conn = db.connect(path)
    db.save_game_line(conn, dict(parse(ITEM), game_id="g", fetched_at="t"))
    assert conn.execute("SELECT close_home_ml, close_away_ml FROM nfl_game_lines WHERE game_id = 'g'").fetchone() == (130, -155)
    assert conn.execute("SELECT close_total FROM nfl_game_lines WHERE game_id = 'kept'").fetchone() == (42,)  # old rows survive
    assert _price("+130") == 130 and _price(None) is None


def test_missing_and_pickem():
    assert parse({"provider": {"name": "X"}})["close_total"] is None
    assert _num("PK") == 0.0 and _num("+3") == 3.0


def test_pick_skips_empty_and_live_books():
    empty = {"provider": {"name": "Bet 365"}}
    live = dict(ITEM, provider={"name": "ESPN Bet - Live Odds"})
    assert pick([empty, live, ITEM]) is ITEM
    assert pick([live, empty]) is empty
    assert pick([]) is None
    no_ml = {"provider": {"name": "DraftKings"}, "open": {"total": {"american": "49.5"}}}
    assert pick([no_ml, ITEM]) is ITEM  # most complete book wins over first listed
