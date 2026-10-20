"""This week's NFL board: every prop and game pick with our probability, the book's prices, the betting splits
and line movement we collect, and a BET / FADE / STAY AWAY verdict from betbot/decision.py."""
import os
import sqlite3
from datetime import datetime, timezone
from statistics import multimode

from betbot import db, decision, predictions
from betbot.collectors import odds_api
from betbot.names import normalize
from betbot.nfl import game_factors, game_model, model
from betbot.nfl.collectors import kalshi
from betbot.nfl.backtest import REAL_LINE_CALIBRATION
from betbot.nfl import steam
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


def _upcoming_team_weeks(conn, now):
    """season, week, team for both teams of every upcoming game, so missing starters count on both sides."""
    import pandas as pd
    rows = conn.execute("SELECT season, week, home_team, away_team FROM nfl_games"
                        " WHERE gameday >= date(?, '-1 day') AND home_score IS NULL", (now[:10],)).fetchall()
    return pd.DataFrame([(s, w, t) for s, w, h, a in rows for t in (h, a)], columns=["season", "week", "team"])


def prop_picks(conn, now=None, prices=None, flags=None, absences=None):
    """regression.evaluate for every market with lines, probabilities recalibrated on real sportsbook lines."""
    now = now or _now()
    real = model.load_params(conn, REAL_LINE_CALIBRATION)
    markets = [m for (m,) in conn.execute("SELECT DISTINCT market FROM lines") if m in regression.MARKETS]
    flags = model.injury_flags(conn) if flags is None else flags
    absences = model.team_absences(conn, flags, _upcoming_team_weeks(conn, now)) if absences is None else absences
    shared = {"context": model.game_context(conn), "flags": flags, "snaps": model.snap_shares(conn),
              "form": model.team_form(conn),  # built once
              "absences": absences}
    prices = prices or kalshi.Prices(conn, now)
    out = []
    for market in markets:
        results, _ = regression.evaluate(conn, market, now, shared)
        for r in results:
            if r.get("p_over") is None:
                continue
            r["p_over_model"] = r["p_over"]
            if real:
                calib = real.get("by_market", {}).get(market, real["calib"])
                r["p_over"] = round(float(model.recalibrate(r["p_over"], calib)), 3)
            splits = _prop_splits(conn, r["player"], market, r["line"])
            if real and splits and splits[0] is not None:   # hype discount: the public's side, as fitted on past props
                gap = ((splits[1] - splits[0]) / 50) if splits[1] is not None else 0.0
                r["p_over"] = round(float(model.apply_public_discount(
                    r["p_over"], (splits[0] - 50) / 50, gap, real.get("public_discount", (0.0, 0.0)))), 3)
            pick = predictions.prop_pick(r)
            if pick is None:
                continue
            first = _prop_first_line(conn, r)
            sides = []
            for side, prob, price, other in (("Over", r["p_over"], r["over_price"], r["under_price"]),
                                             ("Under", 1 - r["p_over"], r["under_price"], r["over_price"])):
                over = side == "Over"
                bets = money = None
                if splits and splits[0] is not None:
                    bets = splits[0] if over else 100 - splits[0]
                    money = (splits[1] if over else 100 - splits[1]) if splits[1] is not None else None
                # a rising line means the market likes the Over
                moved = None if first is None else (r["line"] - first) * (1 if over else -1)
                market_prob = prices.prop(r["game_id"], market, r["player"], side, r["line"])
                # a prop line moving 5% of itself counts like a game line moving one point
                move_units = None if moved is None else moved / r["line"] * 20 if r["line"] else None
                d = (decision.decide(prob, price, other, bets, money, move_units, market_prob)
                     if price is not None else None)
                sides.append({"side": side, "model_prob": prob, "price": price, "other_price": other,
                              "line": r["line"], "decision": d, "bets_pct": bets, "money_pct": money,
                              "line_move": moved, "market_prob": market_prob,
                              "label": f"{r['player']} {side} {r['line']} {market.replace('player_', '')}"})
            chosen = next(s for s in sides if s["side"] == pick["side"])
            out.append(dict(r, **pick, **{k: chosen[k] for k in ("decision", "bets_pct", "money_pct", "line_move",
                                                                 "market_prob", "label")}, sides=sides))
    return out


