"""notifications 表落库测试。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


from storage.db import init_db, open_sqlite_connection  # noqa: E402
from storage.repositories.notification_repository import NotificationRepository  # noqa: E402
from storage.repositories.run_repository import RunRepository  # noqa: E402


def test_notification_repository_can_insert_and_list(tmp_path: Path) -> None:
    """NotificationRepository 应能写入并按 run_id 查询返回。"""
    db_path = tmp_path / "app.db"
    conn = open_sqlite_connection(db_path)
    try:
        init_db(conn)
        RunRepository(conn).create_run(
            run_id="run-001",
            trigger_type="manual_daily",
            snapshot_date="2026-04-15",
            status="RUNNING",
            started_at="2026-04-15T09:00:00+08:00",
        )
        repo = NotificationRepository(conn)
        repo.insert_notification(
            run_id="run-001",
            channel="telegram",
            message_text="hello",
            dry_run=True,
            status="SKIPPED",
            provider_response_json='{"ok":true}',
        )
        rows = repo.list_notifications(run_id="run-001")
    finally:
        conn.close()

    assert len(rows) == 1
    assert rows[0]["run_id"] == "run-001"
    assert rows[0]["channel"] == "telegram"
    assert rows[0]["message_text"] == "hello"
    assert rows[0]["dry_run"] == 1
    assert rows[0]["status"] == "SKIPPED"
