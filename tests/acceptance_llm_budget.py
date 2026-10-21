#!/usr/bin/env python3
"""LLM call budget: the AI report carousel is off and the LLM is used only where it explains something.

    python3 tests/acceptance_llm_budget.py [BASE]

Checks: the legacy carousel request is refused and the legacy summary makes no LLM call; the old report
store queues nothing by itself; the legacy page is gone (/legacy/ redirects to the new dashboard); one-line notes
are AI-worded only for red / amber work orders; /api/llm-usage reports calls by purpose.
"""
import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""), flush=True)


def get(path, as_text=False):
    try:
        with urllib.request.urlopen(BASE + path, timeout=60) as r:
            body = r.read()
            return r.status, body.decode() if as_text else json.loads(body or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


usage0 = get("/api/llm-usage")[1]
check("/api/llm-usage lists calls per day and purpose", {"days", "last_24h", "last_24h_total"} <= set(usage0))
status, body = get("/api/workorders/5101-260102014/ai-summary?mode=carousel&lang=en")
check("the legacy AI report carousel is refused (410)", status == 410 and body["code"] == "carousel_disabled", (status, body))
status, body = get("/api/workorders/5101-260102014/ai-summary?lang=zh")
check("the legacy AI summary returns the stored final report", status == 200 and body["summary"] and "異常" in body["summary"], body and body.get("summary", "")[:60])
status, body = get("/api/workorders/5101-260820009/ai-summary?lang=en")
check("… for a normal work order too", status == 200 and body["summary"])
q0 = get("/api/reports-queue")[1]
get("/api/reports/5101-251126008?lang=zh")
q1 = get("/api/reports-queue")[1]
check("opening an old five-section report queues nothing", q0.get("queued", 0) == q1.get("queued", 0), (q0, q1))
usage1 = get("/api/llm-usage")[1]
check("none of these made an LLM call", usage1["last_24h_total"] == usage0["last_24h_total"], (usage0["last_24h_total"], usage1["last_24h_total"]))

# The legacy page itself was removed at deployment (Q28): its address now redirects to the new dashboard.
import http.client  # noqa: E402
from urllib.parse import urlparse  # noqa: E402
u = urlparse(BASE)
c = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=10)
c.request("GET", "/legacy/")
r = c.getresponse()
check("legacy page removed: /legacy/ → 301 to / (relative, so the :8000 port is kept)", r.status == 301 and r.getheader("Location") == "/",
      (r.status, r.getheader("Location")))

items = get("/api/wo-status")[1]["items"]
flagged = {x["work_order"] for x in items if any(f["level"] in ("bad", "warn") for f in x["flags"])}
ai = {x["work_order"] for x in items if x["note"]["source"] == "llm"}
# A flagged work order whose facts just changed shows the rule sentence, marked pending, until the throttled rewrite.
waiting = {x["work_order"] for x in items if x["work_order"] in flagged and x["note"]["source"] != "llm" and x["note"].get("pending")}
check("AI-worded notes only for red / amber work orders; any flagged one without it is waiting for its rewrite",
      ai <= flagged and flagged <= ai | waiting, (len(ai), len(flagged), sorted(flagged - ai - waiting)))

passed = sum(results)
print(f"\n{passed}/{len(results)} LLM budget checks passed")
sys.exit(0 if passed == len(results) else 1)
