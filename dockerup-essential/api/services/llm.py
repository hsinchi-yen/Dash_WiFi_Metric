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


def chat(messages: list[dict], timeout: int = config.LLM_SUMMARY_TIMEOUT, max_tokens: Optional[int] = None, **options) -> str:
    """One chat completion; returns the assistant text. Raises on transport/HTTP errors.
    Thinking follows LLM_ENABLE_THINKING (off by default: ~3× faster for the same answer)."""
    body = {"model": config.LLM_MODEL, "messages": messages,
            "chat_template_kwargs": {"enable_thinking": config.LLM_ENABLE_THINKING}, **options}
    if max_tokens:
        body["max_tokens"] = max_tokens
    req = urllib.request.Request(f"{config.LLM_API_BASE}/chat/completions",
                                 data=json.dumps(body).encode("utf-8"), headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    with _lock:
        _state["connected"] = True
        _state["last_ok_at"] = _state["checked_at"] = time.time()
    return data["choices"][0]["message"]["content"]
