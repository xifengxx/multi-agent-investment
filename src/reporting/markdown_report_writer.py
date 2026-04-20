"""Markdown 报告写入：按 provider×instrument_type 输出到 data_root/reports。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_run_summary(
    *,
    data_root: str,
    snapshot_date: str,
    run_id: str,
    decisions: list[dict[str, Any]],
    llm_reports: list[dict[str, Any]],
) -> Path:
    out_dir = Path(data_root) / "reports" / snapshot_date / run_id
    _ensure_dir(out_dir)

    out_path = out_dir / "summary.md"

    def _norm_type(v: Any) -> str:
        return str(v or "").strip().lower()

    def _int(v: Any) -> int | None:
        try:
            return int(v)
        except Exception:
            return None

    def _render_decisions(title: str, itype: str) -> list[str]:
        rows = [d for d in decisions if _norm_type(d.get("instrument_type")) == itype]
        rows = sorted(rows, key=lambda d: (_int(d.get("rank_in_list")) or 10**9, str(d.get("symbol") or "")))
        lines: list[str] = [f"## {title}", ""]
        if not rows:
            lines.append("- (empty)")
            lines.append("")
            return lines
        lines.append("| Rank | Symbol | Votes | Tier | Rationale |")
        lines.append("| ---: | :----- | ----: | ---: | :-------- |")
        for d in rows:
            rank = _int(d.get("rank_in_list"))
            symbol = str(d.get("symbol") or "").strip()
            votes = _int(d.get("votes"))
            tier = _int(d.get("tier"))
            rationale = str(d.get("summary_rationale") or "").strip().replace("\n", " ")
            lines.append(
                f"| {rank if rank is not None else ''} | {symbol} | {votes if votes is not None else ''} | {tier if tier is not None else ''} | {rationale} |"
            )
        lines.append("")
        return lines

    def _render_llm_reports() -> list[str]:
        if not llm_reports:
            return []
        lines: list[str] = ["## LLM Reports", ""]
        rows = sorted(
            llm_reports,
            key=lambda r: (_norm_type(r.get("provider")), _norm_type(r.get("instrument_type"))),
        )
        lines.append("| Provider | Type | Valid | Quality Flags | File |")
        lines.append("| :------- | :--- | ----: | :------------ | :--- |")
        for r in rows:
            provider = str(r.get("provider") or "").strip()
            itype = _norm_type(r.get("instrument_type"))
            valid = _int(r.get("is_valid"))
            flags = str(r.get("quality_flags") or "").strip()
            file_name = f"{itype}_{provider}.md" if provider and itype else ""
            lines.append(
                f"| {provider} | {itype} | {valid if valid is not None else ''} | {flags} | {file_name} |"
            )
        lines.append("")
        return lines

    lines: list[str] = []
    lines.append(f"# Run {run_id} / {snapshot_date}".strip(" /"))
    lines.append("")
    lines.extend(_render_decisions("Stocks", "stock"))
    lines.extend(_render_decisions("ETFs", "etf"))
    lines.extend(_render_llm_reports())
    lines.append("## Files")
    lines.append("")
    lines.append(f"- summary.md")
    lines.append(f"- (provider reports) *_*.md")
    lines.append("")

    out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return out_path


def write_provider_report(
    *,
    data_root: str,
    snapshot_date: str,
    run_id: str,
    instrument_type: str,
    provider: str,
    report_json: dict[str, Any],
) -> Path:
    out_dir = Path(data_root) / "reports" / snapshot_date / run_id
    _ensure_dir(out_dir)

    out_path = out_dir / f"{instrument_type}_{provider}.md"

    lines: list[str] = []
    lines.append(f"# {instrument_type} / {provider}")
    lines.append("")

    overview = report_json.get("data_overview") or {}
    notes = overview.get("notes") or []
    if notes:
        lines.append("## Data Overview")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")

    patterns = report_json.get("reversal_patterns") or []
    if patterns:
        lines.append("## Patterns")
        for p in patterns:
            pid = str((p or {}).get("pattern_id") or "")
            sc = (p or {}).get("sample_count")
            lines.append(f"- {pid} (sample_count={sc})")
        lines.append("")

    signals = report_json.get("signals") or {}
    lines.append("## Signals")
    for key in ("strong_entry_signals", "watch_entry_signals", "risk_signals"):
        items = signals.get(key) or []
        if not items:
            continue
        lines.append(f"### {key}")
        for it in items:
            lines.append(f"- {it}")
        lines.append("")

    top10 = report_json.get("top10") or []
    lines.append("## Top10")
    for item in top10:
        symbol = str((item or {}).get("symbol") or "")
        strength = str((item or {}).get("signal_strength") or "")
        confidence = (item or {}).get("confidence")
        brief = str((item or {}).get("entry_logic_brief") or "")
        detail = str((item or {}).get("entry_logic_detail") or "")
        lines.append(f"### {symbol} ({strength}, confidence={confidence})")
        if brief:
            lines.append(brief)
            lines.append("")
        if detail:
            lines.append(detail)
            lines.append("")

    followups = report_json.get("followups") or []
    if followups:
        lines.append("## Followups")
        for f in followups:
            lines.append(f"- {f}")
        lines.append("")

    out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return out_path
