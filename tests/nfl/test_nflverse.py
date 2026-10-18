from betbot import db
from betbot.nfl.collectors.nflverse import _value, is_stale, replace_table


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
    assert is_stale(conn, "2026-10-17")  # player ID table never loaded
    replace_table(conn, "nfl_players", ["gsis_id"], [])
    assert not is_stale(conn, "2026-10-17")  # g2 is too recent to expect stats
    assert is_stale(conn, "2026-10-19")  # g2 should have stats by now


def _fake_downloads(monkeypatch, files):
    from betbot.nfl.collectors import nflverse
    files.setdefault(nflverse.GAMES_URL, (["game_id"], [["g"]]))
    files.setdefault(nflverse.PLAYERS_URL, (["gsis_id"], [["00-1"]]))
    monkeypatch.setattr(nflverse, "_csv", lambda url: files[url])  # any other URL would KeyError
    monkeypatch.setattr(nflverse, "current_season", lambda: 2026)
    return nflverse


def test_load_only_refreshes_current_season(monkeypatch):
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_player_weeks", ["season", "game_id"], [[1999, "old"], [2026, "stale"]])
    replace_table(conn, "nfl_snap_counts", ["season", "game_id"], [[2012, "old snap"]])
    nflverse = _fake_downloads(monkeypatch, {
        "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_2026.csv":
            (["season", "game_id"], [[2026, "new"]]),
        "https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_2026.csv":
            (["season", "game_id"], [[2026, "new snap"]]),
    })
    nflverse.load(conn)
    assert conn.execute("SELECT game_id FROM nfl_player_weeks ORDER BY season").fetchall() == [("old",), ("new",)]
    assert conn.execute("SELECT game_id FROM nfl_snap_counts ORDER BY season").fetchall() == [("old snap",), ("new snap",)]
    assert conn.execute("SELECT count(*) FROM nfl_players").fetchone() == (1,)


def test_load_backfills_a_table_missing_its_history(monkeypatch):
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_player_weeks", ["season", "game_id"], [[1999, "old"]])
    replace_table(conn, "nfl_snap_counts", ["season", "game_id"], [[2026, "only this season"]])
    snap = "https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{}.csv"
    files = {snap.format(y): (["season", "game_id"], [[y, f"s{y}"]]) for y in range(2013, 2027)}
    files["https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_2026.csv"] = (
        ["season", "game_id"], [])
    nflverse = _fake_downloads(monkeypatch, files)
    nflverse.load(conn)
    assert conn.execute("SELECT min(season), max(season), count(*) FROM nfl_snap_counts").fetchone() == (2013, 2026, 14)

