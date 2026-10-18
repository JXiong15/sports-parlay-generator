import json
from datetime import date

from betbot import db
from betbot.nfl.collectors import archive_splits as a
from betbot.nfl.collectors.nflverse import replace_table


def test_nicknames_across_eras_and_sources():
    assert a.nicknames_in("Denver Broncos New York Jets History 24 VSiN Picks") == ["Broncos", "Jets"]
    assert a.nicknames_in("Wash Commanders Philadelphia Eagles") == ["Commanders", "Eagles"]
    assert a.nickname("Washington Football Team") == "Commanders"
    assert a.nickname("Washington Redskins") == "Commanders"
    assert a.nickname("BUF Bills") == "Bills" and a.nickname("OAK") == "Raiders" and a.nickname("JAC") == "Jaguars"
    assert a.nickname("Man City") is None


def make_games():
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_games", ["game_id", "away_team", "home_team", "gameday"], [
        ["2018_09_OAK_SF", "OAK", "SF", "2018-11-01"], ["2026_04_JAX_LV", "JAX", "LV", "2026-09-27"],
        ["2026_06_HOU_JAX", "HOU", "JAX", "2026-10-18"]])
    return conn


def test_match_game_handles_old_names_dates_and_neutral_sites():
    conn = make_games()
    assert a.match_game(conn, "OAK", "SF", date(2018, 10, 30)) == "2018_09_OAK_SF"
    assert a.match_game(conn, "Jacksonville Jaguars", "Las Vegas Raiders", date(2026, 9, 26)) == "2026_04_JAX_LV"
    assert a.match_game(conn, "Jaguars", "Raiders", date(2026, 8, 1)) is None  # too far from the game
    # London game listed with the teams swapped: still matched, and sides follow nflverse's home/away
    gid = a.match_game(conn, "JAX Jaguars", "HOU Texans", date(2026, 10, 17))
    assert gid == "2026_06_HOU_JAX"
    # the page's "home" side here is the Texans, but nflverse has the Jaguars at home, so it becomes "away"
    row = a._row("JAX Jaguars", "HOU Texans", "spread", "home", -2.5)
    assert a.resolve_sides(conn, row, gid)["side"] == "away"
    assert a.resolve_sides(conn, a._row("JAX Jaguars", "HOU Texans", "total", "over"), gid)["side"] == "over"


def test_action_network_2018_table_bets_only():
    page = ("<table><tr><td>% of Bets</td></tr><tr><td>307</td><td>OAK</td><td>308</td><td>SF</td>"
            "<td>Fri 11/02 12:20AM</td><td>+4</td><td>-4</td><td>-1.5</td><td>-110</td><td>+1.5</td><td>-110</td>"
            "<td>71%</td><td>29%</td><td>EDGE</td></tr>"
            "<tr><td>463</td><td>CHI</td><td>464</td><td>BUF</td><td>Sun</td><td>-8</td><td>+8</td><td>-10</td>"
            "<td>-105</td><td>+10</td><td>-115</td><td>66%</td><td>34%</td><td>EDGE</td></tr></table>")
    rows = a.parse_action_network(page)
    assert [(r["away"], r["side"], r["line"], r["price"], r["bets_pct"], r["money_pct"]) for r in rows] == [
        ("OAK", "away", -1.5, -110, 71, None), ("OAK", "home", 1.5, -110, 29, None),
        ("CHI", "away", -10.0, -105, 66, None), ("CHI", "home", 10.0, -115, 34, None)]


def test_action_network_2022_odds_only_free_money():
    def game(gid, rot, money):
        return {"id": gid, "away_rotation_number": rot, "home_team_id": 2, "start_time": "2022-11-24T17:30:00Z",
                "teams": [{"id": 1, "full_name": "Buffalo Bills"}, {"id": 2, "full_name": "Detroit Lions"}],
                "odds": [{"book_id": 15, "spread_home": 9.5, "spread_away": -9.5, "spread_home_line": -106,
                          "spread_away_line": -110, "spread_home_public": 47, "spread_away_public": 53,
                          "spread_home_money": money, "spread_away_money": money, "ml_home": 330, "ml_away": -440,
                          "ml_home_public": 30, "ml_away_public": 70, "ml_home_money": money, "ml_away_money": money,
                          "total": 54.5, "over": -110, "under": -110, "total_over_public": 65,
                          "total_under_public": 35, "total_over_money": money, "total_under_money": money}]}
    data = {"props": {"pageProps": {"scoreboardResponse": {"games": [game(1, 101, 80), game(2, 103, 64)]}}}}
    shown = "<td>Bills</td><td>101</td>" + "<td>50</td><td>%</td>" * 4
    locked = "<td>Bills</td><td>103</td><td>53</td><td>%</td><td>47</td><td>%</td>"
    page = f'{shown}{locked}<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
    rows = a.parse_action_network(page)
    assert len(rows) == 12
    free = [r for r in rows if r["money_pct"] is not None]
    assert len(free) == 6 and all(r["money_pct"] == 80 for r in free)  # game 2's 64 is PRO-only: never kept
    over = next(r for r in rows if r["side"] == "over")
    assert (over["line"], over["price"], over["bets_pct"]) == (54.5, -110, 65)


