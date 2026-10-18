import requests

BASE_URL = "https://api.the-odds-api.com/v4"


def _get(path, params):
    try:
        resp = requests.get(f"{BASE_URL}{path}", params=params, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        # requests' messages include the full URL, which carries the API key; `from None` drops that traceback too
        status = getattr(e.response, "status_code", None)
        raise RuntimeError(f"The Odds API request to {path} failed ({status or e.__class__.__name__})") from None
    return resp.json(), resp.headers.get("x-requests-remaining")


def get_events(api_key, sport):
    """Upcoming and live events. Costs no credits."""
    events, _ = _get(f"/sports/{sport}/events", {"apiKey": api_key})
    return events


def get_event_odds(api_key, sport, event_id, market, regions="us"):
    """Player props are only served per event. Costs 1 credit per market per region."""
    return _get(
        f"/sports/{sport}/events/{event_id}/odds",
        {"apiKey": api_key, "regions": regions, "markets": market, "oddsFormat": "american"},
    )


def get_game_odds(api_key, sport, markets="h2h,spreads,totals", regions="us"):
    """Moneyline, spread and total for every upcoming game in one request: 1 credit per market per region (3 total),
    however many games there are."""
    return _get(
        f"/sports/{sport}/odds",
        {"apiKey": api_key, "regions": regions, "markets": markets, "oddsFormat": "american"},
    )


def game_odds_kind(sport):
    """odds_pulls key for a sport's game-odds request (one request covers every game of that sport)."""
    return f"game_odds:{sport}"


def parse_game_odds(events, fetched_at):
    """side is the team name for moneyline/spread and Over/Under for totals; line is empty for moneylines."""
    rows = []
    for event in events:
        for book in event.get("bookmakers", []):
            for market in book["markets"]:
                for o in market["outcomes"]:
                    rows.append({
                        "event_id": event["id"],
                        "market": market["key"],
                        "side": o["name"],
                        "line": o.get("point"),
                        "price": o["price"],
                        "book": book["key"],
                        "book_updated_at": market.get("last_update") or book["last_update"],
                        "fetched_at": fetched_at,
                    })
    return rows


def parse_props(event_odds, fetched_at):
    rows = []
    for book in event_odds.get("bookmakers", []):
        for market in book["markets"]:
            for o in market["outcomes"]:
                rows.append({
                    "event_id": event_odds["id"],
                    "market": market["key"],
                    "player": o["description"],
                    "side": o["name"],
                    "line": o.get("point"),
                    "price": o["price"],
                    "book": book["key"],
                    "book_updated_at": market.get("last_update") or book["last_update"],
                    "fetched_at": fetched_at,
                })
    return rows
