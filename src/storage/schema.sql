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

CREATE TABLE IF NOT EXISTS llm_outputs (
    output_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    instrument_type TEXT NOT NULL CHECK (instrument_type IN ('stock', 'etf')),
    symbol TEXT NOT NULL,
    provider TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    json_text TEXT,
    is_valid INTEGER NOT NULL CHECK (is_valid IN (0, 1)),
    quality_flags TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_llm_outputs_lookup
ON llm_outputs(run_id, snapshot_date, instrument_type, symbol);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    instrument_type TEXT NOT NULL CHECK (instrument_type IN ('stock', 'etf')),
    symbol TEXT NOT NULL,
    votes INTEGER NOT NULL,
    tier INTEGER NOT NULL CHECK (tier IN (1, 2, 3)),
    rank_in_list INTEGER NOT NULL,
    summary_rationale TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 决策查询通常按 run_id+日期+类型读取榜单
CREATE INDEX IF NOT EXISTS idx_decisions_run_lookup
ON decisions(run_id, snapshot_date, instrument_type, rank_in_list);

-- 方便按标的回溯决策记录
CREATE INDEX IF NOT EXISTS idx_decisions_symbol_lookup
ON decisions(snapshot_date, instrument_type, symbol);

-- 通知发送记录（Telegram 等）
CREATE TABLE IF NOT EXISTS notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    message_text TEXT NOT NULL,
    dry_run INTEGER NOT NULL CHECK (dry_run IN (0, 1)),
    status TEXT NOT NULL CHECK (status IN ('SENT', 'FAILED', 'SKIPPED')),
    provider_response_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_notifications_run_id ON notifications(run_id);
CREATE INDEX IF NOT EXISTS idx_notifications_created_at ON notifications(created_at);

-- 纸面交易明细（Paper Trading）
CREATE TABLE IF NOT EXISTS paper_trades (
    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    instrument_type TEXT NOT NULL CHECK (instrument_type IN ('stock', 'etf')),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    qty INTEGER NOT NULL,
    price REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_run_id ON paper_trades(run_id);
CREATE INDEX IF NOT EXISTS idx_paper_trades_lookup ON paper_trades(snapshot_date, instrument_type, symbol);

-- 持仓快照（累积持仓，qty 可正可负；当前策略只会累加 BUY）
CREATE TABLE IF NOT EXISTS positions (
    position_id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_type TEXT NOT NULL CHECK (instrument_type IN ('stock', 'etf')),
    symbol TEXT NOT NULL,
    qty INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(instrument_type, symbol)
);

CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);
