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
