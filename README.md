# MLB Edge Dashboard

Local Streamlit dashboard for the MLB Edge Model. It runs from this folder, stores data in SQLite, and starts with the included sample slate and sample bets.

## Run Locally

### macOS / Linux

```bash
cd mlb_dashboard_build_package/app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

### Windows PowerShell

```powershell
cd mlb_dashboard_build_package\app
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Open the browser at `http://localhost:8501`.

## What Is Included

- Responsive sports-media redesign for desktop and mobile
- Today page with top opportunities, live status filters, and tabbed supporting data
- Game Center with a scoreboard, market comparison, lineup/injury context, weather, and model inputs
- Profile-specific rule enforcement for lineup status, probable starters, bullpen workload, weather risk, pitcher changes, and odds freshness
- Historical line movement with opener, current price, best available price, and stale-price warnings
- Excel archive export for a day, month, or custom date range from Data & Sync
- Model Guide with plain-language terms, decision flow, and data-source transparency
- Open-Meteo weather forecasts by game
- pybaseball Baseball Savant / Statcast team confluence scoring
- MLB Stats API lineup, injured-list, probable-pitcher, and recent bullpen-workload context
- Bet Tracker that saves bets to SQLite
- Performance dashboard with ROI, live-model calibration, edge/confidence buckets, and Retrosheet backtesting
- Model-versioned prediction history and auditable rule checks for every scored game
- Data Health page with refresh logs
- Settings page for thresholds, stake sizing, bankroll, bookmaker, and Statcast lookback window
- `.env.example` and `.streamlit/secrets.example.toml` for recreating local setup later

## Local Data

The app creates `data/mlb_edge.db` automatically on first run and seeds it from:

- `data/sample_daily_slate.csv`
- `data/sample_bets.csv`

The `Refresh MLB Model` button loads the current MLB schedule from MLB Stats API, live odds from The Odds API, game-weather forecasts from Open-Meteo `/v1/forecast`, and Baseball Savant / Statcast data through `pybaseball`. Refreshed games are scored with team offense, pitching-prevention, and confluence metrics.

Paid leaderboard exports and manual CSV uploads have been removed from the local workflow. Use Baseball Savant / Statcast through `pybaseball` as the primary advanced source, MLB Stats API for schedule and live availability context, Open-Meteo for weather, and Retrosheet for historical outcome backtesting.

Retrosheet is imported from the Performance page. The benchmark uses only information available before each historical game and reports directional accuracy, Brier score, and calibration. Retrosheet does not include historical sportsbook prices, so this benchmark does not report historical betting ROI or closing-line value.

Historical Excel exports are available under Data & Sync. Each workbook contains game summaries, prediction history, game-context snapshots, odds history, weather history, tracked bets, Statcast snapshots, and refresh logs. Supabase is the persistent source for the hosted dashboard; the local SQLite file is only the source when running locally.

History is collected prospectively. A date must be refreshed for its live odds, lineup changes, weather, and model snapshots to be available later. Regular refreshes should be scheduled outside Streamlit if guaranteed unattended collection is required.

Set `PYBASEBALL_CACHE=data/pybaseball_cache` and `MPLCONFIGDIR=data/matplotlib_config` in `.env` so pybaseball keeps its cache files inside the local dashboard folder.

## API Setup Point

The dashboard can use an optional MLB Stats API marker, but the schedule endpoint also works through public access.

If you have an MLB Stats marker, set:

```bash
MLB_STATS_API_MARKER=your-marker-here
```

When ready, create a key for The Odds API and set:

```bash
ODDS_API_KEY=your-key-here
```

Do not hardcode real keys in source code. Use `.env`, Streamlit secrets, or your shell environment.

## Recreate On Another Computer

1. Copy the `mlb_dashboard_build_package/app` folder.
2. Install Python 3.11 or newer.
3. Run the setup commands above.
4. Start Streamlit with `streamlit run app.py`.
5. Add API keys only after you create the needed logins.

To start from a clean local database, stop Streamlit and delete `data/mlb_edge.db`; the app will recreate it from the sample CSV files.

## Hosting With Supabase + Streamlit

Deployment prep has started in:

- `DEPLOYMENT_SUPABASE_STREAMLIT.md`
- `supabase_schema.sql`
- `supabase_migration_20260718_model_context.sql`
- `.streamlit/secrets.supabase.example.toml`

The app runs on SQLite locally by default. When `SUPABASE_DB_URL` is configured in Streamlit secrets or `.env`, the app uses Supabase/Postgres instead. Use `supabase_schema.sql` for a new Supabase project. For a project created before the live-context and validation features, run `supabase_migration_20260718_model_context.sql` once before deploying the matching app update.
