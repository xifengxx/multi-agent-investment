"""Gemini LLM Provider（Google AI Studio / Generative Language API，最小可用版）。

目标（面向个人项目）：
- 仅依赖标准库（urllib.request + json）；
- 调用 endpoint：
  https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key=...
- body: contents:[{role:'user',parts:[{text:prompt}]}]
- 解析响应 candidates[0].content.parts[*].text 并拼接为字符串；
- 失败时抛异常，并确保异常信息不泄露 api_key（包括 URL query 中的 key=...）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
import urllib.request
from urllib.error import HTTPError, URLError
from urllib.parse import quote

from analysis.providers.base import BaseLLMProvider


def _build_generate_content_url(*, model: str, api_key: str) -> str:
    """构建 Gemini generateContent 的完整 URL（包含 key query 参数）。"""
    safe_model = quote(str(model), safe="")
    return (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{safe_model}:generateContent?key={api_key}"
    )


def _redact_api_key(text: str, api_key: str) -> str:
    """从文本中脱敏 api_key（包括 URL 中的 key=...）。

    说明：
    - 防御式处理：既替换明确的 api_key 字符串，也用正则兜底脱敏 key=... 的 query 参数；
    - 避免在异常信息中泄露密钥。
    """
    safe = text
    if api_key:
        safe = safe.replace(api_key, "<REDACTED>")
    # 兜底：即便 error 文本里出现了其他形式的 key=... 也一并脱敏
    safe = re.sub(r"(key=)[^&\s]+", r"\1<REDACTED>", safe)
    return safe


@dataclass
class GeminiProvider(BaseLLMProvider):
    """Gemini Provider：调用 generateContent 并返回拼接后的文本。"""

    model: str
    api_key: str
    timeout_seconds: float = 30.0
    provider_name: str = "gemini"

    @property
    def name(self) -> str:
        """返回 provider 名称。"""
        return self.provider_name

    def invoke(self, prompt: str) -> str:
        """调用 Gemini generateContent 接口并返回拼接后的文本输出。

        Args:
            prompt: 用户 prompt（作为 contents[0].parts[0].text）。

        Returns:
            将 candidates[0].content.parts[*].text 按顺序拼接后的字符串。

        Raises:
            RuntimeError: 网络/HTTP/解析错误（错误信息已脱敏）。
            ValueError: 响应结构不符合预期（会被封装为 RuntimeError 以保证脱敏）。
        """
        url = _build_generate_content_url(model=self.model, api_key=self.api_key)
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
        }

        response_bytes = self._post(url=url, body=body, headers=headers)
        return self._parse_generate_content_response(response_bytes)

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

    def _parse_generate_content_response(self, response_bytes: bytes) -> str:
        """解析 generateContent 响应并提取 candidates[0].content.parts[*].text。"""
        try:
            payload: Any = json.loads(response_bytes.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            raise self._wrap_error(ValueError("invalid_json")) from e

        try:
            candidates = payload["candidates"]
            candidate0 = candidates[0]
            content = candidate0["content"]
            parts = content["parts"]
        except Exception as e:  # noqa: BLE001
            raise self._wrap_error(ValueError("missing_candidates_content_parts")) from e

        if not isinstance(parts, list):
            raise self._wrap_error(ValueError("parts_not_list"))

        texts: list[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if not isinstance(text, str):
                raise self._wrap_error(ValueError("part_text_not_string"))
            texts.append(text)

        return "".join(texts)

    def _wrap_error(self, error: Exception) -> RuntimeError:
        """将底层异常统一封装为 RuntimeError，并确保不泄露 api_key。"""
        safe = _redact_api_key(str(error), self.api_key)
        return RuntimeError(f"GeminiProvider 请求失败: {safe}")
