"""Conviction score: how many signals agree on a side of a prop, and how strongly.

This is NOT a probability, and the signals are not fully independent: "volume", "stale_line" and "model" all lean on
the same recent-form-vs-line fact, so one strong fact can show up in three of them. Each signal is rated -1..+1 toward
the Over (NaN = not available for this prop):

  script      game script: an underdog, a team that has been losing, and a team that throws a lot all mean more
              passing (and less rushing)
  volume      the player's last 3 games vs. the line
  stale_line  the line still sits at last season's average while his recent games say otherwise
  matchup     how much the opposing defense allows to his position
  usage       his role: snap share trend, starter or backup
  health      questionable / limited in practice / his QB out / opposing defensive starters out
  weather     wind and cold
  model       the player model's own probability

The weighted agreement minus twice the weighted disagreement is turned into a 0-100 score by its PERCENTILE among
about 320,000 past props (every completed season since 2019, lines near a slow average of the player's form), so a 95
means "more agreement than 95% of all props ever scored" and each score range has a measurable hit rate. Only
established players are scored (4+ games, a real role), the same filter as the history. Market signals (sharp money against us,
a line that moved against us, a prediction market that disagrees) can only pull a score down, by up to 15 points.

Two hit-rate tables come out of the backtest: the 2019-2025 lines (large sample, but the line is an approximation) and
this season's real DraftKings lines (small sample, real). A pick is shown only when its score is 95+, the real-line
hit rate for that range has at least MIN_HISTORY props, and it beats the book's no-vig price. The market penalty in
the real-line table uses the saved betting splits only (no line-move or Kalshi history exists), so live scores can
only be lower than the table assumed, never higher.
"""
import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from betbot import db, decision, parlay
from betbot.nfl import model
from betbot.nfl.backtest import cached_features as _frame
from betbot.nfl.collectors.nflverse import current_season
from betbot.nfl.signals.regression import MARKETS
from betbot.odds import expected_value, no_vig

THRESHOLD = 95          # minimum score to show a pick; not lowered when nothing qualifies
MIN_HISTORY = 30        # real-line props needed in the score range before its hit rate counts
MAX_MARKET_PENALTY = 15
MIN_SIGNALS = 5         # a prop needs at least this many available signals to be scored
FIRST_TEST_SEASON = 2019  # history runs from here through the last completed season
LINE_OFFSETS = (-0.5, 0.0, 0.5)  # lines at the slow average, and half a standard deviation either side
TABLE_KEY = "_conviction"
SCORING_VERSION = 4     # bump when a signal or the way scores are computed changes

STAT_GROUP = {"passing_yards": "pass", "completions": "pass", "attempts": "pass", "rushing_yards": "rush",
              "carries": "rush", "receptions": "rec", "receiving_yards": "rec"}
# markets with a real range of lines (touchdown and interception props are mostly 0.5 / 1.5)
CONVICTION_MARKETS = {m: s for m, (s, _) in MARKETS.items() if s in STAT_GROUP}
WEIGHTS = {"script": 1.5, "volume": 1.5, "stale_line": 1.0, "matchup": 1.0, "usage": 1.0, "health": 1.0,
           "weather": 0.5, "model": 2.0}
SIGNAL_NAMES = list(WEIGHTS)


# ---------- signals ----------

def _mean_of(*parts):
    """Average of the parts that are available (NaN-aware); NaN when none are."""
    return pd.concat(list(parts), axis=1).mean(axis=1)


