"""NFL teams."""
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

# full team name (The Odds API, ESPN) -> nflverse abbreviation
TEAMS = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL", "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR", "Chicago Bears": "CHI", "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL", "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX", "Kansas City Chiefs": "KC",
    "Los Angeles Rams": "LA", "Los Angeles Chargers": "LAC", "Las Vegas Raiders": "LV", "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN", "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT", "Seattle Seahawks": "SEA",
    "San Francisco 49ers": "SF", "Tampa Bay Buccaneers": "TB", "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}


# home stadium (latitude, longitude, IANA time zone) by nflverse abbreviation, for travel and body-clock effects
HOME = {
    "ARI": (33.53, -112.26, "America/Phoenix"), "ATL": (33.76, -84.40, "America/New_York"),
    "BAL": (39.28, -76.62, "America/New_York"), "BUF": (42.77, -78.79, "America/New_York"),
    "CAR": (35.23, -80.85, "America/New_York"), "CHI": (41.86, -87.62, "America/Chicago"),
    "CIN": (39.10, -84.52, "America/New_York"), "CLE": (41.51, -81.70, "America/New_York"),
    "DAL": (32.75, -97.09, "America/Chicago"), "DEN": (39.74, -105.02, "America/Denver"),
    "DET": (42.34, -83.05, "America/New_York"), "GB": (44.50, -88.06, "America/Chicago"),
    "HOU": (29.68, -95.41, "America/Chicago"), "IND": (39.76, -86.16, "America/New_York"),
    "JAX": (30.32, -81.64, "America/New_York"), "KC": (39.05, -94.48, "America/Chicago"),
    "LA": (33.95, -118.34, "America/Los_Angeles"), "LAC": (33.95, -118.34, "America/Los_Angeles"),
    "LV": (36.09, -115.18, "America/Los_Angeles"), "MIA": (25.96, -80.24, "America/New_York"),
    "MIN": (44.97, -93.26, "America/Chicago"), "NE": (42.09, -71.26, "America/New_York"),
    "NO": (29.95, -90.08, "America/Chicago"), "NYG": (40.81, -74.07, "America/New_York"),
    "NYJ": (40.81, -74.07, "America/New_York"), "PHI": (39.90, -75.17, "America/New_York"),
    "PIT": (40.45, -80.02, "America/New_York"), "SEA": (47.60, -122.33, "America/Los_Angeles"),
    "SF": (37.40, -121.97, "America/Los_Angeles"), "TB": (27.98, -82.50, "America/New_York"),
    "TEN": (36.17, -86.77, "America/Chicago"), "WAS": (38.91, -76.86, "America/New_York"),
}


@lru_cache(maxsize=None)
def _zone(name):
    return ZoneInfo(name)


_EASTERN = ZoneInfo("America/New_York")


def local_hour(gameday, gametime, team):
    """Hour (with minutes as a fraction) on `team`'s clock when a game kicks off at `gametime` Eastern on `gameday`.
    A missing kickoff time counts as 1pm Eastern."""
    time = gametime if isinstance(gametime, str) and len(gametime) >= 5 else "13:00"
    kickoff = datetime.fromisoformat(f"{str(gameday)[:10]}T{time[:5]}").replace(tzinfo=_EASTERN)
    local = kickoff.astimezone(_zone(HOME[team][2]))
    return local.hour + local.minute / 60
