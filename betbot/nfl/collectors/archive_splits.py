"""Past NFL game betting splits (% of bets, % of money/handle) from Internet Archive copies of public pages.

Sources (all public pages; the Internet Archive's Wayback Machine is meant for this kind of lookup):
  - Action Network /nfl/public-betting (2018 on). Only money % the page showed for free is kept, as in
    collectors/action_network.py; the PRO-locked numbers in the page data are ignored.
  - VSiN's DraftKings (and Circa) splits pages (2023 on).
  - DraftKings Network's splits page (2024 on), NFL games only.
Each saved copy is downloaded once (1.5 seconds apart), every row is matched to an nflverse game_id, and copies
already imported are skipped on re-runs. Only NFL-season dates are fetched.
shortcut: each reader follows the page layout of its era; rows it can't read are counted per copy in
archive_captures, so a new layout shows up there instead of failing silently.
"""
import gzip
import html as html_lib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests

from betbot import db
from betbot.collectors import action_network

CDX_URL = "https://web.archive.org/cdx/search/cdx"
SNAPSHOT_URL = "https://web.archive.org/web/{ts}id_/{url}"  # id_ = the page exactly as saved, no archive toolbar
HEADERS = {"User-Agent": "betbot/0.1 (personal research script)"}
PAGES = {
    "action_network": "www.actionnetwork.com/nfl/public-betting",
    "vsin_nfl": "data.vsin.com/nfl/betting-splits",
    "vsin": "data.vsin.com/betting-splits",
    "dk_network": "dknetwork.draftkings.com/draftkings-sportsbook-betting-splits",
}
NFL_MONTHS = {9, 10, 11, 12, 1, 2}

# nickname -> nflverse abbreviations it has had (older ones first); every source's team text contains one of these
NICKNAMES = {
    "Cardinals": ["ARI"], "Falcons": ["ATL"], "Ravens": ["BAL"], "Bills": ["BUF"], "Panthers": ["CAR"],
    "Bears": ["CHI"], "Bengals": ["CIN"], "Browns": ["CLE"], "Cowboys": ["DAL"], "Broncos": ["DEN"],
    "Lions": ["DET"], "Packers": ["GB"], "Texans": ["HOU"], "Colts": ["IND"], "Jaguars": ["JAX"],
    "Chiefs": ["KC"], "Rams": ["STL", "LA"], "Chargers": ["SD", "LAC"], "Raiders": ["OAK", "LV"],
    "Dolphins": ["MIA"], "Vikings": ["MIN"], "Patriots": ["NE"], "Saints": ["NO"], "Giants": ["NYG"],
    "Jets": ["NYJ"], "Eagles": ["PHI"], "Steelers": ["PIT"], "Seahawks": ["SEA"], "49ers": ["SF"],
    "Buccaneers": ["TB"], "Titans": ["TEN"], "Commanders": ["WAS"],
}
ALIASES = {"Redskins": "Commanders", "Football Team": "Commanders", "Washington": "Commanders"}
# abbreviations the sources use -> nickname (Action Network's old table shows only these)
ABBREVIATIONS = {abbr: nick for nick, abbrs in NICKNAMES.items() for abbr in abbrs}
ABBREVIATIONS.update({"JAC": "Jaguars", "WSH": "Commanders", "LAR": "Rams", "LV": "Raiders", "NYJ": "Jets"})
_NICK_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, list(NICKNAMES) + list(ALIASES)), key=len, reverse=True))
                      + r")\b")


def nicknames_in(text):
    """Team nicknames in the order they appear: 'Denver Broncos New York Jets' -> ['Broncos', 'Jets']."""
    return [ALIASES.get(n, n) for n in _NICK_RE.findall(text or "")]


def nickname(text):
    """One team's nickname from a full name, 'BUF Bills', or a bare abbreviation like 'OAK'."""
    found = nicknames_in(text)
    if found:
        return found[0]
    return ABBREVIATIONS.get((text or "").strip().upper())


def _get(address, params=None):
    for attempt in range(3):
        try:
            resp = requests.get(address, params=params, headers=HEADERS, timeout=90)
            resp.raise_for_status()
            body = resp.content
            # some copies are stored still gzip-compressed
            return (gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body).decode("utf-8", "ignore")
        except (requests.RequestException, OSError):
            if attempt == 2:
                raise
            time.sleep(10)