# ---------- games ----------

def _game_calibrations(g):
    """Recalibration for spread / total / moneyline probabilities, from every finished game's raw probabilities."""
    done = g[g["home_score"].notna() & g["spread_line"].notna() & g["total_line"].notna() & g["trust_margin"].notna()]
    margin, total = done["home_score"] - done["away_score"], done["home_score"] + done["away_score"]
    s = done[margin - done["spread_line"] != 0]
    spread = model.fit_recalibration([game_model.p_home_covers(m, -l, sd) for m, l, sd in
                                      zip(s["blend_margin"], s["spread_line"], s["blend_sigma_margin"])],
                                     (margin - done["spread_line"] > 0)[s.index].astype(int))
    t = done[total != done["total_line"]]
    tot = model.fit_recalibration([game_model.p_over(m, l, sd) for m, l, sd in
                                   zip(t["blend_total"], t["total_line"], t["blend_sigma_total"])],
                                  (total > done["total_line"])[t.index].astype(int))
    return {"spread": spread, "total": tot}


def _game_offers(conn, event_id):
    """{market: {side_name: (line, price)}} at DraftKings (else the most common spread/total), latest prices."""
    rows = conn.execute(
        "SELECT o.market, o.side, o.line, o.price, o.book FROM game_odds o WHERE o.event_id = ?"
        " AND o.fetched_at = (SELECT max(fetched_at) FROM game_odds x WHERE x.event_id = o.event_id"
        "  AND x.market = o.market AND x.side = o.side AND x.book = o.book)"
        # still listed in the latest game-odds pull (a line the book took down drops out)
        " AND coalesce(o.seen_at, o.fetched_at) >= coalesce((SELECT max(fetched_at) FROM odds_pulls"
        "  WHERE kind = ? AND event_id = ''), '')", (event_id, odds_api.game_odds_kind("americanfootball_nfl"))).fetchall()
    by = {}
    for market, side, line, price, book in rows:
        by.setdefault(market, {}).setdefault(book, {})[side] = (line, price)
    out = {}
    for market, books in by.items():
        books = {b: sides for b, sides in books.items() if len(sides) == 2}  # both sides still listed
        if not books:
            continue
        if LINE_BOOK in books:
            out[market] = (LINE_BOOK, books[LINE_BOOK])
        else:
            keys = {b: tuple(sorted(v[0] or 0 for v in sides.values())) for b, sides in books.items()}
            common = min(multimode(keys.values()))  # most common line; ties go to the lower one
            book = min(b for b, k in keys.items() if k == common)
            out[market] = (book, books[book])
    return out


def _matching_other_price(market, ours, theirs):
    """The other side's price only when it's for the same bet (spread +3 vs -3, the same total); else None,
    which makes the no-vig price fall back to our side's price alone."""
    if market == "spread" and (ours[0] is None or theirs[0] is None or ours[0] != -theirs[0]):
        return None
    if market == "total" and ours[0] != theirs[0]:
        return None
    return theirs[1]


def _game_splits(conn, home, market, side, kickoff):
    """Latest Action Network (bets %, money %) for this side of THIS game (kickoff within a day); money is None
    where the free page didn't show it."""
    row = conn.execute("SELECT bets_pct, money_pct FROM splits WHERE home_team = ? AND market = ? AND side = ?"
                       " AND abs(julianday(start_time) - julianday(?)) < 1 ORDER BY fetched_at DESC LIMIT 1",
                       (home, market, side, kickoff)).fetchone()
    return row if row else (None, None)


