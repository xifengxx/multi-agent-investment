"""SQLite 初始化与 runs 仓储测试。"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from storage.db import init_db, open_sqlite_connection  # noqa: E402
from storage.repositories.run_repository import RunRepository  # noqa: E402


def _list_index_names(conn: sqlite3.Connection, table_name: str) -> set[str]:
    """查询指定表的索引名称集合。"""
    rows = conn.execute(f"PRAGMA index_list('{table_name}')").fetchall()
    return {str(row[1]) for row in rows}


def test_init_db_creates_runs_table_and_indexes(tmp_path: Path) -> None:
    """初始化数据库后，应创建 runs 表与关键索引。"""
    db_path = tmp_path / "app.db"
    conn = open_sqlite_connection(db_path)

    try:
        init_db(conn)
        table_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='runs'"
        ).fetchall()
        index_names = _list_index_names(conn, "runs")
    finally:
        conn.close()

    assert [row[0] for row in table_rows] == ["runs"]
    assert "idx_runs_snapshot_date" in index_names
    assert "idx_runs_status" in index_names


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    """重复执行初始化时，应保持幂等且不报错。"""
    db_path = tmp_path / "app.db"
    conn = open_sqlite_connection(db_path)

    try:
        init_db(conn)
        init_db(conn)
        table_count = conn.execute(
            "SELECT COUNT(1) FROM sqlite_master WHERE type='table' AND name='runs'"
        ).fetchone()
    finally:
        conn.close()

    assert table_count is not None
    assert table_count[0] == 1


def test_run_repository_can_insert_and_query_run(tmp_path: Path) -> None:
    """RunRepository 应能写入并读取 runs 记录。"""
    db_path = tmp_path / "app.db"
    conn = open_sqlite_connection(db_path)

    try:
        init_db(conn)
        repository = RunRepository(conn)
        repository.create_run(
            run_id="run-001",
            trigger_type="manual_daily",
            snapshot_date="2026-04-15",
            status="RUNNING",
            started_at="2026-04-15T09:00:00+08:00",
        )
        run_row = repository.get_run("run-001")
    finally:
        conn.close()

    assert run_row is not None
    assert run_row["run_id"] == "run-001"
    assert run_row["trigger_type"] == "manual_daily"
    assert run_row["status"] == "RUNNING"
