#!/usr/bin/env python3
"""Phase 5C acceptance (API): the Today page's numbers against the Trend / Fail List endpoints for the same day,
the banner, the FAIL disposition (permissions, validation, automatic states, counts, audit history), the
suggested next steps of the units still failing, and the new Settings field.

    python3 tests/acceptance_phase5c.py [BASE] [admin_user admin_pass]

Needs the account test_rd (tests/acceptance_phase5f1.py creates it). Reads two record ids straight from the
database container (PSQL_CONTAINER, default wifitest-db). Every disposition it sets is put back to "open"
(no row), and the Settings value it changes is restored.
"""
import json
import os
import subprocess
import sys
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


# ── Today = the Trend page's numbers for the same day ──
DAY = "2026-10-01"
td = get("/api/today", day=DAY)
ov = overview_day(DAY)
check("Today 10/01: units / PASS / FAIL / yield / 2.4G / 5G equal the Trend page's 10/01 range",
      all(td["summary"][k] == ov["summary"][k] for k in ("total", "pass", "fail", "yield_pct", "avg_24g", "avg_5g", "test_runs")),
      {k: (td["summary"][k], ov["summary"][k]) for k in ("total", "yield_pct")})
check("not today: is_today false, the day shown is 10/01", td["is_today"] is False and td["day"] == DAY)
h = td["hourly"]
in_hours = sum(p["units"] or 0 for p in h["points"])
check("hourly: working hours 07:00–19:00 = 12 bars, bars + before + after = units of the day",
      [p["hour"] for p in h["points"]][0] == "07:00" and len(h["points"]) == 12 and in_hours + h["before"] + h["after"] == td["summary"]["total"],
      (in_hours, h["before"], h["after"], td["summary"]["total"]))
check("hourly: every hour with tests has its main work order's target", all(p["target"] for p in h["points"] if p["units"]))
prev = td["previous"]
pv = overview_day(prev["day"])
check("previous test day = 09/30 with the Trend page's numbers", prev["day"] == "2026-09-30" and prev["total"] == pv["summary"]["total"]
      and prev["yield_pct"] == pv["summary"]["yield_pct"], prev)
check("day switch: previous 09/30, next none (the newest test day)", td["neighbours"] == {"prev": "2026-09-30", "next": None}, td["neighbours"])
tr = td["trend"]
check("trend: the 14 test days up to 10/01, the last one is 10/01", len(tr) == 14 and tr[-1]["day"] == DAY and tr[-1]["total"] == td["summary"]["total"])
spot = tr[-5]
check("trend: a day in it equals the Trend page's numbers", spot["total"] == overview_day(spot["day"])["summary"]["total"], spot)
check("trend: days under 10 units have no yield point (one FAIL would read as 0%)",
      all((p["yield_pct"] is None) == (p["total"] < td["trend_min_units"]) for p in tr), [(p["day"], p["total"], p["yield_pct"]) for p in tr if p["total"] < 10])
check("work orders of the day add up to the day's units, each with its status and note",
      sum(w["units"] for w in td["work_orders"]) == td["summary"]["total"] and all(w["status"] and w["status"]["note"] for w in td["work_orders"]))
check("monthly = the Trend page's units per month (all products, 2026)", td["monthly"] == ov["monthly"])
check("a normal day: green banner, no reasons", td["banner"]["light"] == "good" and td["banner"]["reasons"] == [], td["banner"])
lh = td["last_hour"]
check("last hour (past day): the 60 minutes up to the day's last test, against the main work order's target",
      lh["to"] == td["summary"]["last_test"] and lh["units"] > 0 and lh["target"] and lh["light"] == "none", lh)

# A day with failures
DAY2 = "2026-01-13"
td2 = get("/api/today", day=DAY2)
fl_day = get("/api/fail-list", date_from=DAY2, date_to=DAY2, page_size=100)
check("01/13: FAIL to handle = the Fail List's failed units of that day (4), none handled yet",
      td2["pending"]["fail_units"] == fl_day["counts"]["all"] == 4 and td2["pending"]["pending"] == 4 and td2["pending"]["handled"] == 0, td2["pending"])
