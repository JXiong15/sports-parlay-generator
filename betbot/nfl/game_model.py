"""Game model: each team's expected points from recency-weighted points scored and allowed in earlier games
(carried across seasons with regression toward the league average), plus home-field edge. Gives P(home covers)
and P(over) for any spread or total. Uses only games before the one predicted."""
import math

import numpy as np
import pandas as pd

HALFLIFE = 8           # games
CARRYOVER = 0.6        # share of last season's rating kept at the start of a new season
DEFAULT_SIGMA = 13.5   # points; used until enough earlier games exist to measure it


def normal_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def games(conn, first_season=1999):
    return pd.read_sql_query(
        "SELECT game_id, season, week, gameday, home_team, away_team, home_score, away_score, location,"
        " spread_line, total_line, home_spread_odds, away_spread_odds, over_odds, under_odds"
        " FROM nfl_games WHERE season >= ? ORDER BY gameday, game_id", conn, params=(first_season,))


def predict(g):
    """Adds pred_home, pred_away, pred_margin, pred_total, sigma_margin, sigma_total to each game (sorted by date)."""
    g = g.sort_values(["gameday", "game_id"]).reset_index(drop=True)
    alpha = 1 - 0.5 ** (1 / HALFLIFE)
    off, dfn, season_of = {}, {}, {}
    league = 21.5
    home_edges, margin_err, total_err = [], [], []
    out = []
    for r in g.itertuples(index=False):
        for team in (r.home_team, r.away_team):
            if season_of.get(team) not in (None, r.season):
                off[team] = CARRYOVER * off[team] + (1 - CARRYOVER) * league
                dfn[team] = CARRYOVER * dfn[team] + (1 - CARRYOVER) * league
            season_of[team] = r.season
            off.setdefault(team, league)
            dfn.setdefault(team, league)
        hfa = float(np.mean(home_edges[-768:])) if len(home_edges) >= 100 else 2.0  # ~3 seasons of games
        neutral = r.location == "Neutral"
        edge = 0.0 if neutral else hfa
        ph = (off[r.home_team] + dfn[r.away_team]) / 2 + edge / 2
        pa = (off[r.away_team] + dfn[r.home_team]) / 2 - edge / 2
        sm = float(np.std(margin_err[-768:])) if len(margin_err) >= 100 else DEFAULT_SIGMA
        st = float(np.std(total_err[-768:])) if len(total_err) >= 100 else DEFAULT_SIGMA
        out.append((ph, pa, ph - pa, ph + pa, sm, st))
        if pd.notna(r.home_score) and pd.notna(r.away_score):
            hs, aw = float(r.home_score), float(r.away_score)
            margin_err.append((hs - aw) - (ph - pa))
            total_err.append((hs + aw) - (ph + pa))
            if not neutral:
                # home edge measured against what the teams' ratings expected on a neutral field
                home_edges.append((hs - aw) - (ph - pa) + edge)
            off[r.home_team] += alpha * (hs - off[r.home_team])
            off[r.away_team] += alpha * (aw - off[r.away_team])
            dfn[r.home_team] += alpha * (aw - dfn[r.home_team])
            dfn[r.away_team] += alpha * (hs - dfn[r.away_team])
            league += 0.002 * ((hs + aw) / 2 - league)
    cols = ["pred_home", "pred_away", "pred_margin", "pred_total", "sigma_margin", "sigma_total"]
    return pd.concat([g, pd.DataFrame(out, columns=cols)], axis=1)


def p_home_covers(pred_margin, home_spread, sigma):
    """home_spread in sportsbook terms (home favored by 3 = -3): home covers if margin + home_spread > 0."""
    return 1 - normal_cdf((-home_spread - pred_margin) / sigma)


def p_over(pred_total, total, sigma):
    return 1 - normal_cdf((total - pred_total) / sigma)
