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


NOW = "2026-10-20T06:00:00+00:00"


def make_db(rows=None, seen="2026-10-20T00:00:00+00:00", away="NE", home="CHI"):
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_games", ["game_id", "away_team", "home_team", "gameday"],
                  [["2026_06_NE_CHI", "NE", "CHI", "2026-10-22"]])
    rows = rows or [
        ("P-1", "player_pass_yds", "Drake Maye", 199.5, 0.68, 0.72), ("P-2", "player_pass_yds", "Drake Maye", 249.5, 0.38, 0.42),
        ("S-1", "spreads", "NE", 3.5, 0.33, 0.37), ("S-2", "spreads", "CHI", 2.5, 0.40, 0.44),
        ("T-1", "totals", "total", 44.5, 0.50, 0.54), ("G-1", "h2h", "NE", None, 0.47, 0.49),
    ]
    db.save_kalshi(conn, [{"ticker": t, "series": "x", "event_ticker": "e", "market": m, "subject": sub, "strike": k,
                           "game_date": "2026-10-22", "away": away, "home": home, "yes_bid": b, "yes_ask": a,
                           "last_price": None, "volume": 1, "open_interest": 1, "close_time": None,
                           "fetched_at": "2026-10-20T00:00:00+00:00"} for t, m, sub, k, b, a in rows])
    db.mark_kalshi_seen(conn, [r[0] for r in rows], seen)
    return conn


def test_prop_and_game_probabilities_from_ladders():
    prices = kalshi.Prices(make_db(), NOW)
    assert abs(prices.prop("2026_06_NE_CHI", "player_pass_yds", "Drake Maye", "Over", 224.5) - 0.55) < 1e-9
    assert abs(prices.prop("2026_06_NE_CHI", "player_pass_yds", "Drake Maye", "Under", 249.5) - 0.60) < 1e-9
    assert prices.prop("2026_06_NE_CHI", "player_pass_yds", "Someone Else", "Over", 224.5) is None
    assert prices.game("2026_06_NE_CHI", "moneyline", "away", None) == 0.48
    assert abs(prices.game("2026_06_NE_CHI", "total", "under", 44.5) - 0.48) < 1e-9
    # NE -3.5 (away favorite): NE must win by more than 3.5
    assert abs(prices.game("2026_06_NE_CHI", "spread", "away", -3.5) - 0.35) < 1e-9
    # NE +2.5: covers unless CHI wins by more than 2.5
    assert abs(prices.game("2026_06_NE_CHI", "spread", "away", 2.5) - 0.58) < 1e-9


def test_stale_prices_are_ignored():
    # last seen 30 hours before NOW: closed, delisted, or the fetch failed since
    prices = kalshi.Prices(make_db(seen="2026-10-19T00:00:00+00:00"), NOW)
    assert prices.game("2026_06_NE_CHI", "moneyline", "away", None) is None


def test_teams_listed_in_the_other_order_still_match():
    prices = kalshi.Prices(make_db(away="CHI", home="NE"), NOW)
    assert prices.game("2026_06_NE_CHI", "moneyline", "away", None) == 0.48


def test_ten_cent_gap_is_tight_enough_and_bad_values_are_skipped():
    assert abs(kalshi.fair_prob(0.45, 0.55) - 0.50) < 1e-12  # 0.55 - 0.45 is 0.10000000000000009 in floats
    assert kalshi.fair_prob(0.45, 0.56) is None
    bad = market("KXNFLGAME-26OCT18HOUJAC-JAC", "Jacksonville wins", "—", "0.58", event="KXNFLGAME-26OCT18HOUJAC")
    assert kalshi.parse_market(bad, "KXNFLGAME")["yes_bid"] is None
    assert kalshi.parse_market({"event_ticker": "KXNFLGAME-26OCT18HOUJAC"}, "KXNFLGAME") is None  # no ticker


def test_one_failing_series_does_not_stop_the_rest(monkeypatch):
    conn = db.connect(":memory:")
    good = [market("KXNFLGAME-26OCT18HOUJAC-JAC", "Jacksonville wins", "0.56", "0.58", event="KXNFLGAME-26OCT18HOUJAC")]

    def fake_fetch(series):
        if series == "KXNFLSPREAD":
            raise ValueError("unexpected response")
        return good if series == "KXNFLGAME" else []
    monkeypatch.setattr(kalshi, "fetch", fake_fetch)
    assert kalshi.update(conn) == (1, 1)
    assert conn.execute("SELECT seen_at IS NOT NULL FROM kalshi_markets").fetchone() == (1,)


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


def test_missing_strike_does_not_crash_and_pickem_uses_moneyline():
    rows = [("S-1", "spreads", "NE", None, 0.33, 0.37), ("S-2", "spreads", "NE", 3.5, 0.33, 0.37),
            ("T-1", "totals", "total", None, 0.50, 0.54), ("G-1", "h2h", "NE", None, 0.47, 0.49)]
    prices = kalshi.Prices(make_db(rows), NOW)
    assert abs(prices.game("2026_06_NE_CHI", "spread", "away", -3.5) - 0.35) < 1e-9  # the strike-less row is skipped
    assert prices.game("2026_06_NE_CHI", "total", "over", 44.5) is None
    assert prices.game("2026_06_NE_CHI", "spread", "away", 0.0) == 0.48  # pick'em = win the game


def test_crossed_and_sub_cent_gaps():
    assert kalshi.fair_prob(0.60, 0.40) is None          # crossed book
    assert kalshi.fair_prob(0.4450, 0.5500) is None      # 10.5c: wider than 10c
    assert kalshi.fair_prob(0.4550, 0.5500) is not None  # 9.5c


def test_freshness_is_measured_from_the_given_time():
    conn = make_db(seen="2026-10-20T00:00:00+00:00")
    assert kalshi.Prices(conn, "2026-10-20T06:00:00+00:00").game("2026_06_NE_CHI", "moneyline", "away", None) == 0.48
    assert kalshi.Prices(conn, "2026-10-21T06:00:00+00:00").game("2026_06_NE_CHI", "moneyline", "away", None) is None
