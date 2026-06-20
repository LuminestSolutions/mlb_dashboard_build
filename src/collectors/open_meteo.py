"""Open-Meteo weather collector scaffold."""

from __future__ import annotations

import requests

BASE_URL = "https://api.open-meteo.com/v1/forecast"


def get_stadium_weather(lat: float, lon: float) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m,precipitation_probability",
        "forecast_days": 2,
        "timezone": "UTC",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
    }
    response = requests.get(BASE_URL, params=params, timeout=20)
    response.raise_for_status()
    return response.json()