def _visible_text(page_html):
    return action_network._visible_text(page_html)


def _signed(s):
    s = (s or "").replace("−", "-").strip()
    if s.upper() in ("PK", "EVEN"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return None


def _row(away, home, market, side, line=None, price=None, bets=None, money=None, start=None):
    return {"away": away, "home": home, "market": market, "side": side, "line": line,
            "price": int(price) if price is not None else None, "bets_pct": bets, "money_pct": money,
            "start_time": start}


# ---------- Action Network ----------

def parse_action_network(page_html):
    """Three layouts: 2024+ (per-market bet_info), 2021-2023 (odds rows with *_public / *_money), and 2018-2020
    (a plain table with % of bets; money shown as locked)."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page_html, re.S)
    games = None
    if m:
        try:
            games = json.loads(m.group(1))["props"]["pageProps"]["scoreboardResponse"]["games"]
        except (KeyError, TypeError, ValueError):
            games = None
    if games and any("markets" in g for g in games):
        return [_row(r["away_team"], r["home_team"], r["market"], r["side"], r["line"], r["price"], r["bets_pct"],
                     r["money_pct"], r["start_time"]) for r in action_network.parse(page_html, "nfl", "")]
    if games:
        return _action_network_odds_rows(games, action_network._money_shown(_visible_text(page_html), games))
    return _action_network_table(page_html)


def _action_network_odds_rows(games, free_money):
    rows = []
    for g in games:
        teams = {t["id"]: t.get("full_name") for t in g.get("teams", [])}
        home = teams.get(g.get("home_team_id"))
        away = next((n for i, n in teams.items() if i != g.get("home_team_id")), None)
        o = next((x for x in g.get("odds", []) if x.get("book_id") == int(action_network.CONSENSUS_BOOK)), None)
        if not o:
            continue
        money = (lambda k: o.get(k)) if g["id"] in free_money else (lambda k: None)
        for side in ("home", "away"):
            rows.append(_row(away, home, "spread", side, o.get(f"spread_{side}"), o.get(f"spread_{side}_line"),
                             o.get(f"spread_{side}_public"), money(f"spread_{side}_money"), g.get("start_time")))
            rows.append(_row(away, home, "moneyline", side, None, o.get(f"ml_{side}"), o.get(f"ml_{side}_public"),
                             money(f"ml_{side}_money"), g.get("start_time")))
        for side in ("over", "under"):
            rows.append(_row(away, home, "total", side, o.get("total"), o.get(side), o.get(f"total_{side}_public"),
                             money(f"total_{side}_money"), g.get("start_time")))
    return rows


_AN_TABLE_GAME = re.compile(r"\|(\d{3})\|([A-Z]{2,3})\|(\d{3})\|([A-Z]{2,3})\|")


def _action_network_table(page_html):
    """2018-2020: '|307|OAK|308|SF|Fri 11/02 12:20AM|+4|-4|-1.5|-110|+1.5|-110|71%|29%|EDGE|' (spread market;
    the two percentages are % of bets, money is locked as EDGE). Newer free rows add two money percentages."""
    text = re.sub(r"\s*%", "%", re.sub(r"\|\s*", "|", _visible_text(page_html)))
    starts = list(_AN_TABLE_GAME.finditer(text))
    rows = []
    for m, nxt in zip(starts, starts[1:] + [None]):
        segment = text[m.end():nxt.start() if nxt else m.end() + 400]
        pct = [int(p) for p in re.findall(r"\|(\d{1,3})%", "|" + segment)]
        if len(pct) < 2:
            continue
        nums = [_signed(t) for t in segment.split("|")[1:12]]
        nums = [n for n in nums if n is not None]
        away, home = m.group(2), m.group(4)
        line_away, price_away, line_home, price_home = (nums[2:6] + [None] * 4)[:4] if len(nums) >= 6 else (None,) * 4
        money = pct[2:4] if len(pct) >= 4 and "EDGE" not in segment else [None, None]
        rows.append(_row(away, home, "spread", "away", line_away, price_away, pct[0], money[0]))
        rows.append(_row(away, home, "spread", "home", line_home, price_home, pct[1], money[1]))
    return rows


# ---------- VSiN ----------

def parse_vsin(page_html):
    """Each game: a header cell 'Away Team Home Team History ...' then 9 cells: spread lines, spread handle %,
    spread bets %, total lines, total handle %, total bets %, moneylines, moneyline handle %, moneyline bets %."""
    cells = [re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
             for c in re.findall(r"<t[dh][^>]*>(.*?)(?=<t[dh][^>]*>|</tr>|</table>)", page_html, re.S)]
    rows = []
    for i, cell in enumerate(cells):
        if "History" not in cell or i + 9 >= len(cells):
            continue
        teams = nicknames_in(cell.split("History")[0])
        if len(teams) != 2:
            continue
        away, home = teams
        spread, s_handle, s_bets, total, t_handle, t_bets, ml, m_handle, m_bets = cells[i + 1:i + 10]
        pct = lambda c: [int(p) for p in re.findall(r"(\d{1,3})%", c)][:2] + [None, None]
        nums = lambda c: [_signed(t) for t in c.split()[:2]] + [None, None]
        if not re.search(r"\d", spread + total + ml):
            continue
        for market, lines, handle, bets, sides in (("spread", nums(spread), pct(s_handle), pct(s_bets), ("away", "home")),
                                                  ("total", nums(total), pct(t_handle), pct(t_bets), ("over", "under")),
                                                  ("moneyline", nums(ml), pct(m_handle), pct(m_bets), ("away", "home"))):
            for k, side in enumerate(sides):
                line = None if market == "moneyline" else lines[k]
                price = lines[k] if market == "moneyline" else None
                rows.append(_row(away, home, market, side, line, price, bets[k], handle[k]))
    return rows


# ---------- DraftKings Network ----------

def parse_dk_network(page_html, snapshot_date):
    """'|LA Chargers @ |BUF Bills |9/27, 01:00PM |Spread|Odds|% Handle|% Bets|BUF Bills -7|-115 |79% |81% |...'
    Includes alternate lines. Pages default to other sports; only games between two NFL teams are kept."""
    parts = [p.strip() for p in re.sub(r"\s+", " ", _visible_text(page_html)).split("|")]
    parts = [p for p in parts if p and p != "-->"]  # blank spacer cells between values
    rows, i = [], 0
    while i < len(parts) - 3:
        if not parts[i].endswith("@"):
            i += 1
            continue
        away, home, when = parts[i][:-1].strip(), parts[i + 1], parts[i + 2]
        i += 3
        if not (nickname(away) and nickname(home)):
            continue
        start = _dk_date(when, snapshot_date)
        while i < len(parts) and parts[i] in ("Moneyline", "Spread", "Total"):
            market = parts[i].lower()
            i += 4  # market, Odds, % Handle, % Bets
            while i + 3 < len(parts) and parts[i] not in ("Moneyline", "Spread", "Total") and not parts[i].endswith("@"):
                label, price, handle, bets = parts[i:i + 4]
                if not (handle.endswith("%") and bets.endswith("%")):
                    break
                line = None
                if market == "total":
                    side, _, line = label.partition(" ")
                    side = side.lower()
                else:
                    m = re.match(r"^(.*?)(?:\s+([+\-−]?\d+(?:\.\d+)?|PK))?$", label)
                    team, line = m.group(1), m.group(2)
                    side = "home" if nickname(team) == nickname(home) else "away"
                rows.append(_row(away, home, market, side, _signed(line) if market != "moneyline" else None,
                                 _signed(price), _signed(bets[:-1]), _signed(handle[:-1]), start))
                i += 4
    return rows


def _dk_date(when, snapshot_date):
    m = re.match(r"(\d{1,2})/(\d{1,2})", when)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    year = snapshot_date.year + (1 if month < snapshot_date.month - 6 else 0) - (1 if month > snapshot_date.month + 6 else 0)
    return date(year, month, day).isoformat()


# ---------- matching to nflverse games ----------

def match_game(conn, away, home, around, start_time=None):
    """nflverse game_id for these two teams, the closest game from 2 days before to 10 days after `around`
    (or on the given start date). None if no such game: the row is still saved with its team names."""
    a, h = nickname(away), nickname(home)
    if not a or not h:
        return None
    day = (start_time or "")[:10] or around.isoformat()
    candidates = conn.execute(
        "SELECT game_id, away_team, home_team, gameday FROM nfl_games"
        " WHERE gameday BETWEEN date(?, '-2 days') AND date(?, '+10 days')", (day, day)).fetchall()
    teams = lambda abbr: ABBREVIATIONS.get(abbr)
    hits = [(abs((date.fromisoformat(g) - date.fromisoformat(day)).days), gid) for gid, ga, gh, g in candidates
            if {teams(ga), teams(gh)} == {a, h}]
    return min(hits)[1] if hits else None


def resolve_sides(conn, row, game_id):
    """Turns team-relative sides into the game's real home/away (a site may list a neutral-site game either way)."""
    if game_id is None or row["side"] not in ("home", "away"):
        return row
    away_abbr, home_abbr = conn.execute("SELECT away_team, home_team FROM nfl_games WHERE game_id = ?",
                                        (game_id,)).fetchone()
    team = row["home"] if row["side"] == "home" else row["away"]
    real = "home" if nickname(team) == ABBREVIATIONS.get(home_abbr) else "away"
    return dict(row, side=real)


# ---------- capture listing and import ----------

def _source_for(page, url):
    query = url.split("?", 1)[1] if "?" in url else ""
    if page.startswith("vsin"):
        if re.search(r"view=(?!nfl\b|circa\b)", query):
            return None  # another sport's view
        return "vsin_circa" if "circa" in query else "vsin_dk"
    if page == "dk_network":
        return "dk_network"
    return page


def captures(page):
    params = {"url": PAGES[page], "matchType": "prefix", "output": "json", "fl": "timestamp,original",
              "filter": "statuscode:200"}
    for attempt in range(5):
        # the archive's index sometimes answers empty or with an error page when busy: wait and retry
        try:
            body = _get(CDX_URL, params).strip()
        except (requests.RequestException, OSError):
            body = ""
        if body.startswith("["):
            break
        time.sleep(30 * (attempt + 1))
    else:
        raise RuntimeError(f"Internet Archive index unavailable for {PAGES[page]}; try again later")
    rows = json.loads(body)[1:]
    out = []
    for ts, url in rows:
        source = _source_for(page, url)
        if source and int(ts[4:6]) in NFL_MONTHS:
            out.append((source, ts, url))
    return out


def parse(source, page_html, snapshot_date):
    if source == "action_network":
        return parse_action_network(page_html)
    if source.startswith("vsin"):
        return parse_vsin(page_html)
    return parse_dk_network(page_html, snapshot_date)


def import_all(conn, pages=PAGES):
    done = {(s, ts, u) for s, ts, u in conn.execute("SELECT source, timestamp, url FROM archive_captures")}
    todo = [c for page in pages for c in captures(page) if c not in done]
    print(f"{len(todo)} saved copies to import ({len(done)} already done)")
    totals = {}
    for n, (source, ts, url) in enumerate(todo, 1):
        time.sleep(1.5)  # polite to the Internet Archive
        snap = datetime.strptime(ts[:8], "%Y%m%d").date()
        try:
            page_html = _get(SNAPSHOT_URL.format(ts=ts, url=url))
        except (requests.RequestException, OSError) as e:
            print(f"  skipped {ts} {url}: {e.__class__.__name__} (retried next run)")
            continue
        rows = parse(source, page_html, snap)
        saved = []
        for r in rows:
            gid = match_game(conn, r["away"], r["home"], snap, r["start_time"])
            saved.append(dict(resolve_sides(conn, r, gid), game_id=gid, source=source, timestamp=ts, url=url))
        with conn:
            db.save_archive_splits(conn, saved)
            conn.execute("INSERT OR REPLACE INTO archive_captures VALUES (?, ?, ?, ?, ?, ?)",
                         (source, ts, url, len(rows), sum(1 for s in saved if s["game_id"]),
                          datetime.now(timezone.utc).isoformat(timespec="seconds")))
        totals[source] = totals.get(source, 0) + len(rows)
        if n % 25 == 0 or n == len(todo):
            print(f"  {n}/{len(todo)} copies, rows so far: {totals}")
    return totals


if __name__ == "__main__":
    conn = db.connect(os.getenv("DB_PATH", "betbot.db"))
    import_all(conn, [p for p in PAGES if p in sys.argv[1:]] or PAGES)
    print(conn.execute("SELECT source, count(*), count(DISTINCT game_id), sum(money_pct IS NOT NULL),"
                       " min(substr(timestamp, 1, 4)), max(substr(timestamp, 1, 4)) FROM archive_splits"
                       " GROUP BY source").fetchall())
