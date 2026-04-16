"""runs 表仓储行为测试。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


from storage.db import init_db, open_sqlite_connection  # noqa: E402
from storage.repositories.run_repository import RunRepository  # noqa: E402


def test_run_repository_can_mark_stale_running_runs_as_failed(tmp_path: Path) -> None:
    """应将超时过久的 RUNNING run 标记为 FAILED，避免长期悬挂。"""
    db_path = tmp_path / "app.db"
    conn = open_sqlite_connection(db_path)
    try:
        init_db(conn)
        repo = RunRepository(conn)

        repo.create_run(
            run_id="run-old",
            trigger_type="manual_daily",
            snapshot_date="2026-04-10",
            status="RUNNING",
            started_at="2026-04-15T00:00:00+00:00",
        )
        repo.create_run(
            run_id="run-recent",
            trigger_type="manual_daily",
            snapshot_date="2026-04-10",
            status="RUNNING",
            started_at="2026-04-15T00:59:30+00:00",
        )

        updated = repo.mark_stale_running_runs_failed(
            now_iso="2026-04-15T01:01:00+00:00",
            max_age_seconds=3600,
        )

        old_row = repo.get_run("run-old")
        recent_row = repo.get_run("run-recent")
    finally:
        conn.close()

    assert updated == 1
    assert old_row is not None
    assert old_row["status"] == "FAILED"
    assert old_row["ended_at"] == "2026-04-15T01:01:00+00:00"
    assert old_row["error_message"] == "stale_run"

    assert recent_row is not None
    assert recent_row["status"] == "RUNNING"

