"""decisions 表仓储实现。"""

from __future__ import annotations

import sqlite3
from typing import Any


class DecisionRepository:
    """封装 decisions 表的读写操作。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """初始化仓储并绑定数据库连接。"""
        self._connection = connection

    def insert_decisions(self, decisions: list[dict[str, Any]]) -> None:
        """批量插入 decisions 记录。

        输入 dict 最小字段约定：
        - run_id, snapshot_date, instrument_type, symbol, votes, tier, rank_in_list, summary_rationale
        """
        if not decisions:
            return
        payloads = [
            (
                d["run_id"],
                d["snapshot_date"],
                d["instrument_type"],
                d["symbol"],
                int(d["votes"]),
                int(d["tier"]),
                int(d["rank_in_list"]),
                str(d["summary_rationale"]),
            )
            for d in decisions
        ]
        with self._connection:
            self._connection.executemany(
                """
                INSERT INTO decisions (
                    run_id,
                    snapshot_date,
                    instrument_type,
                    symbol,
                    votes,
                    tier,
                    rank_in_list,
                    summary_rationale
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payloads,
            )

    def list_decisions(self, *, run_id: str, instrument_type: str) -> list[sqlite3.Row]:
        """查询指定 run_id + instrument_type 的决策榜单，按 rank_in_list 升序返回。"""
        rows = self._connection.execute(
            """
            SELECT
                run_id,
                snapshot_date,
                instrument_type,
                symbol,
                votes,
                tier,
                rank_in_list,
                summary_rationale,
                created_at
            FROM decisions
            WHERE run_id = ? AND instrument_type = ?
            ORDER BY rank_in_list ASC
            """,
            (run_id, instrument_type),
        ).fetchall()
        return list(rows)

