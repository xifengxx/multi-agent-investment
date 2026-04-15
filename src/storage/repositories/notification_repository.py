"""notifications 表仓储实现。"""

from __future__ import annotations

import sqlite3


class NotificationRepository:
    """封装 notifications 表的读写操作。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """初始化仓储并绑定数据库连接。"""
        self._connection = connection

    def insert_notification(
        self,
        *,
        run_id: str,
        channel: str,
        message_text: str,
        dry_run: bool,
        status: str,
        provider_response_json: str | None = None,
    ) -> None:
        """插入一条通知发送记录。"""
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO notifications (
                    run_id,
                    channel,
                    message_text,
                    dry_run,
                    status,
                    provider_response_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    channel,
                    message_text,
                    1 if dry_run else 0,
                    status,
                    provider_response_json,
                ),
            )

    def list_notifications(self, *, run_id: str) -> list[sqlite3.Row]:
        """查询指定 run_id 的通知记录，按 notification_id 升序返回。"""
        rows = self._connection.execute(
            """
            SELECT
                notification_id,
                run_id,
                channel,
                message_text,
                dry_run,
                status,
                provider_response_json,
                created_at
            FROM notifications
            WHERE run_id = ?
            ORDER BY notification_id ASC
            """,
            (run_id,),
        ).fetchall()
        return list(rows)

