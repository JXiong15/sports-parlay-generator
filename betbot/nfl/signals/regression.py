"""Player props: the model's probability of going Over (betbot/nfl/model.py) for every prop, plus the
recency / regression ("chasing yesterday") lean: Under when a big last game has pushed a line above his baseline."""
import os
from datetime import datetime, timezone
from statistics import mean, median, multimode

import pandas as pd

from betbot import db
from betbot.nfl import model
from betbot.nfl.collectors.nflverse import current_season
from betbot.names import normalize
from betbot.nfl.teams import TEAMS
from betbot.nfl.signals import injuries

# Odds API market -> (nflverse stat column, position groups that can hold the prop; used to split same-name players)
MARKETS = {
    "player_pass_yds": ("passing_yards", ("QB",)),
    "player_pass_tds": ("passing_tds", ("QB",)),
    "player_rush_yds": ("rushing_yards", ("QB", "RB", "WR", "TE")),
    "player_receptions": ("receptions", ("RB", "WR", "TE")),
    "player_reception_yds": ("receiving_yards", ("RB", "WR", "TE")),
    "player_pass_attempts": ("attempts", ("QB",)),
    "player_pass_completions": ("completions", ("QB",)),
    "player_pass_interceptions": ("passing_interceptions", ("QB",)),
    "player_rush_attempts": ("carries", ("QB", "RB", "WR", "TE")),
}

BASELINE_GAMES = 20
MIN_GAMES = 8
LINE_BOOK = "draftkings"  # same book as the ESPN and Action Network lines
# shortcut: untuned guesses; backtest against stored lines before trusting them
BLOWOUT = 1.25  # last game at least 25% over the line
INFLATED = 1.05  # line at least 5% above both the 20-game mean and median


def current_lines(conn, market, now):
    """{(player, home, away): {line, book, over_price, under_price}} from each book's latest prices, games not
    started. A real, bettable line: DraftKings', else the most common one (averaging books gives lines like 241.0
    nobody offers); prices are that book's Over and Under at that line."""
    rows = conn.execute(
        "SELECT l.player, e.home_team, e.away_team, l.book, l.side, l.line, l.price, e.id FROM lines l"
        " JOIN events e ON e.id = l.event_id"
        " WHERE l.market = ? AND l.side IN ('Over', 'Under') AND l.line IS NOT NULL AND e.commence_time > ?"
        " AND l.book_updated_at = (SELECT max(book_updated_at) FROM lines x WHERE x.event_id = l.event_id"
        "  AND x.market = l.market AND x.player = l.player AND x.side = l.side AND x.book = l.book)",
        (market, now),
    ).fetchall()
    by_prop = {}
    for player, home, away, book, side, line, price, event_id in rows:
        by_prop.setdefault((player, home, away), {}).setdefault(book, {})[side] = (line, price, event_id)
    out = {}
    for key, books in by_prop.items():
        overs = {b: v["Over"][0] for b, v in books.items() if "Over" in v}
        if not overs:
            continue
        # ties for most common go to the lower line
        book = LINE_BOOK if LINE_BOOK in overs else next(b for b, l in sorted(overs.items())
                                                           if l == min(multimode(overs.values())))
        line, over_price, event_id = books[book]["Over"]
        under = books[book].get("Under")
        out[key] = {"line": line, "book": book, "over_price": over_price, "event_id": event_id,
                    "under_price": under[1] if under and under[0] == line else None}
    return out


def find_game(conn, team, opponent, now):
    """nflverse game_id, season and week of the next game between these teams."""
    return conn.execute(
        "SELECT game_id, season, week FROM nfl_games WHERE ((home_team = ? AND away_team = ?)"
        " OR (home_team = ? AND away_team = ?)) AND gameday >= date(?, '-1 day') ORDER BY gameday LIMIT 1",
        (team, opponent, opponent, team, now[:10])).fetchone()


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
    """Returns (results, problems). problems lists props whose player could not be matched to nflverse.
    Each result has the model's p_over (probability of going Over the line) unless the player can't be modeled."""
    stat, positions = MARKETS[market]
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    index = player_index(conn, positions, current_season() - 1)
    results, problems, upcoming = [], [], []
    for (player, home, away), offer in sorted(current_lines(conn, market, now).items()):
        line = offer["line"]
        ids = index.get(normalize(player), set())
        if len(ids) != 1:
            problems.append({"player": player, "reason": "no nflverse match" if not ids else f"ambiguous: {sorted(ids)}"})
            continue
        player_id = ids.pop()
        history = conn.execute(
            f'SELECT "{stat}", team, position_group FROM nfl_player_weeks WHERE player_id = ?'
            f" ORDER BY season DESC, week DESC LIMIT ?",
            (player_id, BASELINE_GAMES),
        ).fetchall()
        games = [r[0] or 0 for r in history]
        team, teams = history[0][1], {TEAMS.get(home), TEAMS.get(away)}
        opponent = next(iter(teams - {team}), None) if team in teams else None
        result = {"player": player, "player_id": player_id, "market": market, "line": line, "games": len(games),
                  "lean": None, "p_over": None, "book": offer["book"], "over_price": offer["over_price"],
                  "under_price": offer["under_price"], "event_id": offer["event_id"], "game_id": None,
                  "flags": injuries.flags(conn, player, player_id, team, opponent, stat)}
        if opponent is None:
            result["flags"].append(f"last played for {team}, not in {away} @ {home}: check for a trade")
        game = find_game(conn, team, opponent, now) if opponent else None
        if game:
            result["game_id"] = game[0]
            upcoming.append({"player_id": player_id, "player": player, "position_group": history[0][2],
                             "season": game[1], "week": game[2], "game_id": game[0], "team": team,
                             "opponent_team": opponent})
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
    if upcoming:
        _add_probabilities(conn, stat, positions, results, pd.DataFrame(upcoming))
    return results, problems


def _add_probabilities(conn, stat, positions, results, upcoming):
    params = model.fitted(conn, stat, positions)
    df = model.predict(model.features(conn, stat, positions, upcoming), params)
    df = df[df["y"].isna()].set_index(["player_id", "game_id"])
    for r in results:
        key = (r["player_id"], r["game_id"])
        if r["game_id"] and key in df.index:
            row = df.loc[key]
            r["mu"], r["sigma"] = round(float(row["mu"]), 1), round(float(row["sigma"]), 1)
            r["p_over"] = round(float(model.prob_over(params, row["mu"], row["sigma"], r["line"])), 3)


if __name__ == "__main__":
    market = os.getenv("MARKET", "player_pass_yds")
    results, problems = evaluate(db.connect(os.getenv("DB_PATH", "betbot.db")), market)
    for r in results:
        detail = r.get("reason") or f"last {r['last_game']}, 20-game mean {r['mean']} / median {r['median']}"
        prob = f"P(Over) {r['p_over']:.0%}, expects {r['mu']}" if r["p_over"] is not None else "no model probability"
        print(f"{r['lean'] or '-':5} {r['player']}: line {r['line']} | {prob} ({detail})")
        for flag in r["flags"]:
            print(f"      ! {flag}")
    for p in problems:
        print(f"NO MATCH {p['player']}: {p['reason']}")
