"""复用既有 run_id 的日常执行入口测试。"""

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
from orchestrator.run_orchestrator import daily_run_for_existing_run  # noqa: E402
from storage.db import init_db, open_sqlite_connection  # noqa: E402
from storage.repositories.run_repository import RunRepository  # noqa: E402


class _PanelMockProvider(BaseLLMProvider):
    """返回固定 panel report JSON 的 Provider（用于集成测试）。"""

    def __init__(self, provider_name: str) -> None:
        """初始化并固定 provider 名称。"""
        self._provider_name = provider_name

    @property
    def name(self) -> str:  # noqa: D401
        """返回 provider 名称。"""
        return self._provider_name

    def invoke(self, prompt: str) -> str:
        """根据 prompt 里的 instrument_type 返回固定 JSON。"""
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
                    "entry_logic_brief": "brief",
                    "entry_logic_detail": "detail",
                }
                for i in range(1, 11)
            ],
            "followups": [],
        }
        return json.dumps(report, ensure_ascii=False)


def _write_stock_panel_xlsx(file_path: Path, *, sheet_date: str) -> None:
    """写入最小 stock panel xlsx（子表名为日期，包含 Price/Volume）。"""
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
    wb.save(file_path)


def _write_etf_panel_xlsx(file_path: Path, *, sheet_date: str) -> None:
    """写入最小 etf panel xlsx（子表名为日期，不包含 Price/Volume）。"""
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
    ws.append(["SPY", "SPDR S&P 500 ETF", 1, 1, 1, 1, 1, 1])
    wb.save(file_path)


def test_daily_run_for_existing_run_id_uses_given_file_paths(tmp_path: Path) -> None:
    """应能复用既有 run_id，并从指定文件路径执行 daily 流程。"""
    data_root = tmp_path / "data_root"
    data_root.mkdir(parents=True, exist_ok=True)
    snapshot_date = "2026-04-17"

    stock_file = data_root / "inbox" / "upload-1" / f"{snapshot_date}_stock.xlsx"
    etf_file = data_root / "inbox" / "upload-1" / f"{snapshot_date}_etf.xlsx"
    stock_file.parent.mkdir(parents=True, exist_ok=True)
    _write_stock_panel_xlsx(stock_file, sheet_date=snapshot_date)
    _write_etf_panel_xlsx(etf_file, sheet_date=snapshot_date)

    db_path = tmp_path / "app.db"
    conn = open_sqlite_connection(db_path)
    try:
        init_db(conn)
        RunRepository(conn).create_run(
            run_id="run-existing",
            trigger_type="manual_daily",
            snapshot_date=snapshot_date,
            status="RUNNING",
            started_at="2026-04-17T00:00:00+00:00",
        )
    finally:
        conn.close()

    config = AppConfig(
        telegram_bot_token="x",
        telegram_chat_id="x",
        data_root=str(data_root),
        sqlite_path=str(db_path),
        analysis_parallelism=1,
        dry_run=True,
        llm_min_effective_providers=1,
        app_env="test",
    )

    run_id = daily_run_for_existing_run(
        config=config,
        run_id="run-existing",
        snapshot_date=snapshot_date,
        stock_file_path=str(stock_file),
        etf_file_path=str(etf_file),
        providers=[_PanelMockProvider("p1")],
        dry_run=True,
    )

    assert run_id == "run-existing"
    db = open_sqlite_connection(db_path)
    try:
        row = db.execute(
            "SELECT status, error_message FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        db.close()
    assert row is not None
    assert row["status"] == "SUCCEEDED", f"run failed: {row['error_message']}"
    assert (data_root / "reports" / snapshot_date / run_id / "summary.md").exists()
