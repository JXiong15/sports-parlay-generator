"""Saved DraftKings player-prop splits as a DataFrame, tagged with NFL season and week and sorted by week."""
import os

import pandas as pd

from betbot import db

# an article covers the first NFL game on or after the day it was published (DraftKings posts them game day)
QUERY = """
SELECT g.season, g.week, s.game, s.player, replace(s.market, 'player_', '') AS prop_type, s.period, s.line,
       s.over_bets AS over_ticket_pct, s.under_bets AS under_ticket_pct,
       s.over_handle AS over_handle_pct, s.under_handle AS under_handle_pct,
       s.market_text AS dk_wording, a.published, a.link AS source
FROM prop_splits s
JOIN dk_articles a ON a.id = s.article_id
LEFT JOIN nfl_games g ON g.game_id = (
    SELECT game_id FROM nfl_games WHERE gameday >= date(a.published) ORDER BY gameday, gametime LIMIT 1)
WHERE a.league = 'nfl'
"""


def dataframe(conn):
    df = pd.read_sql_query(QUERY, conn)
    return df.sort_values(["season", "week", "published", "player", "prop_type", "period"],
                          na_position="first", kind="stable").reset_index(drop=True)


if __name__ == "__main__":
    df = dataframe(db.connect(os.getenv("DB_PATH", "betbot.db")))
    print(f"{len(df)} props, weeks: {df.groupby('season')['week'].unique().to_dict()}")
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(df.drop(columns=["source", "dk_wording"]).head(15).to_string(index=False))
