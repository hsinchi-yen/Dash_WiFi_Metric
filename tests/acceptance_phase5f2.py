#!/usr/bin/env python3
"""Phase 5F-2 acceptance (API): the RD advanced-analysis window — access (RD and the administrator only), filters,
Pareto and cross tables that add up, batch triage (validation, audit, the rules leave it alone, undo), the RD
instruction written back to the line's pages (Fail List next step, work-order note), unit history, log viewer,
similar cases, CSV / log export, Ask AI (numbers only from the counts, no SN / MAC), taxonomy add / edit / merge /
delete, and three real triage questions answered from the production copy.

    python3 tests/acceptance_phase5f2.py [BASE] [admin_user admin_pass]

Needs test_rd (acceptance_phase5f1.py) and test_uploader (acceptance_upload.py). Everything it changes is put back:
triage through the reset endpoint (the rules classify the runs again), the test category is deleted.
"""
import csv
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from urllib.parse import urlencode

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ADMIN = (sys.argv[2], sys.argv[3]) if len(sys.argv) > 3 else (os.environ.get("TWEAK_USER", "qc"), os.environ.get("TWEAK_PASS", "qcpass"))
RD = ("test_rd", "rd-account-pass-2026")
UPLOADER = ("test_uploader", "uploader-pass-2026")
TEST_CODE = "TEST_5F2_CODE"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""), flush=True)


def call(method, path, body=None, token=None, raw=False):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Tweak-Token"] = token
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(), method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            data = r.read()
            return (r.status, data, dict(r.headers)) if raw else (r.status, json.loads(data or b"null"))
    except urllib.error.HTTPError as e:
        data = e.read()
        if raw:
            return e.code, data, dict(e.headers)
        try:
            return e.code, json.loads(data or b"null")
        except ValueError:
            return e.code, None


def login(user, password):
    status, body = call("POST", "/api/auth/login", {"username": user, "password": password})
    return body.get("token") if status == 200 else None


def q(**kw):
    return "?" + urlencode({k: v for k, v in kw.items() if v is not None}) if kw else ""


rd = login(*RD)
admin = login(*ADMIN)
up = login(*UPLOADER)
check("RD, administrator and uploader sign in", rd and admin and up)


def get(path, token=None, **kw):
    status, body = call("GET", path + q(**kw), token=token or rd)
    assert status == 200, (path, kw, status, body)
    return body


# ── access ──
for path in ("/api/rd/fails", "/api/rd/group", "/api/rd/crosstab", "/api/rd/sn/513126333154", "/api/rd/export.csv"):
    s0 = call("GET", path, raw=True)[0]
    s1 = call("GET", path, token=up, raw=True)[0]
    s2 = call("GET", path, token=admin, raw=True)[0]
    check(f"{path}: signed out 401, uploader 403, administrator 200", (s0, s1, s2) == (401, 403, 200), (s0, s1, s2))

# ── listing and filters ──
all_ = get("/api/rd/fails", page_size=500)
check("all 223 FAIL runs, newest first, each with category, station, retest outcome and RD status",
      all_["total"] == 223 and len(all_["items"]) == 223 and all_["items"][0]["test_time"] >= all_["items"][-1]["test_time"]
      and all(r["category"] and r["rd_status"] for r in all_["items"]))
