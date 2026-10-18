from betbot import db
from betbot.nfl.collectors.nflverse import replace_table
from betbot.nfl.prop_splits import dataframe


def test_tagged_with_week_and_sorted():
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_games", ["game_id", "season", "week", "gameday", "gametime"], [
        ["w5", 2026, 5, "2026-10-11", "13:00"], ["w6", 2026, 6, "2026-10-15", "20:15"]])
    row = {"game": None, "market": "player_pass_yds", "market_text": "Pass Yds", "period": None, "line": 220.5,
           "over_handle": 60, "under_handle": 40, "over_bets": 70, "under_bets": 30}
    for aid, published, player in ((2, "2026-10-15T16:25:55", "TNF Guy"), (1, "2026-10-11T11:16:52", "Sunday Guy")):
        db.save_prop_splits(conn, {"id": aid, "league": "nfl", "link": "x", "published": published, "modified": "m",
                                   "fetched_at": "f"}, [dict(row, player=player)])
    df = dataframe(conn)
    assert list(df["player"]) == ["Sunday Guy", "TNF Guy"]
    assert list(df["week"]) == [5, 6]
    assert df.loc[0, ["over_ticket_pct", "under_ticket_pct", "over_handle_pct", "under_handle_pct"]].tolist() == [70, 30, 60, 40]
