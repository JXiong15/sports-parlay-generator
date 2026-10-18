from betbot import db
from betbot.nfl import board


def test_game_splits_only_from_this_game():
    conn = db.connect(":memory:")
    db.save_splits(conn, [dict(league="nfl", game_id=1, start_time="2026-10-11T17:00:00Z", home_team="Denver Broncos",
                               away_team="X", market="spread", side="home", line=-3, bets_pct=80, money_pct=40,
                               fetched_at="2026-10-11T12:00:00Z", price=-110)])
    assert board._game_splits(conn, "Denver Broncos", "spread", "home", "2026-10-18T20:00:00Z") == (None, None)
    assert board._game_splits(conn, "Denver Broncos", "spread", "home", "2026-10-11T17:00:00Z") == (80, 40)


def test_prop_splits_match_names_loosely():
    conn = db.connect(":memory:")
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.save_prop_splits(conn, {"id": 1, "league": "nfl", "link": "x", "published": today, "modified": "m",
                               "fetched_at": "f"},
                        [{"game": None, "player": "Kenneth Walker", "market": "player_rush_yds", "market_text": "x",
                          "period": None, "line": 70.5, "over_handle": 60, "under_handle": 40, "over_bets": 75,
                          "under_bets": 25}])
    assert board._prop_splits(conn, "Kenneth Walker III", "player_rush_yds", 70.5) == (75, 60)
    assert board._prop_splits(conn, "Kenneth Walker III", "player_rush_yds", 71.5) is None


def test_other_side_price_only_at_the_matching_line():
    assert board._matching_other_price("spread", (-3.0, -110), (3.0, -110)) == -110
    assert board._matching_other_price("spread", (-3.0, -110), (3.5, -120)) is None
    assert board._matching_other_price("total", (47.5, -110), (48.0, -120)) is None
    assert board._matching_other_price("moneyline", (None, -150), (None, 130)) == 130


def test_a_book_missing_one_side_is_skipped():
    conn = db.connect(":memory:")
    db.save_event(conn, {"id": "e1", "sport_key": "americanfootball_nfl", "commence_time": "2099-01-01T00:00:00Z"})
    row = lambda side, line, price, book: {"event_id": "e1", "market": "spreads", "side": side, "line": line,
                                           "price": price, "book": book, "book_updated_at": "t", "fetched_at": "t"}
    db.save_game_odds(conn, [row("Green Bay Packers", -3, -110, "draftkings"),          # DraftKings: one side only
                             row("Green Bay Packers", -3, -112, "fanduel"), row("Dallas Cowboys", 3, -108, "fanduel")])
    book, sides = board._game_offers(conn, "e1")["spreads"]
    assert book == "fanduel" and set(sides) == {"Green Bay Packers", "Dallas Cowboys"}
