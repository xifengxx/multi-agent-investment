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
