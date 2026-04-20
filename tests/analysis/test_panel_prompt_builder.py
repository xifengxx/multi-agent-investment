"""Panel Prompt Builder 测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


def test_load_panel_prompt_reads_file_when_path_exists(tmp_path: Path) -> None:
    from analysis.panel_prompt_builder import PanelPromptBuilder, load_panel_prompt  # noqa: E402

    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("MY_PANEL_PROMPT", encoding="utf-8")

    prompt_text = load_panel_prompt(prompt_path=str(prompt_path))
    builder = PanelPromptBuilder(prompt_text=prompt_text)
    prompt = builder.build(
        snapshot_date="2026-04-09",
        instrument_type="stock",
        provider_name="qwen",
        panel_json={
            "sheet_count": 2,
            "date_range": {"start": "2026-04-08", "end": "2026-04-09"},
            "symbol_count": 2,
            "data_completeness": {"missing_price_ratio": 0.0, "missing_volume_ratio": 0.0, "missing_trend_ratio": 0.0},
            "symbols": [],
        },
    )

    assert "MY_PANEL_PROMPT" in prompt
    assert "snapshot_date=2026-04-09" in prompt
    assert "instrument_type=stock" in prompt
    assert "provider=qwen" in prompt
    assert "\"sheet_count\":2" in prompt.replace(" ", "")
    assert "schema_version" in prompt


def test_build_summary_limits_prompt_length() -> None:
    """当摘要面板数据过大时，应将 summary prompt 限制在预算长度内。"""
    from analysis.panel_prompt_builder import PanelPromptBuilder  # noqa: E402

    builder = PanelPromptBuilder(prompt_text="TEST_PROMPT")
    large_description = "D" * 2_000
    large_series_point = {
        "date": "2026-04-15",
        "x_d_trend_state": 1,
        "x_w_trend_state": 1,
        "x_m_trend_state": 1,
        "price": 123.45,
        "volume": 67890,
    }
    panel_json = {
        "sheet_count": 500,
        "date_range": {"start": "2026-01-01", "end": "2026-04-15"},
        "symbol_count": 500,
        "data_completeness": {"missing_price_ratio": 0.0, "missing_volume_ratio": 0.0, "missing_trend_ratio": 0.0},
        "symbols": [
            {
                "symbol": f"SYM{i}",
                "description": large_description,
                "series": [large_series_point for _ in range(12)],
            }
            for i in range(1, 501)
        ],
    }

    prompt = builder.build_summary(
        snapshot_date="2026-04-15",
        instrument_type="stock",
        provider_name="qwen",
        panel_json=panel_json,
        max_symbols=120,
        max_points_per_symbol=6,
    )

    assert len(prompt) <= 120_000
    assert "input_panel_summary_json:" in prompt
    assert "\"symbols\"" in prompt


def test_build_summary_respects_symbol_and_point_limits() -> None:
    """摘要 prompt 应按 max_symbols / max_points_per_symbol 裁剪数据。"""
    from analysis.panel_prompt_builder import PanelPromptBuilder  # noqa: E402

    builder = PanelPromptBuilder(prompt_text="TEST_PROMPT")
    panel_json = {
        "sheet_count": 3,
        "date_range": {"start": "2026-04-13", "end": "2026-04-15"},
        "symbol_count": 3,
        "data_completeness": {"missing_price_ratio": 0.0, "missing_volume_ratio": 0.0, "missing_trend_ratio": 0.0},
        "symbols": [
            {
                "symbol": "AAA",
                "description": "A",
                "series": [
                    {"date": "2026-04-13", "x_d_trend_state": 0, "x_w_trend_state": 0, "x_m_trend_state": 0},
                    {"date": "2026-04-14", "x_d_trend_state": 1, "x_w_trend_state": 1, "x_m_trend_state": 1},
                ],
            },
            {
                "symbol": "BBB",
                "description": "B",
                "series": [
                    {"date": "2026-04-13", "x_d_trend_state": 0, "x_w_trend_state": 0, "x_m_trend_state": 0},
                    {"date": "2026-04-14", "x_d_trend_state": 1, "x_w_trend_state": 1, "x_m_trend_state": 1},
                ],
            },
            {
                "symbol": "CCC",
                "description": "C",
                "series": [
                    {"date": "2026-04-13", "x_d_trend_state": 0, "x_w_trend_state": 0, "x_m_trend_state": 0},
                    {"date": "2026-04-14", "x_d_trend_state": 1, "x_w_trend_state": 1, "x_m_trend_state": 1},
                ],
            },
        ],
    }
    prompt = builder.build_summary(
        snapshot_date="2026-04-15",
        instrument_type="stock",
        provider_name="qwen",
        panel_json=panel_json,
        max_symbols=2,
        max_points_per_symbol=1,
    )

    summary_json_text = prompt.split("input_panel_summary_json:\n", maxsplit=1)[1].split("\n\noutput_constraints:", maxsplit=1)[0]
    summary = json.loads(summary_json_text)
    assert len(summary["symbols"]) == 2
    assert len(summary["symbols"][0]["series"]) == 1


def test_build_file_prompt_does_not_embed_panel_json() -> None:
    """文件输入路径的 prompt 不应内嵌 input_panel_json。"""
    from analysis.panel_prompt_builder import PanelPromptBuilder  # noqa: E402

    builder = PanelPromptBuilder(prompt_text="TEST_PROMPT")
    prompt = builder.build_file_prompt(
        snapshot_date="2026-04-15",
        instrument_type="stock",
        provider_name="qwen",
    )
    assert "mode=file_input" in prompt
    assert "input_panel_json:" not in prompt
