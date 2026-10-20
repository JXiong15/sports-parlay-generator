import pytest
import numpy as np
import pandas as pd

from betbot import db, decision
from betbot.nfl import game_factors, steam


def seed(conn, rows):
    conn.executemany("INSERT INTO game_odds (event_id, market, side, line, price, book, book_updated_at, fetched_at)"
                     " VALUES ('e', 'totals', 'Over', ?, -110, ?, ?, ?)", [(l, b, t, t) for b, l, t in rows])


def test_steam_counts_books_moving_together_and_names_the_first():
    conn = db.connect(":memory:")
    seed(conn, [("a", 45.5, "2026-10-19T10:00:00Z"), ("b", 45.5, "2026-10-19T10:00:00Z"),
                ("c", 45.5, "2026-10-19T10:00:00Z"), ("a", 46.5, "2026-10-19T11:00:00Z"),
                ("b", 46.5, "2026-10-19T11:05:00Z"), ("c", 46.5, "2026-10-19T11:10:00Z")])
    s = steam.game_steam(conn, "e", "totals", "Over", now=pd.Timestamp("2026-10-19T12:00:00Z").to_pydatetime())
    assert s["books"] == 3 and s["move"] == 1.0 and s["first_book"] == "a"
    under = steam.game_steam(conn, "e", "totals", "Under", now=pd.Timestamp("2026-10-19T12:00:00Z").to_pydatetime())
    assert under is None    # no Under rows were saved


def test_steam_moves_the_probability_with_or_against_the_pick():
    against = {"move": -1.0, "books": 4, "first_book": "x"}
    d = decision.decide(0.60, -110, -110, steam=against)
    assert abs(d["prob"] - 0.59) < 1e-9 and any("steam against" in r for r in d["reasons"])
    d = decision.decide(0.60, -110, -110, steam={"move": 1.0, "books": 4, "first_book": "x"})
    assert d["verdict"] == "BET" and abs(d["prob"] - 0.61) < 1e-9 and any("steam with" in r for r in d["reasons"])
    assert decision.decide(0.60, -110, -110, steam={"move": 1.0, "books": 2, "first_book": "x"})["prob"] == 0.60


def test_factor_fit_recovers_an_effect_and_apply_moves_the_prediction():
    rng = np.random.default_rng(0)
    n = 600
    rows = pd.DataFrame({k: rng.normal(size=n) for k in game_factors.MARGIN + game_factors.TOTAL})
    rows["season"] = 2020
    rows["pred_margin"], rows["pred_total"] = 0.0, 45.0
    rows["actual_margin"] = 3.0 * rows["rest_diff"] + rng.normal(0, 1, n)
    rows["actual_total"] = 45.0 - 2.0 * rows["wind"] + rng.normal(0, 1, n)
    c = game_factors.fit(rows)
    assert 2.5 < c["margin"]["rest_diff"] < 3.1 and -2.1 < c["total"]["wind"] < -1.7
    g = pd.DataFrame({"game_id": ["g"], "pred_margin": [1.0], "pred_total": [44.0], "pred_home": [22.5], "pred_away": [21.5]})
    f = pd.DataFrame({k: [0.0] for k in game_factors.MARGIN + game_factors.TOTAL}, index=["g"])
    f["rest_diff"] = 1.0
    out = game_factors.apply(g, f, c).iloc[0]
    assert out["pred_margin"] > 3.5 and abs(out["pred_home"] + out["pred_away"] - out["pred_total"]) < 1e-9


def test_public_discount_is_zero_without_a_signal_and_learns_a_real_one():
    from betbot.nfl import model
    rng = np.random.default_rng(1)
    n = 3000
    p = rng.uniform(0.3, 0.7, n)
    lean = rng.uniform(-1, 1, n)
    gap = np.zeros(n)
    flat = (rng.uniform(size=n) < p).astype(float)
    a, b = model.fit_public_discount(p, lean, gap, flat)
    assert abs(a) < 0.25
    boosted = (rng.uniform(size=n) < model.apply_public_discount(p, lean, gap, (-1.0, 0.0))).astype(float)
    a, _ = model.fit_public_discount(p, lean, gap, boosted)
    assert a < -0.6                      # the public's side loses: the discount finds it
    assert model.apply_public_discount(np.array([0.5]), np.array([1.0]), np.array([0.0]), (-1.0, 0.0))[0] < 0.5


def test_books_split_evenly_is_not_steam():
    conn = db.connect(":memory:")
    seed(conn, [("a", 45.5, "2026-10-19T10:00:00Z"), ("b", 45.5, "2026-10-19T10:00:00Z"),
                ("a", 46.5, "2026-10-19T11:00:00Z"), ("b", 44.5, "2026-10-19T11:00:00Z")])
    now = pd.Timestamp("2026-10-19T12:00:00Z").to_pydatetime()
    assert steam.game_steam(conn, "e", "totals", "Over", now=now) is None


def test_trust_learns_how_far_to_follow_the_model():
    import numpy as np, pandas as pd
    from betbot.nfl.game_factors import trust
    rng = np.random.default_rng(0)
    market = pd.Series(rng.normal(0, 5, 2000))
    pred = market + rng.normal(0, 3, 2000)
    assert trust(market + 0.5 * (pred - market), pred, market) == pytest.approx(0.5, abs=0.01)   # model half right
    assert trust(market + rng.normal(0, 3, 2000), pred, market) < 0.1                            # model is noise
    assert trust(market[:10], pred[:10], market[:10]) == 0.5                                     # too little history


def test_record_logs_errors_and_trust(tmp_path):
    from betbot import db
    from betbot.nfl.game_factors import record
    conn = db.connect(str(tmp_path / "t.db"))
    g = pd.DataFrame({"season": [2017, 2017, 2018], "actual_margin": [3.0, -7.0, np.nan], "actual_total": [40.0, 50.0, np.nan],
                      "pred_margin": [0.0, 0.0, 1.0], "blend_margin": [1.0, -1.0, 1.0], "market_margin": [2.0, -6.0, 1.0],
                      "pred_total": [45.0] * 3, "blend_total": [44.0] * 3, "market_total": [43.0] * 3,
                      "trust_margin": [0.0, 0.0, 0.1], "trust_total": [0.2, 0.2, 0.3]})
    record(conn, g, "2026-10-20T00:00:00Z")
    record(conn, g, "2026-10-20T05:00:00Z")   # same day: replaces, not duplicates
    assert conn.execute("SELECT count(*) FROM learning_log").fetchone()[0] == 1
    row = conn.execute("SELECT season, graded, model_margin_err, market_margin_err, trust_margin, trust_total FROM learning_log").fetchone()
    assert row[0] == 2017 and row[1] == 2 and row[2] == pytest.approx(np.sqrt((9 + 49) / 2)) and row[3] == pytest.approx(1.0)
    assert row[4] == 0.1 and row[5] == 0.3
