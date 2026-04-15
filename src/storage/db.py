"""SQLite 连接与 Schema 初始化模块。"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def open_sqlite_connection(db_path: str | Path) -> sqlite3.Connection:
    """打开 SQLite 连接并启用外键约束。"""
    resolved_path = Path(db_path)
    if resolved_path.parent != Path("."):
        resolved_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(resolved_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    return connection


def _resolve_schema_path(schema_path: str | Path | None = None) -> Path:
    """解析 schema.sql 路径，未传入时使用当前目录默认文件。"""
    if schema_path is not None:
        return Path(schema_path)
    return Path(__file__).with_name("schema.sql")


def init_db(connection: sqlite3.Connection, schema_path: str | Path | None = None) -> None:
    """执行 schema.sql 完成数据库初始化，支持重复执行。"""
    ddl_path = _resolve_schema_path(schema_path)
    ddl_sql = ddl_path.read_text(encoding="utf-8")
    with connection:
        connection.executescript(ddl_sql)
