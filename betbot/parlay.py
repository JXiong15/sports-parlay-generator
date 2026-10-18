"""Parlay math for any sport: the true chance all legs win when some are correlated, and the parlay's EV.

Each leg is a dict with at least: prob (our probability it wins), price (American odds), game (legs from
different games are independent), label. The caller supplies rho(leg_a, leg_b), the correlation between two
same-game legs' outcomes (positive = they tend to win together). The combined probability uses a Gaussian
copula: correlated normal draws, a leg wins when its draw clears the threshold its probability implies.
"""
from itertools import combinations
from statistics import NormalDist

import numpy as np

from betbot.odds import american, decimal_odds

DRAWS = 200_000


def _nearest_correlation(r):
    """Clips negative eigenvalues so a hand-assembled correlation matrix is valid."""
    vals, vecs = np.linalg.eigh(r)
    fixed = vecs @ np.diag(np.clip(vals, 1e-6, None)) @ vecs.T
    d = np.sqrt(np.diag(fixed))
    return fixed / np.outer(d, d)


_DRAWS_CACHE = {}


def _standard_draws(draws, n, seed):
    """The same independent normal draws for every parlay of n legs: faster, and parlays compare consistently."""
    key = (draws, n, seed)
    if key not in _DRAWS_CACHE:
        _DRAWS_CACHE[key] = np.random.default_rng(seed).standard_normal((draws, n))
    return _DRAWS_CACHE[key]


def joint_probability(probs, corr, draws=DRAWS, seed=7):
    """P(every leg wins). probs: each leg's win probability; corr: leg outcome correlation matrix."""
    probs = np.asarray(probs, dtype=float)
    corr = _nearest_correlation(np.asarray(corr, dtype=float))
    z = _standard_draws(draws, len(probs), seed) @ np.linalg.cholesky(corr).T
    # a leg wins when its standard-normal draw is above the point that leaves `prob` above it
    thresholds = np.array([NormalDist().inv_cdf(1 - p) for p in probs])
    return float(np.mean(np.all(z > thresholds, axis=1)))


def evaluate(legs, rho):
    """True combined probability (with same-game correlation), independent probability, price and EV."""
    n = len(legs)
    corr = np.eye(n)
    for i, j in combinations(range(n), 2):
        if legs[i]["game"] == legs[j]["game"]:
            corr[i, j] = corr[j, i] = rho(legs[i], legs[j])
    probs = [leg["prob"] for leg in legs]
    true_p = joint_probability(probs, corr) if np.any(corr != np.eye(n)) else float(np.prod(probs))
    dec = float(np.prod([decimal_odds(leg["price"]) for leg in legs]))
    return {"legs": legs, "true_prob": true_p, "independent_prob": float(np.prod(probs)),
            "decimal": dec, "price": american(dec), "ev": true_p * dec - 1,
            "same_game": any(legs[i]["game"] == legs[j]["game"] for i, j in combinations(range(n), 2))}


def build(legs, rho, conflicts, max_legs=3, top=10, pool=12):
    """Best parlays of 2..max_legs legs from the `pool` highest-EV legs, positive EV only, sorted by EV.
    conflicts(a, b) -> True for legs that can't go together (both sides of one bet, for example)."""
    candidates = sorted(legs, key=lambda leg: -leg["ev"])[:pool]
    out = []
    for size in range(2, max_legs + 1):
        for combo in combinations(candidates, size):
            if any(conflicts(a, b) for a, b in combinations(combo, 2)):
                continue
            result = evaluate(list(combo), rho)
            if result["ev"] > 0:
                out.append(result)
    return sorted(out, key=lambda r: -r["ev"])[:top]
