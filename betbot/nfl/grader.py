"""Grades logged NFL picks once the game is played: the actual result, and the closing line and price
(closing line value = how the no-vig probability of our side moved by kickoff; positive means we beat the close)."""
import os
import sqlite3
from datetime import datetime, timezone

from betbot import db
from betbot.nfl.signals.regression import MARKETS
from betbot.odds import no_vig


def _outcome(score):
    return "win" if score > 0 else "push" if score == 0 else "loss"


def _closing_prop(conn, p):
    """Our side's line and price at the same book just before kickoff, and the opposite side's price."""
    start = conn.execute("SELECT commence_time FROM events WHERE id = ?", (p["event_id"],)).fetchone()
    if not start:
        return None
    other_side = "Under" if p["side"] == "Over" else "Over"
    last = lambda side: conn.execute(
        "SELECT line, price FROM lines WHERE event_id = ? AND market = ? AND player = ? AND side = ? AND book = ?"
        " AND fetched_at <= ? ORDER BY fetched_at DESC, id DESC LIMIT 1",
        (p["event_id"], p["market"], p["player"], side, p["book"], start[0].replace("Z", "+00:00"))).fetchone()
    ours, theirs = last(p["side"]), last(other_side)
    if not ours:
        return None
    other = theirs[1] if theirs and theirs[0] == ours[0] else None
    return ours[0], ours[1], no_vig(ours[1], other)[0]


def _played(conn, player_id, game_id):
    """True / False from snap counts, or None when they can't tell yet (not posted for this game, or the player
    has no ID link to them): then the pick waits instead of being voided for good."""
    try:
        if not conn.execute("SELECT 1 FROM nfl_snap_counts WHERE game_id = ? LIMIT 1", (game_id,)).fetchone():
            return None
        if not conn.execute("SELECT 1 FROM nfl_players WHERE gsis_id = ? AND pfr_id IS NOT NULL",
                            (player_id,)).fetchone():
            return None
        return conn.execute("SELECT 1 FROM nfl_snap_counts s JOIN nfl_players p ON p.pfr_id = s.pfr_player_id"
                            " WHERE p.gsis_id = ? AND s.game_id = ? AND (s.offense_snaps > 0 OR s.st_snaps > 0)",
                            (player_id, game_id)).fetchone() is not None
    except sqlite3.OperationalError:  # snap counts not loaded
        return None


def _clv_points(p, closing_line):
    """How many points (or yards) better our line was than the close, from our side; None for moneylines."""
    if closing_line is None or p["line"] is None:
        return None
    if p["side"] in ("Over", "over"):
        return closing_line - p["line"]
    if p["side"] in ("Under", "under"):
        return p["line"] - closing_line
    return p["line"] - closing_line  # spread from our side: +7 when it closed +6 is a point better


def grade_prop(conn, p, now=None):
    stat = MARKETS[p["market"]][0]
    row = conn.execute(f'SELECT "{stat}" FROM nfl_player_weeks WHERE player_id = ? AND game_id = ?',
                       (p["player_id"], p["game_id"])).fetchone()
    if row is None:
        posted = conn.execute("SELECT 1 FROM nfl_player_weeks WHERE game_id = ? LIMIT 1", (p["game_id"],)).fetchone()
        if not posted:
            return None
        # nflverse leaves out players who played but recorded nothing; snap counts show who actually played
        played = _played(conn, p["player_id"], p["game_id"])
        if played is None:
            old = conn.execute("SELECT julianday(?) - julianday(gameday) > 7 FROM nfl_games WHERE game_id = ?",
                               (now or datetime.now(timezone.utc).isoformat(), p["game_id"])).fetchone()
            if not (old and old[0]):
                return None  # can't tell yet: grade on a later run
            # a week on and still no way to tell whether he played: neither a win, a loss nor a void
            return {"actual": None, "result": "unverified"}
        if not played:
            return {"actual": None, "result": "void"}  # didn't play: books void the bet
        row = (0,)
    actual = row[0] or 0
    diff = actual - p["line"] if p["side"] == "Over" else p["line"] - actual
    graded = {"actual": actual, "result": _outcome(diff)}
    close = _closing_prop(conn, p)
    if close:
        graded.update(closing_line=close[0], closing_price=close[1], closing_implied=close[2])
    return graded