older = get("/api/fail-list", date_from="2026-01-06", date_to="2026-01-12", page_size=100)
check("01/13: older pending = the Fail List's pending units of the 7 days before", td2["pending"]["older_pending"] == older["counts"]["pending"], (td2["pending"]["older_pending"], older["counts"]))
fs = get("/api/fail-summary", date_from=DAY2, date_to=DAY2)
fs7 = get("/api/fail-summary", date_from="2026-01-07", date_to=DAY2)
check("FAIL Top: the day's counts = the failure summary of 01/13, the 7-day counts = 01/07–01/13",
      sum(r["day"] for r in td2["fail_top"]) == fs["total_runs"] and sum(r["week"] for r in td2["fail_top"]) <= fs7["total_runs"]
      and td2["fail_top"][0]["week"] == next(c["runs"] for c in fs7["categories"] if c["category"] == td2["fail_top"][0]["category"]),
      (sum(r["day"] for r in td2["fail_top"]), fs["total_runs"]))
codes = [(r["code"], r["level"]) for r in td2["banner"]["reasons"]]
check("01/13 banner: red, the red work orders first, then the pending FAILs",
      td2["banner"]["light"] == "bad" and codes[0] == ("wo", "bad") and ("pending", "warn") in codes, codes)
red = [r for r in td2["banner"]["reasons"] if r["code"] == "wo"]
check("the banner quotes the stored one-line note of each red work order (no LLM call)", all(r["note"] and r["note"]["zh"] for r in red))

# Today itself (no tests yet on the local copy)
now = get("/api/today")
check("no day → today on the station clock", now["day"] == date.today().isoformat() and now["is_today"] is True, now["day"])
if now["summary"]["total"] == 0:
    check("no tests yet today: neutral banner with the last test time", now["banner"]["code"] == "no_tests" and now["banner"]["light"] == "none"
          and now["last_test"]["at"], now["banner"])
status, body = call("GET", "/api/today?day=2026-13-45x")
check("a malformed day → 422 (validation)", status == 422, status)
status, body = call("GET", "/api/today?day=2026-02-30")
check("an impossible date → today (not a crash)", status == 200 and body["is_today"], status)

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
day_of = target["test_time"][:10]
td3 = get("/api/today", day=day_of)
check("Today of that day counts it as handled", td3["pending"]["handled"] >= 1, td3["pending"])
status, data = call("PUT", "/api/fail-disposition", {"record_id": rid, "status": "retest"}, admin)
check("administrator changes it to 'retest queued' (still pending)", status == 200 and data["disposition"]["status"] == "retest"
      and get("/api/fail-list", page_size=100)["counts"]["pending"] == 15, status)
check("Today: a queued retest is pending and counted as such", get("/api/today", day=day_of)["pending"]["retest"] == 1)
status, data = call("PUT", "/api/fail-disposition", {"record_id": rid, "status": "closed", "note": "scrapped"}, admin)
check("closed with a note", status == 200 and data["disposition"]["status"] == "closed")
status, data = call("PUT", "/api/fail-disposition", {"record_id": rid, "status": "open"}, admin)
check("back to open: no stored row, counts as before", status == 200 and data["disposition"]["status"] == "open"
      and data["disposition"]["manual"] is None and get("/api/fail-list", page_size=100)["counts"] == {"all": 15, "pending": 15, "handled": 0})
check("no row left in fail_disposition for it", sql(f"SELECT COUNT(*) FROM fail_disposition WHERE record_id = {rid}") == "0")
hist = get(f"/api/fail-analysis/{rid}")["history"]
check("history keeps all four changes, newest first", [h["detail"]["status"] for h in hist[:4]] == ["closed → open", "retest → closed", "repair → retest", "open → repair"],
      [h["detail"]["status"] for h in hist[:4]])

# ── Settings: idle warning ──
st = get("/api/settings")
check("Settings has the Today idle warning (default 30 minutes)", st["settings"]["idle_warn_minutes"] == 30 and st["defaults"]["idle_warn_minutes"] == 30,
      st["settings"].get("idle_warn_minutes"))
status, data = call("PUT", "/api/settings", {"idle_warn_minutes": 3}, admin)
check("idle warning 3 → 400 (5–600)", status == 400 and "idle_warn_minutes" in (data.get("fields") or {}), status)
status, data = call("PUT", "/api/settings", {"idle_warn_minutes": 45}, admin)
check("idle warning 45 saved and audited", status == 200 and data["changed"].get("idle_warn_minutes") == "30 → 45", status)
check("Today reports the new value", get("/api/today", day=DAY)["settings"]["idle_warn_minutes"] == 45)
status, data = call("PUT", "/api/settings", {"idle_warn_minutes": 30}, admin)
check("restored to 30", status == 200 and get("/api/settings")["settings"]["idle_warn_minutes"] == 30)
status, data = call("PUT", "/api/settings", {"idle_warn_minutes": 45}, rd)
check("RD cannot change Settings → 403", status == 403, status)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
