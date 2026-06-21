"""The Odds API collector scaffold."""

from __future__ import annotations

import requests

BASE_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"


def get_mlb_odds(api_key: str, regions: str = "us") -> list[dict]:
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": "h2h,spreads,totals",
        "oddsFormat": "american",
    }
    response = requests.get(BASE_URL, params=params, timeout=20)
    response.raise_for_status()
    return response.json()
