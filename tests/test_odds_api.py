from betbot import db
from betbot.collectors.odds_api import parse_props

EVENT = {"id": "e1", "sport_key": "baseball_mlb", "home_team": "NYY", "away_team": "BOS",
         "commence_time": "2026-10-18T23:05:00Z"}
ODDS = {
    "id": "e1",
    "bookmakers": [{
        "key": "draftkings",
        "last_update": "2026-10-17T20:00:00Z",
        "markets": [{
            "key": "pitcher_strikeouts",
            "last_update": "2026-10-17T19:59:00Z",
            "outcomes": [
                {"name": "Over", "description": "Gerrit Cole", "price": -120, "point": 6.5},
                {"name": "Under", "description": "Gerrit Cole", "price": 100, "point": 6.5},
            ],
        }],
    }],
}


def test_parse_and_dedupe():
    rows = parse_props(ODDS, "2026-10-17T20:01:00Z")
    assert rows[0]["player"] == "Gerrit Cole" and rows[0]["line"] == 6.5
    assert rows[0]["book_updated_at"] == "2026-10-17T19:59:00Z"

    conn = db.connect(":memory:")
    db.save_event(conn, EVENT)
    assert db.save_lines(conn, rows) == 2
    # same book snapshot polled again later: no duplicate history
    assert db.save_lines(conn, parse_props(ODDS, "2026-10-17T20:05:00Z")) == 0
