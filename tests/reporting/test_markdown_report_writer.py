"""Markdown 报告写入测试。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


def test_write_provider_report_creates_expected_file(tmp_path: Path) -> None:
    from reporting.markdown_report_writer import write_provider_report  # noqa: E402

    data_root = tmp_path / "data_root"
    data_root.mkdir(parents=True, exist_ok=True)

    report = {
        "schema_version": "v1",
        "instrument_type": "stock",
        "snapshot_date": "2026-04-09",
        "provider": "qwen",
        "data_overview": {"notes": ["n1"]},
        "reversal_patterns": [{"pattern_id": "D:-1->0", "sample_count": 3, "metrics": {}}],
        "signals": {"strong_entry_signals": ["s1"], "watch_entry_signals": [], "risk_signals": []},
        "top10": [
            {
                "symbol": f"S{i}",
                "signal_strength": "watch",
                "confidence": 0.7,
                "entry_logic_brief": "brief",
                "entry_logic_detail": "detail",
            }
            for i in range(1, 11)
        ],
        "followups": ["f1"],
    }

    out_path = write_provider_report(
        data_root=str(data_root),
        snapshot_date="2026-04-09",
        run_id="run-x",
        instrument_type="stock",
        provider="qwen",
        report_json=report,
    )

    assert out_path.exists()
    text = out_path.read_text(encoding="utf-8")
    assert "stock / qwen" in text
    assert "Top10" in text
    assert "S1" in text


def test_write_run_summary_creates_expected_file(tmp_path: Path) -> None:
    from reporting.markdown_report_writer import write_run_summary  # noqa: E402

    data_root = tmp_path / "data_root"
    data_root.mkdir(parents=True, exist_ok=True)

    decisions = [
        {
            "snapshot_date": "2026-04-09",
            "instrument_type": "stock",
            "symbol": "AAPL",
            "votes": 2,
            "tier": 1,
            "rank_in_list": 1,
            "summary_rationale": "r1",
        },
        {
            "snapshot_date": "2026-04-09",
            "instrument_type": "etf",
            "symbol": "159100",
            "votes": 1,
            "tier": 2,
            "rank_in_list": 1,
            "summary_rationale": "r2",
        },
    ]
    reports = [
        {"instrument_type": "stock", "provider": "qwen", "is_valid": 1, "quality_flags": "[]"},
        {"instrument_type": "etf", "provider": "qwen", "is_valid": 1, "quality_flags": "[]"},
    ]

    out_path = write_run_summary(
        data_root=str(data_root),
        snapshot_date="2026-04-09",
        run_id="run-x",
        decisions=decisions,
        llm_reports=reports,
    )
    assert out_path.name == "summary.md"
    assert out_path.exists()
    text = out_path.read_text(encoding="utf-8")
    assert "Run run-x" in text
    assert "## Stocks" in text
    assert "AAPL" in text
    assert "## ETFs" in text
    assert "159100" in text
