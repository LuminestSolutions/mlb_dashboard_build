"""Local MLB Edge Model dashboard.

Run locally:
    streamlit run app.py

The app initializes a SQLite database in data/mlb_edge.db, seeds it from the
included sample CSV files, and keeps API-backed refreshes behind explicit setup.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

from src.collectors.mlb_stats import get_schedule
from src.collectors.odds_api import get_mlb_odds
from src.collectors.open_meteo import get_stadium_weather
from src.models.fair_line import edge_pct as calculate_edge_pct
from src.models.fair_line import implied_prob_to_american

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
BRANDING_DIR = DATA_DIR / "branding"
load_dotenv(BASE_DIR / ".env")
os.environ.setdefault("PYBASEBALL_CACHE", str(DATA_DIR / "pybaseball_cache"))
os.environ.setdefault("MPLCONFIGDIR", str(DATA_DIR / "matplotlib_config"))
DB_PATH = Path(os.getenv("MLB_EDGE_DB_PATH", DATA_DIR / "mlb_edge.db"))
SCHEMA_PATH = BASE_DIR / "schema.sql"
SUPABASE_SCHEMA_PATH = BASE_DIR / "supabase_schema.sql"
try:
    DISPLAY_TIMEZONE = ZoneInfo(os.getenv("MLB_EDGE_TIMEZONE", "America/New_York"))
except ZoneInfoNotFoundError:
    DISPLAY_TIMEZONE = datetime.now().astimezone().tzinfo or timezone.utc
DISPLAY_TIMEZONE_LABEL = os.getenv("MLB_EDGE_TIMEZONE_LABEL", "ET")

DEFAULT_SETTINGS = {
    "edge_threshold": "4.0",
    "min_line_difference_cents": "15",
    "min_confidence": "7.0",
    "default_stake_units": "1.0",
    "bankroll_units": "100",
    "bookmaker_preference": "DraftKings",
    "statcast_lookback_days": "14",
    "rule_profile": "Conservative",
    "use_manual_rules": "false",
    "dashboard_title": "MLB Edge Model",
    "dashboard_logo_path": "data/branding/mlb_edge_logo_default.png",
    "dashboard_password": "",
}

RULE_PROFILES = {
    "Very Conservative": {
        "edge_threshold": "6.0",
        "min_line_difference_cents": "25",
        "min_confidence": "8.5",
        "max_bets_per_day": 1,
        "stake_size": "0.25 to 0.50 units",
        "market_scope": "Prefer F5 ML or full-game ML only",
        "notes": "Confirmed starters preferred; positive pitching edge; avoid bullpen fatigue and weather risk.",
    },
    "Conservative": {
        "edge_threshold": "4.0",
        "min_line_difference_cents": "15",
        "min_confidence": "7.0",
        "max_bets_per_day": 2,
        "stake_size": "0.50 to 1.00 units",
        "market_scope": "Moneyline focus",
        "notes": "Current default rules.",
    },
    "Moderate": {
        "edge_threshold": "3.0",
        "min_line_difference_cents": "10",
        "min_confidence": "6.5",
        "max_bets_per_day": 4,
        "stake_size": "0.75 to 1.25 units",
        "market_scope": "ML/F5 focus; minor uncertainty allowed",
        "notes": "At least one of pitching or offense should show an edge.",
    },
    "Aggressive": {
        "edge_threshold": "2.0",
        "min_line_difference_cents": "5",
        "min_confidence": "5.5",
        "max_bets_per_day": 7,
        "stake_size": "1.00 to 1.50 units",
        "market_scope": "ML, F5, totals, team totals, run line as support is added",
        "notes": "No chasing losses, martingale, betting every game, overloading parlays, or raising stake after losses.",
    },
}

MARKETS = ["Moneyline", "F5 Moneyline", "Run Line", "Total", "Team Total"]
RESULTS = ["OPEN", "W", "L", "P", "VOID"]
TEAM_NAME_TO_ABBR = {
    "Arizona Diamondbacks": "ARI",
    "Athletics": "ATH",
    "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL",
    "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC",
    "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN",
    "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL",
    "Detroit Tigers": "DET",
    "Houston Astros": "HOU",
    "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA",
    "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA",
    "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN",
    "New York Mets": "NYM",
    "New York Yankees": "NYY",
    "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SD",
    "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA",
    "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB",
    "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR",
    "Washington Nationals": "WSH",
}
TEAM_ABBR_TO_NAME = {abbr: team for team, abbr in TEAM_NAME_TO_ABBR.items()}
TEAM_ABBR_TO_NAME["AZ"] = "Arizona Diamondbacks"
TEAM_ABBR_TO_NAME["OAK"] = "Athletics"
BALLPARK_COORDS = {
    "Angel Stadium": (33.8003, -117.8827),
    "Busch Stadium": (38.6226, -90.1928),
    "Chase Field": (33.4455, -112.0667),
    "Citi Field": (40.7571, -73.8458),
    "Citizens Bank Park": (39.9061, -75.1665),
    "Comerica Park": (42.3390, -83.0485),
    "Coors Field": (39.7559, -104.9942),
    "Daikin Park": (29.7573, -95.3555),
    "Dodger Stadium": (34.0739, -118.2400),
    "Fenway Park": (42.3467, -71.0972),
    "George M. Steinbrenner Field": (27.9796, -82.5067),
    "Globe Life Field": (32.7473, -97.0842),
    "Great American Ball Park": (39.0979, -84.5066),
    "Guaranteed Rate Field": (41.8300, -87.6339),
    "Kauffman Stadium": (39.0517, -94.4803),
    "Las Vegas Ballpark": (36.1582, -115.3203),
    "loanDepot park": (25.7781, -80.2197),
    "Minute Maid Park": (29.7573, -95.3555),
    "Nationals Park": (38.8730, -77.0074),
    "Oracle Park": (37.7786, -122.3893),
    "Oriole Park at Camden Yards": (39.2840, -76.6217),
    "PNC Park": (40.4469, -80.0057),
    "Progressive Field": (41.4962, -81.6852),
    "Rate Field": (41.8300, -87.6339),
    "Rogers Centre": (43.6414, -79.3894),
    "Sutter Health Park": (38.5804, -121.5137),
    "T-Mobile Park": (47.5914, -122.3325),
    "Target Field": (44.9817, -93.2776),
    "Truist Park": (33.8908, -84.4678),
    "Wrigley Field": (41.9484, -87.6553),
    "Yankee Stadium": (40.8296, -73.9262),
    "American Family Field": (43.0280, -87.9712),
    "Petco Park": (32.7073, -117.1566),
}

st.set_page_config(page_title="MLB Edge Dashboard", page_icon="MLB", layout="wide")


def get_secret(name: str, default: str = "") -> str:
    env_value = os.getenv(name)
    if env_value:
        return env_value
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return default


def redact_secret(value: object, secret: str) -> str:
    message = str(value)
    if secret:
        message = message.replace(secret, "[REDACTED]")
    return message


def database_url() -> str:
    return get_secret("SUPABASE_DB_URL")


def is_postgres_enabled() -> bool:
    return bool(database_url())


def translate_sql_for_postgres(sql: str) -> str:
    translated = sql
    if "INSERT OR IGNORE INTO" in translated:
        translated = translated.replace("INSERT OR IGNORE INTO", "INSERT INTO")
        if "ON CONFLICT" not in translated:
            translated = translated.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return translated.replace("?", "%s")


class PostgresConnection:
    def __init__(self, url: str):
        import psycopg2
        import psycopg2.extras

        self._conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.DictCursor)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type:
            self._conn.rollback()
        self._conn.close()

    def execute(self, sql: str, params: tuple | list = ()):
        cursor = self._conn.cursor()
        cursor.execute(translate_sql_for_postgres(sql), params)
        return cursor

    def executescript(self, sql: str) -> None:
        cursor = self._conn.cursor()
        cursor.execute(sql)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if is_postgres_enabled():
        return PostgresConnection(database_url())
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def execute_schema(conn) -> None:
    schema_path = SUPABASE_SCHEMA_PATH if isinstance(conn, PostgresConnection) else SCHEMA_PATH
    if not schema_path.exists() and isinstance(conn, PostgresConnection):
        st.warning(
            "Supabase schema file is not bundled with this deployment. "
            "If you already ran supabase_schema.sql in Supabase, the app can continue."
        )
        return
    conn.executescript(schema_path.read_text())
    conn.commit()


def ensure_column(conn, table: str, column: str, definition: str) -> None:
    if isinstance(conn, PostgresConnection):
        existing = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table,),
        ).fetchall()
        existing_columns = {row["column_name"] for row in existing}
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        return
    existing_columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing_columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def migrate_database(conn) -> None:
    if isinstance(conn, PostgresConnection):
        return
    game_columns = {
        "status_state": "TEXT",
        "status_code": "TEXT",
        "away_score": "INTEGER",
        "home_score": "INTEGER",
        "inning_state": "TEXT",
        "current_inning": "TEXT",
        "score_summary": "TEXT",
        "box_score_summary": "TEXT",
    }
    for column, definition in game_columns.items():
        ensure_column(conn, "games", column, definition)
    conn.commit()


def table_count(conn, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def seed_games_and_recommendations(conn) -> None:
    slate_path = DATA_DIR / "sample_daily_slate.csv"
    if not slate_path.exists() or table_count(conn, "model_recommendations") > 0:
        return

    slate = pd.read_csv(slate_path)
    for row in slate.to_dict("records"):
        game_id = f"{row['game_date']}-{row['away_team']}-{row['home_team']}"
        conn.execute(
            """
            INSERT OR IGNORE INTO games (
                game_id, game_date, away_team, home_team, away_probable_pitcher,
                home_probable_pitcher, venue_name, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game_id,
                row["game_date"],
                row["away_team"],
                row["home_team"],
                "TBD",
                "TBD",
                "TBD",
                "Sample",
            ),
        )
        conn.execute(
            """
            INSERT INTO model_recommendations (
                game_id, run_date, recommended_side, best_market, fair_line,
                market_line, edge_pct, confidence, pitching_score, bullpen_score,
                offense_score, lineup_score, weather_score, situation_score,
                total_score, recommendation, reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game_id,
                row["game_date"],
                row["recommended_side"],
                row["best_market"],
                int(row["fair_line"]),
                int(row["market_line"]),
                float(row["edge_pct"]),
                float(row["confidence"]),
                2 if row["recommendation"] == "BET" else 1,
                1 if row["recommendation"] == "BET" else 0,
                2 if row["recommendation"] == "BET" else 1,
                0,
                0,
                1 if row["recommendation"] == "BET" else 0,
                float(row["model_score"]),
                row["recommendation"],
                row["reason"],
            ),
        )
    conn.commit()


def seed_bets(conn) -> None:
    bets_path = DATA_DIR / "sample_bets.csv"
    if not bets_path.exists() or table_count(conn, "bets") > 0:
        return

    bets = pd.read_csv(bets_path)
    for row in bets.to_dict("records"):
        conn.execute(
            """
            INSERT INTO bets (
                bet_date, bet_label, market, odds, stake_units, result,
                profit_loss_units, closing_line, clv_cents, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["date"],
                row["bet"],
                row["market"],
                int(row["odds"]),
                float(row["stake_units"]),
                row["result"],
                float(row["profit_loss_units"]),
                int(row["closing_line"]),
                int(row["clv_cents"]),
                "Seeded sample bet",
            ),
        )
    conn.commit()


