"""Worker 抢占与执行行为测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from openpyxl import Workbook


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


from analysis.providers.base import BaseLLMProvider  # noqa: E402
from app.config import AppConfig  # noqa: E402
from storage.db import init_db, open_sqlite_connection  # noqa: E402
from storage.repositories.job_repository import JobRepository  # noqa: E402
from storage.repositories.run_repository import RunRepository  # noqa: E402
from storage.repositories.snapshot_repository import SnapshotRepository  # noqa: E402
from worker.job_worker import JobWorker  # noqa: E402


class _PanelMockProvider(BaseLLMProvider):
    """返回固定 panel report JSON 的 Provider（用于 Worker 测试）。"""

    def __init__(self, provider_name: str) -> None:
        """初始化并固定 provider 名称。"""
        self._provider_name = provider_name

    @property
    def name(self) -> str:  # noqa: D401
        """返回 provider 名称。"""
        return self._provider_name

    def invoke(self, prompt: str) -> str:
        """根据 prompt 判定 instrument_type 并输出符合 panel schema 的 JSON。"""
        instrument_type = "stock" if "instrument_type=stock" in prompt else "etf"
        snapshot_date = "2026-04-17"
        top10_prefix = "S" if instrument_type == "stock" else "E"
        report = {
            "schema_version": "v1",
            "instrument_type": instrument_type,
            "snapshot_date": snapshot_date,
            "provider": self._provider_name,
            "data_overview": {
                "sheet_count": 1,
                "date_range": {"start": snapshot_date, "end": snapshot_date},
                "symbol_count": 1,
                "data_completeness": {
                    "missing_price_ratio": 0.0 if instrument_type == "stock" else 1.0,
                    "missing_volume_ratio": 0.0 if instrument_type == "stock" else 1.0,
                    "missing_trend_ratio": 0.0,
                },
                "notes": [],
            },
            "reversal_patterns": [],
            "multi_timeframe_analysis": {
                "needs_week_month_confirmation": [],
                "resonance_is_stronger": [],
                "lead_lag_relationships": [],
            },
            "signals": {"strong_entry_signals": [], "watch_entry_signals": [], "risk_signals": []},
            "top10": [
                {
                    "symbol": f"{top10_prefix}{i}",
                    "signal_strength": "strong",
                    "confidence": 0.8,
                    "entry_logic_brief": "b",
                    "entry_logic_detail": "d",
                }
                for i in range(1, 11)
            ],
            "followups": [],
        }
        return json.dumps(report, ensure_ascii=False)


def _write_stock_xlsx(path: Path, *, sheet_date: str) -> None:
    """写入最小 stock xlsx（包含 Price/Volume）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_date
    ws.append(
        [
            "Symbol",
            "Description",
            "X_D_Trend_State",
            "X_D_State_Bars",
            "X_W_Trend_State",
            "X_W_State_Bars",
            "X_M_Trend_State",
            "X_M_State_Bars",
            "Price",
            "Volume",
        ]
    )
    ws.append(["AAPL", "Apple Inc.", 1, 1, 1, 1, 1, 1, 100.0, 10000])
    wb.save(path)


def _write_etf_xlsx(path: Path, *, sheet_date: str) -> None:
    """写入最小 etf xlsx（不包含 Price/Volume）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_date
    ws.append(
        [
            "Symbol",
            "Description",
            "X_D_Trend_State",
            "X_D_State_Bars",
            "X_W_Trend_State",
            "X_W_State_Bars",
            "X_M_Trend_State",
            "X_M_State_Bars",
        ]
    )
    ws.append(["SPY", "SPDR", 1, 1, 1, 1, 1, 1])
    wb.save(path)


def test_worker_claims_job_and_marks_done(tmp_path: Path) -> None:
    """worker 应能 claim job、执行 orchestrator，并标记 job DONE。"""
    data_root = tmp_path / "data_root"
    data_root.mkdir(parents=True, exist_ok=True)
    snapshot_date = "2026-04-17"
    stock = data_root / "inbox" / "upload-1" / f"{snapshot_date}_stock.xlsx"
    etf = data_root / "inbox" / "upload-1" / f"{snapshot_date}_etf.xlsx"
    stock.parent.mkdir(parents=True, exist_ok=True)
    _write_stock_xlsx(stock, sheet_date=snapshot_date)
    _write_etf_xlsx(etf, sheet_date=snapshot_date)

    db_path = tmp_path / "app.db"
    conn = open_sqlite_connection(db_path)
    try:
        init_db(conn)
        RunRepository(conn).create_run(
            run_id="run-1",
            trigger_type="manual_daily",
            snapshot_date=snapshot_date,
            status="RUNNING",
            started_at="2026-04-17T00:00:00+00:00",
        )
        SnapshotRepository(conn).create_file_batch(
            run_id="run-1",
            snapshot_date=snapshot_date,
            stock_file=str(stock),
            etf_file=str(etf),
            stock_sha256="x",
            etf_sha256="y",
        )
        JobRepository(conn).enqueue(run_id="run-1", snapshot_date=snapshot_date)
    finally:
        conn.close()

    cfg = AppConfig(
        telegram_bot_token="x",
        telegram_chat_id="x",
        data_root=str(data_root),
        sqlite_path=str(db_path),
        analysis_parallelism=1,
        dry_run=True,
        app_env="test",
        llm_min_effective_providers=1,
        worker_poll_interval_seconds=1,
        worker_max_attempts=3,
    )

    worker = JobWorker(worker_id="w1", config=cfg, providers=[_PanelMockProvider("p1")])
    worker.run_once()

    db = open_sqlite_connection(db_path)
    try:
        job_row = db.execute("SELECT status FROM jobs LIMIT 1").fetchone()
        run_row = db.execute("SELECT status FROM runs WHERE run_id = 'run-1'").fetchone()
    finally:
        db.close()

    assert job_row is not None
    assert job_row["status"] == "DONE"
    assert run_row is not None
    assert run_row["status"] == "SUCCEEDED"
    assert (data_root / "reports" / snapshot_date / "run-1" / "summary.md").exists()

