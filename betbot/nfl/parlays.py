"""NFL parlays from this week's board, with same-game correlation measured from history (betbot/nfl/correlation.py)
and true combined probability and EV (betbot/parlay.py). Two modes:

  default: the BET / FADE picks (the side the model favors), ranked by EV.
  hunt:    high-payout mispricings only: either side of any prop or game line, priced +150 to +400, where our
           probability beats the book's no-vig probability by at least min_conv (e.g. 0.12 = 12 points) and the
           side still passes decision.py's market checks. Ranked by combined conviction, then payout.
"""
import argparse
import json
import os
from datetime import datetime, timezone

from betbot import db, decision, parlay
from betbot.nfl import board, correlation
from betbot.odds import decimal_odds
from betbot.nfl.signals.regression import MARKETS

MIN_PRICE, MAX_PRICE, MIN_CONV = 150, 400, 0.12  # hunt defaults: +150 to +400, a 12-point edge
SHRINK = 0.5   # parlay legs trust the model only this far from the book's no-vig price (it has been overconfident)


def _leg(p, s):
    """A parlay leg from board pick p and one of its sides s."""
    d = s["decision"]
    fair = s["model_prob"] - d["edge"]
    prob = fair + SHRINK * d["edge"]
    leg = {"label": s["label"], "prob": prob, "price": s["price"], "ev": prob * decimal_odds(s["price"]) - 1,
           "edge": prob - fair, "game": p["game_id"], "side": s["side"], "market": p["market"],
           "line": p.get("line")}
    if p.get("kind") == "game":
        away, home = p["game_id"].split("_")[2:4]
        leg.update(kind="game", home=home, away=away)
    else:
        leg.update(kind="prop", stat=MARKETS[p["market"]][0], player_id=p["player_id"], team=p["team"])
    return leg


def legs(picks):
    """The board's positive-EV BET / FADE picks (the side the model favors) as parlay legs."""
    out = [_leg(p, p) for p in picks if p["decision"]["verdict"] in ("BET", "FADE")]
    return [leg for leg in out if leg["ev"] > 0]


def hunt_legs(picks, min_price=MIN_PRICE, max_price=MAX_PRICE, min_conv=MIN_CONV):
    """Either side of every bet on the board, priced min_price..max_price (plus money), with an edge over the
    book's no-vig probability of at least min_conv, that decision.py would still BET or FADE."""
    out = []
    for p in picks:
        for s in p.get("sides", [p]):
            d = s.get("decision")
            if d and s["price"] is not None and min_price <= s["price"] <= max_price and d["verdict"] in ("BET", "FADE"):
                out.append(_leg(p, s))
    return [leg for leg in out if leg["edge"] >= min_conv]


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


def log(conn, results):
    """Saves each parlay shown (once per set of legs) so its result can be checked against its predicted chance."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [{"made_at": now, "true_prob": r["true_prob"], "price": r["price"],
             "legs": json.dumps(sorted([l["game"], l.get("player_id"), l["market"], l["side"], l.get("line")]
                                       for l in r["legs"]), sort_keys=True)} for r in results]
    with conn:
        return db.save_parlays(conn, rows)


def record(conn):
    """Settled parlays vs what we predicted: (settled, won, average predicted chance). A parlay loses when any leg
    lost, wins when every leg won, and stays open while a leg is ungraded or can't be found (pushes void a leg)."""
    settled = won = 0
    chance = 0.0
    for legs, prob in conn.execute("SELECT legs, true_prob FROM parlays"):
        results = []
        for game, player, market, side, line in json.loads(legs):
            row = conn.execute("SELECT result FROM predictions WHERE game_id = ? AND player_id IS ? AND market = ?"
                               " AND side = ? AND line IS ? AND result IS NOT NULL LIMIT 1",
                               (game, player, market, side, line)).fetchone()
            results.append(row[0] if row else None)
        if "loss" in results or (None not in results and "win" in results):
            settled += 1
            won += "loss" not in results
            chance += prob
    return settled, won, chance / settled if settled else None


def print_record(conn):
    settled, won, chance = record(conn)
    if settled:
        print(f"Parlays shown and settled: {won}/{settled} won; we predicted {chance:.0%} on average")
    else:
        print("No parlays settled yet.")


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
    ap.add_argument("--legs", type=int, default=2, help="most legs per parlay (fewer legs hit more often)")
    ap.add_argument("--record", action="store_true", help="show settled parlays vs predicted chance, then exit")
    args = ap.parse_args(argv)
    conn = db.connect(os.getenv("DB_PATH", "betbot.db"))
    if args.record:
        return print_record(conn)
    results, available = build(conn, max_legs=args.legs, hunt=args.hunt, min_price=args.min_price,
                               max_price=args.max_price, min_conv=args.min_conv)
    print_parlays(results, available, args.hunt, args.min_price, args.max_price, args.min_conv)
    log(conn, results)
    print_record(conn)


if __name__ == "__main__":
    main()