def seed_settings(conn) -> None:
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
    conn.commit()


def initialize_database() -> None:
    BRANDING_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        execute_schema(conn)
        migrate_database(conn)
        seed_games_and_recommendations(conn)
        seed_bets(conn)
        seed_settings(conn)


@st.cache_data(ttl=5)
def load_dataframe(query: str, params: tuple = ()) -> pd.DataFrame:
    if is_postgres_enabled():
        import psycopg2

        with psycopg2.connect(database_url()) as conn:
            return pd.read_sql_query(translate_sql_for_postgres(query), conn, params=params)
    with connect() as conn:
        return pd.read_sql_query(query, conn, params=params)


def load_settings() -> dict[str, str]:
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def save_setting(key: str, value: object) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
            """,
            (key, str(value)),
        )
        conn.commit()
    load_dataframe.clear()


def resolve_asset_path(value: str) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path if path.exists() else None


def save_uploaded_logo(uploaded_logo) -> str:
    BRANDING_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(uploaded_logo.name).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".png"
    target = BRANDING_DIR / f"dashboard_logo{suffix}"
    target.write_bytes(uploaded_logo.getbuffer())
    return str(target.relative_to(BASE_DIR))


def apply_rule_profile(settings: dict[str, str], profile_name: str) -> dict[str, str]:
    active_settings = dict(settings)
    profile = RULE_PROFILES.get(profile_name, RULE_PROFILES["Conservative"])
    active_settings.update(
        {
            "rule_profile": profile_name,
            "edge_threshold": profile["edge_threshold"],
            "min_line_difference_cents": profile["min_line_difference_cents"],
            "min_confidence": profile["min_confidence"],
            "max_bets_per_day": str(profile["max_bets_per_day"]),
            "profile_stake_size": profile["stake_size"],
            "profile_market_scope": profile["market_scope"],
            "profile_notes": profile["notes"],
        }
    )
    return active_settings


def active_rule_settings(settings: dict[str, str]) -> dict[str, str]:
    if str(settings.get("use_manual_rules", "false")).lower() in {"1", "true", "yes", "on"}:
        active_settings = dict(settings)
        active_settings.update(
            {
                "rule_profile": "Manual Settings",
                "max_bets_per_day": "99",
                "profile_stake_size": f"{float(settings['default_stake_units']):.2f} units",
                "profile_market_scope": "Manual Settings thresholds",
                "profile_notes": "Using thresholds from the Settings page.",
            }
        )
        return active_settings
    return apply_rule_profile(settings, settings.get("rule_profile", "Conservative"))


def american_to_profit(odds: int, stake: float) -> float:
    if odds > 0:
        return stake * odds / 100
    return stake * 100 / abs(odds)


def calculate_profit_loss(result: str, odds: int, stake: float) -> float:
    if result == "W":
        return round(american_to_profit(odds, stake), 2)
    if result == "L":
        return round(-stake, 2)
    return 0.0


def game_status_bucket(status: object, status_state: object = "") -> str:
    status_text = f"{status or ''} {status_state or ''}".lower()
    if any(token in status_text for token in ["final", "completed", "game over", "cancel", "postponed", "suspended"]):
        return "done"
    if any(token in status_text for token in ["live", "progress", "warmup", "manager challenge", "delayed"]):
        return "in_progress"
    return "scheduled"


def format_score_summary(
    away_team: str,
    home_team: str,
    away_score: object,
    home_score: object,
    status: str,
    status_state: str,
    inning_state: str,
    current_inning: str,
) -> str:
    bucket = game_status_bucket(status, status_state)
    score_available = away_score is not None and home_score is not None
    if score_available:
        score_text = f"{away_team} {int(away_score)}, {home_team} {int(home_score)}"
    else:
        score_text = f"{away_team} @ {home_team}"

    if bucket == "in_progress":
        inning_text = " ".join(part for part in [inning_state, current_inning] if part).strip()
        return f"{inning_text}: {score_text}" if inning_text else f"In Progress: {score_text}"
    if bucket == "done":
        return f"{status or 'Final'}: {score_text}"
    return f"{status or 'Scheduled'}: {score_text}"


def style_game_status_rows(row: pd.Series) -> list[str]:
    bucket = row.get("game_status_bucket") or game_status_bucket(row.get("status"), row.get("status_state", ""))
    if bucket == "done":
        color = "background-color: rgba(220, 38, 38, 0.35)"
    elif bucket == "in_progress":
        color = "background-color: rgba(234, 179, 8, 0.35); color: #f8fafc"
    else:
        color = "background-color: rgba(34, 197, 94, 0.25)"
    return [color] * len(row)


def actual_winner(row: pd.Series) -> str:
    bucket = row.get("game_status_bucket") or game_status_bucket(row.get("status"), row.get("status_state", ""))
    if bucket == "scheduled":
        return "Pending"
    if pd.isna(row.get("away_score")) or pd.isna(row.get("home_score")):
        return "No score"
    away_score = int(row["away_score"])
    home_score = int(row["home_score"])
    away_team, home_team = str(row["matchup"]).split(" @ ")
    if bucket == "in_progress":
        if away_score > home_score:
            return f"Leading: {away_team}"
        if home_score > away_score:
            return f"Leading: {home_team}"
        return "Tied"
    if away_score > home_score:
        return away_team
    if home_score > away_score:
        return home_team
    return "Tie"


def settle_recommendation(row: pd.Series) -> str:
    bucket = row.get("game_status_bucket") or game_status_bucket(row.get("status"), row.get("status_state", ""))
    if bucket != "done":
        return "Pending"
    winner = row.get("actual_winner", "No score")
    if winner in {"No score", "Tie"}:
        return "No action"

    recommended_side = row.get("recommended_side")
    side_won = recommended_side == winner
    recommendation = row.get("recommendation")
    if recommendation == "BET":
        return "BET WON" if side_won else "BET LOST"
    if recommendation == "PASS":
        return "PASS WORKED" if not side_won else "PASS MISSED"
    return "Pending"


def style_recommendation_result(row: pd.Series) -> list[str]:
    result = row.get("recommendation_result", "")
    recommendation = row.get("recommendation", "")
    styles = [""] * len(row)
    if result in {"BET WON", "PASS WORKED"}:
        color = "background-color: rgba(22, 163, 74, 0.9); color: white; font-weight: 700"
    elif result in {"BET LOST", "PASS MISSED"}:
        color = "background-color: rgba(220, 38, 38, 0.9); color: white; font-weight: 700"
    elif result == "Pending":
        result_color = "background-color: rgba(234, 179, 8, 0.85); color: #111827; font-weight: 700"
        if recommendation == "BET":
            recommendation_color = "background-color: rgba(22, 163, 74, 0.9); color: white; font-weight: 700"
        elif recommendation == "PASS":
            recommendation_color = "background-color: rgba(220, 38, 38, 0.9); color: white; font-weight: 700"
        else:
            recommendation_color = result_color
        for index, column in enumerate(row.index):
            if column == "recommendation":
                styles[index] = recommendation_color
            elif column == "recommendation_result":
                styles[index] = result_color
        return styles
    else:
        color = "background-color: rgba(107, 114, 128, 0.75); color: white; font-weight: 700"

    for index, column in enumerate(row.index):
        if column in {"recommendation", "recommendation_result"}:
            styles[index] = color
    return styles


def load_slate() -> pd.DataFrame:
    slate = load_dataframe(
        """
        SELECT
            mr.id,
            ROW_NUMBER() OVER (ORDER BY mr.edge_pct DESC, mr.confidence DESC) AS rank,
            g.game_id,
            g.game_date,
            g.away_team || ' @ ' || g.home_team AS matchup,
            g.away_probable_pitcher || ' vs ' || g.home_probable_pitcher AS probable_starters,
            g.venue_name,
            g.venue_lat,
            g.venue_lon,
            g.first_pitch_utc,
            g.status,
            g.status_state,
            g.status_code,
            g.away_score,
            g.home_score,
            g.inning_state,
            g.current_inning,
            COALESCE(g.score_summary, 'Score pending') AS score_summary,
            COALESCE(g.box_score_summary, 'Box score pending') AS box_score_summary,
            ws.forecast_time,
            ws.temperature_f,
            ws.wind_speed_mph,
            ws.wind_direction_deg,
            ws.precipitation_probability,
            COALESCE(ws.weather_summary, 'Weather pending') AS weather_summary,
            away_sc.xwoba_for AS away_xwoba_for,
            away_sc.hard_hit_pct_for AS away_hard_hit_pct_for,
            away_sc.xwoba_allowed AS away_xwoba_allowed,
            away_sc.hard_hit_pct_allowed AS away_hard_hit_pct_allowed,
            away_sc.offense_score AS away_offense_score,
            away_sc.pitching_score AS away_pitching_score,
            away_sc.confluence_score AS away_confluence_score,
            home_sc.xwoba_for AS home_xwoba_for,
            home_sc.hard_hit_pct_for AS home_hard_hit_pct_for,
            home_sc.xwoba_allowed AS home_xwoba_allowed,
            home_sc.hard_hit_pct_allowed AS home_hard_hit_pct_allowed,
            home_sc.offense_score AS home_offense_score,
            home_sc.pitching_score AS home_pitching_score,
            home_sc.confluence_score AS home_confluence_score,
            mr.recommended_side,
            mr.best_market,
            mr.fair_line,
            mr.market_line,
            mr.edge_pct,
            mr.confidence,
            mr.total_score AS model_score,
            mr.pitching_score,
            mr.bullpen_score,
            mr.offense_score,
            mr.lineup_score,
            mr.weather_score,
            mr.situation_score,
            mr.recommendation,
            mr.reason,
            mr.created_at
        FROM model_recommendations mr
        JOIN games g ON g.game_id = mr.game_id
        LEFT JOIN (
            SELECT game_id, forecast_time, temperature_f, wind_speed_mph,
                   wind_direction_deg, precipitation_probability, weather_summary
            FROM weather_snapshots
            WHERE id IN (SELECT MAX(id) FROM weather_snapshots GROUP BY game_id)
        ) ws ON ws.game_id = g.game_id
        LEFT JOIN (
            SELECT *
            FROM team_statcast_metrics
            WHERE id IN (SELECT MAX(id) FROM team_statcast_metrics GROUP BY team)
        ) away_sc ON away_sc.team = g.away_team
        LEFT JOIN (
            SELECT *
            FROM team_statcast_metrics
            WHERE id IN (SELECT MAX(id) FROM team_statcast_metrics GROUP BY team)
        ) home_sc ON home_sc.team = g.home_team
        WHERE mr.run_date = (SELECT MAX(run_date) FROM model_recommendations)
        ORDER BY mr.edge_pct DESC, mr.confidence DESC
        """
    )
    slate["first_pitch_et"] = (
        slate["first_pitch_utc"].fillna("").apply(format_first_pitch)
        if "first_pitch_utc" in slate.columns
        else pd.Series(dtype="object")
    )
    slate["game_status_bucket"] = slate.apply(
        lambda row: game_status_bucket(row.get("status"), row.get("status_state")),
        axis=1,
    )
    slate["actual_winner"] = slate.apply(actual_winner, axis=1)
    slate["recommendation_result"] = slate.apply(settle_recommendation, axis=1)
    return slate


def load_bets() -> pd.DataFrame:
    return load_dataframe("SELECT * FROM bets ORDER BY bet_date, id")


def load_live_odds() -> pd.DataFrame:
    return load_dataframe(
        """
        SELECT event_id, commence_time, away_team, home_team, bookmaker, market, outcomes_json, captured_at
        FROM live_odds_snapshots
        ORDER BY captured_at DESC, commence_time, away_team, home_team, bookmaker, market
        LIMIT 100
        """
    )


def add_bet(
    bet_date: date,
    game_id: str | None,
    bet_label: str,
    market: str,
    odds: int,
    stake_units: float,
    closing_line: int | None,
    result: str,
    notes: str,
) -> None:
    clv_cents = int(closing_line - odds) if closing_line is not None else None
    profit_loss = calculate_profit_loss(result, odds, stake_units)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO bets (
                bet_date, game_id, bet_label, market, odds, stake_units,
                result, profit_loss_units, closing_line, clv_cents, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bet_date.isoformat(),
                game_id,
                bet_label,
                market,
                odds,
                stake_units,
                result,
                profit_loss,
                closing_line,
                clv_cents,
                notes,
            ),
        )
        conn.commit()
    load_dataframe.clear()


def parse_mlb_schedule(schedule: dict, date_str: str) -> list[dict]:
    games = []
    for date_block in schedule.get("dates", []):
        for game in date_block.get("games", []):
            away = game.get("teams", {}).get("away", {})
            home = game.get("teams", {}).get("home", {})
            away_team = away.get("team", {})
            home_team = home.get("team", {})
            venue = game.get("venue", {})
            away_pitcher = away.get("probablePitcher", {})
            home_pitcher = home.get("probablePitcher", {})
            venue_name = venue.get("name", "")
            coords = BALLPARK_COORDS.get(venue_name, (None, None))
            status = game.get("status", {})
            linescore = game.get("linescore", {})
            away_score = away.get("score")
            home_score = home.get("score")
            current_inning = linescore.get("currentInningOrdinal") or ""
            inning_state = linescore.get("inningState") or linescore.get("inningHalf") or ""
            score_summary = format_score_summary(
                away_team.get("name", ""),
                home_team.get("name", ""),
                away_score,
                home_score,
                status.get("detailedState", ""),
                status.get("abstractGameState", ""),
                inning_state,
                current_inning,
            )
            games.append(
                {
                    "game_id": str(game.get("gamePk")),
                    "game_date": date_str,
                    "away_team": away_team.get("abbreviation") or away_team.get("teamName") or away_team.get("name", ""),
                    "home_team": home_team.get("abbreviation") or home_team.get("teamName") or home_team.get("name", ""),
                    "away_name": away_team.get("name", ""),
                    "home_name": home_team.get("name", ""),
                    "venue_name": venue_name,
                    "venue_lat": coords[0],
                    "venue_lon": coords[1],
                    "away_probable_pitcher": away_pitcher.get("fullName", "TBD"),
                    "home_probable_pitcher": home_pitcher.get("fullName", "TBD"),
                    "status": status.get("detailedState", ""),
                    "status_state": status.get("abstractGameState", ""),
                    "status_code": status.get("codedGameState", ""),
                    "away_score": away_score,
                    "home_score": home_score,
                    "inning_state": inning_state,
                    "current_inning": current_inning,
                    "score_summary": score_summary,
                    "box_score_summary": score_summary,
                    "first_pitch_utc": game.get("gameDate", ""),
                }
            )
    return games


def replace_daily_schedule(date_str: str, games: list[dict]) -> None:
    with connect() as conn:
        existing_game_ids = [
            row["game_id"]
            for row in conn.execute("SELECT game_id FROM games WHERE game_date = ?", (date_str,)).fetchall()
        ]
        if existing_game_ids:
            placeholders = ",".join("?" for _ in existing_game_ids)
            conn.execute(f"DELETE FROM model_recommendations WHERE game_id IN ({placeholders})", existing_game_ids)
        for game in games:
            conn.execute(
                """
                INSERT INTO games (
                    game_id, game_date, away_team, home_team, venue_name, venue_lat, venue_lon,
                    away_probable_pitcher, home_probable_pitcher, status, status_state,
                    status_code, away_score, home_score, inning_state, current_inning,
                    score_summary, box_score_summary, first_pitch_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id) DO UPDATE SET
                    game_date = excluded.game_date,
                    away_team = excluded.away_team,
                    home_team = excluded.home_team,
                    venue_name = excluded.venue_name,
                    venue_lat = excluded.venue_lat,
                    venue_lon = excluded.venue_lon,
                    away_probable_pitcher = excluded.away_probable_pitcher,
                    home_probable_pitcher = excluded.home_probable_pitcher,
                    status = excluded.status,
                    status_state = excluded.status_state,
                    status_code = excluded.status_code,
                    away_score = excluded.away_score,
                    home_score = excluded.home_score,
                    inning_state = excluded.inning_state,
                    current_inning = excluded.current_inning,
                    score_summary = excluded.score_summary,
                    box_score_summary = excluded.box_score_summary,
                    first_pitch_utc = excluded.first_pitch_utc
                """,
                (
                    game["game_id"],
                    game["game_date"],
                    game["away_team"],
                    game["home_team"],
                    game["venue_name"],
                    game["venue_lat"],
                    game["venue_lon"],
                    game["away_probable_pitcher"],
                    game["home_probable_pitcher"],
                    game["status"],
                    game["status_state"],
                    game["status_code"],
                    game["away_score"],
                    game["home_score"],
                    game["inning_state"],
                    game["current_inning"],
                    game["score_summary"],
                    game["box_score_summary"],
                    game["first_pitch_utc"],
                ),
            )
            conn.execute(
                """
                INSERT INTO model_recommendations (
                    game_id, run_date, recommended_side, best_market, fair_line,
                    market_line, edge_pct, confidence, pitching_score, bullpen_score,
                    offense_score, lineup_score, weather_score, situation_score,
                    total_score, recommendation, reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    game["game_id"],
                    date_str,
                    "TBD",
                    "Needs scoring",
                    0,
                    0,
                    0.0,
                    0.0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    "PASS",
                    "Live MLB schedule loaded. Run pybaseball Statcast refresh for scoring.",
                ),
            )
        conn.commit()
    load_dataframe.clear()


