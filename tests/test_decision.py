from betbot.decision import NO_EDGE_WARNING, decide


def test_no_edge_is_stay_away():
    d = decide(0.52, -110, -110)
    assert d["verdict"] == "STAY AWAY" and "no clear edge" in d["reasons"][0] and d["fair"] == 0.5
    assert d["warning"] == NO_EDGE_WARNING


def test_edge_without_market_data_is_bet():
    d = decide(0.60, -110, -110)
    assert d["verdict"] == "BET" and abs(d["ev"] - (0.6 * (1 + 100 / 110) - 1)) < 1e-4 and d["edge"] == 0.1
    assert d["prob"] == 0.6 and d["adjustments"] == []


def test_sharp_money_against_us_lowers_the_probability_but_does_not_cancel():
    # 60% of bets on us but only 40% of the money: the other side has 60% of money on 40% of bets
    d = decide(0.60, -110, -110, bets_pct=60, money_pct=40)
    assert d["verdict"] == "BET" and abs(d["prob"] - 0.58) < 1e-9
    assert any("sharp money on the other side" in r for r in d["reasons"])
    assert decide(0.53, -110, -110, bets_pct=60, money_pct=40)["verdict"] == "STAY AWAY"   # too thin to survive it


def test_fade_the_public_with_sharp_money_on_our_side():
    # only 25% of bets on our side but 45% of the money
    d = decide(0.60, -110, -110, bets_pct=25, money_pct=45)
    assert d["verdict"] == "FADE" and any("fading the public" in r for r in d["reasons"])
    assert d["prob"] > 0.60


def test_bait_line_and_line_moves_nudge_by_their_size():
    bait = decide(0.60, -110, -110, bets_pct=90, money_pct=92, line_moved_for_us=0)
    assert abs(bait["prob"] - 0.58) < 1e-9 and bait["verdict"] == "BET"
    against = decide(0.60, -110, -110, line_moved_for_us=-1.5)
    assert abs(against["prob"] - 0.585) < 1e-9 and any("moved against us" in r for r in against["reasons"])
    assert decide(0.60, -110, -110, line_moved_for_us=1.0)["prob"] > 0.60
    assert decide(0.60, -110, -110, line_moved_for_us=-9)["prob"] == 0.57        # capped at 3 points


def test_positive_ev_needed_even_with_edge_at_bad_price():
    # 55% vs a no-vig 50% looks like an edge, but at -150 the EV is negative
    d = decide(0.55, -150, 130)
    assert d["verdict"] == "STAY AWAY"


def test_prediction_market_pulls_the_probability_toward_its_number():
    d = decide(0.60, -110, -110, market_prob=0.45)
    assert abs(d["prob"] - 0.555) < 1e-9 and d["verdict"] == "BET" and any("prediction market" in r for r in d["reasons"])
    assert decide(0.60, -110, -110, market_prob=0.55)["prob"] > 0.584
    assert decide(0.55, -110, -110, market_prob=0.40)["verdict"] == "STAY AWAY"
