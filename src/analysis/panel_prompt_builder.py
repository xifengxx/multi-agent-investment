"""Panel Analysis Prompt Builder：用户 Prompt + 面板 JSON -> 单次调用 Prompt。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from analysis.panel_schema import PATTERN_IDS, SCHEMA_VERSION, SIGNAL_STRENGTHS


_DEFAULT_PROMPT_TEXT = "请作为量化交易分析师，基于输入面板数据识别趋势反转模式并给出Top10建仓标的。"
_SUMMARY_PROMPT_MAX_LENGTH = 120_000


def load_panel_prompt(*, prompt_path: str) -> str:
    """加载用户自定义 Prompt（优先读取文件），否则返回默认 Prompt。"""
    p = Path(prompt_path.strip()) if prompt_path else None
    if p and p.exists() and p.is_file():
        return p.read_text(encoding="utf-8")
    return _DEFAULT_PROMPT_TEXT


class PanelPromptBuilder:
    """将面板数据封装为一次 LLM 调用的 prompt。"""

    def __init__(self, *, prompt_text: str) -> None:
        """初始化 builder。"""
        self._prompt_text = (prompt_text or "").strip() or _DEFAULT_PROMPT_TEXT

    def build(
        self,
        *,
        snapshot_date: str,
        instrument_type: str,
        provider_name: str,
        panel_json: dict[str, Any],
    ) -> str:
        """构造单次调用 prompt。"""
        panel_text = json.dumps(panel_json, ensure_ascii=False, separators=(",", ":"))
        pattern_ids = ", ".join(sorted(PATTERN_IDS))
        signal_strengths = ", ".join(sorted(SIGNAL_STRENGTHS))

        return (
            f"{self._prompt_text}\n\n"
            f"context:\n"
            f"- snapshot_date={snapshot_date}\n"
            f"- instrument_type={instrument_type}\n"
            f"- provider={provider_name}\n\n"
            f"input_panel_json:\n{panel_text}\n\n"
            f"output_constraints:\n"
            f"1) 只输出一个 JSON 对象，禁止输出 Markdown 或解释文字。\n"
            f"2) schema_version 固定为 \"{SCHEMA_VERSION}\"。\n"
            f"3) top10 必须为数组且长度恰好为 10。\n"
            f"4) top10[].signal_strength 只能是：{signal_strengths}\n"
            f"5) top10[].confidence 取值范围 0~1。\n"
            f"6) top10[] 必须包含 entry_logic_brief 与 entry_logic_detail。\n"
            f"7) reversal_patterns[].pattern_id 必须来自以下枚举：{pattern_ids}\n"
            f"8) instrument_type=etf 时，reversal_patterns[].metrics 下的收益类字段必须为 null，并在 data_overview.notes 说明原因。\n"
            f"9) JSON 必须包含字段：schema_version, instrument_type, snapshot_date, provider, data_overview, reversal_patterns, "
            f"multi_timeframe_analysis, signals, top10, followups。\n"
        )

    def build_file_prompt(
        self,
        *,
        snapshot_date: str,
        instrument_type: str,
        provider_name: str,
    ) -> str:
        """构造文件输入路径用 prompt（不内嵌 panel_json）。"""
        pattern_ids = ", ".join(sorted(PATTERN_IDS))
        signal_strengths = ", ".join(sorted(SIGNAL_STRENGTHS))
        return _render_file_prompt(
            prompt_text=self._prompt_text,
            snapshot_date=snapshot_date,
            instrument_type=instrument_type,
            provider_name=provider_name,
            signal_strengths=signal_strengths,
            pattern_ids=pattern_ids,
        )

    def build_summary(
        self,
        *,
        snapshot_date: str,
        instrument_type: str,
        provider_name: str,
        panel_json: dict[str, Any],
        max_symbols: int = 30,
        max_points_per_symbol: int = 2,
    ) -> str:
        """构造文本回退用摘要 prompt，避免携带全量面板造成超大输入。"""
        pattern_ids = ", ".join(sorted(PATTERN_IDS))
        signal_strengths = ", ".join(sorted(SIGNAL_STRENGTHS))
        symbols_limit = max(0, int(max_symbols))
        points_limit = max(0, int(max_points_per_symbol))
        while True:
            summary = _summarize_panel_json(
                panel_json=panel_json,
                max_symbols=symbols_limit,
                max_points_per_symbol=points_limit,
            )
            summary_text = json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
            prompt = _render_summary_prompt(
                prompt_text=self._prompt_text,
                snapshot_date=snapshot_date,
                instrument_type=instrument_type,
                provider_name=provider_name,
                summary_text=summary_text,
                signal_strengths=signal_strengths,
                pattern_ids=pattern_ids,
            )
            if len(prompt) <= _SUMMARY_PROMPT_MAX_LENGTH:
                return prompt
            if symbols_limit > 1:
                symbols_limit = max(1, symbols_limit // 2)
                continue
            if points_limit > 1:
                points_limit = max(1, points_limit // 2)
                continue
            return prompt[:_SUMMARY_PROMPT_MAX_LENGTH]


def _render_summary_prompt(
    *,
    prompt_text: str,
    snapshot_date: str,
    instrument_type: str,
    provider_name: str,
    summary_text: str,
    signal_strengths: str,
    pattern_ids: str,
) -> str:
    """渲染 summary fallback prompt 文本。"""
    return (
        f"{prompt_text}\n\n"
        f"context:\n"
        f"- snapshot_date={snapshot_date}\n"
        f"- instrument_type={instrument_type}\n"
        f"- provider={provider_name}\n"
        f"- mode=summary_fallback\n\n"
        f"input_panel_summary_json:\n{summary_text}\n\n"
        f"output_constraints:\n"
        f"1) 只输出一个 JSON 对象，禁止输出 Markdown 或解释文字。\n"
        f"2) schema_version 固定为 \"{SCHEMA_VERSION}\"。\n"
        f"3) top10 必须为数组且长度恰好为 10。\n"
        f"4) top10[].signal_strength 只能是：{signal_strengths}\n"
        f"5) top10[].confidence 取值范围 0~1。\n"
        f"6) top10[] 必须包含 entry_logic_brief 与 entry_logic_detail。\n"
        f"7) reversal_patterns[].pattern_id 必须来自以下枚举：{pattern_ids}\n"
        f"8) instrument_type=etf 时，reversal_patterns[].metrics 下的收益类字段必须为 null，并在 data_overview.notes 说明原因。\n"
        f"9) JSON 必须包含字段：schema_version, instrument_type, snapshot_date, provider, data_overview, reversal_patterns, "
        f"multi_timeframe_analysis, signals, top10, followups。\n"
    )


def _render_file_prompt(
    *,
    prompt_text: str,
    snapshot_date: str,
    instrument_type: str,
    provider_name: str,
    signal_strengths: str,
    pattern_ids: str,
) -> str:
    """渲染文件输入路径用 prompt 文本（不包含面板 JSON）。"""
    return (
        f"{prompt_text}\n\n"
        f"context:\n"
        f"- snapshot_date={snapshot_date}\n"
        f"- instrument_type={instrument_type}\n"
        f"- provider={provider_name}\n"
        f"- mode=file_input\n\n"
        f"input_files:\n"
        f"- 已提供附件 xlsx（按日期为子表的面板数据）。请基于附件进行整体分析。\n\n"
        f"output_constraints:\n"
        f"1) 只输出一个 JSON 对象，禁止输出 Markdown 或解释文字。\n"
        f"2) schema_version 固定为 \"{SCHEMA_VERSION}\"。\n"
        f"3) top10 必须为数组且长度恰好为 10。\n"
        f"4) top10[].signal_strength 只能是：{signal_strengths}\n"
        f"5) top10[].confidence 取值范围 0~1。\n"
        f"6) top10[] 必须包含 entry_logic_brief 与 entry_logic_detail。\n"
        f"7) reversal_patterns[].pattern_id 必须来自以下枚举：{pattern_ids}\n"
        f"8) instrument_type=etf 时，reversal_patterns[].metrics 下的收益类字段必须为 null，并在 data_overview.notes 说明原因。\n"
        f"9) JSON 必须包含字段：schema_version, instrument_type, snapshot_date, provider, data_overview, reversal_patterns, "
        f"multi_timeframe_analysis, signals, top10, followups。\n"
    )


def _summarize_panel_json(
    *,
    panel_json: dict[str, Any],
    max_symbols: int,
    max_points_per_symbol: int,
) -> dict[str, Any]:
    """提炼 panel 摘要，保留关键信号并限制输入规模。"""
    symbols = panel_json.get("symbols") or []
    compact_symbols: list[dict[str, Any]] = []
    for symbol_item in symbols[: max(0, int(max_symbols))]:
        item = symbol_item or {}
        series = item.get("series") or []
        clipped_series: list[dict[str, Any]] = []
        max_points = max(0, int(max_points_per_symbol))
        selected_points = [] if max_points == 0 else series[-max_points:]
        for point in selected_points:
            point_item = point or {}
            clipped_series.append(
                {
                    "date": point_item.get("date"),
                    "x_d_trend_state": point_item.get("x_d_trend_state"),
                    "x_w_trend_state": point_item.get("x_w_trend_state"),
                    "x_m_trend_state": point_item.get("x_m_trend_state"),
                    "price": point_item.get("price"),
                    "volume": point_item.get("volume"),
                }
            )
        compact_symbols.append(
            {
                "symbol": item.get("symbol"),
                "description": item.get("description"),
                "series": clipped_series,
            }
        )
    return {
        "sheet_count": panel_json.get("sheet_count"),
        "date_range": panel_json.get("date_range"),
        "symbol_count": panel_json.get("symbol_count"),
        "data_completeness": panel_json.get("data_completeness"),
        "symbols": compact_symbols,
    }
