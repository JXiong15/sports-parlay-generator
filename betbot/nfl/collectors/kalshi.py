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
from datetime import date, datetime, timezone

import requests

from betbot import db
from betbot.names import normalize

API_URL = "https://api.elections.kalshi.com/trade-api/v2/markets"
HEADERS = {"User-Agent": "betbot/0.1 (personal research script)"}
MAX_GAP = 0.10  # widest bid-ask gap whose midpoint we trust
# Kalshi series -> our market key (Odds API names for props; h2h / spreads / totals for games)
SERIES = {
    "KXNFLGAME": "h2h", "KXNFLSPREAD": "spreads", "KXNFLTOTAL": "totals",
    "KXNFLPASSYDS": "player_pass_yds", "KXNFLPASSTDS": "player_pass_tds", "KXNFLPASSATT": "player_pass_attempts",
    "KXNFLPASSCOMP": "player_pass_completions", "KXNFLINT": "player_pass_interceptions",
    "KXNFLRSHYDS": "player_rush_yds", "KXNFLRSHATT": "player_rush_attempts", "KXNFLREC": "player_receptions",
    "KXNFLRECYDS": "player_reception_yds", "KXNFLRRYDS": "player_rush_reception_yds",
    "KXNFLANYTD": "player_anytime_td",
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
    """One Kalshi market -> a row for kalshi_markets, or None if it isn't a market we use."""
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
    num = lambda k: float(m[k]) if m.get(k) not in (None, "") else None
    return {"ticker": m["ticker"], "series": series, "event_ticker": m["event_ticker"], "market": market,
            "subject": subject, "strike": m.get("floor_strike"), "game_date": day, "away": away, "home": home,
            "yes_bid": num("yes_bid_dollars"), "yes_ask": num("yes_ask_dollars"),
            "last_price": num("last_price_dollars"), "volume": num("volume_fp"),
            "open_interest": num("open_interest_fp"), "close_time": m.get("close_time")}


def fair_prob(bid, ask):
    """Midpoint of a tight market, else None."""
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask - bid > MAX_GAP:
        return None
    return (bid + ask) / 2


def fetch(series):
    markets, cursor = [], None
    while True:
        params = {"series_ticker": series, "status": "open", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        markets += data.get("markets", [])
        cursor = data.get("cursor")
        time.sleep(0.3)
        if not cursor:
            return markets


def update(conn):
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    changed = total = 0
    for series in SERIES:
        try:
            raw = fetch(series)
        except requests.RequestException as e:
            print(f"  kalshi {series} skipped: {e.__class__.__name__}")
            continue
        rows = [r for r in (parse_market(m, series) for m in raw) if r]
        total += len(rows)
        with conn:
            changed += db.save_kalshi(conn, [dict(r, fetched_at=fetched_at) for r in rows])
    return total, changed


# ---------- lookups for the board ----------

def _latest(conn, market, away, home, game_date, subject=None):
    """(strike, probability) points of the newest tight prices for one ladder, sorted by strike."""
    rows = conn.execute(
        "SELECT k.subject, k.strike, k.yes_bid, k.yes_ask FROM kalshi_markets k WHERE k.market = ?"
        " AND k.away = ? AND k.home = ? AND abs(julianday(k.game_date) - julianday(?)) <= 1"
        " AND k.fetched_at = (SELECT max(fetched_at) FROM kalshi_markets x WHERE x.ticker = k.ticker)",
        (market, away, home, game_date[:10])).fetchall()
    if subject is not None:
        key = normalize(subject)
        rows = [r for r in rows if normalize(r[0]) == key or r[0] == subject]
    points = [(s, fair_prob(b, a)) for _, s, b, a in rows]
    return sorted((s, p) for s, p in points if p is not None and s is not None)


def interpolate(points, line):
    """P(value > line) from ladder points (strike, P(value > strike)); None outside the ladder."""
    if not points:
        return None
    for (s1, p1), (s2, p2) in zip(points, points[1:]):
        if s1 <= line <= s2:
            return p1 if s2 == s1 else p1 + (p2 - p1) * (line - s1) / (s2 - s1)
    return points[0][1] if line == points[0][0] else None


def _game(conn, game_id):
    row = conn.execute("SELECT away_team, home_team, gameday FROM nfl_games WHERE game_id = ?", (game_id,)).fetchone()
    return row


def prop_prob(conn, game_id, market, player, side, line):
    """Kalshi's probability for our side of a prop, or None if Kalshi has no tight price there."""
    g = _game(conn, game_id)
    if not g:
        return None
    p_over = interpolate(_latest(conn, market, g[0], g[1], g[2], subject=player), line)
    return None if p_over is None else (p_over if side == "Over" else 1 - p_over)


def game_prob(conn, game_id, market, side, line):
    """Kalshi's probability for our side of a game bet (side: home/away/over/under; line from our side)."""
    g = _game(conn, game_id)
    if not g:
        return None
    away, home, day = g
    if market == "total":
        p_over = interpolate(_latest(conn, "totals", away, home, day, subject="total"), line)
        return None if p_over is None else (p_over if side == "over" else 1 - p_over)
    team, other = (home, away) if side == "home" else (away, home)
    if market == "moneyline":
        return _moneyline(conn, away, home, day, team)
    # spread from our side: a favorite (-x) must win by more than x; an underdog (+x) loses unless the other
    # team wins by more than x
    if line < 0:
        return interpolate(_latest(conn, "spreads", away, home, day, subject=team), -line)
    p_other = interpolate(_latest(conn, "spreads", away, home, day, subject=other), line)
    return None if p_other is None else 1 - p_other


def _moneyline(conn, away, home, day, team):
    """Newest tight price that `team` wins (game-winner markets have no strike, so no ladder)."""
    row = conn.execute(
        "SELECT k.yes_bid, k.yes_ask FROM kalshi_markets k WHERE k.market = 'h2h' AND k.away = ? AND k.home = ?"
        " AND k.subject = ? AND abs(julianday(k.game_date) - julianday(?)) <= 1 ORDER BY k.fetched_at DESC LIMIT 1",
        (away, home, team, day[:10])).fetchone()
    return fair_prob(*row) if row else None


if __name__ == "__main__":
    total, changed = update(db.connect(os.getenv("DB_PATH", "betbot.db")))
    print(f"kalshi: {total} open NFL markets, {changed} prices changed")
