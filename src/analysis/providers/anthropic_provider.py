"""Anthropic LLM Provider（最小可用版）。

目标（面向个人项目）：
- 仅依赖标准库（urllib.request + json）；
- 调用 Anthropic Messages API：POST https://api.anthropic.com/v1/messages；
- headers: x-api-key / anthropic-version / content-type；
- body: model / max_tokens / messages；
- 解析响应 content blocks，将其中 type=="text" 的 text 字段拼接为字符串；
- 失败时抛异常，并确保异常信息不泄露 api_key。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
import urllib.request
from urllib.error import HTTPError, URLError

from analysis.providers.base import BaseLLMProvider


_ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


def _redact_api_key(text: str, api_key: str) -> str:
    """从文本中脱敏 api_key（防御式：即便错误信息中意外包含 key 也不暴露）。"""
    if not api_key:
        return text
    return text.replace(api_key, "<REDACTED>")


@dataclass
class AnthropicProvider(BaseLLMProvider):
    """Anthropic Messages Provider：调用 /v1/messages 并返回拼接后的文本。"""

    model: str
    api_key: str
    max_tokens: int
    timeout_seconds: float = 30.0
    anthropic_version: str = "2023-06-01"
    provider_name: str = "anthropic"

    @property
    def name(self) -> str:
        """返回 provider 名称。"""
        return self.provider_name

    def invoke(self, prompt: str) -> str:
        """调用 Anthropic Messages API 并返回拼接后的文本输出。

        Args:
            prompt: 用户 prompt（作为 messages[0].content）。

        Returns:
            将响应 content blocks 中 type=="text" 的 text 按顺序拼接后的字符串。

        Raises:
            RuntimeError: 网络/HTTP/解析错误（错误信息已脱敏）。
        """
        payload = {
            "model": self.model,
            "max_tokens": int(self.max_tokens),
            "messages": [{"role": "user", "content": prompt}],
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self.anthropic_version,
        }

        response_bytes = self._post(url=_ANTHROPIC_MESSAGES_URL, body=body, headers=headers)
        return self._parse_messages_response(response_bytes)

    def _post(self, *, url: str, body: bytes, headers: dict[str, str]) -> bytes:
        """执行一次 HTTP POST 请求并返回响应 bytes。"""
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            resp = urllib.request.urlopen(  # noqa: S310 - 标准库 HTTP 调用
                request,
                timeout=float(self.timeout_seconds),
            )
            try:
                return resp.read()
            finally:
                close = getattr(resp, "close", None)
                if callable(close):
                    close()
        except (HTTPError, URLError, TimeoutError) as e:
            raise self._wrap_error(e) from e

    def _parse_messages_response(self, response_bytes: bytes) -> str:
        """解析 Anthropic Messages 响应并提取 content blocks 文本。"""
        try:
            payload: Any = json.loads(response_bytes.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            raise self._wrap_error(ValueError("invalid_json")) from e

        try:
            content_blocks = payload["content"]
        except Exception as e:  # noqa: BLE001
            raise self._wrap_error(ValueError("missing_content_blocks")) from e

        if not isinstance(content_blocks, list):
            raise self._wrap_error(ValueError("content_blocks_not_list"))

        texts: list[str] = []
        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "text":
                continue
            text = block.get("text")
            if not isinstance(text, str):
                raise self._wrap_error(ValueError("content_block_text_not_string"))
            texts.append(text)

        return "".join(texts)

    def _wrap_error(self, error: Exception) -> RuntimeError:
        """将底层异常统一封装为 RuntimeError，并确保不泄露 api_key。"""
        safe = _redact_api_key(str(error), self.api_key)
        return RuntimeError(f"AnthropicProvider 请求失败: {safe}")

