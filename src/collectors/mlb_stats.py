"""MLB Stats API collector scaffold."""

from __future__ import annotations

import requests

BASE_URL = "https://statsapi.mlb.com/api/v1"


def get_schedule(date_str: str, api_marker: str = "") -> dict:
    params = {"sportId": 1, "date": date_str, "hydrate": "probablePitcher,venue,linescore"}
    headers = {"X-API-Key": api_marker} if api_marker else None
    response = requests.get(f"{BASE_URL}/schedule", params=params, headers=headers, timeout=20)
    response.raise_for_status()
    return response.json()
