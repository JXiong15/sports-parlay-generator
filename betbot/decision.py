"""BET / FADE / STAY AWAY for one pick (any sport), following PLAN.md's decision logic:

1. Clear edge? Our (recalibrated) probability vs. the book's no-vig probability, and EV at the actual price.
   EV under MIN_EV or edge under MIN_EDGE -> STAY AWAY.
2. Market behavior (when % of bets / money and line movement are known):
   - sharp money on the other side (its money % well above its bets %)          -> STAY AWAY
   - the public piled on our side (85%+ of bets) but the line didn't move for them -> STAY AWAY (bait line)
   - the line moved against us since it opened (steam)                           -> STAY AWAY
   - we're against a heavy public side and sharp money agrees with us             -> FADE (fade the public)
   - otherwise                                                                   -> BET
3. Only positive-EV picks can be BET or FADE.
shortcut: thresholds below are reasonable defaults, not backtested; the backtest found no proven edge, so every
verdict carries NO_EDGE_WARNING until graded picks say otherwise.
"""
from betbot.odds import expected_value, no_vig

MIN_EV = 0.02        # +2% expected return per $1
MIN_EDGE = 0.03      # 3 points of probability over the book's no-vig price
SHARP_GAP = 10       # money % minus bets % on one side that marks sharp money
PUBLIC_HEAVY = 70    # % of bets that marks a heavy public side
BAIT = 85            # % of bets on our side with no line move for them
NO_EDGE_WARNING = ("Backtest found no proven edge (game lines at coin-flip accuracy; props break-even on real "
                   "lines): research only, not a likely winner.")


def decide(model_prob, price, other_price=None, bets_pct=None, money_pct=None, line_moved_for_us=None):
    """model_prob: our probability this side wins. bets_pct / money_pct: % on OUR side (the other side is the
    rest). line_moved_for_us: points (or % of a prop line) the line moved toward our side since it opened;
    negative = against us. Returns a dict with verdict, ev, edge, fair (book no-vig probability), reasons."""
    fair, _ = no_vig(price, other_price)
    ev = expected_value(model_prob, price)
    edge = model_prob - fair
    out = {"verdict": "STAY AWAY", "ev": round(ev, 4), "edge": round(edge, 4), "fair": round(fair, 4),
           "reasons": [], "warning": NO_EDGE_WARNING}
    if ev < MIN_EV or edge < MIN_EDGE:
        out["reasons"].append(f"no clear edge (EV {ev:+.1%}, edge {edge:+.1%} vs. book's no-vig {fair:.1%})")
        return out
    sharp_us = sharp_them = public_on_us = public_on_them = False
    if bets_pct is not None and money_pct is not None:
        sharp_us = money_pct - bets_pct >= SHARP_GAP
        sharp_them = (100 - money_pct) - (100 - bets_pct) >= SHARP_GAP
        public_on_us, public_on_them = bets_pct >= PUBLIC_HEAVY, 100 - bets_pct >= PUBLIC_HEAVY
        if sharp_them:
            out["reasons"].append(f"sharp money on the other side ({100 - money_pct:.0f}% of money vs."
                                  f" {100 - bets_pct:.0f}% of bets)")
            return out
    if bets_pct is not None and bets_pct >= BAIT and line_moved_for_us is not None and line_moved_for_us <= 0:
        out["reasons"].append(f"bait pattern: {bets_pct:.0f}% of bets on our side but the line hasn't moved for them")
        return out
    if line_moved_for_us is not None and line_moved_for_us < 0:
        out["reasons"].append(f"line moved against us since it opened ({line_moved_for_us:+g})")
        return out
    out["reasons"].append(f"EV {ev:+.1%}, edge {edge:+.1%} over the book's no-vig {fair:.1%}")
    if public_on_them and sharp_us:
        out["verdict"] = "FADE"
        out["reasons"].append(f"fading the public ({100 - bets_pct:.0f}% of bets on the other side) with sharp"
                              f" money on ours ({money_pct:.0f}% of money vs. {bets_pct:.0f}% of bets)")
    else:
        out["verdict"] = "BET"
        if sharp_us:
            out["reasons"].append("sharp money agrees")
    return out
