"""NFL parlays from this week's board, with same-game correlation measured from history (betbot/nfl/correlation.py)
and true combined probability and EV (betbot/parlay.py). Two modes:

  default: the BET / FADE picks (the side the model favors), ranked by EV.
  hunt:    high-payout mispricings only: either side of any prop or game line, priced +150 to +400, where our
           probability (halfway back to the book's, see SHRINK) beats its no-vig probability by at least min_conv and the
           side still passes decision.py's market checks. Ranked by combined conviction, then payout.
"""
import argparse
import json
import os
import sys
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
    fair = d["fair"]
    prob = fair + SHRINK * (d["prob"] - fair)    # d["prob"]: our probability after the market-behavior nudges
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


def best_legs(picks):
    """The side the model favors on every pick, positive EV or not, for the 'best available' view. Market behavior
    is already weighed into each leg's probability."""
    return [_leg(p, p) for p in picks if p["price"] is not None]


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
    """Both sides of one bet, or two bets that can't both be the play (any spread with a moneyline of the same game)."""
    if a["game"] != b["game"]:
        return False
    if a["kind"] == "prop" and b["kind"] == "prop":
        return a["player_id"] == b["player_id"] and a["market"] == b["market"]
    if a["kind"] == "game" and b["kind"] == "game":
        if a["market"] == b["market"]:
            return True
        return {a["market"], b["market"]} == {"spread", "moneyline"}   # a spread and a moneyline of one game can't be bet together
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


def best_available(conn, picks, max_legs=2, top=5, stake=100.0, profit=None):
    """When nothing is +EV: the least-bad parlays of exactly max_legs legs (EV may be negative). With a target
    profit, only parlays that pay at least that much on `stake`. Not recommendations."""
    available = sorted(best_legs(picks), key=lambda leg: -leg["ev"])
    stats = sorted({leg["stat"] for leg in available if leg["kind"] == "prop"})
    rho = correlation.rho(correlation.cached(conn, stats))
    results = parlay.build(available, rho, conflicts, max_legs=max_legs, top=10_000, pool=16 if profit else 12,
                           min_ev=-1.0, min_legs=max_legs)
    if profit:
        results = [r for r in results if (r["decimal"] - 1) * stake >= profit]
    return available, results[:top]


def print_best_available(available, results, top=5, stake=100.0, legs=False):
    """The best parlays. 'Avg return' is what a bet like this pays back on average, per dollar bet, after wins and
    losses: below 0% means you lose money over many bets."""
    print(decision.NO_EDGE_WARNING)
    print("\nBEST PARLAY" + ("S" if len(results) > 1 else ""))
    if legs:
        for leg in available[:top]:
            print(f"  {leg['label']} ({leg['price']:+d}): ours {leg['prob']:.1%}, avg return {leg['ev']:+.1%}")
    if not results:
        print("\n  No parlay meets that.")
    for r in results:
        print(f"\n  {len(r['legs'])} legs at {r['price']:+d}: ${stake:,.0f} wins ${(r['decimal'] - 1) * stake:,.2f},"
              f" chance it hits {r['true_prob']:.1%}, avg return {r['ev']:+.1%}"
              f" (about ${r['ev'] * stake:+,.2f} per ${stake:,.0f} bet)")
        for leg in r["legs"]:
            print(f"      {leg['label']} ({leg['price']:+d}, ours {leg['prob']:.1%})")


def ask(prompt, default, kind=float):
    """Prompt for a number; Enter keeps the default (None = no limit)."""
    while True:
        text = input(f"{prompt} [{'none' if default is None else default}]: ").strip()
        if not text:
            return default
        try:
            value = kind(text)
            if value > 0:
                return value
        except ValueError:
            pass
        print("  enter a positive number")


