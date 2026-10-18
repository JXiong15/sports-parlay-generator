import requests

BASE_URL = "https://api.the-odds-api.com/v4"


def _get(path, params):
    resp = requests.get(f"{BASE_URL}{path}", params=params, timeout=30)
    resp.raise_for_status()
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
