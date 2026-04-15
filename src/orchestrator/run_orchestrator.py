"""日常/周期性运行的 Orchestrator（编排器）。

本模块负责把“扫描输入 -> 校验 -> 解析 -> LLM 分析 -> 聚合决策 -> 记账 -> 通知 -> 落库状态”
串成一条可审计的流水线。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from analysis.llm_invoke_engine import build_llm_invoke_engine
from analysis.providers.base import BaseLLMProvider
from app.config import AppConfig
from decision.voting_aggregator import VotingAggregator
from ingestion.file_scanner import scan_file_pair_candidates
from ingestion.input_contract_validator import ContractViolation, validate_file_pair_candidate
from ingestion.excel_parser import ExcelParseError, parse_validated_file_pair
from ledger.paper_ledger_service import PaperLedgerService
from notification.message_formatter import format_recommendation_message
from notification.telegram_adapter import TelegramAdapter
from storage.db import init_db, open_sqlite_connection
from storage.repositories.decision_repository import DecisionRepository
from storage.repositories.ledger_repository import LedgerRepository
from storage.repositories.llm_repository import LLMRepository
from storage.repositories.notification_repository import NotificationRepository
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
        if not bool(getattr(validation, "is_valid", False)):
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
    - provider 数不足（<3）-> runs: mark_run_end(DEGRADED)；不做决策/推送，只入库
    """
    effective_dry_run = config.dry_run if dry_run is None else bool(dry_run)
    provider_list = providers or []

    run_id = f"run-{uuid.uuid4().hex}"
    started_at = _now_iso()
    conn = open_sqlite_connection(config.sqlite_path)
    try:
        init_db(conn)

        run_repo = RunRepository(conn)
        snapshot_repo = SnapshotRepository(conn)
        llm_repo = LLMRepository(conn)
        decision_repo = DecisionRepository(conn)
        ledger_repo = LedgerRepository(conn)
        notification_repo = NotificationRepository(conn)

        run_repo.create_run(
            run_id=run_id,
            trigger_type="manual_daily",
            snapshot_date=snapshot_date,
            status="RUNNING",
            started_at=started_at,
        )

        resolved_snapshot_date, validated_pair = _pick_validated_file_pair(
            data_root=config.data_root, snapshot_date=snapshot_date
        )

        stock_rows, etf_rows = parse_validated_file_pair(validated_pair)

        batch_id = snapshot_repo.create_file_batch(
            run_id=run_id,
            snapshot_date=resolved_snapshot_date,
            stock_file=str(validated_pair.stock_path),
            etf_file=str(validated_pair.etf_path),
            stock_sha256=validated_pair.stock_sha256,
            etf_sha256=validated_pair.etf_sha256,
        )
        snapshot_repo.insert_instrument_rows(batch_id=batch_id, rows=[*stock_rows, *etf_rows])

        all_votes: list[dict[str, Any]] = []
        for provider in provider_list:
            engine = build_llm_invoke_engine(config=config, provider=provider, repository=llm_repo)

            stock_results = engine.analyze_and_persist(
                run_id=run_id,
                snapshot_date=resolved_snapshot_date,
                instrument_type="stock",
                instruments=stock_rows,
            )
            etf_results = engine.analyze_and_persist(
                run_id=run_id,
                snapshot_date=resolved_snapshot_date,
                instrument_type="etf",
                instruments=etf_rows,
            )
            all_votes.extend(
                _votes_from_engine_results(
                    snapshot_date=resolved_snapshot_date,
                    instrument_type="stock",
                    provider=provider,
                    engine_results=stock_results,
                )
            )
            all_votes.extend(
                _votes_from_engine_results(
                    snapshot_date=resolved_snapshot_date,
                    instrument_type="etf",
                    provider=provider,
                    engine_results=etf_results,
                )
            )

        # providers 不足时，允许完整入库（含 llm_outputs），但不做决策/推送。
        if len(provider_list) < 3:
            RunRepository(conn).mark_run_end(
                run_id=run_id,
                status="DEGRADED",
                ended_at=_now_iso(),
                error_message=f"providers_insufficient:{len(provider_list)}",
            )
            return run_id

        aggregator = VotingAggregator()
        decisions = aggregator.aggregate_for_run(run_id=run_id, votes=all_votes, top_n=10)
        decision_repo.insert_decisions(decisions)

        PaperLedgerService(ledger_repo).record_buys_for_decisions(
            run_id=run_id,
            snapshot_date=resolved_snapshot_date,
            decisions=decisions,
        )

        message_text = format_recommendation_message(run_id=run_id, decisions=decisions)
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
