"""日常 Orchestrator 的端到端流程测试（以 dry_run 运行）。"""

from __future__ import annotations

import sys
from pathlib import Path

from openpyxl import Workbook


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

import json  # noqa: E402

from analysis.providers.base import BaseLLMProvider  # noqa: E402
from app.config import AppConfig  # noqa: E402
from orchestrator.run_orchestrator import daily_run  # noqa: E402
from storage.db import open_sqlite_connection  # noqa: E402


class _PanelMockProvider(BaseLLMProvider):
    """返回固定 panel report JSON 的 Provider（用于集成测试）。"""

    def __init__(self, provider_name: str) -> None:
        self._provider_name = provider_name

    @property
    def name(self) -> str:
        return self._provider_name

    def invoke(self, prompt: str) -> str:
        """根据 prompt 里的 instrument_type 返回固定 JSON。"""
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


class _FileFirstFallbackProvider(_PanelMockProvider):
    """先触发文件上传失败，再通过文本调用返回有效 JSON。"""

    def __init__(self, provider_name: str) -> None:
        """初始化并记录是否尝试过附件调用。"""
        super().__init__(provider_name)
        self._file_attempted = False

    @property
    def supports_file_input(self) -> bool:
        """声明支持文件输入，驱动 orchestrator 先走附件分支。"""
        return True

    def invoke_with_files(self, *, prompt: str, file_paths: list[str]) -> str:
        """模拟附件上传失败，触发文本回退分支。"""
        assert "input_panel_json:" not in prompt
        self._file_attempted = True
        raise RuntimeError("file_upload_failed:simulated")

    def invoke(self, prompt: str) -> str:
        """仅在附件分支已尝试后才允许文本成功，确保“文件优先”被执行。"""
        if not self._file_attempted:
            raise RuntimeError("text_called_without_file_attempt")
        return super().invoke(prompt)


class _SummaryOnlyProvider(_PanelMockProvider):
    """supports_file_input=True 但 summary_only 模式下不应调用 invoke_with_files。"""

    @property
    def supports_file_input(self) -> bool:
        """声明支持文件输入，用于覆盖分支。"""
        return True

    def invoke_with_files(self, *, prompt: str, file_paths: list[str]) -> str:
        """若被调用说明分流策略失败。"""
        raise AssertionError("invoke_with_files_should_not_be_called")


def _write_stock_panel_xlsx(file_path: Path, *, sheet_date: str, rows: list[dict[str, object]]) -> None:
    """写入最小 stock panel xlsx（子表名为日期，包含 Price/Volume）。"""
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
        "Price",
        "Volume",
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
                row.get("Price", 0.0),
                row.get("Volume", 0.0),
            ]
        )
    wb.save(file_path)


def _write_etf_panel_xlsx(file_path: Path, *, sheet_date: str, rows: list[dict[str, object]]) -> None:
    """写入最小 etf panel xlsx（子表名为日期，不包含 Price/Volume）。"""
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


