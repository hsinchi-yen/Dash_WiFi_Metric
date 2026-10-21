#!/usr/bin/env python3
"""Phase 5B acceptance: Dashboard hourly output, work-order detail, comparison, planned quantity (administrator),
structured final report (L3) and follow-up questions.

    python3 tests/acceptance_phase5b.py [BASE] [admin_user admin_pass] [uploader uploader_pass]

Sets and clears a planned quantity on one work order (audited). Asks a few questions (LLM).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dockerup-essential" / "api" / "services"))
import wo_rules  # noqa: E402

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ADMIN = (sys.argv[2], sys.argv[3]) if len(sys.argv) > 3 else (os.environ.get("TWEAK_USER", "qc"), os.environ.get("TWEAK_PASS", "qcpass"))
UPLOADER = (sys.argv[4], sys.argv[5]) if len(sys.argv) > 5 else ("test_uploader", "uploader-pass-2026")
NORMAL = ("5101-260820009", "TN-250_EDM-G-8MP")     # newest work order, all PASS; previous = 5101-260520007
RED = ("5101-260102014", "TN-86_PICO-IMX7")         # 4 FAILs in a row + low 2.4G
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""), flush=True)
    return cond


def call(method, path, body=None, token=None, timeout=120):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Tweak-Token"] = token
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(), method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except ValueError:
            return e.code, None


def q(**kw):
    return "?" + urlencode({k: v for k, v in kw.items() if v is not None})


def login(user, password):
    status, body = call("POST", "/api/auth/login", {"username": user, "password": password})
    return body.get("token") if status == 200 else None


admin, uploader = login(*ADMIN), login(*UPLOADER)
statuses = {(x["work_order"], x["product_model"]): x for x in call("GET", "/api/wo-status")[1]["items"]}

# ── Dashboard: hourly output 07:00–19:00 ──
ov = call("GET", "/api/overview" + q(work_order=NORMAL[0]))[1]
h = ov["hourly"]
check("Dashboard hourly output: the latest test day, 07:00–19:00, 12 hourly bars",
      h["day"] == "2026-10-01" and h["start"] == "07:00" and h["end"] == "19:00" and [p["hour"] for p in h["points"]] == [f"{x:02d}:00" for x in range(7, 19)], h)
day_units = sum(p["total"] for p in ov["trend"]["points"] if p["t"].startswith("2026-10-01"))
check("hourly bars + before + after = the units tested that day (same as the trend)",
      sum(p["units"] for p in h["points"]) + h["before"] + h["after"] == day_units, f"{sum(p['units'] for p in h['points'])} + {h['before']} + {h['after']} vs {day_units}")
check("PASS + FAIL per bar", all(p["pass"] + p["fail"] == p["units"] for p in h["points"]))
check("target line = the work order's hourly target on the Work Orders page", h["target"] == statuses[NORMAL]["uph_target"], (h["target"], statuses[NORMAL]["uph_target"]))
allp = call("GET", "/api/overview")[1]["hourly"]
check("all products in view → no single target line", allp["target"] is None)
check("legacy endpoints unchanged (overview still carries split, trend, monthly)", {"split", "trend", "monthly", "throughput"} <= set(ov))

# ── Detail ──
check("unknown work order → 404", call("GET", "/api/wo-detail" + q(work_order="5101-999999999"))[0] == 404)
d = call("GET", "/api/wo-detail" + q(work_order=RED[0], product_model=RED[1]))[1]
st = statuses[RED]
check("detail status = the Work Orders status (total, yield, light, flags)",
      d["status"]["total"] == st["total"] and d["status"]["yield_pct"] == st["yield_pct"] and d["status"]["light"] == "bad"
      and [f["code"] for f in d["status"]["flags"]] == [f["code"] for f in st["flags"]])
check("detail carries the one-line note", d["status"]["note"]["zh"] and d["status"]["note"]["en"])
check("detail numbers: 594 units, 7 FAIL, 65 retested of 666 runs, 20 active hours",
      d["metrics"]["total"] == 594 and d["metrics"]["failed"] == 7 and d["extra"]["retry_units"] == 65
      and d["extra"]["test_runs"] == 666 and d["extra"]["active_hours"] == 20, d["extra"])
check("the first TN-86 work order has no previous one", d["previous"] is None)
check("trend and throughput for the work order", d["trend"]["points"] and d["throughput"]["bands_24g"])
check("the comparison list holds the 76 other work orders, same product first",
      len(d["others"]) == 76 and all(o["product_model"] == RED[1] for o in d["others"][:12]))
n = call("GET", "/api/wo-detail" + q(work_order=NORMAL[0], product_model=NORMAL[1]))[1]
prev = n["previous"]
check("previous same-product work order of 5101-260820009 = 5101-260520007", prev and prev["metrics"]["work_order"] == "5101-260520007")
ch = prev["changes"]
ok = all(ch[k]["diff"] == round(n["metrics"][k] - prev["metrics"][k], 2 if k in ("yield_pct", "retry_rate") else 1 if k in ("p50_24g", "p50_5g", "uph", "duration_h") else 0)
         for k in ch)
check("changes = this − previous for every metric", ok, ch)
check("better / worse follows the metric (retry up = worse, 5G up = better)",
      ch["retry_rate"]["verdict"] == "worse" and ch["p50_5g"]["verdict"] == "better")
check("small changes are not notable (retry +0.2 points, units / h −3.7 of 31.5)",
      not ch["retry_rate"]["notable"] and not ch["uph"]["notable"])

# ── Compare any two ──
c = call("GET", "/api/wo-compare" + q(a=RED[0], pa=RED[1], b=NORMAL[0], pb=NORMAL[1]))[1]
check("compare any two work orders", c["a"]["work_order"] == RED[0] and c["b"]["work_order"] == NORMAL[0] and "yield_pct" in c["changes"])
check("compare with itself → 400", call("GET", "/api/wo-compare" + q(a=RED[0], pa=RED[1], b=RED[0], pb=RED[1]))[0] == 400)
check("compare with an unknown work order → 404", call("GET", "/api/wo-compare" + q(a=RED[0], b="5101-999999999"))[0] == 404)

# ── Planned quantity (administrator) ──
body = {"work_order": NORMAL[0], "product_model": NORMAL[1], "planned_qty": 300}
check("set plan without signing in → 401", call("PUT", "/api/wo-plan", body)[0] == 401)
if uploader:
    check("set plan as an uploader → 403", call("PUT", "/api/wo-plan", body, uploader)[0] == 403)
check("plan 0 → 400", call("PUT", "/api/wo-plan", {**body, "planned_qty": 0}, admin)[0] == 400)
check("plan 100000 → 400", call("PUT", "/api/wo-plan", {**body, "planned_qty": 100000}, admin)[0] == 400)
check("plan as text → 422", call("PUT", "/api/wo-plan", {**body, "planned_qty": "300"}, admin)[0] == 422)
check("plan for an unknown work order → 404", call("PUT", "/api/wo-plan", {**body, "work_order": "5101-999999999"}, admin)[0] == 404)
status, r = call("PUT", "/api/wo-plan", body, admin)
check("administrator sets 300 → stored as the administrator's", status == 200 and r["plan"]["planned_qty"] == 300 and r["plan"]["source"] == "admin", r)
s2 = {(x["work_order"], x["product_model"]): x for x in call("GET", "/api/wo-status")[1]["items"]}[NORMAL]
check("status follows: 500 of 300 → over plan, 166.7 %", s2["planned"] == 300 and s2["achieved_pct"] == 166.7
      and "over_plan" in [f["code"] for f in s2["flags"]], (s2["planned"], s2["achieved_pct"]))
check("Dashboard shows the plan too", call("GET", "/api/overview" + q(work_order=NORMAL[0]))[1]["plan"]["planned"] == 300)
status, r = call("DELETE", "/api/wo-plan" + q(work_order=NORMAL[0]), token=admin)
check("clear it → no plan (no upload batch to fall back to)", status == 200 and r["plan"] is None, r)
check("clearing again → 404", call("DELETE", "/api/wo-plan" + q(work_order=NORMAL[0]), token=admin)[0] == 404)
audit = [a for a in call("GET", "/api/admin/audit?limit=10", token=admin)[1]["items"] if a["target"] == NORMAL[0]]
check("both are in the audit with before → after",
      [a["action"] for a in audit[:2]] == ["plan_clear", "plan_set"] and audit[1]["detail"]["planned_qty"].endswith("→ 300"),
      [(a["action"], a["detail"]) for a in audit[:2]])

# ── Batches ──
check("upload batches need sign-in", call("GET", "/api/wo-batches" + q(work_order=NORMAL[0]))[0] == 401)
check("administrator sees the work order's batches", call("GET", "/api/wo-batches" + q(work_order=NORMAL[0]), token=admin)[0] == 200)


# ── Final report ──
def wait_ready(key, timeout=240):
    end = time.time() + timeout
    while time.time() < end:
        r = call("GET", "/api/wo-report" + q(work_order=key[0], product_model=key[1]))[1]
        if r["state"] == "ready" and not r["pending"]:
            return r
        time.sleep(4)
    return r


for key in (NORMAL, RED):
    r = call("GET", "/api/wo-report" + q(work_order=key[0], product_model=key[1]))[1]
    if r["state"] != "ready":
        call("POST", "/api/wo-report/generate", {"work_order": key[0], "product_model": key[1]})
    r = wait_ready(key)
    check(f"{key[0]}: report ready, written by the AI", r["state"] == "ready" and r["source"] == "llm", (r["state"], r["source"], r["error"]))
    bad = []
    for lang in ("zh", "en"):
        part = r["report"][lang]
        for text in [part["summary"], *part["anomalies"], *part["causes"], *part["actions"]]:
            ok, extra = wo_rules.numbers_ok(text, r["facts"], key)
            if not ok:
                bad.append((lang, extra, text))
    check(f"{key[0]}: every number in the report is a fact", not bad, bad[:2])
    en_cjk = any("一" <= ch <= "鿿" for part in [r["report"]["en"]] for text in [part["summary"], *part["anomalies"], *part["causes"], *part["actions"]] for ch in text)
    check(f"{key[0]}: the English report has no Chinese", not en_cjk)
    if key == NORMAL:
        check("normal work order → summary only, no invented concerns",
              all(not r["report"][lang][k] for lang in ("zh", "en") for k in ("anomalies", "causes", "actions")), r["report"]["zh"])
        check("the summary compares with the previous work order", "5101-260520007" in r["report"]["zh"]["summary"] or "上一" in r["report"]["zh"]["summary"]
              or "前" in r["report"]["zh"]["summary"], r["report"]["zh"]["summary"])
    else:
        part = r["report"]["zh"]
        check("red work order → anomalies cover both flags, causes and actions given",
              len(part["anomalies"]) >= 2 and part["causes"] and part["actions"], part)
        check("causes are phrased as possibilities", all("可能" in x for x in part["causes"]) and
              all(any(w in x.lower() for w in ("may", "possibl", "could", "might")) for x in r["report"]["en"]["causes"]),
              part["causes"])
check("facts table carries the thresholds, the failure categories (5F) and the previous work order",
      r["facts"]["thresholds"]["yield_good"] == 98 and r["facts"]["fail_runs_by_category"] and r["facts"]["failing_units_by_category"]
      and "previous" in r["facts"])

# ── Follow-up questions ──
for question, lang in (("為什麼這張工單是紅燈？", "zh"), ("How does it compare with the previous work order?", "en")):
    status, a = call("POST", "/api/wo-report/ask", {"work_order": RED[0], "product_model": RED[1], "question": question, "lang": lang})
    facts = call("GET", "/api/wo-report" + q(work_order=RED[0], product_model=RED[1]))[1]["facts"]
    ok = status == 200 and a["answer"] and wo_rules.numbers_ok(a["answer"], facts, RED)[0]
    check(f"ask ({lang}): answered from the facts, numbers checked", ok, a)
status, a = call("POST", "/api/wo-report/ask", {"work_order": RED[0], "product_model": RED[1], "question": "這批的測試室溫度是多少？", "lang": "zh"})
check("ask about something not in the facts → says so (answerable false or refused)", status == 200 and (not a["answerable"] or a["refused"]), a)
check("no codes or field names in answers", "fail_streak" not in a["answer"] and "facts" not in a["answer"].lower(), a["answer"])
check("empty question → 422", call("POST", "/api/wo-report/ask", {"work_order": RED[0], "question": ""})[0] == 422)
codes = [call("POST", "/api/wo-report/generate", {"work_order": RED[0], "product_model": RED[1]})[0] for _ in range(16)]
check("rewrite requests are rate-limited (429 after a few a minute)", 429 in codes, codes)

passed = sum(results)
print(f"\n{passed}/{len(results)} Phase 5B checks passed")
sys.exit(0 if passed == len(results) else 1)
