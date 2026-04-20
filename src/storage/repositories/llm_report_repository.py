"""llm_reports 表仓储实现。"""

from __future__ import annotations

import json
import sqlite3


class LLMReportRepository:
    """封装 llm_reports 表的读写操作。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """初始化仓储并绑定数据库连接。"""
        self._connection = connection

    def insert_report(
        self,
        *,
        run_id: str,
        snapshot_date: str,
        instrument_type: str,
        provider: str,
        raw_text: str,
        json_text: str | None,
        is_valid: bool,
        quality_flags: list[str],
    ) -> int:
        """插入一条报告记录并返回 report_id。"""
        flags_text = json.dumps(quality_flags, ensure_ascii=False, separators=(",", ":"))
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO llm_reports (
                    run_id,
                    snapshot_date,
                    instrument_type,
                    provider,
                    raw_text,
                    json_text,
                    is_valid,
                    quality_flags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    snapshot_date,
                    instrument_type,
                    provider,
                    raw_text,
                    json_text,
                    1 if is_valid else 0,
                    flags_text,
                ),
            )
        return int(cursor.lastrowid)

    def list_reports(self, *, run_id: str) -> list[sqlite3.Row]:
        """按 run_id 查询全部 llm_reports 记录。"""
        rows = self._connection.execute(
            "SELECT * FROM llm_reports WHERE run_id = ? ORDER BY report_id ASC",
            (run_id,),
        ).fetchall()
        return list(rows)

