"""一键执行 daily 并进行落库/报告校验的脚本。"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import load_config
from orchestrator.run_orchestrator import daily_run
from storage.db import open_sqlite_connection


@dataclass(frozen=True)
class RunVerificationResult:
    """一次 run 的验证结果摘要。"""

    run_id: str
    snapshot_date: str
    status: str
    error_message: str | None
    counts: dict[str, int]
    notification_status: str | None
    telegram_ok: bool | None
    telegram_message_id: int | None
    reports_dir: str | None
    report_files: list[str]


_EXPORT_LINE_RE = re.compile(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)\s*$")


def load_env_exports(env_file: Path) -> dict[str, str]:
    """解析 .env.local 中的 export 行，返回键值对（不写入环境）。"""
    if not env_file.exists():
        raise FileNotFoundError(str(env_file))

    result: dict[str, str] = {}
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        m = _EXPORT_LINE_RE.match(line)
        if not m:
            continue

        key = m.group(1)
        value_part = m.group(2).lstrip()
        if not value_part:
            result[key] = ""
            continue

        if value_part[0] in {"'", '"'}:
            quote = value_part[0]
            end = value_part.find(quote, 1)
            if end == -1:
                raise ValueError(f"{env_file.name} 行格式不合法（引号未闭合）: {raw}")
            value = value_part[1:end]
            result[key] = value
            continue

        value = value_part.split("#", 1)[0].strip()
        result[key] = value
    return result


def apply_env(*, exports: dict[str, str], overrides: dict[str, str]) -> None:
    """写入进程环境变量（覆盖同名键）。"""
    for k, v in exports.items():
        os.environ[str(k)] = str(v)
    for k, v in overrides.items():
        os.environ[str(k)] = str(v)


def _safe_int(value: Any) -> int | None:
    """尽可能将值转换为 int；失败则返回 None。"""
    try:
        return int(value)
    except Exception:
        return None


def verify_run(*, sqlite_path: Path, run_id: str, data_root: Path) -> RunVerificationResult:
    """读取 sqlite，汇总本次 run 的关键落库与报告生成情况。"""
    conn = open_sqlite_connection(sqlite_path)
    try:
        run_row = conn.execute(
            "SELECT snapshot_date,status,error_message FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if run_row is None:
            raise RuntimeError(f"sqlite 中找不到 run_id: {run_id}")

        snapshot_date = str(run_row["snapshot_date"] or "")
        status = str(run_row["status"] or "")
        error_message = str(run_row["error_message"]) if run_row["error_message"] is not None else None

        def count(table: str) -> int:
            row = conn.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE run_id = ?", (run_id,)).fetchone()
            return int(row["c"] if row else 0)

        counts = {
            "llm_reports": count("llm_reports"),
            "llm_outputs": count("llm_outputs"),
            "decisions": count("decisions"),
            "paper_trades": count("paper_trades"),
            "notifications": count("notifications"),
        }

        notif = conn.execute(
            "SELECT status, provider_response_json FROM notifications WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        notification_status = str(notif["status"]) if notif else None
        telegram_ok: bool | None = None
        telegram_message_id: int | None = None
        if notif and (notif["provider_response_json"] or "").strip():
            try:
                payload = json.loads(notif["provider_response_json"])
                telegram_ok = bool(payload.get("ok")) if isinstance(payload, dict) else None
                telegram_message_id = _safe_int(((payload.get("result") or {}) if isinstance(payload, dict) else {}).get("message_id"))
            except Exception:
                telegram_ok = None
                telegram_message_id = None

        reports_dir = None
        report_files: list[str] = []
        if snapshot_date:
            p = data_root / "reports" / snapshot_date / run_id
            reports_dir = str(p)
            if p.exists() and p.is_dir():
                report_files = sorted([x.name for x in p.glob("*.md")])

        return RunVerificationResult(
            run_id=run_id,
            snapshot_date=snapshot_date,
            status=status,
            error_message=error_message,
            counts=counts,
            notification_status=notification_status,
            telegram_ok=telegram_ok,
            telegram_message_id=telegram_message_id,
            reports_dir=reports_dir,
            report_files=report_files,
        )
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数。"""
    p = argparse.ArgumentParser(prog="run-daily-stable")
    p.add_argument("--env-file", default=".env.local", help="环境文件路径（包含 export 行）")
    p.add_argument("--sqlite-path", default="", help="覆盖 SQLITE_PATH（为空则使用 env）")
    p.add_argument("--data-root", default="", help="覆盖 DATA_ROOT（为空则使用 env）")
    p.add_argument("--providers", default="", help="覆盖 LLM_ENABLED_PROVIDERS（逗号分隔）")
    p.add_argument("--min-required", default="1", help="覆盖 LLM_MIN_EFFECTIVE_PROVIDERS（默认 1）")
    p.add_argument("--panel-max-sheets", default="2", help="覆盖 PANEL_MAX_SHEETS（默认 2）")
    p.add_argument("--fallback-max-symbols", default="10", help="覆盖 PANEL_FALLBACK_MAX_SYMBOLS（默认 10）")
    p.add_argument(
        "--fallback-max-points",
        default="1",
        help="覆盖 PANEL_FALLBACK_MAX_POINTS_PER_SYMBOL（默认 1）",
    )
    p.add_argument(
        "--provider-input-overrides",
        default="glm:summary_only,minimax:summary_only",
        help="覆盖 LLM_PROVIDER_INPUT_MODE_OVERRIDES",
    )
    p.add_argument("--timeout", default="300", help="覆盖 LLM_REQUEST_TIMEOUT_SECONDS（默认 300）")
    p.add_argument("--max-retries", default="0", help="覆盖 LLM_MAX_RETRIES（默认 0）")
    p.add_argument("--dry-run", default="false", help="覆盖 DRY_RUN（true/false）")
    return p


