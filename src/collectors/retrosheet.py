"""Retrosheet season-level game result download."""

from __future__ import annotations

import csv
import io
import zipfile

import requests

BASE_URL = "https://www.retrosheet.org/downloads"


def get_season_gameinfo(season: int) -> list[dict[str, str]]:
    if season < 1898 or season > 2025:
        raise ValueError("Retrosheet CSV seasons currently run from 1898 through 2025")
    response = requests.get(f"{BASE_URL}/{season}/{season}csvs.zip", timeout=60)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        member = next((name for name in archive.namelist() if name.lower().endswith("gameinfo.csv")), None)
        if not member:
            raise ValueError(f"gameinfo.csv was not found in the Retrosheet {season} archive")
        with archive.open(member) as raw_file:
            reader = csv.DictReader(io.TextIOWrapper(raw_file, encoding="utf-8-sig"))
            return list(reader)
