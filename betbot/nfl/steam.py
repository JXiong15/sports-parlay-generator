"""Steam and originator-vs-copier from our own saved line history: how far the lines moved across books in the last
day or two, toward which side, and which book moved first. Late steam is followed or avoided, never bet against."""
from datetime import datetime, timedelta, timezone
from statistics import median

def _parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def game_steam(conn, event_id, market, side, hours=48, now=None):
    """{'move': median points toward `side` among books that moved, 'books': how many moved that way,
    'first_book': the book that moved first} or None (no move, or books split evenly).
    Spreads/moneyline-less: `market` is 'spreads' or 'totals'; `side` is the team name or Over / Under."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(hours=hours)).isoformat(timespec="seconds")
    rows = conn.execute("SELECT book, line, coalesce(book_updated_at, fetched_at) FROM game_odds"
                        " WHERE event_id = ? AND market = ? AND side = ? AND line IS NOT NULL"
                        " AND coalesce(book_updated_at, fetched_at) >= ? ORDER BY 3", (event_id, market, side, since)).fetchall()
    by_book = {}
    for book, line, at in rows:
        by_book.setdefault(book, []).append((at, line))
    sign = 1.0 if side == "Over" else -1.0       # a total rising favors the Over; any other line shrinking favors it
    moves = {}
    for book, seq in by_book.items():
        delta = sign * (seq[-1][1] - seq[0][1])
        if delta:
            moves[book] = (delta, next(at for at, line in seq if line != seq[0][1]))
    if not moves:
        return None
    up = [b for b, (d, _) in moves.items() if d > 0]
    down = [b for b, (d, _) in moves.items() if d < 0]
    if len(up) == len(down):
        return None
    movers = up if len(up) > len(down) else down
    first = min(movers, key=lambda b: moves[b][1])
    return {"move": float(median(moves[b][0] for b in movers)), "books": len(movers), "first_book": first}