check("facets: the category counts add up to 223", sum(x["n"] for x in all_["facets"]["category"]) == 223)
n24 = get("/api/rd/fails", category="WIFI_TP_LOW_24G")["total"]
check("category filter: 185 runs below the 2.4G threshold", n24 == 185, n24)
check("two categories at once (comma-separated): 185 + 14", get("/api/rd/fails", category="WIFI_TP_LOW_24G,WIFI_TP_LOW_5G")["total"] == 199)
check("still failing units only: 15 runs", get("/api/rd/fails", still_failing=1)["total"] == 15)
check("retest outcome filter: 204 passed on retest", get("/api/rd/fails", retest="pass_on_retest")["total"] == 204)
low = get("/api/rd/fails", category="WIFI_TP_LOW_24G", mbps_max=5, page_size=500)
check("throughput range: every run ≤ 5 Mbps", low["total"] > 0 and all(r["mbps"] <= 5 for r in low["items"]), low["total"])
rssi = get("/api/rd/fails", rssi_max=-40, page_size=500)
check("RSSI range: every run ≤ −40 dBm", rssi["total"] > 0 and all(r["rssi"] <= -40 for r in rssi["items"]), rssi["total"])
grp = get("/api/rd/fails", ssid_group="grpb", page_size=500)
check("SSID group filter", grp["total"] > 0 and all(r["ssid_group"] == "grpb" for r in grp["items"]), grp["total"])
srch = get("/api/rd/fails", search="513126333154")
check("search by SN", srch["total"] == 1 and srch["items"][0]["sn"] == "513126333154")
mac = srch["items"][0]["wifi_mac"]
check("search by MAC", get("/api/rd/fails", search=mac)["total"] >= 1)
day = get("/api/rd/fails", date_from="2026-01-13", date_to="2026-01-13")
check("date range: 01/13 = 44 FAIL runs", day["total"] == 44, day["total"])
wo = get("/api/rd/fails", work_order="5101-260102014")
check("work order filter: 74 FAIL runs of 5101-260102014", wo["total"] == 74, wo["total"])
by_mbps = get("/api/rd/fails", category="WIFI_TP_LOW_24G", sort="mbps", page_size=20)["items"]
check("sort by throughput: lowest first", [r["mbps"] for r in by_mbps] == sorted(r["mbps"] for r in by_mbps))
check("a bad filter value is ignored, not an error", get("/api/rd/fails", mbps_min="abc", date_from="13/01/2026")["total"] == 223)

# ── Pareto and cross table ──
g = get("/api/rd/group", by="category")
check("Pareto by category: 223 runs, largest first, cumulative % ends at 100", g["total"] == 223
      and [i["runs"] for i in g["items"]] == sorted((i["runs"] for i in g["items"]), reverse=True) and g["items"][-1]["cum_pct"] == 100.0)
check("Pareto: 2.4G low = 185 runs, 180 passed on retest", g["items"][0]["key"] == "WIFI_TP_LOW_24G" and g["items"][0]["runs"] == 185
      and g["items"][0]["passed_retest"] == 180, g["items"][0])
gh = get("/api/rd/group", by="hour")
check("by hour of day: in time order and adds up", [i["key"] for i in gh["items"]] == sorted(i["key"] for i in gh["items"]) and gh["total"] == 223)
gs = get("/api/rd/group", by="station")
check("by station: the largest is grpb · USB1 (35 runs, 31 units)", gs["items"][0] == {**gs["items"][0], "key": "grpb · USB1", "runs": 35, "units": 31}, gs["items"][0])
check("an unknown dimension falls back to category", get("/api/rd/group", by="drop table")["by"] == "category")
x = get("/api/rd/crosstab", rows="category", cols="station")
check("cross table category × station: rows add up to the Pareto, columns + other = 223",
      x["total"] == 223 and {r["key"]: r["total"] for r in x["rows"]} == {i["key"]: i["runs"] for i in g["items"]}
      and sum(x["col_totals"]) + x["other_total"] == 223)
xh = get("/api/rd/crosstab", rows="category", cols="hour", top=24)
check("cross table by hour: columns in time order", xh["cols"] == sorted(xh["cols"]))

# ── three real triage questions (the plan's 5F-2 acceptance) ──
q1 = get("/api/rd/crosstab", rows="category", cols="ssid_group", category="WIFI_TP_LOW_24G")
spread = dict(zip(q1["cols"], q1["rows"][0]["cells"]))
print(f"  Q1 Are the 2.4G-low FAILs one station's problem? by SSID group: {spread}; "
      f"{g['items'][0]['passed_retest']} of {g['items'][0]['runs']} passed on retest")
check("Q1: 2.4G low is spread over both SSID groups (not one AP), and 97% pass on retest → environment / margin, not boards",
      len([v for v in spread.values() if v >= 20]) >= 2 and g["items"][0]["passed_retest"] / g["items"][0]["runs"] > 0.95)
q2 = get("/api/rd/fails", category="BT_DEV_NOT_FOUND", page_size=50)["items"]
burst = [r for r in q2 if r["test_time"].startswith("2026-01-07T14:2")]
print(f"  Q2 BT device not found: {len(q2)} runs; {len(burst)} within 3 minutes on 01/07 14:26–14:28 on "
      f"{len({(r['ssid_group'], r['station_port']) for r in burst})} different ports")
check("Q2: 4 of the 7 'BT not found' came within 3 minutes on four ports → the station's BT setup, not four bad boards",
      len(q2) == 7 and len(burst) == 4 and len({(r['ssid_group'], r['station_port']) for r in burst}) == 4)
