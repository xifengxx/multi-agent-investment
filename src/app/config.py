"""应用配置加载模块。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import cast

from common.types import AppEnv


class ConfigError(ValueError):
    """配置错误类型。"""


@dataclass(frozen=True)
class AppConfig:
    """应用运行时配置。"""

    telegram_bot_token: str
    telegram_chat_id: str
    data_root: str
    sqlite_path: str
    analysis_parallelism: int
    dry_run: bool
    app_env: AppEnv
    # ---- LLM providers（可选）----
    # 约定：
    # - enabled_providers 表示“候选 provider 集合”，但不强制其一定具备可用凭证；
    # - effective provider 需同时具备 api_key + model（base_url 允许为空）。
    llm_enabled_providers: tuple[str, ...] = ()
    llm_min_effective_providers: int = 0
    llm_request_timeout_seconds: int = 30
    llm_max_retries: int = 2
    llm_max_instruments_per_type: int = 20
    # OpenAI
    openai_api_key: str = ""
    openai_model: str = ""
    openai_base_url: str = ""
    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = ""
    anthropic_base_url: str = ""
    # Gemini
    gemini_api_key: str = ""
    gemini_model: str = ""
    gemini_base_url: str = ""
    # Qwen
    qwen_api_key: str = ""
    qwen_model: str = ""
    qwen_base_url: str = ""
    # GLM
    glm_api_key: str = ""
    glm_model: str = ""
    glm_base_url: str = ""
    # Kimi
    kimi_api_key: str = ""
    kimi_model: str = ""
    kimi_base_url: str = ""


def _read_required_env(key: str) -> str:
    """读取必填环境变量，缺失时抛出 ConfigError。"""
    value = os.getenv(key, "").strip()
    if not value:
        raise ConfigError(f"缺失必填环境变量: {key}")
    return value


def _read_str_env(key: str, default: str = "") -> str:
    """读取字符串环境变量并去除首尾空白。"""
    return os.getenv(key, default).strip()


def _read_int_env(key: str, default: int) -> int:
    """读取整型环境变量，无法转换时抛出 ConfigError。"""
    raw = os.getenv(key, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} 不是合法整数: {raw}") from exc
    if value <= 0:
        raise ConfigError(f"{key} 必须大于 0: {raw}")
    return value


def _read_non_negative_int_env(key: str, default: int) -> int:
    """读取非负整型环境变量（>=0），无法转换或为负数时抛出 ConfigError。"""
    raw = os.getenv(key, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} 不是合法整数: {raw}") from exc
    if value < 0:
        raise ConfigError(f"{key} 必须大于等于 0: {raw}")
    return value


def _read_bool_env(key: str, default: bool) -> bool:
    """读取布尔环境变量，支持常见真值与假值。"""
    raw = os.getenv(key, str(default)).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{key} 不是合法布尔值: {raw}")


def _read_app_env() -> AppEnv:
    """读取 APP_ENV 并校验枚举值。"""
    raw = os.getenv("APP_ENV", "dev").strip().lower()
    if raw not in {"dev", "test", "prod"}:
        raise ConfigError(f"APP_ENV 不合法: {raw}")
    return cast(AppEnv, raw)


_ALLOWED_LLM_PROVIDERS: tuple[str, ...] = ("openai", "anthropic", "gemini", "qwen", "glm", "kimi")


def _read_llm_enabled_providers() -> tuple[str, ...]:
    """读取 LLM_ENABLED_PROVIDERS 并返回去重后的 provider 列表（保持顺序）。"""
    raw = os.getenv("LLM_ENABLED_PROVIDERS", "").strip()
    if not raw:
        return ()

    allowed = set(_ALLOWED_LLM_PROVIDERS)
    providers: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        name = part.strip().lower()
        if not name:
            continue
        if name not in allowed:
            raise ConfigError(f"LLM_ENABLED_PROVIDERS 包含不支持的 provider: {name}")
        if name in seen:
            continue
        seen.add(name)
        providers.append(name)
    return tuple(providers)


def _is_effective_provider(*, api_key: str, model: str) -> bool:
    """判断 provider 配置是否“可用”（具备最小调用所需字段）。"""
    return bool(api_key.strip()) and bool(model.strip())



def load_config() -> AppConfig:
    """加载应用配置并返回不可变配置对象。"""
    llm_enabled_providers = _read_llm_enabled_providers()
    llm_min_effective_providers = _read_non_negative_int_env("LLM_MIN_EFFECTIVE_PROVIDERS", default=0)
    llm_request_timeout_seconds = _read_int_env("LLM_REQUEST_TIMEOUT_SECONDS", default=30)
    llm_max_retries = _read_non_negative_int_env("LLM_MAX_RETRIES", default=2)
    llm_max_instruments_per_type = _read_non_negative_int_env("LLM_MAX_INSTRUMENTS_PER_TYPE", default=20)

    openai_api_key = _read_str_env("OPENAI_API_KEY", default="")
    openai_model = _read_str_env("OPENAI_MODEL", default="")
    openai_base_url = _read_str_env("OPENAI_BASE_URL", default="")
    anthropic_api_key = _read_str_env("ANTHROPIC_API_KEY", default="")
    anthropic_model = _read_str_env("ANTHROPIC_MODEL", default="")
    anthropic_base_url = _read_str_env("ANTHROPIC_BASE_URL", default="")
    gemini_api_key = _read_str_env("GEMINI_API_KEY", default="")
    gemini_model = _read_str_env("GEMINI_MODEL", default="")
    gemini_base_url = _read_str_env("GEMINI_BASE_URL", default="")
    qwen_api_key = _read_str_env("QWEN_API_KEY", default="")
    qwen_model = _read_str_env("QWEN_MODEL", default="")
    qwen_base_url = _read_str_env("QWEN_BASE_URL", default="")
    glm_api_key = _read_str_env("GLM_API_KEY", default="")
    glm_model = _read_str_env("GLM_MODEL", default="")
    glm_base_url = _read_str_env("GLM_BASE_URL", default="")
    kimi_api_key = _read_str_env("KIMI_API_KEY", default="")
    kimi_model = _read_str_env("KIMI_MODEL", default="")
    kimi_base_url = _read_str_env("KIMI_BASE_URL", default="")

    effective = 0
    for provider in llm_enabled_providers:
        if provider == "openai" and _is_effective_provider(api_key=openai_api_key, model=openai_model):
            effective += 1
        elif provider == "anthropic" and _is_effective_provider(
            api_key=anthropic_api_key, model=anthropic_model
        ):
            effective += 1
        elif provider == "gemini" and _is_effective_provider(api_key=gemini_api_key, model=gemini_model):
            effective += 1
        elif provider == "qwen" and _is_effective_provider(api_key=qwen_api_key, model=qwen_model):
            effective += 1
        elif provider == "glm" and _is_effective_provider(api_key=glm_api_key, model=glm_model):
            effective += 1
        elif provider == "kimi" and _is_effective_provider(api_key=kimi_api_key, model=kimi_model):
            effective += 1

    if llm_min_effective_providers > len(llm_enabled_providers):
        raise ConfigError(
            "LLM_MIN_EFFECTIVE_PROVIDERS 不应大于 LLM_ENABLED_PROVIDERS 数量: "
            f"{llm_min_effective_providers} > {len(llm_enabled_providers)}"
        )
    if llm_min_effective_providers > effective:
        raise ConfigError(
            "LLM_MIN_EFFECTIVE_PROVIDERS 不应大于实际可用 provider 数量: "
            f"{llm_min_effective_providers} > {effective}"
        )

    return AppConfig(
        telegram_bot_token=_read_required_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_read_required_env("TELEGRAM_CHAT_ID"),
        data_root=os.getenv("DATA_ROOT", "stock_data"),
        sqlite_path=os.getenv("SQLITE_PATH", "data/app.db"),
        analysis_parallelism=_read_int_env("ANALYSIS_PARALLELISM", default=3),
        dry_run=_read_bool_env("DRY_RUN", default=False),
        llm_enabled_providers=llm_enabled_providers,
        llm_min_effective_providers=llm_min_effective_providers,
        llm_request_timeout_seconds=llm_request_timeout_seconds,
        llm_max_retries=llm_max_retries,
        llm_max_instruments_per_type=llm_max_instruments_per_type,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        openai_base_url=openai_base_url,
        anthropic_api_key=anthropic_api_key,
        anthropic_model=anthropic_model,
        anthropic_base_url=anthropic_base_url,
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
        gemini_base_url=gemini_base_url,
        qwen_api_key=qwen_api_key,
        qwen_model=qwen_model,
        qwen_base_url=qwen_base_url,
        glm_api_key=glm_api_key,
        glm_model=glm_model,
        glm_base_url=glm_base_url,
        kimi_api_key=kimi_api_key,
        kimi_model=kimi_model,
        kimi_base_url=kimi_base_url,
        app_env=_read_app_env(),
    )
