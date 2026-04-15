"""LLM 调用编排引擎。

职责：
- 为每个标的构建 prompt
- 并发调用 provider 获取 raw_text
- 使用 validator 做容错解析与质量标记
- 将结果落库到 llm_outputs
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from analysis.llm_output_validator import LLMValidationResult, LLMOutputValidator
from analysis.prompt_builder import PromptBuilder
from analysis.providers.base import BaseLLMProvider
from app.config import AppConfig
from common.types import InstrumentRow
from storage.repositories.llm_repository import LLMRepository


@dataclass(frozen=True)
class LLMEngineResult:
    """引擎对单个标的的执行结果。"""

    symbol: str
    validation: LLMValidationResult
    output_id: int


class LLMInvokeEngine:
    """面向批量标的的 LLM 调用与落库引擎。"""

    def __init__(
        self,
        *,
        provider: BaseLLMProvider,
        prompt_builder: PromptBuilder,
        validator: LLMOutputValidator,
        repository: LLMRepository,
        parallelism: int,
    ) -> None:
        """初始化引擎并注入依赖。"""
        self._provider = provider
        self._prompt_builder = prompt_builder
        self._validator = validator
        self._repository = repository
        self._parallelism = max(1, int(parallelism))

    def analyze_and_persist(
        self,
        *,
        run_id: str,
        snapshot_date: str,
        instrument_type: str,
        instruments: list[InstrumentRow],
    ) -> list[LLMEngineResult]:
        """并发分析 instruments 并将输出写入 llm_outputs。"""
        if not instruments:
            return []

        # SQLite connection 默认 check_same_thread=True，避免在 worker 线程内写 DB。
        results: list[tuple[InstrumentRow, LLMValidationResult]] = []
        with ThreadPoolExecutor(max_workers=self._parallelism) as executor:
            futures = {
                executor.submit(self._invoke_and_validate, instrument): instrument for instrument in instruments
            }
            for future in as_completed(futures):
                instrument = futures[future]
                validation = future.result()
                results.append((instrument, validation))

        # 按输入顺序稳定落库（便于调试与测试）。
        results_by_symbol = {instrument.symbol: validation for instrument, validation in results}
        engine_results: list[LLMEngineResult] = []
        for instrument in instruments:
            validation = results_by_symbol[instrument.symbol]
            output_id = self._repository.insert_output(
                run_id=run_id,
                snapshot_date=snapshot_date,
                instrument_type=instrument_type,
                symbol=instrument.symbol,
                provider=self._provider.name,
                raw_text=validation.raw_text,
                json_text=validation.json_text,
                is_valid=validation.is_valid,
                quality_flags=validation.quality_flags,
            )
            engine_results.append(
                LLMEngineResult(symbol=instrument.symbol, validation=validation, output_id=output_id)
            )
        return engine_results

    def _invoke_and_validate(self, instrument: InstrumentRow) -> LLMValidationResult:
        """对单个标的进行 provider 调用并执行校验。"""
        prompt = self._prompt_builder.build(instrument)
        raw_text = self._provider.invoke(prompt)
        return self._validator.validate(raw_text)


def build_llm_invoke_engine(
    *,
    config: AppConfig,
    provider: BaseLLMProvider,
    repository: LLMRepository,
) -> LLMInvokeEngine:
    """根据应用配置构建 LLMInvokeEngine。"""
    return LLMInvokeEngine(
        provider=provider,
        prompt_builder=PromptBuilder(),
        validator=LLMOutputValidator(),
        repository=repository,
        parallelism=config.analysis_parallelism,
    )