def signals(df, line, p_over):
    """Strength of each signal toward the Over, -1..+1. df needs the model.features columns, plus sigma and group;
    line and p_over are arrays aligned with df. NaN means the signal isn't available."""
    df = df.reset_index(drop=True)
    line = pd.Series(np.asarray(line, dtype=float))
    p_over = pd.Series(np.asarray(p_over, dtype=float))
    group, sigma = df["group"], df["sigma"].clip(lower=1e-6)

    trailing = np.tanh(df["spread"] * 1.5)                      # spread is the team's side /7: + = underdog
    losing = np.tanh(-df["margin3"] / 10)
    throwing = np.tanh((df["att3"] - df["league_att"]) / 8)
    pass_lean = _mean_of(trailing, losing, throwing)
    script = pd.Series(np.select([group == "pass", group == "rec"], [pass_lean, 0.7 * pass_lean], -pass_lean))
    script[pass_lean.isna()] = np.nan

    volume = np.tanh((df["y_recent3"] - line) / (0.5 * sigma))

    prior = df["y_prior_season"]
    near = (line - prior).abs() <= 0.35 * sigma
    stale = pd.Series(np.where(near, np.tanh((df["y_recent3"] - prior) / sigma), 0.0))
    stale[prior.isna() | df["y_recent3"].isna()] = np.nan

    matchup = np.tanh(df["def_vs_pos"] / 0.08)

    usage = np.tanh(df["usage_trend"] / 0.25) - 0.5 * (df["snap_pct"] < 0.4)
    usage = usage.clip(-1, 1)

    receiver = group.isin(["rush", "rec"])
    health = (-0.5 * df["questionable"] - 0.5 * df["limited_practice"] - 0.7 * df["qb_out"] * receiver
              + np.where(group.isin(["pass", "rec"]), 0.15 * df["opp_def_starters_out"].clip(upper=2), 0.0))
    health = health.clip(-1, 1)

    wind = np.tanh(1.5 * df["wind"])
    weather = pd.Series(np.where(group == "rush", 0.3 * wind, -wind - 0.2 * df["cold"])).clip(-1, 1)

    model_signal = np.tanh((p_over - 0.5) / 0.12)

    out = pd.DataFrame({"script": script, "volume": volume, "stale_line": stale, "matchup": matchup,
                        "usage": usage, "health": health, "weather": weather, "model": model_signal})
    return out.reindex(columns=SIGNAL_NAMES)


def aggregate(strengths):
    """(raw, sign): sign is +1 when the weighted signals favor the Over, else -1; raw is the weighted agreement with
    that side minus twice the weighted disagreement, per unit of available weight (NaN if too few signals)."""
    w = pd.Series(WEIGHTS)
    available = strengths.notna()
    sign = pd.Series(np.where((strengths.fillna(0) * w).sum(axis=1) >= 0, 1.0, -1.0), index=strengths.index)
    toward = strengths.mul(sign, axis=0)
    agree = (toward.clip(lower=0).fillna(0) * w).sum(axis=1)
    against = ((-toward).clip(lower=0).fillna(0) * w).sum(axis=1)
    total = (available * w).sum(axis=1)
    raw = (agree - 2 * against) / total.replace(0, np.nan)
    # the always-present signals (health, weather, model...) have neutral values, so a count can't tell a prop with
    # nothing known about it from a well-understood one: require the core evidence instead, and a real lean
    core = strengths["script"].notna() & strengths["volume"].notna()
    raw[~core | (available.sum(axis=1) < MIN_SIGNALS) | ((strengths.fillna(0) * w).sum(axis=1) == 0)] = np.nan
    return raw, sign


def to_score(raw, quantiles):
    """0-100: the share of past props (stored quantiles of raw) that this one beats."""
    q = np.asarray(quantiles, dtype=float)
    return 100 * np.interp(np.asarray(raw, dtype=float), q, np.linspace(0, 1, len(q)))


def market_penalty(bets_pct=None, money_pct=None, line_move=None, market_prob=None, fair=None):
    """(points, notes): how much the market pulls a score down. Values are for OUR side (bets / money as the %
    on our side; line_move > 0 = the line moved toward us; market_prob = a prediction market's probability for our
    side). Can only subtract, never add. Unknown values never penalize."""
    points, notes = 0.0, []
    if bets_pct is not None and money_pct is not None and money_pct - bets_pct <= -decision.SHARP_GAP:
        points += 8
        notes.append(f"sharp money against ({money_pct:.0f}% of money vs {bets_pct:.0f}% of bets)")
    if line_move is not None and line_move < 0:
        points += 6
        notes.append(f"line moved against us ({line_move:+g})")
    if bets_pct is not None and bets_pct >= decision.BAIT and line_move == 0:  # (a move against us is penalized above)
        points += 5
        notes.append(f"{bets_pct:.0f}% of bets on our side and the line hasn't moved for them")
    if market_prob is not None and fair is not None and market_prob < fair - decision.SHARP_MARKET_GAP:
        points += 8
        notes.append(f"Kalshi prices our side at {market_prob:.0%}, below the book's {fair:.0%}")
    return min(points, MAX_MARKET_PENALTY), notes


# ---------- the explanation line ----------

def _name(side):
    return "Over" if side > 0 else "Under"


