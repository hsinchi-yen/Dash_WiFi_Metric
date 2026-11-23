#!/usr/bin/env python3
"""Unit tests for services/llm.py against a fake OpenAI-compatible server on 127.0.0.1 that behaves like vLLM or like
llama.cpp — no database, no real LLM. Needs only the standard library:

    python3 tests/unit_llm_probe.py

Regression (2026-11-23): the LLM host moved from llama.cpp to vLLM. vLLM answers 404 to a model name it does not
serve; the old name stayed in LLM_MODEL, every AI feature failed, and the probe still said "connected". The client
must now work with either server whatever LLM_MODEL says: send LLM_MODEL when it is served, else the served model.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dockerup-essential", "api"))
os.environ["LLM_MODEL"] = "Qwen3.8-27B-FP8"
import config  # noqa: E402
from services import llm  # noqa: E402

llm._log = lambda *a, **k: None      # llm_usage needs the database; without one psycopg2 would wait on the connect
results = []
# kind: vllm | llamacpp | llamacpp_new (models under "data" and "models"); ids served; models_status; content
SRV = {"kind": "vllm", "ids": ["Qwen3.8-27B-FP8", "Qwen3.8-27B-UD-Q6_K_L"], "models_status": 200,
       "content": "hello", "seen": []}


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail and not cond else ""))


class Fake(BaseHTTPRequestHandler):
    def _send(self, code, obj=None, raw=None, ctype="application/json"):
        body = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if SRV["models_status"] != 200:
            return self._send(SRV["models_status"], {"error": "boom"})
        owner = "vllm" if SRV["kind"] == "vllm" else "llamacpp"
        data = [{"id": i, "object": "model", "owned_by": owner} for i in SRV["ids"]]
        out = {"object": "list", "data": data}
        if SRV["kind"] == "llamacpp_new":
            out["models"] = [{"name": i, "model": i} for i in SRV["ids"]]
        self._send(200, out)

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SRV["seen"].append(body["model"])
        if SRV["kind"] == "vllm" and body["model"] not in SRV["ids"]:
            return self._send(404, {"error": {"message": f"The model `{body['model']}` does not exist.",
                                              "type": "NotFoundError", "param": "model", "code": 404}})
        ran = body["model"] if SRV["kind"] == "vllm" else SRV["ids"][0]
        if body.get("stream"):
            chunks = [{"choices": [{"delta": {"reasoning": "hmm"}}]}] + \
                     [{"choices": [{"delta": {"content": p}}]} for p in ("hel", "lo")]
            raw = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            return self._send(200, raw=raw.encode(), ctype="text/event-stream")
        self._send(200, {"model": ran, "choices": [{"message": {"role": "assistant", "content": SRV["content"]}}]})

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
config.LLM_API_BASE = f"http://127.0.0.1:{srv.server_port}/v1"


def reset(**kw):
    SRV.update({"kind": "vllm", "ids": ["Qwen3.8-27B-FP8", "Qwen3.8-27B-UD-Q6_K_L"], "models_status": 200,
                "content": "hello", "seen": []}, **kw)
    llm._state.update(connected=False, checked_at=0.0, error=None, request_model=None, served_model=None,
                      backend=None, note=None)


def ask():
    return llm.chat([{"role": "user", "content": "hi"}], timeout=5, purpose="unit")


# --- pure helpers
E = llm.model_entries
check("entries: vLLM data", [m["id"] for m in E({"data": [{"id": "A"}, {"id": "B"}]})] == ["A", "B"])
check("entries: llama.cpp new 'models' key, deduplicated",
      [m["id"] for m in E({"data": [{"id": "a.gguf"}], "models": [{"name": "a.gguf"}, {"model": "b.gguf"}]})] == ["a.gguf", "b.gguf"])
check("entries: junk ignored", E({"data": [None, "x", {"owned_by": "vllm"}]}) == [] and E(None) == [] and E([]) == [])
config.LLM_MODEL = "Qwen3.8-27B-FP8"
check("resolve: listed → LLM_MODEL, no note", llm.resolve_model(E({"data": [{"id": "X"}, {"id": "Qwen3.8-27B-FP8"}]})) == ("Qwen3.8-27B-FP8", None))
m, note = llm.resolve_model([{"id": "X", "owned_by": "vllm"}])
check("resolve: vLLM without it → served model + note naming both", m == "X" and "Qwen3.8-27B-FP8" in note and "'X'" in note, note)
check("resolve: llama.cpp → its model, no note", llm.resolve_model([{"id": "q.gguf", "owned_by": "llamacpp"}]) == ("q.gguf", None))
check("resolve: empty list → LLM_MODEL", llm.resolve_model([]) == ("Qwen3.8-27B-FP8", None))
check("content: <think> block removed", llm._content({"choices": [{"message": {"content": "<think>x\ny</think>\n\nAnswer"}}]}) == "Answer")
check("content: null → ''", llm._content({"choices": [{"message": {"content": None, "reasoning": "r"}}]}) == "")
check("content: no choices → ''", llm._content({}) == "")
check("json: fenced object parsed", llm.parse_json('```json\n{"a": 1}\n```') == {"a": 1})
check("json: plain object parsed", llm.parse_json('\n\n{"a": 2}') == {"a": 2})

# --- vLLM
reset(); config.LLM_MODEL = "Qwen3.8-27B-FP8"
st = llm.status(0)
check("vLLM, LLM_MODEL served → connected, sends LLM_MODEL", st["connected"] and st["model"] == "Qwen3.8-27B-FP8" and st["backend"] == "vllm" and not st["note"], st)
check("… chat works", ask() == "hello" and SRV["seen"] == ["Qwen3.8-27B-FP8"], SRV["seen"])

reset(); config.LLM_MODEL = "Qwen3.6-35B-A3B-Q6_K"       # the production setting of 2026-11-23
st = llm.status(0)
check("vLLM, LLM_MODEL NOT served (the 2026-11-23 case) → still connected", st["connected"] and st["status"] == "ok", st)
check("… uses the served model and says so", st["model"] == "Qwen3.8-27B-FP8" and "Qwen3.6-35B-A3B-Q6_K" in (st["note"] or ""), st)
check("… chat works without a 404", ask() == "hello" and SRV["seen"] == ["Qwen3.8-27B-FP8"], SRV["seen"])
check("… complete_json works", llm.complete_json([{"role": "user", "content": "j"}], {"type": "object"}, name="unit") == {"a": 1}
      if SRV.update(content='{"a": 1}') is None else False)
SRV["content"] = "hello"
check("… stream works, reasoning deltas skipped", "".join(llm.stream_text([{"role": "user", "content": "s"}], timeout=5)) == "hello")

reset(); config.LLM_MODEL = "Qwen3.6-35B-A3B-Q6_K"
check("first chat in a worker (no probe yet) → probes first, no 404", ask() == "hello" and SRV["seen"] == ["Qwen3.8-27B-FP8"], SRV["seen"])

reset(); config.LLM_MODEL = "Qwen3.8-27B-FP8"
llm.status(0)
SRV["ids"] = ["Qwen4-32B"]                                  # model swapped on the host after the probe
check("model swapped while running → 404, re-probe, retried once, works", ask() == "hello" and SRV["seen"] == ["Qwen3.8-27B-FP8", "Qwen4-32B"], SRV["seen"])
check("… status follows", llm.status(1e9)["model"] == "Qwen4-32B")

reset(); config.LLM_MODEL = "Qwen3.8-27B-FP8"
llm.status(0)
SRV["ids"] = []                                             # nothing listed: the retry cannot help
try:
    ask(); raised = False
except Exception as exc:
    raised = "does not exist" in str(exc)
check("refused and nothing better listed → error raised with the server's reason (no loop)", raised and len(SRV["seen"]) == 1, SRV["seen"])

# --- llama.cpp
for kind in ("llamacpp", "llamacpp_new"):
    reset(kind=kind, ids=["/models/Qwen3.6-35B-A3B-Q6_K.gguf"]); config.LLM_MODEL = "anything"
    st = llm.status(0)
    check(f"{kind}: any LLM_MODEL → connected, no note", st["connected"] and st["backend"] == "llamacpp" and not st["note"], st)
    check(f"{kind}: chat works, reports the loaded model", ask() == "hello" and llm.model_name() == "/models/Qwen3.6-35B-A3B-Q6_K.gguf")
reset(kind="llamacpp", ids=["q.gguf"], content="<think>plan</think>\n{\"a\": 3}"); config.LLM_MODEL = "q.gguf"
check("llama.cpp: <think> in content + JSON → parsed", llm.complete_json([{"role": "user", "content": "j"}], {"type": "object"}) == {"a": 3})

# --- failures
reset(models_status=500); config.LLM_MODEL = "Qwen3.8-27B-FP8"
st = llm.status(0)
check("/models HTTP 500 → not connected, error kept", not st["connected"] and st["status"] == "error" and llm._state["error"])
SRV["models_status"] = 200
check("… recovers", llm.status(0)["connected"] and llm._state["error"] is None)

srv.shutdown(); srv.server_close()
check("server down → not connected", not llm.probe() and not llm.status(1e9)["connected"] and llm._state["error"])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
