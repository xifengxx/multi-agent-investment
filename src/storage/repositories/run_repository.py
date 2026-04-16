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

    def mark_run_end(
        self,
        *,
        run_id: str,
        status: str,
        ended_at: str,
        error_message: str | None,
    ) -> None:
        """更新 runs 的结束状态与结束时间。

        约定：
        - status 应为 schema 允许的枚举值（RUNNING/SUCCEEDED/FAILED/DEGRADED）
        - ended_at 由上层生成 ISO 字符串（或任何可读时间戳），仓储不做解析
        """
        with self._connection:
            self._connection.execute(
                """
                UPDATE runs
                SET status = ?, ended_at = ?, error_message = ?
                WHERE run_id = ?
                """,
                (status, ended_at, error_message, run_id),
            )

    def set_snapshot_date(self, *, run_id: str, snapshot_date: str) -> None:
        """为指定 run_id 写入 snapshot_date。"""
        with self._connection:
            self._connection.execute(
                """
                UPDATE runs
                SET snapshot_date = ?
                WHERE run_id = ?
                """,
                (snapshot_date, run_id),
            )
