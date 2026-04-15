"""Mock Provider + 引擎 + 仓储 的集成流测试。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from analysis.llm_invoke_engine import LLMInvokeEngine  # noqa: E402
from app.config import AppConfig  # noqa: E402
from analysis.llm_output_validator import LLMOutputValidator  # noqa: E402
from analysis.prompt_builder import PromptBuilder  # noqa: E402
from analysis.providers.mock_provider import MockLLMProvider  # noqa: E402
from common.types import InstrumentRow  # noqa: E402
from storage.db import init_db, open_sqlite_connection  # noqa: E402
from storage.repositories.llm_repository import LLMRepository  # noqa: E402


def test_mock_provider_flow_persists_llm_outputs(tmp_path: Path) -> None:
    """执行一次 mock 分析流后，应将输出写入 llm_outputs。"""
    db_path = tmp_path / "app.db"
    conn = open_sqlite_connection(db_path)
    try:
        init_db(conn)
        repository = LLMRepository(conn)
        config = AppConfig(
            telegram_bot_token="x",
            telegram_chat_id="x",
            data_root="stock_data",
            sqlite_path=str(db_path),
            analysis_parallelism=2,
            dry_run=True,
            app_env="test",
        )
        engine = LLMInvokeEngine(
            provider=MockLLMProvider(),
            prompt_builder=PromptBuilder(),
            validator=LLMOutputValidator(),
            repository=repository,
            parallelism=config.analysis_parallelism,
        )

        rows = [
            InstrumentRow(
                snapshot_date="2026-04-15",
                instrument_type="stock",
                symbol="AAPL",
                description="Apple Inc.",
                x_d_trend_state="UP",
                x_d_state_bars=5,
                x_w_trend_state="UP",
                x_w_state_bars=3,
                x_m_trend_state="UP",
                x_m_state_bars=2,
                price=180.0,
                volume=1000.0,
            ),
            InstrumentRow(
                snapshot_date="2026-04-15",
                instrument_type="stock",
                symbol="TSLA",
                description="Tesla, Inc.",
                x_d_trend_state="DOWN",
                x_d_state_bars=2,
                x_w_trend_state="DOWN",
                x_w_state_bars=4,
                x_m_trend_state="UP",
                x_m_state_bars=1,
                price=150.0,
                volume=2000.0,
            ),
        ]

        results = engine.analyze_and_persist(
            run_id="run-001",
            snapshot_date="2026-04-15",
            instrument_type="stock",
            instruments=rows,
        )
        persisted = repository.list_outputs(run_id="run-001")
    finally:
        conn.close()

    assert len(results) == 2
    assert len(persisted) == 2
    assert {row["symbol"] for row in persisted} == {"AAPL", "TSLA"}
    assert all(row["provider"] == "mock" for row in persisted)
    assert all(int(row["is_valid"]) == 1 for row in persisted)
