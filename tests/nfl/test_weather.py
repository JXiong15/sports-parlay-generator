from betbot import db
from betbot.nfl.collectors import weather
from betbot.nfl.collectors.weather import choose_place, scoreboard_games

GLENDALE = [{"latitude": 34.1, "longitude": -118.2, "country_code": "US", "admin1": "California", "population": 196000},
            {"latitude": 33.5, "longitude": -112.2, "country_code": "US", "admin1": "Arizona", "population": 248000},
            {"latitude": 33.6, "longitude": -112.0, "country_code": "US", "admin1": "Arizona", "population": 900}]


def test_choose_place_uses_state_and_population():
    assert choose_place(GLENDALE, "CA", "USA") == (34.1, -118.2)  # right state beats bigger city
    assert choose_place(GLENDALE, "AZ", "USA") == (33.5, -112.2)  # bigger of the two in Arizona
    assert choose_place(GLENDALE, "TX", "USA") is None  # wrong state: report, don't guess
    london = [{"latitude": 51.5, "longitude": -0.1, "country": "United Kingdom", "admin1": "England", "population": 8e6},
              {"latitude": 43.0, "longitude": -81.2, "country": "Canada", "admin1": "Ontario", "population": 4e5}]
    assert choose_place(london, None, "England") == (51.5, -0.1)


def test_scoreboard_games():
    data = {"events": [{"id": "401", "date": "2026-10-18T17:00Z", "weather": {"displayValue": "Sunny", "temperature": 60},
                        "competitions": [{"venue": {"fullName": "Lambeau Field", "indoor": False,
                                                    "address": {"city": "Green Bay", "state": "WI", "country": "USA"}}}]},
                       {"id": "402", "date": "2026-10-18T17:00Z",
                        "competitions": [{"venue": {"fullName": "Dome", "indoor": True, "address": {}}}]}]}
    assert scoreboard_games(data) == [
        (401, "2026-10-18T17:00Z", "Lambeau Field", "Green Bay", "WI", "USA", 0, "Sunny", 60),
        (402, "2026-10-18T17:00Z", "Dome", None, None, None, 1, None, None)]


def test_update_matches_games_caches_cities_and_keeps_old_forecast(monkeypatch):
    conn = db.connect(":memory:")
    from betbot.nfl.collectors.nflverse import replace_table
    replace_table(conn, "nfl_games", ["game_id", "espn"], [["2026_06_DAL_GB", 401]])
    board = {"events": [{"id": "401", "date": "2999-10-18T17:00Z",
                         "competitions": [{"venue": {"fullName": "Lambeau Field", "indoor": False,
                                                     "address": {"city": "Green Bay", "state": "WI", "country": "USA"}}}]},
                        {"id": "999", "date": "2999-10-18T17:00Z", "competitions": [{"venue": {}}]}]}
    calls = []

    def fake_get(url, **params):
        calls.append(url)
        if url == weather.SCOREBOARD_URL:
            return board
        if url == weather.GEOCODE_URL:
            return {"results": [{"latitude": 44.5, "longitude": -88.0, "country_code": "US", "admin1": "Wisconsin"}]}
        return {"hourly": {"temperature_2m": [40.0], "wind_speed_10m": [18.0], "wind_gusts_10m": [30.0],
                           "precipitation_probability": [70]}}
    monkeypatch.setattr(weather, "_get", fake_get)
    monkeypatch.setattr(weather.time, "sleep", lambda s: None)
    weather.update(conn)
    weather.update(conn)
    assert calls.count(weather.GEOCODE_URL) == 1  # city looked up once
    assert conn.execute("SELECT game_id, wind_mph, precip_pct FROM nfl_weather").fetchall() == [("2026_06_DAL_GB", 18.0, 70)]

    board["events"][0]["date"] = "2000-01-01T17:00Z"  # kicked off: no new forecast, old one kept
    weather.update(conn)
    assert conn.execute("SELECT wind_mph FROM nfl_weather").fetchone() == (18.0,)
