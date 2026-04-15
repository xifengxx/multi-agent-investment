"""runs 表仓储实现。"""

from __future__ import annotations

import sqlite3


class RunRepository:
    """封装 runs 表的基础读写操作。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """初始化仓储并绑定数据库连接。"""
        self._connection = connection

    def create_run(
        self,
        *,
        run_id: str,
        trigger_type: str,
        snapshot_date: str | None,
        status: str,
        started_at: str,
    ) -> None:
        """创建一条运行记录。"""
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO runs (
                    run_id,
                    trigger_type,
                    snapshot_date,
                    status,
                    started_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, trigger_type, snapshot_date, status, started_at),
            )

    def get_run(self, run_id: str) -> sqlite3.Row | None:
        """根据 run_id 查询运行记录。"""
        row = self._connection.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return row
