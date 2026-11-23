#!/usr/bin/env python3
"""Unit tests for the LLM status probe (services/llm.py: check_models, probe, status) against a fake /models server
on 127.0.0.1 — no database, no real LLM. Needs only the standard library:

    python3 tests/unit_llm_probe.py

Regression (2026-11-23): the LLM host moved from llama.cpp to vLLM. vLLM answers 404 to a model name it does not serve;
the old name stayed in LLM_MODEL, every AI feature failed, and the probe still said "connected" because it only looked
at the HTTP status of /models.
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

results = []
REPLY = {"status": 200, "body": {}}


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail and not cond else ""))


class Fake(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps(REPLY["body"]).encode()
        self.send_response(REPLY["status"])
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
config.LLM_API_BASE = f"http://127.0.0.1:{srv.server_port}/v1"


def vllm(*ids):
    return {"object": "list", "data": [{"id": i, "object": "model", "owned_by": "vllm"} for i in ids]}


def run(status, body):
    REPLY.update(status=status, body=body)
    ok = llm.probe()
    return ok, llm.status(max_age=1e9), dict(llm._state)


# check_models (pure)
check("listed model → that model, no error", llm.check_models(vllm("A", "Qwen3.8-27B-FP8")["data"]) == ("Qwen3.8-27B-FP8", None))
m, err = llm.check_models(vllm("Qwen3.8-27B-FP8-other", "X")["data"])
check("vLLM without the model → error naming it and the available ids", m is None and err and "Qwen3.8-27B-FP8" in err and "X" in err, err)
check("llama.cpp loaded file → accepted (it ignores the name)",
      llm.check_models([{"id": "/models/qwen.gguf", "owned_by": "llamacpp"}]) == ("/models/qwen.gguf", None))
check("empty list → no error (nothing to compare)", llm.check_models([]) == (None, None))
check("junk entries ignored", llm.check_models([None, "x", {"owned_by": "vllm"}]) == (None, None))

# probe / status
ok, st, s = run(200, vllm("Qwen3.8-27B-FP8", "Qwen3.8-27B-UD-Q6_K_L"))
check("vLLM serving LLM_MODEL → connected", ok and st["connected"] and st["status"] == "ok" and st["model"] == "Qwen3.8-27B-FP8", st)
check("… error cleared", s["error"] is None and s["last_ok_at"])

config.LLM_MODEL = "Qwen3.6-35B-A3B-Q6_K"   # the production setting before the fix
ok, st, s = run(200, vllm("Qwen3.8-27B-FP8", "Qwen3.8-27B-UD-Q6_K_L"))
check("vLLM NOT serving LLM_MODEL (the 2026-11-23 case) → not connected", not ok and not st["connected"] and st["status"] == "error", st)
check("… error says which model is missing", s["error"] and "Qwen3.6-35B-A3B-Q6_K" in s["error"], s["error"])

ok, st, s = run(200, {"data": [{"id": "/models/Qwen3.6-35B.gguf", "owned_by": "llamacpp"}]})
check("llama.cpp with another name → connected (old behaviour kept)", ok and st["connected"], st)
check("… reports the loaded model", st["model"] == "/models/Qwen3.6-35B.gguf", st)
config.LLM_MODEL = "Qwen3.8-27B-FP8"

ok, st, s = run(500, {"error": "boom"})
check("HTTP 500 → not connected", not ok and not st["connected"])
check("… error kept", bool(s["error"]))

ok, st, s = run(200, vllm("Qwen3.8-27B-FP8"))
check("recovers once the model is served again", ok and st["connected"] and s["error"] is None)

srv.shutdown(); srv.server_close()
config.LLM_API_BASE = f"http://127.0.0.1:{srv.server_port}/v1"
ok, st, s = run(200, {})
check("server down → not connected", not ok and not st["connected"] and s["error"])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