q3 = get("/api/rd/group", by="product", category="WIFI_TP_LOW_5G")
print(f"  Q3 5G low by product: {[(i['key'], i['runs']) for i in q3['items']]}")
check("Q3: 5G-low FAILs by product answer which product to look at first", q3["total"] == 14 and q3["items"][0]["runs"] >= 7, q3["items"][:2])

# ── triage: validation ──
target = srch["items"][0]                       # 513126333154: 2.4G low, still failing
rid = target["record_id"]
for body, code, label in (({"ids": [rid], "changes": {"category": "NOPE"}}, "bad_category", "unknown category"),
                          ({"ids": [rid], "changes": {"stage": "moon"}}, "bad_stage", "unknown stage"),
                          ({"ids": [rid], "changes": {"rd_status": "done"}}, "bad_status", "unknown RD status"),
                          ({"ids": [rid], "changes": {"rd_note": "x" * 501}}, "bad_rd_note", "a 501-character note"),
                          ({"ids": [rid], "changes": {}}, "no_change", "no change")):
    status, data = call("PATCH", "/api/rd/fails", body, rd)
    check(f"triage: {label} → 400 {code}", status == 400 and data.get("code") == code, (status, data and data.get("code")))
status, data = call("PATCH", "/api/rd/fails", {"ids": [rid], "changes": {"severity": 1}}, rd)
check("triage: a field that is not RD's → 422", status == 422, status)
status, data = call("PATCH", "/api/rd/fails", {"ids": [999999999], "changes": {"rd_status": "confirmed"}}, rd)
check("triage: an unknown FAIL run → 404", status == 404, status)
status, data = call("PATCH", "/api/rd/fails", {"ids": [rid], "changes": {"rd_status": "confirmed"}}, up)
check("triage by an uploader → 403", status == 403, status)

# ── triage: one run, written back to the line ──
status, data = call("PATCH", "/api/rd/fails", {"ids": [rid], "changes": {"stage": "antenna", "rd_status": "confirmed", "rd_owner": "RD-A",
                                                                       "rd_note": "antenna cable loose on fixture 3",
                                                                       "rd_action": "Reseat the antenna cable, then retest"}}, rd)
check("RD confirms one run with stage, owner, note and an instruction for the line", status == 200 and data["updated"] == 1, (status, data))
row = get("/api/rd/fails", search="513126333154")["items"][0]
check("the run shows it (classifier unchanged: the category was not changed)", row["stage"] == "antenna" and row["rd_status"] == "confirmed"
      and row["rd_by"] == RD[0] and row["classifier"] == "rule", row)
fl = call("GET", "/api/fail-list" + q(search="513126333154"))[1]["items"][0]
check("Fail List (public): the next step is RD's instruction", fl["suggest"] == {"code": "rd", "values": {"text": "Reseat the antenna cable, then retest", "by": RD[0]}},
      fl["suggest"])
wd = call("GET", "/api/wo-detail" + q(work_order=target["work_order"], product_model=target["product_model"]))[1]
check("work-order detail: the status note carries RD's instruction", (wd["status"]["note"].get("rd") or {}).get("text") == "Reseat the antenna cable, then retest",
      wd["status"]["note"].get("rd"))
hist = get(f"/api/rd/history/{rid}")["items"]
check("history: one audited RD change with before → after per field", hist and hist[0]["action"] == "fail_rd"
      and hist[0]["detail"]["rd_status"] == "new → confirmed" and hist[0]["detail"]["stage"] == "— → antenna", hist[:1])

# ── triage: reclassify a batch; the rules leave it alone; undo ──
bt = [r["record_id"] for r in burst]
status, data = call("PATCH", "/api/rd/fails", {"ids": bt, "changes": {"category": "LOG_INCOMPLETE", "stage": "station",
                                                                      "rd_note": "BT dongle of the station dropped at 14:26"}}, rd)
check("batch: the 4 'BT not found' of the burst reclassified at once", status == 200 and data["updated"] == 4, (status, data))
moved = get("/api/rd/fails", category="LOG_INCOMPLETE", stage="station", page_size=50)["items"]
check("they are RD-classified, 'reclassified', stage station", len(moved) == 4 and all(r["classifier"] == "rd" and r["rd_status"] == "reclassified" for r in moved))
print("  waiting 70 s for the FAIL scan (the rules must not undo RD's decision)…", flush=True)
time.sleep(70)
check("after a scan RD's classification stays", get("/api/rd/fails", category="LOG_INCOMPLETE", stage="station")["total"] == 4)
check("Pareto follows: BT not found 3, log incomplete 8", {i["key"]: i["runs"] for i in get("/api/rd/group")["items"]}.get("BT_DEV_NOT_FOUND") == 3)

