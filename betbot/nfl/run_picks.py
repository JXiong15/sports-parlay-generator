"""One command for today's picks: `.venv/bin/python -m betbot.nfl.run_picks`.
Loads every free source once, pulls fresh game lines and the main prop markets from the Odds API (only if the saved
odds are 2+ hours old; a re-run inside that window uses the saved data and costs 0 credits), then prints the model's
full board (every prop and game line, by confidence) and the best parlay. Credits per fresh pull: 3 + one per game
per market (about 63 for 12 games). `NO_PAID_REQUESTS=1` = free sources and saved odds only."""
import contextlib
import io
import os
from datetime import datetime, timedelta, timezone

from betbot import db, decision, main
from betbot.nfl import board, grader, parlays

SHRINK = 0.5        # confidence = book's fair + this share of the model's edge (the backtest shows props barely beat it)
MAX_EDGE = 0.05     # ...and never more than 5 points above the book
PER_GROUP = 3       # most rows per market and side, so one model lean (e.g. Under rush yds) can't flood the table
MIN_YARDS_LINE = 5  # a yardage line below this is a fringe player who may not play
TABLE_ROWS = 15
VERDICTS = {"BET": "Bet", "FADE": "Fade", "STAY AWAY": "Pass"}  # Pass = no clear edge on the adjusted odds
MARKETS = ["player_pass_yds", "player_rush_yds", "player_receptions", "player_reception_yds", "player_anytime_td"]
FRESH_HOURS = 2


def saved_odds_age_hours(conn):
    """Age of the oldest market's newest pull (None if any market was never pulled), from the pull log: the lines table
    only keeps changed prices, so its newest row can be older than the last pull."""
    newest = [t for (t,) in conn.execute(f"SELECT max(fetched_at) FROM odds_pulls WHERE kind IN ({','.join('?' * len(MARKETS))})"
                                         " GROUP BY kind", MARKETS)]
    if len(newest) < len(MARKETS):
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(min(newest))).total_seconds() / 3600


def own_flags(p):
    """The player's own injury flags; teammate and opponent flags also contain 'Out' or 'Questionable'."""
    return [f for f in p.get("flags", []) if f.startswith(p.get("player", "\0") + " is ")]


def confidence(p):
    """My honest number: a share of the model's edge over the book, less for an injury flag, capped."""
    d = p["decision"]
    c = d["fair"] + min(SHRINK * (d["prob"] - d["fair"]), MAX_EDGE)
    return c - (0.03 if any(f.endswith(("Questionable", "Doubtful")) for f in own_flags(p)) else 0)


def usable(p, today):
    d = p["decision"]
    if p.get("kind") != "game":
        if p["market"].endswith("yds") and p["line"] < MIN_YARDS_LINE:
            return False
        if any(f.endswith(("Out", "Injured Reserve", "Suspension")) for f in own_flags(p)):
            return False
    if p.get("market_prob") is not None and p["market_prob"] < d["fair"] - 0.02:
        return False  # the prediction market is against us
    return d["ev"] > 0 and p.get("event_id") in today


def row(i, p):
    d, kind = p["decision"], p.get("kind")
    split = "-" if p.get("bets_pct") is None else f"{p['bets_pct']:.0f}% / " + (
        "?" if p.get("money_pct") is None else f"{p['money_pct']:.0f}%")
    if "median" not in p:  # game lines and markets without a stat history
        evidence = f"model {d['model_prob']:.0%}" + (f", line moved {p['line_move']:+g}" if p.get("line_move") is not None else "")
    else:
        evidence = f"median {p['median']:g}, mean {p['mean']:.1f}, last game {p['last_game']:g}, {p['games']} games"
    why = "; ".join(r for r in d["reasons"] if not r.startswith("avg return"))
    risk = [f.replace(p.get("player", ""), "").strip() for f in p.get("flags", [])][:2]
    if p.get("market_prob") is not None:
        risk.append(f"Kalshi {p['market_prob']:.0%}")
    if (p.get("bets_pct") or 0) >= 70:
        risk.append("public side")
    return (f"| {i} | {p['label']} | {p['price']:+d} | {confidence(p):.0%} | {split} | {evidence} | "
            f"{why or 'model edge only'} | {'; '.join(risk) or 'model-only edge'} | {VERDICTS[d['verdict']]} |")


def show(conn):
    """Grade finished picks, then print the table of usable picks sorted by confidence, then the best parlay of them."""
    grader.grade(conn)
    picks = board.build(conn)
    board.log(conn, picks)
    horizon = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    today = {e for (e,) in conn.execute("SELECT id FROM events WHERE commence_time > ? AND commence_time < ?",
                                        (datetime.now(timezone.utc).isoformat(), horizon))}
    chosen, seen = [], {}
    for p in sorted((p for p in picks if usable(p, today)), key=lambda p: -confidence(p)):
        group = (p.get("market"), p.get("side"))
        seen[group] = seen.get(group, 0) + 1
        if seen[group] <= PER_GROUP:
            chosen.append(p)
    print(decision.NO_EDGE_WARNING)
    print(f"\n{len(chosen)} usable picks of {len(picks)} (kicking off in the next 24h; fringe lines, injured-out players and "
          f"picks Kalshi disagrees with are dropped; max {PER_GROUP} per market and side). Confidence is shrunk toward the "
          "book's price. Unvetted for roles and game script: check those before betting.\n")
    print("| # | Pick | Odds | Conf | Bets / money | Evidence | Why | Risk | Verdict |\n|---|---|---|---|---|---|---|---|---|")
    for i, p in enumerate(chosen[:TABLE_ROWS], 1):
        print(row(i, p))
    try:
        available, results = parlays.best_available(conn, chosen, max_legs=int(os.getenv("PARLAY_LEGS", "3")), top=1,
                                                    stake=float(os.getenv("PARLAY_STAKE", "100")))
        parlays.print_best_available(available, results, stake=float(os.getenv("PARLAY_STAKE", "100")))
    except Exception as e:
        print(f"parlay failed: {e.__class__.__name__}: {e}")


def run():
    conn = db.connect(os.getenv("DB_PATH", "betbot.db"))
    age = saved_odds_age_hours(conn)
    if age is None or age >= FRESH_HOURS:
        os.environ.setdefault("FORCE_ODDS_PULL", "1")  # stale: refresh even if this week's game lines were pulled
    else:
        print(f"saved odds are {age:.1f}h old (under {FRESH_HOURS}h): using them, 0 credits")
    for i, market in enumerate(MARKETS):
        os.environ["MARKET"] = market
        os.environ["PARLAY_ONLY"] = "0"  # full board
        os.environ["SKIP_REPORT"] = "1"  # the table below is the report
        if i:
            os.environ["SKIP_FREE_SOURCES"] = "1"
        out = io.StringIO()
        with contextlib.redirect_stdout(out) if i else contextlib.nullcontext():  # the first run shows the free sources
            try:
                main.collect()
            except (Exception, SystemExit) as e:  # keep the markets already saved, still show the table
                print(f"{market} failed: {e.__class__.__name__}: {e}")
        for line in main.problems(out.getvalue()):
            if not line.startswith(("credits used", "game odds:")):
                print(f"{market}: {line}")
        if i:
            credits = [l for l in out.getvalue().splitlines() if l.startswith("credits used")]
            print(f"{market}: {credits[0] if credits else 'no credit line'}")
    show(conn)


if __name__ == "__main__":
    run()
