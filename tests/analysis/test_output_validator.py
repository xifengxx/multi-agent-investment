"""LLM 输出校验器测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from analysis.llm_output_validator import LLMOutputValidator  # noqa: E402


def test_validator_accepts_valid_payload_and_normalizes_rationale() -> None:
    """字段齐全且合法时，应标记为有效并将 rationale 统一为 list[str]。"""
    validator = LLMOutputValidator()
    raw_text = json.dumps(
        {
            "symbol": "AAPL",
            "recommendation": "BUY",
            "confidence": 0.82,
            "scorecard": {"trend": 0.9, "risk": 0.3},
            "rationale": "长期趋势向上，短期回撤有限。",
        },
        ensure_ascii=False,
    )

    result = validator.validate(raw_text)

    assert result.is_valid is True
    assert result.json_text is not None
    assert result.parsed["symbol"] == "AAPL"
    assert result.parsed["recommendation"] == "BUY"
    assert result.parsed["confidence"] == 0.82
    assert result.parsed["scorecard"] == {"trend": 0.9, "risk": 0.3}
    assert result.parsed["rationale"] == ["长期趋势向上，短期回撤有限。"]
    assert result.quality_flags == []


def test_validator_is_tolerant_to_missing_fields_and_sets_quality_flags() -> None:
    """缺字段时，应容错返回并记录 quality_flags，同时标记为无效。"""
    validator = LLMOutputValidator()
    raw_text = json.dumps({"symbol": "TSLA", "recommendation": "HOLD"}, ensure_ascii=False)

    result = validator.validate(raw_text)

    assert result.is_valid is False
    assert result.parsed["symbol"] == "TSLA"
    assert result.parsed["recommendation"] == "HOLD"
    assert result.parsed["confidence"] is None
    assert result.parsed["scorecard"] == {}
    assert result.parsed["rationale"] == []
    assert "missing_confidence" in result.quality_flags
    assert "missing_scorecard" in result.quality_flags
    assert "missing_rationale" in result.quality_flags


def test_validator_marks_invalid_when_json_cannot_be_parsed() -> None:
    """无法解析为 JSON 时，应返回无效并包含 json_parse_error。"""
    validator = LLMOutputValidator()
    raw_text = "这不是 JSON"

    result = validator.validate(raw_text)

    assert result.is_valid is False
    assert result.json_text is None
    assert "json_parse_error" in result.quality_flags
