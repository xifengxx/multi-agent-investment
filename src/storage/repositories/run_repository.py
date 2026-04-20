"""runs 表仓储实现。"""

from __future__ import annotations

from datetime import datetime, timezone
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

    def list_runs(self, *, limit: int = 50) -> list[sqlite3.Row]:
        """按 started_at 倒序列出最近 runs。"""
        rows = self._connection.execute(
            """
            SELECT *
            FROM runs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return list(rows)

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

    def mark_stale_running_runs_failed(self, *, now_iso: str, max_age_seconds: int) -> int:
        """将启动过久的 RUNNING runs 标记为 FAILED，返回更新条数。"""
        now = datetime.fromisoformat(now_iso)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        rows = self._connection.execute(
            """
            SELECT run_id, started_at
            FROM runs
            WHERE status = 'RUNNING'
              AND ended_at IS NULL
            """,
        ).fetchall()

        stale_run_ids: list[str] = []
        for row in rows:
            started_at_raw = str(row["started_at"] or "")
            if not started_at_raw:
                continue
            started_at = datetime.fromisoformat(started_at_raw)
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            age_seconds = (now - started_at).total_seconds()
            if age_seconds > float(max_age_seconds):
                stale_run_ids.append(str(row["run_id"]))

        if not stale_run_ids:
            return 0

        with self._connection:
            for rid in stale_run_ids:
                self._connection.execute(
                    """
                    UPDATE runs
                    SET status = 'FAILED', ended_at = ?, error_message = ?
                    WHERE run_id = ?
                    """,
                    (now_iso, "stale_run", rid),
                )
        return len(stale_run_ids)
