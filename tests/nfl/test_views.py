from betbot import db
from betbot.nfl import views
from betbot.nfl.collectors.nflverse import replace_table

GAME_COLS = ["game_id", "season", "week", "game_type", "gameday", "gametime", "weekday", "away_team", "home_team",
             "away_score", "home_score", "location", "away_rest", "home_rest", "div_game", "referee", "away_coach",
             "home_coach", "stadium", "roof", "surface", "temp", "wind", "total_line", "spread_line",
             "home_moneyline", "away_moneyline", "over_odds", "under_odds"]
WEEK_COLS = ["player_id", "player_display_name", "position", "position_group", "season", "week", "season_type",
             "game_id", "team", "opponent_team", "attempts", "completions", "carries", "targets", "receptions",
             "target_share", "air_yards_share", "wopr", "passing_yards", "passing_tds", "passing_interceptions",
             "rushing_yards", "rushing_tds", "receiving_yards", "receiving_tds", "fantasy_points_ppr"]


def game(game_id, total, spread_line):
    row = dict.fromkeys(GAME_COLS)
    row.update(game_id=game_id, season=2001, week=1, away_team="SF", home_team="ATL", total_line=total,
               spread_line=spread_line, home_moneyline=-150, away_moneyline=130, temp=40, wind=12)
    return [row[c] for c in GAME_COLS]


def make_db():
    conn = db.connect(":memory:")
    replace_table(conn, "nfl_games", GAME_COLS, [game("old", 44.0, 3.0), game("new", 50.0, 1.0)])
    replace_table(conn, "nfl_players", ["gsis_id", "pfr_id"], [["qb1", "PfrQb"]])
    replace_table(conn, "nfl_snap_counts", ["game_id", "pfr_player_id", "offense_snaps", "offense_pct",
                                            "defense_snaps", "defense_pct"], [["old", "PfrQb", 60, 1.0, 0, 0]])
    qb = dict.fromkeys(WEEK_COLS)
    qb.update(player_id="qb1", player_display_name="Q B", position="QB", position_group="QB", season=2001, week=1,
              game_id="old", team="SF", opponent_team="ATL", passing_yards=300, passing_tds=2, receptions=0)
    wr = dict(qb, player_id="wr1", player_display_name="W R", position="WR", position_group="WR",
              passing_yards=0, passing_tds=0, receptions=5, receiving_yards=80)
    replace_table(conn, "nfl_player_weeks", WEEK_COLS, [[qb[c] for c in WEEK_COLS], [wr[c] for c in WEEK_COLS]])
    db.save_game_line(conn, {"game_id": "new", "book": "DK", "current_total": 47.5, "current_home_spread": -3.5,
                             "current_home_ml": -170, "current_away_ml": 145, "fetched_at": "t"})
    views.create(conn)
    return conn


def test_game_context_signs_and_implied_points():
    rows = {r[0]: r[1:] for r in make_db().execute(
        "SELECT game_id, home_spread, total, home_ml, home_implied_points, away_implied_points, temp_f"
        " FROM nfl_game_context")}
    # nflverse only: home favored by 3 (+3 in nflverse) is -3 in ESPN's convention; 44 total -> 23.5 vs 20.5
    assert rows["old"] == (-3.0, 44.0, -150, 23.5, 20.5, 40)
    # ESPN's line wins over nflverse's when present
    assert rows["new"] == (-3.5, 47.5, -170, 25.5, 22.0, 40)


def test_usage_joins_snaps_and_defense_vs_position():
    conn = make_db()
    assert conn.execute("SELECT player, offense_pct FROM nfl_player_usage ORDER BY player").fetchall() == [
        ("Q B", 1.0), ("W R", None)]  # no snap row for the WR: kept, not dropped
    assert conn.execute("SELECT defense, position_group, passing_yards, receiving_yards FROM nfl_defense_vs_position"
                        " ORDER BY position_group").fetchall() == [("ATL", "QB", 300, None), ("ATL", "WR", 0, 80)]
    assert conn.execute("SELECT games, passing_yards FROM nfl_defense_vs_position_season WHERE position_group = 'QB'"
                        ).fetchone() == (1, 300.0)
