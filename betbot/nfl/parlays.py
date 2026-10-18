"""NFL parlays from this week's board: positive-EV legs only (BET / FADE), same-game correlation measured from
history (betbot/nfl/correlation.py), true combined probability and EV (betbot/parlay.py)."""
import os

from betbot import db, decision, parlay
from betbot.nfl import board, correlation
from betbot.nfl.signals.regression import MARKETS


def legs(picks):
    out = []
    for p in picks:
        d = p["decision"]
        if d["verdict"] not in ("BET", "FADE") or d["ev"] <= 0:
            continue
        leg = {"label": p["label"], "prob": p["model_prob"], "price": p["price"], "ev": d["ev"],
               "game": p["game_id"], "side": p["side"], "market": p["market"]}
        if p.get("kind") == "game":
            away, home = p["game_id"].split("_")[2:4]
            leg.update(kind="game", home=home, away=away)
        else:
            leg.update(kind="prop", stat=MARKETS[p["market"]][0], player_id=p["player_id"], team=p["team"])
        out.append(leg)
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


def build(conn, picks=None, max_legs=3, top=10):
    picks = board.build(conn) if picks is None else picks
    available = legs(picks)
    stats = sorted({leg["stat"] for leg in available if leg["kind"] == "prop"})
    rho = correlation.rho(correlation.cached(conn, stats))
    return parlay.build(available, rho, conflicts, max_legs=max_legs, top=top), available


def print_parlays(results, available):
    print(decision.NO_EDGE_WARNING)
    print(f"{len(available)} positive-EV legs available")
    if not results:
        print("No positive-EV parlays.")
    for r in results:
        print(f"\n{len(r['legs'])} legs at {r['price']:+d}: true chance {r['true_prob']:.1%}"
              f" (independent would be {r['independent_prob']:.1%}), EV {r['ev']:+.1%}"
              + (" | same-game: books usually pay less than these multiplied odds" if r["same_game"] else ""))
        for leg in r["legs"]:
            print(f"    {leg['label']} ({leg['price']:+d}, ours {leg['prob']:.1%})")


if __name__ == "__main__":
    print_parlays(*build(db.connect(os.getenv("DB_PATH", "betbot.db"))))
