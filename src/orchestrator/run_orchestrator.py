"""日常/周期性运行的 Orchestrator（编排器）。

本模块负责把“扫描输入 -> 校验 -> 解析 -> LLM 分析 -> 聚合决策 -> 记账 -> 通知 -> 落库状态”
串成一条可审计的流水线。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analysis.panel_output_validator import expand_top10_to_llm_outputs, validate_panel_output
from analysis.panel_parser import parse_panel_xlsx
from analysis.panel_prompt_builder import PanelPromptBuilder, load_panel_prompt
from analysis.provider_registry import ProviderRegistry
from analysis.providers.base import BaseLLMProvider
from app.config import AppConfig
from common.types import InstrumentRow
from decision.voting_aggregator import VotingAggregator
from ingestion.file_scanner import FilePairCandidate, scan_file_pair_candidates
from ingestion.input_contract_validator import ContractViolation, validate_file_pair_candidate
from ingestion.excel_parser import ExcelParseError
from ledger.paper_ledger_service import PaperLedgerService
from notification.message_formatter import format_recommendation_message, format_weekly_review_message
from notification.telegram_adapter import TelegramAdapter
from reporting.markdown_report_writer import write_provider_report, write_run_summary
from review.weekly_review_service import compute_weekly_report
from storage.db import init_db, open_sqlite_connection
from storage.repositories.decision_repository import DecisionRepository
from storage.repositories.ledger_repository import LedgerRepository
from storage.repositories.llm_repository import LLMRepository
from storage.repositories.llm_report_repository import LLMReportRepository
from storage.repositories.notification_repository import NotificationRepository
from storage.repositories.review_repository import ReviewRepository
from storage.repositories.run_repository import RunRepository
from storage.repositories.snapshot_repository import SnapshotRepository


def _now_iso() -> str:
    """生成用于落库的时间戳字符串（UTC，秒级）。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _pick_validated_file_pair(*, data_root: str, snapshot_date: str | None) -> tuple[str, Any]:
    """扫描并挑选一个可用的输入文件对。

    选择规则：
    - 若传入 snapshot_date：只尝试该日期候选
    - 若未传入 snapshot_date：按日期倒序尝试，取第一对通过契约校验者
    """
    candidates = scan_file_pair_candidates(data_root, snapshot_date=snapshot_date)
    if not candidates:
        raise FileNotFoundError("未找到任何符合命名模式的 xlsx 文件（期望: YYYY-MM-DD_stock.xlsx / YYYY-MM-DD_etf.xlsx）")

    ordered = candidates if snapshot_date is not None else list(reversed(candidates))
    last_error: Exception | None = None
    for cand in ordered:
        try:
            validated = validate_file_pair_candidate(cand)
            return validated.snapshot_date, validated
        except ContractViolation as exc:
            last_error = exc
            continue

    if last_error is not None:
        raise last_error
    raise ContractViolation("输入契约校验失败：未找到可用的 stock/etf 文件对")


def _votes_from_engine_results(
    *,
    snapshot_date: str,
    instrument_type: str,
    provider: BaseLLMProvider,
    engine_results: list[Any],
) -> list[dict[str, Any]]:
    """将 LLMInvokeEngine 的结果转换为 VotingAggregator 所需的最小 votes 列表。"""
    votes: list[dict[str, Any]] = []
    for res in engine_results:
        validation = getattr(res, "validation", None)
        symbol = getattr(res, "symbol", "")
        if validation is None:
            continue
        parsed = getattr(validation, "parsed", {}) or {}
        recommendation = parsed.get("recommendation")
        if not isinstance(recommendation, str) or not recommendation.strip():
            continue
        votes.append(
            {
                "snapshot_date": snapshot_date,
                "instrument_type": instrument_type,
                "symbol": str(symbol),
                "provider": provider.name,
                "recommendation": recommendation,
                "confidence": parsed.get("confidence"),
                "rationale": parsed.get("rationale") or [],
            }
        )
    return votes


def _resolve_providers(
    *,
    config: AppConfig,
    providers: list[BaseLLMProvider] | None,
) -> tuple[list[BaseLLMProvider], dict[str, str]]:
    """解析本次运行要使用的 provider 列表。

    约定：
    - 若显式传入 providers：视为“完全注入”，跳过 ProviderRegistry（常用于测试/本地 mock）。
    - 若未传入 providers：按 config.llm_enabled_providers 走 ProviderRegistry，得到
      (enabled_providers, disabled_reasons) 供降级通知审计使用。
    """
    if providers is not None:
        return list(providers), {}
    registry = ProviderRegistry.from_config(config)
    enabled, disabled = registry.enabled_providers()
    return enabled, disabled


