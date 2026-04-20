"""项目公共类型定义。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RunMode = Literal["daily", "weekly", "web", "worker"]
AppEnv = Literal["dev", "test", "prod"]
InstrumentType = Literal["stock", "etf"]


@dataclass(frozen=True)
class InstrumentRow:
    """统一的标的行 DTO，用于承载 stock/etf 解析结果。"""

    snapshot_date: str
    instrument_type: InstrumentType
    symbol: str
    description: str
    x_d_trend_state: str
    x_d_state_bars: int
    x_w_trend_state: str
    x_w_state_bars: int
    x_m_trend_state: str
    x_m_state_bars: int
    price: float | None = None
    volume: float | None = None
