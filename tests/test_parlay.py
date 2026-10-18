import numpy as np

from betbot import parlay


def leg(label, prob, price=-110, game="g1", **extra):
    return dict(label=label, prob=prob, price=price, game=game, ev=prob * (1 + 100 / 110) - 1, **extra)


def test_joint_probability_independent_and_perfectly_correlated():
    p = parlay.joint_probability([0.6, 0.5], np.eye(2))
    assert abs(p - 0.30) < 0.005
    both = parlay.joint_probability([0.6, 0.5], [[1, 0.999], [0.999, 1]])
    assert abs(both - 0.50) < 0.01  # almost the same event: chance is the smaller leg's
    opposite = parlay.joint_probability([0.6, 0.5], [[1, -0.999], [-0.999, 1]])
    assert opposite < 0.12  # nearly mutually exclusive: at most 0.6 + 0.5 - 1 = 0.1


def test_evaluate_uses_correlation_only_within_a_game():
    a, b = leg("A", 0.6, game="g1"), leg("B", 0.6, game="g2")
    r = parlay.evaluate([a, b], lambda x, y: 0.9)
    assert r["true_prob"] == r["independent_prob"] and not r["same_game"]
    c = leg("C", 0.6, game="g1")
    r = parlay.evaluate([a, c], lambda x, y: 0.5)
    assert r["same_game"] and r["true_prob"] > r["independent_prob"] + 0.05
    assert abs(r["decimal"] - (1 + 100 / 110) ** 2) < 1e-9 and r["price"] == 264
    assert abs(r["ev"] - (r["true_prob"] * r["decimal"] - 1)) < 1e-12


def test_build_keeps_positive_ev_skips_conflicts():
    legs = [leg("A over", 0.6, game="g1", key="A"), leg("A under", 0.55, game="g1", key="A"),
            leg("B", 0.58, game="g2", key="B"), leg("C", 0.30, game="g3", key="C")]
    results = parlay.build(legs, lambda x, y: 0.0, lambda x, y: x["key"] == y["key"], max_legs=2)
    labels = [sorted(l["label"] for l in r["legs"]) for r in results]
    assert ["A over", "A under"] not in labels  # both sides of one bet
    assert all(r["ev"] > 0 for r in results)
    assert not any("C" in l for ls in labels for l in ls)  # a 30% leg drags every parlay negative
