from betbot.odds import american, decimal_odds, expected_value, implied_prob, no_vig


def test_implied_and_no_vig():
    assert round(implied_prob(-110), 4) == 0.5238 and implied_prob(150) == 0.4
    p, removed = no_vig(-110, -110)
    assert removed and abs(p - 0.5) < 1e-12
    fav, _ = no_vig(-200, 170)
    dog, _ = no_vig(170, -200)
    assert abs(fav + dog - 1) < 1e-12 and fav > 0.6
    assert no_vig(-110, None) == (implied_prob(-110), False)


def test_payouts_and_ev():
    assert round(decimal_odds(-110), 4) == 1.9091 and decimal_odds(150) == 2.5
    assert abs(expected_value(0.5, 100)) < 1e-12  # fair coin at even money
    assert expected_value(0.5, -110) < 0 < expected_value(0.6, -110)
    assert american(2.5) == 150 and american(1.5) == -200