def drivers(row, line, strengths, sign, team=None):
    """One short line: what pushed the score up, and what argues against it."""
    group = row["group"]
    toward = strengths * sign
    phrases = {}
    implied = 22 * np.exp(row["implied_points"]) if pd.notna(row.get("implied_points")) else None
    parts = []
    if implied is not None:
        parts.append(f"{team + ' ' if team else ''}implied {implied:.1f} pts")
    if pd.notna(row.get("att3")) and group in ("pass", "rec"):
        parts.append(f"{row['att3']:.0f} att/game last 3")
    if pd.notna(row.get("margin3")) and abs(row["margin3"]) >= 3:
        parts.append(f"{'losing' if row['margin3'] < 0 else 'winning'} by {abs(row['margin3']):.0f} on average")
    phrases["script"] = ", ".join(parts)
    if pd.notna(row.get("y_recent3")):
        phrases["volume"] = f"last 3 games avg {row['y_recent3']:.0f} vs line {line:g}"
    if pd.notna(row.get("y_prior_season")):
        phrases["stale_line"] = f"line near last season's avg {row['y_prior_season']:.0f}, recent {row['y_recent3']:.0f}"
    if pd.notna(row.get("def_vs_pos")):
        pct = (np.exp(row["def_vs_pos"]) - 1) * 100
        phrases["matchup"] = f"{row.get('opponent_team', 'opponent')} allows {pct:+.0f}% vs average"
    phrases["usage"] = f"{'role growing' if row['usage_trend'] > 0 else 'role shrinking'}, {row['snap_pct']:.0%} of snaps"
    health = [t for t, on in (("questionable", row["questionable"]), ("limited in practice", row["limited_practice"]),
                              ("QB out", row["qb_out"] and group != "pass")) if on]
    phrases["health"] = ", ".join(health) if health else "healthy"
    phrases["weather"] = f"wind {row['wind'] * 10 + 10:.0f} mph" if row["wind"] > 0 else "calm"
    phrases["model"] = f"model expects {row['mu']:.0f}" if pd.notna(row.get("mu")) else "model"
    order = sorted(SIGNAL_NAMES, key=lambda n: -abs(WEIGHTS[n] * (toward[n] if pd.notna(toward[n]) else 0)))
    for_side = [phrases[n] for n in order if pd.notna(toward[n]) and toward[n] >= 0.3 and phrases.get(n)][:4]
    against = [phrases[n] for n in order if pd.notna(toward[n]) and toward[n] <= -0.3 and phrases.get(n)][:2]
    text = "; ".join(for_side) or "no strong signal"
    return text + (f" | against: {'; '.join(against)}" if against else "")


# ---------- history and the stored tables ----------

def eligible(df):
    """Players a book would post a prop for: 4+ games of history and a real role. History, the real-line
    backtest and live picks all use it, so a score means the same thing everywhere."""
    return (df["prior_n"] >= model.MIN_PRIOR_GAMES) & (df["base"] >= 0.5 * df["pos_mean"])


def _wilson(hit, n, z=1.96):
    """95% range for a hit rate measured on n props."""
    centre = (hit + z * z / (2 * n)) / (1 + z * z / n)
    half = z * np.sqrt(hit * (1 - hit) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return centre - half, centre + half


def _scored(pred, line, p, stat):
    """raw / side / result for each row of a predicted frame at the given lines."""
    strengths = signals(pred.assign(group=STAT_GROUP[stat]), line, p)
    raw, sign = aggregate(strengths)
    won = np.where(sign.to_numpy() > 0, pred["y"].to_numpy() > np.asarray(line), pred["y"].to_numpy() < np.asarray(line))
    return pd.DataFrame({"stat": stat, "season": pred["season"].to_numpy(), "raw": raw.to_numpy(),
                         "side": sign.to_numpy(), "won": won.astype(int)})


def last_completed_season(conn):
    """The latest season in which every game has been played (the current season while it is in progress is not)."""
    row = conn.execute("SELECT max(season) FROM (SELECT season FROM nfl_games GROUP BY season"
                       " HAVING sum(home_score IS NULL) = 0)").fetchone()
    return row[0] or FIRST_TEST_SEASON - 1


def history_rows(conn, cache=None):
    """Every past prop from 2019 through the last completed season at lines near a book's: the slow average, and
    half a deviation either side.
    Each season is predicted by a model fitted only on earlier seasons."""
    cache = {} if cache is None else cache
    out = []
    for market, stat in CONVICTION_MARKETS.items():
        df = _frame(conn, stat, MARKETS[market][1], cache)
        for season in range(FIRST_TEST_SEASON, last_completed_season(conn) + 1):
            train = df[df["season"] < season]
            test = df[(df["season"] == season) & df["y"].notna() & eligible(df)]
            if test.empty or len(train) < 200:
                continue
            params = model.fit_calibrated(train, stat)
            pred = model.predict(test, params)
            centre = pred["y_long"].fillna(pred["base"])
            for k in LINE_OFFSETS:
                line = np.maximum(np.floor(centre + k * pred["sigma"]) + 0.5, 0.5)
                out.append(_scored(pred, line, model.prob_over(params, pred["mu"], pred["sigma"], line), stat))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["stat", "season", "raw", "side", "won"])


