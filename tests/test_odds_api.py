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


def test_errors_never_include_api_key(monkeypatch):
    import pytest
    import requests
    from betbot.collectors import odds_api

    def fail(url, params, timeout):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}?apiKey={params['apiKey']}")
    monkeypatch.setattr(odds_api.requests, "get", fail)
    with pytest.raises(RuntimeError) as err:
        odds_api.get_events("SECRET123", "americanfootball_nfl")
    assert "SECRET123" not in str(err.value) and err.value.__suppress_context__


GAMES = [{
    "id": "g1", "sport_key": "americanfootball_nfl", "commence_time": "2026-10-19T00:20:00Z",
    "home_team": "Green Bay Packers", "away_team": "Dallas Cowboys",
    "bookmakers": [{"key": "fanduel", "last_update": "2026-10-18T15:00:00Z", "markets": [
        {"key": "h2h", "last_update": "2026-10-18T15:00:00Z", "outcomes": [
            {"name": "Green Bay Packers", "price": -118}, {"name": "Dallas Cowboys", "price": 100}]},
        {"key": "spreads", "last_update": "2026-10-18T15:00:00Z", "outcomes": [
            {"name": "Green Bay Packers", "price": -110, "point": -1.5}, {"name": "Dallas Cowboys", "price": -110, "point": 1.5}]},
        {"key": "totals", "last_update": "2026-10-18T15:00:00Z", "outcomes": [
            {"name": "Over", "price": -112, "point": 53.5}, {"name": "Under", "price": -108, "point": 53.5}]},
    ]}],
}]


def test_game_odds_parse_and_dedupe():
    from betbot.collectors.odds_api import parse_game_odds
    rows = parse_game_odds(GAMES, "2026-10-18T15:05:00Z")
    by = {(r["market"], r["side"]): (r["line"], r["price"]) for r in rows}
    assert by[("h2h", "Dallas Cowboys")] == (None, 100)
    assert by[("spreads", "Green Bay Packers")] == (-1.5, -110)
    assert by[("totals", "Under")] == (53.5, -108)
    conn = db.connect(":memory:")
    db.save_event(conn, GAMES[0])
    assert db.save_game_odds(conn, rows) == 6
    assert db.save_game_odds(conn, parse_game_odds(GAMES, "2026-10-18T15:10:00Z")) == 0  # same book snapshot


def test_game_odds_request_is_one_call_for_all_games(monkeypatch):
    from betbot.collectors import odds_api
    calls = []

    class Resp:
        headers = {"x-requests-remaining": "485"}
        def raise_for_status(self): pass
        def json(self): return GAMES

    monkeypatch.setattr(odds_api.requests, "get", lambda url, params, timeout: calls.append((url, params)) or Resp())
    games, remaining = odds_api.get_game_odds("KEY", "americanfootball_nfl")
    assert len(calls) == 1 and calls[0][0].endswith("/sports/americanfootball_nfl/odds")
    assert calls[0][1]["markets"] == "h2h,spreads,totals" and calls[0][1]["regions"] == "us"
    assert games == GAMES and remaining == "485"


def test_only_changed_lines_are_saved():
    conn = db.connect(":memory:")
    db.save_event(conn, EVENT)
    rows = parse_props(ODDS, "2026-10-17T20:01:00Z")
    assert db.save_lines(conn, rows) == 2
    later = [dict(r, book_updated_at="2026-10-17T21:00:00Z", fetched_at="2026-10-17T21:01:00Z") for r in rows]
    assert db.save_lines(conn, later) == 0  # book re-stamped but nothing moved: not saved
    moved = [dict(later[0], line=7.5, price=-105), later[1]]
    assert db.save_lines(conn, moved) == 1  # only the Over moved
    back = [dict(r, book_updated_at="2026-10-17T22:00:00Z", fetched_at="2026-10-17T22:01:00Z") for r in rows]
    assert db.save_lines(conn, back) == 1  # moving back to 6.5 is a change too


def test_hours_since_pull():
    from datetime import datetime, timezone
    conn = db.connect(":memory:")
    now = datetime(2026, 10, 18, 6, 0, tzinfo=timezone.utc)
    assert db.hours_since_pull(conn, "game_odds", "", now) is None
    db.record_pull(conn, "game_odds", "", "2026-10-18T04:30:00+00:00")
    db.record_pull(conn, "player_pass_yds", "e1", "2026-10-17T06:00:00+00:00")
    assert db.hours_since_pull(conn, "game_odds", "", now) == 1.5
    assert db.hours_since_pull(conn, "player_pass_yds", "e1", now) == 24
    assert db.hours_since_pull(conn, "player_pass_yds", "e2", now) is None


def test_line_the_book_took_down_drops_out():
    from betbot.nfl.signals.regression import current_lines
    conn = db.connect(":memory:")
    db.save_event(conn, dict(EVENT, commence_time="2026-10-19T17:00:00Z"))
    first = parse_props(ODDS, "2026-10-17T20:01:00+00:00")
    db.save_lines(conn, first)
    db.mark_seen(conn, "lines", first, "2026-10-17T20:01:00+00:00")
    db.record_pull(conn, "pitcher_strikeouts", "e1", "2026-10-17T20:01:00+00:00")
    assert len(current_lines(conn, "pitcher_strikeouts", "2026-10-18T00:00:00Z")) == 1
    # next pull: same price (nothing new saved) but still listed -> still current
    again = [dict(r, fetched_at="2026-10-17T22:00:00+00:00") for r in first]
    db.save_lines(conn, again)
    db.mark_seen(conn, "lines", again, "2026-10-17T22:00:00+00:00")
    db.record_pull(conn, "pitcher_strikeouts", "e1", "2026-10-17T22:00:00+00:00")
    assert len(current_lines(conn, "pitcher_strikeouts", "2026-10-18T00:00:00Z")) == 1
    # a later pull no longer lists it: gone
    db.record_pull(conn, "pitcher_strikeouts", "e1", "2026-10-18T00:30:00+00:00")
    assert current_lines(conn, "pitcher_strikeouts", "2026-10-18T01:00:00Z") == {}
