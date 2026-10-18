import numpy as np
import pandas as pd

from betbot import db
from betbot.nfl import backtest, game_model
from betbot.nfl.collectors.nflverse import replace_table


def test_metrics_hit_rate_roi_brier_and_buckets():
    # model favors side A at 60% on 4 games, side B (p=0.3) on 1; A won 3 of 4, B won its one
    m = backtest.metrics([0.6, 0.6, 0.6, 0.6, 0.3], [1, 1, 1, 0, 0])
    assert m["n"] == 5 and m["hit_rate"] == 0.8
    assert abs(m["roi"] - (4 * (100 / 110) - 1) / 5) < 1e-4  # four wins at -110, one loss
    assert abs(m["brier"] - np.mean((np.array([0.6] * 4 + [0.3]) - [1, 1, 1, 0, 0]) ** 2)) < 1e-4
    by = {c["bucket"]: c for c in m["calibration"]}
    assert by["60%-65%"]["n"] == 4 and by["60%-65%"]["actual"] == 0.75 and by["70%-100%"]["n"] == 1
    assert backtest.metrics([], [])["n"] == 0


def test_walk_forward_calibration_never_uses_the_same_or_later_seasons():
    seasons = [2000] * 600 + [2001] * 10
    raw = [0.8] * 610
    won = [1, 0] * 300 + [1] * 10   # 2000: an "80%" side that hit 50%
    out = backtest._walk_forward_calibration(seasons, raw, won)
    assert np.allclose(out[:600], 0.8)       # first season: nothing earlier, left raw
    assert np.all(out[600:] < 0.6)           # 2001: shrunk by what 2000 showed, not by 2001's own 10 wins


def test_game_model_uses_only_earlier_games_and_spread_signs():
    g = pd.DataFrame({"game_id": ["a", "b"], "season": [2020, 2020], "week": [1, 2],
                      "gameday": ["2020-09-10", "2020-09-17"], "home_team": ["X", "X"], "away_team": ["Y", "Y"],
                      "home_score": [40, None], "away_score": [10, None], "location": ["Home", "Home"]})
    p = game_model.predict(g)
    assert p.loc[0, "pred_margin"] == 2.0  # first game: league ratings + default home edge only
    assert p.loc[1, "pred_margin"] > 2.0   # second game learned from the first
    # home favored by 3 (-3) with an expected margin of 3: a coin flip
    assert abs(game_model.p_home_covers(3.0, -3.0, 13.5) - 0.5) < 1e-9
    assert game_model.p_home_covers(10.0, -3.0, 13.5) > 0.6 > game_model.p_over(40.0, 44.0, 13.5)


def test_clv_counts_line_moves_toward_our_side():
    conn = db.connect(":memory:")
    db.save_game_line(conn, {"game_id": "a", "book": "x", "open_home_spread": -3.0, "close_home_spread": -4.5,
                             "open_total": 44.0, "close_total": 45.0, "fetched_at": "t"})
    g = pd.DataFrame({"game_id": ["a"], "pred_margin": [8.0], "sigma_margin": [13.5], "pred_total": [50.0],
                      "sigma_total": [13.5]})
    clv = backtest.game_clv(conn, g)
    # we like home -3 and the over 44; the close was home -4.5 and 45: both moved our way
    assert clv["n"] == 1 and clv["spread_avg_points"] == 1.5 and clv["total_avg_points"] == 1.0