def _format_degraded_message(
    *,
    run_id: str,
    min_required: int,
    effective_count: int,
    candidates: tuple[str, ...],
    disabled_reasons: dict[str, str],
    runtime_errors: dict[str, str] | None = None,
) -> str:
    """构造降级通知消息文本（不得包含敏感信息）。"""
    disabled_part = ", ".join(f"{name}:{reason}" for name, reason in sorted(disabled_reasons.items()))
    runtime_part = ", ".join(f"{name}:{reason}" for name, reason in sorted((runtime_errors or {}).items()))
    candidates_part = ", ".join(candidates) if candidates else "(empty)"
    return (
        f"Run {run_id} DEGRADED: providers_insufficient\n"
        f"- min_required={min_required}\n"
        f"- effective={effective_count}\n"
        f"- candidates=[{candidates_part}]\n"
        f"- disabled=[{disabled_part or '(none)'}]\n"
        f"- runtime_errors=[{runtime_part or '(none)'}]"
    )


def _apply_instrument_limit(*, rows: list[Any], limit: int) -> list[Any]:
    """对标的列表应用上限，limit=0 表示不限制。"""
    if limit <= 0:
        return rows
    return rows[:limit]


def _instrument_rows_from_panel(*, instrument_type: str, panel: dict[str, Any]) -> list[InstrumentRow]:
    rows: list[InstrumentRow] = []
    for item in panel.get("symbols") or []:
        symbol = str((item or {}).get("symbol") or "").strip()
        if not symbol:
            continue
        description = str((item or {}).get("description") or "").strip()
        for point in (item or {}).get("series") or []:
            snapshot_date = str((point or {}).get("date") or "").strip()
            if not snapshot_date:
                continue
            x_d = (point or {}).get("x_d_trend_state")
            x_w = (point or {}).get("x_w_trend_state")
            x_m = (point or {}).get("x_m_trend_state")
            rows.append(
                InstrumentRow(
                    snapshot_date=snapshot_date,
                    instrument_type=instrument_type,  # type: ignore[arg-type]
                    symbol=symbol,
                    description=description,
                    x_d_trend_state=str(x_d if x_d is not None else 0),
                    x_d_state_bars=int((point or {}).get("x_d_state_bars") or 0),
                    x_w_trend_state=str(x_w if x_w is not None else 0),
                    x_w_state_bars=int((point or {}).get("x_w_state_bars") or 0),
                    x_m_trend_state=str(x_m if x_m is not None else 0),
                    x_m_state_bars=int((point or {}).get("x_m_state_bars") or 0),
                    price=(point or {}).get("price"),
                    volume=(point or {}).get("volume"),
                )
            )
    return rows


def _invoke_provider_with_fallback(
    *,
    config: AppConfig,
    provider: BaseLLMProvider,
    prompt_builder: PanelPromptBuilder,
    snapshot_date: str,
    instrument_type: str,
    panel: dict[str, Any],
    stock_file_path: str,
    etf_file_path: str,
    runtime_errors: dict[str, str],
    panel_fallback_max_symbols: int,
    panel_fallback_max_points_per_symbol: int,
) -> str | None:
    """按“文件优先、失败回退文本摘要”策略调用 provider。"""
    overrides = dict(getattr(config, "llm_provider_input_mode_overrides", ()) or ())
    input_mode = str(overrides.get(provider.name, "file_first") or "file_first").strip().lower()
    file_prompt = prompt_builder.build_file_prompt(
        snapshot_date=snapshot_date,
        instrument_type=instrument_type,
        provider_name=provider.name,
    )
    prompt = prompt_builder.build(
        snapshot_date=snapshot_date,
        instrument_type=instrument_type,
        provider_name=provider.name,
        panel_json=panel,
    )
    summary_prompt = prompt_builder.build_summary(
        snapshot_date=snapshot_date,
        instrument_type=instrument_type,
        provider_name=provider.name,
        panel_json=panel,
        max_symbols=panel_fallback_max_symbols,
        max_points_per_symbol=panel_fallback_max_points_per_symbol,
    )
    if provider.supports_file_input:
        file_path = stock_file_path if instrument_type == "stock" else etf_file_path
        if input_mode == "summary_only":
            try:
                return provider.invoke(summary_prompt)
            except Exception as text_exc:  # noqa: BLE001 - 运行期错误按分类记录
                runtime_errors[f"{provider.name}.{instrument_type}.text"] = str(text_exc)
                return None
        try:
            return provider.invoke_with_files(prompt=file_prompt, file_paths=[file_path])
        except Exception as file_exc:  # noqa: BLE001 - 运行期错误按分类记录
            runtime_errors[f"{provider.name}.{instrument_type}.file"] = str(file_exc)
            if input_mode == "file_only":
                return None
            try:
                return provider.invoke(summary_prompt)
            except Exception as text_exc:  # noqa: BLE001 - 运行期错误按分类记录
                runtime_errors[f"{provider.name}.{instrument_type}.text"] = str(text_exc)
                return None
    try:
        return provider.invoke(prompt)
    except Exception as text_exc:  # noqa: BLE001 - 运行期错误按分类记录
        runtime_errors[f"{provider.name}.{instrument_type}.text"] = str(text_exc)
        return None


