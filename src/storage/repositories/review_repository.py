"""weekly_reviews / provider_scores 表仓储实现（Task8）。"""

from __future__ import annotations

import sqlite3


class ReviewRepository:
    """封装周度复盘与 Provider 评分表的最小读写操作。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """初始化仓储并绑定数据库连接。"""
        self._connection = connection

    def insert_weekly_review(
        self,
        *,
        review_id: str,
        week_start: str,
        week_end: str,
        report_json: str,
    ) -> None:
        """插入一条周度复盘记录。"""
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO weekly_reviews (
                    review_id,
                    week_start,
                    week_end,
                    report_json
                ) VALUES (?, ?, ?, ?)
                """,
                (str(review_id), str(week_start), str(week_end), str(report_json)),
            )

    def upsert_provider_score(self, *, provider: str, score: float) -> None:
        """插入或更新 provider_scores（最小实现）。"""
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO provider_scores (provider, score, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(provider)
                DO UPDATE SET
                    score = excluded.score,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (str(provider), float(score)),
            )

