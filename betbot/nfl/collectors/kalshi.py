"""NFL prices from Kalshi, a prediction market: trader-set probabilities with no sportsbook margin.

Official public market-data API, no account or key (docs.kalshi.com, "Quick Start: Market Data"). One request
per series page, 0.3 seconds apart. Only price changes are saved, so history builds up run by run.

Markets: game winner, spread ladders ("New England wins by over 7.5"), total ladders, and player-prop ladders
("Tyler Huntley: 275+ passing yards", strike 274.5). A market's probability is the midpoint of its bid and ask,
used only when the gap is tight (thin markets with a 25c bid and 82c ask say nothing).
"""
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

import requests

from betbot import db
from betbot.names import normalize

API_URL = "https://api.elections.kalshi.com/trade-api/v2/markets"
HEADERS = {"User-Agent": "betbot/0.1 (personal research script)"}
MAX_GAP_CENTS = 10  # widest bid-ask gap whose midpoint we trust
FRESH_HOURS = 12    # a price not seen in a run within this long is treated as gone (closed, delisted, fetch failed)
# Kalshi series -> our market key (Odds API names for props; h2h / spreads / totals for games)
SERIES = {
    "KXNFLGAME": "h2h", "KXNFLSPREAD": "spreads", "KXNFLTOTAL": "totals",
    "KXNFLPASSYDS": "player_pass_yds", "KXNFLPASSTDS": "player_pass_tds", "KXNFLPASSATT": "player_pass_attempts",
    "KXNFLPASSCOMP": "player_pass_completions", "KXNFLINT": "player_pass_interceptions",
    "KXNFLRSHYDS": "player_rush_yds", "KXNFLRSHATT": "player_rush_attempts", "KXNFLREC": "player_receptions",
    "KXNFLRECYDS": "player_reception_yds",
}
# Kalshi team codes that differ from nflverse's
CODES = {"JAC": "JAX", "LAR": "LA", "WSH": "WAS"}
NFLVERSE = {"ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX",
            "KC", "LA", "LAC", "LV", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN",
            "WAS"}
MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV",
                                      "DEC"], 1)}


def _team(code):
    code = CODES.get(code, code)
    return code if code in NFLVERSE else None


def parse_event(event_ticker):
    """'KXNFLRECYDS-26OCT18HOUJAC' -> ('2026-10-18', 'HOU', 'JAX') (away, home as Kalshi lists them)."""
    m = re.match(r"^[A-Z0-9]+-(\d{2})([A-Z]{3})(\d{2})([A-Z]+)$", event_ticker)
    if not m or m.group(2) not in MONTHS:
        return None
    day = date(2000 + int(m.group(1)), MONTHS[m.group(2)], int(m.group(3))).isoformat()
    teams = m.group(4)
    for cut in range(2, len(teams) - 1):
        away, home = _team(teams[:cut]), _team(teams[cut:])
        if away and home:
            return day, away, home
    return None


def parse_market(m, series):
    """One Kalshi market -> a row for kalshi_markets, or None if it isn't a market we use (or is malformed)."""
    if not m.get("ticker"):
        return None
    event = parse_event(m.get("event_ticker", ""))
    if not event:
        return None
    day, away, home = event
    market = SERIES[series]
    suffix = m["ticker"].rsplit("-", 1)[-1]
    if market == "h2h":
        subject = _team(suffix)                      # 'PHI' in ...-PHI: Philadelphia wins
    elif market == "spreads":
        subject = _team(re.sub(r"\d+$", "", suffix))  # 'NE8': New England wins by over the strike
    elif market == "totals":
        subject = "total"
    else:
        subject = (m.get("title") or "").split(":")[0].strip()  # 'Tyler Huntley: 275+ passing yards'
    if not subject:
        return None
    def num(k):
        try:
            return float(m[k]) if m.get(k) not in (None, "") else None
        except (TypeError, ValueError):  # e.g. a placeholder like '—'
            return None
    return {"ticker": m["ticker"], "series": series, "event_ticker": m["event_ticker"], "market": market,
            "subject": subject, "strike": m.get("floor_strike"), "game_date": day, "away": away, "home": home,
            "yes_bid": num("yes_bid_dollars"), "yes_ask": num("yes_ask_dollars"),
            "last_price": num("last_price_dollars"), "volume": num("volume_fp"),
            "open_interest": num("open_interest_fp"), "close_time": m.get("close_time")}


def fair_prob(bid, ask):
    """Midpoint of a tight market, else None."""
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return None  # no bid, or a crossed book
    # tolerance, not rounding: 0.55 - 0.45 is 0.10000000000000009 in floats, and prices can carry sub-cents
    if ask - bid > MAX_GAP_CENTS / 100 + 1e-9:
        return None
    return (bid + ask) / 2