# ── taxonomy ──
status, data = call("POST", "/api/fail-taxonomy", {"code": "bad code"}, rd)
check("taxonomy: a malformed code → 400", status == 400 and data["code"] == "bad_code", data and data.get("code"))
status, data = call("POST", "/api/fail-taxonomy", {"code": TEST_CODE, "name_zh": "測試分類", "name_en": "Test category",
                                                   "action_zh": "交 RD。", "action_en": "Hand it to RD.", "likely_stage": "firmware", "severity": 5}, rd)
check("taxonomy: RD adds a code", status == 200 and any(x["code"] == TEST_CODE for x in data["items"]), status)
status, data = call("POST", "/api/fail-taxonomy", {"code": TEST_CODE, "name_zh": "x", "name_en": "x"}, rd)
check("taxonomy: the same code again → 409", status == 409, status)
status, data = call("PATCH", f"/api/fail-taxonomy/{TEST_CODE}", {"name_en": "Test category (renamed)"}, rd)
check("taxonomy: rename", status == 200 and next(x for x in data["items"] if x["code"] == TEST_CODE)["name_en"] == "Test category (renamed)")
status, data = call("PATCH", "/api/fail-taxonomy/UNKNOWN", {"enabled": False}, rd)
check("taxonomy: Unclassified cannot be disabled", status == 400, status)
status, data = call("DELETE", "/api/fail-taxonomy/WIFI_TP_LOW_24G", token=rd)
check("taxonomy: a built-in code cannot be deleted", status == 400 and data["code"] == "seeded", status)
status, data = call("PATCH", "/api/rd/fails", {"ids": [bt[0]], "changes": {"category": TEST_CODE}}, rd)
check("one run moved into the new code", status == 200)
status, data = call("DELETE", f"/api/fail-taxonomy/{TEST_CODE}", token=rd)
check("taxonomy: a code in use cannot be deleted (409)", status == 409 and data["code"] == "in_use", status)
status, data = call("POST", f"/api/fail-taxonomy/{TEST_CODE}/merge", {"into": "LOG_INCOMPLETE"}, rd)
check("taxonomy: merge the new code into 'log incomplete': 1 run moved, the code disabled and marked merged",
      status == 200 and data["moved"] == 1 and next(x for x in data["items"] if x["code"] == TEST_CODE)["merged_into"] == "LOG_INCOMPLETE"
      and not next(x for x in data["items"] if x["code"] == TEST_CODE)["enabled"], (status, data and data.get("moved")))
status, data = call("POST", "/api/fail-taxonomy/LOG_INCOMPLETE/merge", {"into": TEST_CODE}, rd)
check("taxonomy: cannot merge into a disabled code", status == 400 and data["code"] == "disabled_target", status)
status, data = call("DELETE", f"/api/fail-taxonomy/{TEST_CODE}", token=rd)
check("taxonomy: the now unused test code is deleted", status == 200 and all(x["code"] != TEST_CODE for x in data["items"]), status)
status, data = call("PATCH", "/api/fail-taxonomy/BT_PING_FAIL", {"severity": 0}, up)
check("taxonomy changes by an uploader → 403", status == 403, status)

# ── undo the triage ──
status, data = call("POST", "/api/rd/fails/reset", {"ids": [rid, *bt]}, rd)
check("reset: back to the rules' classification (5 runs)", status == 200 and data["reset"] == 5, (status, data))
back = get("/api/rd/group")
check("the rules classified them again at once: the Pareto is as before", {i["key"]: i["runs"] for i in back["items"]} == {i["key"]: i["runs"] for i in g["items"]},
      {i["key"]: i["runs"] for i in back["items"]})
row = get("/api/rd/fails", search="513126333154")["items"][0]
check("RD fields cleared, the line's next step is the rule's again", row["rd_status"] == "new" and row["stage"] is None and row["rd_action"] is None
      and call("GET", "/api/fail-list" + q(search="513126333154"))[1]["items"][0]["suggest"]["code"] != "rd")
