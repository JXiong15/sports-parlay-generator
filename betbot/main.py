import contextlib
import io
import os
import re
import traceback
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from betbot import db
from betbot.collectors import action_network, dk_network, odds_api
from betbot.nfl import board, conviction, grader, parlays, pocket
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


def _flag(name, default=False):
    """An on/off setting from .env: 1 / true / yes (unset or empty = default)."""
    value = os.getenv(name, "")
    return default if value == "" else value.lower() in ("1", "true", "yes")


MIN_REPEAT_HOURS = 0.25  # even FORCE_ODDS_PULL never repeats the same paid request within 15 minutes
PROBLEM_WORDS = re.compile(r"skipped|failed|missing|couldn't|could not|unknown|invalid|no readable|stopped at|"
                           r"not published|error|timed out|unavailable|blocked|forbidden|\b(?:403|429|500|503)\b|"
                           r"no data|no games|unexpected", re.IGNORECASE)


def problems(output):
    """The lines of a collector's hidden output that report something wrong, so short mode still shows them."""
    return [line.strip() for line in output.splitlines() if PROBLEM_WORDS.search(line)]


def should_pull_game_odds(hours_since_last, pulled_this_week, in_sunday_window, refresh_hours, no_paid, force):
    """Whether to spend 3 credits on game odds. Same priority as props: NO_PAID_REQUESTS always wins; FORCE_ODDS_PULL
    overrides everything except a repeat within 15 minutes; otherwise not within refresh_hours, and either not
    pulled yet this week or it's Sunday 11am-noon Central."""
    if no_paid:
        return False
    if hours_since_last is not None and hours_since_last < (MIN_REPEAT_HOURS if force else refresh_hours):
        return False
    return force or not pulled_this_week or in_sunday_window


def should_pull_props(mins_to_kickoff, hours_since_last, refresh_hours, no_paid, force):
    """Whether to spend a credit on one game's props. NO_PAID_REQUESTS always wins; FORCE_ODDS_PULL comes next.
    Otherwise skip if pulled within refresh_hours, except one pull in the last hour before kickoff (at most hourly)."""
    if no_paid:
        return False
    if hours_since_last is not None and hours_since_last < MIN_REPEAT_HOURS:
        return False  # even forced: no repeat within 15 minutes (a stray FORCE_ODDS_PULL in .env can't drain credits)
    if force:
        return True
    if 0 < mins_to_kickoff <= 60:
        # the last hour before kickoff: at least one pull, never stricter than the normal refresh window
        return hours_since_last is None or hours_since_last >= min(1, refresh_hours)
    return hours_since_last is None or hours_since_last >= refresh_hours


