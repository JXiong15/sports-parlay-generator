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


def test_steam_against_a_pick_vetoes_it_and_steam_with_it_is_noted():
    against = {"move": -1.0, "books": 4, "first_book": "x"}
    d = decision.decide(0.6, -110, -110, steam=against)
    assert d["market_veto"] and d["verdict"] == "STAY AWAY"
    d = decision.decide(0.6, -110, -110, steam={"move": 1.0, "books": 4, "first_book": "x"})
    assert d["verdict"] == "BET" and any("steam agrees" in r for r in d["reasons"])


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
