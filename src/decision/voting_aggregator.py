"""投票聚合器：将多 Agent 的 LLM 输出投票汇总为决策榜单。

本模块聚合逻辑只依赖结构化投票列表（最小 DTO），不引入额外依赖。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal


Tier = Literal[1, 2, 3]


@dataclass(frozen=True)
class VoteDTO:
    """最小投票 DTO，用于承载一条 agent 对某个标的的建议。"""

    snapshot_date: str
    instrument_type: str
    symbol: str
    provider: str
    recommendation: str
    confidence: float | None
    rationale: list[str]


def _coerce_vote_dto(item: dict[str, Any]) -> VoteDTO:
    """将 dict 形式的输入投票强制转换为 VoteDTO。"""
    confidence_val = item.get("confidence")
    confidence_norm: float | None
    if isinstance(confidence_val, (int, float)):
        confidence_norm = float(confidence_val)
    else:
        confidence_norm = None
    rationale_val = item.get("rationale")
    if isinstance(rationale_val, list):
        rationale_norm = [x for x in rationale_val if isinstance(x, str)]
    elif isinstance(rationale_val, str):
        rationale_norm = [rationale_val]
    else:
        rationale_norm = []
    return VoteDTO(
        snapshot_date=str(item.get("snapshot_date", "")),
        instrument_type=str(item.get("instrument_type", "")),
        symbol=str(item.get("symbol", "")),
        provider=str(item.get("provider", "")),
        recommendation=str(item.get("recommendation", "")),
        confidence=confidence_norm,
        rationale=rationale_norm,
    )


def _is_buy(recommendation: str) -> bool:
    """判断是否为 BUY 票（容错：大小写/常见别名）。"""
    rec_raw = (recommendation or "").strip()
    if not rec_raw:
        return False

    # 中文兼容（常见输出）
    if rec_raw in {"买入", "强烈买入", "强力买入"}:
        return True

    rec = rec_raw.strip().upper()
    rec = rec.replace("-", "_").replace(" ", "_")
    while "__" in rec:
        rec = rec.replace("__", "_")
    return rec in {"BUY", "LONG", "STRONG_BUY", "STRONGBUY"}


def _tier_for_votes(votes: int, *, thresholds: tuple[int, int, int]) -> Tier | None:
    """根据 buy 票数计算分层。

    thresholds 含义：
    - thresholds[0]：tier1 的最小票数（>=）
    - thresholds[1]：tier2 的最小票数（>=）
    - thresholds[2]：tier3 的最小票数（>=）
    """
    t1, t2, t3 = thresholds
    if votes >= t1:
        return 1
    if votes >= t2:
        return 2
    if votes >= t3:
        return 3
    return None


def _thresholds_for_min_effective_providers(min_effective_providers: int | None) -> tuple[int, int, int]:
    """根据最小有效 provider 数返回投票分层阈值。"""
    if min_effective_providers is None:
        return (4, 3, 2)
    if int(min_effective_providers) <= 2:
        return (2, 1, 1)
    return (4, 3, 2)


def _mean(values: Iterable[float]) -> float | None:
    """计算平均值，空集合返回 None。"""
    total = 0.0
    count = 0
    for val in values:
        total += val
        count += 1
    if count == 0:
        return None
    return total / count


class VotingAggregator:
    """将投票聚合为分层 TopN 决策列表，并分别处理 stock/etf。"""

    def aggregate_top10(
        self,
        votes: list[dict[str, Any]] | list[VoteDTO],
        *,
        top_n: int = 10,
        min_effective_providers: int | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """聚合投票并返回按 instrument_type 分组的 TopN 决策列表。

        输入：
        - votes：可以是 dict 列表（最小 DTO）或 VoteDTO 列表
          dict 最小字段约定：
          - snapshot_date, instrument_type, symbol, provider, recommendation, confidence, rationale

        输出（每项为 dict）字段：
        - snapshot_date, instrument_type, symbol
        - votes: buy 票数
        - tier: 1/2/3
        - rank_in_list: 1..N（按 instrument_type 内部）
        - summary_rationale: 简要汇总（当前实现为拼接前若干条 rationale）

        规则：
        - 分层：
          - 当 min_effective_providers <= 2：>=2 / >=1 / >=1（用于联调）
          - 默认：>=4 / =3 / =2
        - TopN 补齐：优先 tier1，不足用 tier2，再不足用 tier3，最多 top_n 条
        - 排序确定性：同层先按 votes 降序，再按平均 confidence 降序，最后按 symbol 升序
        """
        normalized_votes = [v if isinstance(v, VoteDTO) else _coerce_vote_dto(v) for v in votes]
        thresholds = _thresholds_for_min_effective_providers(min_effective_providers)

        grouped: dict[tuple[str, str], list[VoteDTO]] = {}
        for vote in normalized_votes:
            key = (vote.instrument_type, vote.symbol)
            grouped.setdefault(key, []).append(vote)

        per_type_candidates: dict[str, list[dict[str, Any]]] = {}
        for (instrument_type, symbol), items in grouped.items():
            buy_votes = sum(1 for v in items if _is_buy(v.recommendation))
            tier = _tier_for_votes(buy_votes, thresholds=thresholds)
            if tier is None:
                continue
            confidences = [v.confidence for v in items if isinstance(v.confidence, float)]
            mean_conf = _mean(confidences) or 0.0
            snapshot_date = items[0].snapshot_date
            # rationale 汇总：拼接每条投票的第一条 rationale，保留顺序但最终输出需确定性
            rationale_pieces: list[str] = []
            for v in items:
                if v.rationale:
                    rationale_pieces.append(v.rationale[0])
            rationale_pieces_sorted = sorted(rationale_pieces)
            summary_rationale = ";".join(rationale_pieces_sorted[:5])

            per_type_candidates.setdefault(instrument_type, []).append(
                {
                    "snapshot_date": snapshot_date,
                    "instrument_type": instrument_type,
                    "symbol": symbol,
                    "votes": buy_votes,
                    "tier": int(tier),
                    "_mean_confidence": mean_conf,  # 内部排序字段
                    "summary_rationale": summary_rationale,
                }
            )

        result: dict[str, list[dict[str, Any]]] = {}
        for instrument_type, candidates in per_type_candidates.items():
            # 分层并排序（确定性）
            tiers: dict[int, list[dict[str, Any]]] = {1: [], 2: [], 3: []}
            for item in candidates:
                tiers[int(item["tier"])].append(item)

            def sort_key(x: dict[str, Any]) -> tuple:
                return (-int(x["votes"]), -float(x["_mean_confidence"]), str(x["symbol"]))

            ordered: list[dict[str, Any]] = []
            for t in (1, 2, 3):
                tier_items = sorted(tiers[t], key=sort_key)
                ordered.extend(tier_items)

            top = ordered[:top_n]
            for idx, item in enumerate(top, start=1):
                item["rank_in_list"] = idx
                item.pop("_mean_confidence", None)
            result[instrument_type] = top

        # 保证缺失类型返回空列表（用于调用方更稳定）
        result.setdefault("stock", [])
        result.setdefault("etf", [])
        return result

    def aggregate_for_run(
        self,
        *,
        run_id: str,
        votes: list[dict[str, Any]] | list[VoteDTO],
        top_n: int = 10,
        min_effective_providers: int | None = None,
    ) -> list[dict[str, Any]]:
        """聚合投票并产出可直接落库到 decisions 表的记录列表。

        输出字段满足 decisions 表 schema：
        - run_id, snapshot_date, instrument_type, symbol, votes, tier, rank_in_list, summary_rationale
        """
        grouped = self.aggregate_top10(votes, top_n=top_n, min_effective_providers=min_effective_providers)
        decisions: list[dict[str, Any]] = []
        for instrument_type, items in grouped.items():
            for item in items:
                decisions.append(
                    {
                        "run_id": run_id,
                        "snapshot_date": item["snapshot_date"],
                        "instrument_type": instrument_type,
                        "symbol": item["symbol"],
                        "votes": int(item["votes"]),
                        "tier": int(item["tier"]),
                        "rank_in_list": int(item["rank_in_list"]),
                        "summary_rationale": str(item["summary_rationale"]),
                    }
                )
        # 维持确定性：按 instrument_type 再按 rank 排序
        decisions.sort(key=lambda x: (str(x["instrument_type"]), int(x["rank_in_list"])))
        return decisions
