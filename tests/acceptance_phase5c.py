#!/usr/bin/env python3
"""Phase 5C acceptance (API): the FAIL disposition (permissions, validation, automatic states, counts, audit history),
the suggested next steps of the units still failing, and the working days in Settings. (The Today page of 5C was
removed on 2026-10-21; the Trend page's Forecast view replaced it: tests/acceptance_forecast.py.)

    python3 tests/acceptance_phase5c.py [BASE] [admin_user admin_pass]

Needs the account test_rd (tests/acceptance_phase5f1.py creates it). Reads two record ids straight from the
database container (PSQL_CONTAINER, default wifitest-db). Every disposition it sets is put back to "open"
(no row), and the Settings value it changes is restored.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ADMIN = (sys.argv[2], sys.argv[3]) if len(sys.argv) > 3 else (os.environ.get("TWEAK_USER", "qc"), os.environ.get("TWEAK_PASS", "qcpass"))
RD = ("test_rd", "rd-account-pass-2026")
PSQL = os.environ.get("PSQL_CONTAINER", "wifitest-db")
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""), flush=True)


def call(method, path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Tweak-Token"] = token
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(), method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except ValueError:
            return e.code, None


def get(path, **kw):
    status, body = call("GET", path + ("?" + urlencode({k: v for k, v in kw.items() if v is not None}) if kw else ""))
    assert status == 200, (path, status, body)
    return body


def login(user, password):
    status, body = call("POST", "/api/auth/login", {"username": user, "password": password})
    return body.get("token") if status == 200 else None


def sql(query):
    out = subprocess.run(["docker", "exec", PSQL, "psql", "-U", "qc", "-d", "wifitest", "-At", "-c", query],
                         capture_output=True, text=True, check=True).stdout.strip()
    return out


def overview_day(d):
    return get("/api/overview", date_from=d, date_to=d)


# ── FAIL list: counts, pending / handled ──
fl = get("/api/fail-list", page_size=100)
check("Fail List (all data): 15 failed units, all pending, each with a next step and a disposition",
      fl["counts"] == {"all": 15, "pending": 15, "handled": 0} and all(r["suggest"]["code"] and r["disposition"]["status"] == "open" for r in fl["items"]), fl["counts"])
print("\n  The 15 units still failing and their next step:")
for r in fl["items"]:
    a = r["analysis"] or {}
    print(f"    {r['test_time'][:16]} {r['work_order']} {r['sn']} {a.get('category'):<17} {a.get('run_seq'):<6} → {r['suggest']['code']} {r['suggest']['values'] or ''}")
print()
by_code = {}
for r in fl["items"]:
    by_code[r["suggest"]["code"]] = by_code.get(r["suggest"]["code"], 0) + 1
check("next steps: 'check the station' for the two bursts (2.4G on 01/13: 2 units, BT not found on 01/07: 4), RD for the one "
      "repeated FAIL outside a burst, retest for the other 8", by_code == {"retest": 8, "check_env": 6, "to_rd": 1}, by_code)

# ── disposition: permissions and validation ──
target = next(r for r in fl["items"] if r["sn"] and r["disposition"]["status"] == "open")
rid = int(target["id"])
status, _ = call("PUT", "/api/fail-disposition", {"record_id": rid, "status": "repair"})
check("disposition without signing in → 401", status == 401, status)
admin = login(*ADMIN)
rd = login(*RD)
check("administrator and the RD account sign in", admin and rd)
for body, code, label in (({"record_id": rid, "status": "fixed"}, "bad_status", "unknown status"),
                          ({"record_id": rid, "status": "retest_pass"}, "bad_status", "an automatic status by hand"),
                          ({"record_id": rid, "status": "closed"}, "note_required", "closing without a note"),
                          ({"record_id": rid, "status": "repair", "note": "x" * 301}, "bad_note", "a 301-character note"),
                          ({"record_id": rid, "status": "repair", "extra": 1}, "validation", "an unknown field")):
    status, data = call("PUT", "/api/fail-disposition", body, rd)
    want = 422 if code == "validation" else 400
    check(f"{label} → {want} {code}", status == want and data.get("code") == code, (status, data and data.get("code")))
status, data = call("PUT", "/api/fail-disposition", {"record_id": 999999999, "status": "repair"}, rd)
check("an unknown test run → 404", status == 404 and data.get("code") == "no_fail", status)
pass_id = int(sql("SELECT id FROM test_record WHERE result = TRUE ORDER BY id LIMIT 1"))
status, data = call("PUT", "/api/fail-disposition", {"record_id": pass_id, "status": "repair"}, rd)
check("a PASS test run → 404 (only FAILs have a disposition)", status == 404, status)
retest_id = int(sql("SELECT record_id FROM fail_analysis WHERE retest_outcome = 'pass_on_retest' ORDER BY record_id LIMIT 1"))
status, data = call("PUT", "/api/fail-disposition", {"record_id": retest_id, "status": "repair"}, rd)
check("a FAIL that passed on retest → 409 (closed automatically)", status == 409 and data.get("code") == "closed_by_retest", (status, data))
again_id = int(sql("SELECT record_id FROM fail_analysis WHERE retest_outcome = 'failed_again' ORDER BY record_id LIMIT 1"))
status, data = call("PUT", "/api/fail-disposition", {"record_id": again_id, "status": "repair"}, rd)
check("an earlier FAIL of a unit tested again → 409 (the latest FAIL carries it)", status == 409 and data.get("code") == "superseded", (status, data))
a_retest = get(f"/api/fail-analysis/{retest_id}")
check("FAIL detail of a passed-on-retest run: disposition retest_pass (automatic), next step done",
      a_retest["disposition"]["status"] == "retest_pass" and a_retest["disposition"]["auto"] and a_retest["suggest"]["code"] == "done")
check("FAIL detail lists the unit's runs (FAIL then PASS) with category names, and its MAC",
      len(a_retest["runs"]) >= 2 and a_retest["runs"][0]["id"] <= retest_id and any(r["result"] for r in a_retest["runs"])
      and a_retest["wifi_mac"], [(r["result"], r.get("name_en")) for r in a_retest["runs"]][:4])

# ── disposition: set, counted, audited, put back ──
status, data = call("PUT", "/api/fail-disposition", {"record_id": rid, "status": "repair", "note": "sent to RMA bench"}, rd)
check("RD account sets 'sent to repair' with a note", status == 200 and data["disposition"]["status"] == "repair"
      and data["disposition"]["updated_by"] == RD[0] and data["disposition"]["note"] == "sent to RMA bench", (status, data))
check("the change is in the history: open → repair, with the note", data["history"] and data["history"][0]["detail"]["status"] == "open → repair"
      and data["history"][0]["detail"].get("note") == "sent to RMA bench", data["history"][:1])
fl2 = get("/api/fail-list", page_size=100)
check("Fail List counts follow: 14 pending, 1 handled", fl2["counts"] == {"all": 15, "pending": 14, "handled": 1}, fl2["counts"])
pend = get("/api/fail-list", page_size=100, disposition="pending")
hand = get("/api/fail-list", page_size=100, disposition="handled")
check("'To handle' leaves it out, 'Handled' shows only it", rid not in [int(r["id"]) for r in pend["items"]]
      and [int(r["id"]) for r in hand["items"]] == [rid] and hand["items"][0]["disposition"]["status"] == "repair")
status, data = call("PUT", "/api/fail-disposition", {"record_id": rid, "status": "retest"}, admin)
check("administrator changes it to 'retest queued' (still pending)", status == 200 and data["disposition"]["status"] == "retest"
      and get("/api/fail-list", page_size=100)["counts"]["pending"] == 15, status)
status, data = call("PUT", "/api/fail-disposition", {"record_id": rid, "status": "closed", "note": "scrapped"}, admin)
check("closed with a note", status == 200 and data["disposition"]["status"] == "closed")
status, data = call("PUT", "/api/fail-disposition", {"record_id": rid, "status": "open"}, admin)
check("back to open: no stored row, counts as before", status == 200 and data["disposition"]["status"] == "open"
      and data["disposition"]["manual"] is None and get("/api/fail-list", page_size=100)["counts"] == {"all": 15, "pending": 15, "handled": 0})
check("no row left in fail_disposition for it", sql(f"SELECT COUNT(*) FROM fail_disposition WHERE record_id = {rid}") == "0")
hist = get(f"/api/fail-analysis/{rid}")["history"]
check("history keeps all four changes, newest first", [h["detail"]["status"] for h in hist[:4]] == ["closed → open", "retest → closed", "repair → retest", "open → repair"],
      [h["detail"]["status"] for h in hist[:4]])

# ── Settings: working days (the Forecast view counts only these) ──
admin = admin or login(*ADMIN)
st = get("/api/settings")
check("Settings has the working days (default Monday–Friday)", st["settings"]["work_days"] == [1, 2, 3, 4, 5] and st["defaults"]["work_days"] == [1, 2, 3, 4, 5],
      st["settings"].get("work_days"))
for bad in ([], [0], [8], ["1"], [True]):
    status, data = call("PUT", "/api/settings", {"work_days": bad}, admin)
    check(f"working days {bad!r} → 400 or 422", status in (400, 422), status)
status, data = call("PUT", "/api/settings", {"work_days": [6, 1, 2, 3, 4, 5, 1]}, admin)
check("Monday–Saturday saved sorted without duplicates, audited", status == 200 and data["settings"]["work_days"] == [1, 2, 3, 4, 5, 6]
      and "work_days" in data["changed"], (status, data and data.get("changed")))
time.sleep(6)          # the other API worker re-reads the settings within 5 s
check("the forecast uses them", get("/api/forecast")["assumptions"]["work_days"] == [1, 2, 3, 4, 5, 6])
status, data = call("PUT", "/api/settings", {"work_days": [1, 2, 3, 4, 5]}, admin)
check("restored to Monday–Friday", status == 200 and get("/api/settings")["settings"]["work_days"] == [1, 2, 3, 4, 5])
status, data = call("PUT", "/api/settings", {"work_days": [1, 2]}, rd)
check("RD cannot change Settings → 403", status == 403, status)
status, data = call("GET", "/api/today")
check("the Today page is gone (404)", status == 404, status)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
