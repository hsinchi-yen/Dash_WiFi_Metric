#!/usr/bin/env python3
"""Phase 5E acceptance (API): the anomaly banner (rules, replayed on real days), Ask the dashboard (the LLM only reads
the question; every answer equals the Trend page's own numbers; unit-level questions refused), and weekly / monthly
reports (listing, report, Markdown, writing by the administrator only, summary numbers checked).

    python3 tests/acceptance_phase5e.py [BASE] [admin_user admin_pass]

Asks the LLM about 6 questions and writes one report summary.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ADMIN = (sys.argv[2], sys.argv[3]) if len(sys.argv) > 3 else (os.environ.get("TWEAK_USER", "qc"), os.environ.get("TWEAK_PASS", "qcpass"))
UPLOADER = ("test_uploader", "uploader-pass-2026")
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""), flush=True)


def call(method, path, body=None, token=None, raw=False):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(), method=method,
                                 headers={"Content-Type": "application/json", **({"X-Tweak-Token": token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            data = r.read()
            return r.status, (data if raw else json.loads(data or b"null"))
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            return e.code, json.loads(data or b"null")
        except ValueError:
            return e.code, data


def get(path, **kw):
    status, body = call("GET", path + ("?" + urlencode({k: v for k, v in kw.items() if v is not None}) if kw else ""))
    assert status == 200, (path, kw, status, body)
    return body


def login(user, password):
    status, body = call("POST", "/api/auth/login", {"username": user, "password": password})
    return body.get("token") if status == 200 else None


# ── anomaly banner ──
a = get("/api/alerts")
check("banner now: nothing (the local copy's last test is 10/01)", a["level"] == "none" and a["items"] == [], a["items"][:2])
a = get("/api/alerts", now="2026-01-13T11:00:00")
codes = [(i["code"], i["values"].get("wo")) for i in a["items"]]
check("replay 01/13 11:00: red, the two red work orders first", a["level"] == "bad" and codes[0][0] == "wo_red"
      and {("wo_red", "5101-251126008"), ("wo_red", "5105-260102001")} <= set(codes), codes)
burst = next((i for i in a["items"] if i["code"] == "burst" and i["values"]["category"] == "WIFI_TP_LOW_24G"), None)
check("replay 01/13: the 2.4G burst (6 units in 60 min) is flagged, with a link to the Fail List", burst and burst["values"]["units"] == 6
      and burst["link"]["fails"], burst)
pend = next((i for i in a["items"] if i["code"] == "pending_fail"), None)
check("replay 01/13: 4 failed units to handle = the Fail List's pending units of that day", pend and pend["values"]["n"]
      == get("/api/fail-list", date_from="2026-01-12", date_to="2026-01-13", disposition="pending", page_size=200)["total"], pend)
order = [i["level"] for i in a["items"]]
check("items ordered red → amber → info", order == sorted(order, key=["bad", "warn", "info"].index), order)
a = get("/api/alerts", now="2026-10-01T17:00:00")
check("replay 10/01 17:00 (a normal day): no red, no burst", not any(i["code"] in ("wo_red", "burst") for i in a["items"]), [i["code"] for i in a["items"]])

# ── Ask the dashboard ──
llm = get("/api/meta").get("llm", {}).get("connected")


def ask(question, lang="zh"):
    for attempt in range(6):            # 503: the LLM is busy; 429: 6 questions a minute
        status, body = call("POST", "/api/ask", {"question": question, "lang": lang})
        if status not in (503, 429):
            return status, body
        time.sleep(15)
    return status, body


if llm:
    status, r = ask("上個月測了幾台？")
    ov = get("/api/overview", date_from=r.get("from"), date_to=r.get("to")) if status == 200 else {}
    check("'how many units last month' → units, September, = the Trend page's September", status == 200 and r["supported"]
          and r["query"]["metric"] == "units" and r["from"] == "2026-09-01" and r["value"] == ov["summary"]["total"] == 4734, (status, r.get("query")))
    status, r = ask("Yield of TN-86_PICO-IMX7 in September 2026", "en")
    ov = get("/api/overview", date_from="2026-09-01", date_to="2026-09-30", product_model="TN-86_PICO-IMX7")
    check("product yield in a month = the Trend page's number, English answer", status == 200 and r["query"]["product"] == "TN-86_PICO-IMX7"
          and r["value"] == ov["summary"]["yield_pct"] and "yield" in r["answer"]["en"] and not re.search(r"[一-鿿]", r["answer"]["en"]),
          (r.get("query"), r.get("value"), ov["summary"]["yield_pct"]))
    status, r = ask("今年每個月的測試台數")
    months = {m["month"]: m["total"] for m in get("/api/overview")["monthly"]["months"]}
    rows = {int(x["key"][5:7]): int(x["value"]) for x in r.get("rows") or []}
    check("'units per month this year' → a split by month equal to the Trend page's monthly chart",
          status == 200 and r["query"]["group_by"] == "month" and rows == {k: v for k, v in months.items() if v}, (rows, months))
    status, r = ask("最近 30 天主要的失敗現象")
    check("'main failures in 30 days' → categories from the classification", status == 200 and r["query"]["metric"] == "fail_categories"
          and isinstance(r.get("categories"), list), r.get("query"))
    status, r = ask("SN 513126333154 的 MAC 位址是什麼？")
    check("a question about one unit's SN / MAC is refused (aggregates only)", status == 200 and r["supported"] is False
          and "513126333154" not in json.dumps(r.get("answer") or {}), r)
    status, r = ask("最新工單的 5G 平均吞吐量")
    latest = get("/api/filter-options")["latest_work_order"]
    ov = get("/api/overview", work_order=latest)
    check("'5G average of the latest work order' → that work order, = the Trend page's 5G average", status == 200
          and r["query"]["work_order"] == latest and r["value"] == ov["summary"]["avg_5g"], (r.get("query"), r.get("value"), ov["summary"]["avg_5g"]))
    status, r = ask("最近TN410 的每小時平均產出")
    tn410 = next(w["work_order"] for w in get("/api/filter-options")["work_orders"] if w["product_model"].startswith("TN-410"))
    uph = next(x["uph"] for x in get("/api/wo-status")["items"] if x["work_order"] == tn410)
    check("'recent TN410 output per hour' → 台/時 of TN-410's newest work order, = the Work Orders page", status == 200
          and r["query"]["metric"] == "uph" and r["query"]["work_order"] == tn410 and r["value"] == round(uph, 1)
          and "台/時" in r["answer"]["zh"], (r.get("query"), r.get("value"), tn410, uph))
else:
    print("  (LLM offline: Ask the dashboard not checked)")
status, body = call("POST", "/api/ask", {"question": "", "lang": "zh"})
check("an empty question → 422", status == 422, status)
status, body = call("POST", "/api/ask", {"question": "x" * 301})
check("a question over 300 characters → 422", status == 422, status)

# ── weekly / monthly reports ──
admin, up = login(*ADMIN), login(*UPLOADER)
lst = get("/api/period-reports")["items"]
check("reports listed (the schedule wrote the last finished week and month)", any(x["kind"] == "week" for x in lst) and any(x["kind"] == "month" for x in lst),
      [(x["kind"], x["period_start"]) for x in lst])
status, r = call("POST", "/api/period-reports/generate", {"kind": "month", "start": "2026-09-15", "use_llm": False}, admin)
check("administrator writes September by rules (any day of the month → 09-01 ~ 09-30)", status == 200 and r["period_start"] == "2026-09-01"
      and r["period_end"] == "2026-09-30" and r["source"] == "rule", (status, r.get("period_start") if isinstance(r, dict) else r))
f = r["facts"]
ov = get("/api/overview", date_from="2026-09-01", date_to="2026-09-30")
check("report numbers = the Trend page's September (units, yield)", f["totals"]["units"] == ov["summary"]["total"] and f["totals"]["yield_pct"] == ov["summary"]["yield_pct"])
pv = get("/api/overview", date_from="2026-08-01", date_to="2026-08-31")
check("the period before = August, change = difference", f["previous"]["units"] == pv["summary"]["total"] and f["change"]["units"] == f["totals"]["units"] - f["previous"]["units"])
check("work orders of the month add up to the month's units", sum(w["units"] for w in f["work_orders"]) == f["totals"]["units"])
check("a work order with under 10 units in the month has no yield (not '0%')", all(w["yield_pct"] is None for w in f["work_orders"] if w["units"] < 10)
      and any(w["units"] < 10 for w in f["work_orders"]))
status, md = call("GET", "/api/period-reports/month/2026-09-01.md?lang=zh", raw=True)
md = md.decode("utf-8") if isinstance(md, bytes) else ""
check("Markdown (中文): title, numbers table, work orders, failures", status == 200 and md.startswith("# 月報 2026-09-01 ~ 2026-09-30")
      and "| 測試台數 | 4,734 |" in md and "## 工單" in md and "## FAIL 現象" in md, md[:120])
status, md = call("GET", "/api/period-reports/month/2026-09-01.md?lang=en", raw=True)
check("Markdown (English) has no Chinese", status == 200 and not re.search(r"[一-鿿]", md.decode("utf-8")))
status, body = call("POST", "/api/period-reports/generate", {"kind": "month", "start": "2026-09-01", "use_llm": False}, up)
check("an uploader cannot write reports → 403", status == 403, status)
status, body = call("POST", "/api/period-reports/generate", {"kind": "month", "start": "2026-09-01", "use_llm": False})
check("signed out → 401", status == 401, status)
status, body = call("GET", "/api/period-reports/year/2026-01-01")
check("an unknown kind → 400", status == 400, status)
status, body = call("GET", "/api/period-reports/week/2020-01-06")
check("a report not written → 404", status == 404, status)
if llm:
    status, r = call("POST", "/api/period-reports/generate", {"kind": "month", "start": "2026-09-01", "use_llm": True}, admin)
    ok = status == 200 and r["source"] in ("llm", "rule")
    check("with the AI: a summary in both languages (or the rule summary when it fails), dropped sentences recorded",
          ok and r["summary"]["zh"] and r["summary"]["en"] and isinstance(r["rejected"], list), (status, r.get("source") if isinstance(r, dict) else r))
    if ok:
        print("     zh:", r["summary"]["zh"])
        print("     dropped:", r["rejected"])

print(f"\n{sum(results)}/{len(results)} Phase 5E checks passed")
sys.exit(0 if all(results) else 1)
