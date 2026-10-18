from betbot.nfl import correlation, parlays

TABLE = {"H|T": 0.04, "passing_yards|T": 0.28, "passing_yards|own_margin": 0.06,
         "receiving_yards|passing_yards|teammates": 0.45, "passing_yards|passing_yards|opponents": 0.11}


def prop(side="Over", stat="passing_yards", player="qb1", team="GB"):
    return {"kind": "prop", "stat": stat, "player_id": player, "team": team, "side": side, "market": "player_x",
            "game": "2026_06_DAL_GB"}


def game(market, side):
    return {"kind": "game", "market": market, "side": side, "home": "GB", "away": "DAL", "game": "2026_06_DAL_GB"}


def test_rho_signs_follow_the_sides():
    rho = correlation.rho(TABLE)
    assert rho(prop(), game("total", "over")) == 0.28
    assert rho(prop("Under"), game("total", "over")) == -0.28
    # WR Over with his own QB's Over: positive; order of the pair doesn't matter
    wr = prop(stat="receiving_yards", player="wr1")
    assert rho(wr, prop()) == rho(prop(), wr) == 0.45
    assert rho(prop(player="qb2", team="DAL"), prop()) == 0.11  # opposing QBs: a shootout lifts both
    # GB QB Over vs. betting DAL's spread: against his own team's margin
    assert rho(prop(), game("spread", "away")) == -0.06 and rho(prop(), game("spread", "home")) == 0.06
    assert rho(game("spread", "home"), game("moneyline", "home")) == correlation.SPREAD_ML
    assert rho(game("spread", "home"), game("total", "under")) == -0.04


def test_legs_and_conflicts():
    picks = [{"decision": {"verdict": "BET", "ev": 0.05}, "label": "x", "model_prob": 0.56, "price": -110,
              "game_id": "2026_06_DAL_GB", "side": "home", "market": "spread", "kind": "game"},
             {"decision": {"verdict": "STAY AWAY", "ev": 0.08}, "label": "y", "model_prob": 0.6, "price": -110,
              "game_id": "2026_06_DAL_GB", "side": "over", "market": "total", "kind": "game"}]
    legs = parlays.legs(picks)
    assert len(legs) == 1 and legs[0]["home"] == "GB" and legs[0]["away"] == "DAL"
    assert parlays.conflicts(game("spread", "home") | {"game": "g"}, game("moneyline", "away") | {"game": "g"})
    assert not parlays.conflicts(game("spread", "home") | {"game": "g"}, game("moneyline", "home") | {"game": "g"})
    assert parlays.conflicts(prop(), prop("Under") | {"market": "player_x"})
    assert not parlays.conflicts(prop(), prop(player="qb2"))
