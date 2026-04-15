"""纸面账本服务（Paper Trading Ledger）。"""

from __future__ import annotations

from typing import Any

from storage.repositories.ledger_repository import LedgerRepository


class PaperLedgerService:
    """根据决策榜单生成纸面交易并更新持仓。"""

    def __init__(self, ledger_repository: LedgerRepository) -> None:
        """初始化服务并绑定 LedgerRepository。"""
        self._ledger_repository = ledger_repository

    def record_buys_for_decisions(
        self,
        *,
        run_id: str,
        snapshot_date: str,
        decisions: list[dict[str, Any]],
    ) -> None:
        """BUY 生成策略：对每个入选标的生成一笔 qty=1 的 BUY 交易并落库。

        约定：
        - decisions 至少包含 instrument_type, symbol
        - qty 固定为 1，不依赖价格
        """
        trades: list[dict[str, Any]] = []
        for d in decisions:
            instrument_type = str(d.get("instrument_type", "")).strip().lower()
            symbol = str(d.get("symbol", "")).strip()
            if not instrument_type or not symbol:
                continue
            trades.append(
                {
                    "run_id": run_id,
                    "snapshot_date": snapshot_date,
                    "instrument_type": instrument_type,
                    "symbol": symbol,
                    "side": "BUY",
                    "qty": 1,
                    "price": None,
                }
            )

        if not trades:
            return

        self._ledger_repository.insert_trades_and_update_positions(trades)

