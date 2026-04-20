"""Panel Output Validator 测试：报告级 JSON 校验与 Top10 展开。"""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


def _make_base_report(*, instrument_type: str) -> dict:
    report = {
        "schema_version": "v1",
        "instrument_type": instrument_type,
        "snapshot_date": "2026-04-09",
        "provider": "qwen",
        "data_overview": {
            "sheet_count": 2,
            "date_range": {"start": "2026-04-08", "end": "2026-04-09"},
            "symbol_count": 2,
            "data_completeness": {
                "missing_price_ratio": 0.0 if instrument_type == "stock" else 1.0,
                "missing_volume_ratio": 0.0 if instrument_type == "stock" else 1.0,
                "missing_trend_ratio": 0.0,
            },
            "notes": [],
        },
        "reversal_patterns": [
            {
                "pattern_id": "D:-1->0",
                "pattern_name": "日线空头转静默",
                "pattern_definition": "X_D_Trend_State 从 -1 变为 0",
                "sample_count": 12,
                "metrics": {
                    "avg_return_N": {"N1": 0.01, "N3": 0.02, "N5": 0.03, "N10": 0.05},
                    "win_rate_N": {"N1": 0.6, "N3": 0.62, "N5": 0.64, "N10": 0.66},
                    "avg_holding_return": 0.07,
                    "max_drawdown": -0.05,
                },
                "observations": ["o1"],
            }
        ],
        "multi_timeframe_analysis": {
            "needs_week_month_confirmation": ["n1"],
            "resonance_is_stronger": ["r1"],
            "lead_lag_relationships": ["l1"],
        },
        "signals": {
            "strong_entry_signals": ["s1"],
            "watch_entry_signals": ["w1"],
            "risk_signals": ["k1"],
        },
        "top10": [
            {
                "symbol": f"S{i}",
                "signal_strength": "strong" if i <= 3 else "watch",
                "confidence": 0.8,
                "entry_logic_brief": "brief",
                "entry_logic_detail": "detail",
            }
            for i in range(1, 11)
        ],
        "followups": ["f1"],
    }
    if instrument_type == "etf":
        report["reversal_patterns"][0]["metrics"] = {
            "avg_return_N": None,
            "win_rate_N": None,
            "avg_holding_return": None,
            "max_drawdown": None,
        }
        report["data_overview"]["notes"] = ["ETF 缺 Price，收益类指标暂不计算"]
    return report


def test_validate_stock_report_ok() -> None:
    from analysis.panel_output_validator import validate_panel_output  # noqa: E402

    raw = json.dumps(_make_base_report(instrument_type="stock"), ensure_ascii=False)
    result = validate_panel_output(
        raw_text=raw,
        expected_snapshot_date="2026-04-09",
        expected_instrument_type="stock",
        provider_name="qwen",
    )
    assert result.is_valid is True
    assert len(result.top10_items) == 10


def test_validate_etf_report_requires_null_metrics() -> None:
    from analysis.panel_output_validator import validate_panel_output  # noqa: E402

    bad = _make_base_report(instrument_type="etf")
    bad["reversal_patterns"][0]["metrics"]["avg_return_N"] = {"N1": 0.01}
    raw = json.dumps(bad, ensure_ascii=False)
    result = validate_panel_output(
        raw_text=raw,
        expected_snapshot_date="2026-04-09",
        expected_instrument_type="etf",
        provider_name="qwen",
    )
    assert result.is_valid is False
    assert "etf_metrics_not_null" in result.quality_flags


def test_validate_report_rejects_unknown_pattern_id() -> None:
    from analysis.panel_output_validator import validate_panel_output  # noqa: E402

    bad = _make_base_report(instrument_type="stock")
    bad["reversal_patterns"][0]["pattern_id"] = "UNKNOWN"
    raw = json.dumps(bad, ensure_ascii=False)
    result = validate_panel_output(
        raw_text=raw,
        expected_snapshot_date="2026-04-09",
        expected_instrument_type="stock",
        provider_name="qwen",
    )
    assert result.is_valid is False
    assert "unknown_pattern_id" in result.quality_flags


def test_validate_report_rejects_unknown_signal_strength() -> None:
    from analysis.panel_output_validator import validate_panel_output  # noqa: E402

    bad = _make_base_report(instrument_type="stock")
    bad["top10"][0]["signal_strength"] = "stronger"
    raw = json.dumps(bad, ensure_ascii=False)
    result = validate_panel_output(
        raw_text=raw,
        expected_snapshot_date="2026-04-09",
        expected_instrument_type="stock",
        provider_name="qwen",
    )
    assert result.is_valid is False
    assert "unknown_signal_strength" in result.quality_flags


def test_expand_top10_to_llm_outputs_builds_buy_votes_payload() -> None:
    from analysis.panel_output_validator import expand_top10_to_llm_outputs, validate_panel_output  # noqa: E402

    raw = json.dumps(_make_base_report(instrument_type="stock"), ensure_ascii=False)
    result = validate_panel_output(
        raw_text=raw,
        expected_snapshot_date="2026-04-09",
        expected_instrument_type="stock",
        provider_name="qwen",
    )
    assert result.is_valid is True

    outputs = expand_top10_to_llm_outputs(
        run_id="run-x",
        snapshot_date="2026-04-09",
        instrument_type="stock",
        provider="qwen",
        top10_items=result.top10_items,
    )
    assert len(outputs) == 10
    first = outputs[0]
    assert first["provider"] == "qwen"
    assert first["instrument_type"] == "stock"
    parsed = json.loads(first["json_text"])
    assert parsed["recommendation"] == "BUY"
    assert parsed["rationale"]


def test_validate_report_parses_json_from_markdown_fence() -> None:
    """LLM 常返回 ```json code fence```，应能被 validator 正确解析。"""
    from analysis.panel_output_validator import validate_panel_output  # noqa: E402

    payload = _make_base_report(instrument_type="stock")
    raw = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
    result = validate_panel_output(
        raw_text=raw,
        expected_snapshot_date="2026-04-09",
        expected_instrument_type="stock",
        provider_name="qwen",
    )
    assert result.is_valid is True


def test_validate_report_autofills_entry_logic_brief_when_missing() -> None:
    from analysis.panel_output_validator import validate_panel_output  # noqa: E402

    payload = _make_base_report(instrument_type="stock")
    for row in payload["top10"]:
        row.pop("entry_logic_brief", None)
        row["entry_logic_detail"] = "第一句摘要。第二句补充。"

    raw = json.dumps(payload, ensure_ascii=False)
    result = validate_panel_output(
        raw_text=raw,
        expected_snapshot_date="2026-04-09",
        expected_instrument_type="stock",
        provider_name="qwen",
    )
    assert result.is_valid is True
    assert result.top10_items
    assert result.top10_items[0]["entry_logic_brief"]
