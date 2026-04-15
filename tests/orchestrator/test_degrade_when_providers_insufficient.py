"""当有效 LLM provider 数不足时，Orchestrator 应降级并仅写入降级通知。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from app.config import AppConfig  # noqa: E402
from orchestrator.run_orchestrator import daily_run  # noqa: E402
from storage.db import open_sqlite_connection  # noqa: E402


def _count(conn, sql: str, params: tuple[object, ...] = ()) -> int:
    """执行 COUNT(*) SQL 并返回整数结果。"""
    row = conn.execute(sql, params).fetchone()
    assert row is not None
    return int(row["c"])


def test_degrade_when_effective_providers_insufficient(tmp_path: Path) -> None:
    """enabled_providers 有值但缺少 key/model 时，应触发降级且不生成 decisions/trades。"""
    db_path = tmp_path / "app.db"
    data_root = tmp_path / "data_root"
    data_root.mkdir(parents=True, exist_ok=True)

    # 注意：这里直接构造 AppConfig（不走 load_config），以便覆盖“不一致配置也能降级落库”的行为。
    config = AppConfig(
        telegram_bot_token="x",
        telegram_chat_id="x",
        data_root=str(data_root),
        sqlite_path=str(db_path),
        analysis_parallelism=2,
        dry_run=True,
        app_env="test",
        llm_enabled_providers=("openai", "anthropic", "gemini"),
        llm_min_effective_providers=3,
        # 故意不提供 api_key/model（默认为空），使 effective=0
    )

    run_id = daily_run(config=config, snapshot_date=None, providers=None, dry_run=True)

    conn = open_sqlite_connection(db_path)
    try:
        run_row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        assert run_row is not None
        assert run_row["status"] == "DEGRADED"

        decisions_count = _count(conn, "SELECT COUNT(*) AS c FROM decisions WHERE run_id = ?", (run_id,))
        trades_count = _count(conn, "SELECT COUNT(*) AS c FROM paper_trades WHERE run_id = ?", (run_id,))
        notifications_count = _count(conn, "SELECT COUNT(*) AS c FROM notifications WHERE run_id = ?", (run_id,))
    finally:
        conn.close()

    assert decisions_count == 0
    assert trades_count == 0
    assert notifications_count >= 1

