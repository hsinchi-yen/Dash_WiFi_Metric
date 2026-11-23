"""The local LLM behind an OpenAI-compatible API: status probe and chat. Works with vLLM and llama.cpp.

The two servers differ in ways that matter here:
  - vLLM answers 404 "The model `X` does not exist" to a model name it does not serve; llama.cpp ignores the name.
    The probe therefore reads /v1/models and sends LLM_MODEL only when it is listed, otherwise the served model
    (2026-11-23: the host moved from llama.cpp to vLLM, the old name stayed in the config and every AI call failed
    while the status light stayed green). A chat refused for its model name re-probes and is retried once, so a
    model swapped on the host while the API runs is picked up on the next call.
  - llama.cpp lists its models under "data" (and, in newer builds, also "models"); ids are often file names.
  - Without a reasoning parser a thinking model may put "<think>…</think>" into the content; with one (vLLM) the
    content can be null when the answer was cut off. Both are normalised by _content().

Every completion is logged in llm_usage (purpose, success, duration) so the call rate can be watched
(GET /api/llm-usage, Settings page). The status probe (/models) is not a completion and is not logged."""
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

import config

log = logging.getLogger("wifi.llm")

_state = {"connected": False, "checked_at": 0.0, "last_ok_at": None, "error": None,
          "request_model": None, "served_model": None, "backend": None, "note": None}
_lock = threading.Lock()

_THINK = re.compile(r"^\s*<think>.*?</think>\s*", re.S)
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def _headers():
    return {"Content-Type": "application/json", "Authorization": f"Bearer {config.LLM_API_KEY}"}


def model_entries(payload) -> list[dict]:
    """The model list of a /models answer, as dicts that have an "id" (vLLM, OpenAI, llama.cpp old and new)."""
    if not isinstance(payload, dict):
        return []
    out = []
    for key in ("data", "models"):
        for m in payload.get(key) or []:
            if isinstance(m, dict):
                mid = m.get("id") or m.get("model") or m.get("name")
                if mid and all(e["id"] != mid for e in out):
                    out.append({**m, "id": str(mid)})
    return out


def backend_of(entries: list[dict]) -> Optional[str]:
    owners = {str(m.get("owned_by", "")).lower() for m in entries}
    if "vllm" in owners:
        return "vllm"
    if "llamacpp" in owners or "llama.cpp" in owners:
        return "llamacpp"
    return next(iter(owners - {""}), None)


def resolve_model(entries: list[dict]) -> tuple[str, Optional[str]]:
    """(model name to send, note). LLM_MODEL when the server lists it; else the first served model (llama.cpp ignores
    the name anyway, vLLM would refuse LLM_MODEL); no list → LLM_MODEL as it is."""
    ids = [m["id"] for m in entries]
    if not ids or config.LLM_MODEL in ids:
        return config.LLM_MODEL, None
    if backend_of(entries) == "llamacpp":
        return ids[0], None
    return ids[0], f"LLM_MODEL {config.LLM_MODEL!r} is not served; using {ids[0]!r} (available: {', '.join(ids)[:150]})"


def probe() -> bool:
    error = None
    try:
        req = urllib.request.Request(f"{config.LLM_API_BASE}/models", headers=_headers())
        with urllib.request.urlopen(req, timeout=config.LLM_STATUS_TIMEOUT) as response:
            ok = response.status == 200
            try:
                entries = model_entries(json.loads(response.read().decode("utf-8")))
            except Exception:
                entries = []
        if ok:
            model, note = resolve_model(entries)
            if note and note != _state.get("note"):     # once per change, not every probe
                log.warning("LLM: %s", note)
            with _lock:
                _state.update(request_model=model, note=note, backend=backend_of(entries),
                              served_model=model if entries else _state.get("served_model"))
    except Exception as exc:  # network / HTTP errors all mean "not connected"
        ok = False
        error = str(exc)[:200]
    with _lock:
        _state["connected"] = ok
        _state["checked_at"] = time.time()
        _state["error"] = None if ok else error
        if ok:
            _state["last_ok_at"] = time.time()
    return ok


def status(max_age: float = config.LLM_STATUS_CACHE_SEC) -> dict:
    """Connection state, probing at most once per `max_age` seconds per worker."""
    if time.time() - _state["checked_at"] >= max_age:
        probe()
    with _lock:
        return {"connected": _state["connected"], "model": _state.get("served_model") or config.LLM_MODEL,
                "status": "ok" if _state["connected"] else "error",
                "checked_at": _state["checked_at"], "last_ok_at": _state["last_ok_at"],
                "backend": _state.get("backend"), "note": _state.get("note")}


def _model() -> str:
    with _lock:
        return _state.get("request_model") or config.LLM_MODEL