def hit_table(scores, won):
    """{range: {"n", "hit"}} for 5-point ranges and for 95+ (the only range that gets a pick)."""
    scores, won = np.asarray(scores, dtype=float), np.asarray(won, dtype=float)
    ok = ~np.isnan(scores)
    table = {}
    for lo in range(0, 100, 5):
        m = ok & (scores >= lo) & (scores < lo + 5 if lo < 95 else scores <= 100)
        if m.any():
            table[f"{lo}-{lo + 5}"] = {"n": int(m.sum()), "hit": round(float(won[m].mean()), 4)}
    m = ok & (scores >= THRESHOLD)
    table["95+"] = {"n": int(m.sum()), "hit": round(float(won[m].mean()), 4) if m.any() else None}
    return table


def real_lines(conn):
    """This season's real DraftKings main lines (DraftKings Network articles), one per player and prop per week:
    the line from the earliest article that week (a choice that doesn't depend on how the betting turned out)."""
    from betbot.nfl.prop_splits import dataframe
    lines = dataframe(conn)
    lines = lines[lines["period"].isna() & lines["season"].notna()].copy()
    lines["market"] = "player_" + lines["prop_type"]
    lines = lines[lines["market"].isin(CONVICTION_MARKETS)]
    lines = lines.sort_values("published").drop_duplicates(["season", "week", "player", "market"])
    lines = lines.rename(columns={"over_ticket_pct": "over_tickets", "over_handle_pct": "over_handle"})[
        ["season", "week", "player", "market", "line", "over_tickets", "over_handle"]]
    from betbot.names import normalize
    both = pd.concat([lines, saved_lines(conn)])      # articles first: they win where both have the prop
    both = both.assign(who=both["player"].map(normalize)).drop_duplicates(["season", "week", "who", "market"])
    return both.drop(columns="who").assign(source="DraftKings line")


def saved_lines(conn):
    """Past DraftKings lines from EndzoneAPI, in the shape real_lines returns (see prop_splits.past_lines)."""
    from betbot.nfl.prop_splits import past_lines
    e = past_lines(conn).rename(columns={"over_ticket_pct": "over_tickets", "over_handle_pct": "over_handle"})
    e["market"] = "player_" + e["prop_type"]
    e = e[e["market"].isin(CONVICTION_MARKETS)]
    return e[["season", "week", "player", "market", "line", "over_tickets", "over_handle"]]


