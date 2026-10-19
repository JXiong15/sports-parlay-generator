"""NFL parlays from this week's board, with same-game correlation measured from history (betbot/nfl/correlation.py)
and true combined probability and EV (betbot/parlay.py). Two modes:

  default: the BET / FADE picks (the side the model favors), ranked by EV.
  hunt:    high-payout mispricings only: either side of any prop or game line, priced +150 to +400, where our
           probability beats the book's no-vig probability by at least min_conv (e.g. 0.12 = 12 points) and the
           side still passes decision.py's market checks. Ranked by combined conviction, then payout.
"""
import argparse
import os

from betbot import db, decision, parlay
from betbot.nfl import board, correlation
from betbot.nfl.signals.regression import MARKETS

MIN_PRICE, MAX_PRICE, MIN_CONV = 150, 400, 0.12  # hunt defaults: +150 to +400, a 12-point edge


def _leg(p, s):
    """A parlay leg from board pick p and one of its sides s."""
    d = s["decision"]
    leg = {"label": s["label"], "prob": s["model_prob"], "price": s["price"], "ev": d["ev"], "edge": d["edge"],
           "game": p["game_id"], "side": s["side"], "market": p["market"]}
    if p.get("kind") == "game":
        away, home = p["game_id"].split("_")[2:4]
        leg.update(kind="game", home=home, away=away)
    else:
        leg.update(kind="prop", stat=MARKETS[p["market"]][0], player_id=p["player_id"], team=p["team"])
    return leg


def legs(picks):
    """The board's positive-EV BET / FADE picks (the side the model favors) as parlay legs."""
    return [_leg(p, p) for p in picks if p["decision"]["verdict"] in ("BET", "FADE") and p["decision"]["ev"] > 0]


def hunt_legs(picks, min_price=MIN_PRICE, max_price=MAX_PRICE, min_conv=MIN_CONV):
    """Either side of every bet on the board, priced min_price..max_price (plus money), with an edge over the
    book's no-vig probability of at least min_conv, that decision.py would still BET or FADE."""
    out = []
    for p in picks:
        for s in p.get("sides", [p]):
            d = s.get("decision")
            if (d and s["price"] is not None and min_price <= s["price"] <= max_price and d["edge"] >= min_conv
                    and d["verdict"] in ("BET", "FADE")):
                out.append(_leg(p, s))
    return out


def conflicts(a, b):
    """Both sides of one bet, or two bets that can't both be the play (e.g. one team's spread, the other's ML)."""
    if a["game"] != b["game"]:
        return False
    if a["kind"] == "prop" and b["kind"] == "prop":
        return a["player_id"] == b["player_id"] and a["market"] == b["market"]
    if a["kind"] == "game" and b["kind"] == "game":
        if a["market"] == b["market"]:
            return True
        return {a["market"], b["market"]} == {"spread", "moneyline"} and a["side"] != b["side"]
    return False


def build(conn, picks=None, max_legs=3, top=10, hunt=False, min_price=MIN_PRICE, max_price=MAX_PRICE,
          min_conv=MIN_CONV):
    picks = board.build(conn) if picks is None else picks
    available = hunt_legs(picks, min_price, max_price, min_conv) if hunt else legs(picks)
    stats = sorted({leg["stat"] for leg in available if leg["kind"] == "prop"})
    rho = correlation.rho(correlation.cached(conn, stats))
    results = parlay.build(available, rho, conflicts, max_legs=max_legs, top=top,
                           rank="conviction" if hunt else "ev")
    return results, available


def print_parlays(results, available, hunt=False, min_price=MIN_PRICE, max_price=MAX_PRICE, min_conv=MIN_CONV):
    print(decision.NO_EDGE_WARNING)
    if hunt:
        print(f"Hunt: legs priced +{min_price} to +{max_price} with an edge of {min_conv:.0%}+ over the book's"
              f" no-vig price: {len(available)} found")
    else:
        print(f"{len(available)} positive-EV legs available")
    if not results:
        print("No positive-EV parlays.")
    for r in results:
        print(f"\n{len(r['legs'])} legs at {r['price']:+d} (${(r['decimal'] - 1) * 100:,.0f} profit per $100):"
              f" true chance {r['true_prob']:.1%} (independent would be {r['independent_prob']:.1%}),"
              f" conviction {r['conviction']:+.1%}, EV {r['ev']:+.1%}"
              + (" | same-game: books usually pay less than these multiplied odds" if r["same_game"] else ""))
        for leg in r["legs"]:
            print(f"    {leg['label']} ({leg['price']:+d}, ours {leg['prob']:.1%}, edge {leg['edge']:+.1%})")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hunt", action="store_true", help="plus-money, high-edge legs only")
    ap.add_argument("--min-price", type=int, default=MIN_PRICE)
    ap.add_argument("--max-price", type=int, default=MAX_PRICE)
    ap.add_argument("--min-conv", type=float, default=MIN_CONV, help="minimum edge over the no-vig price, e.g. 0.15")
    ap.add_argument("--legs", type=int, default=3, help="most legs per parlay")
    args = ap.parse_args(argv)
    conn = db.connect(os.getenv("DB_PATH", "betbot.db"))
    results, available = build(conn, max_legs=args.legs, hunt=args.hunt, min_price=args.min_price,
                               max_price=args.max_price, min_conv=args.min_conv)
    print_parlays(results, available, args.hunt, args.min_price, args.max_price, args.min_conv)


if __name__ == "__main__":
    main()
