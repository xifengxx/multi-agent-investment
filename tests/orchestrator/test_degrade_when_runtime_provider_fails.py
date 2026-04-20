"""当运行期 provider 失败导致有效 provider 数不足时，Orchestrator 应降级。"""

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
from orchestrator.run_orchestrator import daily_run  # noqa: E402
from storage.db import open_sqlite_connection  # noqa: E402


class _PanelMockProvider(BaseLLMProvider):
    """返回固定 panel report JSON 的 Provider（用于降级场景集成测试）。"""

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
        snapshot_date = "2026-04-15"
        top10_prefix = "S" if instrument_type == "stock" else "E"
        metrics = {
            "avg_return_N": {"N1": 0.01, "N3": 0.02, "N5": 0.03, "N10": 0.05},
            "win_rate_N": {"N1": 0.6, "N3": 0.62, "N5": 0.64, "N10": 0.66},
            "avg_holding_return": 0.07,
            "max_drawdown": -0.05,
        }
        if instrument_type == "etf":
            metrics = {"avg_return_N": None, "win_rate_N": None, "avg_holding_return": None, "max_drawdown": None}

        report = {
            "schema_version": "v1",
            "instrument_type": instrument_type,
            "snapshot_date": snapshot_date,
            "provider": self._provider_name,
            "data_overview": {
                "sheet_count": 1,
                "date_range": {"start": snapshot_date, "end": snapshot_date},
                "symbol_count": 2,
                "data_completeness": {
                    "missing_price_ratio": 0.0 if instrument_type == "stock" else 1.0,
                    "missing_volume_ratio": 0.0 if instrument_type == "stock" else 1.0,
                    "missing_trend_ratio": 0.0,
                },
                "notes": ["ETF 缺 Price，收益类指标暂不计算"] if instrument_type == "etf" else [],
            },
            "reversal_patterns": [
                {
                    "pattern_id": "D:-1->0",
                    "pattern_name": "日线空头转静默",
                    "pattern_definition": "X_D_Trend_State 从 -1 变为 0",
                    "sample_count": 12,
                    "metrics": metrics,
                    "observations": ["o1"],
                }
            ],
            "multi_timeframe_analysis": {
                "needs_week_month_confirmation": ["n1"],
                "resonance_is_stronger": ["r1"],
                "lead_lag_relationships": ["l1"],
            },
            "signals": {"strong_entry_signals": ["s1"], "watch_entry_signals": ["w1"], "risk_signals": ["k1"]},
            "top10": [
                {
                    "symbol": f"{top10_prefix}{i}",
                    "signal_strength": "strong" if i <= 3 else "watch",
                    "confidence": 0.8,
                    "entry_logic_brief": "brief",
                    "entry_logic_detail": "detail",
                }
                for i in range(1, 11)
            ],
            "followups": ["f1"],
        }
        return json.dumps(report, ensure_ascii=False)


class _FailingProvider(BaseLLMProvider):
    """总是抛异常的 Provider，用于模拟运行期 429/超时等错误。"""

    @property
    def name(self) -> str:  # noqa: D401
        """返回 provider 名称。"""
        return "failing"

    @property
    def supports_file_input(self) -> bool:
        """声明支持文件输入，用于验证 file/text 分类错误记录。"""
        return True

    def invoke_with_files(self, *, prompt: str, file_paths: list[str]) -> str:
        """模拟附件上传调用失败。"""
        raise RuntimeError("file_upload_failed:simulated_provider_failure")

    def invoke(self, prompt: str) -> str:
        """模拟文本调用也失败。"""
        raise RuntimeError("text_fallback_failed:simulated_provider_failure")


def _write_minimal_xlsx(file_path: Path, *, sheet_date: str, rows: list[dict[str, object]]) -> None:
    """写入符合解析契约的最小 xlsx（包含必填列）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_date
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

    _write_minimal_xlsx(stock_file, sheet_date=snapshot_date, rows=[{"Symbol": "AAPL", "Description": "Apple"}])
    _write_minimal_xlsx(etf_file, sheet_date=snapshot_date, rows=[{"Symbol": "SPY", "Description": "SPDR"}])

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

    providers: list[BaseLLMProvider] = [_PanelMockProvider("p1"), _PanelMockProvider("p2"), _FailingProvider()]
    run_id = daily_run(config=config, snapshot_date=snapshot_date, providers=providers, dry_run=True)

    conn = open_sqlite_connection(db_path)
    try:
        run_row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        assert run_row is not None
        assert run_row["status"] == "DEGRADED"

        llm_count = _count(conn, "SELECT COUNT(*) AS c FROM llm_outputs WHERE run_id = ?", (run_id,))
        decisions_count = _count(conn, "SELECT COUNT(*) AS c FROM decisions WHERE run_id = ?", (run_id,))
        notifications_count = _count(conn, "SELECT COUNT(*) AS c FROM notifications WHERE run_id = ?", (run_id,))
        latest_notification = conn.execute(
            "SELECT message_text FROM notifications WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()

    assert llm_count > 0
    assert decisions_count == 0
    assert notifications_count >= 1
    assert latest_notification is not None
    message_text = str(latest_notification["message_text"])
    assert "runtime_errors=[" in message_text
    assert "failing.stock.file:file_upload_failed:simulated_provider_failure" in message_text
    assert "failing.stock.text:text_fallback_failed:simulated_provider_failure" in message_text
