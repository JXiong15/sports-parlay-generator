from betbot import db
from betbot.nfl.collectors.nflverse import current_season, replace_table
from betbot.names import normalize
import numpy as np
import pandas as pd
import pytest

from betbot.nfl import model
from betbot.nfl.signals import regression
from betbot.nfl.signals.regression import evaluate

NOW = "2026-10-18T12:00:00Z"
REAL_ADD = regression._add_probabilities  # kept before the autouse fixture stubs it
SEASON = current_season()


def make_db(weeks, lines):
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_player_weeks",
                  ["player_id", "player_display_name", "position_group", "season", "week", "passing_yards", "team"], weeks)
    replace_table(conn, "nfl_snap_counts", ["game_id", "season", "week", "player", "position", "team", "offense_pct", "defense_pct"], [])
    replace_table(conn, "nfl_practice_reports", ["season", "week", "gsis_id", "practice_status",
                                                 "practice_primary_injury", "report_primary_injury"], [])
    replace_table(conn, "nfl_games", ["game_id", "season", "week", "home_team", "away_team", "gameday"],
                  [["2026_06_CHI_GB", 2026, 6, "GB", "CHI", "2026-10-18"]])
    for event_id, start in (("e1", "2026-10-18T17:00:00Z"), ("old", "2026-10-11T17:00:00Z")):
        db.save_event(conn, {"id": event_id, "sport_key": "americanfootball_nfl", "commence_time": start,
                             "home_team": "Green Bay Packers", "away_team": "Chicago Bears"})
    db.save_lines(conn, [dict(event_id=e, market="player_pass_yds", player=p, side="Over", line=l, price=-110,
                              book=b, book_updated_at=t, fetched_at=t) for e, p, l, b, t in lines])
    return conn


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    """The model needs a full database; these tests cover the prop plumbing, test_model.py covers the model."""
    calls = []
    monkeypatch.setattr(regression, "_add_probabilities", lambda conn, stat, pos, results, up, shared=None: calls.append(up))
    return calls


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


def test_book_prices_and_game_matched(no_model):
    conn = make_db(games("q", "Some QB", [240] * 20), [
        ("e1", "Some QB", 239.5, "draftkings", "2026-10-17T11:00:00Z"),
        ("e1", "Some QB", 241.5, "fanduel", "2026-10-17T11:00:00Z"),
    ])
    db.save_lines(conn, [dict(event_id="e1", market="player_pass_yds", player="Some QB", side="Under", line=239.5,
                              price=-120, book="draftkings", book_updated_at="u", fetched_at="u")])
    r = evaluate(conn, now=NOW)[0][0]
    assert (r["book"], r["line"], r["over_price"], r["under_price"]) == ("draftkings", 239.5, -110, -120)
    assert r["game_id"] == "2026_06_CHI_GB"
    up = no_model[0].iloc[0]
    assert (up["player_id"], up["game_id"], up["season"], up["week"], up["opponent_team"]) == (
        "q", "2026_06_CHI_GB", 2026, 6, "CHI")


def test_add_probabilities_attaches_p_over(monkeypatch):
    params = {"z_quantiles": list(np.linspace(-3, 3, 401))}
    monkeypatch.setattr(model, "fitted", lambda conn, stat, pos: params)
    monkeypatch.setattr(model, "features", lambda conn, stat, pos, up, **kw: up.assign(y=np.nan))
    monkeypatch.setattr(model, "predict", lambda df, p: df.assign(mu=250.0, sigma=25.0))
    results = [{"player_id": "q", "game_id": "g", "line": 250.5}, {"player_id": "x", "game_id": None, "line": 1}]
    REAL_ADD(None, "passing_yards", ("QB",), results, pd.DataFrame([{"player_id": "q", "game_id": "g"}]))
    assert 0.4 < results[0]["p_over"] < 0.5 and results[0]["mu"] == 250.0  # line just above the expectation
    assert "p_over" not in results[1]


def test_same_player_spelled_two_ways_is_one_prop(no_model):
    conn = make_db(games("q", "Kenneth Walker", [240] * 20), [
        ("e1", "Kenneth Walker III", 239.5, "draftkings", "2026-10-17T11:00:00Z"),
        ("e1", "Kenneth Walker", 241.5, "fanduel", "2026-10-17T11:00:00Z"),
    ])
    results, problems = evaluate(conn, now=NOW)
    assert len(results) == 1 and len(no_model[0]) == 1 and problems == []
