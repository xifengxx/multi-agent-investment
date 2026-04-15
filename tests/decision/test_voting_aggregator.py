"""投票聚合器 VotingAggregator 的行为测试。

覆盖：
- 分层（>=4 / =3 / =2）
- Top10 补齐（优先高层，不足时用下一层补齐，最多 10 条）
- stock / etf 分开处理
- 排序稳定性（输入顺序变化时输出仍保持确定性）
- 同票处理策略（同票时按置信度/符号排序）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


def _build_votes(
    *,
    snapshot_date: str,
    instrument_type: str,
    symbol: str,
    buy_votes: int,
    total_votes: int,
    base_confidence: float = 0.6,
) -> list[dict]:
    """构造最小投票 DTO 列表（dict），用于测试聚合逻辑。"""
    votes: list[dict] = []
    for idx in range(total_votes):
        provider = f"agent-{idx+1}"
        is_buy = idx < buy_votes
        votes.append(
            {
                "snapshot_date": snapshot_date,
                "instrument_type": instrument_type,
                "symbol": symbol,
                "provider": provider,
                "recommendation": "BUY" if is_buy else "HOLD",
                "confidence": base_confidence + (0.01 * idx),
                "rationale": [f"{provider}:{'buy' if is_buy else 'hold'}"],
            }
        )
    return votes


def test_voting_aggregator_module_exists() -> None:
    """确保模块存在且可导入（避免以 ImportError 形式报错）。"""
    try:
        from decision.voting_aggregator import VotingAggregator  # noqa: F401
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing module: decision.voting_aggregator ({exc})")


def test_tiering_and_top10_fill_are_applied_per_instrument_type() -> None:
    """stock/etf 应分别分层并按 Top10 补齐规则生成榜单。"""
    from decision.voting_aggregator import VotingAggregator

    snapshot_date = "2026-04-15"
    inputs: list[dict] = []

    # stock: tier1(>=4) 4 个，tier2(=3) 4 个，tier3(=2) 4 个 => 共 12，Top10 应为 4+4+2
    for i in range(4):
        inputs += _build_votes(
            snapshot_date=snapshot_date,
            instrument_type="stock",
            symbol=f"STK_T1_{i}",
            buy_votes=4,
            total_votes=5,
            base_confidence=0.70,
        )
    for i in range(4):
        inputs += _build_votes(
            snapshot_date=snapshot_date,
            instrument_type="stock",
            symbol=f"STK_T2_{i}",
            buy_votes=3,
            total_votes=5,
            base_confidence=0.60,
        )
    for i in range(4):
        inputs += _build_votes(
            snapshot_date=snapshot_date,
            instrument_type="stock",
            symbol=f"STK_T3_{i}",
            buy_votes=2,
            total_votes=5,
            base_confidence=0.50,
        )

    # etf: 仅 tier2 2 个 + tier3 8 个 => Top10=10
    for i in range(2):
        inputs += _build_votes(
            snapshot_date=snapshot_date,
            instrument_type="etf",
            symbol=f"ETF_T2_{i}",
            buy_votes=3,
            total_votes=5,
            base_confidence=0.55,
        )
    for i in range(8):
        inputs += _build_votes(
            snapshot_date=snapshot_date,
            instrument_type="etf",
            symbol=f"ETF_T3_{i}",
            buy_votes=2,
            total_votes=5,
            base_confidence=0.45,
        )

    aggregator = VotingAggregator()
    result = aggregator.aggregate_top10(inputs, top_n=10)

    assert set(result.keys()) == {"stock", "etf"}
    assert len(result["stock"]) == 10
    assert len(result["etf"]) == 10

    stock_tiers = [item["tier"] for item in result["stock"]]
    assert stock_tiers[:4] == [1, 1, 1, 1]
    assert stock_tiers[4:8] == [2, 2, 2, 2]
    assert stock_tiers[8:] == [3, 3]

    # rank_in_list 应从 1 开始连续
    assert [item["rank_in_list"] for item in result["stock"]] == list(range(1, 11))
    assert [item["rank_in_list"] for item in result["etf"]] == list(range(1, 11))


def test_sorting_is_deterministic_when_input_order_changes() -> None:
    """输入顺序变化时，输出应保持确定性（不依赖 dict/set 遍历顺序）。"""
    from decision.voting_aggregator import VotingAggregator

    snapshot_date = "2026-04-15"
    inputs: list[dict] = []

    # 构造同层、同票但符号不同的标的，用于验证确定性排序（按 tie-breaker）
    inputs += _build_votes(
        snapshot_date=snapshot_date,
        instrument_type="stock",
        symbol="AAA",
        buy_votes=3,
        total_votes=5,
        base_confidence=0.60,
    )
    inputs += _build_votes(
        snapshot_date=snapshot_date,
        instrument_type="stock",
        symbol="BBB",
        buy_votes=3,
        total_votes=5,
        base_confidence=0.60,
    )
    inputs += _build_votes(
        snapshot_date=snapshot_date,
        instrument_type="stock",
        symbol="CCC",
        buy_votes=4,
        total_votes=5,
        base_confidence=0.40,
    )

    aggregator = VotingAggregator()
    forward = aggregator.aggregate_top10(inputs, top_n=10)["stock"]
    backward = aggregator.aggregate_top10(list(reversed(inputs)), top_n=10)["stock"]

    assert [item["symbol"] for item in forward] == [item["symbol"] for item in backward]


def test_tie_breaker_prefers_higher_confidence_then_symbol() -> None:
    """同票处理策略：同票同层时，优先平均置信度更高；仍相同则按 symbol 升序。"""
    from decision.voting_aggregator import VotingAggregator

    snapshot_date = "2026-04-15"

    # 两个标的同为 3 票（tier2），但平均置信度不同
    inputs = (
        _build_votes(
            snapshot_date=snapshot_date,
            instrument_type="stock",
            symbol="LOWCONF",
            buy_votes=3,
            total_votes=5,
            base_confidence=0.50,
        )
        + _build_votes(
            snapshot_date=snapshot_date,
            instrument_type="stock",
            symbol="HIGHCONF",
            buy_votes=3,
            total_votes=5,
            base_confidence=0.80,
        )
        # 再加两个同置信度，用于验证 symbol 升序
        + _build_votes(
            snapshot_date=snapshot_date,
            instrument_type="stock",
            symbol="ALPHA",
            buy_votes=3,
            total_votes=5,
            base_confidence=0.70,
        )
        + _build_votes(
            snapshot_date=snapshot_date,
            instrument_type="stock",
            symbol="BETA",
            buy_votes=3,
            total_votes=5,
            base_confidence=0.70,
        )
    )

    result = VotingAggregator().aggregate_top10(inputs, top_n=10)["stock"]
    symbols = [item["symbol"] for item in result]

    # tier2 的 4 个都在，按：HIGHCONF(0.80) > (0.70: ALPHA,BETA按字母) > LOWCONF(0.50)
    assert symbols[:4] == ["HIGHCONF", "ALPHA", "BETA", "LOWCONF"]


def test_aggregate_for_run_returns_records_ready_for_persistence() -> None:
    """aggregate_for_run 应产出满足 decisions 表 schema 的记录列表。"""
    from decision.voting_aggregator import VotingAggregator

    snapshot_date = "2026-04-15"
    votes = (
        _build_votes(
            snapshot_date=snapshot_date,
            instrument_type="stock",
            symbol="AAPL",
            buy_votes=4,
            total_votes=5,
        )
        + _build_votes(
            snapshot_date=snapshot_date,
            instrument_type="etf",
            symbol="SPY",
            buy_votes=3,
            total_votes=5,
        )
    )

    decisions = VotingAggregator().aggregate_for_run(run_id="run-001", votes=votes, top_n=10)
    assert len(decisions) == 2

    required_keys = {
        "run_id",
        "snapshot_date",
        "instrument_type",
        "symbol",
        "votes",
        "tier",
        "rank_in_list",
        "summary_rationale",
    }
    assert all(required_keys.issubset(set(d.keys())) for d in decisions)
    assert {d["instrument_type"] for d in decisions} == {"stock", "etf"}
