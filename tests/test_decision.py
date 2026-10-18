from betbot.decision import NO_EDGE_WARNING, decide


def test_no_edge_is_stay_away():
    d = decide(0.52, -110, -110)
    assert d["verdict"] == "STAY AWAY" and "no clear edge" in d["reasons"][0] and d["fair"] == 0.5
    assert d["warning"] == NO_EDGE_WARNING


def test_edge_without_market_data_is_bet():
    d = decide(0.60, -110, -110)
    assert d["verdict"] == "BET" and abs(d["ev"] - (0.6 * (1 + 100 / 110) - 1)) < 1e-4 and d["edge"] == 0.1


def test_sharp_money_against_us_is_stay_away():
    # 60% of bets on us but only 40% of the money: the other side has 60% of money on 40% of bets
    d = decide(0.60, -110, -110, bets_pct=60, money_pct=40)
    assert d["verdict"] == "STAY AWAY" and "sharp money on the other side" in d["reasons"][0]


def test_fade_the_public_with_sharp_money_on_our_side():
    # only 25% of bets on our side but 45% of the money
    d = decide(0.60, -110, -110, bets_pct=25, money_pct=45)
    assert d["verdict"] == "FADE" and any("fading the public" in r for r in d["reasons"])


def test_bait_line_and_steam_against_us():
    assert decide(0.60, -110, -110, bets_pct=90, money_pct=92, line_moved_for_us=0)["verdict"] == "STAY AWAY"
    d = decide(0.60, -110, -110, line_moved_for_us=-1.5)
    assert d["verdict"] == "STAY AWAY" and "moved against us" in d["reasons"][0]
    assert decide(0.60, -110, -110, line_moved_for_us=1.0)["verdict"] == "BET"


def test_positive_ev_needed_even_with_edge_at_bad_price():
    # 55% vs a no-vig 50% looks like an edge, but at -150 the EV is negative
    d = decide(0.55, -150, 130)
    assert d["verdict"] == "STAY AWAY"
