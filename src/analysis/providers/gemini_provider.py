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
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
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


def _build_files_upload_url(*, api_key: str) -> str:
    return f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={api_key}"


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

    @property
    def supports_file_input(self) -> bool:
        """声明 Gemini provider 支持附件输入。"""
        return True

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

    def invoke_with_files(self, *, prompt: str, file_paths: list[str]) -> str:
        """调用 Gemini 并附带文件（先上传获取 file_uri，再通过 file_data 引用）。

        Args:
            prompt: 用户文本提示。
            file_paths: 待上传文件绝对路径列表。

        Returns:
            模型输出文本（与 invoke 保持同样解析逻辑）。

        Raises:
            RuntimeError: 文件读取或请求失败，错误码前缀为 file_upload_failed。
        """
        if not file_paths:
            raise RuntimeError("file_upload_failed:empty_file_paths")

        url = _build_generate_content_url(model=self.model, api_key=self.api_key)
        headers = {"Content-Type": "application/json"}
        try:
            parts = self._build_user_parts(prompt=prompt, file_paths=file_paths)
            payload = {"contents": [{"role": "user", "parts": parts}]}
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            response_bytes = self._post(url=url, body=body, headers=headers)
            return self._parse_generate_content_response(response_bytes)
        except RuntimeError as e:
            raise RuntimeError(f"file_upload_failed:{e}") from e
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"file_upload_failed:{e}") from e

    def _build_user_parts(self, *, prompt: str, file_paths: list[str]) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = [{"text": prompt}]
        for raw_path in file_paths:
            file_path = Path(raw_path)
            mime_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            file_uri = self._upload_file(file_path=file_path, mime_type=mime_type)
            parts.append({"file_data": {"mime_type": mime_type, "file_uri": file_uri}})
        return parts

    def _upload_file(self, *, file_path: Path, mime_type: str) -> str:
        url = _build_files_upload_url(api_key=self.api_key)
        try:
            num_bytes = int(file_path.stat().st_size)
        except Exception as e:  # noqa: BLE001
            raise self._wrap_error(e) from e

        headers = {
            "x-goog-api-key": self.api_key,
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(num_bytes),
            "X-Goog-Upload-Header-Content-Type": mime_type,
            "Content-Type": "application/json",
        }
        start_body = json.dumps({"file": {"display_name": file_path.name}}, ensure_ascii=False).encode("utf-8")
        start_req = urllib.request.Request(url, data=start_body, headers=headers, method="POST")
        try:
            start_resp = urllib.request.urlopen(start_req, timeout=float(self.timeout_seconds))  # noqa: S310
            try:
                upload_url = None
                hdrs = getattr(start_resp, "headers", None)
                if isinstance(hdrs, dict):
                    upload_url = hdrs.get("x-goog-upload-url") or hdrs.get("X-Goog-Upload-Url")
                else:
                    get = getattr(hdrs, "get", None)
                    if callable(get):
                        upload_url = get("x-goog-upload-url") or get("X-Goog-Upload-Url")
                if not upload_url:
                    raise self._wrap_error(ValueError("missing_upload_url"))
            finally:
                close = getattr(start_resp, "close", None)
                if callable(close):
                    close()
        except (HTTPError, URLError, TimeoutError) as e:
            raise self._wrap_error(e) from e

        data = file_path.read_bytes()
        upload_headers = {
            "Content-Length": str(len(data)),
            "Content-Type": mime_type,
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
        }
        upload_req = urllib.request.Request(upload_url, data=data, headers=upload_headers, method="POST")
        try:
            upload_resp = urllib.request.urlopen(upload_req, timeout=float(self.timeout_seconds))  # noqa: S310
            try:
                payload = json.loads(upload_resp.read().decode("utf-8"))
                file_obj = payload.get("file") if isinstance(payload, dict) else None
                file_uri = file_obj.get("uri") if isinstance(file_obj, dict) else None
                if not isinstance(file_uri, str) or not file_uri.strip():
                    raise self._wrap_error(ValueError("missing_file_uri"))
                return file_uri
            finally:
                close = getattr(upload_resp, "close", None)
                if callable(close):
                    close()
        except (HTTPError, URLError, TimeoutError, ValueError) as e:
            raise self._wrap_error(e) from e

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
