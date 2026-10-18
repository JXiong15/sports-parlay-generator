from betbot import db, predictions
from betbot.nfl import grader
from betbot.nfl.collectors.nflverse import replace_table

GAME = ["game_id", "home_team", "away_team", "home_score", "away_score", "spread_line", "total_line",
        "home_spread_odds", "away_spread_odds", "over_odds", "under_odds", "home_moneyline", "away_moneyline"]


def make_db(home_score=27, away_score=20):
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_games", GAME, [["g1", "GB", "CHI", home_score, away_score, 3.0, 44.5,
                                             -110, -110, -105, -115, -160, 135]])
    replace_table(conn, "nfl_player_weeks", ["player_id", "game_id", "passing_yards"], [["qb", "g1", 251]])
    db.save_event(conn, {"id": "e1", "sport_key": "americanfootball_nfl", "commence_time": "2026-10-18T17:00:00Z"})
    line = lambda side, l, price, t: dict(event_id="e1", market="player_pass_yds", player="Q B", side=side, line=l,
                                          price=price, book="draftkings", book_updated_at=t, fetched_at=t)
    db.save_lines(conn, [line("Over", 245.5, -110, "2026-10-17T10:00:00+00:00"),
                         line("Under", 245.5, -110, "2026-10-17T10:00:00+00:00"),
                         line("Over", 248.5, -115, "2026-10-18T16:30:00+00:00"),   # closing
                         line("Under", 248.5, -105, "2026-10-18T16:30:00+00:00"),
                         line("Over", 260.5, -110, "2026-10-18T18:00:00+00:00")])  # in-game: ignored
    return conn


def prop(p_over=0.6, line=245.5):
    return {"player": "Q B", "player_id": "qb", "market": "player_pass_yds", "line": line, "book": "draftkings",
            "over_price": -110, "under_price": -110, "event_id": "e1", "game_id": "g1", "p_over": p_over, "mu": 255}


def row(conn, cols):
    return conn.execute(f"SELECT {cols} FROM predictions").fetchall()


def test_prop_logged_once_and_graded_with_closing_value():
    conn = make_db()
    assert predictions.log_props(conn, [prop()]) == 1
    assert predictions.log_props(conn, [prop(p_over=0.65)]) == 0  # same pick, same line: first one kept
    assert row(conn, "side, model_prob, implied_prob, vig_removed") == [("Over", 0.6, 0.5, 1)]
    assert grader.grade(conn) == 1
    (actual, result, cl, cp, clv, pts), = row(conn, "actual, result, closing_line, closing_price, clv, clv_points")
    assert (actual, result, cl, cp) == (251, "win", 248.5, -115)
    # the line moved 245.5 -> 248.5: we beat the close by 3 yards; probabilities at different lines aren't compared
    assert pts == 3.0 and clv is None
    assert grader.grade(conn) == 0  # already graded


def test_under_pick_dnp_void_and_unplayed_game():
    conn = make_db(home_score=None, away_score=None)
    predictions.log_props(conn, [prop(p_over=0.3)])
    assert row(conn, "side, round(model_prob, 2)") == [("Under", 0.7)]
    assert grader.grade(conn) == 0  # no final score yet
    conn = make_db()
    other = dict(prop(), player_id="backup", player="Back Up")
    predictions.log_props(conn, [other])
    grader.grade(conn)
    assert row(conn, "result") == [("void",)]  # stats are out, he isn't in them, and he took no snaps


def test_played_with_zero_stats_is_a_loss_not_a_void():
    conn = make_db()
    replace_table(conn, "nfl_players", ["gsis_id", "pfr_id"], [["wr", "PfrWr"]])
    replace_table(conn, "nfl_snap_counts", ["game_id", "pfr_player_id", "offense_snaps", "st_snaps"],
                  [["g1", "PfrWr", 22, 0]])
    predictions.log_props(conn, [dict(prop(), player_id="wr", player="W R", line=35.5)])
    grader.grade(conn)
    assert row(conn, "actual, result") == [(0, "loss")]  # played 22 snaps, caught nothing: a real loss


def test_side_flip_or_line_move_is_not_a_second_pick():
    conn = make_db()
    assert predictions.log_props(conn, [prop(p_over=0.51)]) == 1
    assert predictions.log_props(conn, [prop(p_over=0.49)]) == 0  # now favors the Under: same prop, not a new bet
    assert predictions.log_props(conn, [prop(line=248.5)]) == 0   # line moved: same prop
    assert row(conn, "side, line") == [("Over", 245.5)]


def test_probability_clv_at_the_same_line():
    conn = make_db()
    db.save_lines(conn, [dict(event_id="e1", market="player_pass_yds", player="Q B", side=s, line=248.5, price=pr,
                              book="draftkings", book_updated_at="2026-10-18T16:45:00+00:00",
                              fetched_at="2026-10-18T16:45:00+00:00") for s, pr in (("Over", -110), ("Under", -110))])
    predictions.log_props(conn, [dict(prop(), line=248.5, over_price=100, under_price=-120)])
    grader.grade(conn)
    (clv, pts), = row(conn, "round(clv, 4), clv_points")
    assert pts == 0 and clv > 0  # bought the Over at +100, closed -110: same line, better price


def test_game_picks_spread_total_moneyline():
    conn = make_db(home_score=27, away_score=20)  # GB by 7
    predictions.log_games(conn, [
        {"game_id": "g1", "market": "spread", "side": "away", "line": 7.0, "price": -110, "other_price": -110,
         "model_prob": 0.55, "book": "x"},
        {"game_id": "g1", "market": "total", "side": "under", "line": 44.5, "price": -110, "other_price": -110,
         "model_prob": 0.56, "book": "x"},
        {"game_id": "g1", "market": "moneyline", "side": "home", "line": None, "price": -160, "other_price": 135,
         "model_prob": 0.66, "book": "x"}])
    predictions.log_games(conn, [{"game_id": "g1", "market": "total", "side": "under", "line": 44.5, "price": -110,
                                  "other_price": -110, "model_prob": 0.56, "book": "x"}])  # duplicate: ignored
    assert grader.grade(conn) == 3
    got = {m: (a, r, cl) for m, a, r, cl in row(conn, "market, actual, result, closing_line")}
    assert got["spread"] == (-7, "push", 3.0)  # away +7 lost by exactly 7; nflverse close: home -3 -> away +3
    assert got["total"] == (47, "loss", 44.5)
    assert got["moneyline"] == (7, "win", None)


def test_moneyline_picks_are_not_logged_twice():
    conn = make_db()
    pick = {"game_id": "g1", "market": "moneyline", "side": "home", "line": None, "price": -160, "other_price": 135,
            "model_prob": 0.66, "book": "x"}
    assert predictions.log_games(conn, [pick]) == 1
    assert predictions.log_games(conn, [pick]) == 0
