PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    trigger_type TEXT NOT NULL CHECK (trigger_type IN ('manual_daily', 'scheduled_weekly')),
    snapshot_date TEXT,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'DEGRADED')),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_snapshot_date ON runs(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);

CREATE TABLE IF NOT EXISTS file_batches (
    batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    stock_file TEXT NOT NULL,
    etf_file TEXT NOT NULL,
    stock_sha256 TEXT NOT NULL,
    etf_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_file_batches_run_id ON file_batches(run_id);
CREATE INDEX IF NOT EXISTS idx_file_batches_snapshot_date ON file_batches(snapshot_date);

CREATE TABLE IF NOT EXISTS instrument_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL,
    snapshot_date TEXT NOT NULL,
    instrument_type TEXT NOT NULL CHECK (instrument_type IN ('stock', 'etf')),
    symbol TEXT NOT NULL,
    description TEXT NOT NULL,
    x_d_trend_state TEXT NOT NULL,
    x_d_state_bars INTEGER NOT NULL,
    x_w_trend_state TEXT NOT NULL,
    x_w_state_bars INTEGER NOT NULL,
    x_m_trend_state TEXT NOT NULL,
    x_m_state_bars INTEGER NOT NULL,
    price REAL,
    volume REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (batch_id) REFERENCES file_batches(batch_id)
);

CREATE INDEX IF NOT EXISTS idx_instrument_snapshots_lookup
ON instrument_snapshots(snapshot_date, instrument_type, symbol);
