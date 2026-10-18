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
    assert is_stale(conn, "2026-10-17")  # snap counts never loaded
    replace_table(conn, "nfl_snap_counts", ["game_id"], [])
    assert not is_stale(conn, "2026-10-17")  # g2 is too recent to expect stats
    assert is_stale(conn, "2026-10-19")  # g2 should have stats by now


def test_load_only_refreshes_current_season(monkeypatch):
    from betbot.collectors import nflverse
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_player_weeks", ["season", "game_id"], [[2010, "old"], [2026, "stale"]])
    files = {nflverse.GAMES_URL: (["game_id"], [["g"]]),
             nflverse.PLAYER_WEEK_URL.format(2026): (["season", "game_id"], [[2026, "new"]]),
             nflverse.SNAP_COUNTS_URL.format(2026): (["game_id"], [])}
    monkeypatch.setattr(nflverse, "_csv", lambda url: files[url])  # any other season URL would KeyError
    monkeypatch.setattr(nflverse, "current_season", lambda: 2026)
    nflverse.load(conn)
    assert conn.execute("SELECT game_id FROM nfl_player_weeks ORDER BY season").fetchall() == [("old",), ("new",)]