def choose_bookmaker(bookmakers: list[dict], preference: str) -> dict | None:
    if not bookmakers:
        return None
    preference_key = preference.lower().replace(" ", "_")
    for bookmaker in bookmakers:
        if bookmaker.get("key") == preference_key or bookmaker.get("title", "").lower() == preference.lower():
            return bookmaker
    return bookmakers[0]


def store_live_odds(odds_events: list[dict], bookmaker_preference: str) -> int:
    captured_rows = 0
    with connect() as conn:
        conn.execute("DELETE FROM live_odds_snapshots")
        for event in odds_events:
            bookmaker = choose_bookmaker(event.get("bookmakers", []), bookmaker_preference)
            if not bookmaker:
                continue
            for market in bookmaker.get("markets", []):
                conn.execute(
                    """
                    INSERT INTO live_odds_snapshots (
                        event_id, commence_time, away_team, home_team, bookmaker, market, outcomes_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.get("id", ""),
                        event.get("commence_time", ""),
                        event.get("away_team", ""),
                        event.get("home_team", ""),
                        bookmaker.get("title", bookmaker.get("key", "")),
                        market.get("key", ""),
                        json.dumps(market.get("outcomes", [])),
                    ),
                )
                captured_rows += 1
        conn.commit()
    load_dataframe.clear()
    return captured_rows


def parse_utc_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def format_first_pitch(value: str) -> str:
    first_pitch = parse_utc_datetime(value)
    if first_pitch is None:
        return "Time pending"
    local_pitch = first_pitch.astimezone(DISPLAY_TIMEZONE)
    hour = local_pitch.strftime("%I").lstrip("0") or "12"
    return f"{local_pitch:%a %b} {local_pitch.day}, {hour}:{local_pitch:%M %p} {DISPLAY_TIMEZONE_LABEL}"


def nearest_hourly_weather(weather: dict, first_pitch_utc: str) -> dict[str, object] | None:
    hourly = weather.get("hourly", {})
    times = hourly.get("time", [])
    if not times:
        return None
    first_pitch = parse_utc_datetime(first_pitch_utc) or datetime.now(timezone.utc)
    parsed_times = [parse_utc_datetime(f"{time_value}Z") for time_value in times]
    valid_indexes = [index for index, time_value in enumerate(parsed_times) if time_value is not None]
    if not valid_indexes:
        return None
    nearest_index = min(valid_indexes, key=lambda index: abs((parsed_times[index] - first_pitch).total_seconds()))
    return {
        "forecast_time": times[nearest_index],
        "temperature_f": hourly.get("temperature_2m", [None] * len(times))[nearest_index],
        "wind_speed_mph": hourly.get("wind_speed_10m", [None] * len(times))[nearest_index],
        "wind_direction_deg": hourly.get("wind_direction_10m", [None] * len(times))[nearest_index],
        "precipitation_probability": hourly.get("precipitation_probability", [None] * len(times))[nearest_index],
    }


def format_weather_summary(row: dict[str, object]) -> str:
    temp = row.get("temperature_f")
    wind_speed = row.get("wind_speed_mph")
    wind_direction = row.get("wind_direction_deg")
    precip = row.get("precipitation_probability")
    temp_text = f"{temp:.0f}F" if isinstance(temp, (int, float)) else "Temp n/a"
    wind_text = f"{wind_speed:.0f} mph" if isinstance(wind_speed, (int, float)) else "Wind n/a"
    direction_text = f" from {wind_direction:.0f}deg" if isinstance(wind_direction, (int, float)) else ""
    precip_text = f"{precip:.0f}% precip" if isinstance(precip, (int, float)) else "Precip n/a"
    return f"{temp_text}, wind {wind_text}{direction_text}, {precip_text}"


def refresh_weather_for_games(games: list[dict]) -> tuple[int, list[str]]:
    weather_rows = 0
    errors = []
    with connect() as conn:
        for game in games:
            lat = game.get("venue_lat")
            lon = game.get("venue_lon")
            if lat is None or lon is None:
                errors.append(f"Weather skipped for {game['away_team']} @ {game['home_team']}: missing venue coordinates for {game['venue_name']}")
                continue
            try:
                forecast = get_stadium_weather(float(lat), float(lon))
                selected = nearest_hourly_weather(forecast, game.get("first_pitch_utc", ""))
                if not selected:
                    errors.append(f"Weather skipped for {game['away_team']} @ {game['home_team']}: no hourly forecast returned")
                    continue
                summary = format_weather_summary(selected)
                conn.execute(
                    """
                    INSERT INTO weather_snapshots (
                        game_id, venue_name, forecast_time, temperature_f, wind_speed_mph,
                        wind_direction_deg, precipitation_probability, weather_summary
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        game["game_id"],
                        game["venue_name"],
                        selected["forecast_time"],
                        selected["temperature_f"],
                        selected["wind_speed_mph"],
                        selected["wind_direction_deg"],
                        selected["precipitation_probability"],
                        summary,
                    ),
                )
                weather_rows += 1
            except Exception as exc:
                errors.append(f"Open-Meteo refresh failed for {game['away_team']} @ {game['home_team']}: {exc}")
        conn.commit()
    load_dataframe.clear()
    return weather_rows, errors


