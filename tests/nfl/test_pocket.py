import numpy as np
import pandas as pd

from betbot import db
from betbot.nfl import pocket


def test_cell_names_the_market_side_and_confidence():
    assert pocket.cell("player_rush_yds", 0.515) == "player_rush_yds|over|1"
    assert pocket.cell("player_rush_yds", 0.45) == "player_rush_yds|under|3"
    assert pocket.cell("player_rush_yds", 0.50) == "player_rush_yds|under|0"


def rows(n, hit_rate):
    over = np.array([1] * int(n * hit_rate) + [0] * (n - int(n * hit_rate)))
    return pd.DataFrame({"market": "player_rush_yds", "p_over": 0.41, "over": 1 - over})   # all Unders; `over`=0 is a hit


def test_learn_keeps_only_kinds_with_enough_picks_that_beat_the_price():
    _, good = pocket.learn(rows(60, 0.70))
    assert list(good.values())[0]["n"] == 60 and list(good.values())[0]["hit"] > 0.55
    assert pocket.learn(rows(60, 0.50))[1] == {}              # a coin flip: no rule
    assert pocket.learn(rows(20, 0.90))[1] == {}              # too few picks to trust


def test_select_and_log_picks_the_favored_side_once():
    conn = db.connect(":memory:")
    calib, good = pocket.learn(rows(60, 0.70))
    pick = {"market": "player_rush_yds", "p_over_model": 0.41, "player": "A B", "player_id": "ab", "game_id": "g",
            "event_id": "e", "line": 40.5, "book": "dk",
            "sides": [{"side": "Over", "price": -110, "other_price": -110},
                      {"side": "Under", "price": -105, "other_price": -115}]}
    chosen = pocket.select([pick, dict(pick, p_over_model=None), dict(pick, kind="game")],
                           {"calib": calib, "cells": good})
    assert len(chosen) == 1 and chosen[0]["side"] == "Under" and chosen[0]["price"] == -105
    assert pocket.log(conn, chosen) == 1 and pocket.log(conn, chosen) == 0
    assert pocket.record(conn) == (0, 0)                       # nothing graded yet


def test_learn_with_no_rows_gives_no_rule_instead_of_crashing():
    assert pocket.learn(pd.DataFrame()) == ((0.0, 1.0), {})
    assert pocket.cell("player_rush_yds", 0.51) == "player_rush_yds|over|0"
