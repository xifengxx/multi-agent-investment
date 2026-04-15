"""paper_trades / positions 表仓储实现。"""

from __future__ import annotations

import sqlite3
from typing import Any


class LedgerRepository:
    """封装纸面交易与持仓表的读写操作。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """初始化仓储并绑定数据库连接。"""
        self._connection = connection

    def insert_trades_and_update_positions(self, trades: list[dict[str, Any]]) -> None:
        """批量插入交易并同步更新 positions（qty 累加）。

        输入 trade dict 最小字段约定：
        - run_id, snapshot_date, instrument_type, symbol, side, qty, price(可选)
        """
        if not trades:
            return

        with self._connection:
            for t in trades:
                run_id = str(t["run_id"])
                snapshot_date = str(t["snapshot_date"])
                instrument_type = str(t["instrument_type"])
                symbol = str(t["symbol"])
                side = str(t["side"])
                qty = int(t["qty"])
                price = t.get("price")

                self._connection.execute(
                    """
                    INSERT INTO paper_trades (
                        run_id,
                        snapshot_date,
                        instrument_type,
                        symbol,
                        side,
                        qty,
                        price
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (run_id, snapshot_date, instrument_type, symbol, side, qty, price),
                )

                # positions.qty 累加（BUY 为 +qty，SELL 为 -qty）
                delta = qty if side.upper() == "BUY" else -qty
                self._connection.execute(
                    """
                    INSERT INTO positions (instrument_type, symbol, qty, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(instrument_type, symbol)
                    DO UPDATE SET
                        qty = qty + excluded.qty,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (instrument_type, symbol, delta),
                )

    def list_paper_trades(self, *, run_id: str) -> list[sqlite3.Row]:
        """查询指定 run_id 的 paper_trades 记录，按 trade_id 升序返回。"""
        rows = self._connection.execute(
            """
            SELECT
                trade_id,
                run_id,
                snapshot_date,
                instrument_type,
                symbol,
                side,
                qty,
                price,
                created_at
            FROM paper_trades
            WHERE run_id = ?
            ORDER BY trade_id ASC
            """,
            (run_id,),
        ).fetchall()
        return list(rows)

    def list_positions(self) -> list[sqlite3.Row]:
        """查询全部持仓，按 instrument_type + symbol 升序返回。"""
        rows = self._connection.execute(
            """
            SELECT
                position_id,
                instrument_type,
                symbol,
                qty,
                updated_at
            FROM positions
            ORDER BY instrument_type ASC, symbol ASC
            """
        ).fetchall()
        return list(rows)