def daily_run(
    config: AppConfig,
    snapshot_date: str | None = None,
    providers: list[BaseLLMProvider] | None = None,
    dry_run: bool | None = None,
) -> str:
    """执行一次日常编排流程并返回 run_id。

    运行阶段（成功路径）：
    - runs: create_run(RUNNING)
    - scan -> validate -> parse
    - file_batches + instrument_snapshots 落库（证据 + 事实）
    - 对每个 provider 分别执行 LLMInvokeEngine（stock + etf）并收集 votes
    - VotingAggregator.aggregate_for_run -> DecisionRepository.insert_decisions
    - PaperLedgerService.record_buys_for_decisions
    - format 消息 -> TelegramAdapter 发送（dry_run 支持）
    - NotificationRepository.insert_notification
    - runs: mark_run_end(SUCCEEDED)

    失败分支：
    - 契约失败 / 无文件 / 异常 -> runs: mark_run_end(FAILED, error_message)
    - 尝试发送失败通知（dry_run 时也会记录）

    降级分支：
    - effective provider 数不足（< config.llm_min_effective_providers）-> runs: mark_run_end(DEGRADED)
      并写入降级通知（dry_run 也记录）；不做输入扫描/LLM/决策/记账
    """
    effective_dry_run = config.dry_run if dry_run is None else bool(dry_run)
    provider_list, disabled_reasons = _resolve_providers(config=config, providers=providers)

    run_id = f"run-{uuid.uuid4().hex}"
    started_at = _now_iso()
    conn = open_sqlite_connection(config.sqlite_path)
    try:
        init_db(conn)

        run_repo = RunRepository(conn)
        snapshot_repo = SnapshotRepository(conn)
        llm_repo = LLMRepository(conn)
        llm_report_repo = LLMReportRepository(conn)
        decision_repo = DecisionRepository(conn)
        ledger_repo = LedgerRepository(conn)
        notification_repo = NotificationRepository(conn)

        run_repo.mark_stale_running_runs_failed(now_iso=started_at, max_age_seconds=7200)

        run_repo.create_run(
            run_id=run_id,
            trigger_type="manual_daily",
            snapshot_date=snapshot_date,
            status="RUNNING",
            started_at=started_at,
        )

        # providers 不足时：仅落库 runs + notifications，然后直接结束。
        min_required = int(getattr(config, "llm_min_effective_providers", 0) or 0)
        if min_required > 0 and len(provider_list) < min_required:
            message_text = _format_degraded_message(
                run_id=run_id,
                min_required=min_required,
                effective_count=len(provider_list),
                candidates=tuple(getattr(config, "llm_enabled_providers", ()) or ()),
                disabled_reasons=disabled_reasons,
            )
            try:
                adapter = TelegramAdapter(
                    bot_token=config.telegram_bot_token,
                    chat_id=config.telegram_chat_id,
                    dry_run=effective_dry_run,
                )
                response = adapter.send_message(text=message_text)
                sent_ok = bool(response.get("ok"))
                notification_repo.insert_notification(
                    run_id=run_id,
                    channel="telegram",
                    message_text=message_text,
                    dry_run=effective_dry_run,
                    status="SENT" if sent_ok else "FAILED",
                    provider_response_json=json.dumps(response, ensure_ascii=False),
                )
            except Exception as notify_exc:
                # 降级通知失败也要尽可能落库，便于审计
                notification_repo.insert_notification(
                    run_id=run_id,
                    channel="telegram",
                    message_text=message_text,
                    dry_run=effective_dry_run,
                    status="FAILED",
                    provider_response_json=json.dumps({"error": str(notify_exc)}, ensure_ascii=False),
                )

            run_repo.mark_run_end(
                run_id=run_id,
                status="DEGRADED",
                ended_at=_now_iso(),
                error_message=f"providers_insufficient:{len(provider_list)}/{min_required}",
            )
            return run_id

        resolved_snapshot_date, validated_pair = _pick_validated_file_pair(
            data_root=config.data_root, snapshot_date=snapshot_date
        )
        run_repo.set_snapshot_date(run_id=run_id, snapshot_date=resolved_snapshot_date)

        panel_max_sheets = int(getattr(config, "panel_max_sheets", 60) or 60)
        stock_panel = parse_panel_xlsx(
            file_path=validated_pair.stock_path, instrument_type="stock", max_sheets=panel_max_sheets
        )
        etf_panel = parse_panel_xlsx(file_path=validated_pair.etf_path, instrument_type="etf", max_sheets=panel_max_sheets)

        batch_id = snapshot_repo.create_file_batch(
            run_id=run_id,
            snapshot_date=resolved_snapshot_date,
            stock_file=str(validated_pair.stock_path),
            etf_file=str(validated_pair.etf_path),
            stock_sha256=validated_pair.stock_sha256,
            etf_sha256=validated_pair.etf_sha256,
        )
        snapshot_repo.insert_instrument_rows(
            batch_id=batch_id,
            rows=[
                *_instrument_rows_from_panel(instrument_type="stock", panel=stock_panel),
                *_instrument_rows_from_panel(instrument_type="etf", panel=etf_panel),
            ],
        )

        all_votes: list[dict[str, Any]] = []
        runtime_errors: dict[str, str] = {}
        successful_providers: list[str] = []
        prompt_text = load_panel_prompt(prompt_path=str(getattr(config, "panel_prompt_path", "") or ""))
        prompt_builder = PanelPromptBuilder(prompt_text=prompt_text)
        panel_fallback_max_symbols = int(getattr(config, "panel_fallback_max_symbols", 120) or 120)
        panel_fallback_max_points_per_symbol = int(
            getattr(config, "panel_fallback_max_points_per_symbol", 6) or 6
        )
        for provider in provider_list:
            provider_ok = False
            for inst_type, panel in (("stock", stock_panel), ("etf", etf_panel)):
                raw = _invoke_provider_with_fallback(
                    config=config,
                    provider=provider,
                    prompt_builder=prompt_builder,
                    snapshot_date=resolved_snapshot_date,
                    instrument_type=inst_type,
                    panel=panel,
                    stock_file_path=str(validated_pair.stock_path),
                    etf_file_path=str(validated_pair.etf_path),
                    runtime_errors=runtime_errors,
                    panel_fallback_max_symbols=panel_fallback_max_symbols,
                    panel_fallback_max_points_per_symbol=panel_fallback_max_points_per_symbol,
                )
                if raw is None:
                    continue

                validation = validate_panel_output(
                    raw_text=raw,
                    expected_snapshot_date=resolved_snapshot_date,
                    expected_instrument_type=inst_type,
                    provider_name=provider.name,
                )
                llm_report_repo.insert_report(
                    run_id=run_id,
                    snapshot_date=resolved_snapshot_date,
                    instrument_type=inst_type,
                    provider=provider.name,
                    raw_text=raw,
                    json_text=validation.json_text,
                    is_valid=validation.is_valid,
                    quality_flags=validation.quality_flags,
                )
                try:
                    write_provider_report(
                        data_root=config.data_root,
                        snapshot_date=resolved_snapshot_date,
                        run_id=run_id,
                        instrument_type=inst_type,
                        provider=provider.name,
                        report_json=validation.parsed or {},
                    )
                except Exception as exc:
                    runtime_errors[f"{provider.name}.{inst_type}.report"] = str(exc)

                if not validation.is_valid:
                    continue
                provider_ok = True

                expanded = expand_top10_to_llm_outputs(
                    run_id=run_id,
                    snapshot_date=resolved_snapshot_date,
                    instrument_type=inst_type,
                    provider=provider.name,
                    top10_items=validation.top10_items,
                )
                for row in expanded:
                    llm_repo.insert_output(**row)

                for item in validation.top10_items:
                    all_votes.append(
                        {
                            "snapshot_date": resolved_snapshot_date,
                            "instrument_type": inst_type,
                            "symbol": str(item.get("symbol")),
                            "provider": provider.name,
                            "recommendation": "BUY",
                            "confidence": item.get("confidence"),
                            "rationale": [item.get("entry_logic_brief")] if item.get("entry_logic_brief") else [],
                        }
                    )

            if provider_ok:
                successful_providers.append(provider.name)

        if min_required > 0 and len(successful_providers) < min_required:
            message_text = _format_degraded_message(
                run_id=run_id,
                min_required=min_required,
                effective_count=len(successful_providers),
                candidates=tuple(getattr(config, "llm_enabled_providers", ()) or ()),
                disabled_reasons=disabled_reasons,
                runtime_errors=runtime_errors,
            )
            try:
                adapter = TelegramAdapter(
                    bot_token=config.telegram_bot_token,
                    chat_id=config.telegram_chat_id,
                    dry_run=effective_dry_run,
                )
                response = adapter.send_message(text=message_text)
                sent_ok = bool(response.get("ok"))
                notification_repo.insert_notification(
                    run_id=run_id,
                    channel="telegram",
                    message_text=message_text,
                    dry_run=effective_dry_run,
                    status="SENT" if sent_ok else "FAILED",
                    provider_response_json=json.dumps(response, ensure_ascii=False),
                )
            except Exception as notify_exc:
                notification_repo.insert_notification(
                    run_id=run_id,
                    channel="telegram",
                    message_text=message_text,
                    dry_run=effective_dry_run,
                    status="FAILED",
                    provider_response_json=json.dumps({"error": str(notify_exc)}, ensure_ascii=False),
                )

            run_repo.mark_run_end(
                run_id=run_id,
                status="DEGRADED",
                ended_at=_now_iso(),
                error_message=f"providers_insufficient_runtime:{len(successful_providers)}/{min_required}",
            )
            return run_id

        aggregator = VotingAggregator()
        decisions = aggregator.aggregate_for_run(
            run_id=run_id,
            votes=all_votes,
            top_n=10,
            min_effective_providers=int(getattr(config, "llm_min_effective_providers", 0) or 0),
        )
        decision_repo.insert_decisions(decisions)

        PaperLedgerService(ledger_repo).record_buys_for_decisions(
            run_id=run_id,
            snapshot_date=resolved_snapshot_date,
            decisions=decisions,
        )

        local_reports_dir = str(Path(config.data_root) / "reports" / resolved_snapshot_date / run_id)
        base_url = str(getattr(config, "reports_base_url", "") or "").strip().rstrip("/")
        reports_dir = local_reports_dir
        summary_md_path = None
        try:
            report_rows = [dict(r) for r in llm_report_repo.list_reports(run_id=run_id)]
            summary_path = write_run_summary(
                data_root=config.data_root,
                snapshot_date=resolved_snapshot_date,
                run_id=run_id,
                decisions=decisions,
                llm_reports=report_rows,
            )
            local_summary_path = str(summary_path)
            summary_md_path = local_summary_path
            if base_url:
                summary_md_path = f"{base_url}/reports/{resolved_snapshot_date}/{run_id}/summary.md (local: {local_summary_path})"
        except Exception as exc:
            runtime_errors[f"summary.report"] = str(exc)

        if base_url:
            reports_dir = f"{base_url}/reports/{resolved_snapshot_date}/{run_id}/ (local: {local_reports_dir})"

        message_text = format_recommendation_message(
            run_id=run_id,
            decisions=decisions,
            reports_dir=reports_dir,
            summary_md_path=summary_md_path,
        )
        adapter = TelegramAdapter(
            bot_token=config.telegram_bot_token,
            chat_id=config.telegram_chat_id,
            dry_run=effective_dry_run,
        )
        try:
            response = adapter.send_message(text=message_text)
            sent_ok = bool(response.get("ok"))
            notification_repo.insert_notification(
                run_id=run_id,
                channel="telegram",
                message_text=message_text,
                dry_run=effective_dry_run,
                status="SENT" if sent_ok else "FAILED",
                provider_response_json=json.dumps(response, ensure_ascii=False),
            )
        except Exception as notify_exc:
            notification_repo.insert_notification(
                run_id=run_id,
                channel="telegram",
                message_text=message_text,
                dry_run=effective_dry_run,
                status="FAILED",
                provider_response_json=json.dumps({"error": str(notify_exc)}, ensure_ascii=False),
            )

        run_repo.mark_run_end(run_id=run_id, status="SUCCEEDED", ended_at=_now_iso(), error_message=None)
        return run_id
    except (ContractViolation, FileNotFoundError, ExcelParseError, Exception) as exc:
        error_message = str(exc)
        ended_at = _now_iso()
        try:
            # 尽最大努力标记 FAILED
            RunRepository(conn).mark_run_end(
                run_id=run_id,
                status="FAILED",
                ended_at=ended_at,
                error_message=error_message,
            )
        except Exception:
            # 若 runs 行尚未创建或 DB 异常，忽略（外层仍会抛出）
            pass

        # 尝试发送失败通知（dry_run 时也记录）
        failure_text = f"Run {run_id} FAILED: {error_message}"
        try:
            adapter = TelegramAdapter(
                bot_token=config.telegram_bot_token,
                chat_id=config.telegram_chat_id,
                dry_run=effective_dry_run,
            )
            response = adapter.send_message(text=failure_text)
            NotificationRepository(conn).insert_notification(
                run_id=run_id,
                channel="telegram",
                message_text=failure_text,
                dry_run=effective_dry_run,
                status="SENT" if bool(response.get("ok")) else "FAILED",
                provider_response_json=json.dumps(response, ensure_ascii=False),
            )
        except Exception as notify_exc:
            try:
                NotificationRepository(conn).insert_notification(
                    run_id=run_id,
                    channel="telegram",
                    message_text=failure_text,
                    dry_run=effective_dry_run,
                    status="FAILED",
                    provider_response_json=json.dumps({"error": str(notify_exc)}, ensure_ascii=False),
                )
            except Exception:
                pass
        return run_id
    finally:
        conn.close()


