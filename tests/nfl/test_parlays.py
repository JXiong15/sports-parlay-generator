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
    picks = [{"decision": {"verdict": "BET", "ev": 0.05, "edge": 0.04}, "label": "x", "model_prob": 0.56, "price": -110,
              "game_id": "2026_06_DAL_GB", "side": "home", "market": "spread", "kind": "game"},
             {"decision": {"verdict": "STAY AWAY", "ev": 0.08, "edge": 0.06}, "label": "y", "model_prob": 0.6, "price": -110,
              "game_id": "2026_06_DAL_GB", "side": "over", "market": "total", "kind": "game"}]
    legs = parlays.legs(picks)
    assert len(legs) == 1 and legs[0]["home"] == "GB" and legs[0]["away"] == "DAL"
    assert parlays.conflicts(game("spread", "home") | {"game": "g"}, game("moneyline", "away") | {"game": "g"})
    assert not parlays.conflicts(game("spread", "home") | {"game": "g"}, game("moneyline", "home") | {"game": "g"})
    assert parlays.conflicts(prop(), prop("Under") | {"market": "player_x"})
    assert not parlays.conflicts(prop(), prop(player="qb2"))



def side(name, prob, price, other_price, label, **extra):
    from betbot.decision import decide
    return {"side": name, "model_prob": prob, "price": price, "other_price": other_price, "label": label,
            "decision": decide(prob, price, other_price, extra.get("bets_pct"), extra.get("money_pct"))}


def game_pick(home_prob, home_price=200, away_price=-250, **extra):
    """A moneyline pick with both sides as the board builds them (labels name each side's own team)."""
    sides = [side("home", home_prob, home_price, away_price, "DAL @ GB: Green Bay Packers ML", **extra),
             side("away", 1 - home_prob, away_price, home_price, "DAL @ GB: Dallas Cowboys ML")]
    chosen = sides[0] if home_prob >= 0.5 else sides[1]
    return dict(chosen, kind="game", game_id="2026_06_DAL_GB", market="moneyline", sides=sides)


def test_hunt_keeps_plus_money_high_edge_sides_only():
    # +200 at 40%: no-vig price ~31%, edge ~9 points -> not enough at 12
    assert parlays.hunt_legs([game_pick(0.40)], min_conv=0.12) == []
    # at 48% the edge is ~17 points: kept, even though the board's pick is the other side
    legs = parlays.hunt_legs([game_pick(0.48)], min_conv=0.12)
    assert len(legs) == 1 and legs[0]["price"] == 200 and legs[0]["edge"] > 0.15
    # outside +150..+400 is dropped, however big the edge
    assert parlays.hunt_legs([game_pick(0.48, 450, -600)]) == []
    assert parlays.hunt_legs([game_pick(0.70, -110, -110)]) == []


def test_hunt_leg_names_the_side_actually_bet():
    legs = parlays.hunt_legs([game_pick(0.48)], min_conv=0.12)
    assert legs[0]["side"] == "home" and legs[0]["label"] == "DAL @ GB: Green Bay Packers ML"


def test_hunt_still_applies_market_checks():
    # sharp money on the other side: 70% of the money vs 30% of the bets -> STAY AWAY, so no leg
    legs = parlays.hunt_legs([game_pick(0.48, bets_pct=70, money_pct=30)], min_conv=0.12)
    assert legs == []