def test_daily_run_flow_persists_all_tables(tmp_path: Path) -> None:
    """跑一次 daily_run(dry_run=True) 应落库 runs/llm_outputs/decisions/notifications/ledger 等。"""
    data_root = tmp_path / "data_root"
    data_root.mkdir(parents=True, exist_ok=True)

    snapshot_date = "2026-04-15"
    stock_file = data_root / f"{snapshot_date}_stock.xlsx"
    etf_file = data_root / f"{snapshot_date}_etf.xlsx"

    _write_stock_panel_xlsx(
        stock_file,
        sheet_date=snapshot_date,
        rows=[
            {
                "Symbol": "AAPL",
                "Description": "Apple Inc.",
                "X_D_Trend_State": -1,
                "X_D_State_Bars": 5,
                "X_W_Trend_State": -1,
                "X_W_State_Bars": 3,
                "X_M_Trend_State": 1,
                "X_M_State_Bars": 2,
                "Price": 100.0,
                "Volume": 10_000,
            },
            {
                "Symbol": "MSFT",
                "Description": "Microsoft",
                "X_D_Trend_State": 1,
                "X_D_State_Bars": 4,
                "X_W_Trend_State": 0,
                "X_W_State_Bars": 2,
                "X_M_Trend_State": 1,
                "X_M_State_Bars": 1,
                "Price": 200.0,
                "Volume": 20_000,
            },
        ],
    )
    _write_etf_panel_xlsx(
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
            },
            {
                "Symbol": "QQQ",
                "Description": "Invesco QQQ Trust",
                "X_D_Trend_State": 0,
                "X_D_State_Bars": 2,
                "X_W_Trend_State": 0,
                "X_W_State_Bars": 2,
                "X_M_Trend_State": 1,
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
        llm_min_effective_providers=3,
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
        assert run_row["status"] in {"SUCCEEDED", "DEGRADED"}
        assert run_row["ended_at"] is not None

        llm_count = conn.execute("SELECT COUNT(*) AS c FROM llm_outputs WHERE run_id = ?", (run_id,)).fetchone()[
            "c"
        ]
        report_count = conn.execute("SELECT COUNT(*) AS c FROM llm_reports WHERE run_id = ?", (run_id,)).fetchone()[
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

    assert int(report_count) == 3 * 2
    assert int(llm_count) == 3 * 2 * 10

    if run_row["status"] == "DEGRADED":
        assert int(decisions_count) == 0
        assert int(notifications_count) == 0
        assert int(trades_count) == 0
        assert int(positions_count) == 0
    else:
        # stock/etf 各 10 个入榜
        assert int(decisions_count) == 20
        assert int(notifications_count) == 1
        assert int(trades_count) == 20
        assert int(positions_count) == 20


def test_daily_run_sets_runs_snapshot_date_when_not_provided(tmp_path: Path) -> None:
    """当未显式传入 snapshot_date 时，应将解析出的日期写回 runs.snapshot_date。"""
    data_root = tmp_path / "data_root"
    data_root.mkdir(parents=True, exist_ok=True)

    snapshot_date = "2026-04-15"
    stock_file = data_root / f"{snapshot_date}_stock.xlsx"
    etf_file = data_root / f"{snapshot_date}_etf.xlsx"

    _write_stock_panel_xlsx(
        stock_file,
        sheet_date=snapshot_date,
        rows=[{"Symbol": "AAPL", "Description": "Apple Inc.", "X_D_Trend_State": 1, "X_W_Trend_State": 1, "X_M_Trend_State": 1}],
    )
    _write_etf_panel_xlsx(
        etf_file,
        sheet_date=snapshot_date,
        rows=[{"Symbol": "SPY", "Description": "SPDR S&P 500 ETF", "X_D_Trend_State": 1, "X_W_Trend_State": 1, "X_M_Trend_State": 1}],
    )

    db_path = tmp_path / "app.db"
    config = AppConfig(
        telegram_bot_token="x",
        telegram_chat_id="x",
        data_root=str(data_root),
        sqlite_path=str(db_path),
        analysis_parallelism=1,
        dry_run=True,
        llm_min_effective_providers=3,
        app_env="test",
    )

    run_id = daily_run(
        config=config,
        snapshot_date=None,
        providers=[_PanelMockProvider("p1"), _PanelMockProvider("p2"), _PanelMockProvider("p3")],
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


def test_daily_run_fallback_to_text_when_file_upload_fails(tmp_path: Path) -> None:
    """当附件调用失败时，应自动回退文本调用并成功产出结果。"""
    data_root = tmp_path / "data_root"
    data_root.mkdir(parents=True, exist_ok=True)

    snapshot_date = "2026-04-15"
    stock_file = data_root / f"{snapshot_date}_stock.xlsx"
    etf_file = data_root / f"{snapshot_date}_etf.xlsx"

    _write_stock_panel_xlsx(
        stock_file,
        sheet_date=snapshot_date,
        rows=[{"Symbol": "AAPL", "Description": "Apple Inc.", "X_D_Trend_State": 1, "X_W_Trend_State": 1, "X_M_Trend_State": 1}],
    )
    _write_etf_panel_xlsx(
        etf_file,
        sheet_date=snapshot_date,
        rows=[{"Symbol": "SPY", "Description": "SPDR S&P 500 ETF", "X_D_Trend_State": 1, "X_W_Trend_State": 1, "X_M_Trend_State": 1}],
    )

    db_path = tmp_path / "app.db"
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

    run_id = daily_run(
        config=config,
        snapshot_date=snapshot_date,
        providers=[_FileFirstFallbackProvider("fallback-ok")],
        dry_run=True,
    )

    conn = open_sqlite_connection(db_path)
    try:
        run_row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        assert run_row is not None
        llm_count = conn.execute("SELECT COUNT(*) AS c FROM llm_outputs WHERE run_id = ?", (run_id,)).fetchone()["c"]
    finally:
        conn.close()

    assert run_row["status"] == "SUCCEEDED"
    assert int(llm_count) > 0


def test_daily_run_summary_only_skips_file_attempt(tmp_path: Path) -> None:
    """当 provider 被配置为 summary_only 时，即便支持文件输入也不应尝试附件。"""
    data_root = tmp_path / "data_root"
    data_root.mkdir(parents=True, exist_ok=True)

    snapshot_date = "2026-04-15"
    stock_file = data_root / f"{snapshot_date}_stock.xlsx"
    etf_file = data_root / f"{snapshot_date}_etf.xlsx"

    _write_stock_panel_xlsx(
        stock_file,
        sheet_date=snapshot_date,
        rows=[{"Symbol": "AAPL", "Description": "Apple Inc.", "X_D_Trend_State": 1, "X_W_Trend_State": 1, "X_M_Trend_State": 1}],
    )
    _write_etf_panel_xlsx(
        etf_file,
        sheet_date=snapshot_date,
        rows=[{"Symbol": "SPY", "Description": "SPDR S&P 500 ETF", "X_D_Trend_State": 1, "X_W_Trend_State": 1, "X_M_Trend_State": 1}],
    )

    db_path = tmp_path / "app.db"
    config = AppConfig(
        telegram_bot_token="x",
        telegram_chat_id="x",
        data_root=str(data_root),
        sqlite_path=str(db_path),
        analysis_parallelism=1,
        dry_run=True,
        llm_min_effective_providers=1,
        app_env="test",
        llm_provider_input_mode_overrides=(("p_summary", "summary_only"),),
    )

    run_id = daily_run(
        config=config,
        snapshot_date=snapshot_date,
        providers=[_SummaryOnlyProvider("p_summary")],
        dry_run=True,
    )
    assert run_id


def test_daily_run_does_not_fail_when_telegram_send_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Telegram 发送异常不应导致整次 run 标记 FAILED。"""
    data_root = tmp_path / "data_root"
    data_root.mkdir(parents=True, exist_ok=True)

    snapshot_date = "2026-04-15"
    stock_file = data_root / f"{snapshot_date}_stock.xlsx"
    etf_file = data_root / f"{snapshot_date}_etf.xlsx"
    _write_stock_panel_xlsx(
        stock_file,
        sheet_date=snapshot_date,
        rows=[{"Symbol": "AAPL", "Description": "Apple Inc.", "X_D_Trend_State": 1, "X_W_Trend_State": 1, "X_M_Trend_State": 1}],
    )
    _write_etf_panel_xlsx(
        etf_file,
        sheet_date=snapshot_date,
        rows=[{"Symbol": "SPY", "Description": "SPDR S&P 500 ETF", "X_D_Trend_State": 1, "X_W_Trend_State": 1, "X_M_Trend_State": 1}],
    )

    def _boom(self, text: str) -> dict[str, object]:
        raise RuntimeError("telegram_down")

    monkeypatch.setattr("orchestrator.run_orchestrator.TelegramAdapter.send_message", _boom)

    db_path = tmp_path / "app.db"
    config = AppConfig(
        telegram_bot_token="x",
        telegram_chat_id="x",
        data_root=str(data_root),
        sqlite_path=str(db_path),
        analysis_parallelism=1,
        dry_run=False,
        llm_min_effective_providers=1,
        app_env="test",
    )

    run_id = daily_run(
        config=config,
        snapshot_date=snapshot_date,
        providers=[_PanelMockProvider("p1")],
        dry_run=False,
    )

    conn = open_sqlite_connection(db_path)
    try:
        run_row = conn.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        notif_row = conn.execute(
            "SELECT status FROM notifications WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()

    assert run_row is not None
    assert run_row["status"] in {"SUCCEEDED", "DEGRADED"}
    assert notif_row is not None
    assert notif_row["status"] == "FAILED"
