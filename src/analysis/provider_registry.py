"""Provider Registry：从 AppConfig 生成“有效 provider 列表”。

本项目采取“配置驱动 + 最小可用定义”的策略：
- provider 仅当具备必需字段（api_key / model / base_url 等）时才视为可用；
- 不可用原因以短码返回（用于审计/通知），不得包含敏感信息（如 API Key）。

注意：当前阶段 Provider 仅需要满足 BaseLLMProvider 接口即可。
"""

from __future__ import annotations

from dataclasses import dataclass

from analysis.providers.base import BaseLLMProvider
from analysis.providers.anthropic_provider import AnthropicProvider
from analysis.providers.gemini_provider import GeminiProvider
from analysis.providers.openai_compatible_provider import OpenAICompatibleProvider
from app.config import AppConfig


_OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_ANTHROPIC_MAX_TOKENS = 1024


@dataclass(frozen=True)
class ProviderRegistry:
    """根据 AppConfig 构建有效 provider 列表的注册表。"""

    _providers: list[BaseLLMProvider]
    _disabled_reasons: dict[str, str]

    @classmethod
    def from_config(cls, config: AppConfig) -> ProviderRegistry:
        """从 AppConfig 构建 ProviderRegistry。"""
        providers: list[BaseLLMProvider] = []
        disabled: dict[str, str] = {}

        for name in config.llm_enabled_providers:
            provider, reason = _build_provider_from_config(config=config, provider_name=name)
            if provider is None:
                if reason:
                    disabled[name] = reason
                continue
            providers.append(provider)

        return cls(_providers=providers, _disabled_reasons=disabled)

    def enabled_providers(self) -> tuple[list[BaseLLMProvider], dict[str, str]]:
        """返回 (enabled_providers, disabled_reasons)。

        disabled_reasons 仅包含短码，不包含任何敏感信息。
        """

        # 保护内部结构不被外部意外修改
        return list(self._providers), dict(self._disabled_reasons)


def _build_provider_from_config(
    *,
    config: AppConfig,
    provider_name: str,
) -> tuple[BaseLLMProvider | None, str | None]:
    """按 provider_name 从配置构造 provider；失败时返回 (None, reason_short_code)。"""
    name = provider_name.strip().lower()

    if name == "openai":
        return _build_openai_like_provider(
            config=config,
            provider_name="openai",
            api_key=config.openai_api_key,
            model=config.openai_model,
            base_url=config.openai_base_url,
            base_url_required=False,
        )
    if name == "qwen":
        return _build_openai_like_provider(
            config=config,
            provider_name="qwen",
            api_key=config.qwen_api_key,
            model=config.qwen_model,
            base_url=config.qwen_base_url,
            base_url_required=True,
        )
    if name == "glm":
        return _build_openai_like_provider(
            config=config,
            provider_name="glm",
            api_key=config.glm_api_key,
            model=config.glm_model,
            base_url=config.glm_base_url,
            base_url_required=True,
        )
    if name == "kimi":
        return _build_openai_like_provider(
            config=config,
            provider_name="kimi",
            api_key=config.kimi_api_key,
            model=config.kimi_model,
            base_url=config.kimi_base_url,
            base_url_required=True,
        )
    if name == "anthropic":
        return _build_simple_provider(
            config=config,
            provider_name="anthropic",
            api_key=config.anthropic_api_key,
            model=config.anthropic_model,
        )
    if name == "gemini":
        return _build_simple_provider(
            config=config,
            provider_name="gemini",
            api_key=config.gemini_api_key,
            model=config.gemini_model,
        )

    # AppConfig 已对 enabled_providers 做了白名单校验；这里保持防御式分支。
    return None, "unknown_provider"


def _build_simple_provider(
    *,
    config: AppConfig,
    provider_name: str,
    api_key: str,
    model: str,
) -> tuple[BaseLLMProvider | None, str | None]:
    """构造不需要 base_url 的 provider（Anthropic/Gemini）。"""
    if not api_key.strip():
        return None, "missing_api_key"
    if not model.strip():
        return None, "missing_model"
    if provider_name == "anthropic":
        return (
            AnthropicProvider(
                model=model,
                api_key=api_key,
                max_tokens=_DEFAULT_ANTHROPIC_MAX_TOKENS,
                timeout_seconds=float(config.llm_request_timeout_seconds),
                provider_name="anthropic",
            ),
            None,
        )
    if provider_name == "gemini":
        return (
            GeminiProvider(
                model=model,
                api_key=api_key,
                timeout_seconds=float(config.llm_request_timeout_seconds),
                provider_name="gemini",
            ),
            None,
        )
    return None, "unknown_provider"


def _build_openai_like_provider(
    *,
    config: AppConfig,
    provider_name: str,
    api_key: str,
    model: str,
    base_url: str,
    base_url_required: bool,
) -> tuple[BaseLLMProvider | None, str | None]:
    """构造 OpenAI-compatible provider（OpenAI/Qwen/GLM/Kimi）。"""
    if not api_key.strip():
        return None, "missing_api_key"
    if not model.strip():
        return None, "missing_model"

    normalized_base_url = base_url.strip()
    if base_url_required and not normalized_base_url:
        return None, "missing_base_url"
    if not normalized_base_url:
        normalized_base_url = _OPENAI_DEFAULT_BASE_URL

    return (
        OpenAICompatibleProvider(
            base_url=normalized_base_url,
            model=model,
            api_key=api_key,
            timeout_seconds=float(config.llm_request_timeout_seconds),
            max_retries=int(config.llm_max_retries),
            extra_headers=None,
            provider_name=provider_name,
        ),
        None,
    )
