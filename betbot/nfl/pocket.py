"""Forward tracker for a learned rule: pick the kinds of props (market, side, confidence) that have beaten the price
so far. Each week the rule is re-learned from every earlier would-be pick on real DraftKings lines; today's picks are
logged (kind 'pocket') before the games and graded like any other, so the rule is judged only on games it never saw.

A rolling replay of 2023-2026 gave 50% on the 2023-25 picks and 68% on 2026's; this tracker tests whether that holds.
"""
import numpy as np
import pandas as pd

from betbot import db
from betbot.nfl import backtest, model
from betbot.nfl.signals.regression import MARKETS
from betbot.odds import no_vig

TABLE_KEY = "_pocket"
MIN_CELL, MIN_HIT = 40, 0.55          # a kind of pick needs 40+ earlier picks hitting above 55% (-110 breaks even at 52.4%)
EDGES = [-1, 0.01, 0.02, 0.04, 1]     # confidence buckets: how far the recalibrated probability is from 50%


def cell(market, p):
    """The kind of pick for a recalibrated P(Over): market | side | confidence bucket."""
    bucket = int(np.searchsorted(EDGES, round(abs(p - 0.5), 9), side="left") - 1)
    return f"{market}|{'over' if p > 0.5 else 'under'}|{max(bucket, 0)}"


def learn(rows):
    """(calibration, good cells) from played real-line props. Pure: `rows` is backtest.real_rows' DataFrame."""
    if rows.empty:
        return (0.0, 1.0), {}
    calib = model.fit_recalibration(rows["p_over"], rows["over"])
    p = model.recalibrate(rows["p_over"].values, calib)
    hit = np.where(p > 0.5, rows["over"], 1 - rows["over"])
    stats = pd.DataFrame({"cell": [cell(m, x) for m, x in zip(rows["market"], p)], "hit": hit}).groupby("cell")["hit"]
    stats = stats.agg(["size", "mean"])
    good = stats[(stats["size"] >= MIN_CELL) & (stats["mean"] > MIN_HIT)]
    return calib, {c: {"n": int(r["size"]), "hit": round(float(r["mean"]), 4)} for c, r in good.iterrows()}


def ensure_tables(conn):
    """The stored rule, relearned when a new week of games has arrived (takes a few minutes: it refits the model)."""
    stored = model.load_params(conn, TABLE_KEY)
    version = model.latest_week(conn)
    if stored and stored["version"] == version:
        return stored
    calib, good = learn(backtest.real_rows(conn))
    stored = {"stat": TABLE_KEY, "version": version, "calib": calib, "cells": good}
    model.save_params(conn, stored, version)
    return stored


def select(picks, tables):
    """The board's prop picks whose kind has been a winner, on the side the recalibrated probability favors."""
    out = []
    for p in picks:
        if p.get("kind") == "game" or p.get("market") not in MARKETS or p.get("p_over_model") is None:
            continue
        prob = float(model.recalibrate(np.array([p["p_over_model"]]), tables["calib"])[0])
        info = tables["cells"].get(cell(p["market"], prob))
        side = "Over" if prob > 0.5 else "Under"
        s = next((x for x in p.get("sides", []) if x["side"] == side), None)
        if info and s and s.get("price") is not None:
            out.append(dict(p, side=side, price=s["price"], other_price=s.get("other_price"), hit=info["hit"],
                            hit_n=info["n"]))
    return out


def log(conn, chosen, made_at=None):
    from datetime import datetime, timezone
    made_at = made_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [{"kind": "pocket", "made_at": made_at, "game_id": p["game_id"], "event_id": p.get("event_id"),
             "player": p["player"], "player_id": p["player_id"], "market": p["market"], "side": p["side"],
             "line": p["line"], "price": p["price"], "other_price": p["other_price"], "book": p["book"],
             "model_prob": p["hit"], "implied_prob": no_vig(p["price"], p["other_price"])[0], "vig_removed": int(p["other_price"] is not None),
             "p_over": p["p_over_model"], "mu": None, "conviction": None} for p in chosen]
    with conn:
        return db.save_predictions(conn, rows)


def run(conn, picks):
    chosen = select(picks, ensure_tables(conn))
    print(f"pocket rule: {len(chosen)} picks logged for tracking (not recommendations; judged on games it hasn't seen)")
    log(conn, chosen)
    return chosen


def record(conn):
    """(picks graded, wins) for the tracker so far."""
    row = conn.execute("SELECT count(*), sum(result = 'win') FROM predictions WHERE kind = 'pocket'"
                       " AND result IN ('win', 'loss')").fetchone()
    return row[0], row[1] or 0
