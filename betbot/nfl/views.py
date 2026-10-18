"""Read-only SQL views that combine the NFL tables into ready-to-use context. Recreated after every stats load."""

VIEWS = {
    # one row per player per game: role and usage alongside production
    "nfl_player_usage": """
        SELECT w.player_id, w.player_display_name AS player, w.position, w.position_group, w.season, w.week,
               w.season_type, w.game_id, w.team, w.opponent_team,
               s.offense_snaps, s.offense_pct, s.defense_snaps, s.defense_pct,
               w.attempts, w.completions, w.carries, w.targets, w.receptions,
               w.target_share, w.air_yards_share, w.wopr,
               w.passing_yards, w.rushing_yards, w.receiving_yards
        FROM nfl_player_weeks w
        LEFT JOIN nfl_players p ON p.gsis_id = w.player_id
        LEFT JOIN nfl_snap_counts s ON s.pfr_player_id = p.pfr_id AND s.game_id = w.game_id""",

    # what each defense allowed to each position group in each game
    "nfl_defense_vs_position": """
        SELECT season, week, game_id, opponent_team AS defense, position_group,
               sum(passing_yards) AS passing_yards, sum(passing_tds) AS passing_tds,
               sum(passing_interceptions) AS interceptions, sum(completions) AS completions,
               sum(attempts) AS pass_attempts, sum(rushing_yards) AS rushing_yards, sum(carries) AS carries,
               sum(rushing_tds) AS rushing_tds, sum(receptions) AS receptions, sum(targets) AS targets,
               sum(receiving_yards) AS receiving_yards, sum(receiving_tds) AS receiving_tds,
               sum(fantasy_points_ppr) AS fantasy_points_ppr
        FROM nfl_player_weeks
        WHERE position_group IN ('QB', 'RB', 'WR', 'TE')
        GROUP BY season, week, game_id, opponent_team, position_group""",

    # per-game averages allowed, by season (the usual "rank vs QBs/WRs" number)
    "nfl_defense_vs_position_season": """
        SELECT season, defense, position_group, count(*) AS games,
               round(avg(passing_yards), 1) AS passing_yards, round(avg(passing_tds), 2) AS passing_tds,
               round(avg(rushing_yards), 1) AS rushing_yards, round(avg(receptions), 1) AS receptions,
               round(avg(receiving_yards), 1) AS receiving_yards, round(avg(fantasy_points_ppr), 1) AS fantasy_points_ppr
        FROM nfl_defense_vs_position
        GROUP BY season, defense, position_group""",

    # everything about a game's setting. Spreads use ESPN's sign (home favorite is negative);
    # nflverse's spread_line is the opposite sign, so it is negated.
    "nfl_game_context": """
        SELECT g.game_id, g.season, g.week, g.game_type, g.gameday, g.gametime, g.weekday,
               g.away_team, g.home_team, g.away_score, g.home_score, g.location,
               g.away_rest, g.home_rest, g.div_game, g.referee, g.away_coach, g.home_coach,
               g.stadium, g.roof, g.surface,
               coalesce(w.temp_f, g.temp) AS temp_f, coalesce(w.wind_mph, g.wind) AS wind_mph,
               w.gust_mph, w.precip_pct, w.espn_condition,
               l.open_total, coalesce(l.close_total, l.current_total, g.total_line) AS total,
               l.open_home_spread, coalesce(l.close_home_spread, l.current_home_spread, -g.spread_line) AS home_spread,
               coalesce(l.close_home_ml, l.current_home_ml, g.home_moneyline) AS home_ml,
               coalesce(l.close_away_ml, l.current_away_ml, g.away_moneyline) AS away_ml,
               coalesce(l.close_over_price, l.current_over_price, g.over_odds) AS over_price,
               coalesce(l.close_under_price, l.current_under_price, g.under_odds) AS under_price,
               -- expected points per team from the total and spread: a big underdog tends to pass more
               (coalesce(l.close_total, l.current_total, g.total_line)
                - coalesce(l.close_home_spread, l.current_home_spread, -g.spread_line)) / 2.0 AS home_implied_points,
               (coalesce(l.close_total, l.current_total, g.total_line)
                + coalesce(l.close_home_spread, l.current_home_spread, -g.spread_line)) / 2.0 AS away_implied_points
        FROM nfl_games g
        LEFT JOIN nfl_game_lines l ON l.game_id = g.game_id
        LEFT JOIN nfl_weather w ON w.game_id = g.game_id""",
}


def create(conn):
    with conn:
        for name, sql in VIEWS.items():
            conn.execute(f"DROP VIEW IF EXISTS {name}")
            conn.execute(f"CREATE VIEW {name} AS {sql}")
