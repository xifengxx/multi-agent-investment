"""消息格式化单测。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


from notification.message_formatter import format_recommendation_message  # noqa: E402


def test_format_recommendation_message_groups_by_type_and_is_deterministic() -> None:
    """推荐消息应按 instrument_type 分组并保持确定性排序。"""
    decisions = [
        {
            "snapshot_date": "2026-04-15",
            "instrument_type": "stock",
            "symbol": "AAPL",
            "votes": 4,
            "tier": 1,
            "rank_in_list": 2,
            "summary_rationale": "a",
        },
        {
            "snapshot_date": "2026-04-15",
            "instrument_type": "stock",
            "symbol": "MSFT",
            "votes": 3,
            "tier": 2,
            "rank_in_list": 1,
            "summary_rationale": "b",
        },
        {
            "snapshot_date": "2026-04-15",
            "instrument_type": "etf",
            "symbol": "SPY",
            "votes": 4,
            "tier": 1,
            "rank_in_list": 1,
            "summary_rationale": "c",
        },
    ]

    msg1 = format_recommendation_message(run_id="run-001", decisions=decisions)
    msg2 = format_recommendation_message(run_id="run-001", decisions=decisions)

    assert msg1 == msg2
    assert "2026-04-15" in msg1
    assert "run-001" in msg1
    # 分组标题
    assert "Stocks" in msg1
    assert "ETFs" in msg1
    # stock 内部应按 rank_in_list 升序
    assert msg1.index("1. MSFT") < msg1.index("2. AAPL")
    # etf 也应出现
    assert "1. SPY" in msg1


def test_format_recommendation_message_appends_summary_path_when_provided() -> None:
    decisions = [
        {
            "snapshot_date": "2026-04-15",
            "instrument_type": "stock",
            "symbol": "AAPL",
            "votes": 4,
            "tier": 1,
            "rank_in_list": 1,
            "summary_rationale": "a",
        }
    ]
    msg = format_recommendation_message(
        run_id="run-001",
        decisions=decisions,
        summary_md_path="/tmp/reports/2026-04-15/run-001/summary.md",
        reports_dir="/tmp/reports/2026-04-15/run-001",
    )
    assert "summary.md" in msg
    assert "/tmp/reports/2026-04-15/run-001" in msg