MAX_PAGES = 50


def fetch(series):
    markets, cursor, seen = [], None, set()
    for _ in range(MAX_PAGES):  # a repeated or endless cursor can't hang the run
        params = {"series_ticker": series, "status": "open", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        markets += data.get("markets", [])
        cursor = data.get("cursor")
        time.sleep(0.3)
        if not cursor or cursor in seen:
            return markets
        seen.add(cursor)
    return markets


def update(conn):
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    changed = total = 0
    for series in SERIES:
        try:
            rows = [r for r in (parse_market(m, series) for m in fetch(series)) if r]
            with conn:
                changed += db.save_kalshi(conn, [dict(r, fetched_at=fetched_at) for r in rows])
                db.mark_seen(conn, "kalshi_markets", rows, fetched_at)
        except (requests.RequestException, ValueError, KeyError, TypeError) as e:  # Kalshi down or odd data
            print(f"  kalshi {series} skipped: {e.__class__.__name__}: {e}")
            continue
        total += len(rows)
    return total, changed


# ---------- lookups for the board ----------

class Prices:
    """Kalshi's fresh, tight prices, loaded once per board build and grouped by game, market and subject.
    Teams are matched in either order (a neutral-site game may be listed either way)."""

    def __init__(self, conn, now=None):
        self.conn = conn
        now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.by = {}
        for market, subject, strike, away, home, day, bid, ask in conn.execute(
                "SELECT k.market, k.subject, k.strike, k.away, k.home, k.game_date, k.yes_bid, k.yes_ask"
                " FROM kalshi_markets k WHERE k.seen_at IS NOT NULL"
                " AND (julianday(?) - julianday(k.seen_at)) * 24 <= ?"
                " AND k.fetched_at = (SELECT max(fetched_at) FROM kalshi_markets x WHERE x.ticker = k.ticker)",
                (now, FRESH_HOURS)):
            p = fair_prob(bid, ask)
            if p is not None:
                key = (market, frozenset((away, home)), day)
                self.by.setdefault(key, {}).setdefault(normalize(subject), []).append((strike, p))

    def _points(self, market, teams, day, subject):
        for delta in (0, -1, 1):  # Kalshi's date can differ by a day from nflverse's (late kickoffs in UTC)
            d = (date.fromisoformat(day[:10]) + timedelta(days=delta)).isoformat()
            found = self.by.get((market, frozenset(teams), d), {}).get(normalize(subject))
            if found:
                return sorted(found, key=lambda x: float("inf") if x[0] is None else x[0])
        return []

    def _game(self, game_id):
        return self.conn.execute("SELECT away_team, home_team, gameday FROM nfl_games WHERE game_id = ?",
                                 (game_id,)).fetchone()

    def prop(self, game_id, market, player, side, line):
        """Kalshi's probability for our side of a prop, or None if Kalshi has no fresh tight price there."""
        g = self._game(game_id)
        if not g:
            return None
        points = [(s, p) for s, p in self._points(market, g[:2], g[2], player) if s is not None]
        p_over = interpolate(points, line)
        return None if p_over is None else (p_over if side == "Over" else 1 - p_over)

    def game(self, game_id, market, side, line):
        """Kalshi's probability for our side of a game bet (side: home/away/over/under; line from our side)."""
        g = self._game(game_id)
        if not g:
            return None
        away, home, day = g
        ladder = lambda m, subject: [(s, p) for s, p in self._points(m, (away, home), day, subject) if s is not None]
        if market == "total":
            p_over = interpolate(ladder("totals", "total"), line)
            return None if p_over is None else (p_over if side == "over" else 1 - p_over)
        team, other = (home, away) if side == "home" else (away, home)
        if market == "moneyline" or line == 0:  # a pick'em spread is a moneyline
            points = self._points("h2h", (away, home), day, team)
            return points[0][1] if points else None
        # spread from our side: a favorite (-x) must win by more than x; an underdog (+x) covers unless the
        # other team wins by more than x
        if line < 0:
            return interpolate(ladder("spreads", team), -line)
        p_other = interpolate(ladder("spreads", other), line)
        return None if p_other is None else 1 - p_other


def interpolate(points, line):
    """P(value > line) from ladder points (strike, P(value > strike)); None outside the ladder."""
    if not points:
        return None
    for (s1, p1), (s2, p2) in zip(points, points[1:]):
        if s1 <= line <= s2:
            return p1 if s2 == s1 else p1 + (p2 - p1) * (line - s1) / (s2 - s1)
    return points[0][1] if line == points[0][0] else None


if __name__ == "__main__":
    total, changed = update(db.connect(os.getenv("DB_PATH", "betbot.db")))
    print(f"kalshi: {total} open NFL markets, {changed} prices changed")
