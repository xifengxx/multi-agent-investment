"""快照数据仓储实现。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from common.types import InstrumentRow


@dataclass(frozen=True)
class SnapshotWriteResult:
    """快照写入统计。"""

    inserted: int
    updated: int
    ignored: int


class SnapshotRepository:
    """封装 file_batches 与 instrument_snapshots 的写入操作。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """初始化仓储并绑定数据库连接。"""
        self._connection = connection

    def create_file_batch(
        self,
        *,
        run_id: str,
        snapshot_date: str,
        stock_file: str,
        etf_file: str,
        stock_sha256: str,
        etf_sha256: str,
    ) -> int:
        """写入一条文件批次证据记录并返回 batch_id。"""
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO file_batches (
                    run_id,
                    snapshot_date,
                    stock_file,
                    etf_file,
                    stock_sha256,
                    etf_sha256
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, snapshot_date, stock_file, etf_file, stock_sha256, etf_sha256),
            )
        return int(cursor.lastrowid)

    def insert_instrument_rows(self, *, batch_id: int, rows: list[InstrumentRow]) -> SnapshotWriteResult:
        """批量写入快照事实数据并返回写入统计。"""
        if not rows:
            return SnapshotWriteResult(inserted=0, updated=0, ignored=0)

        with self._connection:
            self._connection.executemany(
                """
                INSERT INTO instrument_snapshots (
                    batch_id,
                    snapshot_date,
                    instrument_type,
                    symbol,
                    description,
                    x_d_trend_state,
                    x_d_state_bars,
                    x_w_trend_state,
                    x_w_state_bars,
                    x_m_trend_state,
                    x_m_state_bars,
                    price,
                    volume
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        batch_id,
                        row.snapshot_date,
                        row.instrument_type,
                        row.symbol,
                        row.description,
                        row.x_d_trend_state,
                        row.x_d_state_bars,
                        row.x_w_trend_state,
                        row.x_w_state_bars,
                        row.x_m_trend_state,
                        row.x_m_state_bars,
                        row.price,
                        row.volume,
                    )
                    for row in rows
                ],
            )
        return SnapshotWriteResult(inserted=len(rows), updated=0, ignored=0)
