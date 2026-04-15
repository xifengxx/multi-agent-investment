"""Prompt 构建模块。

本项目将“事实快照”转为 prompt，让 provider 仅关心调用与返回文本。
"""

from __future__ import annotations

from common.types import InstrumentRow


class PromptBuilder:
    """从 InstrumentRow 构建分析 prompt。"""

    def build(self, instrument: InstrumentRow) -> str:
        """根据单个标的快照生成 prompt 文本。

        为了便于 MockProvider 与真实 Provider 共用，该 prompt 使用“明确字段行”的结构，
        便于简单解析：
        - 第一行固定包含 SYMBOL: <symbol>
        - 其余字段使用 key=value 形式
        """
        lines = [
            f"SYMBOL: {instrument.symbol}",
            f"snapshot_date={instrument.snapshot_date}",
            f"instrument_type={instrument.instrument_type}",
            f"description={instrument.description}",
            f"x_d_trend_state={instrument.x_d_trend_state}",
            f"x_d_state_bars={instrument.x_d_state_bars}",
            f"x_w_trend_state={instrument.x_w_trend_state}",
            f"x_w_state_bars={instrument.x_w_state_bars}",
            f"x_m_trend_state={instrument.x_m_trend_state}",
            f"x_m_state_bars={instrument.x_m_state_bars}",
            f"price={instrument.price}",
            f"volume={instrument.volume}",
            "",
            "请仅返回 JSON 对象，字段为：symbol, recommendation, confidence(0-1), scorecard(dict), rationale(list[str]或str)。",
        ]
        return "\n".join(lines)

