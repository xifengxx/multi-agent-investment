"""jobs / snapshot_locks 表仓储实现。"""

from __future__ import annotations

import sqlite3
import uuid


class JobRepository:
    """封装 jobs 表的入队、抢占与状态更新。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """初始化仓储并绑定数据库连接。"""
        self._connection = connection

    def enqueue(self, *, run_id: str, snapshot_date: str) -> str:
        """插入一条 PENDING job 并返回 job_id。"""
        job_id = f"job-{uuid.uuid4().hex}"
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO jobs (job_id, run_id, snapshot_date, status, attempts)
                VALUES (?, ?, ?, 'PENDING', 0)
                """,
                (job_id, run_id, snapshot_date),
            )
        return job_id

    def claim_next(self, *, worker_id: str, max_attempts: int) -> sqlite3.Row | None:
        """原子抢占下一条可执行 job。

        约定：
        - 仅抢占 status in ('PENDING','FAILED') 且 attempts < max_attempts 的任务
        - 抢占成功后更新为 RUNNING，并写入 locked_by/locked_at
        """
        if max_attempts <= 0:
            raise ValueError(f"max_attempts must be > 0, got: {max_attempts}")

        now_row = self._connection.execute("SELECT CURRENT_TIMESTAMP AS now").fetchone()
        now_text = str(now_row["now"]) if now_row is not None else ""

        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute(
                """
                SELECT job_id
                FROM jobs
                WHERE status IN ('PENDING', 'FAILED')
                  AND attempts < ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (int(max_attempts),),
            ).fetchone()
            if row is None:
                self._connection.execute("COMMIT")
                return None

            job_id = str(row["job_id"])
            cur = self._connection.execute(
                """
                UPDATE jobs
                SET status = 'RUNNING', locked_by = ?, locked_at = ?
                WHERE job_id = ?
                  AND status IN ('PENDING', 'FAILED')
                  AND locked_by IS NULL
                """,
                (worker_id, now_text, job_id),
            )
            if int(cur.rowcount or 0) != 1:
                self._connection.execute("COMMIT")
                return None

            claimed = self._connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            self._connection.execute("COMMIT")
            return claimed
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def mark_done(self, *, job_id: str) -> None:
        """标记 job 为 DONE，并清空锁字段。"""
        with self._connection:
            self._connection.execute(
                """
                UPDATE jobs
                SET status = 'DONE', locked_by = NULL, locked_at = NULL
                WHERE job_id = ?
                """,
                (job_id,),
            )

    def mark_failed(self, *, job_id: str, error: str) -> None:
        """标记 job 为 FAILED，并增加 attempts，清空锁字段。"""
        with self._connection:
            self._connection.execute(
                """
                UPDATE jobs
                SET status = 'FAILED',
                    attempts = attempts + 1,
                    last_error = ?,
                    locked_by = NULL,
                    locked_at = NULL
                WHERE job_id = ?
                """,
                (error, job_id),
            )

    def release_to_pending(self, *, job_id: str) -> None:
        """将 RUNNING job 释放回 PENDING（用于未获取 snapshot_lock 时的退回）。"""
        with self._connection:
            self._connection.execute(
                """
                UPDATE jobs
                SET status = 'PENDING', locked_by = NULL, locked_at = NULL
                WHERE job_id = ? AND status = 'RUNNING'
                """,
                (job_id,),
            )


class SnapshotLockRepository:
    """封装 snapshot_locks 表的互斥锁获取与释放。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """初始化仓储并绑定数据库连接。"""
        self._connection = connection

    def try_acquire(self, *, snapshot_date: str, worker_id: str, now_iso: str) -> bool:
        """尝试获取互斥锁，成功返回 True，失败返回 False。"""
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO snapshot_locks (snapshot_date, locked_by, locked_at)
                    VALUES (?, ?, ?)
                    """,
                    (snapshot_date, worker_id, now_iso),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def release(self, *, snapshot_date: str, worker_id: str) -> bool:
        """释放互斥锁，仅当 worker_id 匹配持有者才会删除并返回 True。"""
        with self._connection:
            cur = self._connection.execute(
                """
                DELETE FROM snapshot_locks
                WHERE snapshot_date = ? AND locked_by = ?
                """,
                (snapshot_date, worker_id),
            )
        return int(cur.rowcount or 0) == 1

