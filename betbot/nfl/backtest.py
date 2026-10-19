"""Backtests: replay past games using only what was known before each one.

  games:      team-ratings model vs. nflverse closing spreads/totals, 1999 on; closing line value from ESPN
              opening -> closing lines (2024 on).
  props_real: player model vs. DraftKings prop lines saved from DraftKings Network (Sep 2026 on), refit each week
              on earlier weeks only. Prices aren't in those articles, so -110 is assumed on both sides.
  calibration: player model on every player-game 2019-2025 against a line at his pre-game baseline, refit each
              season on earlier seasons: do "60%" picks hit 60%?
Metrics: hit rate of the side the model favors, ROI at the prices available, Brier score (0.25 = coin flip;
lower is better), calibration by confidence bucket, closing line value.
"""
import os

import numpy as np
import pandas as pd

from betbot import db
from betbot.names import normalize
from betbot.nfl import game_model, model
from betbot.nfl.signals.regression import MARKETS
from betbot.odds import decimal_odds, no_vig

BUCKETS = [0.5, 0.55, 0.6, 0.65, 0.7, 1.01]
REAL_LINE_CALIBRATION = "_props_real_lines"  # model_params row: recalibration learned on real sportsbook lines


def metrics(p, won, price=None, other_price=None):
    """p: model probability that side A wins; won: 1 if side A won (pushes removed). Picks the favored side."""
    p, won = np.asarray(p, dtype=float), np.asarray(won, dtype=float)
    n = len(p)
    if n == 0:
        return {"n": 0}
    price = np.full(n, -110.0) if price is None else np.nan_to_num(np.asarray(price, dtype=float), nan=-110.0)
    other = np.full(n, -110.0) if other_price is None else np.nan_to_num(np.asarray(other_price, dtype=float), nan=-110.0)
    pick_a = p >= 0.5
    conf = np.where(pick_a, p, 1 - p)
    hit = np.where(pick_a, won, 1 - won)
    pick_price = np.where(pick_a, price, other)
    profit = np.where(hit == 1, [decimal_odds(x) - 1 for x in pick_price], -1.0)
    fair = np.array([no_vig(a, b)[0] for a, b in zip(price, other)])
    edge = conf - np.where(pick_a, fair, 1 - fair)
    calib = []
    for lo, hi in zip(BUCKETS, BUCKETS[1:]):
        m = (conf >= lo) & (conf < hi)
        if m.any():
            calib.append({"bucket": f"{lo:.0%}-{min(hi, 1):.0%}", "n": int(m.sum()),
                          "predicted": round(float(conf[m].mean()), 3), "actual": round(float(hit[m].mean()), 3)})
    strong = edge >= 0.03
    return {"n": n, "hit_rate": round(float(hit.mean()), 4), "roi": round(float(profit.mean()), 4),
            "brier": round(float(np.mean((p - won) ** 2)), 4), "calibration": calib,
            "edge_picks": int(strong.sum()),
            "edge_hit_rate": round(float(hit[strong].mean()), 4) if strong.any() else None,
            "edge_roi": round(float(profit[strong].mean()), 4) if strong.any() else None}


# ---------- games ----------

