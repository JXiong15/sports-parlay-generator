import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from betbot import db
from betbot.collectors import odds_api
from betbot.nfl.collectors import espn, nflverse


def print_line_moves(conn):
    rows = conn.execute(
        "SELECT g.away_team, g.home_team, l.open_total, l.current_total, l.open_home_spread, l.current_home_spread"
        " FROM nfl_game_lines l JOIN nfl_games g USING (game_id)"
        " WHERE g.gameday >= date('now', 'localtime') ORDER BY g.gameday, g.gametime"
    ).fetchall()
    for away, home, ot, ct, os_, cs in rows:
        total = f"total {ot} -> {ct}" + (f" ({ct - ot:+g})" if None not in (ot, ct) and ct != ot else "")
        spread = f"{home} spread {os_:+g} -> {cs:+g}" if None not in (os_, cs) else f"{home} spread n/a"
        print(f"{away} @ {home}: {total}, {spread}")


def collect():
    load_dotenv()
    api_key = os.environ["ODDS_API_KEY"]
    sport = os.getenv("SPORT", "americanfootball_nfl")
    market = os.getenv("MARKET", "player_pass_yds")
    conn = db.connect(os.getenv("DB_PATH", "betbot.db"))
    if sport == "americanfootball_nfl" and nflverse.is_stale(conn):
        print("NFL stats are out of date, updating...")
        nflverse.load(conn)
    if sport == "americanfootball_nfl":
        espn.update(conn, espn.upcoming(conn))
        espn.update_injuries(conn)
        nflverse.update_practice_reports(conn)
        print_line_moves(conn)

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=24)
    fetched_at = now.isoformat(timespec="seconds")
    remaining = None
    # skip live games and anything beyond 24h: each event costs a credit
    for event in odds_api.get_events(api_key, sport):
        start = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
        if not now < start < horizon:
            continue
        odds, remaining = odds_api.get_event_odds(api_key, sport, event["id"], market)
        with conn:
            db.save_event(conn, event)
            added = db.save_lines(conn, odds_api.parse_props(odds, fetched_at))
        print(f"{event['away_team']} @ {event['home_team']}: {added} new lines")
    print(f"credits remaining: {remaining}")


if __name__ == "__main__":
    collect()
