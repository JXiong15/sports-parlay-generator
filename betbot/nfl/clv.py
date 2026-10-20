"""Does the model beat the OPENING line, i.e. predict where the market moves? Hindsight features are left out so the
test only uses what was knowable at the open: no bets / money / line-move features (they come from the close), no
absences (past games use who actually played), no weather (past games use measured kickoff weather).
`python -m betbot.nfl.clv`"""
import numpy as np
import pandas as pd

from betbot.nfl import game_factors as gf

HINDSIGHT = {"bets_home", "gap_home", "move_home", "bets_over", "gap_over", "move_total",
             "qb_diff", "ol_diff", "skill_diff", "rush_def_diff", "secondary_diff", "lb_diff",
             "div_game", "prime_favorite", "ref_home", "ref_total",   # signed by / fit against the closing spread
             "qb_total", "ol_total", "skill_total", "defense_total", "wind", "gust", "rain", "cold", "dome"}
FIRST_TEST = 2024


def clv_stats(edge, move):
    """edge: model minus open line, move: open-to-close move, both signed the same way. Betting the model's side at
    the open gains `move` points when the model leans that way."""
    edge, move = np.asarray(edge, float), np.asarray(move, float)
    gain = np.sign(edge) * move
    moved = move != 0
    return {"n": len(edge), "corr": float(np.corrcoef(edge, move)[0, 1]), "mean_clv": float(gain.mean()),
            "share_positive": float((gain[moved] > 0).mean())}


def evaluate(conn):
    margin = [c for c in gf.MARGIN if c not in HINDSIGHT]
    total = [c for c in gf.TOTAL if c not in HINDSIGHT]
    g = gf.table(conn)
    played = g[g["actual_margin"].notna()]
    lines = pd.read_sql_query("SELECT game_id, open_home_spread AS o_s, close_home_spread AS c_s, open_total AS o_t,"
                              " close_total AS c_t FROM nfl_game_lines", conn).drop_duplicates("game_id")
    rows = []
    for season in range(FIRST_TEST, int(played["season"].max()) + 1):
        test = played[played["season"] == season]
        if test.empty:
            continue
        train = played[(played["season"] < season) & (played["season"] >= gf.FIRST_FIT_SEASON)]
        coefs = {"margin": dict.fromkeys(gf.MARGIN, 0.0), "total": dict.fromkeys(gf.TOTAL, 0.0)}
        for key, cols, base, actual in (("margin", margin, "pred_margin", "actual_margin"),
                                        ("total", total, "pred_total", "actual_total")):
            coefs[key].update(zip(cols, map(float, gf._ridge(train[cols], train[actual] - train[base]))))
        rows.append(gf.apply(test.drop(columns=gf.MARGIN + gf.TOTAL), test[["game_id"] + gf.MARGIN + gf.TOTAL].set_index("game_id"), coefs))
    if not rows:
        return {}
    d = pd.concat(rows).merge(lines, on="game_id")
    s_ok, t_ok = d.dropna(subset=["o_s", "c_s"]), d.dropna(subset=["o_t", "c_t"])
    out = {}
    for label in ["all"] + sorted(d["season"].unique()):
        sp = s_ok if label == "all" else s_ok[s_ok["season"] == label]
        tp = t_ok if label == "all" else t_ok[t_ok["season"] == label]
        if len(sp) > 1 and len(tp) > 1:
            out[label if label == "all" else int(label)] = {
                "margin": clv_stats(sp["pred_margin"] + sp["o_s"], sp["o_s"] - sp["c_s"]),
                "total": clv_stats(tp["pred_total"] - tp["o_t"], tp["c_t"] - tp["o_t"])}
    return out


if __name__ == "__main__":
    import os
    from betbot import db
    for label, r in evaluate(db.connect(os.getenv("DB_PATH", "betbot.db"))).items():
        for k in ("margin", "total"):
            s = r[k]
            print(f"{label!s:5} {k:6} n={s['n']:4} corr {s['corr']:+.3f}  mean CLV {s['mean_clv']:+.2f} pts"
                  f"  positive {s['share_positive']:.1%}")
