"""Local MLB Edge Model dashboard.

Run locally:
    streamlit run app.py

The app initializes a SQLite database in data/mlb_edge.db, seeds it from the
included sample CSV files, and keeps API-backed refreshes behind explicit setup.
"""

from __future__ import annotations

import html
import io
import json
import math
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

from src.collectors.mlb_stats import get_game_boxscore, get_schedule, get_schedule_range, get_team_roster
from src.collectors.odds_api import get_mlb_odds
from src.collectors.open_meteo import get_stadium_weather
from src.collectors.retrosheet import get_season_gameinfo
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
    "odds_stale_minutes": "20",
    "retrosheet_season": "2025",
    "rule_profile": "Conservative",
    "use_manual_rules": "false",
    "dashboard_title": "MLB Edge Model",
    "dashboard_logo_path": "data/branding/mlb_edge_logo_default.png",
    "dashboard_password": "",
}

MODEL_VERSION = "statcast_context_v2.0"
RETROSHEET_MODEL_VERSION = "retrosheet_rolling_baseline_v1.0"
RETROSHEET_TEAM_MAP = {
    "ANA": "LAA", "ARI": "ARI", "ATL": "ATL", "BAL": "BAL", "BOS": "BOS",
    "CHA": "CWS", "CHN": "CHC", "CIN": "CIN", "CLE": "CLE", "COL": "COL",
    "DET": "DET", "HOU": "HOU", "KCA": "KC", "LAN": "LAD", "MIA": "MIA",
    "MIL": "MIL", "MIN": "MIN", "NYA": "NYY", "NYN": "NYM", "OAK": "ATH",
    "PHI": "PHI", "PIT": "PIT", "SDN": "SD", "SEA": "SEA", "SFN": "SF",
    "SLN": "STL", "TBA": "TB", "TEX": "TEX", "TOR": "TOR", "WAS": "WSH",
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

st.set_page_config(
    page_title="MLB Edge Model",
    page_icon="MLB",
    layout="wide",
    initial_sidebar_state="auto",
)


def inject_dashboard_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --edge-ink: #102133;
            --edge-navy: #152b40;
            --edge-red: #b51f32;
            --edge-green: #087f5b;
            --edge-gold: #b7791f;
            --edge-muted: #5f7182;
            --edge-line: #dbe3e9;
            --edge-surface: #ffffff;
            --edge-canvas: #f3f6f8;
        }

        html, body, [class*="css"] {
            font-family: "Avenir Next", "Segoe UI", Arial, sans-serif;
            color: var(--edge-ink);
        }

        [data-testid="stAppViewContainer"] {
            background: var(--edge-canvas);
        }

        [data-testid="stHeader"] {
            background: rgba(243, 246, 248, 0.92);
            border-bottom: 1px solid rgba(219, 227, 233, 0.75);
        }

        [data-testid="stMainBlockContainer"] {
            max-width: 1480px;
            padding-top: 4.5rem;
            padding-bottom: 4rem;
        }

        [data-testid="stSidebar"] {
            background: var(--edge-navy);
            border-right: 1px solid #263e53;
        }

        [data-testid="stSidebar"] * {
            color: #f4f7f9;
        }

        [data-testid="stSidebar"] [data-testid="stImage"] img {
            max-height: 168px;
            object-fit: contain;
            border-radius: 6px;
        }

        [data-testid="stSidebar"] [role="radiogroup"] label {
            border-radius: 6px;
            padding: 0.55rem 0.65rem;
            margin: 0.14rem 0;
            transition: background-color 120ms ease, transform 120ms ease;
        }

        [data-testid="stSidebar"] [role="radiogroup"] label:hover {
            background: #203b52;
            transform: translateX(2px);
        }

        [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
            background: #f4f7f9;
        }

        [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) p {
            color: var(--edge-ink) !important;
            font-weight: 700;
        }

        [data-testid="stSidebar"] [data-baseweb="radio"] > div:first-child {
            display: none;
        }

        [data-testid="stMainBlockContainer"] h1,
        [data-testid="stMainBlockContainer"] h2,
        [data-testid="stMainBlockContainer"] h3 {
            color: var(--edge-ink) !important;
            font-family: "Arial Narrow", "Aptos Display", "Segoe UI", sans-serif;
            letter-spacing: 0;
        }

        h1 { font-size: 2.15rem; line-height: 1.05; }
        h2 { font-size: 1.35rem; }
        h3 { font-size: 1.05rem; }

        .edge-page-header {
            display: flex;
            align-items: flex-end;
            justify-content: space-between;
            gap: 1.25rem;
            margin: 0 0 1.15rem;
            padding-bottom: 1rem;
            border-bottom: 1px solid var(--edge-line);
        }

        .edge-eyebrow {
            color: var(--edge-red) !important;
            font-family: "Arial Narrow", "Aptos Display", sans-serif;
            font-size: 0.76rem;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 0.32rem;
        }

        .edge-page-title {
            color: var(--edge-ink) !important;
            font-family: "Arial Narrow", "Aptos Display", sans-serif;
            font-size: clamp(2rem, 4vw, 3.4rem);
            font-weight: 800;
            line-height: 0.98;
            margin: 0;
        }

        .edge-page-subtitle {
            color: var(--edge-muted);
            font-size: 0.96rem;
            line-height: 1.5;
            margin: 0.55rem 0 0;
            max-width: 740px;
        }

        .edge-page-meta {
            color: var(--edge-muted);
            font-family: "SFMono-Regular", Consolas, monospace;
            font-size: 0.76rem;
            white-space: nowrap;
            padding-bottom: 0.25rem;
        }

        .edge-kpi {
            min-height: 112px;
            background: var(--edge-surface);
            border: 1px solid var(--edge-line);
            border-top: 3px solid var(--edge-navy);
            border-radius: 6px;
            padding: 0.95rem 1rem 0.9rem;
            box-shadow: 0 8px 24px rgba(16, 33, 51, 0.045);
        }

        .edge-kpi-grid {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 1rem;
        }

        .edge-kpi.accent { border-top-color: var(--edge-red); }
        .edge-kpi.positive { border-top-color: var(--edge-green); }

        .edge-kpi-label {
            color: var(--edge-muted);
            font-size: 0.72rem;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }

        .edge-kpi-value {
            color: var(--edge-ink);
            font-family: "Arial Narrow", "Aptos Display", sans-serif;
            font-size: 1.85rem;
            font-weight: 800;
            line-height: 1.1;
            margin-top: 0.4rem;
        }

        .edge-kpi-note {
            color: var(--edge-muted);
            font-size: 0.72rem;
            margin-top: 0.28rem;
        }

        .edge-section-heading {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 1rem;
            margin: 1.8rem 0 0.8rem;
        }

        .edge-section-heading h2 {
            margin: 0;
            font-size: 1.35rem;
            color: var(--edge-ink) !important;
        }

        .edge-section-heading span {
            color: var(--edge-muted);
            font-size: 0.78rem;
        }

        .edge-pick-card {
            position: relative;
            min-height: 218px;
            overflow: hidden;
            background: var(--edge-surface);
            border: 1px solid var(--edge-line);
            border-radius: 7px;
            padding: 1rem 1.05rem;
            box-shadow: 0 10px 28px rgba(16, 33, 51, 0.055);
        }

        .edge-pick-card::after {
            content: "";
            position: absolute;
            width: 78px;
            height: 78px;
            right: -36px;
            bottom: -36px;
            border: 12px solid #e9eef2;
            border-radius: 50%;
        }

        .edge-pick-top {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.8rem;
        }

        .edge-badge {
            display: inline-flex;
            border-radius: 999px;
            padding: 0.22rem 0.55rem;
            font-size: 0.68rem;
            font-weight: 900;
            letter-spacing: 0.04em;
        }

        .edge-badge.bet { color: #056448; background: #dff4ec; }
        .edge-badge.pass { color: #982033; background: #f9e3e7; }
        .edge-badge.live { color: #805b05; background: #fff1c2; }

        .edge-pick-time {
            color: var(--edge-muted);
            font-family: "SFMono-Regular", Consolas, monospace;
            font-size: 0.72rem;
        }

        .edge-matchup {
            color: var(--edge-ink);
            font-family: "Arial Narrow", "Aptos Display", sans-serif;
            font-size: 1.32rem;
            font-weight: 800;
            line-height: 1.15;
            margin: 0.95rem 0 0.5rem;
        }

        .edge-pick-side {
            color: var(--edge-muted);
            font-size: 0.82rem;
            min-height: 2.3rem;
        }

        .edge-pick-stats {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 0.55rem;
            margin-top: 1rem;
            padding-top: 0.75rem;
            border-top: 1px solid var(--edge-line);
        }

        .edge-pick-stat strong {
            display: block;
            color: var(--edge-ink);
            font-size: 0.93rem;
        }

        .edge-pick-stat span {
            color: var(--edge-muted);
            font-size: 0.66rem;
            text-transform: uppercase;
        }

        .edge-scoreboard {
            display: grid;
            grid-template-columns: 1fr auto 1fr;
            align-items: center;
            gap: 1rem;
            background: var(--edge-navy);
            border-radius: 7px;
            padding: 1.25rem 1.4rem;
            color: white;
            box-shadow: 0 14px 34px rgba(16, 33, 51, 0.14);
        }

        .edge-team { min-width: 0; }
        .edge-team.home { text-align: right; }
        .edge-team-name {
            color: white;
            font-family: "Arial Narrow", "Aptos Display", sans-serif;
            font-size: clamp(1.2rem, 2.5vw, 2rem);
            font-weight: 800;
            line-height: 1.05;
        }
        .edge-team-pitcher { color: #aebdca; font-size: 0.78rem; margin-top: 0.35rem; }
        .edge-score-center { text-align: center; min-width: 92px; }
        .edge-score { color: white; font-family: "Arial Narrow", sans-serif; font-size: 2rem; font-weight: 800; }
        .edge-score-status { color: #becbd5; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.07em; }

        .edge-rule-card {
            background: #203b52;
            border: 1px solid #315069;
            border-radius: 6px;
            padding: 0.8rem;
            margin-top: 0.8rem;
        }
        .edge-rule-card strong { display: block; font-size: 0.86rem; }
        .edge-rule-card span { color: #c5d1da !important; font-size: 0.7rem; line-height: 1.4; }
        .edge-sidebar-label { color: #9fb0bf !important; font-size: 0.67rem; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase; margin-top: 0.7rem; }
        .edge-sidebar-footer { color: #9fb0bf !important; font-size: 0.66rem; line-height: 1.45; margin-top: 1.2rem; }

        .edge-guide-card {
            background: white;
            border: 1px solid var(--edge-line);
            border-radius: 6px;
            padding: 1rem;
            min-height: 160px;
        }
        .edge-guide-card strong { display: block; color: var(--edge-ink); margin-bottom: 0.4rem; }
        .edge-guide-card p { color: var(--edge-muted); font-size: 0.86rem; line-height: 1.5; margin: 0; }

        div[data-testid="stMetric"] {
            background: white;
            border: 1px solid var(--edge-line);
            border-radius: 6px;
            padding: 0.85rem 0.95rem;
            box-shadow: 0 7px 20px rgba(16, 33, 51, 0.04);
        }

        [data-testid="stMainBlockContainer"] div[data-testid="stMetric"] *,
        [data-testid="stMainBlockContainer"] div[data-testid="stWidgetLabel"] p,
        [data-testid="stMainBlockContainer"] [data-baseweb="tab"] p {
            color: var(--edge-ink) !important;
        }

        div[data-testid="stForm"], div[data-testid="stExpander"] {
            background: white;
            border-color: var(--edge-line);
            border-radius: 6px;
        }

        .stButton > button, .stFormSubmitButton > button {
            border-radius: 5px;
            font-weight: 750;
            min-height: 2.7rem;
        }

        .stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
            background: var(--edge-red);
            border-color: var(--edge-red);
        }

        button:focus-visible, input:focus-visible, [role="tab"]:focus-visible {
            outline: 3px solid rgba(181, 31, 50, 0.28) !important;
            outline-offset: 2px;
        }

        [data-baseweb="tab-list"] { gap: 0.35rem; }
        [data-baseweb="tab"] {
            border-radius: 5px 5px 0 0;
            padding: 0.65rem 0.9rem;
            font-weight: 700;
        }
        [aria-selected="true"][data-baseweb="tab"] {
            color: var(--edge-red);
            background: white;
        }

        [data-testid="stDataFrame"] {
            border: 1px solid var(--edge-line);
            border-radius: 6px;
            overflow: hidden;
        }

        @media (max-width: 860px) {
            [data-testid="stMainBlockContainer"] { padding: 4.25rem 0.85rem 3rem; }
            .edge-page-header { display: block; }
            .edge-page-meta { margin-top: 0.75rem; white-space: normal; }
            .edge-page-title { font-size: 2.25rem; }
            .edge-scoreboard { grid-template-columns: 1fr; text-align: left; }
            .edge-team.home { text-align: left; }
            .edge-score-center { text-align: left; }
            .edge-score { font-size: 1.4rem; }
            .edge-kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.7rem; }
            .edge-kpi-grid .edge-kpi:last-child { grid-column: 1 / -1; }
            .edge-kpi { min-height: 102px; padding: 0.78rem; }
            .edge-kpi-value { font-size: 1.55rem; }
        }

        @media (prefers-reduced-motion: reduce) {
            * { scroll-behavior: auto !important; transition: none !important; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_page_header(title: str, eyebrow: str, subtitle: str, meta: str = "") -> None:
    st.markdown(
        f"""
        <div class="edge-page-header">
            <div>
                <div class="edge-eyebrow">{html.escape(eyebrow)}</div>
                <h1 class="edge-page-title">{html.escape(title)}</h1>
                <p class="edge-page-subtitle">{html.escape(subtitle)}</p>
            </div>
            <div class="edge-page-meta">{html.escape(meta)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_section_heading(title: str, detail: str = "") -> None:
    st.markdown(
        f'<div class="edge-section-heading"><h2>{html.escape(title)}</h2><span>{html.escape(detail)}</span></div>',
        unsafe_allow_html=True,
    )


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
    if isinstance(conn, PostgresConnection) and get_secret("RUN_SUPABASE_SCHEMA_ON_START", "false").lower() != "true":
        return
    if not schema_path.exists():
        raise FileNotFoundError(f"Database schema file not found: {schema_path}")
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
        "away_team_id": "INTEGER",
        "home_team_id": "INTEGER",
        "away_team_name": "TEXT",
        "home_team_name": "TEXT",
        "status_state": "TEXT",
        "status_code": "TEXT",
        "away_score": "INTEGER",
        "home_score": "INTEGER",
        "inning_state": "TEXT",
        "current_inning": "TEXT",
        "score_summary": "TEXT",
        "box_score_summary": "TEXT",
        "away_lineup_status": "TEXT DEFAULT 'Pending'",
        "home_lineup_status": "TEXT DEFAULT 'Pending'",
        "away_lineup_json": "TEXT",
        "home_lineup_json": "TEXT",
        "away_injury_count": "INTEGER DEFAULT 0",
        "home_injury_count": "INTEGER DEFAULT 0",
        "away_injured_hitters": "INTEGER DEFAULT 0",
        "home_injured_hitters": "INTEGER DEFAULT 0",
        "away_injuries_json": "TEXT",
        "home_injuries_json": "TEXT",
        "away_bullpen_pitches_1d": "INTEGER DEFAULT 0",
        "home_bullpen_pitches_1d": "INTEGER DEFAULT 0",
        "away_bullpen_pitches_3d": "INTEGER DEFAULT 0",
        "home_bullpen_pitches_3d": "INTEGER DEFAULT 0",
        "away_bullpen_status": "TEXT DEFAULT 'Unknown'",
        "home_bullpen_status": "TEXT DEFAULT 'Unknown'",
        "pitcher_change_detected": "INTEGER DEFAULT 0",
        "pitcher_change_details": "TEXT",
        "weather_risk_level": "TEXT DEFAULT 'Unknown'",
        "context_updated_at": "TEXT",
    }
    for column, definition in game_columns.items():
        ensure_column(conn, "games", column, definition)
    recommendation_columns = {
        "model_version": "TEXT",
        "model_probability": "REAL",
        "rule_profile": "TEXT",
        "rule_checks_json": "TEXT",
    }
    for column, definition in recommendation_columns.items():
        ensure_column(conn, "model_recommendations", column, definition)
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
    bucket = row.get("game_status_bucket") or game_status_bucket(
        row.get("status", row.get("Status")), row.get("status_state", "")
    )
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
    result = row.get("recommendation_result", row.get("Result", ""))
    recommendation = row.get("recommendation", row.get("Call", ""))
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
            if column in {"recommendation", "Call"}:
                styles[index] = recommendation_color
            elif column in {"recommendation_result", "Result"}:
                styles[index] = result_color
        return styles
    else:
        color = "background-color: rgba(107, 114, 128, 0.75); color: white; font-weight: 700"

    for index, column in enumerate(row.index):
        if column in {"recommendation", "recommendation_result", "Call", "Result"}:
            styles[index] = color
    return styles


def parse_rule_blockers(value: object) -> str:
    try:
        payload = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return ""
    return ", ".join(str(blocker) for blocker in payload.get("blockers", []))


def load_slate() -> pd.DataFrame:
    slate = load_dataframe(
        """
        SELECT
            mr.id,
            ROW_NUMBER() OVER (ORDER BY mr.edge_pct DESC, mr.confidence DESC) AS rank,
            g.game_id,
            g.game_date,
            g.away_team,
            g.home_team,
            COALESCE(g.away_team_name, g.away_team) AS away_team_name,
            COALESCE(g.home_team_name, g.home_team) AS home_team_name,
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
            COALESCE(g.away_lineup_status, 'Pending') AS away_lineup_status,
            COALESCE(g.home_lineup_status, 'Pending') AS home_lineup_status,
            COALESCE(g.away_lineup_json, '[]') AS away_lineup_json,
            COALESCE(g.home_lineup_json, '[]') AS home_lineup_json,
            COALESCE(g.away_injury_count, 0) AS away_injury_count,
            COALESCE(g.home_injury_count, 0) AS home_injury_count,
            COALESCE(g.away_injured_hitters, 0) AS away_injured_hitters,
            COALESCE(g.home_injured_hitters, 0) AS home_injured_hitters,
            COALESCE(g.away_injuries_json, '[]') AS away_injuries_json,
            COALESCE(g.home_injuries_json, '[]') AS home_injuries_json,
            COALESCE(g.away_bullpen_pitches_1d, 0) AS away_bullpen_pitches_1d,
            COALESCE(g.home_bullpen_pitches_1d, 0) AS home_bullpen_pitches_1d,
            COALESCE(g.away_bullpen_pitches_3d, 0) AS away_bullpen_pitches_3d,
            COALESCE(g.home_bullpen_pitches_3d, 0) AS home_bullpen_pitches_3d,
            COALESCE(g.away_bullpen_status, 'Unknown') AS away_bullpen_status,
            COALESCE(g.home_bullpen_status, 'Unknown') AS home_bullpen_status,
            COALESCE(g.pitcher_change_detected, 0) AS pitcher_change_detected,
            COALESCE(g.pitcher_change_details, '') AS pitcher_change_details,
            COALESCE(g.weather_risk_level, 'Unknown') AS weather_risk_level,
            g.context_updated_at,
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
            COALESCE(mr.model_version, 'legacy') AS model_version,
            COALESCE(mr.model_probability, 0) AS model_probability,
            COALESCE(mr.rule_profile, 'Legacy') AS rule_profile,
            COALESCE(mr.rule_checks_json, '{}') AS rule_checks_json,
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
    slate["rule_blockers"] = slate["rule_checks_json"].apply(parse_rule_blockers)
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


def historical_date_bounds() -> tuple[date, date, int]:
    bounds = load_dataframe(
        "SELECT MIN(game_date) AS first_date, MAX(game_date) AS last_date, COUNT(*) AS game_count FROM games"
    )
    if bounds.empty or not bounds.iloc[0]["first_date"]:
        today = date.today()
        return today, today, 0
    return (
        date.fromisoformat(str(bounds.iloc[0]["first_date"])),
        date.fromisoformat(str(bounds.iloc[0]["last_date"])),
        int(bounds.iloc[0]["game_count"]),
    )


def json_list_text(value: object, field: str = "") -> str:
    try:
        payload = json.loads(str(value or "[]"))
    except (TypeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, list):
        return ""
    if field:
        return "; ".join(str(item.get(field, "")) for item in payload if isinstance(item, dict) and item.get(field))
    return "; ".join(str(item) for item in payload if item)


def load_historical_export_data(start_date: date, end_date: date) -> dict[str, pd.DataFrame]:
    start_text = start_date.isoformat()
    end_text = end_date.isoformat()
    games = load_dataframe(
        """
        SELECT
            g.game_id, g.game_date, g.first_pitch_utc,
            g.away_team, g.home_team,
            COALESCE(g.away_team_name, g.away_team) AS away_team_name,
            COALESCE(g.home_team_name, g.home_team) AS home_team_name,
            g.away_probable_pitcher, g.home_probable_pitcher,
            g.venue_name, g.status, g.status_state, g.status_code,
            g.away_score, g.home_score, g.score_summary, g.box_score_summary,
            g.away_lineup_status, g.home_lineup_status,
            g.away_lineup_json, g.home_lineup_json,
            g.away_injury_count, g.home_injury_count,
            g.away_injured_hitters, g.home_injured_hitters,
            g.away_injuries_json, g.home_injuries_json,
            g.away_bullpen_pitches_1d, g.home_bullpen_pitches_1d,
            g.away_bullpen_pitches_3d, g.home_bullpen_pitches_3d,
            g.away_bullpen_status, g.home_bullpen_status,
            g.pitcher_change_detected, g.pitcher_change_details,
            g.weather_risk_level, g.context_updated_at,
            mr.recommended_side, mr.best_market, mr.fair_line, mr.market_line,
            mr.edge_pct, mr.confidence, mr.model_probability,
            mr.pitching_score, mr.bullpen_score, mr.offense_score,
            mr.lineup_score, mr.weather_score, mr.situation_score,
            mr.total_score AS model_score, mr.recommendation, mr.reason,
            mr.model_version, mr.rule_profile, mr.rule_checks_json,
            ws.forecast_time, ws.temperature_f, ws.wind_speed_mph,
            ws.wind_direction_deg, ws.precipitation_probability, ws.weather_summary
        FROM games g
        LEFT JOIN model_recommendations mr ON mr.game_id = g.game_id
            AND mr.id IN (SELECT MAX(id) FROM model_recommendations GROUP BY game_id, run_date)
        LEFT JOIN weather_snapshots ws ON ws.game_id = g.game_id
            AND ws.id IN (SELECT MAX(id) FROM weather_snapshots GROUP BY game_id)
        WHERE g.game_date BETWEEN ? AND ?
        ORDER BY g.game_date, g.first_pitch_utc, g.game_id
        """,
        (start_text, end_text),
    )
    if not games.empty:
        games["matchup"] = games["away_team_name"] + " @ " + games["home_team_name"]
        games["first_pitch_et"] = games["first_pitch_utc"].fillna("").apply(format_first_pitch)
        games["away_lineup"] = games["away_lineup_json"].apply(json_list_text)
        games["home_lineup"] = games["home_lineup_json"].apply(json_list_text)
        games["away_injuries"] = games["away_injuries_json"].apply(lambda value: json_list_text(value, "player"))
        games["home_injuries"] = games["home_injuries_json"].apply(lambda value: json_list_text(value, "player"))
        games["actual_winner"] = games.apply(actual_winner, axis=1)
        games["recommendation_result"] = games.apply(settle_recommendation, axis=1)
        games["rule_blockers"] = games["rule_checks_json"].apply(parse_rule_blockers)
        preferred = [
            "game_date", "first_pitch_et", "matchup", "status", "score_summary",
            "away_score", "home_score", "actual_winner", "recommendation",
            "recommendation_result", "recommended_side", "best_market", "fair_line",
            "market_line", "edge_pct", "confidence", "model_probability", "rule_profile",
            "model_version", "rule_blockers", "reason", "away_probable_pitcher",
            "home_probable_pitcher", "venue_name", "away_lineup_status", "home_lineup_status",
            "away_lineup", "home_lineup", "away_injury_count", "home_injury_count",
            "away_injured_hitters", "home_injured_hitters", "away_injuries", "home_injuries",
            "away_bullpen_pitches_1d", "home_bullpen_pitches_1d",
            "away_bullpen_pitches_3d", "home_bullpen_pitches_3d",
            "away_bullpen_status", "home_bullpen_status", "weather_risk_level",
            "temperature_f", "wind_speed_mph", "precipitation_probability", "weather_summary",
            "pitcher_change_detected", "pitcher_change_details", "pitching_score",
            "bullpen_score", "offense_score", "lineup_score", "weather_score",
            "situation_score", "model_score", "box_score_summary", "game_id", "context_updated_at",
        ]
        games = games[[column for column in preferred if column in games.columns]]

    prediction_history = load_dataframe(
        """
        SELECT mph.*, g.game_date, g.away_team, g.home_team
        FROM model_prediction_history mph
        JOIN games g ON g.game_id = mph.game_id
        WHERE g.game_date BETWEEN ? AND ?
        ORDER BY g.game_date, mph.game_id, mph.captured_at
        """,
        (start_text, end_text),
    )
    context_history = load_dataframe(
        """
        SELECT gcs.*, g.game_date, g.away_team, g.home_team
        FROM game_context_snapshots gcs
        JOIN games g ON g.game_id = gcs.game_id
        WHERE g.game_date BETWEEN ? AND ?
        ORDER BY g.game_date, gcs.game_id, gcs.captured_at
        """,
        (start_text, end_text),
    )
    odds_raw = load_dataframe(
        """
        SELECT event_id, commence_time, away_team, home_team, bookmaker,
               market, outcomes_json, captured_at
        FROM live_odds_snapshots
        WHERE SUBSTR(commence_time, 1, 10) BETWEEN ? AND ?
        ORDER BY commence_time, captured_at, bookmaker, market
        """,
        (start_text, end_text),
    )
    odds_rows = []
    for snapshot in odds_raw.to_dict("records"):
        try:
            outcomes = json.loads(str(snapshot.pop("outcomes_json") or "[]"))
        except json.JSONDecodeError:
            outcomes = []
        for outcome in outcomes:
            odds_rows.append(
                {
                    **snapshot,
                    "outcome": outcome.get("name", ""),
                    "price": outcome.get("price"),
                    "point": outcome.get("point"),
                }
            )
    odds_history = pd.DataFrame(odds_rows)
    weather_history = load_dataframe(
        """
        SELECT ws.*, g.game_date, g.away_team, g.home_team
        FROM weather_snapshots ws
        JOIN games g ON g.game_id = ws.game_id
        WHERE g.game_date BETWEEN ? AND ?
        ORDER BY g.game_date, ws.game_id, ws.captured_at
        """,
        (start_text, end_text),
    )
    bets = load_dataframe(
        "SELECT * FROM bets WHERE bet_date BETWEEN ? AND ? ORDER BY bet_date, id",
        (start_text, end_text),
    )
    statcast = load_dataframe(
        """
        SELECT * FROM team_statcast_metrics
        WHERE end_date >= ? AND start_date <= ?
        ORDER BY captured_at, team
        """,
        (start_text, end_text),
    )
    refreshes = load_dataframe(
        """
        SELECT * FROM refresh_logs
        WHERE SUBSTR(refresh_started_at, 1, 10) BETWEEN ? AND ?
        ORDER BY refresh_started_at
        """,
        (start_text, end_text),
    )
    return {
        "Games": games,
        "Prediction History": prediction_history,
        "Game Context": context_history,
        "Odds History": odds_history,
        "Weather History": weather_history,
        "Bets": bets,
        "Statcast Snapshots": statcast,
        "Refresh Log": refreshes,
    }


def build_historical_excel(start_date: date, end_date: date) -> tuple[bytes, dict[str, int]]:
    datasets = load_historical_export_data(start_date, end_date)
    counts = {name: len(frame) for name, frame in datasets.items()}
    export_info = pd.DataFrame(
        [
            ["Export created", datetime.now(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %I:%M %p") + f" {DISPLAY_TIMEZONE_LABEL}"],
            ["Date range", f"{start_date.isoformat()} through {end_date.isoformat()}"],
            ["Games", counts["Games"]],
            ["Prediction snapshots", counts["Prediction History"]],
            ["Odds outcomes", counts["Odds History"]],
            ["Database", "Supabase/Postgres" if is_postgres_enabled() else "Local SQLite"],
            ["Model version", MODEL_VERSION],
        ],
        columns=["Field", "Value"],
    )
    output = io.BytesIO()
    with pd.ExcelWriter(
        output,
        engine="xlsxwriter",
        date_format="yyyy-mm-dd",
        datetime_format="yyyy-mm-dd hh:mm:ss",
        engine_kwargs={"options": {"strings_to_formulas": False, "strings_to_urls": False}},
    ) as writer:
        workbook = writer.book
        header_format = workbook.add_format(
            {"bold": True, "font_color": "#FFFFFF", "bg_color": "#111827", "border": 0, "valign": "vcenter"}
        )
        wrap_format = workbook.add_format({"text_wrap": True, "valign": "top"})
        bet_format = workbook.add_format({"bg_color": "#DCFCE7", "font_color": "#166534", "bold": True})
        pass_format = workbook.add_format({"bg_color": "#FEE2E2", "font_color": "#991B1B", "bold": True})
        sheets = {"Export Info": export_info, **datasets}
        for sheet_name, frame in sheets.items():
            export_frame = frame.copy()
            if export_frame.empty:
                export_frame = pd.DataFrame({"Status": ["No records for the selected date range"]})
            for date_column in {"game_date", "bet_date", "start_date", "end_date"}.intersection(export_frame.columns):
                export_frame[date_column] = pd.to_datetime(export_frame[date_column], errors="coerce").dt.date
            export_frame.to_excel(writer, sheet_name=sheet_name[:31], index=False)
            worksheet = writer.sheets[sheet_name[:31]]
            worksheet.hide_gridlines(2)
            worksheet.freeze_panes(1, 0)
            worksheet.set_row(0, 24, header_format)
            for column_index, column in enumerate(export_frame.columns):
                worksheet.write(0, column_index, str(column).replace("_", " ").title(), header_format)
            worksheet.autofilter(0, 0, len(export_frame), len(export_frame.columns) - 1)
            for column_index, column in enumerate(export_frame.columns):
                sample = export_frame[column].head(250).fillna("").astype(str)
                width = min(52, max(11, len(str(column)) + 2, max((len(value) for value in sample), default=0) + 2))
                cell_format = wrap_format if width >= 40 else None
                worksheet.set_column(column_index, column_index, width, cell_format)
            for recommendation_column in ("recommendation", "recommendation_result"):
                if recommendation_column in export_frame.columns:
                    column_index = export_frame.columns.get_loc(recommendation_column)
                    worksheet.conditional_format(1, column_index, len(export_frame), column_index, {
                        "type": "text", "criteria": "containing", "value": "BET", "format": bet_format,
                    })
                    worksheet.conditional_format(1, column_index, len(export_frame), column_index, {
                        "type": "text", "criteria": "containing", "value": "PASS", "format": pass_format,
                    })
    return output.getvalue(), counts


def odds_history_dataframe(away_name: str = "", home_name: str = "") -> pd.DataFrame:
    query = """
        SELECT event_id, commence_time, away_team, home_team, bookmaker, market, outcomes_json, captured_at
        FROM live_odds_snapshots
        WHERE market = 'h2h'
    """
    params: tuple = ()
    if away_name and home_name:
        query += " AND away_team = ? AND home_team = ?"
        params = (away_name, home_name)
    query += " ORDER BY captured_at, bookmaker"
    snapshots = load_dataframe(query, params)
    rows = []
    for snapshot in snapshots.to_dict("records"):
        try:
            outcomes = json.loads(snapshot["outcomes_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        for outcome in outcomes:
            if "price" not in outcome:
                continue
            rows.append(
                {
                    "event_id": snapshot["event_id"],
                    "commence_time": snapshot["commence_time"],
                    "away_team": snapshot["away_team"],
                    "home_team": snapshot["home_team"],
                    "bookmaker": snapshot["bookmaker"],
                    "team": outcome.get("name", ""),
                    "price": int(outcome["price"]),
                    "captured_at": snapshot["captured_at"],
                }
            )
    return pd.DataFrame(rows)


def line_movement_summary(
    away_name: str, home_name: str, bookmaker_preference: str, stale_minutes: int = 20
) -> pd.DataFrame:
    history = odds_history_dataframe(away_name, home_name)
    if history.empty:
        return pd.DataFrame()
    history["captured_at"] = pd.to_datetime(history["captured_at"], utc=True, errors="coerce")
    history = history.dropna(subset=["captured_at"]).sort_values("captured_at")
    rows = []
    now_utc = pd.Timestamp.now(tz="UTC")
    for team, team_history in history.groupby("team"):
        preferred = team_history[
            team_history["bookmaker"].str.lower() == bookmaker_preference.lower()
        ]
        selected = preferred if not preferred.empty else team_history
        opening = selected.iloc[0]
        current = selected.iloc[-1]
        latest_by_book = team_history.sort_values("captured_at").groupby("bookmaker", as_index=False).tail(1)
        best = latest_by_book.sort_values("price", ascending=False).iloc[0]
        age_minutes = max(0.0, (now_utc - current["captured_at"]).total_seconds() / 60)
        rows.append(
            {
                "team": team,
                "opening_line": int(opening["price"]),
                "current_line": int(current["price"]),
                "line_movement": int(current["price"] - opening["price"]),
                "current_book": current["bookmaker"],
                "best_line": int(best["price"]),
                "best_book": best["bookmaker"],
                "last_update": current["captured_at"],
                "age_minutes": round(age_minutes, 1),
                "stale": age_minutes > stale_minutes,
            }
        )
    return pd.DataFrame(rows)


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


def import_retrosheet_backtest(season: int) -> dict[str, object]:
    raw_games = get_season_gameinfo(season)
    games = []
    for row in raw_games:
        away_team = RETROSHEET_TEAM_MAP.get(str(row.get("visteam", "")))
        home_team = RETROSHEET_TEAM_MAP.get(str(row.get("hometeam", "")))
        if not away_team or not home_team:
            continue
        try:
            away_score = int(row.get("vruns", ""))
            home_score = int(row.get("hruns", ""))
        except (TypeError, ValueError):
            continue
        if away_score == home_score:
            continue
        raw_date = str(row.get("date", ""))
        if len(raw_date) != 8:
            continue
        game_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
        games.append(
            {
                "retro_game_id": str(row.get("gid", "")),
                "season": season,
                "game_date": game_date,
                "away_team": away_team,
                "home_team": home_team,
                "away_score": away_score,
                "home_score": home_score,
                "winner": home_team if home_score > away_score else away_team,
                "game_type": str(row.get("gametype", "")),
            }
        )

    games.sort(key=lambda item: (item["game_date"], item["retro_game_id"]))
    team_state: dict[str, dict[str, float]] = {}
    predictions = []
    for game in games:
        away = team_state.setdefault(game["away_team"], {"games": 0, "wins": 0, "runs_for": 0, "runs_against": 0})
        home = team_state.setdefault(game["home_team"], {"games": 0, "wins": 0, "runs_for": 0, "runs_against": 0})
        away_win_pct = (away["wins"] + 5) / (away["games"] + 10)
        home_win_pct = (home["wins"] + 5) / (home["games"] + 10)
        away_run_diff = (away["runs_for"] - away["runs_against"]) / max(1, away["games"])
        home_run_diff = (home["runs_for"] - home["runs_against"]) / max(1, home["games"])
        logit = math.log(0.54 / 0.46) + 1.35 * (home_win_pct - away_win_pct) + 0.018 * (home_run_diff - away_run_diff)
        home_probability = max(0.30, min(0.70, 1 / (1 + math.exp(-logit))))
        predicted_side = game["home_team"] if home_probability >= 0.5 else game["away_team"]
        actual_home_win = 1.0 if game["winner"] == game["home_team"] else 0.0
        predictions.append(
            {
                **game,
                "predicted_home_probability": home_probability,
                "predicted_side": predicted_side,
                "confidence": round(min(10.0, 5.0 + abs(home_probability - 0.5) * 20), 1),
                "correct": predicted_side == game["winner"],
                "brier_score": (home_probability - actual_home_win) ** 2,
            }
        )
        away["games"] += 1
        home["games"] += 1
        away["wins"] += int(game["winner"] == game["away_team"])
        home["wins"] += int(game["winner"] == game["home_team"])
        away["runs_for"] += game["away_score"]
        away["runs_against"] += game["home_score"]
        home["runs_for"] += game["home_score"]
        home["runs_against"] += game["away_score"]

    with connect() as conn:
        conn.execute(
            "DELETE FROM retrosheet_backtests WHERE season = ? AND model_version = ?",
            (season, RETROSHEET_MODEL_VERSION),
        )
        for game in games:
            conn.execute(
                """
                INSERT INTO retrosheet_games (
                    retro_game_id, season, game_date, away_team, home_team,
                    away_score, home_score, winner, game_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(retro_game_id) DO UPDATE SET
                    season = excluded.season, game_date = excluded.game_date,
                    away_team = excluded.away_team, home_team = excluded.home_team,
                    away_score = excluded.away_score, home_score = excluded.home_score,
                    winner = excluded.winner, game_type = excluded.game_type
                """,
                (
                    game["retro_game_id"], game["season"], game["game_date"], game["away_team"],
                    game["home_team"], game["away_score"], game["home_score"], game["winner"], game["game_type"],
                ),
            )
        for prediction in predictions:
            conn.execute(
                """
                INSERT INTO retrosheet_backtests (
                    retro_game_id, season, model_version, predicted_home_probability,
                    predicted_side, confidence, actual_winner, correct, brier_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prediction["retro_game_id"], season, RETROSHEET_MODEL_VERSION,
                    prediction["predicted_home_probability"], prediction["predicted_side"],
                    prediction["confidence"], prediction["winner"], prediction["correct"],
                    prediction["brier_score"],
                ),
            )
        conn.commit()
    load_dataframe.clear()
    accuracy = sum(int(row["correct"]) for row in predictions) / len(predictions) if predictions else 0.0
    brier = sum(float(row["brier_score"]) for row in predictions) / len(predictions) if predictions else 0.0
    return {"season": season, "games": len(predictions), "accuracy": accuracy, "brier_score": brier}


def live_model_validation() -> pd.DataFrame:
    history = load_dataframe(
        """
        SELECT mph.*, g.first_pitch_utc, g.status, g.status_state,
               g.away_score, g.home_score,
               COALESCE(g.away_team_name, g.away_team) AS away_name,
               COALESCE(g.home_team_name, g.home_team) AS home_name
        FROM model_prediction_history mph
        JOIN games g ON g.game_id = mph.game_id
        WHERE mph.model_probability > 0
        ORDER BY mph.captured_at
        """
    )
    if history.empty:
        return history
    history["captured_at"] = pd.to_datetime(history["captured_at"], utc=True, errors="coerce")
    history["first_pitch_utc"] = pd.to_datetime(history["first_pitch_utc"], utc=True, errors="coerce")
    pregame = history[
        history["first_pitch_utc"].isna() | (history["captured_at"] <= history["first_pitch_utc"])
    ].copy()
    pregame = pregame.sort_values("captured_at").groupby("game_id", as_index=False).tail(1)
    completed = pregame[
        pregame.apply(lambda row: game_status_bucket(row["status"], row["status_state"]) == "done", axis=1)
        & pregame["away_score"].notna()
        & pregame["home_score"].notna()
    ].copy()
    if completed.empty:
        return completed
    completed["actual_winner"] = completed.apply(
        lambda row: row["away_name"] if int(row["away_score"]) > int(row["home_score"]) else row["home_name"], axis=1
    )
    completed["correct"] = completed["recommended_side"] == completed["actual_winner"]
    completed["outcome"] = completed["correct"].astype(int)
    completed["brier_score"] = (completed["model_probability"] - completed["outcome"]) ** 2
    return completed


def calibration_table(df: pd.DataFrame, probability_column: str, outcome_column: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    calibration = df[[probability_column, outcome_column]].copy()
    calibration["probability_bucket"] = pd.cut(
        calibration[probability_column], bins=[0, 0.4, 0.45, 0.5, 0.55, 0.6, 1.0], include_lowest=True
    ).astype(str)
    return calibration.groupby("probability_bucket", as_index=False, observed=True).agg(
        predictions=(outcome_column, "size"),
        average_probability=(probability_column, "mean"),
        actual_win_rate=(outcome_column, "mean"),
    )


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
            away_name = away_team.get("name", "")
            home_name = home_team.get("name", "")
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
                    "away_team": away_team.get("abbreviation") or TEAM_NAME_TO_ABBR.get(away_name, away_name),
                    "home_team": home_team.get("abbreviation") or TEAM_NAME_TO_ABBR.get(home_name, home_name),
                    "away_team_id": away_team.get("id"),
                    "home_team_id": home_team.get("id"),
                    "away_name": away_name,
                    "home_name": home_name,
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
        for game in games:
            existing = conn.execute(
                """
                SELECT away_probable_pitcher, home_probable_pitcher
                FROM games WHERE game_id = ?
                """,
                (game["game_id"],),
            ).fetchone()
            change_details = []
            if existing:
                old_away = str(existing["away_probable_pitcher"] or "")
                old_home = str(existing["home_probable_pitcher"] or "")
                new_away = str(game["away_probable_pitcher"] or "")
                new_home = str(game["home_probable_pitcher"] or "")
                if old_away not in {"", "TBD"} and new_away not in {"", "TBD"} and old_away != new_away:
                    change_details.append(f"Away starter changed: {old_away} to {new_away}")
                if old_home not in {"", "TBD"} and new_home not in {"", "TBD"} and old_home != new_home:
                    change_details.append(f"Home starter changed: {old_home} to {new_home}")
            conn.execute(
                """
                INSERT INTO games (
                    game_id, game_date, away_team, home_team, away_team_id, home_team_id,
                    away_team_name, home_team_name, venue_name, venue_lat, venue_lon,
                    away_probable_pitcher, home_probable_pitcher, status, status_state,
                    status_code, away_score, home_score, inning_state, current_inning,
                    score_summary, box_score_summary, first_pitch_utc,
                    pitcher_change_detected, pitcher_change_details, context_updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id) DO UPDATE SET
                    game_date = excluded.game_date,
                    away_team = excluded.away_team,
                    home_team = excluded.home_team,
                    away_team_id = excluded.away_team_id,
                    home_team_id = excluded.home_team_id,
                    away_team_name = excluded.away_team_name,
                    home_team_name = excluded.home_team_name,
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
                    first_pitch_utc = excluded.first_pitch_utc,
                    pitcher_change_detected = excluded.pitcher_change_detected,
                    pitcher_change_details = excluded.pitcher_change_details,
                    context_updated_at = excluded.context_updated_at
                """,
                (
                    game["game_id"],
                    game["game_date"],
                    game["away_team"],
                    game["home_team"],
                    game.get("away_team_id"),
                    game.get("home_team_id"),
                    game.get("away_name", game["away_team"]),
                    game.get("home_name", game["home_team"]),
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
                    bool(change_details),
                    "; ".join(change_details),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            recommendation_exists = conn.execute(
                "SELECT COUNT(*) AS count FROM model_recommendations WHERE game_id = ? AND run_date = ?",
                (game["game_id"], date_str),
            ).fetchone()[0]
            if not recommendation_exists:
                conn.execute(
                    """
                    INSERT INTO model_recommendations (
                        game_id, run_date, recommended_side, best_market, fair_line,
                        market_line, edge_pct, confidence, pitching_score, bullpen_score,
                        offense_score, lineup_score, weather_score, situation_score,
                        total_score, recommendation, reason, model_version, model_probability,
                        rule_profile, rule_checks_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        game["game_id"], date_str, "TBD", "Needs scoring", 0, 0, 0.0, 0.0,
                        0, 0, 0, 0, 0, 0, 0, "PASS",
                        "Live MLB schedule loaded. Refresh model inputs for scoring.",
                        MODEL_VERSION, 0.0, "Pending", "{}",
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
        for event in odds_events:
            for bookmaker in event.get("bookmakers", []):
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


def weather_risk_level(precipitation_probability: object, game_status: str = "") -> str:
    status_text = str(game_status or "").lower()
    if any(token in status_text for token in ["postponed", "cancelled", "canceled", "suspended"]):
        return "High"
    if "delayed" in status_text:
        return "High"
    try:
        precipitation = float(precipitation_probability)
    except (TypeError, ValueError):
        return "Unknown"
    if precipitation >= 55:
        return "High"
    if precipitation >= 35:
        return "Moderate"
    return "Low"


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
                conn.execute(
                    "UPDATE games SET weather_risk_level = ?, context_updated_at = ? WHERE game_id = ?",
                    (
                        weather_risk_level(selected["precipitation_probability"], game.get("status", "")),
                        datetime.now(timezone.utc).isoformat(),
                        game["game_id"],
                    ),
                )
                weather_rows += 1
            except Exception as exc:
                errors.append(f"Open-Meteo refresh failed for {game['away_team']} @ {game['home_team']}: {exc}")
        conn.commit()
    load_dataframe.clear()
    return weather_rows, errors


def parse_confirmed_lineup(team_boxscore: dict) -> tuple[str, list[str]]:
    batting_order = [int(player_id) for player_id in team_boxscore.get("battingOrder", [])]
    players = team_boxscore.get("players", {})
    names = []
    for player_id in batting_order:
        player = players.get(f"ID{player_id}", {})
        name = player.get("person", {}).get("fullName")
        if name:
            names.append(name)
    if len(names) >= 9:
        return "Confirmed", names[:9]
    if names:
        return "Partial", names
    return "Pending", []


def parse_injury_roster(roster: dict) -> dict[str, object]:
    injuries = []
    injured_hitters = 0
    injured_pitchers = 0
    for entry in roster.get("roster", []):
        status = entry.get("status", {})
        description = str(status.get("description", ""))
        if "injured" not in description.lower():
            continue
        position_type = str(entry.get("position", {}).get("type", ""))
        if position_type == "Pitcher":
            injured_pitchers += 1
        else:
            injured_hitters += 1
        injuries.append(
            {
                "player": entry.get("person", {}).get("fullName", "Unknown"),
                "position": entry.get("position", {}).get("abbreviation", ""),
                "status": description,
                "note": entry.get("note", ""),
            }
        )
    return {
        "injuries": injuries,
        "injured_count": len(injuries),
        "injured_hitters": injured_hitters,
        "injured_pitchers": injured_pitchers,
    }


def bullpen_fatigue_status(pitches_1d: int, pitches_3d: int) -> str:
    if pitches_1d >= 75 or pitches_3d >= 180:
        return "Heavy"
    if pitches_1d >= 45 or pitches_3d >= 135:
        return "Moderate"
    return "Fresh"


def bullpen_pitches_from_boxscore(team_boxscore: dict) -> int:
    pitcher_ids = team_boxscore.get("pitchers", [])
    if len(pitcher_ids) <= 1:
        return 0
    players = team_boxscore.get("players", {})
    total = 0
    for player_id in pitcher_ids[1:]:
        pitching = players.get(f"ID{player_id}", {}).get("stats", {}).get("pitching", {})
        pitches = pitching.get("pitchesThrown", pitching.get("numberOfPitches", 0))
        try:
            total += int(pitches or 0)
        except (TypeError, ValueError):
            continue
    return total


def refresh_mlb_game_context(
    games: list[dict], date_str: str, api_marker: str = ""
) -> tuple[int, int, int, list[str]]:
    errors: list[str] = []
    lineup_games = 0
    availability_teams = 0
    bullpen_teams = 0
    availability: dict[int, dict[str, object]] = {}
    team_names: dict[int, str] = {}
    boxscore_cache: dict[str, dict] = {}

    for game in games:
        for side in ("away", "home"):
            team_id = game.get(f"{side}_team_id")
            if team_id:
                team_names[int(team_id)] = str(game.get(f"{side}_name") or game.get(f"{side}_team") or team_id)

    for team_id, team_name in team_names.items():
        try:
            parsed = parse_injury_roster(get_team_roster(team_id, date_str, api_marker=api_marker))
            availability[team_id] = parsed
            availability_teams += 1
        except Exception as exc:
            availability[team_id] = {"injuries": [], "injured_count": 0, "injured_hitters": 0, "injured_pitchers": 0}
            errors.append(f"MLB injury status unavailable for {team_name}: {exc}")

    current_day = date.fromisoformat(date_str)
    workload: dict[int, dict[str, int]] = {team_id: {"pitches_1d": 0, "pitches_3d": 0} for team_id in team_names}
    try:
        recent_schedule = get_schedule_range(
            (current_day - timedelta(days=3)).isoformat(), date_str, api_marker=api_marker
        )
        for date_block in recent_schedule.get("dates", []):
            game_day = date.fromisoformat(date_block.get("date"))
            days_ago = (current_day - game_day).days
            for recent_game in date_block.get("games", []):
                state = str(recent_game.get("status", {}).get("abstractGameState", "")).lower()
                if state not in {"final", "live"}:
                    continue
                game_pk = str(recent_game.get("gamePk"))
                try:
                    boxscore = boxscore_cache.get(game_pk) or get_game_boxscore(game_pk, api_marker=api_marker)
                    boxscore_cache[game_pk] = boxscore
                except Exception as exc:
                    errors.append(f"Bullpen workload unavailable for game {game_pk}: {exc}")
                    continue
                for side in ("away", "home"):
                    team_id = recent_game.get("teams", {}).get(side, {}).get("team", {}).get("id")
                    if team_id not in workload:
                        continue
                    pitches = bullpen_pitches_from_boxscore(boxscore.get("teams", {}).get(side, {}))
                    workload[team_id]["pitches_3d"] += pitches
                    if days_ago <= 1:
                        workload[team_id]["pitches_1d"] += pitches
    except Exception as exc:
        errors.append(f"MLB recent schedule unavailable for bullpen workload: {exc}")

    with connect() as conn:
        for team_id, parsed in availability.items():
            conn.execute(
                """
                INSERT INTO team_availability_snapshots (
                    team_id, team_name, injured_count, injured_hitters, injured_pitchers, injuries_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    team_id,
                    team_names.get(team_id, str(team_id)),
                    parsed["injured_count"],
                    parsed["injured_hitters"],
                    parsed["injured_pitchers"],
                    json.dumps(parsed["injuries"]),
                ),
            )

        for game in games:
            game_pk = str(game["game_id"])
            try:
                boxscore = boxscore_cache.get(game_pk) or get_game_boxscore(game_pk, api_marker=api_marker)
                boxscore_cache[game_pk] = boxscore
                away_lineup_status, away_lineup = parse_confirmed_lineup(boxscore.get("teams", {}).get("away", {}))
                home_lineup_status, home_lineup = parse_confirmed_lineup(boxscore.get("teams", {}).get("home", {}))
                if away_lineup or home_lineup:
                    lineup_games += 1
            except Exception:
                away_lineup_status, home_lineup_status = "Pending", "Pending"
                away_lineup, home_lineup = [], []

            away_id = int(game["away_team_id"]) if game.get("away_team_id") else 0
            home_id = int(game["home_team_id"]) if game.get("home_team_id") else 0
            away_availability = availability.get(away_id, {"injuries": [], "injured_count": 0})
            home_availability = availability.get(home_id, {"injuries": [], "injured_count": 0})
            away_workload = workload.get(away_id, {"pitches_1d": 0, "pitches_3d": 0})
            home_workload = workload.get(home_id, {"pitches_1d": 0, "pitches_3d": 0})
            away_bullpen = bullpen_fatigue_status(away_workload["pitches_1d"], away_workload["pitches_3d"])
            home_bullpen = bullpen_fatigue_status(home_workload["pitches_1d"], home_workload["pitches_3d"])
            bullpen_teams += int(away_id > 0) + int(home_id > 0)
            conn.execute(
                """
                UPDATE games SET
                    away_lineup_status = ?, home_lineup_status = ?,
                    away_lineup_json = ?, home_lineup_json = ?,
                    away_injury_count = ?, home_injury_count = ?,
                    away_injured_hitters = ?, home_injured_hitters = ?,
                    away_injuries_json = ?, home_injuries_json = ?,
                    away_bullpen_pitches_1d = ?, home_bullpen_pitches_1d = ?,
                    away_bullpen_pitches_3d = ?, home_bullpen_pitches_3d = ?,
                    away_bullpen_status = ?, home_bullpen_status = ?, context_updated_at = ?
                WHERE game_id = ?
                """,
                (
                    away_lineup_status, home_lineup_status, json.dumps(away_lineup), json.dumps(home_lineup),
                    away_availability["injured_count"], home_availability["injured_count"],
                    away_availability.get("injured_hitters", 0), home_availability.get("injured_hitters", 0),
                    json.dumps(away_availability["injuries"]), json.dumps(home_availability["injuries"]),
                    away_workload["pitches_1d"], home_workload["pitches_1d"],
                    away_workload["pitches_3d"], home_workload["pitches_3d"],
                    away_bullpen, home_bullpen, datetime.now(timezone.utc).isoformat(), game_pk,
                ),
            )
            conn.execute(
                """
                INSERT INTO game_context_snapshots (
                    game_id, away_lineup_status, home_lineup_status,
                    away_lineup_json, home_lineup_json,
                    away_injury_count, home_injury_count,
                    away_injured_hitters, home_injured_hitters,
                    away_injuries_json, home_injuries_json,
                    away_bullpen_pitches_1d, home_bullpen_pitches_1d,
                    away_bullpen_pitches_3d, home_bullpen_pitches_3d,
                    away_bullpen_status, home_bullpen_status,
                    pitcher_change_detected, pitcher_change_details, weather_risk_level
                )
                SELECT game_id, away_lineup_status, home_lineup_status,
                       away_lineup_json, home_lineup_json,
                       away_injury_count, home_injury_count,
                       away_injured_hitters, home_injured_hitters,
                       away_injuries_json, home_injuries_json,
                       away_bullpen_pitches_1d, home_bullpen_pitches_1d,
                       away_bullpen_pitches_3d, home_bullpen_pitches_3d,
                       away_bullpen_status, home_bullpen_status,
                       pitcher_change_detected, pitcher_change_details, weather_risk_level
                FROM games WHERE game_id = ?
                """,
                (game_pk,),
            )
        conn.commit()
    load_dataframe.clear()
    return lineup_games, availability_teams, bullpen_teams, errors


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


def latest_h2h_market_line(game: dict, team_name: str, bookmaker_preference: str = "") -> int:
    odds_df = load_dataframe(
        """
        SELECT bookmaker, outcomes_json, captured_at
        FROM live_odds_snapshots
        WHERE market = 'h2h'
          AND away_team = ?
          AND home_team = ?
        ORDER BY captured_at DESC
        LIMIT 100
        """,
        (game["away_name"], game["home_name"]),
    )
    if odds_df.empty:
        odds_df = load_dataframe(
            """
            SELECT bookmaker, outcomes_json, captured_at
            FROM live_odds_snapshots
            WHERE market = 'h2h'
              AND away_team = ?
              AND home_team = ?
            ORDER BY captured_at DESC
            LIMIT 100
            """,
            (game["away_team"], game["home_team"]),
        )
    if odds_df.empty:
        return 0
    if bookmaker_preference:
        preferred = odds_df[odds_df["bookmaker"].str.lower() == bookmaker_preference.lower()]
        if not preferred.empty:
            odds_df = preferred
    for _, snapshot in odds_df.iterrows():
        outcomes = json.loads(snapshot["outcomes_json"])
        for outcome in outcomes:
            if outcome.get("name") == team_name:
                return int(outcome.get("price", 0))
    return 0


def confluence_probability(score_diff: float) -> float:
    return max(0.35, min(0.65, 0.5 + score_diff / 100))


def load_games_for_scoring(date_str: str) -> list[dict]:
    games = load_dataframe(
        """
        SELECT game_id, game_date, away_team, home_team,
               COALESCE(away_team_name, away_team) AS away_name,
               COALESCE(home_team_name, home_team) AS home_name,
               away_probable_pitcher, home_probable_pitcher, first_pitch_utc,
               away_lineup_status, home_lineup_status,
               away_injury_count, home_injury_count,
               away_injured_hitters, home_injured_hitters,
               away_bullpen_pitches_1d, home_bullpen_pitches_1d,
               away_bullpen_pitches_3d, home_bullpen_pitches_3d,
               away_bullpen_status, home_bullpen_status,
               pitcher_change_detected, pitcher_change_details, weather_risk_level
        FROM games WHERE game_date = ?
        """,
        (date_str,),
    )
    return games.to_dict("records")


def is_confirmed_starter(value: object) -> bool:
    return str(value or "").strip().upper() not in {"", "TBD", "TBA", "NONE", "NAN"}


def hours_until_first_pitch(value: object) -> float:
    first_pitch = parse_utc_datetime(str(value or ""))
    if first_pitch is None:
        return 99.0
    return (first_pitch - datetime.now(timezone.utc)).total_seconds() / 3600


def evaluate_rule_gates(
    game: dict,
    profile_name: str,
    recommended_team: str,
    edge: float,
    line_diff: int,
    confidence: float,
    edge_threshold: float,
    min_line_diff: int,
    min_confidence: float,
    pitching_edge: float,
    offense_edge: float,
    line_movement: int | None,
    odds_stale: bool,
) -> dict[str, object]:
    away_name = str(game.get("away_name") or game.get("away_team"))
    recommended_is_away = recommended_team == away_name
    recommended_bullpen = str(game.get("away_bullpen_status") if recommended_is_away else game.get("home_bullpen_status"))
    opponent_bullpen = str(game.get("home_bullpen_status") if recommended_is_away else game.get("away_bullpen_status"))
    recommended_injuries = int(
        (game.get("away_injured_hitters") if recommended_is_away else game.get("home_injured_hitters")) or 0
    )
    bullpen_rank = {"Fresh": 0, "Moderate": 1, "Heavy": 2, "Unknown": 1}
    bullpen_edge = bullpen_rank.get(recommended_bullpen, 1) < bullpen_rank.get(opponent_bullpen, 1)
    pitching_positive = pitching_edge > 0 if recommended_is_away else pitching_edge < 0
    offense_positive = offense_edge > 0 if recommended_is_away else offense_edge < 0
    starters_confirmed = is_confirmed_starter(game.get("away_probable_pitcher")) and is_confirmed_starter(
        game.get("home_probable_pitcher")
    )
    lineups_confirmed = game.get("away_lineup_status") == "Confirmed" and game.get("home_lineup_status") == "Confirmed"
    hours_to_game = hours_until_first_pitch(game.get("first_pitch_utc"))
    lineup_due = hours_to_game <= (1.0 if profile_name == "Moderate" else 2.5)
    weather_risk = str(game.get("weather_risk_level") or "Unknown")
    pitcher_changed = bool(game.get("pitcher_change_detected"))

    checks = [
        {"rule": "Market line available", "passed": bool(game and line_diff >= 0 and edge != 0), "value": line_diff},
        {"rule": "Minimum edge", "passed": edge >= edge_threshold, "value": round(edge, 2), "required": edge_threshold},
        {"rule": "Fair-line difference", "passed": line_diff >= min_line_diff, "value": line_diff, "required": min_line_diff},
        {"rule": "Minimum confidence", "passed": confidence >= min_confidence, "value": confidence, "required": min_confidence},
        {"rule": "Odds freshness", "passed": not odds_stale, "value": "Fresh" if not odds_stale else "Stale"},
    ]

    def add(rule: str, passed: bool, value: object, required: object = "") -> None:
        checks.append({"rule": rule, "passed": bool(passed), "value": value, "required": required})

    if profile_name == "Very Conservative":
        add("Starting pitchers confirmed", starters_confirmed, starters_confirmed, True)
        add("Lineups confirmed when due", lineups_confirmed or not lineup_due, f"{game.get('away_lineup_status') or 'Pending'}/{game.get('home_lineup_status') or 'Pending'}")
        add("Positive pitching edge", pitching_positive, round(pitching_edge, 1), "> 0 for selected side")
        add("Bullpen not heavily taxed", recommended_bullpen != "Heavy", recommended_bullpen)
        add("Low weather risk", weather_risk in {"Low", "Unknown"}, weather_risk)
        add("No pitcher change", not pitcher_changed, game.get("pitcher_change_details") or "Stable")
        add("No adverse line move", line_movement is None or line_movement > -15, line_movement if line_movement is not None else "New")
        add("No major unresolved injury cluster", lineups_confirmed or recommended_injuries < 3, recommended_injuries, "< 3 if lineup pending")
    elif profile_name in {"Conservative", "Manual Settings"}:
        add("Starting pitchers confirmed", starters_confirmed, starters_confirmed, True)
        add("Lineups confirmed when due", lineups_confirmed or not lineup_due, f"{game.get('away_lineup_status') or 'Pending'}/{game.get('home_lineup_status') or 'Pending'}")
        add("Bullpen not heavily taxed", recommended_bullpen != "Heavy", recommended_bullpen)
        add("No high weather risk", weather_risk != "High", weather_risk)
        add("No pitcher change", not pitcher_changed, game.get("pitcher_change_details") or "Stable")
        add("No major unresolved injury cluster", lineups_confirmed or recommended_injuries < 3, recommended_injuries, "< 3 if lineup pending")
        add("No severe adverse line move", line_movement is None or line_movement > -25, line_movement if line_movement is not None else "New")
    elif profile_name == "Moderate":
        add("Starting pitchers confirmed", starters_confirmed, starters_confirmed, True)
        add("Pitching or bullpen edge", pitching_positive or bullpen_edge, f"Pitching {pitching_positive}; bullpen {bullpen_edge}")
        add("Lineup uncertainty limited", lineups_confirmed or not lineup_due or recommended_injuries < 3, recommended_injuries)
        add("No high weather risk", weather_risk != "High", weather_risk)
        add("No pitcher change", not pitcher_changed, game.get("pitcher_change_details") or "Stable")
    else:
        add("No high weather risk", weather_risk != "High", weather_risk)
        add("No pitcher change", not pitcher_changed, game.get("pitcher_change_details") or "Stable")

    blockers = [str(check["rule"]) for check in checks if not check["passed"]]
    return {
        "passed": not blockers,
        "checks": checks,
        "blockers": blockers,
        "context": {
            "hours_to_game": round(hours_to_game, 1),
            "recommended_bullpen": recommended_bullpen,
            "opponent_bullpen": opponent_bullpen,
            "weather_risk": weather_risk,
            "line_movement": line_movement,
            "odds_stale": odds_stale,
            "pitching_positive": pitching_positive,
            "offense_positive": offense_positive,
            "bullpen_edge": bullpen_edge,
        },
    }


def score_games_from_statcast(date_str: str, games: list[dict], settings: dict[str, str]) -> int:
    metrics = latest_team_metrics()
    scored = 0
    edge_threshold = float(settings["edge_threshold"])
    min_line_diff = int(float(settings["min_line_difference_cents"]))
    min_confidence = float(settings["min_confidence"])
    max_bets_per_day = int(float(settings.get("max_bets_per_day", len(games) or 99)))
    profile_name = settings.get("rule_profile", "Manual Settings")
    stale_minutes = int(float(settings.get("odds_stale_minutes", 20)))
    bookmaker_preference = settings.get("bookmaker_preference", "")
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
                    "model_probability": 0.0,
                    "bullpen_score": 0,
                    "lineup_score": 0,
                    "weather_score": 0,
                    "situation_score": 0,
                    "rule_checks": {"passed": False, "checks": [], "blockers": ["Statcast team metrics unavailable"]},
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
        market_line = latest_h2h_market_line(game, recommended_team, bookmaker_preference)
        edge = calculate_edge_pct(model_prob, market_line) if market_line else 0.0
        line_diff = abs(fair_line - market_line) if market_line else 0
        confidence = round(min(10.0, 5.0 + abs(diff) / 5), 1)
        if profile_name == "Aggressive":
            if not is_confirmed_starter(game.get("away_probable_pitcher")) or not is_confirmed_starter(game.get("home_probable_pitcher")):
                confidence = max(0.0, confidence - 1.0)
            if game.get("away_lineup_status") != "Confirmed" or game.get("home_lineup_status") != "Confirmed":
                confidence = max(0.0, confidence - 0.5)
            if str(game.get("weather_risk_level")) == "Moderate":
                confidence = max(0.0, confidence - 0.5)
        movement = line_movement_summary(
            game["away_name"], game["home_name"], bookmaker_preference, stale_minutes
        )
        team_movement = movement[movement["team"] == recommended_team] if not movement.empty else pd.DataFrame()
        line_move = int(team_movement.iloc[0]["line_movement"]) if not team_movement.empty else None
        odds_stale = bool(team_movement.iloc[0]["stale"]) if not team_movement.empty else False
        pitching_edge = round(float(away_metrics["pitching_score"]) - float(home_metrics["pitching_score"]), 1)
        offense_edge = round(float(away_metrics["offense_score"]) - float(home_metrics["offense_score"]), 1)
        rule_checks = evaluate_rule_gates(
            game, profile_name, recommended_team, edge, line_diff, confidence,
            edge_threshold, min_line_diff, min_confidence, pitching_edge, offense_edge,
            line_move, odds_stale,
        )
        candidate_bet = bool(market_line and rule_checks["passed"])
        recommended_is_away = recommended_team == away_name
        recommended_bullpen = game.get("away_bullpen_status") if recommended_is_away else game.get("home_bullpen_status")
        lineup_confirmed = game.get("away_lineup_status") == "Confirmed" and game.get("home_lineup_status") == "Confirmed"
        weather_risk = str(game.get("weather_risk_level") or "Unknown")
        reason = (
            f"{profile_name} rules. Statcast confluence: {away_name} {away_score:.1f} vs {home_name} {home_score:.1f}. "
            f"{recommended_team} projects at {model_prob:.1%}; market {market_line or 'n/a'}. "
            f"Context: lineups {game.get('away_lineup_status')}/{game.get('home_lineup_status')}, "
            f"selected bullpen {recommended_bullpen}, weather {weather_risk}."
        )
        if rule_checks["blockers"]:
            reason += " PASS blockers: " + ", ".join(rule_checks["blockers"]) + "."
        scored_rows.append(
            {
                "game_id": game["game_id"],
                "recommended_side": recommended_team,
                "best_market": "Moneyline",
                "fair_line": fair_line,
                "market_line": market_line,
                "edge": edge,
                "confidence": confidence,
                "model_probability": model_prob,
                "pitching_score": pitching_edge,
                "bullpen_score": 2 if recommended_bullpen == "Fresh" else (0 if recommended_bullpen == "Moderate" else -2),
                "offense_score": offense_edge,
                "lineup_score": 2 if lineup_confirmed else 0,
                "weather_score": 1 if weather_risk == "Low" else (0 if weather_risk in {"Moderate", "Unknown"} else -2),
                "situation_score": -2 if game.get("pitcher_change_detected") else 0,
                "total_score": round(diff, 1),
                "candidate_bet": candidate_bet,
                "rule_checks": rule_checks,
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
                    edge_pct = ?, confidence = ?, pitching_score = ?, bullpen_score = ?,
                    offense_score = ?, lineup_score = ?, weather_score = ?, situation_score = ?,
                    total_score = ?, recommendation = ?, reason = ?, model_version = ?,
                    model_probability = ?, rule_profile = ?, rule_checks_json = ?
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
                    row["bullpen_score"],
                    row["offense_score"],
                    row["lineup_score"],
                    row["weather_score"],
                    row["situation_score"],
                    row["total_score"],
                    recommendation,
                    reason,
                    MODEL_VERSION,
                    row["model_probability"],
                    profile_name,
                    json.dumps(row["rule_checks"]),
                    row["game_id"],
                    date_str,
                ),
            )
            conn.execute(
                """
                INSERT INTO model_prediction_history (
                    game_id, run_date, model_version, model_probability, recommended_side,
                    fair_line, market_line, edge_pct, confidence, recommendation,
                    rule_profile, rule_checks_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["game_id"], date_str, MODEL_VERSION, row["model_probability"],
                    row["recommended_side"], row["fair_line"], row["market_line"],
                    round(row["edge"], 2), row["confidence"], recommendation,
                    profile_name, json.dumps(row["rule_checks"]),
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
    lineup_games = 0
    availability_teams = 0
    bullpen_teams = 0
    statcast_teams = 0
    scored_games = 0
    errors = []
    if schedule_error:
        errors.append(schedule_error)
    if today_games:
        weather_loaded, weather_errors = refresh_weather_for_games(today_games)
        errors.extend(weather_errors)
        lineup_games, availability_teams, bullpen_teams, context_errors = refresh_mlb_game_context(
            today_games, refresh_date, api_marker=mlb_stats_marker
        )
        errors.extend(context_errors)
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
        scored_games = score_games_from_statcast(refresh_date, load_games_for_scoring(refresh_date), settings)

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
        "lineup_games": lineup_games,
        "availability_teams": availability_teams,
        "bullpen_teams": bullpen_teams,
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
    return score_games_from_statcast(run_date, load_games_for_scoring(run_date), settings)


def metric_row(slate_df: pd.DataFrame, bets_df: pd.DataFrame) -> None:
    bet_plays = slate_df[slate_df["recommendation"] == "BET"]
    total_stake = float(bets_df["stake_units"].sum()) if not bets_df.empty else 0.0
    total_units = float(bets_df["profit_loss_units"].sum()) if not bets_df.empty else 0.0
    roi = total_units / total_stake if total_stake else 0.0

    values = [
        ("Games today", str(len(slate_df)), "Current MLB slate", ""),
        ("Model bets", str(len(bet_plays)), "Qualified by active rules", "accent"),
        (
            "Average edge",
            f"{bet_plays['edge_pct'].mean():.1f}%" if len(bet_plays) else "0.0%",
            "Across qualified bets",
            "positive",
        ),
        (
            "Confidence",
            f"{bet_plays['confidence'].mean():.1f}/10" if len(bet_plays) else "0.0/10",
            "Average qualified rating",
            "",
        ),
        ("Tracked units", f"{total_units:+.2f}", f"ROI {roi:.1%}", "positive" if total_units >= 0 else "accent"),
    ]
    cards = "".join(
        f'<div class="edge-kpi {css_class}">'
        f'<div class="edge-kpi-label">{html.escape(label)}</div>'
        f'<div class="edge-kpi-value">{html.escape(value)}</div>'
        f'<div class="edge-kpi-note">{html.escape(note)}</div>'
        "</div>"
        for label, value, note, css_class in values
    )
    st.markdown(f'<div class="edge-kpi-grid">{cards}</div>', unsafe_allow_html=True)


def render_pick_card(row: pd.Series) -> None:
    recommendation = str(row.get("recommendation", "PASS"))
    bucket = str(row.get("game_status_bucket", "scheduled"))
    badge_class = "live" if bucket == "in_progress" else recommendation.lower()
    badge_label = "LIVE" if bucket == "in_progress" else recommendation
    recommended_side = str(row.get("recommended_side") or "No side selected")
    market = str(row.get("best_market") or "Market pending")
    edge = float(row.get("edge_pct") or 0)
    confidence = float(row.get("confidence") or 0)
    market_line = int(row.get("market_line") or 0)
    market_line_label = f"{market_line:+d}" if market_line else "Pending"
    st.markdown(
        f"""
        <div class="edge-pick-card">
            <div class="edge-pick-top">
                <span class="edge-badge {badge_class}">{html.escape(badge_label)}</span>
                <span class="edge-pick-time">{html.escape(str(row.get('first_pitch_et', 'Time pending')))}</span>
            </div>
            <div class="edge-matchup">{html.escape(str(row.get('matchup', 'Matchup pending')))}</div>
            <div class="edge-pick-side">Model lean: <strong>{html.escape(recommended_side)}</strong><br>{html.escape(market)}</div>
            <div class="edge-pick-stats">
                <div class="edge-pick-stat"><strong>{edge:.1f}%</strong><span>Edge</span></div>
                <div class="edge-pick-stat"><strong>{confidence:.1f}</strong><span>Confidence</span></div>
                <div class="edge-pick-stat"><strong>{html.escape(market_line_label)}</strong><span>Market</span></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def sidebar(settings: dict[str, str]) -> tuple[str, dict[str, str]]:
    active_settings = active_rule_settings(settings)
    logo_path = resolve_asset_path(settings.get("dashboard_logo_path", ""))
    if logo_path:
        st.sidebar.image(str(logo_path), width=218)
    else:
        st.sidebar.title(settings.get("dashboard_title", "MLB Edge Model"))
    st.sidebar.markdown('<div class="edge-sidebar-label">Navigation</div>', unsafe_allow_html=True)
    page_labels = {
        "Today": "Daily Slate",
        "Game Center": "Game Breakdown",
        "Bet Tracker": "Bet Tracker",
        "Performance": "Performance",
        "Model Guide": "Model Guide",
        "Data & Sync": "Data Health",
        "Settings": "Settings",
    }
    page = st.sidebar.radio(
        "Dashboard Pages",
        list(page_labels.keys()),
        label_visibility="collapsed",
    )
    st.sidebar.markdown('<div class="edge-sidebar-label">Active strategy</div>', unsafe_allow_html=True)
    st.sidebar.markdown(
        f"""
        <div class="edge-rule-card">
            <strong>{html.escape(active_settings['rule_profile'])}</strong>
            <span>Edge {float(active_settings['edge_threshold']):.1f}%+ | Confidence {float(active_settings['min_confidence']):.1f}+<br>
            Up to {active_settings['max_bets_per_day']} model bets per day</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.sidebar.markdown(
        '<div class="edge-sidebar-footer">Model output is informational. Set a budget, keep stakes consistent, and never chase losses.</div>',
        unsafe_allow_html=True,
    )
    return page_labels[page], active_settings


def slate_line_movement(slate_df: pd.DataFrame, settings: dict[str, str]) -> pd.DataFrame:
    frames = []
    stale_minutes = int(float(settings.get("odds_stale_minutes", 20)))
    for _, row in slate_df.iterrows():
        movement = line_movement_summary(
            str(row["away_team_name"]), str(row["home_team_name"]),
            settings.get("bookmaker_preference", ""), stale_minutes,
        )
        if movement.empty:
            continue
        movement.insert(0, "matchup", row["matchup"])
        frames.append(movement)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def json_list(value: object) -> list:
    try:
        payload = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def render_daily_slate(slate_df: pd.DataFrame, bets_df: pd.DataFrame, settings: dict[str, str]) -> None:
    slate_date = str(slate_df.iloc[0]["game_date"]) if not slate_df.empty else date.today().isoformat()
    render_page_header(
        "Today's MLB slate",
        "Daily dashboard",
        "Live game status, model recommendations, weather, and market value in one place.",
        f"{slate_date} | {DISPLAY_TIMEZONE_LABEL} | {settings['rule_profile']} rules",
    )
    metric_row(slate_df, bets_df)

    action_copy, action_button = st.columns([4, 1])
    with action_copy:
        scheduled_count = int((slate_df["game_status_bucket"] == "scheduled").sum())
        live_count = int((slate_df["game_status_bucket"] == "in_progress").sum())
        final_count = int((slate_df["game_status_bucket"] == "done").sum())
        st.caption(f"{scheduled_count} upcoming | {live_count} live | {final_count} final or postponed")
    refresh_clicked = action_button.button("Refresh data", type="primary", use_container_width=True)
    if refresh_clicked:
        with st.spinner("Updating schedule, odds, weather, and Statcast metrics..."):
            result = run_local_refresh(settings)
        if result["status"] == "success":
            st.success(
                f"Data updated: {result['games']} games, {result['odds_events']} odds events, "
                f"{result['weather_loaded']} forecasts, {result['lineup_games']} posted lineups, "
                f"{result['availability_teams']} injury rosters, and {result['scored_games']} scored games."
            )
        else:
            st.warning("Some sources did not finish updating. The most recent saved data is still shown.")
            for error in result["errors"]:
                st.write(f"- {error}")

    qualified = slate_df[slate_df["recommendation"] == "BET"]
    opportunity_df = qualified.head(3) if not qualified.empty else slate_df.head(3)
    heading_detail = "Qualified by the active rules" if not qualified.empty else "Strongest model reads; no bets currently qualify"
    render_section_heading("Top opportunities", heading_detail)
    pick_columns = st.columns(max(1, len(opportunity_df)))
    for column, (_, row) in zip(pick_columns, opportunity_df.iterrows()):
        with column:
            render_pick_card(row)

    render_section_heading("All matchups", "Use the tabs to move from decisions to supporting detail")
    filter_col, status_col, spacer = st.columns([1.1, 1.4, 2.5])
    show_bets_only = filter_col.toggle("Model bets only", value=False)
    available_statuses = ["Upcoming", "Live", "Final / delayed"]
    status_filter = status_col.multiselect("Game status", available_statuses, placeholder="All game statuses")
    display_df = slate_df[slate_df["recommendation"] == "BET"] if show_bets_only else slate_df.copy()
    status_map = {"Upcoming": "scheduled", "Live": "in_progress", "Final / delayed": "done"}
    if status_filter:
        display_df = display_df[display_df["game_status_bucket"].isin([status_map[item] for item in status_filter])]

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
        "rule_blockers",
    ]
    matchup_tab, movement_tab, context_tab, weather_tab, statcast_tab, charts_tab = st.tabs(
        ["Matchups", "Line movement", "Lineups & risk", "Weather", "Statcast", "Model charts"]
    )
    with matchup_tab:
        friendly_slate = display_df[slate_columns].rename(
            columns={
                "rank": "Rank",
                "first_pitch_et": "First pitch",
                "status": "Status",
                "score_summary": "Score",
                "matchup": "Matchup",
                "probable_starters": "Probable starters",
                "weather_summary": "Weather",
                "away_confluence_score": "Away rating",
                "home_confluence_score": "Home rating",
                "recommended_side": "Model side",
                "best_market": "Market",
                "fair_line": "Fair line",
                "market_line": "Book line",
                "edge_pct": "Edge %",
                "confidence": "Confidence",
                "recommendation": "Call",
                "recommendation_result": "Result",
                "rule_blockers": "Rule blockers",
            }
        )

    with movement_tab:
        movement = slate_line_movement(display_df, settings)
        if movement.empty:
            st.info("Line history begins after the next Odds API refresh. Each refresh now appends a snapshot instead of replacing it.")
        else:
            stale_count = int(movement["stale"].sum())
            if stale_count:
                st.warning(f"{stale_count} displayed prices are older than {settings.get('odds_stale_minutes', '20')} minutes.")
            st.dataframe(
                movement.rename(
                    columns={
                        "matchup": "Matchup", "team": "Team", "opening_line": "Opening",
                        "current_line": "Current", "line_movement": "Move", "current_book": "Current book",
                        "best_line": "Best", "best_book": "Best book", "age_minutes": "Age (min)", "stale": "Stale",
                    }
                ),
                hide_index=True,
                width="stretch",
            )

    with context_tab:
        context_columns = [
            "first_pitch_et", "matchup", "away_lineup_status", "home_lineup_status",
            "away_injury_count", "home_injury_count", "away_injured_hitters", "home_injured_hitters",
            "away_bullpen_status", "home_bullpen_status",
            "away_bullpen_pitches_1d", "home_bullpen_pitches_1d",
            "away_bullpen_pitches_3d", "home_bullpen_pitches_3d",
            "pitcher_change_detected", "weather_risk_level", "rule_blockers",
        ]
        st.dataframe(
            display_df[context_columns].rename(
                columns={
                    "first_pitch_et": "First pitch", "matchup": "Matchup",
                    "away_lineup_status": "Away lineup", "home_lineup_status": "Home lineup",
                    "away_injury_count": "Away IL", "home_injury_count": "Home IL",
                    "away_injured_hitters": "Away hitters IL", "home_injured_hitters": "Home hitters IL",
                    "away_bullpen_status": "Away bullpen", "home_bullpen_status": "Home bullpen",
                    "away_bullpen_pitches_1d": "Away RP 1d", "home_bullpen_pitches_1d": "Home RP 1d",
                    "away_bullpen_pitches_3d": "Away RP 3d", "home_bullpen_pitches_3d": "Home RP 3d",
                    "pitcher_change_detected": "Pitcher changed", "weather_risk_level": "Weather risk",
                    "rule_blockers": "Rule blockers",
                }
            ),
            hide_index=True,
            width="stretch",
        )
        st.dataframe(
            friendly_slate.style.apply(style_game_status_rows, axis=1).apply(style_recommendation_result, axis=1),
            hide_index=True,
            width="stretch",
            height=520,
        )

    weather_columns = [
        "first_pitch_et",
        "status",
        "score_summary",
        "matchup",
        "venue_name",
        "first_pitch_utc",
        "weather_summary",
        "weather_risk_level",
    ]
    with weather_tab:
        st.dataframe(
            display_df[weather_columns]
            .rename(
                columns={
                    "first_pitch_et": "First pitch",
                    "status": "Status",
                    "score_summary": "Score",
                    "matchup": "Matchup",
                    "venue_name": "Ballpark",
                    "first_pitch_utc": "UTC time",
                    "weather_summary": "Forecast",
                    "weather_risk_level": "Risk",
                }
            )
            .style.apply(style_game_status_rows, axis=1),
            hide_index=True,
            width="stretch",
        )

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
    with statcast_tab:
        st.dataframe(
            display_df[confluence_columns]
            .rename(
                columns={
                    "first_pitch_et": "First pitch",
                    "status": "Status",
                    "score_summary": "Score",
                    "matchup": "Matchup",
                    "away_offense_score": "Away offense",
                    "away_pitching_score": "Away pitching",
                    "away_confluence_score": "Away confluence",
                    "home_offense_score": "Home offense",
                    "home_pitching_score": "Home pitching",
                    "home_confluence_score": "Home confluence",
                }
            )
            .style.apply(style_game_status_rows, axis=1),
            hide_index=True,
            width="stretch",
        )

    with charts_tab:
        c1, c2 = st.columns(2)
        with c1:
            fig = px.bar(
                slate_df.sort_values("edge_pct"),
                x="edge_pct",
                y="matchup",
                orientation="h",
                color="recommendation",
                color_discrete_map={"BET": "#087f5b", "PASS": "#b51f32"},
                title="Model edge by game",
            )
            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#ffffff", font_color="#102133")
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            fig = px.scatter(
                slate_df,
                x="confidence",
                y="edge_pct",
                color="recommendation",
                color_discrete_map={"BET": "#087f5b", "PASS": "#b51f32"},
                hover_data=["first_pitch_et", "matchup", "best_market"],
                title="Confidence and edge",
            )
            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#ffffff", font_color="#102133")
            st.plotly_chart(fig, use_container_width=True)


def render_game_breakdown(slate_df: pd.DataFrame, settings: dict[str, str]) -> None:
    slate_date = str(slate_df.iloc[0]["game_date"]) if not slate_df.empty else date.today().isoformat()
    render_page_header(
        "Game Center",
        "Matchup detail",
        "Open any game to see the score, market recommendation, weather, and model inputs.",
        f"{slate_date} | {DISPLAY_TIMEZONE_LABEL}",
    )
    selected = st.selectbox("Choose a matchup", slate_df["matchup"].tolist())
    row = slate_df[slate_df["matchup"] == selected].iloc[0]
    away_team, home_team = str(row["matchup"]).split(" @ ", maxsplit=1)
    probable_starters = str(row.get("probable_starters") or "Pitchers pending")
    if " vs " in probable_starters:
        away_pitcher, home_pitcher = probable_starters.split(" vs ", maxsplit=1)
    else:
        away_pitcher = home_pitcher = probable_starters
    away_score = "-" if pd.isna(row.get("away_score")) else str(int(row["away_score"]))
    home_score = "-" if pd.isna(row.get("home_score")) else str(int(row["home_score"]))
    st.markdown(
        f"""
        <div class="edge-scoreboard">
            <div class="edge-team">
                <div class="edge-team-name">{html.escape(away_team)}</div>
                <div class="edge-team-pitcher">{html.escape(away_pitcher)}</div>
            </div>
            <div class="edge-score-center">
                <div class="edge-score">{html.escape(away_score)} &ndash; {html.escape(home_score)}</div>
                <div class="edge-score-status">{html.escape(str(row['status']))} | {html.escape(str(row['first_pitch_et']))}</div>
            </div>
            <div class="edge-team home">
                <div class="edge-team-name">{html.escape(home_team)}</div>
                <div class="edge-team-pitcher">{html.escape(home_pitcher)}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    render_section_heading("Model call", "The recommendation updates when the active strategy is saved")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Decision", row["recommendation"])
    c2.metric("Model side", row["recommended_side"])
    c3.metric("Best market", row["best_market"])
    c4.metric("Edge", f"{row['edge_pct']:.1f}%")
    c5.metric("Confidence", f"{row['confidence']:.1f}/10")

    overview_tab, availability_tab, movement_tab, inputs_tab, weather_tab, box_tab = st.tabs(
        ["Overview", "Availability", "Line movement", "Model inputs", "Weather", "Box score"]
    )
    with overview_tab:
        price_left, price_middle, price_right = st.columns(3)
        market_line = int(row["market_line"] or 0)
        fair_line = int(row["fair_line"] or 0)
        price_left.metric("Sportsbook line", f"{market_line:+d}" if market_line else "Pending")
        price_middle.metric("Model fair line", f"{fair_line:+d}" if fair_line else "Pending")
        price_right.metric("Result check", row["recommendation_result"])
        st.info(row["reason"])
        st.caption(f"{row['venue_name']} | Probable starters: {row['probable_starters']}")
        with st.expander("Rule audit", expanded=row["recommendation"] == "PASS"):
            try:
                rule_payload = json.loads(str(row.get("rule_checks_json") or "{}"))
            except json.JSONDecodeError:
                rule_payload = {}
            rule_rows = rule_payload.get("checks", [])
            if rule_rows:
                st.dataframe(pd.DataFrame(rule_rows), hide_index=True, width="stretch")
            else:
                st.info("Rule-level audit details will appear after this game is scored with model version 2.")
            st.caption(f"Model version: {row.get('model_version', 'legacy')} | Profile: {row.get('rule_profile', 'Legacy')}")

    with availability_tab:
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Away lineup", row["away_lineup_status"])
        a2.metric("Home lineup", row["home_lineup_status"])
        a3.metric("Away bullpen", row["away_bullpen_status"])
        a4.metric("Home bullpen", row["home_bullpen_status"])
        if row["pitcher_change_detected"]:
            st.error(f"Starting-pitcher change detected: {row['pitcher_change_details']}")
        lineup_left, lineup_right = st.columns(2)
        with lineup_left:
            st.markdown(f"#### {away_team} lineup")
            away_lineup = json_list(row["away_lineup_json"])
            if away_lineup:
                st.dataframe(pd.DataFrame({"Order": range(1, len(away_lineup) + 1), "Player": away_lineup}), hide_index=True, width="stretch")
            else:
                st.info("Official batting order has not been posted.")
        with lineup_right:
            st.markdown(f"#### {home_team} lineup")
            home_lineup = json_list(row["home_lineup_json"])
            if home_lineup:
                st.dataframe(pd.DataFrame({"Order": range(1, len(home_lineup) + 1), "Player": home_lineup}), hide_index=True, width="stretch")
            else:
                st.info("Official batting order has not been posted.")
        injury_left, injury_right = st.columns(2)
        with injury_left:
            st.markdown(f"#### {away_team} injured list")
            away_injuries = json_list(row["away_injuries_json"])
            if away_injuries:
                st.dataframe(pd.DataFrame(away_injuries), hide_index=True, width="stretch")
            else:
                st.caption("No injured-list entries returned.")
        with injury_right:
            st.markdown(f"#### {home_team} injured list")
            home_injuries = json_list(row["home_injuries_json"])
            if home_injuries:
                st.dataframe(pd.DataFrame(home_injuries), hide_index=True, width="stretch")
            else:
                st.caption("No injured-list entries returned.")
        bullpen_df = pd.DataFrame(
            [
                [away_team, row["away_bullpen_pitches_1d"], row["away_bullpen_pitches_3d"], row["away_bullpen_status"]],
                [home_team, row["home_bullpen_pitches_1d"], row["home_bullpen_pitches_3d"], row["home_bullpen_status"]],
            ],
            columns=["Team", "Relief pitches - 1 day", "Relief pitches - 3 days", "Fatigue"],
        )
        st.markdown("#### Bullpen workload")
        st.dataframe(bullpen_df, hide_index=True, width="stretch")

    with movement_tab:
        movement = line_movement_summary(
            str(row["away_team_name"]), str(row["home_team_name"]),
            settings.get("bookmaker_preference", ""), int(float(settings.get("odds_stale_minutes", 20))),
        )
        history = odds_history_dataframe(str(row["away_team_name"]), str(row["home_team_name"]))
        if movement.empty:
            st.info("No line history is stored for this matchup yet. Run at least one Odds API refresh.")
        else:
            st.dataframe(movement, hide_index=True, width="stretch")
            selected_history = history[history["team"] == row["recommended_side"]].copy()
            if not selected_history.empty:
                selected_history["captured_at"] = pd.to_datetime(selected_history["captured_at"], utc=True, errors="coerce")
                line_fig = px.line(
                    selected_history, x="captured_at", y="price", color="bookmaker", markers=True,
                    title=f"{row['recommended_side']} moneyline history",
                )
                line_fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#ffffff", font_color="#102133")
                st.plotly_chart(line_fig, use_container_width=True)

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
                f"Selected bullpen is {row['away_bullpen_status'] if row['recommended_side'] == row['away_team_name'] else row['home_bullpen_status']} based on recent relief pitches.",
                "Derived from team Statcast batting confluence.",
                f"Official status: {row['away_lineup_status']} / {row['home_lineup_status']}.",
                f"{row['weather_summary']} Risk: {row['weather_risk_level']}.",
                row["pitcher_change_details"] or "No starting-pitcher change detected.",
            ],
        }
    )
    with inputs_tab:
        st.markdown("#### Statcast team comparison")
        st.dataframe(statcast_metrics, hide_index=True, width="stretch")
        st.markdown("#### Category breakdown")
        st.dataframe(score_breakdown, hide_index=True, width="stretch")
        st.caption("Confluence combines recent quality of contact and pitching prevention. It is one input, not a guarantee.")

    with weather_tab:
        w1, w2, w3, w4 = st.columns(4)
        w1.metric("Temperature", f"{row['temperature_f']:.0f} F" if pd.notna(row["temperature_f"]) else "Pending")
        w2.metric("Wind", f"{row['wind_speed_mph']:.0f} mph" if pd.notna(row["wind_speed_mph"]) else "Pending")
        w3.metric("Direction", f"{row['wind_direction_deg']:.0f} deg" if pd.notna(row["wind_direction_deg"]) else "Pending")
        w4.metric("Rain chance", f"{row['precipitation_probability']:.0f}%" if pd.notna(row["precipitation_probability"]) else "Pending")
        st.info(row["weather_summary"])
        st.caption(f"Forecast hour: {row['forecast_time']}" if pd.notna(row["forecast_time"]) else "Weather refresh pending")

    with box_tab:
        st.info(row["box_score_summary"])
        b1, b2, b3 = st.columns(3)
        b1.metric("Game status", row["status"])
        b2.metric("Score", row["score_summary"])
        b3.metric("Winner / leader", row["actual_winner"])


def render_bet_tracker(slate_df: pd.DataFrame, bets_df: pd.DataFrame, settings: dict[str, str]) -> None:
    render_page_header(
        "Bet Tracker",
        "Your ledger",
        "Record the bets you actually place, then compare results and closing-line value over time.",
        f"Default stake {float(settings['default_stake_units']):.2f} units",
    )

    with st.expander("Add a bet", expanded=bets_df.empty):
        with st.form("new_bet_form", clear_on_submit=True):
            st.markdown("#### Bet details")
            game_options = {"No linked game": None}
            game_options.update({row["matchup"]: row["game_id"] for _, row in slate_df.iterrows()})
            form_left, form_right = st.columns(2)
            with form_left:
                game_label = st.selectbox("Game", list(game_options.keys()))
                bet_label = st.text_input("Bet label", "LAD F5 ML")
                market = st.selectbox("Market", MARKETS)
                odds = st.number_input("Odds", value=-110, step=1)
            with form_right:
                stake = st.number_input(
                    "Stake units",
                    value=float(settings["default_stake_units"]),
                    min_value=0.0,
                    step=0.25,
                )
                closing_line_value = st.text_input("Closing line", "")
                result = st.selectbox("Result", RESULTS)
                notes = st.text_area("Notes", "")
            submitted = st.form_submit_button("Save bet", type="primary")
            if submitted:
                closing_line = int(closing_line_value) if closing_line_value.strip() else None
                add_bet(date.today(), game_options[game_label], bet_label, market, int(odds), float(stake), closing_line, result, notes)
                st.success("Bet saved.")

    render_section_heading("Bet history", f"{len(bets_df)} tracked entries")
    if bets_df.empty:
        st.info("No bets have been recorded. Add the first one above when you have a confirmed ticket.")
    else:
        st.dataframe(bets_df, hide_index=True, width="stretch")


def render_performance(bets_df: pd.DataFrame, settings: dict[str, str]) -> None:
    render_page_header(
        "Performance",
        "Results and calibration",
        "Track profitability, closing-line value, and whether stronger model signals perform better over time.",
        "Settled bets only where noted",
    )
    bets_tab, live_tab, retro_tab = st.tabs(["Bet results", "Live model validation", "Retrosheet backtest"])

    with bets_tab:
        if bets_df.empty:
            st.info("Bet performance appears after bets are tracked and settled.")
        else:
            total_units = float(bets_df["profit_loss_units"].sum())
            total_stake = float(bets_df["stake_units"].sum())
            settled = bets_df[bets_df["result"].isin(["W", "L"])]
            win_rate = float((settled["result"] == "W").mean()) if len(settled) else 0.0
            avg_clv = float(bets_df["clv_cents"].dropna().mean()) if bets_df["clv_cents"].notna().any() else 0.0
            clv_win_pct = float((bets_df["clv_cents"].fillna(0) > 0).mean())
            p1, p2, p3, p4, p5 = st.columns(5)
            p1.metric("Total units", f"{total_units:+.2f}")
            p2.metric("ROI", f"{(total_units / total_stake if total_stake else 0):.1%}")
            p3.metric("Win rate", f"{win_rate:.1%}")
            p4.metric("Avg CLV", f"{avg_clv:.1f} cents")
            p5.metric("Positive CLV", f"{clv_win_pct:.1%}")

            chart_df = bets_df.copy()
            chart_df["bet_date"] = pd.to_datetime(chart_df["bet_date"])
            chart_df["month"] = chart_df["bet_date"].dt.to_period("M").astype(str)
            chart_df["cumulative_units"] = chart_df["profit_loss_units"].cumsum()
            c1, c2 = st.columns(2)
            with c1:
                units_fig = px.line(chart_df, x="bet_date", y="cumulative_units", title="Cumulative units")
                units_fig.update_traces(line_color="#087f5b", line_width=3)
                units_fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#ffffff", font_color="#102133")
                st.plotly_chart(units_fig, use_container_width=True)
                market_fig = px.bar(chart_df, x="market", y="profit_loss_units", title="Profit and loss by market", color_discrete_sequence=["#152b40"])
                market_fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#ffffff", font_color="#102133")
                st.plotly_chart(market_fig, use_container_width=True)
            with c2:
                monthly = chart_df.groupby("month", as_index=False).agg(profit_loss_units=("profit_loss_units", "sum"), stake_units=("stake_units", "sum"))
                monthly["roi"] = monthly["profit_loss_units"] / monthly["stake_units"]
                roi_fig = px.bar(monthly, x="month", y="roi", title="ROI by month", color_discrete_sequence=["#b51f32"])
                roi_fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#ffffff", font_color="#102133")
                st.plotly_chart(roi_fig, use_container_width=True)
                clv_fig = px.histogram(chart_df, x="clv_cents", title="Closing-line value distribution", color_discrete_sequence=["#b7791f"])
                clv_fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#ffffff", font_color="#102133")
                st.plotly_chart(clv_fig, use_container_width=True)

    with live_tab:
        validation = live_model_validation()
        if validation.empty:
            st.info("Live validation starts after version 2 predictions are saved before first pitch and those games become final.")
        else:
            bets_only = validation[validation["recommendation"] == "BET"]
            v1, v2, v3, v4 = st.columns(4)
            v1.metric("Settled predictions", len(validation))
            v2.metric("Directional accuracy", f"{validation['correct'].mean():.1%}")
            v3.metric("Brier score", f"{validation['brier_score'].mean():.3f}")
            v4.metric("BET accuracy", f"{bets_only['correct'].mean():.1%}" if len(bets_only) else "Pending")
            validation["edge_bucket"] = pd.cut(
                validation["edge_pct"], bins=[-100, 2, 3, 4, 6, 100], labels=["<2%", "2-3%", "3-4%", "4-6%", "6%+"]
            )
            validation["confidence_bucket"] = pd.cut(
                validation["confidence"], bins=[0, 5.5, 6.5, 7.5, 8.5, 10], include_lowest=True
            ).astype(str)
            edge_results = validation.groupby("edge_bucket", as_index=False, observed=True).agg(
                games=("correct", "size"), accuracy=("correct", "mean"), average_brier=("brier_score", "mean")
            )
            confidence_results = validation.groupby("confidence_bucket", as_index=False, observed=True).agg(
                games=("correct", "size"), accuracy=("correct", "mean"), average_brier=("brier_score", "mean")
            )
            calibration = calibration_table(validation, "model_probability", "outcome")
            left, right = st.columns(2)
            with left:
                st.markdown("#### Performance by edge")
                st.dataframe(edge_results, hide_index=True, width="stretch")
                st.markdown("#### Performance by confidence")
                st.dataframe(confidence_results, hide_index=True, width="stretch")
            with right:
                calibration_fig = px.scatter(
                    calibration, x="average_probability", y="actual_win_rate", size="predictions",
                    range_x=[0.3, 0.7], range_y=[0.3, 0.7], title="Live probability calibration",
                )
                calibration_fig.add_shape(type="line", x0=0.3, y0=0.3, x1=0.7, y1=0.7, line={"dash": "dash", "color": "#5f7182"})
                calibration_fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#ffffff", font_color="#102133")
                st.plotly_chart(calibration_fig, use_container_width=True)
            st.dataframe(
                validation[["run_date", "model_version", "recommended_side", "model_probability", "edge_pct", "confidence", "recommendation", "actual_winner", "correct", "brier_score"]],
                hide_index=True, width="stretch",
            )

    with retro_tab:
        retro_left, retro_action = st.columns([3, 1])
        retro_season = retro_left.number_input(
            "Retrosheet season", min_value=1898, max_value=2025,
            value=min(2025, int(float(settings.get("retrosheet_season", 2025)))), step=1,
        )
        if retro_action.button("Import and run", type="primary", use_container_width=True):
            with st.spinner(f"Downloading and backtesting Retrosheet {int(retro_season)}..."):
                result = import_retrosheet_backtest(int(retro_season))
            save_setting("retrosheet_season", int(retro_season))
            st.success(
                f"Backtest complete: {result['games']} games, {result['accuracy']:.1%} directional accuracy, "
                f"Brier score {result['brier_score']:.3f}."
            )
        retro = load_dataframe(
            """
            SELECT rb.*, rg.home_team
            FROM retrosheet_backtests rb
            JOIN retrosheet_games rg ON rg.retro_game_id = rb.retro_game_id
            WHERE rb.season = ? AND rb.model_version = ? ORDER BY rb.id
            """,
            (int(retro_season), RETROSHEET_MODEL_VERSION),
        )
        st.caption("The information used here was obtained free of charge from and is copyrighted by Retrosheet.")
        if retro.empty:
            st.info("Choose a season and run the import. Retrosheet currently publishes complete processed CSV seasons through 2025.")
        else:
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Games", len(retro))
            r2.metric("Accuracy", f"{retro['correct'].astype(float).mean():.1%}")
            r3.metric("Brier score", f"{retro['brier_score'].mean():.3f}")
            r4.metric("Model version", RETROSHEET_MODEL_VERSION)
            retro["actual_home_win"] = (retro["actual_winner"] == retro["home_team"]).astype(int)
            retro_calibration = calibration_table(retro, "predicted_home_probability", "actual_home_win")
            retro_fig = px.scatter(
                retro_calibration, x="average_probability", y="actual_win_rate", size="predictions",
                range_x=[0.3, 0.7], range_y=[0.3, 0.7], title=f"Retrosheet {int(retro_season)} calibration",
            )
            retro_fig.add_shape(type="line", x0=0.3, y0=0.3, x1=0.7, y1=0.7, line={"dash": "dash", "color": "#5f7182"})
            retro_fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#ffffff", font_color="#102133")
            st.plotly_chart(retro_fig, use_container_width=True)
            st.caption("This is a no-lookahead rolling team-strength baseline. Retrosheet does not include historical sportsbook prices, so ROI and edge are not claimed here.")


def render_data_health(settings: dict[str, str]) -> None:
    render_page_header(
        "Data & Sync",
        "System status",
        "See which sources are connected, when the model last refreshed, and what needs attention.",
        "Admin view",
    )
    render_section_heading("Historical export", "Download one day, one month, or a custom range as an Excel workbook")
    first_stored_date, last_stored_date, stored_game_count = historical_date_bounds()
    export_mode = st.radio(
        "Export period",
        ["Single day", "Month", "Custom range"],
        horizontal=True,
        label_visibility="collapsed",
    )
    export_start = first_stored_date
    export_end = last_stored_date
    if export_mode == "Single day":
        export_day = st.date_input(
            "Game date",
            value=last_stored_date,
            min_value=first_stored_date,
            max_value=last_stored_date,
        )
        export_start = export_end = export_day
    elif export_mode == "Month":
        stored_months = load_dataframe(
            "SELECT DISTINCT SUBSTR(game_date, 1, 7) AS game_month FROM games ORDER BY game_month DESC"
        )["game_month"].dropna().astype(str).tolist()
        selected_month = st.selectbox("Month", stored_months or [last_stored_date.strftime("%Y-%m")])
        month_start = date.fromisoformat(f"{selected_month}-01")
        next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        export_start = max(first_stored_date, month_start)
        export_end = min(last_stored_date, next_month - timedelta(days=1))
    else:
        selected_range = st.date_input(
            "Game date range",
            value=(first_stored_date, last_stored_date),
            min_value=first_stored_date,
            max_value=last_stored_date,
        )
        if isinstance(selected_range, (tuple, list)) and len(selected_range) == 2:
            export_start, export_end = selected_range

    range_game_count = int(
        load_dataframe(
            "SELECT COUNT(*) AS count FROM games WHERE game_date BETWEEN ? AND ?",
            (export_start.isoformat(), export_end.isoformat()),
        ).iloc[0]["count"]
    )
    st.caption(
        f"Stored history: {stored_game_count} games from {first_stored_date.isoformat()} through "
        f"{last_stored_date.isoformat()}. Selected export: {range_game_count} games."
    )
    st.info(
        "Exports include records already captured in the database. Odds movement, lineup changes, and model snapshots "
        "are not retroactive, so the dashboard must refresh on each date you want represented in future archives."
    )
    if st.button("Prepare Excel export", type="primary", disabled=range_game_count == 0):
        try:
            with st.spinner("Building historical workbook..."):
                export_bytes, export_counts = build_historical_excel(export_start, export_end)
            st.session_state["historical_export"] = {
                "data": export_bytes,
                "file_name": f"mlb_edge_history_{export_start.isoformat()}_to_{export_end.isoformat()}.xlsx",
                "counts": export_counts,
                "start_date": export_start.isoformat(),
                "end_date": export_end.isoformat(),
            }
        except Exception as exc:
            st.error(f"Excel export could not be prepared: {exc}")
    prepared_export = st.session_state.get("historical_export")
    if prepared_export and prepared_export.get("start_date") == export_start.isoformat() and prepared_export.get("end_date") == export_end.isoformat():
        export_counts = prepared_export["counts"]
        st.download_button(
            "Download Excel archive",
            data=prepared_export["data"],
            file_name=prepared_export["file_name"],
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )
        st.caption(
            f"Workbook ready: {export_counts['Games']} games, "
            f"{export_counts['Prediction History']} prediction snapshots, and "
            f"{export_counts['Odds History']} odds outcomes."
        )

    mlb_marker_configured = bool(get_secret("MLB_STATS_API_MARKER"))
    odds_configured = bool(get_secret("ODDS_API_KEY"))
    statcast_metrics = load_dataframe(
        """
        SELECT COUNT(DISTINCT team) AS team_count, COUNT(*) AS snapshot_count,
               MAX(captured_at) AS last_refresh
        FROM team_statcast_metrics
        """
    )
    team_count = int(statcast_metrics.iloc[0]["team_count"]) if not statcast_metrics.empty else 0
    statcast_snapshot_count = int(statcast_metrics.iloc[0]["snapshot_count"]) if not statcast_metrics.empty else 0
    last_statcast_refresh = statcast_metrics.iloc[0]["last_refresh"] if team_count else "Not loaded"
    availability_metrics = load_dataframe(
        "SELECT COUNT(*) AS snapshots, MAX(captured_at) AS last_refresh FROM team_availability_snapshots"
    )
    availability_count = int(availability_metrics.iloc[0]["snapshots"]) if not availability_metrics.empty else 0
    odds_history_count = int(load_dataframe("SELECT COUNT(*) AS count FROM live_odds_snapshots").iloc[0]["count"])
    retrosheet_count = int(load_dataframe("SELECT COUNT(*) AS count FROM retrosheet_games").iloc[0]["count"])
    health = pd.DataFrame(
        [
            ["MLB Stats API", "Schedule, lineups, injuries, bullpen", f"{availability_count} availability snapshots" if availability_count else ("Marker configured" if mlb_marker_configured else "Ready via public endpoint")],
            ["Odds API", "Timestamped market history", f"{odds_history_count} stored rows" if odds_configured else "Needs API key/login before live odds"],
            ["Open-Meteo", "Weather", "Ready, no login required"],
            ["pybaseball Statcast", "Baseball Savant team metrics", f"{team_count} teams; {statcast_snapshot_count} retained snapshots; last {last_statcast_refresh}" if team_count else "Needs refresh"],
            ["Retrosheet", "Historical validation", f"{retrosheet_count} games imported" if retrosheet_count else "Ready; import from Performance"],
            ["Model engine", "Versioned rule audit", MODEL_VERSION],
            [
                "Supabase" if is_postgres_enabled() else "Local database",
                "Saved games, settings, and bets",
                "Connected" if is_postgres_enabled() else ("Writable" if os.access(DATA_DIR, os.W_OK) else "Not writable"),
            ],
        ],
        columns=["Source", "Purpose", "Status"],
    )
    st.dataframe(health, hide_index=True, width="stretch")

    if st.button("Run refresh check", type="primary"):
        result = run_local_refresh(settings)
        if result["status"] == "success":
            st.success(
                f"Refresh succeeded: {result['odds_events']} odds events, "
                f"{result['odds_markets']} market rows, {result['weather_loaded']} weather forecasts, "
                f"{result['lineup_games']} posted lineups, {result['availability_teams']} injury rosters, "
                f"{result['statcast_teams']} Statcast teams, {result['scored_games']} scored games."
            )
        else:
            st.warning("Refresh logged as partial.")
            for error in result["errors"]:
                st.write(f"- {error}")

    live_odds = load_live_odds()
    render_section_heading("Latest odds snapshot")
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
    render_section_heading("Refresh history", "Most recent 25 runs")
    st.dataframe(logs, hide_index=True, width="stretch")


def render_model_guide(settings: dict[str, str]) -> None:
    render_page_header(
        "Model Guide",
        "How to read the dashboard",
        "A plain-language explanation of the model call, the data behind it, and the limits of every prediction.",
        f"Active strategy: {settings['rule_profile']}",
    )

    render_section_heading("The decision path", "Each recommendation passes through the same sequence")
    guide_columns = st.columns(4)
    guide_cards = [
        (
            "1. Measure team form",
            "Recent Baseball Savant and Statcast quality-of-contact data is combined with pitching-prevention metrics for both teams.",
        ),
        (
            "2. Build a fair price",
            "The difference between team confluence ratings becomes a win probability and an American-odds fair line.",
        ),
        (
            "3. Compare the market",
            "The fair price is compared with the selected sportsbook line to calculate the available model edge.",
        ),
        (
            "4. Apply your rules",
            "Price, confidence, lineups, starter changes, bullpen workload, injuries, weather, odds freshness, and the daily limit determine BET or PASS.",
        ),
    ]
    for column, (title, description) in zip(guide_columns, guide_cards):
        column.markdown(
            f'<div class="edge-guide-card"><strong>{html.escape(title)}</strong><p>{html.escape(description)}</p></div>',
            unsafe_allow_html=True,
        )

    render_section_heading("Key terms", "The four numbers to understand first")
    terms = pd.DataFrame(
        [
            ["Fair line", "The American-odds price implied by the model's estimated win probability."],
            ["Market line", "The current sportsbook price used for comparison."],
            ["Edge", "The gap between model probability and market-implied probability."],
            ["Confidence", "A 0-10 rating based on the strength of the current Statcast confluence difference."],
            ["Confluence", "A combined rating of recent offensive quality and pitching prevention."],
            ["Rule blocker", "A required condition that failed and forced a PASS even when the model prefers one side."],
            ["Stale price", "An odds snapshot older than the configured freshness limit. Stale prices cannot qualify as a BET."],
            ["PASS", "The game does not satisfy every active rule. It is not a prediction that the model side will lose."],
        ],
        columns=["Term", "Meaning"],
    )
    st.dataframe(terms, hide_index=True, width="stretch")

    render_section_heading("Data sources", "What is live today")
    sources = pd.DataFrame(
        [
            ["MLB Stats API", "Schedule, scores, probable pitchers, confirmed batting orders, injured-list status, and bullpen workload"],
            ["The Odds API", "Opening, current, and best market prices with timestamped movement history"],
            ["Open-Meteo", "Hourly temperature, wind, and precipitation risk by ballpark"],
            ["Baseball Savant / Statcast", "Recent batted-ball quality and pitching-prevention metrics via pybaseball"],
            ["Retrosheet", "Historical game results for no-lookahead benchmark backtesting and calibration"],
        ],
        columns=["Source", "Used for"],
    )
    st.dataframe(sources, hide_index=True, width="stretch")

    st.warning(
        "Model output is informational and can be wrong. Confirm the starting pitcher, lineup, weather, and current price before acting. "
        "Use fixed stakes and a daily limit; never chase losses."
    )


def render_settings(settings: dict[str, str]) -> None:
    render_page_header(
        "Settings",
        "Dashboard controls",
        "Manage branding, strategy rules, model thresholds, and source preferences.",
        "Changes save for all sessions",
    )
    render_section_heading("Branding")
    current_logo = resolve_asset_path(settings.get("dashboard_logo_path", ""))
    if current_logo:
        st.image(str(current_logo), width=260)

    with st.form("settings_form"):
        dashboard_title = st.text_input("Dashboard Title", settings.get("dashboard_title", "MLB Edge Model"))
        uploaded_logo = st.file_uploader("Upload Dashboard Logo", type=["png", "jpg", "jpeg", "webp"])

        st.markdown("#### Rule profile")
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

        st.markdown("#### Manual thresholds")
        edge_threshold = st.number_input("Manual Edge Threshold (%)", value=float(settings["edge_threshold"]), step=0.25)
        min_line_difference = st.number_input("Minimum Line Difference (cents)", value=int(float(settings["min_line_difference_cents"])), step=1)
        min_confidence = st.number_input("Minimum Confidence", value=float(settings["min_confidence"]), min_value=0.0, max_value=10.0, step=0.1)
        default_stake = st.number_input("Default Stake Units", value=float(settings["default_stake_units"]), step=0.25)
        bankroll_units = st.number_input("Bankroll Units", value=float(settings["bankroll_units"]), step=1.0)
        bookmaker = st.text_input("Odds Source / Bookmaker Preference", settings["bookmaker_preference"])
        odds_stale_minutes = st.number_input(
            "Mark Odds Stale After (minutes)",
            value=int(float(settings.get("odds_stale_minutes", 20))), min_value=5, max_value=180, step=5,
        )
        statcast_lookback_days = st.number_input("Statcast Lookback Days", value=int(float(settings["statcast_lookback_days"])), min_value=3, max_value=45, step=1)
        dashboard_password = st.text_input("Dashboard Password", settings["dashboard_password"], type="password")
        submitted = st.form_submit_button("Save changes", type="primary")
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
                "odds_stale_minutes": odds_stale_minutes,
                "statcast_lookback_days": statcast_lookback_days,
                "dashboard_password": dashboard_password,
            }
            for key, value in updates.items():
                save_setting(key, value)
            saved_settings = load_settings()
            scored_games = rescore_current_slate(active_rule_settings(saved_settings))
            st.success(f"Changes saved. {scored_games} games were re-scored with the active rules.")
            st.rerun()

    render_section_heading("Connection status")
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
    inject_dashboard_styles()
    settings = load_settings()
    slate_df = load_slate()
    bets_df = load_bets()
    page, active_settings = sidebar(settings)

    if page == "Daily Slate":
        render_daily_slate(slate_df, bets_df, active_settings)
    elif page == "Game Breakdown":
        render_game_breakdown(slate_df, active_settings)
    elif page == "Bet Tracker":
        render_bet_tracker(slate_df, bets_df, active_settings)
    elif page == "Performance":
        render_performance(bets_df, active_settings)
    elif page == "Model Guide":
        render_model_guide(active_settings)
    elif page == "Data Health":
        render_data_health(active_settings)
    elif page == "Settings":
        render_settings(settings)


if __name__ == "__main__":
    main()
