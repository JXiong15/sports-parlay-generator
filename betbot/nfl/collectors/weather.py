"""Kickoff weather for this week's NFL games.

ESPN's scoreboard gives each game's stadium, city and indoor flag; Open-Meteo (free, no key) turns the city into
coordinates and gives the hourly forecast at kickoff: temperature, wind, gusts, chance of rain.
Past games already have kickoff temp/wind/roof in nflverse's nfl_games.
"""
import os
import time
from datetime import datetime, timezone

import requests

from betbot import db

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def _get(url, **params):
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def scoreboard_games(data):
    """[(espn event id, kickoff UTC, venue, city, state, country, indoor, ESPN condition, ESPN temp)]"""
    games = []
    for e in data.get("events", []):
        venue = e["competitions"][0].get("venue") or {}
        address = venue.get("address") or {}
        weather = e.get("weather") or {}
        games.append((int(e["id"]), e["date"], venue.get("fullName"), address.get("city"), address.get("state"),
                      address.get("country"), int(bool(venue.get("indoor"))), weather.get("displayValue"),
                      weather.get("temperature")))
    return games


def choose_place(results, state, country):
    """The geocoding result in the right state (US) or country, most populous first; None if nothing matches."""
    if country in ("USA", "United States"):
        want = US_STATES.get(state)
        results = [r for r in results if r.get("country_code") == "US" and (want is None or r.get("admin1") == want)]
    elif country:
        # ESPN says e.g. "England" or "Germany"; Open-Meteo's country/admin1 cover both spellings
        results = [r for r in results if country in (r.get("country"), r.get("admin1"))] or results
    results = sorted(results, key=lambda r: r.get("population") or 0, reverse=True)
    return (results[0]["latitude"], results[0]["longitude"]) if results else None


def coordinates(conn, city, state, country):
    """Cached in nfl_venues, so each city is looked up once."""
    row = conn.execute("SELECT latitude, longitude FROM nfl_venues WHERE city = ? AND state IS ? AND country IS ?",
                       (city, state, country)).fetchone()
    if row:
        return row
    found = choose_place(_get(GEOCODE_URL, name=city, count=20).get("results", []), state, country)
    if found:
        with conn:
            conn.execute("INSERT INTO nfl_venues VALUES (?, ?, ?, ?, ?)", (city, state, country, *found))
    return found


def kickoff_forecast(lat, lon, kickoff):
    hour = kickoff[:13] + ":00"  # ESPN gives '2026-10-18T17:00Z'
    hourly = _get(FORECAST_URL, latitude=lat, longitude=lon, timezone="UTC", start_hour=hour, end_hour=hour,
                  hourly="temperature_2m,wind_speed_10m,wind_gusts_10m,precipitation_probability",
                  temperature_unit="fahrenheit", wind_speed_unit="mph").get("hourly", {})
    first = lambda key: (hourly.get(key) or [None])[0]
    return {"temp_f": first("temperature_2m"), "wind_mph": first("wind_speed_10m"),
            "gust_mph": first("wind_gusts_10m"), "precip_pct": first("precipitation_probability")}


def update(conn):
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    espn_to_game = dict(conn.execute("SELECT espn, game_id FROM nfl_games WHERE espn IS NOT NULL"))
    rows, unmatched = [], []
    for espn_id, kickoff, venue, city, state, country, indoor, condition, espn_temp in scoreboard_games(
            _get(SCOREBOARD_URL)):
        game_id = espn_to_game.get(espn_id)
        if game_id is None:
            unmatched.append(espn_id)
            continue
        row = {"game_id": game_id, "kickoff": kickoff, "venue": venue, "city": city, "state": state,
               "country": country, "indoor": indoor, "espn_condition": condition, "espn_temp_f": espn_temp,
               "temp_f": None, "wind_mph": None, "gust_mph": None, "precip_pct": None, "fetched_at": fetched_at}
        place = coordinates(conn, city, state, country) if city else None
        # indoor games still get a forecast: retractable roofs can open, and it's cheap
        if place and kickoff > fetched_at:
            row.update(kickoff_forecast(*place, kickoff))
            time.sleep(0.2)
        elif not place and city:
            print(f"couldn't place {city}, {state}, {country} for {game_id}")
        rows.append(row)
    with conn:
        for row in rows:
            db.save_weather(conn, row)
    if unmatched:
        print(f"ESPN games not in nflverse schedule (refresh NFL stats?): {unmatched}")
    return rows


if __name__ == "__main__":
    for r in update(db.connect(os.getenv("DB_PATH", "betbot.db"))):
        if r["indoor"]:
            where = "indoors"
        elif r["temp_f"] is None:
            where = "already played (past weather is in nfl_games)"
        else:
            where = f"{r['temp_f']}F, wind {r['wind_mph']} mph (gusts {r['gust_mph']}), rain {r['precip_pct']}%"
        print(f"{r['game_id']:18} {r['venue']} ({r['city']}): {where}")
