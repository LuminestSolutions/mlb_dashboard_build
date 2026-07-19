"""Public MLB Stats API collectors used by the live model context."""

from __future__ import annotations

import requests

BASE_URL = "https://statsapi.mlb.com/api/v1"


def _get(path: str, params: dict | None = None, api_marker: str = "", timeout: int = 20) -> dict:
    headers = {"X-API-Key": api_marker} if api_marker else None
    response = requests.get(f"{BASE_URL}{path}", params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def get_schedule(date_str: str, api_marker: str = "") -> dict:
    params = {"sportId": 1, "date": date_str, "hydrate": "probablePitcher,venue,linescore"}
    return _get("/schedule", params=params, api_marker=api_marker)


def get_schedule_range(start_date: str, end_date: str, api_marker: str = "") -> dict:
    params = {
        "sportId": 1,
        "startDate": start_date,
        "endDate": end_date,
        "hydrate": "probablePitcher,venue,linescore",
    }
    return _get("/schedule", params=params, api_marker=api_marker, timeout=30)


def get_game_boxscore(game_pk: str | int, api_marker: str = "") -> dict:
    return _get(f"/game/{game_pk}/boxscore", api_marker=api_marker)


def get_team_roster(team_id: int, date_str: str, api_marker: str = "") -> dict:
    return _get(
        f"/teams/{team_id}/roster",
        params={"rosterType": "40Man", "date": date_str},
        api_marker=api_marker,
    )
