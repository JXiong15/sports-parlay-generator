"""Opening, current and closing NFL game lines (total, home spread) from ESPN's public odds feed.

shortcut: this feed is undocumented and can change without notice; switch to a paid odds-history API if it breaks.
"""
import os
import sys
import time
from datetime import datetime, timezone

import requests

from betbot import db
from betbot.names import TEAMS, normalize

INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
ODDS_URL = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/{0}/competitions/{0}/odds"


def _num(s):
    if s is None:
        return None
    if str(s).lower() in ("pk", "even"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return None


def _dig(d, *keys):
    for k in keys:
        d = (d or {}).get(k)
    return d


def parse(item):
    row = {"book": item["provider"]["name"]}
    for k in ("open", "current", "close"):
        row[f"{k}_total"] = _num(_dig(item, k, "total", "american"))
        row[f"{k}_home_spread"] = _num(_dig(item, "homeTeamOdds", k, "pointSpread", "american"))
    return row


def pick(items):
    """First pregame book that has an opening total; books often list it empty. Live (in-game) odds are skipped."""
    pregame = [i for i in items if "live" not in i["provider"]["name"].lower()]
    return next((i for i in pregame if _dig(i, "open", "total", "american")), pregame[0] if pregame else None)


def update(conn, games):
    """games: (nflverse game_id, espn event id) pairs."""
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for game_id, espn_id in games:
        try:
            resp = requests.get(ODDS_URL.format(espn_id), timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            # unsaved games are retried on the next run
            print(f"skipped {game_id}: {e.__class__.__name__}")
            continue
        item = pick(resp.json().get("items") or [])
        if item:
            row = dict(parse(item), game_id=game_id, fetched_at=fetched_at)
            with conn:
                db.save_game_line(conn, row)
        time.sleep(0.2)  # be polite to an unofficial feed


def parse_injuries(data, fetched_at):
    rows = []
    for team in data.get("injuries", []):
        abbr = TEAMS.get(team.get("displayName"))
        if abbr is None:
            print(f"unknown team in ESPN injuries: {team.get('displayName')!r}")
            continue
        for i in team.get("injuries", []):
            name = _dig(i, "athlete", "displayName")
            if name and i.get("status"):
                rows.append({"team": abbr, "player": name, "name_key": normalize(name),
                             "position": _dig(i, "athlete", "position", "abbreviation"),
                             "status": i["status"], "fetched_at": fetched_at})
    return rows


def update_injuries(conn):
    resp = requests.get(INJURIES_URL, timeout=30)
    resp.raise_for_status()
    rows = parse_injuries(resp.json(), datetime.now(timezone.utc).isoformat(timespec="seconds"))
    if rows:  # an empty or reshaped response must not wipe the last good report
        with conn:
            db.replace_injuries(conn, rows)


def upcoming(conn, days=7):
    return conn.execute(
        "SELECT game_id, espn FROM nfl_games WHERE espn IS NOT NULL"
        " AND gameday BETWEEN date('now', 'localtime') AND date('now', 'localtime', ?)",
        (f"+{days} days",),
    ).fetchall()


def unfetched_finished(conn, first_season):
    return conn.execute(
        "SELECT game_id, espn FROM nfl_games WHERE espn IS NOT NULL AND home_score IS NOT NULL AND season >= ?"
        " AND game_id NOT IN (SELECT game_id FROM nfl_game_lines WHERE close_total IS NOT NULL)",
        (first_season,),
    ).fetchall()


if __name__ == "__main__":
    # backfill past games for backtesting: python -m betbot.collectors.espn 2024
    conn = db.connect(os.getenv("DB_PATH", "betbot.db"))
    games = unfetched_finished(conn, int(sys.argv[1]) if len(sys.argv) > 1 else 2024)
    print(f"fetching {len(games)} games...")
    update(conn, games)
    print(conn.execute("SELECT count(*) FROM nfl_game_lines WHERE close_total IS NOT NULL").fetchone()[0], "games with open/close lines")
