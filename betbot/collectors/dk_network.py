"""Player-prop betting splits (% of handle = money, % of bets) from DraftKings Network's public articles.

DraftKings posts these as articles (a Sunday slate article plus one per prime-time game). New ones are found through
the site's public WordPress post list, and each is saved once so the data survives after DraftKings takes it down.
robots.txt allows these pages; one request per new article, 1 second apart.
shortcut: depends on the article table layouts; unreadable rows are reported, and a layout change shows up there.
"""
import html as html_lib
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

from betbot import db

POSTS_URL = "https://dknetwork.draftkings.com/wp-json/wp/v2/posts"
HEADERS = {"User-Agent": "betbot/0.1 (personal research script)"}
CATEGORIES = {"nfl": 33, "nba": 36, "mlb": 39, "nhl": 113, "ncaaf": 49, "ncaab": 105}
# DraftKings' prop wording -> The Odds API market key, so splits join to the lines table. Longest phrases first.
MARKET_KEYS = {
    "passing + rushing yards": "player_pass_rush_yds",
    "passing + rush yds": "player_pass_rush_yds",
    "rushing + receiving yards": "player_rush_reception_yds",
    "rushing + rec yds": "player_rush_reception_yds",
    "longest passing completion": "player_pass_longest_completion",
    "interceptions thrown": "player_pass_interceptions",
    "passing completions": "player_pass_completions",
    "passing completion": "player_pass_completions",
    "passing attempts": "player_pass_attempts",
    "passing touchdowns": "player_pass_tds",
    "passing yards": "player_pass_yds",
    "pass tds": "player_pass_tds",
    "pass yds": "player_pass_yds",
    "rushing attempts": "player_rush_attempts",
    "rushing yards": "player_rush_yds",
    "longest rush": "player_rush_longest",
    "rush yds": "player_rush_yds",
    "receiving yards": "player_reception_yds",
    "longest reception": "player_reception_longest",
    "receptions": "player_receptions",
    "rec yds": "player_reception_yds",
    "tackles + assists": "player_tackles_assists",
    "kicking points": "player_kicking_points",
    "field goals made": "player_field_goals",
    "extra points made": "player_pats",
}
# 'Rec Yds – 1st Half', 'receiving yards in the first quarter' -> period 1H / 1Q; full-game props have no period
_PERIOD_RE = re.compile(r"\s*(?:–|-|in the)\s*(?:1st|first)\s+(half|quarter)$", re.I)
_MARKET_RE = re.compile(r"^(.*?)\s+(" + "|".join(map(re.escape, MARKET_KEYS)) + r")$", re.I)


def _cells(row_html):
    return [html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c))).strip()
            for c in re.findall(r"<t[dh][^>]*>.*?</t[dh]>", row_html, re.S)]


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _split(cell):
    """(over %, under %) from 'Over: 74.5% Under: 25.5%', 'Over: 100.0% Under: N/A' or 'Under (60.0%)'."""
    over = re.search(r"Over:\s*([\d.]+)%", cell)
    under = re.search(r"Under:\s*([\d.]+)%", cell)
    if over or under:
        return _num(over and over.group(1)), _num(under and under.group(1))
    leader = re.search(r"(Over|Under)\s*\(([\d.]+)%\)", cell)
    if leader:
        pct = float(leader.group(2))
        return (pct, round(100 - pct, 1)) if leader.group(1) == "Over" else (round(100 - pct, 1), pct)
    return None, None


def parse_prop(text, line=None):
    """'Jordan Love Pass TDs 1.5' -> ('Jordan Love', 'player_pass_tds', 'Pass TDs', None, 1.5). Unknown -> None."""
    if line is None:
        m = re.match(r"^(.*?)\s+(\d+(?:\.\d+)?)$", text)
        if m:
            text, line = m.group(1), float(m.group(2))
    text = text.strip()
    period = _PERIOD_RE.search(text)
    if period:
        text = text[:period.start()]
        period = "1H" if period.group(1).lower() == "half" else "1Q"
    m = _MARKET_RE.match(text)
    if not m or line is None:
        return None
    return m.group(1), MARKET_KEYS[m.group(2).lower()], m.group(2), period, line


def parse(page_html):
    """(rows, unreadable prop texts) from every table that has a 'Player prop' column; rank tables are skipped."""
    rows, unreadable = [], []
    for table in re.findall(r"<table.*?</table>", page_html, re.S):
        trs = re.findall(r"<tr.*?</tr>", table, re.S)
        header = [c.lower() for c in _cells(trs[0])] if trs else []
        if "player prop" not in header:
            continue
        col = {name: header.index(name) for name in header}
        handle = next((i for name, i in col.items() if name.startswith("handle")), None)
        bets = next((i for name, i in col.items() if name.startswith("bet")), None)
        for tr in trs[1:]:
            cells = _cells(tr)
            if len(cells) != len(header):
                continue
            text = cells[col["player prop"]]
            if "line" in col and _num(cells[col["line"]]) is None:
                unreadable.append(f"{text} (DraftKings gives no line)")
                continue
            parsed = parse_prop(text, _num(cells[col["line"]]) if "line" in col else None)
            if parsed is None or handle is None or bets is None:
                unreadable.append(f"{text} (unknown wording: add it to MARKET_KEYS)")
                continue
            player, market, market_text, period, line = parsed
            over_handle, under_handle = _split(cells[handle])
            over_bets, under_bets = _split(cells[bets])
            rows.append({"game": cells[col["game"]] if "game" in col else None, "player": player, "market": market,
                         "market_text": market_text, "period": period, "line": line, "over_handle": over_handle,
                         "under_handle": under_handle, "over_bets": over_bets, "under_bets": under_bets})
    return rows, unreadable


def _get(url, **params):
    resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp


def articles(league, page=1):
    resp = _get(POSTS_URL, categories=CATEGORIES[league], search="prop bet splits", per_page=100, page=page,
                _fields="id,date,modified,link,slug")
    return [a for a in resp.json() if "prop-bet-splits" in a["slug"]], int(resp.headers.get("X-WP-TotalPages", 1))


def update(conn, league="nfl", all_pages=False):
    """Saves every prop-split article not saved yet (or edited since). Returns unreadable prop texts."""
    known = dict(conn.execute("SELECT id, modified FROM dk_articles"))
    page, pages, unreadable = 1, 1, []
    while page <= pages:
        found, pages = articles(league, page)
        for a in found:
            if known.get(a["id"]) == a["modified"]:
                continue
            time.sleep(1)  # polite to a public site
            rows, bad = parse(_get(a["link"]).text)
            unreadable += bad
            if not rows:  # taken down or reshaped: keep whatever was saved before
                print(f"no readable props in {a['link']}")
                continue
            with conn:
                db.save_prop_splits(conn, {"id": a["id"], "league": league, "link": a["link"], "published": a["date"],
                                           "modified": a["modified"],
                                           "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, rows)
            print(f"saved {len(rows)} props from {a['slug']}")
        page = page + 1 if all_pages else pages + 1
    if unreadable:
        print(f"{len(unreadable)} props skipped: {sorted(set(unreadable))[:10]}")
    return unreadable


if __name__ == "__main__":
    # every article DraftKings still has: python -m betbot.collectors.dk_network nfl --all
    update(db.connect(os.getenv("DB_PATH", "betbot.db")), sys.argv[1] if len(sys.argv) > 1 else "nfl", "--all" in sys.argv)