def score_lines(conn, lines, quantiles, cache=None):
    """Score each (season, week, player, market, line) the way the live board does: a model fitted on earlier weeks
    only, signals from information before that game, the market penalty from the saved splits when there are any.
    A missing line (NaN) is filled with a book-style stand-in (the slow average) and marked as such."""
    from betbot.names import normalize
    cache = {} if cache is None else cache
    rows = []
    for market, stat in CONVICTION_MARKETS.items():
        part = lines[lines["market"] == market]
        if part.empty or stat not in quantiles:
            continue
        df = _frame(conn, stat, MARKETS[market][1], cache)
        names = df.assign(key=df["player"].map(normalize)).groupby("key")["player_id"].agg(set)
        for (season, week), wk in part.groupby(["season", "week"]):
            train = df[(df["season"] < season) | ((df["season"] == season) & (df["week"] < week))]
            if len(train) < 200:
                continue
            params = model.fit_calibrated(train, stat)
            target = model.predict(df[(df["season"] == season) & (df["week"] == week)], params)
            by_player = target.drop_duplicates("player_id").set_index("player_id")
            for r in wk.itertuples():
                ids = names.get(normalize(r.player), set())
                if len(ids) != 1 or next(iter(ids)) not in by_player.index:
                    continue
                t = by_player.loc[[next(iter(ids))]].assign(group=STAT_GROUP[stat])
                if not eligible(t).iloc[0]:
                    continue  # the history never scored players like this
                source = r.source
                line = r.line
                if pd.isna(line):
                    line, source = max(np.floor(t["y_long"].fillna(t["base"]).iloc[0]) + 0.5, 0.5), "stand-in line"
                p = model.prob_over(params, t["mu"].iloc[0], t["sigma"].iloc[0], line)
                strengths = signals(t, [line], [p])
                raw, sign = aggregate(strengths)
                if pd.isna(raw.iloc[0]):
                    continue
                over = sign.iloc[0] > 0
                # no market penalty here: live splits are often missing, so the evidence must not assume them
                score = float(to_score(raw.iloc[0], quantiles[stat]))
                y = t["y"].iloc[0]
                rows.append({"season": season, "week": week, "player": r.player, "market": market, "line": line,
                             "source": source, "side": _name(sign.iloc[0]), "score": score,
                             # a landing exactly on a whole-number line is a refund, not a win or a loss
                             "won": None if pd.isna(y) or y == line else int((y > line) if over else (y < line)),
                             "actual": y,
                             "text": drivers(t.iloc[0], line, strengths.iloc[0], sign.iloc[0], t.iloc[0]["team"])})
    return pd.DataFrame(rows)


def real_evidence(scored):
    """The scored rows that count as hit-rate evidence: real posted lines only (never a stand-in line), already played."""
    if not len(scored):
        return scored
    return scored[(scored["source"] == "DraftKings line") & scored["won"].notna()]


def parse_weeks(text):
    """'3' -> range(3, 4); '1-5' -> range(1, 6)."""
    lo, _, hi = text.partition("-")
    return range(int(lo), int(hi or lo) + 1)


def build_tables(conn, cache=None):
    """Run the history, derive each stat's score scale, then measure hit rates on both sets. Takes about a minute."""
    cache = {} if cache is None else cache
    hist = history_rows(conn, cache)
    hist = hist[hist["raw"].notna()].copy()
    quantiles = {stat: np.quantile(g["raw"], np.linspace(0, 1, 1001)).tolist() for stat, g in hist.groupby("stat")}
    hist["score"] = np.nan
    for stat, g in hist.groupby("stat"):
        hist.loc[g.index, "score"] = to_score(g["raw"], quantiles[stat])
    real = real_evidence(score_lines(conn, real_lines(conn), quantiles, cache))
    tables = {"quantiles": quantiles, "history": hit_table(hist["score"], hist["won"]),
              "real": hit_table(real["score"], real["won"]) if len(real) else {},
              "history_n": int(len(hist)), "real_n": int(len(real)), "version": model.latest_week(conn),
              "scoring": scoring_key()}
    model.save_params(conn, {"stat": TABLE_KEY, **tables}, tables["version"])
    return tables


def scoring_key():
    """Identifies the scoring rules (code version, signals, weights): tables built under other rules are stale."""
    return f"{SCORING_VERSION}:{json.dumps(WEIGHTS, sort_keys=True)}:{MIN_SIGNALS}"


def load_tables(conn):
    return model.load_params(conn, TABLE_KEY)


def ensure_tables(conn):
    """The stored tables, rebuilt when a new week of games has arrived or the scoring rules have changed."""
    tables = load_tables(conn)
    if tables is None or tables["version"] < model.latest_week(conn) or tables.get("scoring") != scoring_key():
        print("building the conviction tables (about a minute)...")
        tables = build_tables(conn)
    return tables


# ---------- today's picks ----------

