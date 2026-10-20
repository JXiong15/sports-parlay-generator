"""Past kickoff weather beyond what nflverse records: rain and wind gusts at each home stadium, from Open-Meteo's free
archive (no key). One request per home city covers every season; stored per game as the rain (mm) and strongest gust
(mph) in the three hours from kickoff.

    .venv/bin/python -m betbot.nfl.collectors.weather_history
"""
import time

import pandas as pd
import requests

from betbot.nfl.teams import HOME, local_hour

URL = "https://archive-api.open-meteo.com/v1/archive"
TABLE = "nfl_weather_history"


def kickoff_values(times, precip, gust, date, hour, hours=3, index=None):
    """(rain mm, max gust mph) over `hours` hours from local_hour on `date`; None if the hours aren't in the data."""
    start = f"{date}T{int(hour):02d}:00"
    index = {t: i for i, t in enumerate(times)} if index is None else index
    if start not in index:
        return None, None
    i = index[start]
    window = slice(i, i + hours)
    rain = [x for x in precip[window] if x is not None]
    gusts = [x for x in gust[window] if x is not None]
    return (sum(rain) if rain else None), (max(gusts) if gusts else None)


def _team_series(team, start_date, end_date):
    lat, lon, _ = HOME[team]
    params = {"latitude": lat, "longitude": lon, "start_date": start_date, "end_date": end_date,
              "hourly": "precipitation,wind_gusts_10m", "timezone": "auto", "wind_speed_unit": "mph"}
    for wait in (65, 130, 260, 0):          # the free archive rate-limits (429): wait out the minute / hour window
        resp = requests.get(URL, params=params, timeout=120)
        if resp.status_code != 429 or not wait:
            break
        time.sleep(wait)
    resp.raise_for_status()
    h = resp.json()["hourly"]
    return h["time"], h["precipitation"], h["wind_gusts_10m"]


def load(conn, end_date=None):
    """Fills in every played home game not yet saved."""
    end_date = end_date or (pd.Timestamp.today() - pd.Timedelta(days=6)).strftime("%Y-%m-%d")
    games = pd.read_sql_query("SELECT game_id, gameday, gametime, home_team FROM nfl_games WHERE home_score IS NOT NULL"
                              " AND season >= 2012 AND gameday <= ?", conn, params=(end_date,))
    try:
        done = {g for (g,) in conn.execute(f"SELECT game_id FROM {TABLE}")}
    except Exception:
        done = set()
    games = games[~games["game_id"].isin(done)]
    rows = []
    for team, chunk in games.groupby("home_team"):
        if team not in HOME:
            continue
        try:
            # only the dates still missing, not 14 years every run
            times, precip, gust = _team_series(team, str(chunk["gameday"].min())[:10], str(chunk["gameday"].max())[:10])
        except requests.RequestException as e:
            print(f"weather history {team}: skipped ({e})")
            continue
        index = {t: i for i, t in enumerate(times)}
        for r in chunk.itertuples():
            rain, wind = kickoff_values(times, precip, gust, str(r.gameday)[:10],
                                        local_hour(r.gameday, r.gametime, team), index=index)
            if rain is not None and wind is not None:      # a hole in the archive is retried next run, not saved
                rows.append((r.game_id, rain, wind))
        time.sleep(1)
    if rows:
        with conn:
            conn.executemany(f"INSERT OR REPLACE INTO {TABLE} VALUES (?, ?, ?)", rows)
    print(f"weather history: {len(rows)} games saved")


if __name__ == "__main__":
    import os
    from betbot import db
    load(db.connect(os.getenv("DB_PATH", "betbot.db")))
