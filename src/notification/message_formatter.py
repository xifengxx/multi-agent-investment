"""消息格式化模块（用于通知渠道展示）。"""

from __future__ import annotations

from typing import Any


def format_recommendation_message(*, run_id: str, decisions: list[dict[str, Any]]) -> str:
    """将决策榜单格式化为可直接发送的文本消息。

    约定：
    - decisions 为落库前/落库后都可用的最小字段 dict 列表
      期望字段：snapshot_date, instrument_type, symbol, votes, tier, rank_in_list, summary_rationale
    - 输出保持确定性：同一输入应产生完全一致的字符串
    """
    snapshot_date = str(decisions[0].get("snapshot_date")) if decisions else ""

    def _norm_type(val: Any) -> str:
        return str(val or "").strip().lower()

    groups: dict[str, list[dict[str, Any]]] = {"stock": [], "etf": []}
    for item in decisions:
        groups.setdefault(_norm_type(item.get("instrument_type")), []).append(item)

    def _sort_key(x: dict[str, Any]) -> tuple[int, str]:
        rank_raw = x.get("rank_in_list")
        try:
            rank = int(rank_raw)
        except (TypeError, ValueError):
            rank = 10**9
        return (rank, str(x.get("symbol", "")))

    def _render_section(title: str, items: list[dict[str, Any]]) -> str:
        ordered = sorted(items, key=_sort_key)
        if not ordered:
            return f"{title}:\n- (empty)"
        lines: list[str] = [f"{title}:"]
        for it in ordered:
            symbol = str(it.get("symbol", "")).strip()
            rank = it.get("rank_in_list", "")
            votes = it.get("votes", "")
            tier = it.get("tier", "")
            rationale = str(it.get("summary_rationale", "")).strip()
            prefix = f"{rank}. " if str(rank).strip() else "- "
            tail_parts = []
            if str(tier).strip():
                tail_parts.append(f"tier={tier}")
            if str(votes).strip():
                tail_parts.append(f"votes={votes}")
            tail = f" ({', '.join(tail_parts)})" if tail_parts else ""
            rationale_tail = f" - {rationale}" if rationale else ""
            lines.append(f"{prefix}{symbol}{tail}{rationale_tail}")
        return "\n".join(lines)

    header = f"Run {run_id} / {snapshot_date}".strip(" /")
    sections = [
        _render_section("Stocks", groups.get("stock", [])),
        _render_section("ETFs", groups.get("etf", [])),
    ]
    return "\n\n".join([header, *sections]).strip()

