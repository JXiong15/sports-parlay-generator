"""BET / FADE / STAY AWAY for one pick (any sport), following PLAN.md's decision logic:

1. Start from our (recalibrated) probability for the pick.
2. Weigh market behavior into that probability instead of cancelling picks (each is a small, capped nudge):
   - a prediction market (Kalshi) price pulls us 30% of the way toward its number
   - sharp money on the other side (its money % well above its bets %) lowers it; sharp money on ours raises it
   - the public piled on our side (85%+ of bets) with no line move for them (bait) lowers it
   - the line moving toward / away from our side since it opened raises / lowers it
   - steam (3+ books moving together) toward / away from our side raises / lowers it
3. Clear edge? The adjusted probability against the book's no-vig price and the EV at the actual price. EV under
   MIN_EV or edge under MIN_EDGE -> STAY AWAY. Against a heavy public side with sharp money on ours -> FADE; else BET.
shortcut: the size of each nudge is a reasonable default, not backtested; the backtest found no proven edge, so
every verdict carries NO_EDGE_WARNING until graded picks say otherwise. Game lines already carry the splits and
line-move effect in their fitted probability (game_factors.py), so they pass those inputs as None here.
"""
from betbot.odds import expected_value, no_vig

MIN_EV = 0.02        # +2% expected return per $1
MIN_EDGE = 0.03      # 3 points of probability over the book's no-vig price
SHARP_GAP = 10       # money % minus bets % on one side that marks sharp money
PUBLIC_HEAVY = 70    # % of bets that marks a heavy public side
BAIT = 85            # % of bets on our side with no line move for them
SHARP_MARKET_GAP = 0.03  # prediction market this far below the book's no-vig price counts as disagreeing (conviction)
STEAM_BOOKS, STEAM_MOVE = 3, 0.5  # books moving the same way, and points, that make a line move "steam"
# nudges to the probability (0.01 = one point)
MARKET_BLEND = 0.30       # share of the way toward a prediction market's probability
SHARP_NUDGE, BAIT_NUDGE = 0.02, 0.02
MOVE_PER_POINT, MOVE_CAP = 0.01, 0.03
STEAM_PER_POINT, STEAM_CAP = 0.01, 0.02
NO_EDGE_WARNING = ("Backtest found no proven edge (game lines at coin-flip accuracy; props break-even on real "
                   "lines): research only, not a likely winner.")


def _clip(x, cap):
    return max(-cap, min(cap, x))


def decide(model_prob, price, other_price=None, bets_pct=None, money_pct=None, line_moved_for_us=None,
           market_prob=None, steam=None):
    """model_prob: our probability this side wins. bets_pct / money_pct: % on OUR side (the other side is the
    rest). line_moved_for_us: points (or prop-line moves in point-equivalents) the line moved toward our side since
    it opened; negative = against us. market_prob: a prediction market's probability for our side. steam: from
    steam.game_steam. Returns a dict with verdict, prob (after the nudges), ev, edge, fair (book no-vig
    probability), adjustments and reasons."""
    fair, _ = no_vig(price, other_price)
    p = model_prob
    adjustments = []                # (probability change, why)

    def nudge(delta, why):
        nonlocal p
        if abs(delta) >= 0.0005:
            adjustments.append((round(delta, 4), why))
            p += delta

    if market_prob is not None:
        nudge(MARKET_BLEND * (market_prob - p), f"prediction market at {market_prob:.1%}")
    sharp_us = sharp_them = public_on_them = False
    if bets_pct is not None and money_pct is not None:
        sharp_us = money_pct - bets_pct >= SHARP_GAP
        sharp_them = (100 - money_pct) - (100 - bets_pct) >= SHARP_GAP
        public_on_them = 100 - bets_pct >= PUBLIC_HEAVY
        if sharp_them:
            nudge(-SHARP_NUDGE, f"sharp money on the other side ({100 - money_pct:.0f}% of money vs."
                                f" {100 - bets_pct:.0f}% of bets)")
        elif sharp_us:
            nudge(SHARP_NUDGE / 2, f"sharp money on our side ({money_pct:.0f}% of money vs. {bets_pct:.0f}% of bets)")
    if bets_pct is not None and bets_pct >= BAIT and line_moved_for_us is not None and line_moved_for_us <= 0:
        nudge(-BAIT_NUDGE, f"bait pattern: {bets_pct:.0f}% of bets on our side but the line hasn't moved for them")
    elif line_moved_for_us:
        nudge(_clip(MOVE_PER_POINT * line_moved_for_us, MOVE_CAP),
              f"line moved {'toward' if line_moved_for_us > 0 else 'against'} us since it opened ({line_moved_for_us:+g})")
    if steam and steam["books"] >= STEAM_BOOKS and abs(steam["move"]) >= STEAM_MOVE:
        nudge(_clip(STEAM_PER_POINT * steam["move"], STEAM_CAP),
              f"steam {'with' if steam['move'] > 0 else 'against'} us: {steam['books']} books moved"
              f" {abs(steam['move']):g} point(s), {steam['first_book']} first")
    p = min(0.99, max(0.01, p))
    ev = expected_value(p, price)
    edge = p - fair
    out = {"verdict": "STAY AWAY", "prob": round(p, 4), "model_prob": round(model_prob, 4), "ev": round(ev, 4),
           "edge": round(edge, 4), "fair": round(fair, 4), "reasons": [why for _, why in adjustments],
           "adjustments": adjustments, "warning": NO_EDGE_WARNING}
    if ev < MIN_EV or edge < MIN_EDGE:
        out["reasons"].insert(0, f"no clear edge (avg return {ev:+.1%}, edge {edge:+.1%} vs. book's no-vig {fair:.1%})")
        return out
    out["reasons"].insert(0, f"avg return {ev:+.1%}, edge {edge:+.1%} over the book's no-vig {fair:.1%}")
    if public_on_them and sharp_us:
        out["verdict"] = "FADE"
        out["reasons"].append(f"fading the public ({100 - bets_pct:.0f}% of bets on the other side)")
    else:
        out["verdict"] = "BET"
    return out
