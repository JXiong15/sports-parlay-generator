from betbot.collectors.espn import _num, parse, pick

# trimmed from a real ESPN response
ITEM = {
    "provider": {"name": "DraftKings"},
    "open": {"total": {"american": "43.5"}},
    "close": {"total": {"american": "40.5"}},
    "current": {"total": {"american": "40.5"}},
    "homeTeamOdds": {
        "open": {"pointSpread": {"american": "+2.5"}},
        "current": {"pointSpread": {"american": "+3"}},
        "close": {"pointSpread": {"american": "+3"}},
    },
}


def test_parse():
    row = parse(ITEM)
    assert row["open_total"] == 43.5 and row["close_total"] == 40.5
    assert row["open_home_spread"] == 2.5 and row["current_home_spread"] == 3.0


def test_missing_and_pickem():
    assert parse({"provider": {"name": "X"}})["close_total"] is None
    assert _num("PK") == 0.0 and _num("+3") == 3.0


def test_pick_skips_empty_and_live_books():
    empty = {"provider": {"name": "Bet 365"}}
    live = dict(ITEM, provider={"name": "ESPN Bet - Live Odds"})
    assert pick([empty, live, ITEM]) is ITEM
    assert pick([live, empty]) is empty
    assert pick([]) is None
