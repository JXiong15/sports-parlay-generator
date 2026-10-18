import json

from betbot import db
from betbot.collectors.action_network import parse


def game(gid, away_rot, money):
    side = lambda bets: {"tickets": {"percent": bets}, "money": {"percent": money}}
    return {"id": gid, "start_time": "2026-10-18T17:00:00Z", "home_team_id": 2, "away_rotation_number": away_rot,
            "teams": [{"id": 1, "full_name": "Houston Texans"}, {"id": 2, "full_name": "Jacksonville Jaguars"}],
            "markets": {"15": {"event": {"total": [{"side": "over", "value": 42, "bet_info": side(80)},
                                                   {"side": "under", "value": 42, "bet_info": side(20)}]}},
                        "68": {"event": {"total": [{"side": "over", "value": 41.5}, {"side": "under", "value": 41.5}]}}}}


def page(games, rows_html):
    data = {"props": {"pageProps": {"scoreboardResponse": {"games": games}}}}
    return f'<table>{rows_html}</table><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'


def test_money_only_where_page_shows_it():
    html = page([game(1, 261, 40), game(2, 269, 70)], (
        "<tr><td>Texans</td><td>261</td><td>Jaguars</td><td>262</td>"
        "<td>46</td><td>%</td><td>54</td><td>%</td><td>50</td><td>%</td><td>50</td><td>%</td></tr>"
        "<tr><td>Bears</td><td>269</td><td>Falcons</td><td>270</td><td>57</td><td>%</td><td>43</td><td>%</td>"
        "<td>Unlock all money percentages with Action PRO</td></tr>"))
    rows = parse(html, "nfl", "t")
    free = [r for r in rows if r["game_id"] == 1]
    locked = [r for r in rows if r["game_id"] == 2]
    assert free[0]["home_team"] == "Jacksonville Jaguars" and free[0]["away_team"] == "Houston Texans"
    assert [(r["side"], r["line"], r["bets_pct"], r["money_pct"]) for r in free] == [
        ("over", 41.5, 80, 40), ("under", 41.5, 20, 40)]  # DraftKings line, not the 42 consensus
    assert all(r["bets_pct"] is not None and r["money_pct"] is None for r in locked)  # PRO data never kept

    conn = db.connect(":memory:")
    db.save_splits(conn, rows)
    db.save_splits(conn, rows)  # same fetch twice: no duplicates
    assert conn.execute("SELECT count(*) FROM splits").fetchone()[0] == 4


def test_layout_change_fails_loudly():
    import pytest
    with pytest.raises(RuntimeError):
        parse("<html>redesigned</html>", "nfl", "t")


def test_rows_rendered_twice_and_last_row_bounded():
    # mobile + desktop layouts both list game 2 (locked) last; text after the table has stray percentages
    locked = "<tr><td>Bears</td><td>269</td><td>57</td><td>%</td><td>43</td><td>%</td></tr>"
    free = "<tr><td>Texans</td><td>261</td>" + "<td>50</td><td>%</td>" * 4 + "</tr>"
    html = page([game(1, 261, 40), game(2, 269, 70)], free + locked + free + locked + "<h2>NFL Public Betting: Percentages, Trends and How to Use Them</h2>" + "<p>99</p><p>%</p>" * 4)
    money = {r["game_id"]: r["money_pct"] for r in parse(html, "nfl", "t")}
    assert money == {1: 40, 2: None}
