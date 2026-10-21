#!/usr/bin/env python3
"""Trend page Forecast view (API, 2026-10-21): output forecast for all data and one product against the Trend page's
own numbers, and a work order's completion forecast with and without a planned quantity.

    python3 tests/acceptance_forecast.py [BASE] [admin_user admin_pass]

Sets a planned quantity on 5101-260820009 twice and clears it again.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ADMIN = (sys.argv[2], sys.argv[3]) if len(sys.argv) > 3 else (os.environ.get("TWEAK_USER", "qc"), os.environ.get("TWEAK_PASS", "qcpass"))
WO, PM = "5101-260820009", "TN-250_EDM-G-8MP"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""), flush=True)


def call(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(), method=method,
                                 headers={"Content-Type": "application/json", **({"X-Tweak-Token": token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def get(path, **kw):
    status, body = call("GET", path + ("?" + urlencode({k: v for k, v in kw.items() if v is not None}) if kw else ""))
    assert status == 200, (path, kw, status, body)
    return body


def units(a, b, **kw):
    return get("/api/overview", date_from=a.isoformat(), date_to=b.isoformat(), **kw)["summary"]["total"]


# ── all data ──
f = get("/api/forecast")
today = date.fromisoformat(f["today"])
now = datetime.fromisoformat(f["now"])
check("no work order → the output forecast, starting today on the station clock", f["kind"] == "overall" and today == now.date())
month_start = today.replace(day=1)
check("this month so far = the Trend page's units from the 1st to today", f["month"]["done"] == units(month_start, today), f["month"]["done"])
week_start = today - timedelta(days=today.isoweekday() - 1)
check("this week so far = the Trend page's units from Monday to today", f["week"]["done"] == units(week_start, today), f["week"]["done"])
prev_start = (month_start - timedelta(days=1)).replace(day=1)
check("last month = the Trend page's last month", f["month"]["previous"] == units(prev_start, month_start - timedelta(days=1)), f["month"]["previous"])
a = f["average"]
window = [date.fromisoformat(a["to"]) - timedelta(days=i) for i in range(400)]
window = [d for d in window if d.isoweekday() in f["assumptions"]["work_days"] and d >= date.fromisoformat(a["from"])]
avg = round(sum(units(d, d) for d in window) / len(window), 1)
check("average per working day = the mean of the last 10 working days (days without tests count as 0)", a["working_days"] == 10
      and len(window) == 10 and a["per_working_day"] == avg, (a, avg))
m = f["month"]
check("projected = done + average × working days left", m["projected"] == round(m["done"] + a["per_working_day"] * m["working_days_left"]), m)
check("if testing resumes = done + average test day × working days left",
      m["projected_resume"] == round(m["done"] + (a["per_test_day"] or 0) * m["working_days_left"]), m)
check("the month by day: one entry per day, actual units add up to the month so far",
      len(f["daily"]) == (date(today.year + (today.month == 12), today.month % 12 + 1, 1) - month_start).days
      and sum(x["units"] or 0 for x in f["daily"]) == m["done"])
check("days after today have no actual units, working days ahead have a projection",
      all(x["units"] is None for x in f["daily"] if x["day"] > today.isoformat())
      and all(x["projected"] is not None for x in f["daily"] if x["day"] > today.isoformat()
              and date.fromisoformat(x["day"]).isoweekday() in f["assumptions"]["work_days"]))
mon = {x["month"]: x for x in f["monthly"]}
check("this year by month: the current month = this month so far, later months empty", mon[today.month]["units"] == m["done"]
      and all(mon[k]["units"] is None for k in range(today.month + 1, 13)))
check("idle days since the last test are reported", f["idle_days"] == (today - datetime.fromisoformat(f["last_test"]).date()).days)

fp = get("/api/forecast", product_model="TN-86_PICO-IMX7")
check("one product: its own month so far and last month", fp["product_model"] == "TN-86_PICO-IMX7"
      and fp["month"]["previous"] == units(prev_start, month_start - timedelta(days=1), product_model="TN-86_PICO-IMX7"))
check("the date range of the filter bar does not change a forecast", get("/api/forecast", date_from="2026-01-01", date_to="2026-01-31")["month"] == m)

# ── one work order without a plan ──
w = get("/api/forecast", work_order=WO)
check("work order without a plan: pace and units per working day, no finish", w["kind"] == "work_order" and w["found"] and w["planned"] is None
      and w["finish"] is None and w["rate"]["uph"] and w["rate"]["per_working_day"], w["rate"])
check("cumulative series ends at the units tested", w["series"]["actual"][-1]["cum"] == w["total"] == 500, w["series"]["actual"][-1])
check("pace = the last 6 finished test hours", w["rate"]["basis"] == "recent" and w["rate"]["recent_hours"] == 6)
check("units per working day = pace × typical test hours a day",
      abs(w["rate"]["per_working_day"] - w["rate"]["uph"] * w["rate"]["hours_per_day"]) < 0.11, w["rate"])
check("an unknown work order → found false", get("/api/forecast", work_order="9999-999999999")["found"] is False)

# ── with a plan ──
token = call("POST", "/api/auth/login", {"username": ADMIN[0], "password": ADMIN[1]})[1]["token"]
try:
    call("PUT", "/api/wo-plan", {"work_order": WO, "product_model": PM, "planned_qty": 800}, token)
    w = get("/api/forecast", work_order=WO)
    fin = datetime.fromisoformat(w["finish"]["at"])
    ws, we = (datetime.strptime(x, "%H:%M").time() for x in (w["assumptions"]["work_start"], w["assumptions"]["work_end"]))
    check("plan 800: 300 remaining, a finish after now, on a working day inside working hours",
          w["remaining"] == 300 and fin > now and fin.isoweekday() in w["assumptions"]["work_days"] and ws <= fin.time() <= we, w["finish"])
    need = 300 / w["rate"]["per_working_day"]
    check("working days needed = remaining ÷ units per working day", abs(w["finish"]["working_days"] - need) < 0.02, (w["finish"], need))
    proj = w["series"]["projected"]
    check("the projection rises to the plan and stops there", proj and proj[-1]["cum"] == 800
          and all(proj[i]["cum"] <= proj[i + 1]["cum"] for i in range(len(proj) - 1)))
    check("projected hours are working hours on working days (no nights, no weekends)",
          all(datetime.fromisoformat(p["t"]).isoweekday() in w["assumptions"]["work_days"] for p in proj))
    check("the finish at the hourly target is given too", w["finish_target"] and w["rate"]["target_uph"])
    check("idle hours reported (the finish assumes testing resumes now)", w["idle_hours"] and w["idle_hours"] > 2)
    call("PUT", "/api/wo-plan", {"work_order": WO, "product_model": PM, "planned_qty": 300}, token)
    w = get("/api/forecast", work_order=WO)
    check("plan 300 (already passed): reached, nothing remaining, the time it was reached",
          w["remaining"] == 0 and w["finish"] is None and w["reached_at"] and w["reached_at"].startswith("2026-10-01"), (w["remaining"], w["reached_at"]))
finally:
    status, _ = call("DELETE", f"/api/wo-plan?work_order={WO}", token=token)
check("plan cleared again", get("/api/forecast", work_order=WO)["planned"] is None)

print(f"\n{sum(results)}/{len(results)} forecast checks passed")
sys.exit(0 if all(results) else 1)
