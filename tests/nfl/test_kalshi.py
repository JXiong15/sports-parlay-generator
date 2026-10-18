from betbot import db
from betbot.nfl.collectors import kalshi
from betbot.nfl.collectors.nflverse import replace_table


def market(ticker, title, bid, ask, strike=None, event=None):
    return {"ticker": ticker, "event_ticker": event,
            "title": title, "yes_bid_dollars": bid, "yes_ask_dollars": ask, "last_price_dollars": "0.0000",
            "volume_fp": "10.00", "open_interest_fp": "5.00", "close_time": "2026-10-20T13:30:00Z",
            "floor_strike": strike}


def test_parse_event_and_team_codes():
    assert kalshi.parse_event("KXNFLRECYDS-26OCT18HOUJAC") == ("2026-10-18", "HOU", "JAX")
    assert kalshi.parse_event("KXNFLGAME-26OCT22NECHI") == ("2026-10-22", "NE", "CHI")
    assert kalshi.parse_event("KXNFLGAME-26OCT18LACKC") == ("2026-10-18", "LAC", "KC")
    assert kalshi.parse_event("KXNFLGAME-26OCT18ZZZYYY") is None


def test_parse_markets_by_type():
    prop = kalshi.parse_market(market("KXNFLPASSYDS-26OCT18BALCLE-BALTHUNTLEY5-275", "Tyler Huntley: 275+ passing yards",
                                      "0.0500", "0.0700", 274.5, "KXNFLPASSYDS-26OCT18BALCLE"), "KXNFLPASSYDS")
    assert (prop["market"], prop["subject"], prop["strike"], prop["away"], prop["home"]) == (
        "player_pass_yds", "Tyler Huntley", 274.5, "BAL", "CLE")
    spread = kalshi.parse_market(market("KXNFLSPREAD-26OCT22NECHI-NE8", "New England wins by over 7.5 points?",
                                        "0.19", "0.21", 7.5, "KXNFLSPREAD-26OCT22NECHI"), "KXNFLSPREAD")
    assert (spread["market"], spread["subject"], spread["strike"]) == ("spreads", "NE", 7.5)
    ml = kalshi.parse_market(market("KXNFLGAME-26OCT18HOUJAC-JAC", "Jacksonville wins", "0.56", "0.58",
                                    event="KXNFLGAME-26OCT18HOUJAC"), "KXNFLGAME")
    assert (ml["market"], ml["subject"]) == ("h2h", "JAX")


def test_fair_prob_and_interpolation():
    assert abs(kalshi.fair_prob(0.40, 0.44) - 0.42) < 1e-12
    assert kalshi.fair_prob(0.25, 0.82) is None and kalshi.fair_prob(0.0, 0.05) is None  # too thin
    ladder = [(199.5, 0.70), (249.5, 0.40), (299.5, 0.10)]
    assert kalshi.interpolate(ladder, 249.5) == 0.40
    assert abs(kalshi.interpolate(ladder, 224.5) - 0.55) < 1e-9
    assert kalshi.interpolate(ladder, 150.5) is None and kalshi.interpolate([], 1) is None


def make_db():
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_games", ["game_id", "away_team", "home_team", "gameday"],
                  [["2026_06_NE_CHI", "NE", "CHI", "2026-10-22"]])
    rows = [
        ("P-1", "player_pass_yds", "Drake Maye", 199.5, 0.68, 0.72), ("P-2", "player_pass_yds", "Drake Maye", 249.5, 0.38, 0.42),
        ("S-1", "spreads", "NE", 3.5, 0.33, 0.37), ("S-2", "spreads", "CHI", 2.5, 0.40, 0.44),
        ("T-1", "totals", "total", 44.5, 0.50, 0.54), ("G-1", "h2h", "NE", None, 0.47, 0.49),
    ]
    db.save_kalshi(conn, [{"ticker": t, "series": "x", "event_ticker": "e", "market": m, "subject": s, "strike": k,
                           "game_date": "2026-10-22", "away": "NE", "home": "CHI", "yes_bid": b, "yes_ask": a,
                           "last_price": None, "volume": 1, "open_interest": 1, "close_time": None,
                           "fetched_at": "2026-10-20T00:00:00+00:00"} for t, m, s, k, b, a in rows])
    return conn


def test_prop_and_game_probabilities_from_ladders():
    conn = make_db()
    assert abs(kalshi.prop_prob(conn, "2026_06_NE_CHI", "player_pass_yds", "Drake Maye", "Over", 224.5) - 0.55) < 1e-9
    assert abs(kalshi.prop_prob(conn, "2026_06_NE_CHI", "player_pass_yds", "Drake Maye", "Under", 249.5) - 0.60) < 1e-9
    assert kalshi.prop_prob(conn, "2026_06_NE_CHI", "player_pass_yds", "Someone Else", "Over", 224.5) is None
    assert kalshi.game_prob(conn, "2026_06_NE_CHI", "moneyline", "away", None) == 0.48
    assert abs(kalshi.game_prob(conn, "2026_06_NE_CHI", "total", "under", 44.5) - 0.48) < 1e-9
    # NE -3.5 (away favorite): NE must win by more than 3.5
    assert abs(kalshi.game_prob(conn, "2026_06_NE_CHI", "spread", "away", -3.5) - 0.35) < 1e-9
    # NE +2.5: covers unless CHI wins by more than 2.5
    assert abs(kalshi.game_prob(conn, "2026_06_NE_CHI", "spread", "away", 2.5) - 0.58) < 1e-9


def test_only_price_changes_are_saved():
    conn = make_db()
    base = conn.execute("SELECT count(*) FROM kalshi_markets").fetchone()[0]
    row = {"ticker": "G-1", "series": "x", "event_ticker": "e", "market": "h2h", "subject": "NE", "strike": None,
           "game_date": "2026-10-22", "away": "NE", "home": "CHI", "yes_bid": 0.47, "yes_ask": 0.49,
           "last_price": None, "volume": 2, "open_interest": 1, "close_time": None,
           "fetched_at": "2026-10-20T01:00:00+00:00"}
    assert db.save_kalshi(conn, [row]) == 0                    # same prices: nothing new
    assert db.save_kalshi(conn, [dict(row, yes_bid=0.50, fetched_at="2026-10-20T02:00:00+00:00")]) == 1
    assert conn.execute("SELECT count(*) FROM kalshi_markets").fetchone()[0] == base + 1
