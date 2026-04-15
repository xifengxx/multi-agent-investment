"""用于测试与本地开发的 Mock LLM Provider。"""

from __future__ import annotations

import json
import re

from analysis.providers.base import BaseLLMProvider


class MockLLMProvider(BaseLLMProvider):
    """基于 prompt 规则生成可预测 JSON 的 Mock Provider。"""

    @property
    def name(self) -> str:
        """返回 provider 名称。"""
        return "mock"

    def invoke(self, prompt: str) -> str:
        """根据 prompt 简单生成分析结果 JSON 字符串。

        设计目标：
        - 不依赖外部网络
        - 输出稳定可复现，便于测试“引擎+校验+落库”整条链路
        """
        symbol = self._extract_symbol(prompt) or "UNKNOWN"
        daily_trend = self._extract_field(prompt, "x_d_trend_state")
        recommendation = "BUY"
        if daily_trend and daily_trend.upper() == "DOWN":
            recommendation = "SELL"

        payload = {
            "symbol": symbol,
            "recommendation": recommendation,
            "confidence": 0.8 if recommendation == "BUY" else 0.7,
            "scorecard": {"daily_trend": daily_trend or "N/A"},
            "rationale": [
                f"Mock 分析：基于 x_d_trend_state={daily_trend or 'N/A'} 给出 {recommendation}。"
            ],
        }
        return json.dumps(payload, ensure_ascii=False)

    def _extract_symbol(self, prompt: str) -> str | None:
        """从 prompt 中提取 SYMBOL 字段。"""
        match = re.search(r"^SYMBOL:\s*(?P<symbol>[A-Za-z0-9.\-_]+)\s*$", prompt, flags=re.M)
        return match.group("symbol") if match else None

    def _extract_field(self, prompt: str, field: str) -> str | None:
        """从 prompt 中提取某个键值字段，形如 `field=value`。"""
        pattern = rf"^{re.escape(field)}=(?P<value>.+?)\s*$"
        match = re.search(pattern, prompt, flags=re.M)
        return match.group("value").strip() if match else None

