"""Injury context: flags a prop when the player, his key teammates or the opponent's key defenders are missing.

Flags only; they don't move any numbers until a backtest says by how much.
"""
from betbot.names import normalize

PRACTICE = {
    "Did Not Participate In Practice": "did not practice",
    "Limited Participation in Practice": "was limited in practice",
    "Full Participation in Practice": "practiced fully",
}
MISSING = ("Out", "Doubtful", "Injured Reserve", "Suspension")
RECENT_GAMES = 3
# shortcut: untuned; a player counts as a starter at 60%+ of snaps over his team's last 3 games
STARTER_SHARE = 0.6
# stat -> (own-offense positions, opposing-defense positions) whose absence matters; nflverse snap-count positions
PASSING = ({"WR", "TE", "T", "OL"}, {"CB", "S", "DB", "DE", "DL", "LB"})
RECEIVING = ({"QB", "WR", "TE"}, {"CB", "S", "DB", "LB"})
KEY_POSITIONS = {
    "passing_yards": PASSING,
    "passing_tds": PASSING,
    "rushing_yards": ({"QB", "T", "G", "C", "OL"}, {"DT", "DL", "NT", "DE", "LB"}),
    "receptions": RECEIVING,
    "receiving_yards": RECEIVING,
}


def starters(conn, team, pct_column):
    """name_key -> position for players at STARTER_SHARE+ of snaps over the team's last RECENT_GAMES games.

    Long-term injuries drop out on their own: they have no recent snaps, and their absence is already in the baseline.
    """
    games = [g for (g,) in conn.execute(
        "SELECT game_id FROM nfl_snap_counts WHERE team = ? GROUP BY game_id"
        " ORDER BY max(season) DESC, max(week) DESC LIMIT ?",
        (team, RECENT_GAMES),
    )]
    if not games:
        return {}
    share = {}
    for name, pos, pct in conn.execute(
        f"SELECT player, position, {pct_column} FROM nfl_snap_counts"
        f" WHERE team = ? AND game_id IN ({', '.join('?' * len(games))})",
        (team, *games),
    ):
        key = normalize(name)
        share[key] = (pos, share.get(key, (pos, 0))[1] + (pct or 0))
    return {k: pos for k, (pos, total) in share.items() if total / len(games) >= STARTER_SHARE}


def _missing(conn, team):
    return conn.execute(
        f"SELECT name_key, player, status FROM nfl_injuries WHERE team = ? AND status IN ({', '.join('?' * len(MISSING))})",
        (team, *MISSING),
    ).fetchall()


def practice(conn, player_id):
    """(practice status, injury) from this week's official report, or None if he isn't on it for an injury."""
    row = conn.execute(
        "SELECT practice_status, coalesce(practice_primary_injury, report_primary_injury) FROM nfl_practice_reports"
        " WHERE gsis_id = ? AND (season, week) = (SELECT season, week FROM nfl_practice_reports"
        "  ORDER BY season DESC, week DESC LIMIT 1)",
        (player_id,),
    ).fetchone()
    if row and row[1] and not row[1].startswith("Not injury related"):
        return row
    return None


def flags(conn, player, player_id, team, opponent, stat):
    out = []
    own = conn.execute(
        "SELECT status FROM nfl_injuries WHERE team = ? AND name_key = ?", (team, normalize(player))
    ).fetchone()
    if own and own[0] != "Active":
        out.append(f"{player} is {own[0]}")
    # catches players with no game status who are still playing hurt
    hurt = practice(conn, player_id)
    if hurt:
        status, injury = hurt
        out.append(f"{player} {PRACTICE.get(status, status or 'is on the injury report')} this week ({injury})")
    own_positions, opp_positions = KEY_POSITIONS[stat]
    for side, side_team, pct_column, positions in (
        ("teammate", team, "offense_pct", own_positions),
        (f"{opponent} missing", opponent, "defense_pct", opp_positions),
    ):
        if side_team is None:
            continue
        starting = starters(conn, side_team, pct_column)
        for key, name, status in _missing(conn, side_team):
            if key != normalize(player) and starting.get(key) in positions:
                out.append(f"{side}: {starting[key]} {name} ({status})")
    return out
