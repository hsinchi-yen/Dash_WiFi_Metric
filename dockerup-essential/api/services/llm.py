"""The local LLM (llama.cpp behind an OpenAI-compatible API): status probe and chat."""
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

import config

log = logging.getLogger("wifi.llm")

_state = {"connected": False, "checked_at": 0.0, "last_ok_at": None, "error": None}
_lock = threading.Lock()


def _headers():
    return {"Content-Type": "application/json", "Authorization": f"Bearer {config.LLM_API_KEY}"}


def probe() -> bool:
    try:
        req = urllib.request.Request(f"{config.LLM_API_BASE}/models", headers=_headers())
        with urllib.request.urlopen(req, timeout=config.LLM_STATUS_TIMEOUT) as response:
            ok = response.status == 200
    except Exception as exc:  # network / HTTP errors all mean "not connected"
        ok = False
        _state["error"] = str(exc)[:200]
    with _lock:
        _state["connected"] = ok
        _state["checked_at"] = time.time()
        if ok:
            _state["last_ok_at"] = time.time()
            _state["error"] = None
    return ok


def status(max_age: float = config.LLM_STATUS_CACHE_SEC) -> dict:
    """Connection state, probing at most once per `max_age` seconds per worker."""
    if time.time() - _state["checked_at"] >= max_age:
        probe()
    with _lock:
        return {"connected": _state["connected"], "model": config.LLM_MODEL,
                "status": "ok" if _state["connected"] else "error",
                "checked_at": _state["checked_at"], "last_ok_at": _state["last_ok_at"]}


def _request(payload: dict, stream: bool = False, timeout: Optional[int] = None):
    body = {"model": config.LLM_MODEL,
            "chat_template_kwargs": {"enable_thinking": config.LLM_ENABLE_THINKING},
            "stream": stream, **payload}
    req = urllib.request.Request(f"{config.LLM_API_BASE}/chat/completions",
                                 data=json.dumps(body).encode("utf-8"), headers=_headers())
    return urllib.request.urlopen(req, timeout=timeout or config.LLM_CHAT_TIMEOUT)


def _ok():
    with _lock:
        _state["connected"] = True
        _state["last_ok_at"] = _state["checked_at"] = time.time()


def _failed(exc):
    with _lock:
        _state["connected"] = False
        _state["checked_at"] = time.time()
        _state["error"] = str(exc)[:200]


def chat(messages: list[dict], timeout: int = config.LLM_SUMMARY_TIMEOUT, max_tokens: Optional[int] = None, **options) -> str:
    """One chat completion; returns the assistant text. Raises on transport/HTTP errors.
    Thinking follows LLM_ENABLE_THINKING (off by default: ~3× faster for the same answer)."""
    payload = {"messages": messages, **options}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    try:
        with _request(payload, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        _failed(exc)
        raise
    _ok()
    return data["choices"][0]["message"]["content"]


def complete_json(messages: list[dict], schema: dict, name: str = "result", timeout: Optional[int] = None, **params) -> dict:
    """Structured output via response_format json_schema → parsed dict."""
    content = chat(messages, timeout=timeout or config.LLM_CHAT_TIMEOUT,
                   response_format={"type": "json_schema", "json_schema": {"name": name, "schema": schema}}, **params)
    return json.loads(content)


def stream_text(messages: list[dict], timeout: Optional[int] = None, **params):
    """Yield content deltas from an SSE stream."""
    try:
        with _request({"messages": messages, **params}, stream=True, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                choices = json.loads(chunk).get("choices") or []
                delta = (choices[0].get("delta") or {}).get("content") if choices else None
                if delta:
                    yield delta
        _ok()
    except GeneratorExit:
        raise
    except Exception as exc:
        _failed(exc)
        raise
