#!/usr/bin/env python3
"""Phase 5F-1 acceptance (API): failure taxonomy, classifications of the 223 FAIL runs, retest outcomes,
stations, FAIL list / work-order detail / final-report facts, and the RD role.

    python3 tests/acceptance_phase5f1.py [BASE] [admin_user admin_pass]

Creates (or re-enables) the account `test_rd` with role rd; switches its role once and back.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlencode

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ADMIN = (sys.argv[2], sys.argv[3]) if len(sys.argv) > 3 else (os.environ.get("TWEAK_USER", "qc"), os.environ.get("TWEAK_PASS", "qcpass"))
RD_USER, RD_PASS = "test_rd", "rd-account-pass-2026"
RED = ("5101-260102014", "TN-86_PICO-IMX7")
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
        with urllib.request.urlopen(req, timeout=120) as r:
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
    return (body.get("token"), body.get("role")) if status == 200 else (None, None)


# ── taxonomy ──
tax = call("GET", "/api/fail-taxonomy")[1]
codes = [t["code"] for t in tax["items"]]
check("taxonomy: 8 codes seeded (MULTI is category + secondary, not a code)",
      set(codes) == {"WIFI_TP_LOW_24G", "WIFI_TP_LOW_5G", "WIFI_NO_TP_DATA", "WIFI_ASSOC_FAIL", "BT_PING_FAIL", "BT_DEV_NOT_FOUND",
                     "LOG_INCOMPLETE", "UNKNOWN"}, codes)
check("every code has Chinese and English names, a description, a suggested action and a likely stage",
      all(t["name_zh"] and t["name_en"] and t["description_zh"] and t["action_zh"] and t["action_en"] and t["likely_stage"] for t in tax["items"]))
check("7 stages for RD to confirm (board, antenna, fixture, AP, firmware, station, unknown)", len(tax["stages"]) == 7)

# ── the 223 FAIL runs ──
s = call("GET", "/api/fail-summary")[1]
by = {c["category"]: c for c in s["categories"]}
check("all 223 FAIL runs are classified", s["total_runs"] == 223, s["total_runs"])
expected = {"WIFI_TP_LOW_24G": 185, "WIFI_TP_LOW_5G": 14, "BT_DEV_NOT_FOUND": 7, "BT_PING_FAIL": 7, "WIFI_ASSOC_FAIL": 6, "LOG_INCOMPLETE": 4}
check("categories on the production copy", {k: v["runs"] for k, v in by.items()} == expected, {k: v["runs"] for k, v in by.items()})
check("retest outcomes add up per category", all(c["pass_on_retest"] + c["failed_again"] + c["not_retested"] == c["runs"] for c in s["categories"]))
check("180 of the 185 2.4G throughput failures passed when retested", by["WIFI_TP_LOW_24G"]["pass_on_retest"] == 180, by["WIFI_TP_LOW_24G"])
check("15 runs not retested = the 15 units still failing", s["retest"].get("not_retested") == 15, s["retest"])
check("stations are listed (SSID group + port)", s["stations"] and s["stations"][0]["ssid_group"] and s["stations"][0]["station_port"])
scoped = call("GET", "/api/fail-summary" + q(work_order=RED[0], product_model=RED[1]))[1]
check("summary for one work order (5101-260102014: 74 FAIL runs, 63 passed on retest)",
      scoped["total_runs"] == 74 and scoped["retest"].get("pass_on_retest") == 63, (scoped["total_runs"], scoped["retest"]))
dated = call("GET", "/api/fail-summary" + q(date_from="2026-01-01", date_to="2026-01-31"))[1]
check("summary for a date range", 0 < dated["total_runs"] < 223, dated["total_runs"])

# ── FAIL list, one analysis, detail, report facts ──
fl = call("GET", "/api/fail-list?page_size=50")[1]
check("FAIL list: every failed unit carries its classification", fl["items"] and all(r["analysis"] for r in fl["items"]), len(fl["items"]))
a = fl["items"][0]["analysis"]
check("… with category names, run sequence, retest outcome and station",
      a["name_zh"] and a["name_en"] and a["run_seq"] and a["retest_outcome"] == "not_retested" and (a["ssid_group"] or a["station_port"]), a)
one = call("GET", f"/api/fail-analysis/{fl['items'][0]['id']}")[1]
check("one FAIL run: evidence lines with line numbers, measurements per band",
      one["evidence"] and all("n" in e and "text" in e for e in one["evidence"]) and "bands" in one["measures"], one["evidence"][:1])
check("unknown record → 404", call("GET", "/api/fail-analysis/1")[0] == 404)
d = call("GET", "/api/wo-detail" + q(work_order=RED[0], product_model=RED[1]))[1]
check("detail: failing units by category (7 units: BT device not found 5, BT ping 2)",
      {r["code"]: r["units"] for r in d["extra"]["fail_reasons"]} == {"BT_DEV_NOT_FOUND": 5, "BT_PING_FAIL": 2}, d["extra"]["fail_reasons"])
check("detail: FAIL runs by category with retest outcomes", d["extra"]["fail_runs"]["total_runs"] == 74)
rf = call("GET", "/api/wo-report" + q(work_order=RED[0], product_model=RED[1]))[1]["facts"]
check("final-report facts use the categories (runs, passed on retest) and the stations",
      rf["fail_runs_by_category"] and rf["fail_runs_by_category"][0]["passed_on_retest"] >= 0 and rf["fail_stations"]
      and rf["failing_units_by_category"], rf["fail_runs_by_category"][:2])

# ── date ranges (regression: one shared Query() object had merged date_from and date_to) ──
spec = call("GET", "/openapi.json")[1]
ranged = ["/api/overview", "/api/work-order-summary", "/api/fail-list", "/api/rf", "/api/fail-summary"]
check("every date-filtered endpoint declares both date_from and date_to",
      all({"date_from", "date_to"} <= {x["name"] for x in spec["paths"][path]["get"]["parameters"]} for path in ranged))
sept = call("GET", "/api/overview" + q(date_from="2026-09-01", date_to="2026-09-30"))[1]
day1 = call("GET", "/api/overview" + q(date_from="2026-09-01", date_to="2026-09-01"))[1]
check("a September range covers the whole month (4,734 units), not just September 1 (540)",
      sept["summary"]["total"] == 4734 and day1["summary"]["total"] == 540 and sept["window"]["date_to"] == "2026-09-30",
      (sept["summary"]["total"], day1["summary"]["total"]))
wos = call("GET", "/api/work-order-summary" + q(date_from="2026-09-01", date_to="2026-09-30"))[1]
check("Work Orders for September lists more than September 1's work orders",
      len(wos) > len(call("GET", "/api/work-order-summary" + q(date_from="2026-09-01", date_to="2026-09-01"))[1]))

# ── RD role ──
admin, _ = login(*ADMIN)
users = {u["username"]: u for u in call("GET", "/api/admin/users", token=admin)[1]["items"]}
if RD_USER not in users:
    status, r = call("POST", "/api/admin/users", {"username": RD_USER, "display_name": "Test RD", "department": "RD",
                                                   "role": "rd", "password": RD_PASS}, admin)
    check("administrator creates an RD account", status == 200, r)
else:
    call("PATCH", f"/api/admin/users/{RD_USER}", {"role": "rd", "enabled": True}, admin)
    call("POST", f"/api/admin/users/{RD_USER}/reset-password", {"password": RD_PASS}, admin)
users = {u["username"]: u for u in call("GET", "/api/admin/users", token=admin)[1]["items"]}
check("the account list shows the role", users[RD_USER]["role"] == "rd")
check("a bad role is refused (422)", call("POST", "/api/admin/users", {"username": "x_bad_role", "display_name": "x", "role": "boss",
                                                                       "password": RD_PASS}, admin)[0] == 422)
rd, role = login(RD_USER, RD_PASS)
check("RD signs in with role rd", rd and role == "rd", role)
check("/api/auth/me says rd", call("GET", "/api/auth/me", token=rd)[1]["role"] == "rd")
check("RD cannot start a log upload (403)", call("POST", "/api/log-upload/folder/start", {"name": "x", "total_files": 1}, rd)[0] == 403)
check("RD cannot see upload history (403)", call("GET", "/api/log-upload/history", token=rd)[0] == 403)
check("RD cannot add a product (403)", call("POST", "/api/products", {"name": "TN-997_RD-TEST"}, rd)[0] == 403)
check("RD reads the product list", call("GET", "/api/products/list", token=rd)[0] == 200)
check("RD reads every upload batch of a work order", call("GET", "/api/wo-batches" + q(work_order="5101-251216015"), token=rd)[0] == 200)
check("RD is not an administrator (accounts 403, settings 403)",
      call("GET", "/api/admin/users", token=rd)[0] == 403 and call("PUT", "/api/settings", {"fail_streak": 3}, rd)[0] == 403)
status, r = call("PATCH", f"/api/admin/users/{RD_USER}", {"role": "uploader"}, admin)
check("changing the role signs the account out", status == 200 and r["role"] == "uploader" and call("GET", "/api/auth/me", token=rd)[0] == 401)
up, role = login(RD_USER, RD_PASS)
check("… and the next sign-in carries the new role", role == "uploader")
call("PATCH", f"/api/admin/users/{RD_USER}", {"role": "rd"}, admin)
audit = [x for x in call("GET", f"/api/admin/audit?username={RD_USER}&limit=5", token=admin)[1]["items"] if x["action"] == "role"]
check("role changes are in the audit", len(audit) >= 2 and "→" in audit[0]["detail"]["role"], audit[:1])

passed = sum(results)
print(f"\n{passed}/{len(results)} Phase 5F-1 checks passed")
sys.exit(0 if passed == len(results) else 1)
