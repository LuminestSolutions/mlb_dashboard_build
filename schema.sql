CREATE TABLE IF NOT EXISTS games (
    game_id TEXT PRIMARY KEY,
    game_date TEXT NOT NULL,
    away_team TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team_id INTEGER,
    home_team_id INTEGER,
    away_team_name TEXT,
    home_team_name TEXT,
    venue_name TEXT,
    venue_lat REAL,
    venue_lon REAL,
    away_probable_pitcher TEXT,
    home_probable_pitcher TEXT,
    status TEXT,
    status_state TEXT,
    status_code TEXT,
    away_score INTEGER,
    home_score INTEGER,
    inning_state TEXT,
    current_inning TEXT,
    score_summary TEXT,
    box_score_summary TEXT,
    first_pitch_utc TEXT,
    away_lineup_status TEXT DEFAULT 'Pending',
    home_lineup_status TEXT DEFAULT 'Pending',
    away_lineup_json TEXT,
    home_lineup_json TEXT,
    away_injury_count INTEGER DEFAULT 0,
    home_injury_count INTEGER DEFAULT 0,
    away_injured_hitters INTEGER DEFAULT 0,
    home_injured_hitters INTEGER DEFAULT 0,
    away_injuries_json TEXT,
    home_injuries_json TEXT,
    away_bullpen_pitches_1d INTEGER DEFAULT 0,
    home_bullpen_pitches_1d INTEGER DEFAULT 0,
    away_bullpen_pitches_3d INTEGER DEFAULT 0,
    home_bullpen_pitches_3d INTEGER DEFAULT 0,
    away_bullpen_status TEXT DEFAULT 'Unknown',
    home_bullpen_status TEXT DEFAULT 'Unknown',
    pitcher_change_detected INTEGER DEFAULT 0,
    pitcher_change_details TEXT,
    weather_risk_level TEXT DEFAULT 'Unknown',
    context_updated_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS odds_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    bookmaker TEXT,
    market TEXT NOT NULL,
    away_price INTEGER,
    home_price INTEGER,
    total_points REAL,
    over_price INTEGER,
    under_price INTEGER,
    captured_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(game_id) REFERENCES games(game_id)
);

CREATE TABLE IF NOT EXISTS model_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    run_date TEXT NOT NULL,
    recommended_side TEXT,
    best_market TEXT,
    fair_line INTEGER,
    market_line INTEGER,
    edge_pct REAL,
    confidence REAL,
    pitching_score REAL,
    bullpen_score REAL,
    offense_score REAL,
    lineup_score REAL,
    weather_score REAL,
    situation_score REAL,
    total_score REAL,
    recommendation TEXT,
    reason TEXT,
    model_version TEXT,
    model_probability REAL,
    rule_profile TEXT,
    rule_checks_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(game_id) REFERENCES games(game_id)
);

CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bet_date TEXT NOT NULL,
    game_id TEXT,
    bet_label TEXT NOT NULL,
    market TEXT NOT NULL,
    odds INTEGER NOT NULL,
    stake_units REAL NOT NULL,
    result TEXT DEFAULT 'OPEN',
    profit_loss_units REAL DEFAULT 0,
    closing_line INTEGER,
    clv_cents INTEGER,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS refresh_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    refresh_started_at TEXT NOT NULL,
    refresh_completed_at TEXT,
    status TEXT NOT NULL,
    games_loaded INTEGER DEFAULT 0,
    odds_loaded INTEGER DEFAULT 0,
    weather_loaded INTEGER DEFAULT 0,
    errors TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS live_odds_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    commence_time TEXT,
    away_team TEXT,
    home_team TEXT,
    bookmaker TEXT,
    market TEXT NOT NULL,
    outcomes_json TEXT NOT NULL,
    captured_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS model_prediction_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    run_date TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_probability REAL,
    recommended_side TEXT,
    fair_line INTEGER,
    market_line INTEGER,
    edge_pct REAL,
    confidence REAL,
    recommendation TEXT,
    rule_profile TEXT,
    rule_checks_json TEXT,
    captured_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(game_id) REFERENCES games(game_id)
);

CREATE TABLE IF NOT EXISTS team_availability_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL,
    team_name TEXT,
    injured_count INTEGER DEFAULT 0,
    injured_hitters INTEGER DEFAULT 0,
    injured_pitchers INTEGER DEFAULT 0,
    injuries_json TEXT,
    captured_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS game_context_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    away_lineup_status TEXT,
    home_lineup_status TEXT,
    away_lineup_json TEXT,
    home_lineup_json TEXT,
    away_injury_count INTEGER DEFAULT 0,
    home_injury_count INTEGER DEFAULT 0,
    away_injured_hitters INTEGER DEFAULT 0,
    home_injured_hitters INTEGER DEFAULT 0,
    away_injuries_json TEXT,
    home_injuries_json TEXT,
    away_bullpen_pitches_1d INTEGER DEFAULT 0,
    home_bullpen_pitches_1d INTEGER DEFAULT 0,
    away_bullpen_pitches_3d INTEGER DEFAULT 0,
    home_bullpen_pitches_3d INTEGER DEFAULT 0,
    away_bullpen_status TEXT,
    home_bullpen_status TEXT,
    pitcher_change_detected INTEGER DEFAULT 0,
    pitcher_change_details TEXT,
    weather_risk_level TEXT,
    captured_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(game_id) REFERENCES games(game_id)
);

CREATE TABLE IF NOT EXISTS retrosheet_games (
    retro_game_id TEXT PRIMARY KEY,
    season INTEGER NOT NULL,
    game_date TEXT NOT NULL,
    away_team TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_score INTEGER NOT NULL,
    home_score INTEGER NOT NULL,
    winner TEXT,
    game_type TEXT,
    imported_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS retrosheet_backtests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    retro_game_id TEXT NOT NULL,
    season INTEGER NOT NULL,
    model_version TEXT NOT NULL,
    predicted_home_probability REAL NOT NULL,
    predicted_side TEXT NOT NULL,
    confidence REAL NOT NULL,
    actual_winner TEXT NOT NULL,
    correct INTEGER NOT NULL,
    brier_score REAL NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(retro_game_id) REFERENCES retrosheet_games(retro_game_id)
);

CREATE TABLE IF NOT EXISTS weather_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    venue_name TEXT,
    forecast_time TEXT,
    temperature_f REAL,
    wind_speed_mph REAL,
    wind_direction_deg REAL,
    precipitation_probability REAL,
    weather_summary TEXT,
    captured_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(game_id) REFERENCES games(game_id)
);

CREATE TABLE IF NOT EXISTS team_statcast_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team TEXT NOT NULL,
    team_abbr TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    plate_appearances INTEGER DEFAULT 0,
    pitches_seen INTEGER DEFAULT 0,
    xwoba_for REAL,
    woba_for REAL,
    hard_hit_pct_for REAL,
    barrel_pct_for REAL,
    k_pct_for REAL,
    bb_pct_for REAL,
    xwoba_allowed REAL,
    woba_allowed REAL,
    hard_hit_pct_allowed REAL,
    barrel_pct_allowed REAL,
    k_pct_pitching REAL,
    bb_pct_pitching REAL,
    offense_score REAL,
    pitching_score REAL,
    confluence_score REAL,
    captured_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_prediction_history_game_time ON model_prediction_history(game_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_game_context_history_game_time ON game_context_snapshots(game_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_live_odds_history_matchup ON live_odds_snapshots(market, away_team, home_team, captured_at);
CREATE INDEX IF NOT EXISTS idx_retrosheet_backtests_season ON retrosheet_backtests(season, model_version);
