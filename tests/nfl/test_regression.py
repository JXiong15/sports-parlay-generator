from betbot import db
from betbot.nfl.collectors.nflverse import current_season, replace_table
from betbot.names import normalize
from betbot.nfl.signals.regression import evaluate

NOW = "2026-10-18T12:00:00Z"
SEASON = current_season()


def make_db(weeks, lines):
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_player_weeks",
                  ["player_id", "player_display_name", "position_group", "season", "week", "passing_yards", "team"], weeks)
    replace_table(conn, "nfl_snap_counts", ["game_id", "season", "week", "player", "position", "team", "offense_pct", "defense_pct"], [])
    replace_table(conn, "nfl_practice_reports", ["season", "week", "gsis_id", "practice_status",
                                                 "practice_primary_injury", "report_primary_injury"], [])
    for event_id, start in (("e1", "2026-10-18T17:00:00Z"), ("old", "2026-10-11T17:00:00Z")):
        db.save_event(conn, {"id": event_id, "sport_key": "americanfootball_nfl", "commence_time": start,
                             "home_team": "Green Bay Packers", "away_team": "Chicago Bears"})
    db.save_lines(conn, [dict(event_id=e, market="player_pass_yds", player=p, side="Over", line=l, price=-110,
                              book=b, book_updated_at=t, fetched_at=t) for e, p, l, b, t in lines])
    return conn


def games(pid, name, yards, pos="QB", team="GB"):
    # yards[0] is the most recent game
    return [[pid, name, pos, SEASON, len(yards) - i, y, team] for i, y in enumerate(yards)]


def test_normalize():
    assert normalize("Michael Penix Jr.") == normalize("michael penix") == "michael penix"
    assert normalize("C.J. Stroud") == normalize("CJ Stroud")


def test_under_lean_after_blowout_game():
    weeks = games("hot", "Hot Hand", [400] + [230] * 19) + games("steady", "Steady Eddie", [240] * 20)
    conn = make_db(weeks, [
        ("e1", "Hot Hand", 270.5, "dk", "2026-10-17T10:00:00Z"),
        ("e1", "Hot Hand", 275.5, "dk", "2026-10-17T11:00:00Z"),  # latest dk snapshot wins
        ("e1", "Hot Hand", 270.5, "fd", "2026-10-17T11:00:00Z"),
        ("e1", "Hot Hand", 270.5, "mgm", "2026-10-17T11:00:00Z"),
        ("e1", "Steady Eddie", 239.5, "dk", "2026-10-17T11:00:00Z"),
        ("old", "Steady Eddie", 500.5, "dk", "2026-10-11T11:00:00Z"),  # game already played: ignored
    ])
    results, problems = evaluate(conn, now=NOW)
    by = {r["player"]: r for r in results}
    assert by["Hot Hand"]["line"] == 270.5 and by["Hot Hand"]["lean"] == "UNDER"  # most common real line, not 273
    assert by["Steady Eddie"]["line"] == 239.5 and by["Steady Eddie"]["lean"] is None
    assert problems == []


def test_unmatched_ambiguous_and_short_history_are_reported():
    weeks = (games("a1", "Josh Allen", [250] * 20) + games("a2", "Josh Allen", [0] * 20, pos="LB")  # LB filtered out
             + games("s1", "Sam Smith", [200] * 20) + games("s2", "Sam Smith", [210] * 20)
             + games("r1", "Rookie Guy", [300] * 3))
    conn = make_db(weeks, [(("e1", p, 250.5, "dk", "2026-10-17T11:00:00Z"))
                           for p in ("Josh Allen", "Sam Smith", "Nobody Here", "Rookie Guy")])
    results, problems = evaluate(conn, now=NOW)
    assert {r["player"] for r in results} == {"Josh Allen", "Rookie Guy"}
    assert [r for r in results if r["player"] == "Rookie Guy"][0]["reason"] == "only 3 games"
    reasons = {p["player"]: p["reason"] for p in problems}
    assert reasons.keys() == {"Sam Smith", "Nobody Here"}
    assert reasons["Sam Smith"].startswith("ambiguous") and reasons["Nobody Here"] == "no nflverse match"


def test_traded_player_is_flagged():
    conn = make_db(games("t1", "Moved Guy", [250] * 20, team="DAL"), [("e1", "Moved Guy", 250.5, "dk", "2026-10-17T11:00:00Z")])
    results, _ = evaluate(conn, now=NOW)
    assert "check for a trade" in results[0]["flags"][0]


def test_draftkings_line_preferred():
    conn = make_db(games("q", "Some QB", [240] * 20), [
        ("e1", "Some QB", 239.5, "draftkings", "2026-10-17T11:00:00Z"),
        ("e1", "Some QB", 242.5, "fanduel", "2026-10-17T11:00:00Z"),
        ("e1", "Some QB", 242.5, "betmgm", "2026-10-17T11:00:00Z"),
    ])
    assert evaluate(conn, now=NOW)[0][0]["line"] == 239.5
