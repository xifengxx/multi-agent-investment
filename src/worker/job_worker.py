"""SQLite Job Queue Worker 实现。"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from analysis.providers.base import BaseLLMProvider
from app.config import AppConfig
from orchestrator.run_orchestrator import daily_run_for_existing_run
from storage.db import init_db, open_sqlite_connection
from storage.repositories.job_repository import JobRepository, SnapshotLockRepository


def _now_iso() -> str:
    """生成 UTC 秒级时间戳。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class JobWorker:
    """从 jobs 表抢占任务并执行 daily run。"""

    def __init__(self, *, worker_id: str, config: AppConfig, providers: list[BaseLLMProvider] | None = None) -> None:
        """初始化 worker。"""
        self._worker_id = worker_id
        self._config = config
        self._providers = providers

    def run_once(self) -> None:
        """执行一次抢占与处理（用于测试与可控执行）。"""
        conn = open_sqlite_connection(self._config.sqlite_path)
        try:
            init_db(conn)
            jobs = JobRepository(conn)
            locks = SnapshotLockRepository(conn)

            claimed = jobs.claim_next(
                worker_id=self._worker_id, max_attempts=int(getattr(self._config, "worker_max_attempts", 3) or 3)
            )
            if claimed is None:
                return

            job_id = str(claimed["job_id"])
            run_id = str(claimed["run_id"])
            snapshot_date = str(claimed["snapshot_date"])

            if not locks.try_acquire(snapshot_date=snapshot_date, worker_id=self._worker_id, now_iso=_now_iso()):
                jobs.release_to_pending(job_id=job_id)
                return
        finally:
            conn.close()

        try:
            conn2 = open_sqlite_connection(self._config.sqlite_path)
            try:
                init_db(conn2)
                row = conn2.execute(
                    """
                    SELECT stock_file, etf_file
                    FROM file_batches
                    WHERE run_id = ?
                    ORDER BY batch_id DESC
                    LIMIT 1
                    """,
                    (run_id,),
                ).fetchone()
            finally:
                conn2.close()

            if row is None:
                raise RuntimeError("missing_file_batch_for_run")

            daily_run_for_existing_run(
                config=self._config,
                run_id=run_id,
                snapshot_date=snapshot_date,
                stock_file_path=str(row["stock_file"]),
                etf_file_path=str(row["etf_file"]),
                providers=self._providers,
                dry_run=self._config.dry_run,
            )

            conn3 = open_sqlite_connection(self._config.sqlite_path)
            try:
                init_db(conn3)
                JobRepository(conn3).mark_done(job_id=job_id)
            finally:
                conn3.close()
        except Exception as exc:  # noqa: BLE001
            conn3 = open_sqlite_connection(self._config.sqlite_path)
            try:
                init_db(conn3)
                JobRepository(conn3).mark_failed(job_id=job_id, error=str(exc))
            finally:
                conn3.close()
        finally:
            conn4 = open_sqlite_connection(self._config.sqlite_path)
            try:
                init_db(conn4)
                SnapshotLockRepository(conn4).release(snapshot_date=snapshot_date, worker_id=self._worker_id)
            finally:
                conn4.close()

    def run_forever(self) -> None:
        """循环执行 worker（生产环境）。"""
        interval = float(getattr(self._config, "worker_poll_interval_seconds", 2) or 2)
        while True:
            self.run_once()
            time.sleep(interval)

