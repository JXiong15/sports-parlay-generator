from betbot.main import should_pull_props


def test_no_paid_requests_never_pays():
    assert not should_pull_props(600, None, 2, no_paid=True, force=False)  # never pulled: still no
    assert not should_pull_props(30, None, 2, no_paid=True, force=True)    # not even forced


def test_force_refresh_window_and_last_hour():
    assert should_pull_props(600, 0.5, 2, no_paid=False, force=True)
    assert not should_pull_props(600, 0.5, 2, no_paid=False, force=False)  # pulled 30 min ago
    assert should_pull_props(600, 3.0, 2, no_paid=False, force=False)
    assert should_pull_props(600, None, 2, no_paid=False, force=False)
    # last hour before kickoff: one more pull if the last was an hour or more ago, even inside refresh_hours
    assert should_pull_props(45, 1.5, 2, no_paid=False, force=False)
    assert not should_pull_props(45, 0.5, 2, no_paid=False, force=False)


def test_game_odds_rule_matches_the_props_priority():
    from betbot.main import should_pull_game_odds as pull
    assert not pull(None, False, False, 2, no_paid=True, force=True)   # NO_PAID_REQUESTS always wins
    assert pull(0.5, True, False, 2, no_paid=False, force=True)        # forced: even right after a pull
    assert not pull(0.5, False, True, 2, no_paid=False, force=False)   # within refresh_hours: skip
    assert pull(None, False, False, 2, no_paid=False, force=False)     # never pulled
    assert not pull(30, True, False, 2, no_paid=False, force=False)    # already this week
    assert pull(30, True, True, 2, no_paid=False, force=False)         # ...except Sunday 11am-noon Central


def test_last_hour_never_stricter_than_refresh_window():
    assert should_pull_props(45, 0.6, 0.5, no_paid=False, force=False)  # refresh 30 min, last pull 36 min ago


def test_short_mode_still_shows_warnings():
    from betbot.main import problems
    out = ("saved 82 props\n  kalshi KXNFLGAME skipped: ConnectionError\nkalshi: 10 open markets\n"
           "  kalshi KXNFLREC: stopped at 50 pages, some markets may be missing")
    assert problems(out) == ["kalshi KXNFLGAME skipped: ConnectionError",
                             "kalshi KXNFLREC: stopped at 50 pages, some markets may be missing"]


def test_forced_pulls_still_wait_15_minutes():
    from betbot.main import should_pull_game_odds as game
    assert not should_pull_props(600, 0.1, 2, no_paid=False, force=True)    # pulled 6 minutes ago
    assert should_pull_props(600, 0.3, 2, no_paid=False, force=True)        # 18 minutes ago: forced pull allowed
    assert not game(0.1, True, False, 2, no_paid=False, force=True)
    assert game(0.3, True, False, 2, no_paid=False, force=True)


def test_more_failure_wording_is_shown():
    from betbot.main import problems
    out = "action network: HTTP 403 forbidden\nespn: request timed out\nsaved 82 props"
    assert problems(out) == ["action network: HTTP 403 forbidden", "espn: request timed out"]


def test_status_codes_match_as_whole_words_only():
    from betbot.main import problems
    assert problems("espn: 14290 rows\nweather: 0.0429\nweather: HTTP 429 too many requests") == [
        "weather: HTTP 429 too many requests"]
    assert problems("kalshi: could not parse a market") == ["kalshi: could not parse a market"]


def test_game_odds_refresh_when_a_game_starts_within_the_hour_but_not_within_refresh_window():
    from betbot.main import should_pull_game_odds as game
    assert game(30, True, False, 2, no_paid=False, force=False, game_soon=True)      # stale week-old line, game within the hour
    assert not game(1, True, False, 2, no_paid=False, force=False, game_soon=True)   # pulled an hour ago
    assert not game(30, True, False, 2, no_paid=True, force=False, game_soon=True)
    assert not game(30, True, False, 2, no_paid=False, force=False, game_soon=False)


def test_saved_odds_age_hours():
    import sqlite3
    from datetime import datetime, timedelta, timezone
    from betbot.nfl.run_picks import MARKETS, saved_odds_age_hours
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE odds_pulls (kind TEXT, event_id TEXT, fetched_at TEXT)")
    assert saved_odds_age_hours(conn) is None
    ago = lambda h: (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat()
    conn.executemany("INSERT INTO odds_pulls VALUES (?, 'e', ?)", [(m, ago(1)) for m in MARKETS[:-1]])
    assert saved_odds_age_hours(conn) is None  # one market never pulled
    conn.execute("INSERT INTO odds_pulls VALUES (?, 'e', ?)", (MARKETS[-1], ago(3)))
    assert 2.9 < saved_odds_age_hours(conn) < 3.1


def test_only_the_players_own_injury_flags_count():
    from betbot.nfl.run_picks import confidence, usable
    pick = lambda *flags: {"player": "A B", "market": "player_rush_yds", "line": 50, "event_id": "e", "flags": list(flags),
                           "decision": {"fair": 0.5, "prob": 0.6, "ev": 0.1}}
    assert usable(pick("teammate: WR C D (Out)", "NYJ missing: CB E F (Out)"), {"e"})
    assert not usable(pick("A B is Out"), {"e"})
    assert confidence(pick("teammate: WR C D (Questionable)")) > confidence(pick("A B is Questionable"))


def test_receiving_under_dropped_when_a_wr_te_teammate_is_out():
    from betbot.nfl.run_picks import usable
    pick = lambda side, flag, market="player_receptions": {"player": "A B", "market": market, "side": side, "line": 5.5, "event_id": "e",
                               "flags": [flag], "decision": {"fair": 0.5, "prob": 0.6, "ev": 0.1}}
    assert not usable(pick("Under", "teammate: WR C D (Out)"), {"e"})
    assert not usable(pick("Under", "teammate: TE C D (Suspension)", "player_reception_yds"), {"e"})
    assert usable(pick("Under", "teammate: WR C D (Questionable)"), {"e"})
    assert usable(pick("Under", "teammate: WR C D (Out)", "player_rush_yds"), {"e"})
    assert usable(pick("Over", "teammate: WR C D (Out)"), {"e"})
    assert usable(pick("Under", "teammate: T C D (Out)"), {"e"})
