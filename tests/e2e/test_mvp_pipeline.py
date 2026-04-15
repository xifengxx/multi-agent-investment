"""MVP 主流水线端到端测试（tmp_path + openpyxl + dry_run）。"""

from __future__ import annotations

import sys
from pathlib import Path

from openpyxl import Workbook


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from analysis.providers.mock_provider import MockLLMProvider  # noqa: E402
from app.config import AppConfig  # noqa: E402
from orchestrator.run_orchestrator import daily_run  # noqa: E402
from storage.db import open_sqlite_connection  # noqa: E402


class _NamedMockProvider(MockLLMProvider):
    """为 E2E 测试提供可区分 name 的 Mock Provider。"""

    def __init__(self, provider_name: str) -> None:
        """初始化并固定 provider 名称。"""
        super().__init__()
        self._provider_name = provider_name

    @property
    def name(self) -> str:  # noqa: D401
        """返回 provider 名称。"""
        return self._provider_name


def _write_minimal_xlsx(file_path: Path, *, rows: list[dict[str, object]]) -> None:
    """写入符合 ingestion.excel_parser 解析契约的最小 xlsx（包含必填列）。"""
    wb = Workbook()
    ws = wb.active
    headers = [
        "Symbol",
        "Description",
        "X_D_Trend_State",
        "X_D_State_Bars",
        "X_W_Trend_State",
        "X_W_State_Bars",
        "X_M_Trend_State",
        "X_M_State_Bars",
    ]
    ws.append(headers)
    for row in rows:
        ws.append(
            [
                row.get("Symbol", ""),
                row.get("Description", ""),
                row.get("X_D_Trend_State", ""),
                row.get("X_D_State_Bars", 0),
                row.get("X_W_Trend_State", ""),
                row.get("X_W_State_Bars", 0),
                row.get("X_M_Trend_State", ""),
                row.get("X_M_State_Bars", 0),
            ]
        )
    wb.save(file_path)


def _count(conn, sql: str, params: tuple[object, ...] = ()) -> int:
    """执行 COUNT(*) SQL 并返回整数结果。"""
    row = conn.execute(sql, params).fetchone()
    assert row is not None
    return int(row["c"])


def test_mvp_pipeline_daily_run_succeeds_and_persists_evidence_tables(tmp_path: Path) -> None:
    """dry_run 执行 daily_run 后，runs=SUCCEEDED 且核心表均应有数据。"""
    data_root = tmp_path / "data_root"
    data_root.mkdir(parents=True, exist_ok=True)

    snapshot_date = "2026-04-15"
    stock_file = data_root / f"{snapshot_date}_stock.xlsx"
    etf_file = data_root / f"{snapshot_date}_etf.xlsx"

    _write_minimal_xlsx(
        stock_file,
        rows=[
            {
                "Symbol": "AAPL",
                "Description": "Apple Inc.",
                "X_D_Trend_State": "UP",
                "X_D_State_Bars": 5,
                "X_W_Trend_State": "UP",
                "X_W_State_Bars": 3,
                "X_M_Trend_State": "UP",
                "X_M_State_Bars": 2,
            }
        ],
    )
    _write_minimal_xlsx(
        etf_file,
        rows=[
            {
                "Symbol": "SPY",
                "Description": "SPDR S&P 500 ETF",
                "X_D_Trend_State": "UP",
                "X_D_State_Bars": 3,
                "X_W_Trend_State": "UP",
                "X_W_State_Bars": 2,
                "X_M_Trend_State": "UP",
                "X_M_State_Bars": 1,
            }
        ],
    )

    db_path = tmp_path / "app.db"
    config = AppConfig(
        telegram_bot_token="x",
        telegram_chat_id="x",
        data_root=str(data_root),
        sqlite_path=str(db_path),
        analysis_parallelism=2,
        dry_run=True,
        app_env="test",
    )

    run_id = daily_run(
        config=config,
        snapshot_date=snapshot_date,
        providers=[_NamedMockProvider("p1"), _NamedMockProvider("p2"), _NamedMockProvider("p3")],
        dry_run=True,
    )

    conn = open_sqlite_connection(db_path)
    try:
        run_row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        assert run_row is not None
        assert run_row["status"] == "SUCCEEDED"
        assert run_row["ended_at"] is not None

        file_batches_count = _count(conn, "SELECT COUNT(*) AS c FROM file_batches WHERE run_id = ?", (run_id,))
        snapshots_count = _count(
            conn,
            """
            SELECT COUNT(*) AS c
            FROM instrument_snapshots s
            JOIN file_batches b ON b.batch_id = s.batch_id
            WHERE b.run_id = ?
            """,
            (run_id,),
        )
        llm_outputs_count = _count(conn, "SELECT COUNT(*) AS c FROM llm_outputs WHERE run_id = ?", (run_id,))
        decisions_count = _count(conn, "SELECT COUNT(*) AS c FROM decisions WHERE run_id = ?", (run_id,))
        notifications_count = _count(conn, "SELECT COUNT(*) AS c FROM notifications WHERE run_id = ?", (run_id,))
        trades_count = _count(conn, "SELECT COUNT(*) AS c FROM paper_trades WHERE run_id = ?", (run_id,))
        positions_count = _count(conn, "SELECT COUNT(*) AS c FROM positions")
    finally:
        conn.close()

    assert file_batches_count > 0
    assert snapshots_count > 0
    assert llm_outputs_count > 0
    assert decisions_count > 0
    assert notifications_count > 0
    assert trades_count > 0
    assert positions_count > 0

