"""Recency / regression ("chasing yesterday"): lean Under when a big last game has pushed a line above the player's baseline."""
import os
from datetime import datetime, timezone
from statistics import mean, median

from betbot import db
from betbot.collectors.nflverse import current_season
from betbot.names import TEAMS, normalize
from betbot.signals import injuries

# Odds API market -> (nflverse stat column, position groups that can hold the prop; used to split same-name players)
MARKETS = {
    "player_pass_yds": ("passing_yards", ("QB",)),
    "player_pass_tds": ("passing_tds", ("QB",)),
    "player_rush_yds": ("rushing_yards", ("QB", "RB", "WR", "TE")),
    "player_receptions": ("receptions", ("RB", "WR", "TE")),
    "player_reception_yds": ("receiving_yards", ("RB", "WR", "TE")),
}

BASELINE_GAMES = 20
MIN_GAMES = 8
# shortcut: untuned guesses; backtest against stored lines before trusting them
BLOWOUT = 1.25  # last game at least 25% over the line
INFLATED = 1.05  # line at least 5% above both the 20-game mean and median


def current_lines(conn, market, now):
    """{(player, home, away): consensus line}: median across books of each book's latest Over line, games not started."""
    rows = conn.execute(
        "SELECT l.player, e.home_team, e.away_team, l.line FROM lines l JOIN events e ON e.id = l.event_id"
        " WHERE l.market = ? AND l.side = 'Over' AND l.line IS NOT NULL AND e.commence_time > ?"
        " AND l.book_updated_at = (SELECT max(book_updated_at) FROM lines x WHERE x.event_id = l.event_id"
        "  AND x.market = l.market AND x.player = l.player AND x.side = l.side AND x.book = l.book)",
        (market, now),
    ).fetchall()
    by_prop = {}
    for player, home, away, line in rows:
        by_prop.setdefault((player, home, away), []).append(line)
    return {k: median(ls) for k, ls in by_prop.items()}


def player_index(conn, positions, since_season):
    index = {}
    rows = conn.execute(
        f"SELECT DISTINCT player_id, player_display_name FROM nfl_player_weeks"
        f" WHERE season >= ? AND position_group IN ({', '.join('?' * len(positions))})",
        (since_season, *positions),
    ).fetchall()
    for player_id, name in rows:
        index.setdefault(normalize(name), set()).add(player_id)
    return index


def evaluate(conn, market="player_pass_yds", now=None):
    """Returns (results, problems). problems lists props whose player could not be matched to nflverse."""
    stat, positions = MARKETS[market]
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    index = player_index(conn, positions, current_season() - 1)
    results, problems = [], []
    for (player, home, away), line in sorted(current_lines(conn, market, now).items()):
        ids = index.get(normalize(player), set())
        if len(ids) != 1:
            problems.append({"player": player, "reason": "no nflverse match" if not ids else f"ambiguous: {sorted(ids)}"})
            continue
        player_id = ids.pop()
        history = conn.execute(
            f'SELECT "{stat}", team FROM nfl_player_weeks WHERE player_id = ? ORDER BY season DESC, week DESC LIMIT ?',
            (player_id, BASELINE_GAMES),
        ).fetchall()
        games = [r[0] or 0 for r in history]
        team, teams = history[0][1], {TEAMS.get(home), TEAMS.get(away)}
        opponent = next(iter(teams - {team}), None) if team in teams else None
        result = {"player": player, "line": line, "games": len(games), "lean": None,
                  "flags": injuries.flags(conn, player, player_id, team, opponent, stat)}
        if opponent is None:
            result["flags"].append(f"last played for {team}, not in {away} @ {home}: check for a trade")
        results.append(result)
        if len(games) < MIN_GAMES:
            result["reason"] = f"only {len(games)} games"
            continue
        result.update(last_game=games[0], mean=round(mean(games), 1), median=median(games))
        # shortcut: compares the last game to today's line because we have no stored line for that game yet;
        # switch to the stored line once lines history covers a full week
        blew_past = games[0] >= line * BLOWOUT
        inflated = line >= result["mean"] * INFLATED and line >= result["median"] * INFLATED
        if blew_past and inflated:
            result["lean"] = "UNDER"
    return results, problems


if __name__ == "__main__":
    market = os.getenv("MARKET", "player_pass_yds")
    results, problems = evaluate(db.connect(os.getenv("DB_PATH", "betbot.db")), market)
    for r in results:
        detail = r.get("reason") or f"last {r['last_game']}, 20-game mean {r['mean']} / median {r['median']}"
        print(f"{r['lean'] or '-':5} {r['player']}: line {r['line']} ({detail})")
        for flag in r["flags"]:
            print(f"      ! {flag}")
    for p in problems:
        print(f"NO MATCH {p['player']}: {p['reason']}")
