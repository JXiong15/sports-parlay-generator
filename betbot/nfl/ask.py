"""Ask about one player's props: is the line a trap? `python -m betbot.nfl.ask "CeeDee Lamb" [reception_yds]`.
Reads only the saved database (run betbot.main first for fresh odds). Applies the picks rules in .claude/CLAUDE.md:
price spread across books, line movement, public splits, injury/role changes, last-8 hit rate and median."""
import os
import sys
import pandas as pd

from betbot import db
from betbot.names import normalize
from betbot.odds import implied_prob as implied

STATS = {"pass_yds": "passing_yards", "pass_tds": "passing_tds", "pass_attempts": "attempts",
         "pass_completions": "completions", "pass_interceptions": "passing_interceptions",
         "rush_yds": "rushing_yards", "rush_attempts": "carries", "receptions": "receptions",
         "reception_yds": "receiving_yards"}


def flags(over_p, under_p, line_spread, moved, over_bets, hit, median, mean, line, status, stale_hours,
          over_handle=None):
    """Trap warnings from already-computed numbers; returns a list of plain sentences."""
    out = []
    hold = over_p + under_p - 1
    if hold > 0.08:
        out.append(f"book margin {hold:.0%}: priced to take your money, not to be fair")
    if status in ("Out", "Doubtful", "Injured Reserve"):
        out.append(f"listed {status} but still has a price")
    if line_spread >= 1:
        out.append(f"books disagree by {line_spread:g} on the line: shop it or it's stale somewhere")
    if stale_hours is not None and stale_hours > 6:
        out.append(f"newest price is {stale_hours:.0f}h old")
    if over_bets is not None:
        side, pct = ("Over", over_bets) if over_bets >= 50 else ("Under", 100 - over_bets)
        if pct >= 70:
            out.append(f"{pct:.0f}% of bets on the {side}: the public side (alone this is not an edge: public sides win about 50%)")
            if moved and (moved > 0) == (side == "Under"):
                out.append(f"line moved against the public ({moved:+g}): sharp money on the other side")
    if over_bets is not None and over_handle is not None and abs(over_handle - over_bets) >= 15:
        side = "Over" if over_handle > over_bets else "Under"
        out.append(f"{over_bets:.0f}% of bets but {over_handle:.0f}% of money on the Over: big money is on the {side}")
    if hit is not None and median is not None and mean > line > median:
        out.append(f"average {mean:.1f} beats the line but median {median:g} doesn't: one big game inflates it")
    return out