def main() -> int:
    """入口：加载 env -> 覆盖稳态参数 -> 执行 daily -> 校验落库。"""
    args = build_parser().parse_args()

    exports = load_env_exports(Path(args.env_file))
    overrides: dict[str, str] = {
        "DRY_RUN": str(args.dry_run).strip().lower(),
        "LLM_PROVIDER_INPUT_MODE_OVERRIDES": str(args.provider_input_overrides).strip(),
        "LLM_MIN_EFFECTIVE_PROVIDERS": str(args.min_required).strip(),
        "PANEL_MAX_SHEETS": str(args.panel_max_sheets).strip(),
        "PANEL_FALLBACK_MAX_SYMBOLS": str(args.fallback_max_symbols).strip(),
        "PANEL_FALLBACK_MAX_POINTS_PER_SYMBOL": str(args.fallback_max_points).strip(),
        "LLM_REQUEST_TIMEOUT_SECONDS": str(args.timeout).strip(),
        "LLM_MAX_RETRIES": str(args.max_retries).strip(),
    }
    if str(args.sqlite_path).strip():
        overrides["SQLITE_PATH"] = str(args.sqlite_path).strip()
    if str(args.data_root).strip():
        overrides["DATA_ROOT"] = str(args.data_root).strip()
    if str(args.providers).strip():
        overrides["LLM_ENABLED_PROVIDERS"] = str(args.providers).strip()

    apply_env(exports=exports, overrides=overrides)
    config = load_config()
    run_id = daily_run(config=config, snapshot_date=None, providers=None, dry_run=config.dry_run)

    result = verify_run(sqlite_path=Path(config.sqlite_path), run_id=run_id, data_root=Path(config.data_root))
    payload = {
        "run_id": result.run_id,
        "snapshot_date": result.snapshot_date,
        "status": result.status,
        "error_message": result.error_message,
        "counts": result.counts,
        "notification_status": result.notification_status,
        "telegram_ok": result.telegram_ok,
        "telegram_message_id": result.telegram_message_id,
        "reports_dir": result.reports_dir,
        "report_files_count": len(result.report_files),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