def games(conn, first_season=1999):
    g = game_model.predict(game_model.games(conn))
    g = g[g["home_score"].notna() & (g["season"] >= first_season)].copy()
    margin, total = g["home_score"] - g["away_score"], g["home_score"] + g["away_score"]
    home_spread = -g["spread_line"]  # nflverse: + = home favored
    s = g[g["spread_line"].notna() & (margin + home_spread != 0)]
    s_raw = [game_model.p_home_covers(m, hs, sd) for m, hs, sd in zip(s["pred_margin"], -s["spread_line"], s["sigma_margin"])]
    s_p = _walk_forward_calibration(s["season"], s_raw, (margin + home_spread > 0)[s.index].astype(int))
    t = g[g["total_line"].notna() & (total != g["total_line"])]
    t_raw = [game_model.p_over(m, tl, sd) for m, tl, sd in zip(t["pred_total"], t["total_line"], t["sigma_total"])]
    t_p = _walk_forward_calibration(t["season"], t_raw, (total > g["total_line"])[t.index].astype(int))
    out = {"seasons": f"{int(g['season'].min())}-{int(g['season'].max())}",
           "spread": metrics(s_p, (margin + home_spread > 0)[s.index].astype(int), s["home_spread_odds"], s["away_spread_odds"]),
           "total": metrics(t_p, (total > g["total_line"])[t.index].astype(int), t["over_odds"], t["under_odds"])}
    out["spread_by_era"] = {era: metrics(np.array(s_p)[(s["season"] // 10 * 10 == era).to_numpy()],
                                         (margin + home_spread > 0)[s.index][s["season"] // 10 * 10 == era].astype(int))
                            ["hit_rate"] for era in sorted(set(s["season"] // 10 * 10))}
    out["clv"] = game_clv(conn, g)
    return out


def _walk_forward_calibration(seasons, raw, won, min_rows=500):
    """Each season's probabilities recalibrated on earlier seasons' results only (raw until enough history)."""
    seasons, raw, won = np.asarray(seasons), np.asarray(raw, dtype=float), np.asarray(won, dtype=float)
    out = raw.copy()
    for season in sorted(set(seasons)):
        earlier = seasons < season
        if earlier.sum() >= min_rows:
            out[seasons == season] = model.recalibrate(raw[seasons == season],
                                                       model.fit_recalibration(raw[earlier], won[earlier]))
    return out


def game_clv(conn, g):
    """Picks made at ESPN's opening line (2024 on): how often the closing line moved our way, in points."""
    lines = pd.read_sql_query("SELECT game_id, open_home_spread, close_home_spread, open_total, close_total"
                              " FROM nfl_game_lines", conn)
    both = g.merge(lines, on="game_id")
    d = both.dropna(subset=["open_home_spread", "close_home_spread"])
    t = both.dropna(subset=["open_total", "close_total"])  # a game can have spreads but no totals
    if d.empty:
        return {"n": 0}
    p_home = np.array([game_model.p_home_covers(m, hs, sd) for m, hs, sd in
                       zip(d["pred_margin"], d["open_home_spread"], d["sigma_margin"])])
    # points gained vs. the close: home bettor gains when the home spread shrinks (e.g. -3 at open, -4.5 at close)
    move = d["open_home_spread"] - d["close_home_spread"]
    spread_clv = np.where(p_home >= 0.5, move, -move)
    p_o = np.array([game_model.p_over(m, x, sd) for m, x, sd in zip(t["pred_total"], t["open_total"], t["sigma_total"])])
    tmove = t["close_total"] - t["open_total"]
    total_clv = np.where(p_o >= 0.5, tmove, -tmove) if len(t) else np.array([np.nan])
    return {"n": int(len(d)), "total_n": int(len(t)), "spread_avg_points": round(float(spread_clv.mean()), 3),
            "spread_beat_close": round(float((spread_clv > 0).mean()), 3),
            "spread_same_as_close": round(float((spread_clv == 0).mean()), 3),
            "total_avg_points": round(float(total_clv.mean()), 3),
            "total_beat_close": round(float((total_clv > 0).mean()), 3)}


# ---------- props ----------

def cached_features(conn, stat, positions, cache):
    key = (stat, positions)
    if key not in cache:
        if "form" not in cache:
            cache["form"] = model.team_form(conn)  # the same for every stat
        cache[key] = model.features(conn, stat, positions, form=cache["form"])
    return cache[key]


def real_rows(conn, cache=None):
    """Real DraftKings prop lines (DraftKings Network articles, plus EndzoneAPI history), graded against actual stats."""
    cache = {} if cache is None else cache
    from betbot.nfl.prop_splits import dataframe, past_lines
    lines = dataframe(conn)
    lines = lines[lines["period"].isna() & lines["season"].notna()]
    past = past_lines(conn)
    key = ["season", "week", "player", "prop_type"]   # an article's line wins; EndzoneAPI fills the other weeks
    lines = pd.concat([lines, past[~past.set_index(key).index.isin(lines.set_index(key).index)]])
    rows = []
    for market, (stat, positions) in MARKETS.items():
        lm = lines[lines["prop_type"] == market.replace("player_", "")]
        if lm.empty:
            continue
        df = cached_features(conn, stat, positions, cache)
        names = df.assign(key=df["player"].map(normalize)).groupby("key")["player_id"].agg(set)
        for (season, week), wk in lm.groupby(["season", "week"]):
            train = df[(df["season"] < season) | ((df["season"] == season) & (df["week"] < week))]
            params = model.fit_calibrated(train, stat)
            target = model.predict(df[(df["season"] == season) & (df["week"] == week) & df["y"].notna()], params)
            by_player = target.set_index("player_id")
            for r in wk.itertuples():
                ids = names.get(normalize(r.player), set())
                if len(ids) != 1 or next(iter(ids)) not in by_player.index:
                    continue  # unmatched name, or he didn't play (a void)
                t = by_player.loc[[next(iter(ids))]].iloc[0]
                if t["y"] == r.line:
                    continue  # push
                rows.append({"market": market, "season": season, "week": week, "player": r.player, "line": r.line,
                             "p_over": float(model.prob_over(params, t["mu"], t["sigma"], r.line)),
                             "over": int(t["y"] > r.line), "over_tickets": r.over_ticket_pct,
                             "over_handle": r.over_handle_pct, "mu": float(t["mu"]), "sigma": float(t["sigma"]),
                             "base": float(t["base"])})
    return pd.DataFrame(rows)


def props_real(conn, cache=None):
    """Hit rate, calibration and the stored real-line recalibration for the model on real lines."""
    d = real_rows(conn, cache)
    if d.empty:
        return {"n": 0}
    # market lines are sharper than a player's own baseline: learn a second recalibration from real lines,
    # walk-forward (each week adjusted only by earlier weeks), and store the all-weeks version for decisions
    d["week_key"] = d["season"] * 100 + d["week"]
    adjusted = d["p_over"].to_numpy().copy()
    for wk in sorted(d["week_key"].unique()):
        earlier = d["week_key"] < wk
        if earlier.sum() >= 100:
            calib = model.fit_recalibration(d.loc[earlier, "p_over"], d.loc[earlier, "over"])
            adjusted[(d["week_key"] == wk).to_numpy()] = model.recalibrate(d.loc[d["week_key"] == wk, "p_over"], calib)
    real_calib = model.fit_recalibration(d["p_over"], d["over"])
    model.save_params(conn, {"stat": REAL_LINE_CALIBRATION, "calib": real_calib, "rows": int(len(d))},
                      int(d["week_key"].max()))
    out = {"all": metrics(d["p_over"], d["over"]),
           "all_real_line_calibrated": metrics(adjusted[(d["week_key"] > d["week_key"].min()).to_numpy()],
                                               d.loc[d["week_key"] > d["week_key"].min(), "over"]),
           "real_line_calibration": [round(x, 3) for x in real_calib], "weeks": sorted({f"{int(s)} wk {int(w)}" for s, w in zip(d.season, d.week)})}
    out["by_market"] = {m: metrics(g["p_over"], g["over"]) for m, g in d.groupby("market") if len(g) >= 30}
    # does the public's side win? (Over tickets >= 70%)
    heavy = d[d["over_tickets"] >= 70]
    out["public_heavy_over_hit"] = round(float(heavy["over"].mean()), 3) if len(heavy) else None
    out["public_heavy_n"] = int(len(heavy))
    return out


def calibration(conn, stats=("passing_yards", "rushing_yards", "receiving_yards", "receptions"),
                seasons=range(2019, 2026), cache=None):
    """Every player-game with a real role, against a line at his pre-game baseline, refit each season."""
    cache = {} if cache is None else cache
    positions_for = {s: MARKETS[m][1] for m, (s, _) in MARKETS.items()}
    out = {}
    for stat in stats:
        df = cached_features(conn, stat, positions_for[stat], cache)
        ps, wins = [], []
        for season in seasons:
            train = df[df["season"] < season]
            test = df[(df["season"] == season) & df["y"].notna() & (df["prior_n"] >= model.MIN_PRIOR_GAMES)]
            test = test[test["base"] >= 0.5 * test["pos_mean"]]  # players a book would post a prop for
            if test.empty or len(train) < 200:
                continue
            params = model.fit_calibrated(train, stat)
            test = model.predict(test, params)
            line = np.floor(test["base"]) + 0.5
            ps += list(model.prob_over(params, test["mu"], test["sigma"], line))
            wins += list((test["y"] > line).astype(int))
        out[stat] = metrics(ps, wins)
    return out


def report(conn):
    cache = {}
    return {"games": games(conn), "props_real": props_real(conn, cache), "calibration": calibration(conn, cache=cache)}


def print_report(r):
    def line(name, m):
        if not m or not m.get("n"):
            print(f"  {name}: no data")
            return
        print(f"  {name}: n={m['n']}, hit {m['hit_rate']:.1%}, ROI {m['roi']:+.1%}, Brier {m['brier']:.4f}"
              f" (coin flip 0.2500); edge>=3% picks {m['edge_picks']}"
              + (f", hit {m['edge_hit_rate']:.1%}, ROI {m['edge_roi']:+.1%}" if m["edge_picks"] else ""))
        for c in m["calibration"]:
            print(f"      {c['bucket']:>9}: n={c['n']:>5}  predicted {c['predicted']:.1%}  actual {c['actual']:.1%}")
    g = r["games"]
    print(f"GAME LINES vs closing lines ({g['seasons']})")
    line("spread", g["spread"])
    line("total", g["total"])
    print("  spread hit rate by decade:", {k: f"{v:.1%}" for k, v in g["spread_by_era"].items()})
    print("  closing line value from openers (2024+):", g["clv"])
    p = r["props_real"]
    print(f"PLAYER PROPS vs real DraftKings lines ({', '.join(p.get('weeks', [])) or 'none'}; -110 assumed)")
    line("all props", p.get("all"))
    line("all props, recalibrated on earlier weeks' real lines (weeks 2+)", p.get("all_real_line_calibrated"))
    print(f"  real-line recalibration stored for decisions: a, b = {p.get('real_line_calibration')}")
    for m, mm in p.get("by_market", {}).items():
        line(m, mm)
    print(f"  Over hit rate when 70%+ of tickets were on the Over: {p.get('public_heavy_over_hit')} (n={p.get('public_heavy_n')})")
    print("PLAYER MODEL CALIBRATION, 2019-2025 (line at each player's pre-game baseline; not real lines,"
          " so hit rate and ROI are meaningless here: only predicted vs. actual matters)")
    for stat, m in r["calibration"].items():
        print(f"  {stat}: n={m['n']}, Brier {m['brier']:.4f}")
        for c in m["calibration"]:
            print(f"      {c['bucket']:>9}: n={c['n']:>5}  predicted {c['predicted']:.1%}  actual {c['actual']:.1%}")


if __name__ == "__main__":
    print_report(report(db.connect(os.getenv("DB_PATH", "betbot.db"))))