def daily_run_for_existing_run(
    *,
    config: AppConfig,
    run_id: str,
    snapshot_date: str,
    stock_file_path: str,
    etf_file_path: str,
    providers: list[BaseLLMProvider] | None = None,
    dry_run: bool | None = None,
) -> str:
    """复用既有 run_id 执行日常编排流程，并使用指定文件路径作为输入。"""
    effective_dry_run = config.dry_run if dry_run is None else bool(dry_run)
    provider_list, disabled_reasons = _resolve_providers(config=config, providers=providers)

    started_at = _now_iso()
    conn = open_sqlite_connection(config.sqlite_path)
    try:
        init_db(conn)

        run_repo = RunRepository(conn)
        snapshot_repo = SnapshotRepository(conn)
        llm_repo = LLMRepository(conn)
        llm_report_repo = LLMReportRepository(conn)
        decision_repo = DecisionRepository(conn)
        ledger_repo = LedgerRepository(conn)
        notification_repo = NotificationRepository(conn)

        existing = run_repo.get_run(run_id)
        if existing is None:
            raise ValueError(f"run_not_found:{run_id}")
        if str(existing["status"]) != "RUNNING":
            raise ValueError(f"run_not_running:{run_id}:{existing['status']}")

        run_repo.set_snapshot_date(run_id=run_id, snapshot_date=snapshot_date)

        min_required = int(getattr(config, "llm_min_effective_providers", 0) or 0)
        if min_required > 0 and len(provider_list) < min_required:
            message_text = _format_degraded_message(
                run_id=run_id,
                min_required=min_required,
                effective_count=len(provider_list),
                candidates=tuple(getattr(config, "llm_enabled_providers", ()) or ()),
                disabled_reasons=disabled_reasons,
            )
            try:
                adapter = TelegramAdapter(
                    bot_token=config.telegram_bot_token,
                    chat_id=config.telegram_chat_id,
                    dry_run=effective_dry_run,
                )
                response = adapter.send_message(text=message_text)
                sent_ok = bool(response.get("ok"))
                notification_repo.insert_notification(
                    run_id=run_id,
                    channel="telegram",
                    message_text=message_text,
                    dry_run=effective_dry_run,
                    status="SENT" if sent_ok else "FAILED",
                    provider_response_json=json.dumps(response, ensure_ascii=False),
                )
            except Exception as notify_exc:
                notification_repo.insert_notification(
                    run_id=run_id,
                    channel="telegram",
                    message_text=message_text,
                    dry_run=effective_dry_run,
                    status="FAILED",
                    provider_response_json=json.dumps({"error": str(notify_exc)}, ensure_ascii=False),
                )

            run_repo.mark_run_end(
                run_id=run_id,
                status="DEGRADED",
                ended_at=_now_iso(),
                error_message=f"providers_insufficient:{len(provider_list)}/{min_required}",
            )
            return run_id

        candidate = FilePairCandidate(
            snapshot_date=snapshot_date,
            stock_path=Path(stock_file_path),
            etf_path=Path(etf_file_path),
            actual_files=(Path(stock_file_path).name, Path(etf_file_path).name),
        )
        validated_pair = validate_file_pair_candidate(candidate)

        panel_max_sheets = int(getattr(config, "panel_max_sheets", 60) or 60)
        stock_panel = parse_panel_xlsx(
            file_path=validated_pair.stock_path, instrument_type="stock", max_sheets=panel_max_sheets
        )
        etf_panel = parse_panel_xlsx(
            file_path=validated_pair.etf_path, instrument_type="etf", max_sheets=panel_max_sheets
        )

        batch_row = conn.execute(
            """
            SELECT batch_id
            FROM file_batches
            WHERE run_id = ?
            ORDER BY batch_id DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if batch_row is not None:
            batch_id = int(batch_row["batch_id"])
        else:
            batch_id = snapshot_repo.create_file_batch(
                run_id=run_id,
                snapshot_date=snapshot_date,
                stock_file=str(validated_pair.stock_path),
                etf_file=str(validated_pair.etf_path),
                stock_sha256=validated_pair.stock_sha256,
                etf_sha256=validated_pair.etf_sha256,
            )
        snapshot_repo.insert_instrument_rows(
            batch_id=batch_id,
            rows=[
                *_instrument_rows_from_panel(instrument_type="stock", panel=stock_panel),
                *_instrument_rows_from_panel(instrument_type="etf", panel=etf_panel),
            ],
        )

        all_votes: list[dict[str, Any]] = []
        runtime_errors: dict[str, str] = {}
        successful_providers: list[str] = []
        prompt_text = load_panel_prompt(prompt_path=str(getattr(config, "panel_prompt_path", "") or ""))
        prompt_builder = PanelPromptBuilder(prompt_text=prompt_text)
        panel_fallback_max_symbols = int(getattr(config, "panel_fallback_max_symbols", 120) or 120)
        panel_fallback_max_points_per_symbol = int(
            getattr(config, "panel_fallback_max_points_per_symbol", 6) or 6
        )

        for provider in provider_list:
            provider_ok = False
            for inst_type, panel in (("stock", stock_panel), ("etf", etf_panel)):
                raw = _invoke_provider_with_fallback(
                    config=config,
                    provider=provider,
                    prompt_builder=prompt_builder,
                    snapshot_date=snapshot_date,
                    instrument_type=inst_type,
                    panel=panel,
                    stock_file_path=str(validated_pair.stock_path),
                    etf_file_path=str(validated_pair.etf_path),
                    runtime_errors=runtime_errors,
                    panel_fallback_max_symbols=panel_fallback_max_symbols,
                    panel_fallback_max_points_per_symbol=panel_fallback_max_points_per_symbol,
                )
                if raw is None:
                    continue

                validation = validate_panel_output(
                    raw_text=raw,
                    expected_snapshot_date=snapshot_date,
                    expected_instrument_type=inst_type,
                    provider_name=provider.name,
                )
                llm_report_repo.insert_report(
                    run_id=run_id,
                    snapshot_date=snapshot_date,
                    instrument_type=inst_type,
                    provider=provider.name,
                    raw_text=raw,
                    json_text=validation.json_text,
                    is_valid=validation.is_valid,
                    quality_flags=validation.quality_flags,
                )
                try:
                    write_provider_report(
                        data_root=config.data_root,
                        snapshot_date=snapshot_date,
                        run_id=run_id,
                        instrument_type=inst_type,
                        provider=provider.name,
                        report_json=validation.parsed or {},
                    )
                except Exception as exc:
                    runtime_errors[f"{provider.name}.{inst_type}.report"] = str(exc)

                if not validation.is_valid:
                    continue
                provider_ok = True

                expanded = expand_top10_to_llm_outputs(
                    run_id=run_id,
                    snapshot_date=snapshot_date,
                    instrument_type=inst_type,
                    provider=provider.name,
                    top10_items=validation.top10_items,
                )
                for row in expanded:
                    llm_repo.insert_output(**row)

                for item in validation.top10_items:
                    all_votes.append(
                        {
                            "snapshot_date": snapshot_date,
                            "instrument_type": inst_type,
                            "symbol": str(item.get("symbol")),
                            "provider": provider.name,
                            "recommendation": "BUY",
                            "confidence": item.get("confidence"),
                            "rationale": [item.get("entry_logic_brief")]
                            if item.get("entry_logic_brief")
                            else [],
                        }
                    )

            if provider_ok:
                successful_providers.append(provider.name)

        if min_required > 0 and len(successful_providers) < min_required:
            message_text = _format_degraded_message(
                run_id=run_id,
                min_required=min_required,
                effective_count=len(successful_providers),
                candidates=tuple(getattr(config, "llm_enabled_providers", ()) or ()),
                disabled_reasons=disabled_reasons,
                runtime_errors=runtime_errors,
            )
            try:
                adapter = TelegramAdapter(
                    bot_token=config.telegram_bot_token,
                    chat_id=config.telegram_chat_id,
                    dry_run=effective_dry_run,
                )
                response = adapter.send_message(text=message_text)
                sent_ok = bool(response.get("ok"))
                notification_repo.insert_notification(
                    run_id=run_id,
                    channel="telegram",
                    message_text=message_text,
                    dry_run=effective_dry_run,
                    status="SENT" if sent_ok else "FAILED",
                    provider_response_json=json.dumps(response, ensure_ascii=False),
                )
            except Exception as notify_exc:
                notification_repo.insert_notification(
                    run_id=run_id,
                    channel="telegram",
                    message_text=message_text,
                    dry_run=effective_dry_run,
                    status="FAILED",
                    provider_response_json=json.dumps({"error": str(notify_exc)}, ensure_ascii=False),
                )

            run_repo.mark_run_end(
                run_id=run_id,
                status="DEGRADED",
                ended_at=_now_iso(),
                error_message=f"providers_insufficient_runtime:{len(successful_providers)}/{min_required}",
            )
            return run_id

        aggregator = VotingAggregator()
        decisions = aggregator.aggregate_for_run(
            run_id=run_id,
            votes=all_votes,
            top_n=10,
            min_effective_providers=int(getattr(config, "llm_min_effective_providers", 0) or 0),
        )
        decision_repo.insert_decisions(decisions)

        PaperLedgerService(ledger_repo).record_buys_for_decisions(
            run_id=run_id,
            snapshot_date=snapshot_date,
            decisions=decisions,
        )

        local_reports_dir = str(Path(config.data_root) / "reports" / snapshot_date / run_id)
        base_url = str(getattr(config, "reports_base_url", "") or "").strip().rstrip("/")
        reports_dir = local_reports_dir
        summary_md_path = None
        try:
            report_rows = [dict(r) for r in llm_report_repo.list_reports(run_id=run_id)]
            summary_path = write_run_summary(
                data_root=config.data_root,
                snapshot_date=snapshot_date,
                run_id=run_id,
                decisions=decisions,
                llm_reports=report_rows,
            )
            local_summary_path = str(summary_path)
            summary_md_path = local_summary_path
            if base_url:
                summary_md_path = (
                    f"{base_url}/reports/{snapshot_date}/{run_id}/summary.md (local: {local_summary_path})"
                )
        except Exception as exc:
            runtime_errors["summary.report"] = str(exc)

        if base_url:
            reports_dir = f"{base_url}/reports/{snapshot_date}/{run_id}/ (local: {local_reports_dir})"

        message_text = format_recommendation_message(
            run_id=run_id,
            decisions=decisions,
            reports_dir=reports_dir,
            summary_md_path=summary_md_path,
        )
        adapter = TelegramAdapter(
            bot_token=config.telegram_bot_token,
            chat_id=config.telegram_chat_id,
            dry_run=effective_dry_run,
        )
        try:
            response = adapter.send_message(text=message_text)
            sent_ok = bool(response.get("ok"))
            notification_repo.insert_notification(
                run_id=run_id,
                channel="telegram",
                message_text=message_text,
                dry_run=effective_dry_run,
                status="SENT" if sent_ok else "FAILED",
                provider_response_json=json.dumps(response, ensure_ascii=False),
            )
        except Exception as notify_exc:
            notification_repo.insert_notification(
                run_id=run_id,
                channel="telegram",
                message_text=message_text,
                dry_run=effective_dry_run,
                status="FAILED",
                provider_response_json=json.dumps({"error": str(notify_exc)}, ensure_ascii=False),
            )

        run_repo.mark_run_end(run_id=run_id, status="SUCCEEDED", ended_at=_now_iso(), error_message=None)
        return run_id
    except (ContractViolation, FileNotFoundError, ExcelParseError, Exception) as exc:
        error_message = str(exc)
        ended_at = _now_iso()
        try:
            RunRepository(conn).mark_run_end(
                run_id=run_id,
                status="FAILED",
                ended_at=ended_at,
                error_message=error_message,
            )
        except Exception:
            pass

        failure_text = f"Run {run_id} FAILED: {error_message}"
        try:
            adapter = TelegramAdapter(
                bot_token=config.telegram_bot_token,
                chat_id=config.telegram_chat_id,
                dry_run=effective_dry_run,
            )
            response = adapter.send_message(text=failure_text)
            NotificationRepository(conn).insert_notification(
                run_id=run_id,
                channel="telegram",
                message_text=failure_text,
                dry_run=effective_dry_run,
                status="SENT" if bool(response.get("ok")) else "FAILED",
                provider_response_json=json.dumps(response, ensure_ascii=False),
            )
        except Exception as notify_exc:
            try:
                NotificationRepository(conn).insert_notification(
                    run_id=run_id,
                    channel="telegram",
                    message_text=failure_text,
                    dry_run=effective_dry_run,
                    status="FAILED",
                    provider_response_json=json.dumps({"error": str(notify_exc)}, ensure_ascii=False),
                )
            except Exception:
                pass
        return run_id
    finally:
        conn.close()


def weekly_run(
    config: AppConfig,
    week_end: str | None = None,
    dry_run: bool | None = None,
) -> str:
    """执行一次周度复盘流程并返回 run_id。

    运行阶段（成功路径）：
    - runs: create_run(RUNNING, trigger_type=scheduled_weekly)
    - compute_weekly_report（基于 paper_trades 的基础统计）
    - weekly_reviews 落库（report_json）
    - format 消息 -> TelegramAdapter 发送（dry_run 支持）并记录 notifications
    - runs: mark_run_end(SUCCEEDED)

    失败分支：
    - 异常 -> runs: mark_run_end(FAILED, error_message)
    - 尝试发送失败通知（dry_run 时也记录）
    """
    effective_dry_run = config.dry_run if dry_run is None else bool(dry_run)
    resolved_week_end = (
        week_end if week_end is not None else datetime.now(timezone.utc).date().isoformat()
    )

    run_id = f"run-{uuid.uuid4().hex}"
    started_at = _now_iso()
    conn = open_sqlite_connection(config.sqlite_path)
    try:
        init_db(conn)

        run_repo = RunRepository(conn)
        notification_repo = NotificationRepository(conn)
        review_repo = ReviewRepository(conn)

        run_repo.mark_stale_running_runs_failed(now_iso=started_at, max_age_seconds=7200)

        run_repo.create_run(
            run_id=run_id,
            trigger_type="scheduled_weekly",
            snapshot_date=resolved_week_end,
            status="RUNNING",
            started_at=started_at,
        )

        report = compute_weekly_report(conn, week_end=resolved_week_end, days=7)
        review_repo.insert_weekly_review(
            review_id=f"review-{uuid.uuid4().hex}",
            week_start=str(report.get("week_start", "")),
            week_end=str(report.get("week_end", "")),
            report_json=json.dumps(report, ensure_ascii=False),
        )

        message_text = format_weekly_review_message(run_id=run_id, report=report)
        adapter = TelegramAdapter(
            bot_token=config.telegram_bot_token,
            chat_id=config.telegram_chat_id,
            dry_run=effective_dry_run,
        )
        response = adapter.send_message(text=message_text)
        sent_ok = bool(response.get("ok"))
        notification_repo.insert_notification(
            run_id=run_id,
            channel="telegram",
            message_text=message_text,
            dry_run=effective_dry_run,
            status="SENT" if sent_ok else "FAILED",
            provider_response_json=json.dumps(response, ensure_ascii=False),
        )

        run_repo.mark_run_end(run_id=run_id, status="SUCCEEDED", ended_at=_now_iso(), error_message=None)
        return run_id
    except Exception as exc:
        error_message = str(exc)
        ended_at = _now_iso()
        try:
            RunRepository(conn).mark_run_end(
                run_id=run_id,
                status="FAILED",
                ended_at=ended_at,
                error_message=error_message,
            )
        except Exception:
            pass

        failure_text = f"Weekly Run {run_id} FAILED: {error_message}"
        try:
            adapter = TelegramAdapter(
                bot_token=config.telegram_bot_token,
                chat_id=config.telegram_chat_id,
                dry_run=effective_dry_run,
            )
            response = adapter.send_message(text=failure_text)
            NotificationRepository(conn).insert_notification(
                run_id=run_id,
                channel="telegram",
                message_text=failure_text,
                dry_run=effective_dry_run,
                status="SENT" if bool(response.get("ok")) else "FAILED",
                provider_response_json=json.dumps(response, ensure_ascii=False),
            )
        except Exception as notify_exc:
            try:
                NotificationRepository(conn).insert_notification(
                    run_id=run_id,
                    channel="telegram",
                    message_text=failure_text,
                    dry_run=effective_dry_run,
                    status="FAILED",
                    provider_response_json=json.dumps({"error": str(notify_exc)}, ensure_ascii=False),
                )
            except Exception:
                pass
        return run_id
    finally:
        conn.close()
