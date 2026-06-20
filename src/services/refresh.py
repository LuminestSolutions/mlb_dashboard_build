"""Daily refresh service scaffold.

The Streamlit Refresh button should call run_daily_refresh().
"""

from __future__ import annotations


def run_daily_refresh(date_str: str) -> dict:
    # TODO: Connect collectors, normalizers, scoring engine, and SQLite persistence.
    return {"status": "prototype", "games": 0, "message": "Refresh service not wired yet"}
