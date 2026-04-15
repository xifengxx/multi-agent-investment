"""LLM 输出解析与校验模块。

该模块负责将模型原始输出(raw_text)尽可能解析为 JSON，并对约定字段进行容错校验。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LLMValidationResult:
    """LLM 输出校验结果。"""

    raw_text: str
    json_text: str | None
    parsed: dict[str, Any]
    is_valid: bool
    quality_flags: list[str]


class LLMOutputValidator:
    """将 LLM 输出解析为统一 schema，并产出质量标记。"""

    def validate(self, raw_text: str) -> LLMValidationResult:
        """校验 LLM 原始输出文本并返回结构化结果。

        约定输出 schema：
        - symbol: str
        - recommendation: str
        - confidence: float (0-1)
        - scorecard: dict
        - rationale: list[str] 或 str（会被规范化为 list[str]）

        规则：
        - 缺字段时容错：填入 None/{} /[]，并记录 quality_flags
        - 解析失败时：json_text=None，is_valid=False，quality_flags 含 json_parse_error
        """
        flags: list[str] = []
        extracted = self._extract_json_text(raw_text)
        if extracted is None:
            return LLMValidationResult(
                raw_text=raw_text,
                json_text=None,
                parsed={
                    "symbol": None,
                    "recommendation": None,
                    "confidence": None,
                    "scorecard": {},
                    "rationale": [],
                },
                is_valid=False,
                quality_flags=["json_parse_error"],
            )

        try:
            payload = json.loads(extracted)
        except json.JSONDecodeError:
            return LLMValidationResult(
                raw_text=raw_text,
                json_text=None,
                parsed={
                    "symbol": None,
                    "recommendation": None,
                    "confidence": None,
                    "scorecard": {},
                    "rationale": [],
                },
                is_valid=False,
                quality_flags=["json_parse_error"],
            )

        if not isinstance(payload, dict):
            return LLMValidationResult(
                raw_text=raw_text,
                json_text=None,
                parsed={
                    "symbol": None,
                    "recommendation": None,
                    "confidence": None,
                    "scorecard": {},
                    "rationale": [],
                },
                is_valid=False,
                quality_flags=["non_object_json"],
            )

        normalized = self._normalize(payload, flags)
        json_text = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        is_valid = len(flags) == 0
        return LLMValidationResult(
            raw_text=raw_text,
            json_text=json_text,
            parsed=normalized,
            is_valid=is_valid,
            quality_flags=flags,
        )

    def _extract_json_text(self, raw_text: str) -> str | None:
        """从 LLM 输出中提取 JSON 文本片段。

        兼容常见的 ```json ... ``` 包裹形式；若无法定位 JSON 对象边界则返回 None。
        """
        text = raw_text.strip()
        if not text:
            return None

        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        return text[start : end + 1]

    def _normalize(self, payload: dict[str, Any], flags: list[str]) -> dict[str, Any]:
        """将 payload 规范化到约定 schema，并在 flags 中记录缺陷。"""
        symbol = payload.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            flags.append("missing_symbol")
            symbol_norm: str | None = None
        else:
            symbol_norm = symbol.strip()

        recommendation = payload.get("recommendation")
        if not isinstance(recommendation, str) or not recommendation.strip():
            flags.append("missing_recommendation")
            recommendation_norm: str | None = None
        else:
            recommendation_norm = recommendation.strip()

        confidence_val = payload.get("confidence")
        confidence_norm: float | None
        if confidence_val is None:
            flags.append("missing_confidence")
            confidence_norm = None
        elif isinstance(confidence_val, (int, float)):
            confidence_norm = float(confidence_val)
            if confidence_norm < 0.0 or confidence_norm > 1.0:
                flags.append("invalid_confidence")
                confidence_norm = None
        else:
            flags.append("invalid_confidence")
            confidence_norm = None

        scorecard_val = payload.get("scorecard")
        if scorecard_val is None:
            flags.append("missing_scorecard")
            scorecard_norm: dict[str, Any] = {}
        elif isinstance(scorecard_val, dict):
            scorecard_norm = scorecard_val
        else:
            flags.append("invalid_scorecard")
            scorecard_norm = {}

        rationale_val = payload.get("rationale")
        rationale_norm: list[str]
        if rationale_val is None:
            flags.append("missing_rationale")
            rationale_norm = []
        elif isinstance(rationale_val, str):
            rationale_norm = [rationale_val]
        elif isinstance(rationale_val, list):
            rationale_norm = [item for item in rationale_val if isinstance(item, str)]
            if len(rationale_norm) != len(rationale_val):
                flags.append("invalid_rationale")
        else:
            flags.append("invalid_rationale")
            rationale_norm = []

        return {
            "symbol": symbol_norm,
            "recommendation": recommendation_norm,
            "confidence": confidence_norm,
            "scorecard": scorecard_norm,
            "rationale": rationale_norm,
        }
