"""周度复盘服务（统计层）。

当前阶段无行情数据，因此收益/回撤等指标暂不计算，仅提供交易维度的基础统计。
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any


def _compute_week_start(*, week_end: str, days: int) -> str:
    """根据 week_end 与 days 计算 week_start（包含边界）。

    约定：
    - week_end 使用 ISO 日期字符串（YYYY-MM-DD）
    - days 默认为 7；week_start = week_end - (days - 1)
    """
    if days <= 0:
        raise ValueError("days 必须为正整数")
    end_day = date.fromisoformat(str(week_end))
    start_day = end_day - timedelta(days=days - 1)
    return start_day.isoformat()


def compute_weekly_report(conn: sqlite3.Connection, week_end: str, days: int = 7) -> dict[str, Any]:
    """计算周度复盘报告并返回结构化 dict。

    输出字段（最小约束）：
    - week_start / week_end: ISO 日期字符串（YYYY-MM-DD）
    - trade_count: 周期内 paper_trades 条数
    - unique_symbols: 周期内去重后的 symbol 数量（跨 stock/etf 合并）
    - by_instrument_type: 分 instrument_type 的 trade_count / unique_symbols

    说明：
    - 当前无行情数据，收益/回撤等字段暂不计算，可为空或不返回。
    - 查询基于 paper_trades.snapshot_date（按 ISO 文本进行区间过滤，包含边界）。
    """
    week_end_str = str(week_end)
    week_start_str = _compute_week_start(week_end=week_end_str, days=days)

    rows = conn.execute(
        """
        SELECT instrument_type, symbol
        FROM paper_trades
        WHERE snapshot_date >= ? AND snapshot_date <= ?
        """,
        (week_start_str, week_end_str),
    ).fetchall()

    trade_count = len(rows)
    all_symbols = {str(r["symbol"]) for r in rows}

    by_type: dict[str, dict[str, Any]] = {}
    for r in rows:
        itype = str(r["instrument_type"])
        sym = str(r["symbol"])
        bucket = by_type.setdefault(
            itype,
            {
                "trade_count": 0,
                "unique_symbols": 0,
                "_symbols": set(),
            },
        )
        bucket["trade_count"] = int(bucket["trade_count"]) + 1
        bucket["_symbols"].add(sym)

    # 规范输出：补全 stock/etf，确保字段稳定
    for itype in ("stock", "etf"):
        by_type.setdefault(itype, {"trade_count": 0, "unique_symbols": 0, "_symbols": set()})

    for itype, bucket in list(by_type.items()):
        syms = bucket.get("_symbols") or set()
        bucket["unique_symbols"] = len(syms)
        bucket.pop("_symbols", None)
        by_type[itype] = bucket

    return {
        "week_start": week_start_str,
        "week_end": week_end_str,
        "trade_count": trade_count,
        "unique_symbols": len(all_symbols),
        "by_instrument_type": by_type,
    }

