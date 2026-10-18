"""Public betting splits (% of bets, % of money) for game lines, from Action Network's public betting page.

Reads only what a logged-out visitor sees: % of bets for every game, % of money only for games the page shows for
free. The page's embedded data also holds the PRO-locked money numbers; we deliberately ignore those.
One page load per run; robots.txt allows /{league}/public-betting.
shortcut: depends on the page's embedded data and table layout; breaks if Action Network redesigns the page.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

import requests

from betbot import db

PAGE_URL = "https://www.actionnetwork.com/{}/public-betting"
HEADERS = {"User-Agent": "betbot/0.1 (personal research script)"}
CONSENSUS_BOOK = "15"  # Action Network's all-books consensus: source of the percentages
LINE_BOOK = "68"  # DraftKings: consensus lines are averages (e.g. 42) no book offers, so show a real one
MARKETS = ("spread", "total", "moneyline")


def _embedded_games(html):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError("Action Network page layout changed: no embedded data")
    return json.loads(m.group(1))["props"]["pageProps"]["scoreboardResponse"]["games"]


def _visible_text(html):
    body = html.split('<script id="__NEXT_DATA__"')[0]
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", body, flags=re.S)
    return re.sub(r"\|+", "|", re.sub(r"<[^>]+>", "|", body))


def _money_shown(text, games):
    """Action Network game ids whose table row shows % of money (4 percentages) rather than only % of bets (2).

    The page renders each game more than once (mobile and desktop layouts), so every occurrence is a row.
    """
    starts = sorted((m.start(), g["id"]) for g in games
                    for m in re.finditer(rf"\|{g['away_rotation_number']}\|", text))
    shown = set()
    for (pos, gid), (end, _) in zip(starts, starts[1:] + [(len(text), None)]):
        # a row ends at the first long text (a banner, heading or article), so content after the table can't count
        row = re.split(r"\|[^|]{41,}\|", text[pos:end])[0]
        if len(re.findall(r"\|\d{1,3}\|%", row)) >= 4:
            shown.add(gid)
    return shown


def parse(html, league, fetched_at):
    games = _embedded_games(html)
    free_money = _money_shown(_visible_text(html), games)
    rows = []
    for g in games:
        teams = {t["id"]: t["full_name"] for t in g["teams"]}
        home = teams.get(g["home_team_id"])
        away = next((name for tid, name in teams.items() if tid != g["home_team_id"]), None)
        markets = g.get("markets", {})
        event = markets.get(CONSENSUS_BOOK, {}).get("event", {})
        line_event = markets.get(LINE_BOOK, {}).get("event", {})
        for market in MARKETS:
            real_lines = {o.get("side"): o.get("value") for o in line_event.get(market, [])}
            for o in event.get(market, []):
                info = o.get("bet_info") or {}
                rows.append({
                    "league": league, "game_id": g["id"], "start_time": g["start_time"],
                    "home_team": home, "away_team": away, "market": market, "side": o.get("side"),
                    "line": real_lines.get(o.get("side"), o.get("value")), "bets_pct": (info.get("tickets") or {}).get("percent"),
                    "money_pct": (info.get("money") or {}).get("percent") if g["id"] in free_money else None,
                    "fetched_at": fetched_at,
                })
    return rows


def update(conn, league="nfl"):
    resp = requests.get(PAGE_URL.format(league), headers=HEADERS, timeout=30)
    resp.raise_for_status()
    rows = parse(resp.text, league, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    with conn:
        db.save_splits(conn, rows)
    return rows


if __name__ == "__main__":
    rows = update(db.connect(os.getenv("DB_PATH", "betbot.db")), sys.argv[1] if len(sys.argv) > 1 else "nfl")
    for r in rows:
        if r["market"] == "total":
            money = f"{r['money_pct']}% of money" if r["money_pct"] is not None else "money locked"
            print(f"{r['away_team']} @ {r['home_team']} {r['side']} {r['line']}: {r['bets_pct']}% of bets, {money}")
