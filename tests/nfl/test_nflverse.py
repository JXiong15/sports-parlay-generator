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
    assert is_stale(conn, "2026-10-17")  # injury history never loaded
    replace_table(conn, "nfl_injury_reports", ["gsis_id"], [])
    assert is_stale(conn, "2026-10-17")  # g1 has stats but no snap counts yet
    replace_table(conn, "nfl_snap_counts", ["game_id"], [["g1"]])
    assert not is_stale(conn, "2026-10-17")  # g2 is too recent to expect stats
    assert is_stale(conn, "2026-10-19")  # g2 should have stats by now


def _fake_downloads(monkeypatch, files):
    from betbot.nfl.collectors import nflverse
    files.setdefault(nflverse.GAMES_URL, (["game_id"], [["g"]]))
    files.setdefault(nflverse.INJURIES_URL.format(2026), (["season", "gsis_id"], []))
    files.setdefault(nflverse.PLAYERS_URL, (["gsis_id"], [["00-1"]]))
    monkeypatch.setattr(nflverse, "_csv", lambda url, optional=False: files[url])  # any other URL would KeyError
    monkeypatch.setattr(nflverse, "current_season", lambda: 2026)
    return nflverse


def test_load_only_refreshes_current_season(monkeypatch):
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_player_weeks", ["season", "game_id"], [[1999, "old"], [2026, "stale"]])
    replace_table(conn, "nfl_snap_counts", ["season", "game_id"], [[2012, "old snap"]])
    replace_table(conn, "nfl_injury_reports", ["season", "gsis_id"], [[2009, "x"]])
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
    replace_table(conn, "nfl_injury_reports", ["season", "gsis_id"], [[2009, "x"]])
    snap = "https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{}.csv"
    files = {snap.format(y): (["season", "game_id"], [[y, f"s{y}"]]) for y in range(2013, 2027)}
    files["https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_2026.csv"] = (
        ["season", "game_id"], [])
    nflverse = _fake_downloads(monkeypatch, files)
    nflverse.load(conn)
    assert conn.execute("SELECT min(season), max(season), count(*) FROM nfl_snap_counts").fetchone() == (2013, 2026, 14)



def test_align_lines_up_columns_that_differ_by_year():
    from betbot.nfl.collectors.nflverse import _align
    header, rows = _align([(["season", "week", "gsis_id"], [[2013, 1, "a"]]),
                           (["season", "season_type", "week", "gsis_id"], [[2026, "REG", 6, "b"]])])
    assert header == ["season", "week", "gsis_id", "season_type"]
    assert rows == [[2013, 1, "a", None], [2026, 6, "b", "REG"]]


def test_incremental_load_adds_new_columns(monkeypatch):
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_player_weeks", ["season", "game_id"], [[1999, "old"]])
    replace_table(conn, "nfl_snap_counts", ["season", "game_id"], [[2013, "s"]])
    replace_table(conn, "nfl_injury_reports", ["season", "gsis_id"], [[2009, "x"]])
    base = "https://github.com/nflverse/nflverse-data/releases/download/"
    nflverse = _fake_downloads(monkeypatch, {
        base + "stats_player/stats_player_week_2026.csv": (["season", "game_id"], []),
        base + "snap_counts/snap_counts_2026.csv": (["season", "game_id"], []),
        base + "injuries/injuries_2026.csv": (["season", "gsis_id", "season_type"], [[2026, "y", "REG"]]),
    })
    nflverse.load(conn)
    assert conn.execute("SELECT season, gsis_id, season_type FROM nfl_injury_reports ORDER BY season").fetchall() == [
        (2009, "x", None), (2026, "y", "REG")]


def test_unpublished_injury_file_does_not_block_the_update(monkeypatch):
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_player_weeks", ["season", "game_id"], [[1999, "old"]])
    replace_table(conn, "nfl_snap_counts", ["season", "game_id"], [[2013, "s"]])
    replace_table(conn, "nfl_injury_reports", ["season", "gsis_id"], [[2009, "x"]])
    base = "https://github.com/nflverse/nflverse-data/releases/download/"
    files = {base + "stats_player/stats_player_week_2026.csv": (["season", "game_id"], [[2026, "new"]]),
             base + "snap_counts/snap_counts_2026.csv": (["season", "game_id"], [])}
    nflverse = _fake_downloads(monkeypatch, files)
    files[nflverse.INJURIES_URL.format(2026)] = ([], [])  # preseason: not published yet (404)
    nflverse.load(conn)
    assert conn.execute("SELECT count(*) FROM nfl_player_weeks WHERE season = 2026").fetchone() == (1,)
    assert conn.execute("SELECT gsis_id FROM nfl_injury_reports").fetchall() == [("x",)]  # kept as is


def test_snap_reload_waits_24_hours_for_a_game_that_never_posts():
    from datetime import datetime, timezone
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_games", ["game_id", "gameday"], [["g1", "2026-10-11"]])
    replace_table(conn, "nfl_player_weeks", ["game_id"], [["g1"]])
    replace_table(conn, "nfl_snap_counts", ["game_id"], [["other"]])  # g1's snaps never posted
    replace_table(conn, "nfl_players", ["gsis_id"], [])
    replace_table(conn, "nfl_injury_reports", ["gsis_id"], [])
    now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
    assert is_stale(conn, "2026-10-17", now)                       # never fully loaded: reload
    db.record_pull(conn, "nflverse_load", "", "2026-10-17T06:00:00+00:00")
    assert not is_stale(conn, "2026-10-17", now)                    # loaded 6 hours ago: don't reload again
    assert is_stale(conn, "2026-10-19", datetime(2026, 10, 19, 12, tzinfo=timezone.utc))  # a day+ later: once more
