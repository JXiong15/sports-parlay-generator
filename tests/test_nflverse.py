from betbot import db
from betbot.collectors.nflverse import _value, is_stale, replace_table


def test_value_types():
    assert _value("") is None
    assert _value("244") == 244
    assert _value("-3.5") == -3.5
    assert _value("00-0023459") == "00-0023459"


def test_replace_table_reloads():
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_games", ["game_id", "spread_line"], [["g1", -3.5]])
    replace_table(conn, "nfl_games", ["game_id", "spread_line"], [["g1", -3.5], ["g2", 7]])
    assert conn.execute("SELECT count(*), sum(spread_line) FROM nfl_games").fetchone() == (2, 3.5)


def test_is_stale():
    conn = db.connect(":memory:")
    assert is_stale(conn)  # nothing loaded yet
    replace_table(conn, "nfl_games", ["game_id", "gameday"], [["g1", "2026-10-11"], ["g2", "2026-10-16"]])
    replace_table(conn, "nfl_player_weeks", ["game_id"], [["g1"]])
    assert not is_stale(conn, "2026-10-17")  # g2 is too recent to expect stats
    assert is_stale(conn, "2026-10-19")  # g2 should have stats by now
