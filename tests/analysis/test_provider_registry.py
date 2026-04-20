"""ProviderRegistry 配置驱动启用/禁用规则测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from app.config import AppConfig  # noqa: E402
from analysis.providers.mock_provider import MockLLMProvider  # noqa: E402
from analysis.provider_registry import ProviderRegistry  # noqa: E402


def _base_config(**overrides: object) -> AppConfig:
    """构造一份最小 AppConfig，并允许通过 overrides 覆盖字段。"""
    base = AppConfig(
        telegram_bot_token="x",
        telegram_chat_id="x",
        data_root="stock_data",
        sqlite_path=":memory:",
        analysis_parallelism=1,
        dry_run=True,
        app_env="test",
    )
    return AppConfig(**{**base.__dict__, **overrides})


def test_registry_disables_provider_when_missing_api_key() -> None:
    """当缺失 api_key 时，应禁用该 provider 并给出短码原因。"""
    config = _base_config(llm_enabled_providers=("openai",), openai_api_key="", openai_model="gpt-4.1-mini")

    registry = ProviderRegistry.from_config(config)
    providers, disabled = registry.enabled_providers()

    assert providers == []
    assert disabled["openai"] == "missing_api_key"


def test_registry_disables_provider_when_missing_model() -> None:
    """当缺失 model 时，应禁用该 provider 并给出短码原因。"""
    config = _base_config(llm_enabled_providers=("openai",), openai_api_key="sk-x", openai_model="")

    registry = ProviderRegistry.from_config(config)
    providers, disabled = registry.enabled_providers()

    assert providers == []
    assert disabled["openai"] == "missing_model"


def test_registry_openai_base_url_can_be_empty_and_defaults_to_official() -> None:
    """OpenAI base_url 允许为空，空时应默认使用官方 https://api.openai.com/v1。"""
    config = _base_config(
        llm_enabled_providers=("openai",),
        openai_api_key="sk-x",
        openai_model="gpt-4.1-mini",
        openai_base_url="",
    )

    registry = ProviderRegistry.from_config(config)
    providers, disabled = registry.enabled_providers()

    assert disabled == {}
    assert [p.name for p in providers] == ["openai"]
    assert getattr(providers[0], "base_url") == "https://api.openai.com/v1"


@pytest.mark.parametrize("provider_name", ["qwen", "glm", "kimi", "minimax"])
def test_registry_openai_compatible_requires_base_url_for_non_openai(provider_name: str) -> None:
    """Qwen/GLM/Kimi/MiniMax 的 base_url 必填，缺失时应禁用并给出短码原因。"""
    overrides: dict[str, object] = {"llm_enabled_providers": (provider_name,)}
    overrides[f"{provider_name}_api_key"] = "sk-x"
    overrides[f"{provider_name}_model"] = "some-model"
    overrides[f"{provider_name}_base_url"] = ""
    config = _base_config(**overrides)

    registry = ProviderRegistry.from_config(config)
    providers, disabled = registry.enabled_providers()

    assert providers == []
    assert disabled[provider_name] == "missing_base_url"


@pytest.mark.parametrize("provider_name", ["anthropic", "gemini"])
def test_registry_anthropic_and_gemini_do_not_require_base_url(provider_name: str) -> None:
    """Anthropic/Gemini 不使用 base_url，缺失 base_url 不应导致禁用。"""
    overrides: dict[str, object] = {"llm_enabled_providers": (provider_name,)}
    overrides[f"{provider_name}_api_key"] = "sk-x"
    overrides[f"{provider_name}_model"] = "some-model"
    overrides[f"{provider_name}_base_url"] = ""  # 允许存在但不要求
    config = _base_config(**overrides)

    registry = ProviderRegistry.from_config(config)
    providers, disabled = registry.enabled_providers()

    assert disabled == {}
    assert [p.name for p in providers] == [provider_name]


def test_provider_capability_default_flags() -> None:
    """默认 provider 不支持文件输入能力。"""
    provider = MockLLMProvider()
    assert provider.supports_file_input is False


def test_provider_invoke_with_files_default_raises_not_implemented() -> None:
    """默认 invoke_with_files 应明确抛出未实现异常。"""
    provider = MockLLMProvider()
    with pytest.raises(NotImplementedError, match="file_input_not_supported"):
        provider.invoke_with_files(prompt="hello", file_paths=["/tmp/demo.xlsx"])
