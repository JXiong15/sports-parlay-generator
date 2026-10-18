import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
    if "," in market:
        raise SystemExit("MARKET takes one prop market per run (pulls are tracked per market)")
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
        try:
            print("kalshi: %d open NFL markets, %d prices changed" % kalshi.update(conn))
        except Exception as e:  # e.g. a database error: report it, but never block the paid odds pull below
            print(f"kalshi failed: {e.__class__.__name__}: {e}")
        print_line_moves(conn)

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=24)
    fetched_at = now.isoformat(timespec="seconds")
    remaining = None
    # paid requests made within this many hours are skipped, so re-running soon after costs nothing
    refresh_hours = float(os.getenv("ODDS_REFRESH_HOURS", "2"))

    # Central Time helpers: allow one weekly run plus a window at 11am CT on Sundays
    ct = ZoneInfo("America/Chicago")
    now_ct = now.astimezone(ct)
    start_of_week_ct = (now_ct.replace(hour=0, minute=0, second=0, microsecond=0)
                        - timedelta(days=now_ct.weekday()))

    def pulled_this_week(kind, event_id):
        h = db.hours_since_pull(conn, kind, event_id, now)
        if h is None:
            return False
        last = now - timedelta(hours=h)
        return last.astimezone(ct) >= start_of_week_ct

    def fresh(kind, event_id):
        """Return True when the last pull was recent enough to skip entirely (< refresh_hours).
        Weekly and Sunday-11am logic is handled separately."""
        h = db.hours_since_pull(conn, kind, event_id, now)
        return h is not None and h < refresh_hours

    # Manual override: set FORCE_ODDS_PULL=1 (or true/yes) to force paid pulls now
    force_pull = os.getenv("FORCE_ODDS_PULL", "").lower() in ("1", "true", "yes")
    # When set, skip all paid Odds API requests (safe for dry runs)
    no_paid = os.getenv("NO_PAID_REQUESTS", "").lower() in ("1", "true", "yes")

    game_kind = odds_api.game_odds_kind(sport)  # per sport, so an MLB run doesn't count as an NFL pull
    # Decide whether to run the paid game-odds request:
    # - skip if we pulled very recently (< refresh_hours)
    # - otherwise, allow if we haven't pulled this week OR it's Sunday 11am CT, or if forced
    in_sunday_11am_window = now_ct.weekday() == 6 and 11 <= now_ct.hour < 12
    if no_paid:
        print("NO_PAID_REQUESTS set: skipping all paid Odds API requests (game odds + props)")
        games = []
        remaining = None
    elif fresh(game_kind, ""):
        print(f"game odds: pulled less than {refresh_hours:g}h ago, skipped (0 credits)")
        games = []
    elif pulled_this_week(game_kind, "") and not (in_sunday_11am_window or force_pull):
        print("game odds: already pulled this week, skipping until Sunday 11am CT (or set FORCE_ODDS_PULL=1)")
        games = []
    else:
        games, remaining = odds_api.get_game_odds(api_key, sport)
        with conn:
            for event in games:
                db.save_event(conn, event)
            rows = odds_api.parse_game_odds(games, fetched_at)
            changed = db.save_game_odds(conn, rows)
            db.mark_seen(conn, "game_odds", rows, fetched_at)
            db.record_pull(conn, game_kind, "", fetched_at)
        print(f"game odds: {changed} prices changed across {len(games)} games (3 credits)")

    # skip live games and anything beyond 24h: each event costs a credit
    skipped = 0
    for event in odds_api.get_events(api_key, sport):
        start = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
        if not now < start < horizon:
            continue
        # minutes until kickoff
        mins_to_kick = (start - now).total_seconds() / 60
        within_one_hour = 0 < mins_to_kick <= 60

        # when the event was last pulled (hours); None if never
        last_h = db.hours_since_pull(conn, market, event["id"], now)
        last_mins = None if last_h is None else last_h * 60

        allow_pull = False
        if force_pull:
            allow_pull = True
        elif no_paid:
            # default: skip paid requests unless it hasn't been pulled this week, or it's within one hour
            if not pulled_this_week(market, event["id"]):
                allow_pull = True
            elif within_one_hour and (last_mins is None or last_mins >= 60):
                allow_pull = True
            else:
                allow_pull = False
        else:
            # paid allowed normally: skip if pulled recently (< refresh_hours), except allow one pull within the hour
            if within_one_hour:
                # allow only if not pulled in the last 60 minutes
                allow_pull = last_mins is None or last_mins >= 60
            else:
                allow_pull = not fresh(market, event["id"])  # respect refresh_hours

        if not allow_pull:
            skipped += 1
            continue

        odds, remaining = odds_api.get_event_odds(api_key, sport, event["id"], market)
        with conn:
            db.save_event(conn, event)
            rows = odds_api.parse_props(odds, fetched_at)
            changed = db.save_lines(conn, rows)
            db.mark_seen(conn, "lines", rows, fetched_at)
            db.record_pull(conn, market, event["id"], fetched_at)
        print(f"{event['away_team']} @ {event['home_team']}: {changed} lines changed (1 credit)")
    if skipped:
        print(f"props: {skipped} games skipped (no paid request)")
    print(f"credits remaining: {remaining if remaining is not None else 'unchanged (no paid requests)'}")

    if sport == "americanfootball_nfl":
        print(f"graded: {grader.grade(conn)} finished picks")  # first: doesn't depend on the board
        picks = board.build(conn)
        props_logged, games_logged = board.log(conn, picks)
        print(f"predictions: {props_logged} prop and {games_logged} game picks logged")
        board.print_board(picks)
        parlays.print_parlays(*parlays.build(conn, picks))


if __name__ == "__main__":
    collect()