def _closing_game(conn, p, g):
    """ESPN's closing line and prices for our side (2024 on), else nflverse's closing line."""
    l = conn.execute("SELECT close_home_spread, close_home_spread_price, close_away_spread_price, close_total,"
                     " close_over_price, close_under_price, close_home_ml, close_away_ml FROM nfl_game_lines"
                     " WHERE game_id = ?", (p["game_id"],)).fetchone()
    espn = l or (None,) * 8
    nflverse = (-g["spread_line"] if g["spread_line"] is not None else None,  # nflverse: + = home favored
                g["home_spread_odds"], g["away_spread_odds"], g["total_line"], g["over_odds"], g["under_odds"],
                g["home_moneyline"], g["away_moneyline"])
    # each market from ESPN when ESPN has it (line and both prices), else from nflverse
    pick = lambda idx: tuple(espn[i] for i in idx) if all(espn[i] is not None for i in idx) else tuple(nflverse[i] for i in idx)
    home_spread, home_sp, away_sp = pick((0, 1, 2))
    total, over_p, under_p = pick((3, 4, 5))
    home_ml, away_ml = pick((6, 7))
    side, market = p["side"], p["market"]
    if market == "spread" and home_spread is not None:
        line = home_spread if side == "home" else -home_spread
        price, other = (home_sp, away_sp) if side == "home" else (away_sp, home_sp)
    elif market == "total" and total is not None:
        line, (price, other) = total, ((over_p, under_p) if side == "over" else (under_p, over_p))
    elif market == "moneyline":
        line, (price, other) = None, ((home_ml, away_ml) if side == "home" else (away_ml, home_ml))
    else:
        return None
    if price is None:
        return None
    return line, price, no_vig(price, other)[0]


def grade_game(conn, p, g):
    margin = g["home_score"] - g["away_score"]
    side_margin = margin if p["side"] == "home" else -margin
    if p["market"] == "spread":
        graded = {"actual": side_margin, "result": _outcome(side_margin + p["line"])}
    elif p["market"] == "total":
        total = g["home_score"] + g["away_score"]
        graded = {"actual": total, "result": _outcome(total - p["line"] if p["side"] == "over" else p["line"] - total)}
    else:
        graded = {"actual": side_margin, "result": _outcome(side_margin)}
    close = _closing_game(conn, p, g)
    if close:
        graded.update(closing_line=close[0], closing_price=close[1], closing_implied=close[2])
    return graded


def grade(conn, now=None):
    """Grades every ungraded pick whose game has a final score. Returns how many were graded."""
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(predictions)")]
    game_cols = ["game_id", "home_score", "away_score", "spread_line", "total_line", "home_spread_odds",
                 "away_spread_odds", "over_odds", "under_odds", "home_moneyline", "away_moneyline"]
    done = 0
    for values in conn.execute("SELECT * FROM predictions WHERE graded_at IS NULL").fetchall():
        p = dict(zip(cols, values))
        g = conn.execute(f"SELECT {', '.join(game_cols)} FROM nfl_games WHERE game_id = ?", (p["game_id"],)).fetchone()
        if g is None or g[1] is None:
            continue  # not played yet
        g = dict(zip(game_cols, g))
        graded = grade_prop(conn, p, now) if p["kind"] == "prop" else grade_game(conn, p, g)
        if graded is None:
            continue  # stats not posted yet
        if "closing_line" in graded:
            graded["clv_points"] = _clv_points(p, graded["closing_line"])
            # the no-vig probabilities are only comparable at the same line
            if graded.get("closing_implied") is not None and graded["closing_line"] == p["line"]:
                graded["clv"] = graded["closing_implied"] - p["implied_prob"]
        graded["graded_at"] = now
        with conn:
            conn.execute(f"UPDATE predictions SET {', '.join(k + ' = ?' for k in graded)} WHERE id = ?",
                         (*graded.values(), p["id"]))
        done += 1
    return done


if __name__ == "__main__":
    print(grade(db.connect(os.getenv("DB_PATH", "betbot.db"))), "picks graded")
