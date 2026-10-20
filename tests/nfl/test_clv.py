from betbot.nfl.clv import clv_stats


def test_clv_stats_rewards_edge_that_the_market_follows():
    s = clv_stats([2, -1, 3, -2], [1, -1, 2, -1])
    assert s["mean_clv"] == 1.25 and s["share_positive"] == 1.0 and s["corr"] > 0.9


def test_clv_stats_ignores_unmoved_lines_in_share():
    assert clv_stats([1, 1, -1], [0, -2, 1])["share_positive"] == 0.0


def test_hindsight_names_exist_in_game_factors():
    from betbot.nfl import game_factors as gf
    from betbot.nfl.clv import HINDSIGHT
    assert HINDSIGHT <= set(gf.MARGIN + gf.TOTAL)
