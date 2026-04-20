"""消息格式化模块（用于通知渠道展示）。"""

from __future__ import annotations

from typing import Any


def format_recommendation_message(
    *,
    run_id: str,
    decisions: list[dict[str, Any]],
    reports_dir: str | None = None,
    summary_md_path: str | None = None,
) -> str:
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
    tail: list[str] = []
    if summary_md_path:
        tail.append(f"Summary: {summary_md_path}")
    if reports_dir:
        tail.append(f"Reports: {reports_dir}")
    parts = [header, *sections]
    if tail:
        parts.append("\n".join(tail))
    return "\n\n".join(parts).strip()


def format_weekly_review_message(*, run_id: str, report: dict[str, Any]) -> str:
    """将周度复盘 report 格式化为可直接发送的文本消息。

    约定：
    - report 来自 review.weekly_review_service.compute_weekly_report
    - 输出保持确定性：同一输入应产生完全一致的字符串
    """
    week_start = str(report.get("week_start", "")).strip()
    week_end = str(report.get("week_end", "")).strip()
    trade_count = report.get("trade_count", 0)
    unique_symbols = report.get("unique_symbols", 0)
    by_type = report.get("by_instrument_type") or {}

    def _render_type_line(itype: str) -> str:
        bucket = by_type.get(itype) or {}
        tc = bucket.get("trade_count", 0)
        us = bucket.get("unique_symbols", 0)
        return f"- {itype}: trades={tc}, symbols={us}"

    header = f"Weekly Review / Run {run_id}".strip()
    range_line = f"Range: {week_start} ~ {week_end}".strip()
    summary = f"Trades: {trade_count}, Unique Symbols: {unique_symbols}".strip()
    sections = ["By Type:", _render_type_line("stock"), _render_type_line("etf")]
    return "\n".join([header, range_line, summary, *sections]).strip()
