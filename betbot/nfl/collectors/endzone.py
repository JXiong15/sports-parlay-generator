"""Historical DraftKings player-prop lines (2023 on) from EndzoneAPI, saved in our own database so each player and
market is pulled once, ever. Docs: https://mcp.endzoneapi.com/openapi.json

One get_prop_history call = one player + one market = every game since 2023 (10 credits, empty results free).
Results, schedules and stats are not pulled: nflverse already gives them free. A plain run only prints the plan
(calls and credits, free); add --go and --max-credits N to spend.
"""
import argparse
import os
import time

import requests

from betbot import db
from betbot.nfl.signals.regression import MARKETS

URL = "https://mcp.endzoneapi.com/v1/get_prop_history"
BOOK = "draftkings"
CREDITS_PER_CALL = 10
PAUSE = 1.1  # 60 requests a minute
# market -> (nflverse column that shows he is a real part of the offense, minimum per game, minimum games)
VOLUME = {"player_pass_yds": ("attempts", 15, 3), "player_pass_tds": ("attempts", 15, 3),
          "player_pass_attempts": ("attempts", 15, 3), "player_pass_completions": ("attempts", 15, 3),
          "player_pass_interceptions": ("attempts", 15, 3), "player_rush_yds": ("carries", 8, 3),
          "player_rush_attempts": ("carries", 8, 3), "player_reception_yds": ("targets", 4, 5),
          "player_receptions": ("targets", 4, 5),
          # a touchdown scorer: any mix of passing, carries and targets (a QB's attempts count a quarter each)
          "player_anytime_td": ("attempts / 4.0 + carries + targets", 6, 3)}
POSITIONS = {m: MARKETS[m][1] for m in MARKETS} | {"player_anytime_td": ("QB", "RB", "WR", "TE")}
YARDS = ["player_pass_yds", "player_rush_yds", "player_reception_yds"]
LINE_COLUMNS = ("player_id", "market", "game_id", "game_date", "player_name", "line", "open_line", "over_odds",
                "under_odds", "books")


def candidates(conn, market, since=2023, min_games=None):
    """Players with a real role in this market since that season, current starters first. (A pulled player's
    history still runs back to 2023: the call costs the same.)"""
    column, per_game, games = VOLUME[market]
    games = min_games or games
    positions = POSITIONS[market]
    marks = ", ".join("?" * len(positions))
    return conn.execute(
        f"SELECT player_id, max(player_display_name) FROM nfl_player_weeks WHERE season >= ? AND position IN ({marks})"
        f" AND {column} >= ? GROUP BY player_id HAVING count(*) >= ? ORDER BY max(season * 100 + week) DESC,"
        f" count(*) DESC", (since, *positions, per_game, games)).fetchall()


def plan(conn, markets, max_credits=None, since=2023, min_games=None):
    """(player_id, name, market) not pulled yet, in priority order, within the credit budget."""
    done = {tuple(r) for r in conn.execute("SELECT player_id, market FROM endzone_pulled WHERE bookmaker = ?", (BOOK,))}
    todo = [(pid, name, m) for m in markets for pid, name in candidates(conn, m, since, min_games) if (pid, m) not in done]
    return todo if max_credits is None else todo[:max(max_credits, 0) // CREDITS_PER_CALL]


def save(conn, player_id, market, games, pulled_at):
    rows = [{"player_id": player_id, "market": market, **{c: g.get(c) for c in LINE_COLUMNS[2:] if c != "player_name"},
             "player_name": g.get("player_name")} for g in games]
    with conn:
        conn.executemany(f"INSERT OR REPLACE INTO endzone_lines ({', '.join(LINE_COLUMNS)}) VALUES"
                         f" ({', '.join(':' + c for c in LINE_COLUMNS)})", rows)
        conn.execute("INSERT OR REPLACE INTO endzone_pulled VALUES (?, ?, ?, ?, ?)",
                     (player_id, market, BOOK, pulled_at, len(rows)))


def fetch(session, key, player_id, market):
    """(games, calls made): all of one player's games for one market, following the paging cursor. Retries a rate
    limit or server error three times; any other non-200 reply raises."""
    games, start, calls = [], None, 0
    while True:
        body = {"player_id": player_id, "market": market, "bookmaker": BOOK, "limit": 200}
        if start:
            body["from_date"] = start
        for attempt in range(3):
            resp = session.post(URL, json=body, headers={"x-api-key": key}, timeout=60)
            calls += 1
            if resp.status_code != 429 and resp.status_code < 500:
                break
            time.sleep(PAUSE * 5 * (attempt + 1))
        if resp.status_code != 200:
            raise RuntimeError(f"EndzoneAPI {resp.status_code}: {resp.text[:200]}")
        data = resp.json()["data"]
        name = data.get("player_name")
        games += [dict(g, player_name=name) for g in data.get("games", [])]
        start = data.get("next_from_date")
        if not (data.get("truncated") and start):
            return games, calls
        time.sleep(PAUSE)


def pull(conn, todo, key, session=None, sleep=time.sleep, max_credits=None):
    """Pulls and saves each player in turn; stops before the next one if the credits spent (every call counts,
    paging and retries included) would pass max_credits."""
    from datetime import datetime, timezone
    session = session or requests.Session()
    spent = 0
    for i, (pid, name, market) in enumerate(todo, 1):
        if max_credits is not None and spent + CREDITS_PER_CALL > max_credits:
            print(f"stopped at {spent} credits (cap {max_credits})")
            break
        games, calls = fetch(session, key, pid, market)
        spent += calls * CREDITS_PER_CALL
        save(conn, pid, market, games, datetime.now(timezone.utc).isoformat(timespec="seconds"))
        print(f"[{i}/{len(todo)}] {name} {market}: {len(games)} games")
        sleep(PAUSE)
    return len(todo)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--markets", nargs="+", default=YARDS, choices=sorted(VOLUME))
    ap.add_argument("--since", type=int, default=2023, help="only players with a real role since this season (2026 = current players)")
    ap.add_argument("--min-games", type=int, help="games with a real role needed since --since (default: 3, or 5 for receivers)")
    ap.add_argument("--max-credits", type=int, help="most credits this run may spend (required with --go)")
    ap.add_argument("--go", action="store_true", help="actually make the calls (spends credits)")
    args = ap.parse_args(argv)
    conn = db.connect(os.getenv("DB_PATH", "betbot.db"))
    todo = plan(conn, args.markets, args.max_credits if args.go else None, args.since,
                args.min_games)
    by_market = {m: sum(1 for t in todo if t[2] == m) for m in args.markets}
    print(f"{len(todo)} calls, up to {len(todo) * CREDITS_PER_CALL} credits (empty results are free): {by_market}")
    if not args.go:
        return print("Plan only. Add --go --max-credits N to pull.")
    if args.max_credits is None or args.max_credits < 0:
        raise SystemExit("--go needs --max-credits N (zero or more)")
    key = os.getenv("ENDZONE_API_KEY")
    if not key:
        raise SystemExit("ENDZONE_API_KEY is not set in .env")
    pull(conn, todo, key, max_credits=args.max_credits)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    main()
