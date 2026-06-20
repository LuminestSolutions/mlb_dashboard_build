"""SQLite connection helper scaffold."""

from __future__ import annotations

import os
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def get_engine() -> Engine:
    database_url = os.getenv("DATABASE_URL", "sqlite:///data/mlb_edge.db")
    return create_engine(database_url, future=True)
