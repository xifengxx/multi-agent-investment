"""Panel Analysis 报告输出校验：JSON 结构校验与 Top10 提取。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from analysis.panel_schema import PATTERN_IDS, SCHEMA_VERSION, SIGNAL_STRENGTHS


@dataclass(frozen=True)
class PanelValidationResult:
    raw_text: str
    json_text: str | None
    parsed: dict[str, Any]
    is_valid: bool
    quality_flags: list[str]
    top10_items: list[dict[str, Any]]


_NON_FATAL_FLAGS: set[str] = {"entry_logic_brief_autofilled"}


def _flag(flags: list[str], name: str) -> None:
    if name not in flags:
        flags.append(name)


def _extract_json_text(raw_text: str) -> str | None:
    """从 LLM 原始输出中提取 JSON 文本。

    支持的常见形式：
    - 纯 JSON：`{...}`
    - Markdown code fence：```json\\n{...}\\n```
    - 前后带解释文字：尝试截取第一个 `{` 到最后一个 `}` 之间的片段
    """
    text = (raw_text or "").strip()
    if not text:
        return None

    if text.startswith("{") and text.endswith("}"):
        return text

    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, flags=re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1].strip()
    return None


def validate_panel_output(
    *,
    raw_text: str,
    expected_snapshot_date: str,
    expected_instrument_type: str,
    provider_name: str,
) -> PanelValidationResult:
    flags: list[str] = []
    parsed: dict[str, Any] = {}
    json_text: str | None = None
    top10_items: list[dict[str, Any]] = []

    try:
        candidate = _extract_json_text(raw_text)
        if candidate is None:
            raise ValueError("no_json_candidate")
        parsed = json.loads(candidate)
        if not isinstance(parsed, dict):
            _flag(flags, "json_not_object")
            return PanelValidationResult(
                raw_text=raw_text,
                json_text=None,
                parsed={},
                is_valid=False,
                quality_flags=flags,
                top10_items=[],
            )
        json_text = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        _flag(flags, "invalid_json")
        return PanelValidationResult(
            raw_text=raw_text,
            json_text=None,
            parsed={},
            is_valid=False,
            quality_flags=flags,
            top10_items=[],
        )

    if str(parsed.get("schema_version") or "") != SCHEMA_VERSION:
        _flag(flags, "schema_version_mismatch")

    if str(parsed.get("instrument_type") or "") != expected_instrument_type:
        _flag(flags, "instrument_type_mismatch")

    if str(parsed.get("snapshot_date") or "") != expected_snapshot_date:
        _flag(flags, "snapshot_date_mismatch")

    if str(parsed.get("provider") or "") != provider_name:
        _flag(flags, "provider_mismatch")

    reversal_patterns = parsed.get("reversal_patterns")
    if not isinstance(reversal_patterns, list):
        _flag(flags, "reversal_patterns_invalid")
        reversal_patterns = []

    for item in reversal_patterns:
        if not isinstance(item, dict):
            _flag(flags, "reversal_pattern_item_invalid")
            continue
        pattern_id = str(item.get("pattern_id") or "")
        if pattern_id not in PATTERN_IDS:
            _flag(flags, "unknown_pattern_id")

        if expected_instrument_type == "etf":
            metrics = item.get("metrics")
            if not isinstance(metrics, dict):
                _flag(flags, "etf_metrics_invalid")
                continue
            if metrics.get("avg_return_N") is not None:
                _flag(flags, "etf_metrics_not_null")
            if metrics.get("win_rate_N") is not None:
                _flag(flags, "etf_metrics_not_null")
            if metrics.get("avg_holding_return") is not None:
                _flag(flags, "etf_metrics_not_null")
            if metrics.get("max_drawdown") is not None:
                _flag(flags, "etf_metrics_not_null")

    top10 = parsed.get("top10")
    if not isinstance(top10, list):
        _flag(flags, "top10_invalid")
        top10 = []

    if len(top10) != 10:
        _flag(flags, "top10_invalid_length")

    for row in top10:
        if not isinstance(row, dict):
            _flag(flags, "top10_item_invalid")
            continue
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            _flag(flags, "top10_symbol_missing")
            continue
        signal_strength = str(row.get("signal_strength") or "").strip()
        if signal_strength not in SIGNAL_STRENGTHS:
            _flag(flags, "unknown_signal_strength")
        confidence_raw = row.get("confidence")
        try:
            confidence = float(confidence_raw)
        except Exception:
            confidence = -1.0
        if not (0.0 <= confidence <= 1.0):
            _flag(flags, "invalid_confidence")

        brief = str(row.get("entry_logic_brief") or "").strip()
        detail = str(row.get("entry_logic_detail") or "").strip()
        if not brief and detail:
            m = re.split(r"[。！？.!?]\s*", detail, maxsplit=1)
            brief = (m[0] if m else detail).strip()
            if brief:
                _flag(flags, "entry_logic_brief_autofilled")
        if not brief:
            _flag(flags, "entry_logic_brief_missing")
        if not detail:
            _flag(flags, "entry_logic_detail_missing")

        top10_items.append(
            {
                "symbol": symbol,
                "signal_strength": signal_strength,
                "confidence": confidence if 0.0 <= confidence <= 1.0 else 0.0,
                "entry_logic_brief": brief,
                "entry_logic_detail": detail,
            }
        )

    fatal_flags = [f for f in flags if f not in _NON_FATAL_FLAGS]
    is_valid = len(fatal_flags) == 0
    return PanelValidationResult(
        raw_text=raw_text,
        json_text=json_text,
        parsed=parsed,
        is_valid=is_valid,
        quality_flags=flags,
        top10_items=top10_items if is_valid else top10_items,
    )


def expand_top10_to_llm_outputs(
    *,
    run_id: str,
    snapshot_date: str,
    instrument_type: str,
    provider: str,
    top10_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """将 Top10 展开为写入 llm_outputs 的 payload 列表。"""
    rows: list[dict[str, Any]] = []
    for item in top10_items:
        symbol = str(item.get("symbol") or "").strip()
        if not symbol:
            continue
        confidence = item.get("confidence")
        brief = str(item.get("entry_logic_brief") or "").strip()
        detail = str(item.get("entry_logic_detail") or "").strip()
        json_payload = {
            "symbol": symbol,
            "recommendation": "BUY",
            "confidence": confidence,
            "rationale": [brief] if brief else [],
            "detail": detail,
        }
        rows.append(
            {
                "run_id": run_id,
                "snapshot_date": snapshot_date,
                "instrument_type": instrument_type,
                "symbol": symbol,
                "provider": provider,
                "raw_text": (brief + "\n\n" + detail).strip(),
                "json_text": json.dumps(json_payload, ensure_ascii=False, separators=(",", ":")),
                "is_valid": True,
                "quality_flags": ["panel_top10"],
            }
        )
    return rows
