"""Schema 新增表结构测试（Task6）。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


from storage.db import init_db, open_sqlite_connection  # noqa: E402


def _table_exists(conn, name: str) -> bool:  # type: ignore[no-untyped-def]
    """判断表是否存在。"""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def test_init_db_creates_task6_tables(tmp_path: Path) -> None:
    """初始化数据库后，应创建 notifications / paper_trades / positions 表。"""
    db_path = tmp_path / "app.db"
    conn = open_sqlite_connection(db_path)
    try:
        init_db(conn)
        assert _table_exists(conn, "notifications")
        assert _table_exists(conn, "paper_trades")
        assert _table_exists(conn, "positions")
        assert _table_exists(conn, "llm_reports")
    finally:
        conn.close()
