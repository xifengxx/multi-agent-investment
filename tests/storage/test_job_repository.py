"""jobs/snapshot_locks 表仓储行为测试。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


from storage.db import init_db, open_sqlite_connection  # noqa: E402
from storage.repositories.job_repository import JobRepository, SnapshotLockRepository  # noqa: E402
from storage.repositories.run_repository import RunRepository  # noqa: E402


def test_claim_next_job_is_exclusive(tmp_path: Path) -> None:
    """同一个 job 不应被两个 worker 同时 claim。"""
    db_path = tmp_path / "app.db"
    conn_a = open_sqlite_connection(db_path)
    conn_b = open_sqlite_connection(db_path)
    try:
        init_db(conn_a)
        RunRepository(conn_a).create_run(
            run_id="run-1",
            trigger_type="manual_daily",
            snapshot_date="2026-04-17",
            status="RUNNING",
            started_at="2026-04-17T00:00:00+00:00",
        )
        repo_a = JobRepository(conn_a)
        repo_b = JobRepository(conn_b)

        job_id = repo_a.enqueue(run_id="run-1", snapshot_date="2026-04-17")

        claimed_a = repo_a.claim_next(worker_id="w1", max_attempts=3)
        claimed_b = repo_b.claim_next(worker_id="w2", max_attempts=3)
    finally:
        conn_a.close()
        conn_b.close()

    assert job_id
    assert claimed_a is not None
    assert str(claimed_a["job_id"]) == job_id
    assert claimed_b is None


def test_snapshot_lock_is_exclusive(tmp_path: Path) -> None:
    """同一 snapshot_date 只允许一个 worker 获取互斥锁。"""
    db_path = tmp_path / "app.db"
    conn_a = open_sqlite_connection(db_path)
    conn_b = open_sqlite_connection(db_path)
    try:
        init_db(conn_a)
        lock_a = SnapshotLockRepository(conn_a)
        lock_b = SnapshotLockRepository(conn_b)

        ok1 = lock_a.try_acquire(
            snapshot_date="2026-04-17",
            worker_id="w1",
            now_iso="2026-04-17T00:00:00+00:00",
        )
        ok2 = lock_b.try_acquire(
            snapshot_date="2026-04-17",
            worker_id="w2",
            now_iso="2026-04-17T00:00:01+00:00",
        )
    finally:
        conn_a.close()
        conn_b.close()

    assert ok1 is True
    assert ok2 is False


def test_snapshot_lock_can_be_released_only_by_owner(tmp_path: Path) -> None:
    """互斥锁只能由持有者释放。"""
    db_path = tmp_path / "app.db"
    conn = open_sqlite_connection(db_path)
    try:
        init_db(conn)
        lock_repo = SnapshotLockRepository(conn)

        ok = lock_repo.try_acquire(
            snapshot_date="2026-04-17",
            worker_id="w1",
            now_iso="2026-04-17T00:00:00+00:00",
        )
        released_wrong = lock_repo.release(snapshot_date="2026-04-17", worker_id="w2")
        released_right = lock_repo.release(snapshot_date="2026-04-17", worker_id="w1")
    finally:
        conn.close()

    assert ok is True
    assert released_wrong is False
    assert released_right is True
