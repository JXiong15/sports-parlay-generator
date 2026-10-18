"""This week's NFL board: every prop and game pick with our probability, the book's prices, the betting splits
and line movement we collect, and a BET / FADE / STAY AWAY verdict from betbot/decision.py."""
import os
from datetime import datetime, timezone
from statistics import multimode

from betbot import db, decision, predictions
from betbot.names import normalize
from betbot.nfl import game_model, model
from betbot.nfl.backtest import REAL_LINE_CALIBRATION
from betbot.nfl.signals import regression
from betbot.nfl.teams import TEAMS

LINE_BOOK = regression.LINE_BOOK
GAME_MARKETS = {"spreads": "spread", "totals": "total", "h2h": "moneyline"}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- props ----------

def _prop_splits(conn, player, market, line):
    """This week's DraftKings ticket/handle % for this prop (newest article within 7 days), or None. Names are
    matched loosely ('Kenneth Walker III' = 'Kenneth Walker'), as the Odds API and DraftKings spell them differently."""
    key = normalize(player)
    for name, bets, handle in conn.execute(
            "SELECT p.player, p.over_bets, p.over_handle FROM prop_splits p JOIN dk_articles a ON a.id = p.article_id"
            " WHERE p.market = ? AND p.line = ? AND p.period IS NULL AND a.published >= date('now', '-7 days')"
            " ORDER BY a.published DESC", (market, line)):
        if normalize(name) == key:
            return bets, handle
    return None


def _prop_first_line(conn, r):
    row = conn.execute("SELECT line FROM lines WHERE event_id = ? AND market = ? AND player = ? AND book = ?"
                       " AND side = 'Over' ORDER BY fetched_at, id LIMIT 1",
                       (r["event_id"], r["market"], r["player"], r["book"])).fetchone()
    return row[0] if row else None


def prop_picks(conn, now=None):
    """regression.evaluate for every market with lines, probabilities recalibrated on real sportsbook lines."""
    now = now or _now()
    real = model.load_params(conn, REAL_LINE_CALIBRATION)
    markets = [m for (m,) in conn.execute("SELECT DISTINCT market FROM lines") if m in regression.MARKETS]
    shared = {"context": model.game_context(conn), "flags": model.injury_flags(conn)}  # same for every market
    out = []
    for market in markets:
        results, _ = regression.evaluate(conn, market, now, shared)
        for r in results:
            if r.get("p_over") is None:
                continue
            r["p_over_model"] = r["p_over"]
            if real:
                r["p_over"] = round(float(model.recalibrate(r["p_over"], real["calib"])), 3)
            pick = predictions.prop_pick(r)
            if pick is None:
                continue
            over = pick["side"] == "Over"
            splits = _prop_splits(conn, r["player"], market, r["line"])
            bets = money = None
            if splits and splits[0] is not None:
                bets = splits[0] if over else 100 - splits[0]
                money = (splits[1] if over else 100 - splits[1]) if splits[1] is not None else None
            first = _prop_first_line(conn, r)
            # a rising line means the market likes the Over
            moved = None if first is None else (r["line"] - first) * (1 if over else -1)
            d = decision.decide(pick["model_prob"], pick["price"], pick["other_price"], bets, money, moved)
            out.append(dict(r, **pick, decision=d, bets_pct=bets, money_pct=money, line_move=moved,
                            label=f"{r['player']} {pick['side']} {r['line']} {market.replace('player_', '')}"))
    return out


# ---------- games ----------

def _game_calibrations(g):
    """Recalibration for spread / total / moneyline probabilities, from every finished game's raw probabilities."""
    done = g[g["home_score"].notna() & g["spread_line"].notna() & g["total_line"].notna()]
    margin, total = done["home_score"] - done["away_score"], done["home_score"] + done["away_score"]
    s = done[margin - done["spread_line"] != 0]
    spread = model.fit_recalibration([game_model.p_home_covers(m, -l, sd) for m, l, sd in
                                      zip(s["pred_margin"], s["spread_line"], s["sigma_margin"])],
                                     (margin - done["spread_line"] > 0)[s.index].astype(int))
    t = done[total != done["total_line"]]
    tot = model.fit_recalibration([game_model.p_over(m, l, sd) for m, l, sd in
                                   zip(t["pred_total"], t["total_line"], t["sigma_total"])],
                                  (total > done["total_line"])[t.index].astype(int))
    return {"spread": spread, "total": tot}


def _game_offers(conn, event_id):
    """{market: {side_name: (line, price)}} at DraftKings (else the most common spread/total), latest prices."""
    rows = conn.execute(
        "SELECT o.market, o.side, o.line, o.price, o.book FROM game_odds o WHERE o.event_id = ?"
        " AND o.fetched_at = (SELECT max(fetched_at) FROM game_odds x WHERE x.event_id = o.event_id"
        "  AND x.market = o.market AND x.side = o.side AND x.book = o.book)", (event_id,)).fetchall()
    by = {}
    for market, side, line, price, book in rows:
        by.setdefault(market, {}).setdefault(book, {})[side] = (line, price)
    out = {}
    for market, books in by.items():
        if LINE_BOOK in books:
            out[market] = (LINE_BOOK, books[LINE_BOOK])
        else:
            common = multimode(tuple(sorted(v[0] or 0 for v in sides.values())) for sides in books.values())[0]
            book = sorted(b for b, sides in books.items() if tuple(sorted(v[0] or 0 for v in sides.values())) == common)[0]
            out[market] = (book, books[book])
    return out


