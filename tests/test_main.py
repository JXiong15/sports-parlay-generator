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
