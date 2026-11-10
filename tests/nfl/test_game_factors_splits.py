import sqlite3

from betbot.nfl.game_factors import _splits


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE archive_splits (game_id, market, side, bets_pct, money_pct, timestamp)")
    conn.execute("CREATE TABLE splits (home_team, away_team, start_time, market, side, bets_pct, money_pct, fetched_at)")
    return conn


def test_splits_with_no_saved_rows_is_empty_not_a_keyerror():
    out = _splits(_db())
    assert out.empty and list(out.columns) == ["bets_home", "bets_over", "gap_home", "gap_over"]


def test_splits_with_only_one_market_keeps_float_columns():
    conn = _db()
    conn.execute("INSERT INTO archive_splits VALUES ('g1', 'spread', 'home', 70, 60, '2026-01-01')")
    out = _splits(conn)
    assert (out.dtypes == float).all()
    assert out.loc["g1", "bets_home"] == 0.4 and out.loc["g1", "bets_over"] == 0.0