def _game_splits(conn, home, market, side, kickoff):
    """Latest Action Network (bets %, money %) for this side of THIS game (kickoff within a day); money is None
    where the free page didn't show it."""
    row = conn.execute("SELECT bets_pct, money_pct FROM splits WHERE home_team = ? AND market = ? AND side = ?"
                       " AND abs(julianday(start_time) - julianday(?)) < 1 ORDER BY fetched_at DESC LIMIT 1",
                       (home, market, side, kickoff)).fetchone()
    return row if row else (None, None)


def game_picks(conn, now=None):
    now = now or _now()
    g = game_model.predict(game_model.games(conn))
    calib = _game_calibrations(g)
    by_game = g.set_index("game_id")
    events = conn.execute("SELECT id, home_team, away_team, commence_time FROM events WHERE commence_time > ?",
                          (now,)).fetchall()
    out = []
    for event_id, home, away, kickoff in events:
        h, a = TEAMS.get(home), TEAMS.get(away)
        game = regression.find_game(conn, h, a, now) if h and a else None
        if not game or game[0] not in by_game.index:
            continue
        r = by_game.loc[game[0]]
        lines = conn.execute("SELECT open_home_spread, current_home_spread, open_total, current_total"
                             " FROM nfl_game_lines WHERE game_id = ?", (game[0],)).fetchone()
        offers = _game_offers(conn, event_id)
        for odds_market, market in GAME_MARKETS.items():
            offer = offers.get(odds_market)
            if not offer:
                continue
            book, sides = offer
            if market == "total":
                if "Over" not in sides or "Under" not in sides:
                    continue
                line = sides["Over"][0]
                p_a = model.recalibrate(game_model.p_over(r["pred_total"], line, r["sigma_total"]), calib["total"])
                a_name, b_name, a_side, b_side = "Over", "Under", "over", "under"
            else:
                if home not in sides or away not in sides:
                    continue
                spread = offers.get("spreads")
                home_spread = spread[1][home][0] if spread and home in spread[1] else None
                if market == "spread":
                    line = sides[home][0]
                    p_a = model.recalibrate(game_model.p_home_covers(r["pred_margin"], line, r["sigma_margin"]),
                                            calib["spread"])
                elif home_spread is not None:
                    # moneyline from the spread view, so the two can't contradict each other
                    p_cover = model.recalibrate(game_model.p_home_covers(r["pred_margin"], home_spread, r["sigma_margin"]),
                                                calib["spread"])
                    p_a = game_model.p_home_wins_from_spread(p_cover, home_spread, r["sigma_margin"])
                else:
                    continue
                a_name, b_name, a_side, b_side = home, away, "home", "away"
            pick_a = p_a >= 0.5
            name, other, side = (a_name, b_name, a_side) if pick_a else (b_name, a_name, b_side)
            prob = float(p_a if pick_a else 1 - p_a)
            bets, money = _game_splits(conn, home, market, side, kickoff)
            moved = None
            if lines and market == "spread" and lines[0] is not None and lines[1] is not None:
                toward_home = lines[0] - lines[1]   # home spread getting more negative = market likes home
                moved = toward_home if side == "home" else -toward_home
            elif lines and market == "total" and lines[2] is not None and lines[3] is not None:
                moved = (lines[3] - lines[2]) * (1 if side == "over" else -1)
            d = decision.decide(prob, sides[name][1], sides[other][1], bets, money, moved)
            pick_line = sides[name][0] if market != "moneyline" else None
            out.append({"kind": "game", "game_id": game[0], "event_id": event_id, "market": market, "side": side,
                        "line": pick_line, "price": sides[name][1], "other_price": sides[other][1], "book": book,
                        "model_prob": round(prob, 3), "mu": float(r["pred_margin"] if market != "total" else r["pred_total"]),
                        "decision": d, "bets_pct": bets, "money_pct": money, "line_move": moved,
                        "label": f"{away} @ {home}: {name}" + (f" {pick_line:+g}" if market == "spread" else
                                                                f" {pick_line:g}" if market == "total" else " ML")})
    return out


def build(conn, now=None):
    return prop_picks(conn, now) + game_picks(conn, now)


def log(conn, picks, made_at=None):
    """Logs the recalibrated probabilities the verdicts were based on."""
    props = [p for p in picks if p.get("kind") != "game"]
    games = [p for p in picks if p.get("kind") == "game"]
    return predictions.log_props(conn, props, made_at), predictions.log_games(conn, games, made_at)


def print_board(picks):
    print(decision.NO_EDGE_WARNING)
    for verdict in ("BET", "FADE", "STAY AWAY"):
        chosen = [p for p in picks if p["decision"]["verdict"] == verdict]
        print(f"\n{verdict} ({len(chosen)})")
        for p in sorted(chosen, key=lambda p: -p["decision"]["ev"])[:40 if verdict == "STAY AWAY" else None]:
            d = p["decision"]
            print(f"  {p['label']} ({p['price']:+d} {p['book']}): ours {p['model_prob']:.1%} vs book {d['fair']:.1%},"
                  f" EV {d['ev']:+.1%} | {'; '.join(d['reasons'])}")


if __name__ == "__main__":
    conn = db.connect(os.getenv("DB_PATH", "betbot.db"))
    picks = build(conn)
    print_board(picks)