def score_picks(picks, tables):
    """Score the board's prop picks (those with a model row and a market we score). Returns one dict per prop
    that has enough signals, with the side, score, the hit-rate evidence and the price at that side."""
    out = []
    quantiles = tables["quantiles"]
    for p in picks:
        if p.get("kind") == "game" or p.get("market") not in CONVICTION_MARKETS or "row" not in p:
            continue
        stat = CONVICTION_MARKETS[p["market"]]
        if stat not in quantiles:
            continue  # no score scale for this stat (not enough history)
        row = pd.Series(p["row"])
        row["group"] = STAT_GROUP[stat]
        if not eligible(pd.DataFrame([row])).iloc[0]:
            continue  # a rookie or backup: the history that defines the scale never scored players like this
        strengths = signals(pd.DataFrame([row]), [p["line"]], [p["p_over_model"]])
        raw, sign = aggregate(strengths)
        if pd.isna(raw.iloc[0]):
            continue
        side_name = _name(sign.iloc[0])
        side = next((s for s in p["sides"] if s["side"] == side_name), None)
        if side is None or side["price"] is None:
            continue
        fair, _ = no_vig(side["price"], side["other_price"])
        penalty, notes = market_penalty(side["bets_pct"], side["money_pct"], side["line_move"], side["market_prob"], fair)
        score = max(0.0, float(to_score(raw.iloc[0], quantiles[stat])) - penalty)
        out.append({"player": p["player"], "player_id": p["player_id"], "team": p["team"], "market": p["market"],
                    "stat": stat, "game_id": p["game_id"], "event_id": p.get("event_id"), "line": p["line"],
                    "side": side_name, "book": p["book"], "price": side["price"], "other_price": side["other_price"],
                    "fair": fair, "score": score, "penalty": penalty, "market_notes": notes,
                    "why": drivers(row, p["line"], strengths.iloc[0], sign.iloc[0], p["team"])})
    return out


def select(scored, tables):
    """The scored props that qualify: 95+, real-line evidence for that range with enough props, a hit rate above the
    book's no-vig price. Adds the hit rate, EV per $100 and the evidence text."""
    real, hist = tables["real"].get("95+"), tables["history"].get("95+")
    picks = []
    if not real or not real["hit"] or real["n"] < MIN_HISTORY:
        return picks
    for s in scored:
        if s["score"] < THRESHOLD or real["hit"] <= s["fair"]:
            continue
        ev = expected_value(real["hit"], s["price"]) * 100
        if ev <= 0:
            continue
        low, high = _wilson(real["hit"], real["n"])
        caution = " [range includes the book's price: not yet statistically better than it]" if low <= s["fair"] else ""
        picks.append(dict(s, hit=real["hit"], hit_n=real["n"], ev100=ev, uncertain=low <= s["fair"],
                          evidence=f"Score {s['score']:.0f} | hits {real['hit']:.0%} historically ({real['n']} props, "
                                   f"95% range {low:.0%}-{high:.0%}, real DraftKings lines this season){caution}"
                                   + (f"; {hist['hit']:.0%} on {hist['n']:,} older stand-in-line props" if hist and hist['hit'] else "")))
    return sorted(picks, key=lambda x: -x["ev100"])


def parlay_legs(picks):
    """Parlay legs whose probability is the measured hit rate (not the score)."""
    return [{"label": f"{p['player']} {p['side']} {p['line']:g} {p['market'].replace('player_', '')}",
             "prob": p["hit"], "price": p["price"], "ev": expected_value(p["hit"], p["price"]),
             "edge": p["hit"] - p["fair"], "game": p["game_id"], "side": p["side"], "market": p["market"],
             "kind": "prop", "line": p["line"], "stat": p["stat"], "player_id": p["player_id"], "team": p["team"]} for p in picks]


def parlays_for(conn, picks, max_legs=3, top=10):
    from betbot.nfl import correlation, parlays
    legs = parlay_legs(picks)
    if len(legs) < 2:
        return []
    rho = correlation.rho(correlation.cached(conn, sorted({leg["stat"] for leg in legs})))
    return parlay.build(legs, rho, parlays.conflicts, max_legs=max_legs, top=top, rank="ev")


# ---------- printing ----------

def format_pick(p):
    return (f"{p['player']} | {p['market'].replace('player_', '')} {p['line']:g} {p['side']} | {p['book']} "
            f"{p['price']:+d} | {p['evidence']} | book implied {p['fair']:.1%} | EV {p['ev100']:+.0f} per $100\n"
            f"    why: {p['why']}")