def _open(body: dict, timeout: Optional[int]):
    req = urllib.request.Request(f"{config.LLM_API_BASE}/chat/completions",
                                 data=json.dumps(body).encode("utf-8"), headers=_headers())
    return urllib.request.urlopen(req, timeout=timeout or config.LLM_CHAT_TIMEOUT)


def _model_refused(exc: urllib.error.HTTPError) -> bool:
    """vLLM: 404 {"error": {"type": "NotFoundError", "param": "model", "message": "The model `X` does not exist."}}."""
    if exc.code not in (400, 404):
        return False
    try:
        text = exc.read().decode("utf-8", errors="replace")[:500]
    except Exception:
        text = ""
    exc.msg = f"{exc.msg}: {text}" if text else exc.msg      # keep the server's reason in the logged error
    low = text.lower()
    return "model" in low and ("does not exist" in low or "not found" in low or '"param": "model"' in low
                               or '"param":"model"' in low)


def _request(payload: dict, stream: bool = False, timeout: Optional[int] = None):
    if not _state["checked_at"]:
        probe()                                    # first call in this worker: learn the served model first
    body = {"model": _model(),
            "chat_template_kwargs": {"enable_thinking": config.LLM_ENABLE_THINKING},
            "stream": stream, **payload}
    try:
        return _open(body, timeout)
    except urllib.error.HTTPError as exc:
        if not _model_refused(exc):
            raise
        tried = body["model"]
        probe()
        if _model() == tried:
            raise
        log.warning("LLM refused model %r; retrying with %r", tried, _model())
        return _open({**body, "model": _model()}, timeout)


def _log(purpose: str, ok: bool, t0: float) -> None:
    try:
        import db
        with db.db() as conn:
            db.execute(conn, "INSERT INTO llm_usage (purpose, ok, ms) VALUES (%s, %s, %s)",
                       (purpose[:40], ok, int((time.perf_counter() - t0) * 1000)))
    except Exception:   # the log must never break a call
        log.debug("llm usage log failed", exc_info=True)


def _ok():
    with _lock:
        _state["connected"] = True
        _state["last_ok_at"] = _state["checked_at"] = time.time()


def _failed(exc):
    with _lock:
        _state["connected"] = False
        _state["checked_at"] = time.time()
        _state["error"] = str(exc)[:200]


def _content(data: dict) -> str:
    """The answer text of a completion: "" for a null content, a leading <think>…</think> block removed."""
    message = ((data.get("choices") or [{}])[0] or {}).get("message") or {}
    return _THINK.sub("", message.get("content") or "", count=1)


def chat(messages: list[dict], timeout: int = config.LLM_SUMMARY_TIMEOUT, max_tokens: Optional[int] = None,
         purpose: str = "chat", **options) -> str:
    """One chat completion; returns the assistant text. Raises on transport/HTTP errors.
    Thinking follows LLM_ENABLE_THINKING (off by default: ~3× faster for the same answer)."""
    payload = {"messages": messages, **options}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    t0 = time.perf_counter()
    try:
        with _request(payload, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        _failed(exc)
        _log(purpose, False, t0)
        raise
    _ok()
    _log(purpose, True, t0)
    if data.get("model"):
        with _lock:
            _state["served_model"] = data["model"]      # what the server actually ran (llama.cpp ignores the name)
    return _content(data)


def model_name() -> str:
    """The model the LLM server last reported (llama.cpp serves whatever it has loaded), else the configured name."""
    with _lock:
        return _state.get("served_model") or config.LLM_MODEL


def parse_json(content: str):
    """json.loads, tolerating a ```json fence around the object (a server that ignores response_format)."""
    m = _FENCE.match(content or "")
    return json.loads(m.group(1) if m else content)


def complete_json(messages: list[dict], schema: dict, name: str = "result", timeout: Optional[int] = None, **params) -> dict:
    """Structured output via response_format json_schema → parsed dict (vLLM and llama.cpp both enforce the schema)."""
    content = chat(messages, timeout=timeout or config.LLM_CHAT_TIMEOUT, purpose=name,
                   response_format={"type": "json_schema", "json_schema": {"name": name, "schema": schema}}, **params)
    return parse_json(content)


def stream_text(messages: list[dict], timeout: Optional[int] = None, purpose: str = "stream", **params):
    """Yield content deltas from an SSE stream (reasoning deltas, sent apart by vLLM, are skipped)."""
    t0 = time.perf_counter()
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
        _log(purpose, True, t0)
    except GeneratorExit:
        _log(purpose, True, t0)
        raise
    except Exception as exc:
        _failed(exc)
        _log(purpose, False, t0)
        raise