def test_vsin_reads_handle_and_bets_in_order():
    cells = ["Denver Broncos New York Jets History 24 VSiN Picks", "-7.5 +7.5", "32% 68% Win", "58% 42% Loss",
             "43.5 43.5", "65% 35% Loss", "63% 37% Loss", "-395 +310", "52% 48% Win", "87% 13% Win"]
    page = "<table><tr>" + "".join(f"<td>{c}" for c in cells) + "</tr></table>"  # VSiN leaves cells unclosed
    rows = {(r["market"], r["side"]): r for r in a.parse_vsin(page)}
    assert (rows["spread", "away"]["line"], rows["spread", "away"]["money_pct"], rows["spread", "away"]["bets_pct"]) == (
        -7.5, 32, 58)
    assert (rows["total", "under"]["line"], rows["total", "under"]["money_pct"], rows["total", "under"]["bets_pct"]) == (
        43.5, 35, 37)
    assert (rows["moneyline", "home"]["line"], rows["moneyline", "home"]["price"]) == (None, 310)
    assert rows["spread", "away"]["away"] == "Broncos" and rows["spread", "away"]["home"] == "Jets"


def test_dk_network_nfl_only_with_alt_lines():
    page = ("<div>LA Chargers @ </div><div>BUF Bills </div><div>9/27, 01:00PM </div>"
            "<div>Spread</div><div>Odds</div><div>% Handle</div><div>% Bets</div>"
            "<div>BUF Bills -7</div><div>−115 </div><div>79% </div><div>81% </div>"
            "<div>LA Chargers +7</div><div>−105 </div><div>21% </div><div>19% </div>"
            "<div>Total</div><div>Odds</div><div>% Handle</div><div>% Bets</div>"
            "<div>Over 47.5</div><div>−110 </div><div>60% </div><div>55% </div>"
            "<div>Leicester @ </div><div>Man City </div><div>12/29, 09:30AM</div>"
            "<div>Moneyline</div><div>Odds</div><div>% Handle</div><div>% Bets</div>"
            "<div>Leicester</div><div>+650 </div><div>31% </div><div>16% </div>")
    rows = a.parse_dk_network(page, date(2026, 9, 26))
    assert [(r["market"], r["side"], r["line"], r["price"], r["money_pct"], r["bets_pct"]) for r in rows] == [
        ("spread", "home", -7.0, -115, 79.0, 81.0), ("spread", "away", 7.0, -105, 21.0, 19.0),
        ("total", "over", 47.5, -110, 60.0, 55.0)]  # the soccer game is dropped
    assert rows[0]["start_time"] == "2026-09-27"
    assert a._dk_date("1/04, 01:00PM", date(2026, 12, 30)) == "2027-01-04"  # season crosses the new year


def test_vsin_views_and_circa_labeled():
    assert a._source_for("vsin", "https://data.vsin.com/betting-splits/?bookid=dk&view=cbb") is None
    assert a._source_for("vsin", "https://data.vsin.com/betting-splits/?bookid=circa") == "vsin_circa"
    assert a._source_for("vsin_nfl", "https://data.vsin.com/nfl/betting-splits/?view=circa") == "vsin_circa"
    assert a._source_for("vsin_nfl", "https://data.vsin.com/nfl/betting-splits/") == "vsin_dk"


def test_captures_lists_nfl_season_copies_and_retries(monkeypatch):
    answers = ["", '[["timestamp","original"],["20241006120000","https://data.vsin.com/betting-splits/?bookid=dk&view=cbb"],'
                   '["20241006130000","https://data.vsin.com/betting-splits/?bookid=circa"],'
                   '["20240706130000","https://data.vsin.com/betting-splits/"]]']
    seen = []

    def fake_get(address, params=None):
        seen.append(params["url"])
        return answers.pop(0)
    monkeypatch.setattr(a, "_get", fake_get)
    monkeypatch.setattr(a.time, "sleep", lambda s: None)
    # first answer empty (archive busy) -> retried; basketball view and July copy dropped
    assert a.captures("vsin") == [("vsin_circa", "20241006130000", "https://data.vsin.com/betting-splits/?bookid=circa")]
    assert seen == ["data.vsin.com/betting-splits"] * 2
