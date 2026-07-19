# Deploy MLB Edge Dashboard With Supabase + Streamlit

This is the deployment path for hosting the dashboard while still editing from this computer.

## Current State

The app runs locally on SQLite by default. If `SUPABASE_DB_URL` is set in Streamlit secrets or `.env`, the app switches to Supabase/Postgres for its database connection.

Use this as the rollout order:

1. Create GitHub repo.
2. Create Supabase project and run `supabase_schema.sql`.
3. Add Streamlit secrets.
4. Deploy to Streamlit Community Cloud.
5. Add the public subdomain.

## Files To Commit

Commit these:

- `app.py`
- `schema.sql`
- `supabase_schema.sql`
- `supabase_migration_20260718_model_context.sql`
- `requirements.txt`
- `src/`
- `data/branding/`
- `.streamlit/secrets.example.toml`
- `.streamlit/secrets.supabase.example.toml`
- `README.md`

Do not commit these:

- `.env`
- `.streamlit/secrets.toml`
- `.venv/`
- `data/mlb_edge.db`
- `data/pybaseball_cache/`
- `data/matplotlib_config/`
- `__pycache__/`

## Supabase Setup

1. Create a Supabase account.
2. Create a new project.
3. Open SQL Editor.
4. Paste and run `supabase_schema.sql`.
5. Go to Project Settings > Database.
6. Copy the Postgres connection string.
7. Save the database password somewhere safe.

### Upgrade An Existing Project

If this Supabase project already contains the dashboard tables, do not rerun the full schema as your upgrade step. Before pushing the updated app:

1. Open Supabase SQL Editor.
2. Open `supabase_migration_20260718_model_context.sql` from this app folder.
3. Paste the entire file into a new SQL query and run it once.
4. Confirm that the query completes without errors.
5. Push the app changes to GitHub and allow Streamlit to redeploy.

The migration is additive and uses `IF NOT EXISTS`, so it preserves existing games, bets, settings, and recommendations. It creates the live-context, prediction-history, Retrosheet, and historical-export support tables required by the updated dashboard. If you ran an earlier copy of this migration before Excel exports were added, run the current file again once; the new statements are safe to apply to the existing project.

For hosted Streamlit, use a pooled connection string if Supabase offers one for your project.

Supabase's docs describe each project as a full Postgres database. For hosted apps, prefer a Supavisor pooler connection string when available.

After the schema has been run once, leave this Streamlit secret unset or set to `"false"`:

```toml
RUN_SUPABASE_SCHEMA_ON_START = "false"
```

That keeps the app from running DDL on every Streamlit startup.

## Streamlit Community Cloud Setup

1. Push this app folder to GitHub.
2. Go to Streamlit Community Cloud.
3. Click Create app.
4. Select the GitHub repo.
5. Set the main file path to:

```text
app.py
```

If the repo root is above this folder, use:

```text
mlb_dashboard_build_package/app/app.py
```

6. In Advanced settings, add secrets using `.streamlit/secrets.supabase.example.toml` as the template.
7. Deploy.

The app will use SQLite if `SUPABASE_DB_URL` is missing. It will use Supabase/Postgres when that secret is present.

## Secrets To Add In Streamlit

```toml
ODDS_API_KEY = "your-odds-api-key"
MLB_STATS_API_MARKER = ""
SUPABASE_DB_URL = "postgresql://postgres.your-project-ref:your-password@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
RUN_SUPABASE_SCHEMA_ON_START = "false"
PYBASEBALL_CACHE = "data/pybaseball_cache"
MPLCONFIGDIR = "data/matplotlib_config"
DASHBOARD_PASSWORD = ""
```

Never put real secrets in GitHub.

## Subdomain

Streamlit Community Cloud gives apps a `streamlit.app` URL and lets you manage apps from your workspace. Use the app settings to set the app URL/name if available for your account.

Recommended first URL:

```text
mlb-edge-model.streamlit.app
```

If you want a custom domain like:

```text
dashboard.yourdomain.com
```

we should plan that as a separate hosting layer. Streamlit Community Cloud is easiest for a `streamlit.app` subdomain; a private custom domain may require another host/proxy depending on your domain provider and Streamlit account options.

## Local Editing Workflow

From this computer:

```bash
git add .
git commit -m "Update MLB edge dashboard"
git push
```

Streamlit redeploys from GitHub after you push.

After the first updated deploy, open Data Health and confirm that Model Engine shows `statcast_context_v2.0`. Then run `Refresh MLB Model` to populate live lineup, injury, bullpen, weather-risk, odds-history, and prediction-audit data. Import a Retrosheet season separately from Performance > Retrosheet backtest; a local SQLite import is not automatically copied to Supabase.

Use Data & Sync > Historical export to download a day, month, or custom range. Treat Supabase as the hosted system of record. On a free Supabase project, create regular off-site database dumps or Excel archives; paid projects can also use Supabase-managed daily backups, with Point-in-Time Recovery available separately. Streamlit Community Cloud apps can hibernate, so do not rely on the Streamlit process itself for guaranteed unattended refreshes.

## Local Supabase Test

After adding `SUPABASE_DB_URL` to `.env`, reinstall requirements and start the app:

```bash
pip install -r requirements.txt
streamlit run app.py
```

If the app starts and the Settings page loads, the database adapter is reading Supabase successfully.