def log(conn, results):
    """Saves each parlay shown (once per set of legs) so its result can be checked against its predicted chance."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [{"made_at": now, "true_prob": r["true_prob"], "price": r["price"],
             "legs": json.dumps(sorted(([l["game"], l.get("player_id"), l["market"], l["side"], l.get("line")]
                                        for l in r["legs"]), key=repr))} for r in results]
    with conn:
        return db.save_parlays(conn, rows)


def _leg_result(conn, game, player, market, side, line):
    """win / loss / push / void for one parlay leg from the logged pick on that prop or game market. The logged pick
    is the first one made, so a leg on the other side or line is graded from the actual stat when there is one."""
    row = conn.execute("SELECT side, line, result, actual FROM predictions WHERE game_id = ? AND player_id IS ?"
                       " AND market = ? AND result IS NOT NULL LIMIT 1", (game, player, market)).fetchone()
    if row is None:
        return None
    if (row[0], row[1]) == (side, line):
        return row[2]
    if player is None or line is None or row[3] is None:
        return None     # a game leg on another side or line, or a prop without its stat: can't say
    if row[3] == line:
        return "push"
    return "win" if (row[3] > line) == (side == "Over") else "loss"


def record(conn):
    """Settled parlays vs what we predicted: (settled, won, average predicted chance). A parlay loses when any leg
    lost and wins when every leg won or pushed (at least one won); it stays open while a leg is ungraded, unverified
    or can't be found."""
    settled = won = 0
    chance = 0.0
    for legs, prob in conn.execute("SELECT legs, true_prob FROM parlays").fetchall():
        results = [_leg_result(conn, *leg) for leg in json.loads(legs)]
        if "loss" in results or ("win" in results and all(r in ("win", "push", "void") for r in results)):
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
        print(f"{len(available)} legs with a positive average return")
    if not results:
        print("No parlays with a positive average return.")
    for r in results:
        print(f"\n{len(r['legs'])} legs at {r['price']:+d} (${(r['decimal'] - 1) * 100:,.0f} profit per $100):"
              f" true chance {r['true_prob']:.1%} (independent would be {r['independent_prob']:.1%}),"
              f" conviction {r['conviction']:+.1%}, avg return {r['ev']:+.1%}"
              + (" | same-game: books usually pay less than these multiplied odds" if r["same_game"] else ""))
        for leg in r["legs"]:
            print(f"    {leg['label']} ({leg['price']:+d}, ours {leg['prob']:.1%}, edge {leg['edge']:+.1%})")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hunt", action="store_true", help="plus-money, high-edge legs only")
    ap.add_argument("--min-price", type=int, default=MIN_PRICE)
    ap.add_argument("--max-price", type=int, default=MAX_PRICE)
    ap.add_argument("--min-conv", type=float, default=MIN_CONV, help="minimum edge over the no-vig price after SHRINK halves the model's edge, e.g. 0.10")
    ap.add_argument("--legs", type=int, default=None, help="most legs per parlay (fewer legs hit more often); --best builds exactly this many")
    ap.add_argument("--stake", type=float, default=None, help="--best: your bet amount in dollars")
    ap.add_argument("--profit", type=float, default=None, help="--best: only parlays that win at least this much on the stake")
    ap.add_argument("--best", action="store_true", help="show the best parlays even if the average return is negative")
    ap.add_argument("--record", action="store_true", help="show settled parlays vs predicted chance, then exit")
    args = ap.parse_args(argv)
    conn = db.connect(os.getenv("DB_PATH", "betbot.db"))
    if args.record:
        return print_record(conn)
    if args.best:
        if sys.stdin.isatty():  # ask for whatever wasn't given as a flag
            args.legs = args.legs or ask("Legs", 3, int)
            args.stake = args.stake or ask("Bet amount ($)", 100.0)
            args.profit = args.profit or ask("Target profit ($, Enter for none)", None)
        legs, stake = args.legs or 2, args.stake or 100.0
        available, results = best_available(conn, board.build(conn), max_legs=legs, stake=stake, profit=args.profit)
        print_best_available(available, results, stake=stake)
        log(conn, results)
        return print_record(conn)
    args.legs = args.legs or 2
    results, available = build(conn, max_legs=args.legs, hunt=args.hunt, min_price=args.min_price,
                               max_price=args.max_price, min_conv=args.min_conv)
    print_parlays(results, available, args.hunt, args.min_price, args.max_price, args.min_conv)
    log(conn, results)
    print_record(conn)


if __name__ == "__main__":
    main()