def ask(conn, player, market=None):
    key = normalize(player)
    names = {n: normalize(n) for (n,) in conn.execute("SELECT DISTINCT player FROM lines")}
    mine = [n for n, k in names.items() if k == key] or [n for n, k in names.items() if key in k]
    if not mine:
        return f"No prop lines saved for '{player}'. Run betbot.main to pull them."
    name = mine[0]
    lines = pd.read_sql_query("SELECT market, side, line, price, book, fetched_at FROM lines WHERE player = ?",
                              conn, params=[name])
    weeks = pd.read_sql_query("SELECT * FROM nfl_player_weeks WHERE player_display_name = ? AND season >= 2025"
                              " ORDER BY season, week", conn, params=[name])
    team = weeks.team.iloc[-1] if len(weeks) else None
    inj = conn.execute("SELECT status FROM nfl_injuries WHERE name_key = ?", (key,)).fetchone()
    status = inj[0] if inj else None
    out_mates = [r[0] + f" ({r[1]})" for r in conn.execute(
        "SELECT player, position FROM nfl_injuries WHERE team = ? AND status IN ('Out','Doubtful','Injured Reserve')"
        " AND position IN ('QB','RB','WR','TE','T','OT','G','C') AND name_key != ?", (team, key))] if team else []
    ctx = conn.execute("SELECT away_team, home_team, home_spread, total, home_implied_points, away_implied_points, game_id"
                       " FROM nfl_game_context WHERE (home_team = ? OR away_team = ?) AND gameday >= date('now','-1 day')"
                       " ORDER BY gameday LIMIT 1", (team, team)).fetchone() if team else None
    res = [f"{name} ({team}), status: {status or 'not on injury list'}"]
    bad_air = None
    if ctx:
        res.append(f"game: {ctx[0]} @ {ctx[1]}, home spread {ctx[2]:+g}, total {ctx[3]:g}, implied {ctx[0]} {ctx[5]:g} / {ctx[1]} {ctx[4]:g}")
        w = conn.execute("SELECT indoor, temp_f, wind_mph, gust_mph, precip_pct FROM nfl_weather WHERE game_id = ?",
                         (ctx[6],)).fetchone()
        if w and not w[0]:
            res.append(f"weather: {w[1]:.0f}F, wind {w[2]:.0f} mph (gusts {w[3]:.0f}), rain chance {w[4]:.0f}%")
            if w[2] >= 15 or w[3] >= 25 or w[4] >= 50 or w[1] <= 32:
                bad_air = "bad weather (wind/rain/cold): passing and receiving yards tend to go Under"
        elif w:
            res.append("weather: indoors")
        g = weeks[weeks.game_id.notna()].assign(home=lambda z: z.game_id.str.split("_").str[-1] == z.team)
        res.append(f"tonight: {'HOME' if ctx[1] == team else 'AWAY'}; " + ", ".join(
            f"{'home' if h else 'away'} games avg " + "/".join(f"{k} {v:.0f}" for k, v in
                                                          x[[c for c in ('receiving_yards', 'rushing_yards', 'passing_yards') if x[c].sum() > 0]].mean().items())
            for h, x in g.groupby("home") if len(x) >= 2))
    if len(weeks):
        lg = pd.read_sql_query("SELECT sum(rushing_tds) * 1.0 / sum(carries) r, sum(receiving_tds) * 1.0 / sum(targets) c"
                               " FROM nfl_player_weeks WHERE season >= 2024 AND position_group IN ('RB','WR','TE')", conn).iloc[0]
        x = weeks.tail(8)
        exp, act = x.carries.sum() * lg.r + x.targets.sum() * lg.c, x.rushing_tds.sum() + x.receiving_tds.sum()
        if exp >= 0.5:
            res.append(f"TDs last {len(x)} games: {act:g} scored vs {exp:.1f} expected from {x.carries.sum():g} carries and "
                       f"{x.targets.sum():g} targets: " + ("DUE (running cold)" if act <= exp - 1 else
                                                           "running hot, expect fewer" if act >= exp + 1 else "about normal"))
    if out_mates:
        res.append("teammates out (their targets/carries go to others): " + ", ".join(out_mates))
    for m in sorted(set(lines.market)):
        short = m.replace("player_", "")
        if market and market not in (m, short):
            continue
        d = lines[lines.market == m]
        latest = d[d.fetched_at == d.fetched_at.max()]
        # each book's main line = the one priced closest to even money (the rest are alternate lines)
        if short == "anytime_td":
            yes = latest[latest.side.isin(["Yes", "Over"])]
            if not yes.empty:
                rate = (weeks.rushing_tds + weeks.receiving_tds).gt(0).tail(8).mean()
                res.append(f"\nanytime_td: best {yes.price.max():+d}, books imply {yes.price.apply(implied).mean():.0%} "
                           f"(includes margin); he scored in {rate:.0%} of his last 8 games")
            continue
        mains = {}
        for book, g in latest.groupby("book"):
            pairs = [(abs(g[(g.line == ln) & (g.side == "Over")].price.apply(implied).mean() - 0.5), ln)
                     for ln in set(g.line) if not g[(g.line == ln) & (g.side == "Over")].empty
                     and not g[(g.line == ln) & (g.side == "Under")].empty]
            if pairs:
                mains[book] = min(pairs)[1]
        if not mains:
            continue
        line = float(pd.Series(mains).median())
        spread = max(mains.values()) - min(mains.values())
        cur = latest[latest.line == line]
        o, u = cur[cur.side == "Over"], cur[cur.side == "Under"]
        if o.empty or u.empty:
            continue
        op, up = o.price.apply(implied).mean(), u.price.apply(implied).mean()
        opens = d.sort_values("fetched_at").groupby("book").first().line
        first = float(opens.median())
        age = (pd.Timestamp.now(tz="UTC") - pd.to_datetime(d.fetched_at, utc=True).max()).total_seconds() / 3600
        sp = conn.execute("SELECT over_bets, over_handle FROM prop_splits WHERE market = ? AND player = ?"
                          " AND abs(line - ?) <= 1 ORDER BY article_id DESC LIMIT 1", (m, name, line)).fetchone()
        stat = STATS.get(short)
        hit = med = mean = None
        if stat and stat in weeks:
            x = weeks[stat].tail(8).dropna()
            hit, med, mean = (x > line).mean(), float(x.median()), float(x.mean())
        head = f"\n{short}: line {line:g}, best over {o.price.max():+d} / best under {u.price.max():+d}, fair over {op / (op + up):.0%}"
        if hit is not None:
            head += f"; last 8: over {hit:.0%}, median {med:g}, mean {mean:.1f}"
        res.append(head)
        if sp:
            res.append(f"  splits: {sp[0]:.0f}% of bets / {sp[1]:.0f}% of money on the Over")
        odd = max(mains, key=lambda b: abs(mains[b] - line))
        found = flags(op, up, spread, line - first, sp[0] if sp else None, hit, med, mean, line, status, age,
                      sp[1] if sp else None)
        if bad_air and short.startswith(("pass", "reception")):
            found.append(bad_air)
        if spread >= 1:
            found = [f"{f} (odd one out: {odd} at {mains[odd]:g})" if f.startswith("books disagree") else f for f in found]
        res.append("  TRAP RISK: " + "; ".join(found) if found else "  trap check: nothing flagged")
    return "\n".join(res)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('usage: python -m betbot.nfl.ask "Player Name" [market]')
    conn = db.connect(os.getenv("DB_PATH", "betbot.db"))
    print(ask(conn, sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
