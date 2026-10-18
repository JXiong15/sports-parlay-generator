from betbot import db
from betbot.collectors.dk_network import parse, parse_prop


def table(header, *rows):
    tr = lambda cells, tag: "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"
    return "<table>" + tr(header, "th") + "".join(tr(r, "td") for r in rows) + "</table>"


def test_parse_prop_wordings():
    assert parse_prop("Michael Penix Jr. Passing Attempts 28.5") == (
        "Michael Penix Jr.", "player_pass_attempts", "Passing Attempts", None, 28.5)
    assert parse_prop("Jacoby Brissett passing yards", 231.5)[1:] == ("player_pass_yds", "passing yards", None, 231.5)
    assert parse_prop("A.J. Barner Rec Yds – 1st Half 10.5")[0] == "A.J. Barner"
    assert parse_prop("A.J. Barner Rec Yds – 1st Half 10.5")[3] == "1H"
    assert parse_prop("Alvin Kamara rushing yards in the first quarter", 4.5)[1:4] == ("player_rush_yds", "rushing yards", "1Q")
    assert parse_prop("Alex Anzalone tackles + assists", 7.5)[1] == "player_tackles_assists"
    assert parse_prop("Bo Nix Longest Passing Completion 34.5")[:2] == ("Bo Nix", "player_pass_longest_completion")
    assert parse_prop("Bijan Robinson Rushing + Rec Yds 95.5")[:2] == ("Bijan Robinson", "player_rush_reception_yds")
    assert parse_prop("Bo Nix passing + rushing yards", 250.5)[:2] == ("Bo Nix", "player_pass_rush_yds")
    assert parse_prop("Someone Fantasy Score 12.5") is None  # unknown wording is reported, not guessed
    assert parse_prop("Jordan Love Pass TDs") is None  # no line


def test_all_table_layouts():
    page = (
        table(["Rank", "Most bet by handle", "Most bet by bets"], ["1", "Jaxon Smith-Njigba", "Jaxon Smith-Njigba"])
        + table(["Game", "Player prop", "Handle %", "Bet %"],
                ["CHI Bears vs. GB Packers", "Jordan Love Pass TDs 1.5", "Over: 74.5%<br>Under: 25.5%",
                 "Over: 71.4%<br>Under: 28.6%"],
                ["BAL Ravens vs. ATL Falcons", "Bijan Robinson Longest Rush 17.5", "Over: 100.0% Under: N/A",
                 "Over: 100.0% Under: N/A"],
                ["X vs. Y", "Some Guy Fantasy Score 12.5", "Over: 50% Under: 50%", "Over: 50% Under: 50%"])
        + table(["Player prop", "Line", "Handle leader", "Bet leader"],
                ["Patrick Mahomes Passing Completions", "21.5", "Under (60.0%)", "Under (73.2%)"],
                ["Alex Anzalone tackles + assists", "N/A", "Under (87.4%)", "Under (91.0%)"])
    )
    rows, unreadable = parse(page)
    assert unreadable == ["Some Guy Fantasy Score 12.5 (unknown wording: add it to MARKET_KEYS)",
                          "Alex Anzalone tackles + assists (DraftKings gives no line)"]
    love, bijan, mahomes = rows
    assert (love["game"], love["player"], love["market"], love["line"]) == (
        "CHI Bears vs. GB Packers", "Jordan Love", "player_pass_tds", 1.5)
    assert (love["over_handle"], love["under_handle"], love["over_bets"], love["under_bets"]) == (74.5, 25.5, 71.4, 28.6)
    assert (bijan["over_handle"], bijan["under_handle"]) == (100.0, None)
    assert mahomes["game"] is None and (mahomes["over_handle"], mahomes["under_handle"]) == (40.0, 60.0)


def test_save_replaces_an_edited_article():
    conn = db.connect(":memory:")
    article = {"id": 7, "league": "nfl", "link": "x", "published": "p", "modified": "m1", "fetched_at": "f"}
    row = {"game": None, "player": "P", "market": "player_pass_yds", "market_text": "Pass Yds", "period": None,
           "line": 200.5, "over_handle": 1, "under_handle": 99, "over_bets": 2, "under_bets": 98}
    db.save_prop_splits(conn, article, [row, row])
    db.save_prop_splits(conn, dict(article, modified="m2"), [row])
    assert conn.execute("SELECT count(*) FROM prop_splits").fetchone()[0] == 1
    assert conn.execute("SELECT modified FROM dk_articles").fetchone()[0] == "m2"
