"""OpenAI-compatible LLM Provider（最小可用版）。

面向个人项目的目标：
- 依赖标准库（urllib.request + json），无需额外 SDK；
- 兼容常见的 OpenAI Chat Completions 风格接口（/v1/chat/completions）；
- 支持 base_url / model / api_key / timeout_seconds / max_retries / extra_headers；
- 返回 choices[0].message.content 的文本内容；
- 失败时抛异常，但异常信息不得泄露 api_key。
"""

from __future__ import annotations

import base64
import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import urllib.request
from urllib.error import HTTPError, URLError

from analysis.providers.base import BaseLLMProvider


def _normalize_base_url(base_url: str) -> str:
    """规范化 base_url：去除首尾空白与末尾 `/`。"""
    return base_url.strip().rstrip("/")


def _build_chat_completions_url(base_url: str) -> str:
    """由 base_url 推导 chat/completions 端点 URL。

    规则：
    - 若 base_url 以 `/v1` 结尾：拼接 `/chat/completions`
    - 否则：拼接 `/v1/chat/completions`
    """
    normalized = _normalize_base_url(base_url)
    if normalized.endswith("/v1"):
        return f"{normalized}/chat/completions"
    return f"{normalized}/v1/chat/completions"


def _redact_api_key(text: str, api_key: str) -> str:
    """从文本中脱敏 api_key（防御式：即便错误信息中意外包含 key 也不暴露）。"""
    if not api_key:
        return text
    return text.replace(api_key, "<REDACTED>")


@dataclass
class OpenAICompatibleProvider(BaseLLMProvider):
    """OpenAI-compatible Provider：调用 chat/completions 并返回 message.content。"""

    base_url: str
    model: str
    api_key: str
    timeout_seconds: float = 30.0
    max_retries: int = 0
    extra_headers: dict[str, str] | None = None
    provider_name: str = "openai_compatible"

    @property
    def name(self) -> str:
        """返回 provider 名称。"""
        return self.provider_name

    @property
    def supports_file_input(self) -> bool:
        """声明 OpenAI-compatible provider 支持附件输入。"""
        return True

    def invoke(self, prompt: str) -> str:
        """调用 OpenAI-compatible Chat Completions 接口并返回文本内容。

        Args:
            prompt: 用户 prompt（作为 messages[0].content）。

        Returns:
            choices[0].message.content 文本。

        Raises:
            RuntimeError: 网络/HTTP/解析错误。
            ValueError: 响应结构不符合预期。
        """
        url = _build_chat_completions_url(self.base_url)
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if self.extra_headers:
            headers.update({str(k): str(v) for k, v in self.extra_headers.items()})

        attempts = max(0, int(self.max_retries)) + 1
        for attempt_index in range(attempts):
            try:
                response_bytes = self._post(url=url, body=body, headers=headers)
                return self._parse_chat_completions_response(response_bytes)
            except Exception as e:  # noqa: BLE001 - 个人项目：集中封装为 RuntimeError
                if attempt_index >= attempts - 1:
                    raise
                continue

        # attempts>=1，循环中要么 return，要么在最后一次 attempt 里 raise
        raise RuntimeError("OpenAICompatibleProvider unknown_error")

    def invoke_with_files(self, *, prompt: str, file_paths: list[str]) -> str:
        """调用 OpenAI-compatible Chat Completions，并附带 input_file parts。

        Args:
            prompt: 用户文本提示。
            file_paths: 待上传文件路径列表。

        Returns:
            choices[0].message.content 文本。

        Raises:
            RuntimeError: 文件读取或请求失败，错误码前缀为 file_upload_failed。
        """
        if not file_paths:
            raise RuntimeError("file_upload_failed:empty_file_paths")

        url = _build_chat_completions_url(self.base_url)
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if self.extra_headers:
            headers.update({str(k): str(v) for k, v in self.extra_headers.items()})

        try:
            content_parts = self._build_content_parts(prompt=prompt, file_paths=file_paths)
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": content_parts}],
            }
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            response_bytes = self._post(url=url, body=body, headers=headers)
            return self._parse_chat_completions_response(response_bytes)
        except RuntimeError as e:
            raise RuntimeError(f"file_upload_failed:{e}") from e
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"file_upload_failed:{e}") from e

    def _build_content_parts(self, *, prompt: str, file_paths: list[str]) -> list[dict[str, str]]:
        """构造 Chat Completions 消息 content，包含 text 与 input_file 部分。"""
        parts: list[dict[str, str]] = [{"type": "text", "text": prompt}]
        for raw_path in file_paths:
            file_path = Path(raw_path)
            file_bytes = file_path.read_bytes()
            mime_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            parts.append(
                {
                    "type": "input_file",
                    "filename": file_path.name,
                    "mime_type": mime_type,
                    "data": base64.b64encode(file_bytes).decode("ascii"),
                }
            )
        return parts

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

    def _parse_chat_completions_response(self, response_bytes: bytes) -> str:
        """解析 OpenAI Chat Completions 响应并提取 message.content。"""
        try:
            payload: Any = json.loads(response_bytes.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            raise self._wrap_error(ValueError("invalid_json")) from e

        try:
            content = payload["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            raise self._wrap_error(ValueError("missing_choices_message_content")) from e

        if not isinstance(content, str):
            raise self._wrap_error(ValueError("message_content_not_string"))
        return content

    def _wrap_error(self, error: Exception) -> RuntimeError:
        """将底层异常统一封装为 RuntimeError，并确保不泄露 api_key。"""
        safe = _redact_api_key(str(error), self.api_key)
        return RuntimeError(f"OpenAICompatibleProvider 请求失败: {safe}")