check("nothing RD-classified is left", get("/api/rd/fails", classifier="rd")["total"] == 0 and get("/api/rd/fails", rd_status="confirmed,reclassified,closed")["total"] == 0)

# ── unit history, log viewer, similar cases ──
seq = next(r for r in all_["items"] if r["run_seq"] == "FP")
h = get(f"/api/rd/sn/{seq['sn']}")
check("unit history: FAIL then PASS with MAC, station and file name", [r["result"] for r in h["runs"]] == [False, True]
      and h["runs"][0]["wifi_mac"] and h["runs"][0]["source_file"], [(r["result"], r["source_file"]) for r in h["runs"]])
status, _ = call("GET", "/api/rd/sn/000000000000", token=rd)
check("unit history of an unknown SN → 404", status == 404)
lv = get(f"/api/rd/log/{rid}")
check("log viewer: every line, evidence line numbers inside the log and among the signal lines",
      len(lv["lines"]) > 500 and lv["evidence_lines"] and all(1 <= n <= len(lv["lines"]) for n in lv["evidence_lines"])
      and set(lv["evidence_lines"]) <= set(lv["signal_lines"]), (len(lv["lines"]), lv["evidence_lines"]))
check("log viewer: the evidence lines say FAILED", all("FAILED" in lv["lines"][n - 1] for n in lv["evidence_lines"]))
sim = get(f"/api/rd/similar/{bt[0]}")
check("similar cases: the same signature finds the other 'BT device not found' runs", sim["same_signature_total"] >= 5
      and all(r["category"] == "BT_DEV_NOT_FOUND" for r in sim["same_signature"]), (sim["signature"], sim["same_signature_total"]))

# ── export ──
status, data, headers = call("GET", "/api/rd/export.csv" + q(category="WIFI_TP_LOW_5G"), token=rd, raw=True)
text = data.decode("utf-8")
rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
check("CSV export: BOM for Excel, 14 rows of the filter, evidence quoted", status == 200 and text.startswith("﻿") and len(rows) == 14
      and all(r["category"] == "WIFI_TP_LOW_5G" and r["evidence"] for r in rows), (status, len(rows)))
status, data, headers = call("GET", "/api/rd/export-logs.zip" + q(category="LOG_INCOMPLETE"), token=rd, raw=True)
z = zipfile.ZipFile(io.BytesIO(data))
check("log export: a zip with the 4 'log incomplete' raw logs in a category folder", status == 200 and len(z.namelist()) == 4
      and all(n.startswith("LOG_INCOMPLETE/") for n in z.namelist()) and {k.lower(): v for k, v in headers.items()}.get("x-total") == "4",
      (status, z.namelist()[:2]))

# ── Ask AI (L2b) ──
llm = call("GET", "/api/meta")[1].get("llm", {}).get("connected")
if llm:
    status, data = call("POST", "/api/rd/ask", {"filters": {"category": "BT_DEV_NOT_FOUND"}, "lang": "en"}, rd)
    if status == 503:
        print("  (the shared LLM was busy; retrying once in 20 s)", flush=True)
        time.sleep(20)
        status, data = call("POST", "/api/rd/ask", {"filters": {"category": "BT_DEV_NOT_FOUND"}, "lang": "en"}, rd)
    ans = (data or {}).get("answer") or {}
    check("Ask AI on the 7 'BT not found' runs: a summary and next checks", status == 200 and ans.get("summary") and ans.get("checks"), (status, ans))
    blob = json.dumps(data.get("facts"))
    check("the AI saw aggregates only: no SN (12 digits) and no MAC in what it was given", not re.search(r"\b\d{12}\b", blob)
          and not re.search(r"001F7B[0-9A-F]{6}", blob) and data["facts"]["fail_runs"] == 7)
    texts = [ans.get("summary") or ""] + ans.get("common", []) + ans.get("causes", []) + ans.get("checks", [])
    check("its sentences use category names, not codes", not any(re.search(r"\b[A-Z]{2,}_[A-Z0-9_]+\b", s) for s in texts), texts)
    print("  answer:", json.dumps(ans, ensure_ascii=False)[:600])
    status, data = call("POST", "/api/rd/ask", {"filters": {"category": "BT_DEV_NOT_FOUND"}}, up)
    check("Ask AI by an uploader → 403", status == 403, status)
else:
    print("  (LLM offline: Ask AI not checked)")

print(f"\n{sum(results)}/{len(results)} Phase 5F-2 checks passed")
sys.exit(0 if all(results) else 1)