def batting_team_for_pitch(row: pd.Series) -> str | None:
    if row.get("inning_topbot") == "Top":
        return row.get("away_team")
    if row.get("inning_topbot") == "Bot":
        return row.get("home_team")
    return None


def pitching_team_for_pitch(row: pd.Series) -> str | None:
    if row.get("inning_topbot") == "Top":
        return row.get("home_team")
    if row.get("inning_topbot") == "Bot":
        return row.get("away_team")
    return None


def safe_rate(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def zscore(series: pd.Series, higher_is_better: bool = True) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce").fillna(series.median() if series.notna().any() else 0)
    std = numeric.std(ddof=0)
    if not std:
        scored = pd.Series([0.0] * len(numeric), index=numeric.index)
    else:
        scored = (numeric - numeric.mean()) / std
    return scored if higher_is_better else -scored


def aggregate_statcast_metrics(statcast_df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    if statcast_df.empty:
        return pd.DataFrame()

    df = statcast_df.copy()
    df["batting_team"] = df.apply(batting_team_for_pitch, axis=1)
    df["pitching_team"] = df.apply(pitching_team_for_pitch, axis=1)
    df["is_pa"] = df["events"].notna()
    df["is_bbe"] = df["launch_speed"].notna()
    df["is_hard_hit"] = pd.to_numeric(df["launch_speed"], errors="coerce") >= 95
    df["is_barrel"] = pd.to_numeric(df.get("launch_speed_angle"), errors="coerce") == 6
    df["is_strikeout"] = df["events"].isin(["strikeout", "strikeout_double_play"])
    df["is_walk"] = df["events"].isin(["walk", "intent_walk", "hit_by_pitch"])
    df["xwoba"] = pd.to_numeric(df["estimated_woba_using_speedangle"], errors="coerce")
    df["woba_value_num"] = pd.to_numeric(df["woba_value"], errors="coerce").fillna(0)
    df["woba_denom_num"] = pd.to_numeric(df["woba_denom"], errors="coerce").fillna(0)

    batting_rows = []
    for team_abbr, group in df.groupby("batting_team", dropna=True):
        pa = int(group["is_pa"].sum())
        bbe = int(group["is_bbe"].sum())
        batting_rows.append(
            {
                "team_abbr": team_abbr,
                "team": TEAM_ABBR_TO_NAME.get(team_abbr, team_abbr),
                "plate_appearances": pa,
                "pitches_seen": int(len(group)),
                "xwoba_for": float(group["xwoba"].mean()) if group["xwoba"].notna().any() else 0.0,
                "woba_for": safe_rate(group["woba_value_num"].sum(), group["woba_denom_num"].sum()),
                "hard_hit_pct_for": safe_rate(group["is_hard_hit"].sum(), bbe),
                "barrel_pct_for": safe_rate(group["is_barrel"].sum(), bbe),
                "k_pct_for": safe_rate(group["is_strikeout"].sum(), pa),
                "bb_pct_for": safe_rate(group["is_walk"].sum(), pa),
            }
        )

    pitching_rows = []
    for team_abbr, group in df.groupby("pitching_team", dropna=True):
        pa = int(group["is_pa"].sum())
        bbe = int(group["is_bbe"].sum())
        pitching_rows.append(
            {
                "team_abbr": team_abbr,
                "xwoba_allowed": float(group["xwoba"].mean()) if group["xwoba"].notna().any() else 0.0,
                "woba_allowed": safe_rate(group["woba_value_num"].sum(), group["woba_denom_num"].sum()),
                "hard_hit_pct_allowed": safe_rate(group["is_hard_hit"].sum(), bbe),
                "barrel_pct_allowed": safe_rate(group["is_barrel"].sum(), bbe),
                "k_pct_pitching": safe_rate(group["is_strikeout"].sum(), pa),
                "bb_pct_pitching": safe_rate(group["is_walk"].sum(), pa),
            }
        )

    batting = pd.DataFrame(batting_rows)
    pitching = pd.DataFrame(pitching_rows)
    if batting.empty or pitching.empty:
        return pd.DataFrame()
    metrics = batting.merge(pitching, on="team_abbr", how="outer")
    metrics["team"] = metrics["team"].fillna(metrics["team_abbr"].map(TEAM_ABBR_TO_NAME)).fillna(metrics["team_abbr"])
    metrics = metrics.fillna(0)

    metrics["offense_raw"] = (
        zscore(metrics["xwoba_for"])
        + zscore(metrics["woba_for"])
        + zscore(metrics["hard_hit_pct_for"])
        + zscore(metrics["barrel_pct_for"])
        + zscore(metrics["bb_pct_for"])
        + zscore(metrics["k_pct_for"], higher_is_better=False)
    )
    metrics["pitching_raw"] = (
        zscore(metrics["xwoba_allowed"], higher_is_better=False)
        + zscore(metrics["woba_allowed"], higher_is_better=False)
        + zscore(metrics["hard_hit_pct_allowed"], higher_is_better=False)
        + zscore(metrics["barrel_pct_allowed"], higher_is_better=False)
        + zscore(metrics["k_pct_pitching"])
        + zscore(metrics["bb_pct_pitching"], higher_is_better=False)
    )
    metrics["offense_score"] = (50 + metrics["offense_raw"] * 5).round(1)
    metrics["pitching_score"] = (50 + metrics["pitching_raw"] * 5).round(1)
    metrics["confluence_score"] = (metrics["offense_score"] + metrics["pitching_score"]).round(1)
    metrics["start_date"] = start_date
    metrics["end_date"] = end_date
    return metrics


def refresh_statcast_metrics(lookback_days: int) -> tuple[int, str, str, str]:
    from pybaseball import statcast

    end_day = date.today() - timedelta(days=1)
    start_day = end_day - timedelta(days=max(1, lookback_days) - 1)
    start_date = start_day.isoformat()
    end_date = end_day.isoformat()
    statcast_df = statcast(start_date, end_date, verbose=False, parallel=False)
    metrics = aggregate_statcast_metrics(statcast_df, start_date, end_date)
    if metrics.empty:
        return 0, start_date, end_date, "No Statcast rows returned."

    with connect() as conn:
        conn.execute("DELETE FROM team_statcast_metrics")
        for row in metrics.to_dict("records"):
            conn.execute(
                """
                INSERT INTO team_statcast_metrics (
                    team, team_abbr, start_date, end_date, plate_appearances, pitches_seen,
                    xwoba_for, woba_for, hard_hit_pct_for, barrel_pct_for, k_pct_for, bb_pct_for,
                    xwoba_allowed, woba_allowed, hard_hit_pct_allowed, barrel_pct_allowed,
                    k_pct_pitching, bb_pct_pitching, offense_score, pitching_score, confluence_score
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["team"],
                    row["team_abbr"],
                    row["start_date"],
                    row["end_date"],
                    int(row["plate_appearances"]),
                    int(row["pitches_seen"]),
                    float(row["xwoba_for"]),
                    float(row["woba_for"]),
                    float(row["hard_hit_pct_for"]),
                    float(row["barrel_pct_for"]),
                    float(row["k_pct_for"]),
                    float(row["bb_pct_for"]),
                    float(row["xwoba_allowed"]),
                    float(row["woba_allowed"]),
                    float(row["hard_hit_pct_allowed"]),
                    float(row["barrel_pct_allowed"]),
                    float(row["k_pct_pitching"]),
                    float(row["bb_pct_pitching"]),
                    float(row["offense_score"]),
                    float(row["pitching_score"]),
                    float(row["confluence_score"]),
                ),
            )
        conn.commit()
    load_dataframe.clear()
    return len(metrics), start_date, end_date, ""


def latest_team_metrics() -> dict[str, dict]:
    df = load_dataframe(
        """
        SELECT *
        FROM team_statcast_metrics
        WHERE id IN (SELECT MAX(id) FROM team_statcast_metrics GROUP BY team)
        """
    )
    return {row["team"]: row for row in df.to_dict("records")}


def latest_h2h_market_line(game: dict, team_name: str) -> int:
    odds_df = load_dataframe(
        """
        SELECT outcomes_json
        FROM live_odds_snapshots
        WHERE market = 'h2h'
          AND away_team = ?
          AND home_team = ?
        ORDER BY captured_at DESC
        LIMIT 1
        """,
        (game["away_name"], game["home_name"]),
    )
    if odds_df.empty:
        odds_df = load_dataframe(
            """
            SELECT outcomes_json
            FROM live_odds_snapshots
            WHERE market = 'h2h'
              AND away_team = ?
              AND home_team = ?
            ORDER BY captured_at DESC
            LIMIT 1
            """,
            (game["away_team"], game["home_team"]),
        )
    if odds_df.empty:
        return 0
    outcomes = json.loads(odds_df.iloc[0]["outcomes_json"])
    for outcome in outcomes:
        if outcome.get("name") == team_name:
            return int(outcome.get("price", 0))
    return 0


def confluence_probability(score_diff: float) -> float:
    return max(0.35, min(0.65, 0.5 + score_diff / 100))


def score_games_from_statcast(date_str: str, games: list[dict], settings: dict[str, str]) -> int:
    metrics = latest_team_metrics()
    scored = 0
    edge_threshold = float(settings["edge_threshold"])
    min_line_diff = int(float(settings["min_line_difference_cents"]))
    min_confidence = float(settings["min_confidence"])
    max_bets_per_day = int(float(settings.get("max_bets_per_day", len(games) or 99)))
    profile_name = settings.get("rule_profile", "Manual Settings")
    scored_rows = []

    for game in games:
        away_name = game["away_name"] or game["away_team"]
        home_name = game["home_name"] or game["home_team"]
        away_metrics = metrics.get(away_name)
        home_metrics = metrics.get(home_name)
        if not away_metrics or not home_metrics:
            scored_rows.append(
                {
                    "game_id": game["game_id"],
                    "recommended_side": "TBD",
                    "best_market": "Moneyline",
                    "fair_line": 0,
                    "market_line": 0,
                    "edge": 0.0,
                    "confidence": 0.0,
                    "pitching_score": 0,
                    "offense_score": 0,
                    "total_score": 0,
                    "candidate_bet": False,
                    "reason": "Statcast/Savant team metrics unavailable for one or both teams.",
                }
            )
            continue

        away_score = float(away_metrics["confluence_score"])
        home_score = float(home_metrics["confluence_score"]) + 1.5
        diff = away_score - home_score
        recommended_team = away_name if diff >= 0 else home_name
        recommended_score = away_score if diff >= 0 else home_score
        opponent_score = home_score if diff >= 0 else away_score
        model_prob = confluence_probability(recommended_score - opponent_score)
        fair_line = implied_prob_to_american(model_prob)
        market_line = latest_h2h_market_line(game, recommended_team)
        edge = calculate_edge_pct(model_prob, market_line) if market_line else 0.0
        line_diff = abs(fair_line - market_line) if market_line else 0
        confidence = round(min(10.0, 5.0 + abs(diff) / 5), 1)
        candidate_bet = bool(
            market_line and edge >= edge_threshold and line_diff >= min_line_diff and confidence >= min_confidence
        )
        reason = (
            f"{profile_name} rules. Statcast confluence: {away_name} {away_score:.1f} vs {home_name} {home_score:.1f}. "
            f"{recommended_team} projects at {model_prob:.1%}; market {market_line or 'n/a'}."
        )
        scored_rows.append(
            {
                "game_id": game["game_id"],
                "recommended_side": recommended_team,
                "best_market": "Moneyline",
                "fair_line": fair_line,
                "market_line": market_line,
                "edge": edge,
                "confidence": confidence,
                "pitching_score": round(float(away_metrics["pitching_score"]) - float(home_metrics["pitching_score"]), 1),
                "offense_score": round(float(away_metrics["offense_score"]) - float(home_metrics["offense_score"]), 1),
                "total_score": round(diff, 1),
                "candidate_bet": candidate_bet,
                "reason": reason,
            }
        )

    candidate_rows = [
        row
        for row in sorted(scored_rows, key=lambda item: (item["edge"], item["confidence"]), reverse=True)
        if row["candidate_bet"]
    ]
    bet_game_ids = {row["game_id"] for row in candidate_rows[: max(0, max_bets_per_day)]}

    with connect() as conn:
        for row in scored_rows:
            recommendation = "BET" if row["game_id"] in bet_game_ids else "PASS"
            reason = row["reason"]
            if row["candidate_bet"] and recommendation == "PASS":
                reason = f"{reason} PASS: profile max bets per day limit reached."
            conn.execute(
                """
                UPDATE model_recommendations
                SET recommended_side = ?, best_market = ?, fair_line = ?, market_line = ?,
                    edge_pct = ?, confidence = ?, pitching_score = ?, offense_score = ?,
                    total_score = ?, recommendation = ?, reason = ?
                WHERE game_id = ? AND run_date = ?
                """,
                (
                    row["recommended_side"],
                    row["best_market"],
                    row["fair_line"],
                    row["market_line"],
                    round(row["edge"], 2),
                    row["confidence"],
                    row["pitching_score"],
                    row["offense_score"],
                    row["total_score"],
                    recommendation,
                    reason,
                    row["game_id"],
                    date_str,
                ),
            )
            scored += 1
        conn.commit()
    load_dataframe.clear()
    return scored


def run_local_refresh(settings: dict[str, str]) -> dict[str, object]:
    started_at = datetime.now().isoformat(timespec="seconds")
    refresh_date = date.today().isoformat()
    mlb_stats_marker = get_secret("MLB_STATS_API_MARKER")
    games_loaded = 0
    try:
        schedule = get_schedule(refresh_date, api_marker=mlb_stats_marker)
        today_games = parse_mlb_schedule(schedule, refresh_date)
        if today_games:
            replace_daily_schedule(refresh_date, today_games)
            games_loaded = len(today_games)
        else:
            games_loaded = 0
    except Exception as exc:
        today_games = []
        games_loaded = len(load_slate())
        schedule_error = f"MLB Stats API schedule refresh failed: {exc}"
    else:
        schedule_error = ""

    odds_api_key = get_secret("ODDS_API_KEY")
    odds_ready = bool(odds_api_key)
    odds_events = 0
    odds_markets = 0
    weather_loaded = 0
    statcast_teams = 0
    scored_games = 0
    errors = []
    if schedule_error:
        errors.append(schedule_error)
    if today_games:
        weather_loaded, weather_errors = refresh_weather_for_games(today_games)
        errors.extend(weather_errors)
    if not odds_ready:
        errors.append("Odds API key not configured. Live market lines remain disabled.")
    else:
        try:
            odds = get_mlb_odds(odds_api_key)
            odds_events = len(odds)
            odds_markets = store_live_odds(odds, settings["bookmaker_preference"])
        except Exception as exc:
            errors.append(f"Odds API refresh failed: {redact_secret(exc, odds_api_key)}")
    try:
        statcast_teams, statcast_start, statcast_end, statcast_message = refresh_statcast_metrics(
            int(float(settings["statcast_lookback_days"]))
        )
        if statcast_message:
            errors.append(statcast_message)
    except Exception as exc:
        errors.append(f"pybaseball Statcast refresh failed: {exc}")
    if today_games and statcast_teams:
        scored_games = score_games_from_statcast(refresh_date, today_games, settings)

    status = "partial" if errors else "success"
    completed_at = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO refresh_logs (
                refresh_started_at, refresh_completed_at, status, games_loaded,
                odds_loaded, weather_loaded, errors
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                started_at,
                completed_at,
                status,
                games_loaded,
                odds_markets,
                weather_loaded,
                "\n".join(errors),
            ),
        )
        conn.commit()
    load_dataframe.clear()
    return {
        "status": status,
        "games": games_loaded,
        "odds_events": odds_events,
        "odds_markets": odds_markets,
        "weather_loaded": weather_loaded,
        "statcast_teams": statcast_teams,
        "scored_games": scored_games,
        "errors": errors,
    }


def rescore_current_slate(settings: dict[str, str]) -> int:
    slate_games = load_dataframe(
        """
        SELECT
            g.game_id,
            g.game_date,
            g.away_team,
            g.home_team,
            g.away_team AS away_name,
            g.home_team AS home_name
        FROM games g
        JOIN model_recommendations mr ON mr.game_id = g.game_id
        WHERE mr.run_date = (SELECT MAX(run_date) FROM model_recommendations)
        """
    )
    if slate_games.empty:
        return 0
    run_date = str(slate_games.iloc[0]["game_date"])
    return score_games_from_statcast(run_date, slate_games.to_dict("records"), settings)


def metric_row(slate_df: pd.DataFrame, bets_df: pd.DataFrame) -> None:
    bet_plays = slate_df[slate_df["recommendation"] == "BET"]
    total_stake = float(bets_df["stake_units"].sum()) if not bets_df.empty else 0.0
    total_units = float(bets_df["profit_loss_units"].sum()) if not bets_df.empty else 0.0
    roi = total_units / total_stake if total_stake else 0.0

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Games Analyzed", len(slate_df))
    k2.metric("BET Plays", len(bet_plays))
    k3.metric("Avg BET Edge", f"{bet_plays['edge_pct'].mean():.1f}%" if len(bet_plays) else "0.0%")
    k4.metric("Avg Confidence", f"{bet_plays['confidence'].mean():.1f}/10" if len(bet_plays) else "0.0/10")
    k5.metric("Total Units", f"{total_units:+.2f}")
    k6.metric("ROI", f"{roi:.1%}")


def sidebar(settings: dict[str, str]) -> tuple[str, dict[str, str]]:
    active_settings = active_rule_settings(settings)
    logo_path = resolve_asset_path(settings.get("dashboard_logo_path", ""))
    if logo_path:
        st.sidebar.image(str(logo_path), use_container_width=True)
    else:
        st.sidebar.title(settings.get("dashboard_title", "MLB Edge Model"))
    page = st.sidebar.radio(
        "Dashboard Pages",
        ["Daily Slate", "Game Breakdown", "Bet Tracker", "Performance", "Data Health", "Settings"],
    )
    st.sidebar.caption(f"Local database: {DB_PATH}")
    st.sidebar.caption("Live odds configured." if get_secret("ODDS_API_KEY") else "Live odds require The Odds API key.")
    st.sidebar.caption("MLB Stats marker configured." if get_secret("MLB_STATS_API_MARKER") else "MLB Stats uses public access.")
    st.sidebar.write(f"{active_settings['rule_profile']} rules")
    st.sidebar.write(
        f"Edge >= {float(active_settings['edge_threshold']):.1f}% | "
        f"Line diff >= {int(float(active_settings['min_line_difference_cents']))}c | "
        f"Confidence >= {float(active_settings['min_confidence']):.1f}"
    )
    st.sidebar.caption(f"Max BETs/day: {active_settings['max_bets_per_day']}")
    st.sidebar.caption(f"Stake guide: {active_settings['profile_stake_size']}")
    st.sidebar.caption(active_settings["profile_market_scope"])
    with st.sidebar.expander("Profile Notes"):
        st.write(active_settings["profile_notes"])
    return page, active_settings


def render_daily_slate(slate_df: pd.DataFrame, bets_df: pd.DataFrame, settings: dict[str, str]) -> None:
    st.title("Daily Slate")
    metric_row(slate_df, bets_df)

    if st.button("Refresh MLB Model", type="primary"):
        result = run_local_refresh(settings)
        if result["status"] == "success":
            st.success(
                f"Refresh complete. {result['games']} games loaded, "
                f"{result['odds_events']} odds events fetched, "
                f"{result['weather_loaded']} weather forecasts stored, "
                f"{result['scored_games']} games scored."
            )
        else:
            st.warning("Local refresh logged as partial.")
            for error in result["errors"]:
                st.write(f"- {error}")

    show_bets_only = st.toggle("Show BET only", value=False)
    display_df = slate_df[slate_df["recommendation"] == "BET"] if show_bets_only else slate_df
    slate_columns = [
        "rank",
        "first_pitch_et",
        "status",
        "score_summary",
        "matchup",
        "probable_starters",
        "weather_summary",
        "away_confluence_score",
        "home_confluence_score",
        "recommended_side",
        "best_market",
        "fair_line",
        "market_line",
        "edge_pct",
        "confidence",
        "recommendation",
        "recommendation_result",
        "reason",
    ]
    st.dataframe(
        display_df[slate_columns]
        .style.apply(style_game_status_rows, axis=1)
        .apply(style_recommendation_result, axis=1),
        hide_index=True,
        width="stretch",
    )

    st.subheader("Weather by Game")
    weather_columns = [
        "first_pitch_et",
        "status",
        "score_summary",
        "matchup",
        "venue_name",
        "first_pitch_utc",
        "weather_summary",
    ]
    st.table(
        display_df[weather_columns].style.apply(style_game_status_rows, axis=1),
    )

    st.subheader("Statcast Confluence by Game")
    confluence_columns = [
        "first_pitch_et",
        "status",
        "score_summary",
        "matchup",
        "away_offense_score",
        "away_pitching_score",
        "away_confluence_score",
        "home_offense_score",
        "home_pitching_score",
        "home_confluence_score",
    ]
    st.dataframe(
        display_df[confluence_columns].style.apply(style_game_status_rows, axis=1),
        hide_index=True,
        width="stretch",
    )

    c1, c2 = st.columns(2)
    with c1:
        fig = px.bar(
            slate_df.sort_values("edge_pct"),
            x="edge_pct",
            y="matchup",
            orientation="h",
            color="recommendation",
            title="Edge % by Game",
        )
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        fig = px.scatter(
            slate_df,
            x="confidence",
            y="edge_pct",
            color="recommendation",
            hover_data=["first_pitch_et", "matchup", "best_market"],
            title="Confidence vs Edge %",
        )
        st.plotly_chart(fig, use_container_width=True)


def render_game_breakdown(slate_df: pd.DataFrame) -> None:
    st.title("Game Breakdown")
    selected = st.selectbox("Select matchup", slate_df["matchup"].tolist())
    row = slate_df[slate_df["matchup"] == selected].iloc[0]

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Game Status", row["status"])
    c2.metric("Score", row["score_summary"])
    c3.metric("Recommendation", row["recommendation"])
    c4.metric("Result Check", row["recommendation_result"])
    c5.metric("Actual Winner", row["actual_winner"])
    c6.metric("Best Market", row["best_market"])

    c7, c8, c9 = st.columns(3)
    c7.metric("Market Line", int(row["market_line"]))
    c8.metric("Fair Line", int(row["fair_line"]))
    c9.metric("Edge / Confidence", f"{row['edge_pct']:.1f}% | {row['confidence']:.1f}/10")

    st.subheader("Box Score Summary")
    st.info(row["box_score_summary"])

    st.subheader("Game Metadata")
    st.write(
        f"{row['first_pitch_et']} | {row['matchup']} | Venue: {row['venue_name']} | "
        f"Probable starters: {row['probable_starters']}"
    )
    st.info(row["reason"])

    st.subheader("Weather Forecast")
    w1, w2, w3, w4 = st.columns(4)
    w1.metric("Temperature", f"{row['temperature_f']:.0f}F" if pd.notna(row["temperature_f"]) else "Pending")
    w2.metric("Wind", f"{row['wind_speed_mph']:.0f} mph" if pd.notna(row["wind_speed_mph"]) else "Pending")
    w3.metric("Wind Direction", f"{row['wind_direction_deg']:.0f}deg" if pd.notna(row["wind_direction_deg"]) else "Pending")
    w4.metric("Precip Chance", f"{row['precipitation_probability']:.0f}%" if pd.notna(row["precipitation_probability"]) else "Pending")
    st.caption(f"Forecast hour: {row['forecast_time']}" if pd.notna(row["forecast_time"]) else "Weather refresh pending")

    st.subheader("Baseball Savant / Statcast Confluence")
    statcast_metrics = pd.DataFrame(
        {
            "Metric": [
                "Offense Score",
                "Pitching Prevention Score",
                "Confluence Score",
                "xwOBA For",
                "Hard-Hit % For",
                "xwOBA Allowed",
                "Hard-Hit % Allowed",
            ],
            "Away": [
                row["away_offense_score"],
                row["away_pitching_score"],
                row["away_confluence_score"],
                row["away_xwoba_for"],
                row["away_hard_hit_pct_for"],
                row["away_xwoba_allowed"],
                row["away_hard_hit_pct_allowed"],
            ],
            "Home": [
                row["home_offense_score"],
                row["home_pitching_score"],
                row["home_confluence_score"],
                row["home_xwoba_for"],
                row["home_hard_hit_pct_for"],
                row["home_xwoba_allowed"],
                row["home_hard_hit_pct_allowed"],
            ],
        }
    )
    st.dataframe(statcast_metrics, hide_index=True, width="stretch")

    score_breakdown = pd.DataFrame(
        {
            "Category": ["Starting Pitching", "Bullpen", "Offense", "Lineups", "Weather/Park", "Situation"],
            "Model Score": [
                row["pitching_score"],
                row["bullpen_score"],
                row["offense_score"],
                row["lineup_score"],
                row["weather_score"],
                row["situation_score"],
            ],
            "Notes": [
                "Derived from team Statcast pitching-prevention confluence.",
                "Included in pitching-prevention proxy until bullpen-specific layer is added.",
                "Derived from team Statcast batting confluence.",
                "No major lineup uncertainty in sample data.",
                row["weather_summary"],
                "Home field is included as a small confluence adjustment.",
            ],
        }
    )
    st.dataframe(score_breakdown, hide_index=True, width="stretch")

    st.subheader("Best Market Explanation")
    st.write(
        f"The final prediction points to {row['recommended_side']} in {row['best_market']} at "
        f"{row['market_line']}. Fair value is {row['fair_line']}, with a "
        f"{row['edge_pct']:.1f}% edge and {row['confidence']:.1f}/10 confidence. "
        f"Reason: {row['reason']}"
    )


def render_bet_tracker(slate_df: pd.DataFrame, bets_df: pd.DataFrame, settings: dict[str, str]) -> None:
    st.title("Bet Tracker")

    with st.form("new_bet_form", clear_on_submit=True):
        st.subheader("Add Bet")
        game_options = {"No linked game": None}
        game_options.update({row["matchup"]: row["game_id"] for _, row in slate_df.iterrows()})
        game_label = st.selectbox("Game", list(game_options.keys()))
        bet_label = st.text_input("Bet Label", "LAD F5 ML")
        market = st.selectbox("Market", MARKETS)
        odds = st.number_input("Odds", value=-110, step=1)
        stake = st.number_input(
            "Stake Units",
            value=float(settings["default_stake_units"]),
            min_value=0.0,
            step=0.25,
        )
        closing_line_value = st.text_input("Closing Line", "")
        result = st.selectbox("Result", RESULTS)
        notes = st.text_area("Notes", "")
        submitted = st.form_submit_button("Save Bet")
        if submitted:
            closing_line = int(closing_line_value) if closing_line_value.strip() else None
            add_bet(date.today(), game_options[game_label], bet_label, market, int(odds), float(stake), closing_line, result, notes)
            st.success("Bet saved to SQLite.")

    st.subheader("Historical Bets")
    st.dataframe(bets_df, hide_index=True, width="stretch")


def render_performance(bets_df: pd.DataFrame) -> None:
    st.title("Performance")
    if bets_df.empty:
        st.info("No bets recorded yet.")
        return

    total_units = float(bets_df["profit_loss_units"].sum())
    total_stake = float(bets_df["stake_units"].sum())
    settled = bets_df[bets_df["result"].isin(["W", "L"])]
    win_rate = float((settled["result"] == "W").mean()) if len(settled) else 0.0
    avg_clv = float(bets_df["clv_cents"].dropna().mean()) if bets_df["clv_cents"].notna().any() else 0.0
    clv_win_pct = float((bets_df["clv_cents"].fillna(0) > 0).mean())

    p1, p2, p3, p4, p5 = st.columns(5)
    p1.metric("Total Units", f"{total_units:+.2f}")
    p2.metric("ROI", f"{(total_units / total_stake if total_stake else 0):.1%}")
    p3.metric("Win Rate", f"{win_rate:.1%}")
    p4.metric("Avg CLV", f"{avg_clv:.1f} cents")
    p5.metric("CLV Win %", f"{clv_win_pct:.1%}")

    chart_df = bets_df.copy()
    chart_df["bet_date"] = pd.to_datetime(chart_df["bet_date"])
    chart_df["month"] = chart_df["bet_date"].dt.to_period("M").astype(str)
    chart_df["cumulative_units"] = chart_df["profit_loss_units"].cumsum()
    chart_df["confidence_bucket"] = pd.cut(
        chart_df.get("confidence", pd.Series([0] * len(chart_df))),
        bins=[0, 6, 7, 8, 10],
        labels=["<6", "6-7", "7-8", "8+"],
        include_lowest=True,
    )

    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(px.line(chart_df, x="bet_date", y="cumulative_units", title="Cumulative Units"), use_container_width=True)
        st.plotly_chart(px.bar(chart_df, x="market", y="profit_loss_units", title="Profit/Loss by Market"), use_container_width=True)
    with c2:
        monthly = chart_df.groupby("month", as_index=False).agg(profit_loss_units=("profit_loss_units", "sum"), stake_units=("stake_units", "sum"))
        monthly["roi"] = monthly["profit_loss_units"] / monthly["stake_units"]
        st.plotly_chart(px.bar(monthly, x="month", y="roi", title="ROI by Month"), use_container_width=True)
        st.plotly_chart(px.histogram(chart_df, x="clv_cents", title="CLV Distribution"), use_container_width=True)


def render_data_health(settings: dict[str, str]) -> None:
    st.title("Data Health & Refresh Logs")
    mlb_marker_configured = bool(get_secret("MLB_STATS_API_MARKER"))
    odds_configured = bool(get_secret("ODDS_API_KEY"))
    statcast_metrics = load_dataframe("SELECT COUNT(*) AS team_count, MAX(captured_at) AS last_refresh FROM team_statcast_metrics")
    team_count = int(statcast_metrics.iloc[0]["team_count"]) if not statcast_metrics.empty else 0
    last_statcast_refresh = statcast_metrics.iloc[0]["last_refresh"] if team_count else "Not loaded"
    health = pd.DataFrame(
        [
            ["MLB Stats API", "Schedule/probable pitchers", "Marker configured" if mlb_marker_configured else "Ready via public endpoint"],
            ["Odds API", "Market lines", "Configured" if odds_configured else "Needs API key/login before live odds"],
            ["Open-Meteo", "Weather", "Ready, no login required"],
            ["pybaseball Statcast", "Baseball Savant team metrics", f"{team_count} teams loaded; last {last_statcast_refresh}" if team_count else "Needs refresh"],
            ["SQLite", "Local storage", "Writable" if os.access(DATA_DIR, os.W_OK) else "Not writable"],
        ],
        columns=["Source", "Purpose", "Status"],
    )
    st.dataframe(health, hide_index=True, width="stretch")

    if st.button("Run Local Refresh Check"):
        result = run_local_refresh(settings)
        if result["status"] == "success":
            st.success(
                f"Refresh succeeded: {result['odds_events']} odds events, "
                f"{result['odds_markets']} market rows, {result['weather_loaded']} weather forecasts, "
                f"{result['statcast_teams']} Statcast teams, {result['scored_games']} scored games."
            )
        else:
            st.warning("Refresh logged as partial.")
            for error in result["errors"]:
                st.write(f"- {error}")

    live_odds = load_live_odds()
    st.subheader("Latest Live Odds Snapshot")
    if live_odds.empty:
        st.info("No live odds snapshot stored yet. Run the local refresh check after setting the API key.")
    else:
        display_odds = live_odds.copy()
        display_odds["matchup"] = display_odds["away_team"] + " @ " + display_odds["home_team"]
        st.dataframe(
            display_odds[["commence_time", "matchup", "bookmaker", "market", "outcomes_json", "captured_at"]],
            hide_index=True,
            width="stretch",
        )

    logs = load_dataframe("SELECT * FROM refresh_logs ORDER BY id DESC LIMIT 25")
    st.subheader("Refresh Logs")
    st.dataframe(logs, hide_index=True, width="stretch")


def render_settings(settings: dict[str, str]) -> None:
    st.title("Settings")
    st.subheader("Dashboard Branding")
    current_logo = resolve_asset_path(settings.get("dashboard_logo_path", ""))
    if current_logo:
        st.image(str(current_logo), width=260)

    with st.form("settings_form"):
        dashboard_title = st.text_input("Dashboard Title", settings.get("dashboard_title", "MLB Edge Model"))
        uploaded_logo = st.file_uploader("Upload Dashboard Logo", type=["png", "jpg", "jpeg", "webp"])

        st.subheader("Rule Profile")
        rule_profile = st.selectbox(
            "Saved Rule Profile",
            list(RULE_PROFILES.keys()),
            index=list(RULE_PROFILES.keys()).index(settings.get("rule_profile", "Conservative"))
            if settings.get("rule_profile", "Conservative") in RULE_PROFILES
            else 1,
        )
        use_manual_rules = st.checkbox(
            "Use manual threshold rules below instead of the saved profile",
            value=str(settings.get("use_manual_rules", "false")).lower() in {"1", "true", "yes", "on"},
        )
        profile_preview = active_rule_settings({**settings, "rule_profile": rule_profile, "use_manual_rules": str(use_manual_rules).lower()})
        st.caption(
            f"Active after save: {profile_preview['rule_profile']} | "
            f"Edge >= {float(profile_preview['edge_threshold']):.1f}% | "
            f"Line diff >= {int(float(profile_preview['min_line_difference_cents']))}c | "
            f"Confidence >= {float(profile_preview['min_confidence']):.1f} | "
            f"Max BETs/day {profile_preview['max_bets_per_day']}"
        )

        st.subheader("Manual Thresholds")
        edge_threshold = st.number_input("Manual Edge Threshold (%)", value=float(settings["edge_threshold"]), step=0.25)
        min_line_difference = st.number_input("Minimum Line Difference (cents)", value=int(float(settings["min_line_difference_cents"])), step=1)
        min_confidence = st.number_input("Minimum Confidence", value=float(settings["min_confidence"]), min_value=0.0, max_value=10.0, step=0.1)
        default_stake = st.number_input("Default Stake Units", value=float(settings["default_stake_units"]), step=0.25)
        bankroll_units = st.number_input("Bankroll Units", value=float(settings["bankroll_units"]), step=1.0)
        bookmaker = st.text_input("Odds Source / Bookmaker Preference", settings["bookmaker_preference"])
        statcast_lookback_days = st.number_input("Statcast Lookback Days", value=int(float(settings["statcast_lookback_days"])), min_value=3, max_value=45, step=1)
        dashboard_password = st.text_input("Dashboard Password", settings["dashboard_password"], type="password")
        submitted = st.form_submit_button("Save Settings")
        if submitted:
            logo_path = settings.get("dashboard_logo_path", "")
            if uploaded_logo is not None:
                logo_path = save_uploaded_logo(uploaded_logo)
            updates = {
                "dashboard_title": dashboard_title,
                "dashboard_logo_path": logo_path,
                "rule_profile": rule_profile,
                "use_manual_rules": str(use_manual_rules).lower(),
                "edge_threshold": edge_threshold,
                "min_line_difference_cents": min_line_difference,
                "min_confidence": min_confidence,
                "default_stake_units": default_stake,
                "bankroll_units": bankroll_units,
                "bookmaker_preference": bookmaker,
                "statcast_lookback_days": statcast_lookback_days,
                "dashboard_password": dashboard_password,
            }
            for key, value in updates.items():
                save_setting(key, value)
            saved_settings = load_settings()
            scored_games = rescore_current_slate(active_rule_settings(saved_settings))
            st.success(f"Settings saved and {scored_games} games re-scored with the active rules.")
            st.rerun()

    st.subheader("API Setup Status")
    if get_secret("MLB_STATS_API_MARKER"):
        st.success("MLB Stats API marker is configured locally.")
    else:
        st.info("MLB Stats API is using the public no-login schedule endpoint.")
    if get_secret("ODDS_API_KEY"):
        st.success("The Odds API key is configured locally.")
    else:
        st.write("Live odds are disabled until an API key is configured.")
        st.code("ODDS_API_KEY=your-key-here", language="bash")


def main() -> None:
    initialize_database()
    settings = load_settings()
    slate_df = load_slate()
    bets_df = load_bets()
    page, active_settings = sidebar(settings)

    if page == "Daily Slate":
        render_daily_slate(slate_df, bets_df, active_settings)
    elif page == "Game Breakdown":
        render_game_breakdown(slate_df)
    elif page == "Bet Tracker":
        render_bet_tracker(slate_df, bets_df, active_settings)
    elif page == "Performance":
        render_performance(bets_df)
    elif page == "Data Health":
        render_data_health(active_settings)
    elif page == "Settings":
        render_settings(settings)


if __name__ == "__main__":
    main()
