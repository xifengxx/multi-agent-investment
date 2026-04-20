"""MVP 主流水线端到端测试（tmp_path + openpyxl + dry_run）。"""

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
    """返回固定 panel report JSON 的 Provider（用于 E2E）。"""

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
                "symbol_count": 1,
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


class _FileFirstOnlyProvider(_PanelMockProvider):
    """模拟“仅文件调用成功”的 provider。"""

    def __init__(self, provider_name: str) -> None:
        """初始化调用计数器。"""
        super().__init__(provider_name)
        self.file_calls = 0
        self.text_calls = 0

    @property
    def supports_file_input(self) -> bool:
        """声明支持附件输入，驱动 orchestrator 先走文件分支。"""
        return True

    def invoke_with_files(self, *, prompt: str, file_paths: list[str]) -> str:
        """模拟文件路径可用时成功返回结构化结果。"""
        if not file_paths:
            raise RuntimeError("file_upload_failed:empty_file_paths")
        assert "input_panel_json:" not in prompt
        self.file_calls += 1
        return super().invoke(prompt)

    def invoke(self, prompt: str) -> str:
        """文本分支不应被调用；若调用则抛错。"""
        self.text_calls += 1
        raise RuntimeError("text_path_should_not_be_called")


class _FileThenFallbackProvider(_PanelMockProvider):
    """模拟“文件失败 -> 文本摘要回退成功”的 provider。"""

    def __init__(self, provider_name: str) -> None:
        """初始化调用计数器与 prompt 记录。"""
        super().__init__(provider_name)
        self.file_calls = 0
        self.text_calls = 0
        self.summary_prompts: list[str] = []

    @property
    def supports_file_input(self) -> bool:
        """声明支持附件输入，以覆盖 hybrid 路径。"""
        return True

    def invoke_with_files(self, *, prompt: str, file_paths: list[str]) -> str:
        """始终模拟附件上传失败。"""
        if not file_paths:
            raise RuntimeError("file_upload_failed:empty_file_paths")
        assert "input_panel_json:" not in prompt
        self.file_calls += 1
        raise RuntimeError("file_upload_failed:simulated")

    def invoke(self, prompt: str) -> str:
        """校验回退 prompt 为 summary 模式并返回有效 JSON。"""
        self.text_calls += 1
        if "mode=summary_fallback" not in prompt:
            raise RuntimeError("expected_summary_fallback_prompt")
        if "input_panel_summary_json:" not in prompt:
            raise RuntimeError("expected_panel_summary_payload")
        self.summary_prompts.append(prompt)
        return super().invoke(prompt)


def _write_minimal_xlsx(file_path: Path, *, sheet_date: str, rows: list[dict[str, object]]) -> None:
    """写入符合 ingestion.excel_parser 解析契约的最小 xlsx（包含必填列）。"""
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


def _prepare_minimal_file_pair(data_root: Path, *, snapshot_date: str) -> None:
    """在给定目录下写入一对最小 stock/etf 输入文件。"""
    stock_file = data_root / f"{snapshot_date}_stock.xlsx"
    etf_file = data_root / f"{snapshot_date}_etf.xlsx"
    _write_minimal_xlsx(
        stock_file,
        sheet_date=snapshot_date,
        rows=[
            {
                "Symbol": "AAPL",
                "Description": "Apple Inc.",
                "X_D_Trend_State": 1,
                "X_D_State_Bars": 5,
                "X_W_Trend_State": 1,
                "X_W_State_Bars": 3,
                "X_M_Trend_State": 1,
                "X_M_State_Bars": 2,
            }
        ],
    )
    _write_minimal_xlsx(
        etf_file,
        sheet_date=snapshot_date,
        rows=[
            {
                "Symbol": "SPY",
                "Description": "SPDR S&P 500 ETF",
                "X_D_Trend_State": 1,
                "X_D_State_Bars": 3,
                "X_W_Trend_State": 1,
                "X_W_State_Bars": 2,
                "X_M_Trend_State": 1,
                "X_M_State_Bars": 1,
            }
        ],
    )


