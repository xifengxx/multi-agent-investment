"""最小诊断：验证 Gemini/OpenRouter/Qwen 的 endpoint 可达性与最小请求是否通过。"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DiagResult:
    name: str
    ok: bool
    http_status: int | None
    error: str | None
    response_snippet: str | None


def _read_env(name: str) -> str:
    v = (os.getenv(name) or "").strip()
    return v


def _post_json(*, url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> tuple[int, str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            status = int(getattr(resp, "status", 200))
            text = resp.read().decode("utf-8", errors="replace")
            return status, text
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        try:
            txt = e.read().decode("utf-8", errors="replace")
        except Exception:
            txt = str(e)
        if not txt.strip():
            txt = str(e)
        return int(getattr(e, "code", 0) or 0), txt
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def _get_text(*, url: str, headers: dict[str, str], timeout: float) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            status = int(getattr(resp, "status", 200))
            text = resp.read().decode("utf-8", errors="replace")
            return status, text
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        try:
            txt = e.read().decode("utf-8", errors="replace")
        except Exception:
            txt = str(e)
        if not txt.strip():
            txt = str(e)
        return int(getattr(e, "code", 0) or 0), txt
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def diag_gemini(*, timeout: float) -> DiagResult:
    api_key = _read_env("GEMINI_API_KEY")
    model = _read_env("GEMINI_MODEL")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "multi_agent_investment/diag"}
    payload = {"contents": [{"role": "user", "parts": [{"text": "ping"}]}]}
    status, text = _post_json(url=url, headers=headers, payload=payload, timeout=timeout)
    ok = 200 <= status < 300
    snippet = text[:800]
    return DiagResult(
        name="gemini.generateContent(text_only)",
        ok=ok,
        http_status=status,
        error=None if ok else "http_error",
        response_snippet=snippet,
    )


def diag_gemini_models_list(*, timeout: float) -> DiagResult:
    api_key = _read_env("GEMINI_API_KEY")
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    headers = {"Accept": "application/json", "User-Agent": "multi_agent_investment/diag"}
    status, text = _get_text(url=url, headers=headers, timeout=timeout)
    ok = 200 <= status < 300
    snippet = text[:800]
    return DiagResult(
        name="gemini.models_list",
        ok=ok,
        http_status=status,
        error=None if ok else "http_error",
        response_snippet=snippet,
    )


def diag_openrouter(*, timeout: float) -> DiagResult:
    api_key = _read_env("OPENAI_API_KEY")
    base_url = _read_env("OPENAI_BASE_URL").rstrip("/")
    model = _read_env("OPENAI_MODEL")
    referer = _read_env("OPENAI_HTTP_REFERER")
    title = _read_env("OPENAI_X_TITLE")
    url = f"{base_url}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-Title"] = title
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "temperature": 0,
    }
    status, text = _post_json(url=url, headers=headers, payload=payload, timeout=timeout)
    ok = 200 <= status < 300
    snippet = text[:800]
    return DiagResult(
        name="openrouter.chat_completions(text_only)",
        ok=ok,
        http_status=status,
        error=None if ok else "http_error",
        response_snippet=snippet,
    )


def diag_qwen(*, timeout: float) -> DiagResult:
    api_key = _read_env("QWEN_API_KEY")
    base_url = _read_env("QWEN_BASE_URL").rstrip("/")
    model = _read_env("QWEN_MODEL")
    url = f"{base_url}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {"model": model, "messages": [{"role": "user", "content": "ping"}], "temperature": 0}
    status, text = _post_json(url=url, headers=headers, payload=payload, timeout=timeout)
    ok = 200 <= status < 300
    snippet = text[:800]
    return DiagResult(
        name="qwen.chat_completions(text_only)",
        ok=ok,
        http_status=status,
        error=None if ok else "http_error",
        response_snippet=snippet,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="diag-llm-endpoints")
    p.add_argument("--timeout", default="15")
    return p


def main() -> int:
    args = build_parser().parse_args()
    timeout = float(str(args.timeout).strip() or "15")
    results = [
        diag_gemini(timeout=timeout),
        diag_gemini_models_list(timeout=timeout),
        diag_openrouter(timeout=timeout),
        diag_qwen(timeout=timeout),
    ]
    out = [r.__dict__ for r in results]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
