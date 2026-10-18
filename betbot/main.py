import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from betbot import db
from betbot.collectors import action_network, dk_network, odds_api
from betbot.nfl import board, grader, parlays
from betbot.nfl.collectors import espn, kalshi, nflverse, weather


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
        action_network.update(conn, "nfl")
        dk_network.update(conn, "nfl")
        weather.update(conn)
        print("kalshi: %d open NFL markets, %d prices changed" % kalshi.update(conn))
        print_line_moves(conn)

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=24)
    fetched_at = now.isoformat(timespec="seconds")
    remaining = None
    # paid requests made within this many hours are skipped, so re-running soon after costs nothing
    refresh_hours = float(os.getenv("ODDS_REFRESH_HOURS", "2"))
    fresh = lambda kind, event_id: (lambda h: h is not None and h < refresh_hours)(
        db.hours_since_pull(conn, kind, event_id, now))

    if fresh("game_odds", ""):
        print(f"game odds: pulled less than {refresh_hours:g}h ago, skipped (0 credits)")
    else:
        games, remaining = odds_api.get_game_odds(api_key, sport)
        with conn:
            for event in games:
                db.save_event(conn, event)
            changed = db.save_game_odds(conn, odds_api.parse_game_odds(games, fetched_at))
            db.record_pull(conn, "game_odds", "", fetched_at)
        print(f"game odds: {changed} prices changed across {len(games)} games (3 credits)")

    # skip live games and anything beyond 24h: each event costs a credit
    skipped = 0
    for event in odds_api.get_events(api_key, sport):
        start = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
        if not now < start < horizon:
            continue
        if fresh(market, event["id"]):
            skipped += 1
            continue
        odds, remaining = odds_api.get_event_odds(api_key, sport, event["id"], market)
        with conn:
            db.save_event(conn, event)
            changed = db.save_lines(conn, odds_api.parse_props(odds, fetched_at))
            db.record_pull(conn, market, event["id"], fetched_at)
        print(f"{event['away_team']} @ {event['home_team']}: {changed} lines changed (1 credit)")
    if skipped:
        print(f"props: {skipped} games pulled less than {refresh_hours:g}h ago, skipped (0 credits)")
    print(f"credits remaining: {remaining if remaining is not None else 'unchanged (no paid requests)'}")

    if sport == "americanfootball_nfl":
        picks = board.build(conn)
        props_logged, games_logged = board.log(conn, picks)
        print(f"predictions: {props_logged} prop and {games_logged} game picks logged")
        print(f"graded: {grader.grade(conn)} finished picks")
        board.print_board(picks)
        parlays.print_parlays(*parlays.build(conn, picks))


if __name__ == "__main__":
    collect()