def test_mvp_pipeline_daily_run_succeeds_and_persists_evidence_tables(tmp_path: Path) -> None:
    """dry_run 执行 daily_run 后，runs=SUCCEEDED 且核心表均应有数据。"""
    data_root = tmp_path / "data_root"
    data_root.mkdir(parents=True, exist_ok=True)

    snapshot_date = "2026-04-15"
    stock_file = data_root / f"{snapshot_date}_stock.xlsx"
    etf_file = data_root / f"{snapshot_date}_etf.xlsx"

    _write_minimal_xlsx(
        stock_file,
        sheet_date=snapshot_date,
        rows=[
            {
                "Symbol": "AAPL",
                "Description": "Apple Inc.",
                "X_D_Trend_State": 1,
                "X_D_State_Bars": 5,
                "X_W_Trend_State": 1,
                "X_W_State_Bars": 3,
                "X_M_Trend_State": 1,
                "X_M_State_Bars": 2,
            }
        ],
    )
    _write_minimal_xlsx(
        etf_file,
        sheet_date=snapshot_date,
        rows=[
            {
                "Symbol": "SPY",
                "Description": "SPDR S&P 500 ETF",
                "X_D_Trend_State": 1,
                "X_D_State_Bars": 3,
                "X_W_Trend_State": 1,
                "X_W_State_Bars": 2,
                "X_M_Trend_State": 1,
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
        providers=[_PanelMockProvider("p1"), _PanelMockProvider("p2"), _PanelMockProvider("p3")],
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
        notification_text_row = conn.execute(
            "SELECT message_text FROM notifications WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        trades_count = _count(conn, "SELECT COUNT(*) AS c FROM paper_trades WHERE run_id = ?", (run_id,))
        positions_count = _count(conn, "SELECT COUNT(*) AS c FROM positions")
    finally:
        conn.close()

    assert file_batches_count > 0
    assert snapshots_count > 0
    assert llm_outputs_count > 0
    assert decisions_count > 0
    assert notifications_count > 0
    assert notification_text_row is not None
    assert "http://127.0.0.1:8888/reports/" in notification_text_row["message_text"]
    assert trades_count > 0
    assert positions_count > 0

    summary_path = data_root / "reports" / snapshot_date / run_id / "summary.md"
    assert summary_path.exists()


def test_mvp_pipeline_file_first_then_fallback_paths(tmp_path: Path) -> None:
    """覆盖 hybrid 两条链路：文件优先成功 + 文件失败后文本摘要回退成功。"""
    snapshot_date = "2026-04-15"
    data_root = tmp_path / "data_root"
    data_root.mkdir(parents=True, exist_ok=True)
    _prepare_minimal_file_pair(data_root, snapshot_date=snapshot_date)

    file_first_provider = _FileFirstOnlyProvider("file-first-ok")
    db_file_first = tmp_path / "app_file_first.db"
    config_file_first = AppConfig(
        telegram_bot_token="x",
        telegram_chat_id="x",
        data_root=str(data_root),
        sqlite_path=str(db_file_first),
        analysis_parallelism=2,
        dry_run=True,
        llm_min_effective_providers=1,
        app_env="test",
    )
    run_id_file_first = daily_run(
        config=config_file_first,
        snapshot_date=snapshot_date,
        providers=[file_first_provider],
        dry_run=True,
    )
    conn = open_sqlite_connection(db_file_first)
    try:
        run_row = conn.execute("SELECT status FROM runs WHERE run_id = ?", (run_id_file_first,)).fetchone()
        llm_count = _count(conn, "SELECT COUNT(*) AS c FROM llm_outputs WHERE run_id = ?", (run_id_file_first,))
    finally:
        conn.close()
    assert run_row is not None
    assert run_row["status"] == "SUCCEEDED"
    assert llm_count > 0
    assert file_first_provider.file_calls == 2
    assert file_first_provider.text_calls == 0
    assert (data_root / "reports" / snapshot_date / run_id_file_first / "summary.md").exists()

    fallback_provider = _FileThenFallbackProvider("file-fallback-ok")
    db_fallback = tmp_path / "app_fallback.db"
    config_fallback = AppConfig(
        telegram_bot_token="x",
        telegram_chat_id="x",
        data_root=str(data_root),
        sqlite_path=str(db_fallback),
        analysis_parallelism=2,
        dry_run=True,
        llm_min_effective_providers=1,
        app_env="test",
    )
    run_id_fallback = daily_run(
        config=config_fallback,
        snapshot_date=snapshot_date,
        providers=[fallback_provider],
        dry_run=True,
    )
    conn = open_sqlite_connection(db_fallback)
    try:
        run_row = conn.execute("SELECT status FROM runs WHERE run_id = ?", (run_id_fallback,)).fetchone()
        llm_count = _count(conn, "SELECT COUNT(*) AS c FROM llm_outputs WHERE run_id = ?", (run_id_fallback,))
    finally:
        conn.close()
    assert run_row is not None
    assert run_row["status"] == "SUCCEEDED"
    assert llm_count > 0
    assert fallback_provider.file_calls == 2
    assert fallback_provider.text_calls == 2
    assert len(fallback_provider.summary_prompts) == 2
    assert (data_root / "reports" / snapshot_date / run_id_fallback / "summary.md").exists()
