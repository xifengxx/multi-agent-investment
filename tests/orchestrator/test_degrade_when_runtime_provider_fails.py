"""当运行期 provider 失败导致有效 provider 数不足时，Orchestrator 应降级。"""

from __future__ import annotations

import sys
from pathlib import Path

from openpyxl import Workbook


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from analysis.providers.base import BaseLLMProvider  # noqa: E402
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


class _FailingProvider(BaseLLMProvider):
    """总是抛异常的 Provider，用于模拟运行期 429/超时等错误。"""

    @property
    def name(self) -> str:  # noqa: D401
        """返回 provider 名称。"""
        return "failing"

    def invoke(self, prompt: str) -> str:
        """模拟调用失败。"""
        raise RuntimeError("simulated_provider_failure")


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


def _count(conn, sql: str, params: tuple[object, ...] = ()) -> int:
    """执行 COUNT(*) SQL 并返回整数结果。"""
    row = conn.execute(sql, params).fetchone()
    assert row is not None
    return int(row["c"])


def test_degrade_when_runtime_provider_fails_and_min_required_not_met(tmp_path: Path) -> None:
    """当 provider 列表包含失败 provider 且成功数量 < min_required 时，应降级且不产出决策。"""
    data_root = tmp_path / "data_root"
    data_root.mkdir(parents=True, exist_ok=True)

    snapshot_date = "2026-04-15"
    stock_file = data_root / f"{snapshot_date}_stock.xlsx"
    etf_file = data_root / f"{snapshot_date}_etf.xlsx"

    _write_minimal_xlsx(stock_file, rows=[{"Symbol": "AAPL", "Description": "Apple"}])
    _write_minimal_xlsx(etf_file, rows=[{"Symbol": "SPY", "Description": "SPDR"}])

    db_path = tmp_path / "app.db"
    config = AppConfig(
        telegram_bot_token="x",
        telegram_chat_id="x",
        data_root=str(data_root),
        sqlite_path=str(db_path),
        analysis_parallelism=1,
        dry_run=True,
        app_env="test",
        llm_min_effective_providers=3,
    )

    providers: list[BaseLLMProvider] = [_NamedMockProvider("p1"), _NamedMockProvider("p2"), _FailingProvider()]
    run_id = daily_run(config=config, snapshot_date=snapshot_date, providers=providers, dry_run=True)

    conn = open_sqlite_connection(db_path)
    try:
        run_row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        assert run_row is not None
        assert run_row["status"] == "DEGRADED"

        llm_count = _count(conn, "SELECT COUNT(*) AS c FROM llm_outputs WHERE run_id = ?", (run_id,))
        decisions_count = _count(conn, "SELECT COUNT(*) AS c FROM decisions WHERE run_id = ?", (run_id,))
        notifications_count = _count(conn, "SELECT COUNT(*) AS c FROM notifications WHERE run_id = ?", (run_id,))
    finally:
        conn.close()

    assert llm_count > 0
    assert decisions_count == 0
    assert notifications_count >= 1

