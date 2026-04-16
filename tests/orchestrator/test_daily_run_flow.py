"""日常 Orchestrator 的端到端流程测试（以 dry_run 运行）。"""

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
    """为测试提供可区分 name 的 Mock Provider。"""

    def __init__(self, provider_name: str) -> None:
        """初始化并固定 provider 名称。"""
        super().__init__()
        self._provider_name = provider_name

    @property
    def name(self) -> str:  # noqa: D401
        """返回 provider 名称。"""
        return self._provider_name


def _write_minimal_xlsx(file_path: Path, *, rows: list[dict[str, object]]) -> None:
    """写入符合解析契约的最小 xlsx（包含必填列）。"""
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


def test_daily_run_flow_persists_all_tables(tmp_path: Path) -> None:
    """跑一次 daily_run(dry_run=True) 应落库 runs/llm_outputs/decisions/notifications/ledger 等。"""
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
            },
            {
                "Symbol": "MSFT",
                "Description": "Microsoft",
                "X_D_Trend_State": "UP",
                "X_D_State_Bars": 4,
                "X_W_Trend_State": "UP",
                "X_W_State_Bars": 2,
                "X_M_Trend_State": "UP",
                "X_M_State_Bars": 1,
            },
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
            },
            {
                "Symbol": "QQQ",
                "Description": "Invesco QQQ Trust",
                "X_D_Trend_State": "UP",
                "X_D_State_Bars": 2,
                "X_W_Trend_State": "UP",
                "X_W_State_Bars": 2,
                "X_M_Trend_State": "UP",
                "X_M_State_Bars": 1,
            },
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
        assert run_row["status"] in {"SUCCEEDED", "DEGRADED"}
        assert run_row["ended_at"] is not None

        llm_count = conn.execute("SELECT COUNT(*) AS c FROM llm_outputs WHERE run_id = ?", (run_id,)).fetchone()[
            "c"
        ]
        decisions_count = conn.execute(
            "SELECT COUNT(*) AS c FROM decisions WHERE run_id = ?", (run_id,)
        ).fetchone()["c"]
        notifications_count = conn.execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE run_id = ?", (run_id,)
        ).fetchone()["c"]
        trades_count = conn.execute(
            "SELECT COUNT(*) AS c FROM paper_trades WHERE run_id = ?", (run_id,)
        ).fetchone()["c"]
        positions_count = conn.execute("SELECT COUNT(*) AS c FROM positions").fetchone()["c"]
    finally:
        conn.close()

    # 2 stocks + 2 etfs, 每个 provider 都会跑一遍并写入 llm_outputs
    assert int(llm_count) == 3 * 4

    if run_row["status"] == "DEGRADED":
        assert int(decisions_count) == 0
        assert int(notifications_count) == 0
        assert int(trades_count) == 0
        assert int(positions_count) == 0
    else:
        # 每个标的在 3 票 BUY 下应入榜，故 4 条 decisions + 4 条 BUY trades + 4 个 positions
        assert int(decisions_count) == 4
        assert int(notifications_count) == 1
        assert int(trades_count) == 4
        assert int(positions_count) == 4


def test_daily_run_sets_runs_snapshot_date_when_not_provided(tmp_path: Path) -> None:
    """当未显式传入 snapshot_date 时，应将解析出的日期写回 runs.snapshot_date。"""
    data_root = tmp_path / "data_root"
    data_root.mkdir(parents=True, exist_ok=True)

    snapshot_date = "2026-04-15"
    stock_file = data_root / f"{snapshot_date}_stock.xlsx"
    etf_file = data_root / f"{snapshot_date}_etf.xlsx"

    _write_minimal_xlsx(stock_file, rows=[{"Symbol": "AAPL", "Description": "Apple Inc."}])
    _write_minimal_xlsx(etf_file, rows=[{"Symbol": "SPY", "Description": "SPDR S&P 500 ETF"}])

    db_path = tmp_path / "app.db"
    config = AppConfig(
        telegram_bot_token="x",
        telegram_chat_id="x",
        data_root=str(data_root),
        sqlite_path=str(db_path),
        analysis_parallelism=1,
        dry_run=True,
        app_env="test",
    )

    run_id = daily_run(
        config=config,
        snapshot_date=None,
        providers=[_NamedMockProvider("p1"), _NamedMockProvider("p2"), _NamedMockProvider("p3")],
        dry_run=True,
    )

    conn = open_sqlite_connection(db_path)
    try:
        run_row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        assert run_row is not None
        batch_row = conn.execute("SELECT snapshot_date FROM file_batches WHERE run_id = ?", (run_id,)).fetchone()
        assert batch_row is not None
    finally:
        conn.close()

    assert run_row["snapshot_date"] == batch_row["snapshot_date"]