def game_picks(conn, now=None, prices=None, absences=None):
    now = now or _now()
    if absences is None:
        absences = model.team_absences(conn, model.injury_flags(conn), _upcoming_team_weeks(conn, now))
    g = game_factors.adjusted(conn, absences)   # ratings + matchups, injuries, weather, rest, hype, QB out
    calib = _game_calibrations(g)
    try:
        game_factors.record(conn, g, now)   # a diagnostics row must never stop the picks
    except sqlite3.Error as e:
        print(f"learning log skipped: {e}")
    prices = prices or kalshi.Prices(conn, now)
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
        book_home = home  # the betting splits are stored under the sportsbooks' home team
        swapped = TEAMS.get(home) != r["home_team"]
        if swapped:
            # neutral site listed the other way round: the model, ESPN lines, Kalshi and the grader all use
            # nflverse's home team, so the prices must too
            home, away = away, home
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
                p_a = model.recalibrate(game_model.p_over(r["blend_total"], line, r["blend_sigma_total"]), calib["total"])
                a_name, b_name, a_side, b_side = "Over", "Under", "over", "under"
            else:
                if home not in sides or away not in sides:
                    continue
                spread = offers.get("spreads")
                home_spread = spread[1][home][0] if spread and home in spread[1] else None
                if market == "spread":
                    line = sides[home][0]
                    p_a = model.recalibrate(game_model.p_home_covers(r["blend_margin"], line, r["blend_sigma_margin"]),
                                            calib["spread"])
                elif home_spread is not None:
                    # moneyline from the spread view, so the two can't contradict each other
                    p_cover = model.recalibrate(game_model.p_home_covers(r["blend_margin"], home_spread, r["blend_sigma_margin"]),
                                                calib["spread"])
                    p_a = game_model.p_home_wins_from_spread(p_cover, home_spread, r["blend_sigma_margin"])
                else:
                    continue
                a_name, b_name, a_side, b_side = home, away, "home", "away"
            both = []
            for name, other, side, prob in ((a_name, b_name, a_side, float(p_a)), (b_name, a_name, b_side, float(1 - p_a))):
                splits_side = {"home": "away", "away": "home"}.get(side, side) if swapped else side
                bets, money = _game_splits(conn, book_home, market, splits_side, kickoff)
                moved = None
                if lines and market == "spread" and lines[0] is not None and lines[1] is not None:
                    toward_home = lines[0] - lines[1]   # home spread getting more negative = market likes home
                    moved = toward_home if side == "home" else -toward_home
                elif lines and market == "total" and lines[2] is not None and lines[3] is not None:
                    moved = (lines[3] - lines[2]) * (1 if side == "over" else -1)
                side_line = sides[name][0] if market != "moneyline" else None
                market_prob = prices.game(game[0], market, side, side_line)
                steam_move = steam.game_steam(conn, event_id, odds_market, name)
                other_price = _matching_other_price(market, sides[name], sides[other])
                both.append({"side": side, "model_prob": prob, "line": side_line, "price": sides[name][1],
                             "other_price": other_price, "bets_pct": bets, "money_pct": money, "line_move": moved,
                             "market_prob": market_prob,
                             # bets, money and line move are already inside the fitted game probability
                             "decision": decision.decide(prob, sides[name][1], other_price, None, None, None,
                                                         market_prob, steam_move),
                             "steam": steam_move, "label": f"{away} @ {home}: {name}" + (f" {side_line:+g}" if market == "spread" else
                                                                     f" {side_line:g}" if market == "total" else " ML")})
            chosen = both[0] if p_a >= 0.5 else both[1]  # the side the model favors is the pick
            out.append(dict(chosen, kind="game", game_id=game[0], event_id=event_id, market=market, book=book,
                            mu=float(r["blend_margin"] if market != "total" else r["blend_total"]), sides=both))
    return out


def build(conn, now=None):
    now = now or _now()
    prices = kalshi.Prices(conn, now)  # loaded once for props and games
    flags = model.injury_flags(conn)
    absences = model.team_absences(conn, flags, _upcoming_team_weeks(conn, now))   # once, for props and games
    return prop_picks(conn, now, prices, flags, absences) + game_picks(conn, now, prices, absences)


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
            kalshi_note = f", Kalshi {p['market_prob']:.1%}" if p.get("market_prob") is not None else ""
            print(f"  {p['label']} ({p['price']:+d} {p['book']}): ours {p['model_prob']:.1%} vs book {d['fair']:.1%}{kalshi_note},"
                  f" avg return {d['ev']:+.1%} | {'; '.join(d['reasons'])}")


if __name__ == "__main__":
    conn = db.connect(os.getenv("DB_PATH", "betbot.db"))
    picks = build(conn)
    print_board(picks)