def print_report(picks, checked, parlay_results, tables):
    print(decision.NO_EDGE_WARNING)
    if not picks:
        print(f"No picks at score {THRESHOLD}+ today ({checked} props checked)")
        real = tables["real"].get("95+") if tables else None
        if real is None or not real["hit"] or real["n"] < MIN_HISTORY:
            print(f"(the 95+ range has only {0 if real is None else real['n']} real-line props so far; "
                  f"{MIN_HISTORY} are needed before its hit rate counts)")
        return
    print(f"{len(picks)} picks at score {THRESHOLD}+ ({checked} props checked)")
    for p in picks:
        print("\n" + format_pick(p))
    if not parlay_results:
        print("\nNo positive-EV parlays from these picks.")
    for r in parlay_results:
        print(f"\n{len(r['legs'])} legs at {r['price']:+d}: pays ${(r['decimal'] - 1) * 100:,.0f} per $100,"
              f" true chance {r['true_prob']:.1%} from the hit rates (independent would be {r['independent_prob']:.1%}),"
              f" EV {r['ev']:+.1%}"
              + (" | same-game: books usually pay less than these multiplied odds" if r["same_game"] else ""))
        for leg in r["legs"]:
            print(f"    {leg['label']} ({leg['price']:+d}, hits {leg['prob']:.0%})")


def run(conn, picks, max_legs=2):
    """Score the board's picks and print the 95+ ones with parlays. Returns the qualifying picks."""
    tables = ensure_tables(conn)
    scored = score_picks(picks, tables)
    chosen = select(scored, tables)
    results = parlays_for(conn, chosen, max_legs)
    print_report(chosen, len(scored), results, tables)
    from betbot.nfl import parlays
    parlays.log(conn, results)
    return chosen


def log_picks(conn, chosen, made_at=None):
    """Save the shown picks for later grading, with the probability we used (the hit rate) and the score."""
    made_at = made_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [{"kind": "conviction", "made_at": made_at, "game_id": p["game_id"], "event_id": p["event_id"],
             "player": p["player"], "player_id": p["player_id"], "market": p["market"], "side": p["side"],
             "line": p["line"], "price": p["price"], "other_price": p["other_price"], "book": p["book"],
             "model_prob": p["hit"], "implied_prob": p["fair"], "vig_removed": int(p["other_price"] is not None),
             "p_over": None, "mu": None, "conviction": p["score"]} for p in chosen]
    with conn:
        return db.save_predictions(conn, rows)


# ---------- single-player check ----------

def explain_player(conn, name, market="player_pass_yds", season=None, weeks=range(1, 6)):
    """The score for one player each week: against the real posted line when one is saved, else a stand-in line."""
    tables = ensure_tables(conn)
    season = season or current_season()
    real = real_lines(conn)
    have = real[(real["player"] == name) & (real["market"] == market) & (real["season"] == season)]
    lines = []
    for week in weeks:
        got = have[have["week"] == week]
        lines.append(got.iloc[0].to_dict() if len(got) else
                     {"season": season, "week": week, "player": name, "market": market, "line": np.nan,
                      "over_tickets": np.nan, "over_handle": np.nan, "source": "stand-in line"})
    return score_lines(conn, pd.DataFrame(lines), tables["quantiles"])


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Conviction score tables and single-player checks")
    ap.add_argument("--build", action="store_true", help="rebuild the history and hit-rate tables")
    ap.add_argument("--player", help="score one player week by week, e.g. 'Tyler Shough'")
    ap.add_argument("--market", default="player_pass_yds")
    ap.add_argument("--weeks", default="1-5")
    args = ap.parse_args(argv)
    conn = db.connect(os.getenv("DB_PATH", "betbot.db"))
    if args.build:
        tables = build_tables(conn)
        print(f"{tables['history_n']:,} history props, {tables['real_n']:,} real-line props")
        for label in ("history", "real"):
            print(f"\nhit rate by score range ({'real DraftKings lines' if label == 'real' else '2019-2025 stand-in lines'})")
            for rng, v in tables[label].items():
                if rng in ("90-95", "95-100", "95+", "85-90", "80-85", "50-55", "0-5"):
                    print(f"  {rng:>7}: {v['n']:>6} props, hit {v['hit']:.1%}" if v["hit"] is not None else f"  {rng}: none")
    if args.player:
        res = explain_player(conn, args.player, args.market, weeks=parse_weeks(args.weeks))
        for r in res.itertuples():
            result = "pending" if r.won is None or pd.isna(r.won) else ("hit" if r.won else "missed")
            print(f"week {r.week}: score {r.score:.0f} {r.side} {r.line:g} ({r.source}), actual {r.actual:g} -> {result}\n"
                  f"    {r.text}")


if __name__ == "__main__":
    main()
