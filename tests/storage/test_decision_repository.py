"""decisions 表 Schema 与仓储测试。"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


from storage.db import init_db, open_sqlite_connection  # noqa: E402


def _list_index_names(conn: sqlite3.Connection, table_name: str) -> set[str]:
    """查询指定表的索引名称集合。"""
    rows = conn.execute(f"PRAGMA index_list('{table_name}')").fetchall()
    return {str(row[1]) for row in rows}


def test_init_db_creates_decisions_table_and_indexes(tmp_path: Path) -> None:
    """初始化数据库后，应创建 decisions 表与关键索引。"""
    db_path = tmp_path / "app.db"
    conn = open_sqlite_connection(db_path)

    try:
        init_db(conn)
        table_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='decisions'"
        ).fetchall()
        index_names = _list_index_names(conn, "decisions")
    finally:
        conn.close()

    assert [row[0] for row in table_rows] == ["decisions"]
    assert "idx_decisions_run_lookup" in index_names
    assert "idx_decisions_symbol_lookup" in index_names


def test_decision_repository_can_insert_and_list_decisions(tmp_path: Path) -> None:
    """DecisionRepository 应能写入并读取 decisions 记录。"""
    try:
        from storage.repositories.decision_repository import DecisionRepository
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing module: storage.repositories.decision_repository ({exc})")

    db_path = tmp_path / "app.db"
    conn = open_sqlite_connection(db_path)

    try:
        init_db(conn)
        repo = DecisionRepository(conn)
        repo.insert_decisions(
            [
                {
                    "run_id": "run-001",
                    "snapshot_date": "2026-04-15",
                    "instrument_type": "stock",
                    "symbol": "AAPL",
                    "votes": 4,
                    "tier": 1,
                    "rank_in_list": 1,
                    "summary_rationale": "agent-1:buy;agent-2:buy",
                },
                {
                    "run_id": "run-001",
                    "snapshot_date": "2026-04-15",
                    "instrument_type": "stock",
                    "symbol": "MSFT",
                    "votes": 3,
                    "tier": 2,
                    "rank_in_list": 2,
                    "summary_rationale": "agent-1:buy",
                },
            ]
        )
        rows = repo.list_decisions(run_id="run-001", instrument_type="stock")
    finally:
        conn.close()

    assert [row["symbol"] for row in rows] == ["AAPL", "MSFT"]
    assert [row["tier"] for row in rows] == [1, 2]