def collect():
    load_dotenv()
    api_key = os.environ["ODDS_API_KEY"]
    sport = os.getenv("SPORT", "americanfootball_nfl")
    market = os.getenv("MARKET", "player_pass_yds")
    if "," in market:
        raise SystemExit("MARKET takes one prop market per run (pulls are tracked per market)")
    conn = db.connect(os.getenv("DB_PATH", "betbot.db"))
    parlay_only = _flag("PARLAY_ONLY", default=True)  # short output; PARLAY_ONLY=0 shows everything
    if sport == "americanfootball_nfl" and nflverse.is_stale(conn):
        print("NFL stats are out of date, updating...")
        nflverse.load(conn)
    if sport == "americanfootball_nfl":
        steps = (("espn lines", lambda: espn.update(conn, espn.upcoming(conn))),
                 ("espn injuries", lambda: espn.update_injuries(conn)),
                 ("practice reports", lambda: nflverse.update_practice_reports(conn)),
                 ("action network", lambda: action_network.update(conn, "nfl")),
                 ("dk network", lambda: dk_network.update(conn, "nfl")),
                 ("weather", lambda: weather.update(conn)),
                 ("kalshi", lambda: print("kalshi: %d open NFL markets, %d prices changed" % kalshi.update(conn))))
        for name, step in steps:
            # a failed free source is reported and skipped; it never blocks the paid odds pull below
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out) if parlay_only else contextlib.nullcontext():
                    step()
            except Exception as e:
                print(f"{name} failed: {e.__class__.__name__}: {e}")
            for line in problems(out.getvalue()):  # short mode hides the chatter, not the warnings
                print(f"{name}: {line}")
        if not parlay_only:
            print_line_moves(conn)

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=24)
    fetched_at = now.isoformat(timespec="seconds")
    remaining = None
    # paid requests made within this many hours are skipped, so re-running soon after costs nothing
    refresh_hours = float(os.getenv("ODDS_REFRESH_HOURS", "2"))
    force_pull = _flag("FORCE_ODDS_PULL")  # pull now even if pulled recently or already this week
    no_paid = _flag("NO_PAID_REQUESTS")    # never spend credits this run (wins over FORCE_ODDS_PULL)
    credits_used = 0

    # game odds: once a week, plus a window at 11am-noon Central on Sundays
    ct = ZoneInfo("America/Chicago")
    now_ct = now.astimezone(ct)
    week_start = now_ct.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now_ct.weekday())
    game_kind = odds_api.game_odds_kind(sport)  # per sport, so an MLB run doesn't count as an NFL pull
    last = db.hours_since_pull(conn, game_kind, "", now)
    games = []
    if should_pull_game_odds(last, last is not None and (now - timedelta(hours=last)).astimezone(ct) >= week_start,
                             now_ct.weekday() == 6 and 11 <= now_ct.hour < 12, refresh_hours, no_paid, force_pull):
        games, remaining = odds_api.get_game_odds(api_key, sport)
        with conn:
            for event in games:
                db.save_event(conn, event)
            rows = odds_api.parse_game_odds(games, fetched_at)
            changed = db.save_game_odds(conn, rows)
            db.mark_seen(conn, "game_odds", rows, fetched_at)
            db.record_pull(conn, game_kind, "", fetched_at)
        credits_used += 3  # charged for the request (1 per market), even if no games come back
        print(f"game odds: {changed} prices changed across {len(games)} games (3 credits)")
    elif no_paid:
        print("NO_PAID_REQUESTS set: no paid Odds API requests this run")
    else:
        print("game odds: pulled recently or already this week, skipped (0 credits; FORCE_ODDS_PULL=1 to pull)")

    # props: games starting in the next 24 hours, 1 credit each
    to_pull, skipped = [], 0
    for event in odds_api.get_events(api_key, sport):
        start = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
        if not now < start < horizon:
            continue
        if should_pull_props((start - now).total_seconds() / 60, db.hours_since_pull(conn, market, event["id"], now),
                             refresh_hours, no_paid, force_pull):
            to_pull.append(event)
        else:
            skipped += 1
    for idx, event in enumerate(to_pull, start=1):
        odds, remaining = odds_api.get_event_odds(api_key, sport, event["id"], market)
        with conn:
            db.save_event(conn, event)
            rows = odds_api.parse_props(odds, fetched_at)
            changed = db.save_lines(conn, rows)
            db.mark_seen(conn, "lines", rows, fetched_at)
            db.record_pull(conn, market, event["id"], fetched_at)
        credits_used += 1
        print(f"props {idx}/{len(to_pull)}: {event['away_team']} @ {event['home_team']}: {changed} lines changed (1 credit)")
    print(f"credits used: {credits_used}; remaining: {remaining if remaining is not None else 'unchanged'};"
          f" props pulled for {len(to_pull)} games, {skipped} skipped")

    if sport == "americanfootball_nfl":
        graded = grader.grade(conn)  # first: doesn't depend on the board
        if not parlay_only:
            print(f"graded: {graded} finished picks")
        try:
            picks = board.build(conn)
            props_logged, games_logged = board.log(conn, picks)
        except Exception as e:  # the data pulled above is saved either way
            print(f"board failed: {e.__class__.__name__}: {e}")
            traceback.print_exc()  # a code bug shows up here with its traceback, not just one line
            return
        print(f"predictions: {props_logged} prop and {games_logged} game picks logged")
        if not parlay_only:  # the full board and the EV-ranked parlays are for debugging
            board.print_board(picks)
            try:
                results, available = parlays.build(conn, picks, max_legs=2)
                parlays.print_parlays(results, available)
                parlays.log(conn, results)
            except Exception as e:
                print(f"parlays failed: {e.__class__.__name__}: {e}")
                traceback.print_exc()
        try:
            # the default view: only props scoring 95+ with a hit rate that beats the book, and parlays from them
            chosen = conviction.run(conn, picks)
            conviction.log_picks(conn, chosen)
            if not chosen:  # nothing cleared the bar: still show the least-bad picks, labeled
                available, results = parlays.best_available(conn, picks)
                parlays.print_best_available(available, results)
                parlays.log(conn, results)
        except Exception as e:
            print(f"conviction failed: {e.__class__.__name__}: {e}")
            traceback.print_exc()
        try:
            pocket.run(conn, picks)
            n_graded, wins = pocket.record(conn)
            print(f"pocket rule so far: {wins}/{n_graded} graded picks won")
        except Exception as e:
            print(f"pocket failed: {e.__class__.__name__}: {e}")
            traceback.print_exc()


if __name__ == "__main__":
    collect()
