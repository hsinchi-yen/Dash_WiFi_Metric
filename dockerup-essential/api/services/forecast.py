"""The Trend page's Forecast view: rules on the data already there, no LLM.

forecast(work_order, product_model)
  one work order (the Trend page follows the latest one by default) → completion forecast:
      tested / planned, remaining, the recent hourly rate (last 6 active hours) and the work order's typical active hours
      per test day → units per working day → the estimated finish (Settings working hours and days); the same at the
      hourly target for comparison; cumulative units so far and the projected line up to the plan; output per test day.
  no work order (all data or one product) → output forecast:
      this week and this month: done so far + the average of the last 10 working days (days without tests count as 0,
      so a stopped line forecasts nothing) × the working days left — and, beside it, what the average test day would
      give if testing resumes; last week / last month for comparison; yields;
      units per day this month (actual + projected) with the cumulative against last month's; units per month this year.
The date range of the filter bar does not apply: a forecast always starts now (the test stations' clock).
"""
import statistics
from datetime import date, datetime, timedelta
from typing import Any, Optional

import config
import db
from services import settings, wo_status
from services.filters import UNIT
from services.forecast_rules import eta, last_working_days, recent_uph, working_days_between, working_share
from services.wo_rules import local_now, parse_dt

AVG_WORKING_DAYS = 10
RECENT_HOURS = 6
MAX_POINTS = 60


def _pct(passed, total):
    return round(passed * 100.0 / total, 2) if total else None


def _scope(product_model: Optional[str], work_order: Optional[str] = None) -> tuple[str, list]:
    w, p = ["TRUE"], []
    if work_order:
        w.append("work_order = %s"); p.append(work_order)
    if product_model:
        w.append("product_model = %s"); p.append(product_model)
    return " AND ".join(w), p


# ─── One work order ──────────────────────────────────────────────────────────

def _status_for(work_order: str, product_model: Optional[str]) -> Optional[dict]:
    for (wo, pm), st in wo_status.statuses().items():
        if wo == work_order and (not product_model or pm == product_model):
            return st
    return None


def work_order_forecast(work_order: str, product_model: Optional[str], now: datetime, s: dict) -> dict[str, Any]:
    st = _status_for(work_order, product_model)
    if not st:
        return {"kind": "work_order", "work_order": work_order, "product_model": product_model, "found": False}
    pm = st["product_model"]
    where, params = _scope(pm, work_order)
    hours = [(parse_dt(r["h"]), int(r["n"])) for r in db.rows(
        f"SELECT date_trunc('hour', test_time) AS h, COUNT(*) AS n FROM {UNIT} WHERE {where} GROUP BY 1 ORDER BY 1", tuple(params))]
    per_day = {}
    for h, n in hours:
        d = per_day.setdefault(h.date(), {"units": 0, "hours": 0})
        d["units"] += n
        d["hours"] += 1
    days = [{"day": d.isoformat(), "units": v["units"], "active_hours": v["hours"],
             "uph": round(v["units"] / v["hours"], 1)} for d, v in sorted(per_day.items())]
    total, planned = int(st["total"]), st.get("planned")
    uph_recent, used = recent_uph(hours, now, RECENT_HOURS)
    uph_all = st.get("uph")
    uph = uph_recent if used >= 3 else uph_all
    # Typical active hours per test day: this work order's own days (2 or more), else the product's work orders.
    own = [d["active_hours"] for d in days[:-1] if d["active_hours"]] if len(days) > 2 else [d["active_hours"] for d in days]
    if len(own) < 2:
        rows = db.rows(f"""SELECT COUNT(DISTINCT date_trunc('hour', test_time)) AS h FROM {UNIT}
                           WHERE product_model = %s GROUP BY work_order, test_time::date""", (pm,))
        own = [int(r["h"]) for r in rows if r["h"]] or own
    hours_per_day = round(statistics.median(own), 1) if own else None
    per_working_day = round(uph * hours_per_day, 1) if uph and hours_per_day else None
    target = st.get("uph_target")
    target_per_day = round(target * hours_per_day, 1) if target and hours_per_day else None

    remaining = max(planned - total, 0) if planned else None
    finish = finish_target = None
    reached_at = None
    if planned and remaining == 0:
        row = db.rows(f"SELECT test_time FROM {UNIT} WHERE {where} ORDER BY test_time, id OFFSET %s LIMIT 1", (*params, planned - 1))
        reached_at = row[0]["test_time"] if row else None
    elif planned:
        finish = eta(now, remaining, per_working_day, s["work_start"], s["work_end"], s["work_days"])
        finish_target = eta(now, remaining, target_per_day, s["work_start"], s["work_end"], s["work_days"]) if target_per_day else None

    last_test = parse_dt(st.get("last_test"))
    series = _wo_series(where, params, now, s, planned, total, per_working_day, finish, days)
    return {
        "kind": "work_order", "found": True, "work_order": work_order, "product_model": pm,
        "state": st.get("state"), "light": st.get("light"), "flags": st.get("flags"),
        "total": total, "planned": planned, "remaining": remaining, "achieved_pct": st.get("achieved_pct"),
        "yield_pct": st.get("yield_pct"), "passed": st.get("passed"), "failed": st.get("failed"),
        "first_test": st.get("first_test"), "last_test": last_test,
        "idle_hours": round((now - last_test).total_seconds() / 3600, 1) if last_test else None,
        "rate": {"uph": uph, "recent_uph": uph_recent, "recent_hours": used, "overall_uph": uph_all, "target_uph": target,
                 "target_source": st.get("uph_target_source"), "hours_per_day": hours_per_day,
                 "per_working_day": per_working_day, "target_per_day": target_per_day,
                 "basis": "recent" if used >= 3 else "overall"},
        "finish": finish, "finish_target": finish_target, "reached_at": reached_at,
        "series": series, "days": days[-14:],
    }


def _wo_series(where, params, now, s, planned, total, per_day, finish, days) -> dict[str, Any]:
    """Cumulative units by test day (by hour for a work order of one or two days) and the projection up to the plan."""
    hourly = len(days) <= 2
    unit = "hour" if hourly else "day"
    rows = db.rows(f"SELECT date_trunc('{unit}', test_time) AS b, COUNT(*) AS n FROM {UNIT} WHERE {where} GROUP BY 1 ORDER BY 1",
                   tuple(params))
    cum, actual = 0, []
    for r in rows:
        cum += int(r["n"])
        actual.append({"t": r["b"], "cum": cum})
    projected = []
    if planned and per_day and finish and total < planned:
        step = timedelta(hours=1) if hourly else timedelta(days=1)
        point = now.replace(minute=0, second=0, microsecond=0) + step if hourly else \
            datetime.combine(now.date(), datetime.min.time()) + step       # end of each hour / day ahead
        ws, we = s["work_start"], s["work_end"]
        while point < finish["at"] and len(projected) < MAX_POINTS:
            label = point if hourly else point - step
            working = label.isoweekday() in s["work_days"] and (not hourly or ws < point.strftime("%H:%M") <= we)
            if working:            # nights and days off are left out: the line does not test then
                share = working_share(now, point, ws, we, s["work_days"])
                projected.append({"t": label, "cum": round(total + per_day * share, 1)})
            point += step
        projected.append({"t": finish["at"] if hourly else datetime.combine(finish["at"].date(), datetime.min.time()), "cum": planned})
    return {"unit": unit, "actual": actual, "projected": projected, "plan": planned}


# ─── All data / one product ──────────────────────────────────────────────────

def _units_between(where, params, a, b) -> dict[str, Any]:
    r = db.rows(f"""SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE result = TRUE) AS p FROM {UNIT}
                    WHERE {where} AND test_time >= %s AND test_time < %s""", (*params, a, b))[0]
    return {"units": int(r["n"]), "pass": int(r["p"]), "yield_pct": _pct(int(r["p"]), int(r["n"]))}


def overall_forecast(product_model: Optional[str], now: datetime, s: dict) -> dict[str, Any]:
    where, params = _scope(product_model)
    today = now.date()
    days = s["work_days"]
    week_start = today - timedelta(days=today.isoweekday() - 1)
    week_end = week_start + timedelta(days=6)
    month_start = today.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    month_end = next_month - timedelta(days=1)
    prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
    dt = lambda d: datetime.combine(d, datetime.min.time())  # noqa: E731

    per_day = {r["d"] if isinstance(r["d"], date) else date.fromisoformat(str(r["d"])[:10]): {"units": int(r["n"]), "pass": int(r["p"])}
               for r in db.rows(f"""SELECT test_time::date AS d, COUNT(*) AS n, COUNT(*) FILTER (WHERE result = TRUE) AS p
                                     FROM {UNIT} WHERE {where} AND test_time >= %s GROUP BY 1""", (*params, dt(prev_month_start - timedelta(days=40))))}
    window = last_working_days(today, AVG_WORKING_DAYS, days)
    avg_wd = round(sum(per_day.get(d, {}).get("units", 0) for d in window) / len(window), 1) if window else 0.0
    test_days = db.rows(f"""SELECT test_time::date AS d, COUNT(*) AS n FROM {UNIT} WHERE {where} AND test_time < %s
                            GROUP BY 1 ORDER BY 1 DESC LIMIT %s""", (*params, dt(today), AVG_WORKING_DAYS))
    avg_test_day = round(sum(int(r["n"]) for r in test_days) / len(test_days), 1) if test_days else None
    tested_in_window = sum(1 for d in window if per_day.get(d, {}).get("units"))

    def period(a: date, b: date, prev_a: date, prev_b: date):
        done = _units_between(where, params, dt(a), dt(min(b, today) + timedelta(days=1)))
        left = working_days_between(now, b, s["work_start"], s["work_end"], days)
        prev = _units_between(where, params, dt(prev_a), dt(prev_b + timedelta(days=1)))
        same_point = _units_between(where, params, dt(prev_a), dt(prev_a + (today - a) + timedelta(days=1)))
        return {"from": a.isoformat(), "to": b.isoformat(), "done": done["units"], "yield_pct": done["yield_pct"],
                "working_days_left": left, "projected": round(done["units"] + avg_wd * left),
                "projected_resume": round(done["units"] + (avg_test_day or 0) * left),
                "previous": prev["units"], "previous_yield_pct": prev["yield_pct"], "previous_same_point": same_point["units"]}

    week = period(week_start, week_end, week_start - timedelta(days=7), week_start - timedelta(days=1))
    month = period(month_start, month_end, prev_month_start, month_start - timedelta(days=1))

    # This month day by day: actual units, then the average on each working day left.
    daily, cum, prev_cum = [], 0, 0
    prev_days = sorted(d for d in per_day if prev_month_start <= d < month_start)
    prev_by_dom = {}
    for d in prev_days:
        prev_cum += per_day[d]["units"]
        prev_by_dom[d.day] = prev_cum
    running_prev = 0
    proj = None
    for i in range((month_end - month_start).days + 1):
        d = month_start + timedelta(days=i)
        running_prev = prev_by_dom.get(d.day, running_prev)
        actual = per_day.get(d, {}).get("units", 0) if d <= today else None
        future = None
        if d >= today and d.isoweekday() in days:
            share = working_share(max(now, dt(d)), dt(d + timedelta(days=1)), s["work_start"], s["work_end"], days)
            future = round(avg_wd * share, 1)
        if actual is not None:
            cum += actual
        if d >= today:
            proj = (cum if proj is None else proj) + (future or 0)
        daily.append({"day": d.isoformat(), "units": actual, "projected": future, "cum": cum if d <= today else None,
                      "cum_projected": round(proj, 1) if proj is not None else None, "prev_cum": running_prev if prev_days else None})

    months = db.rows(f"""SELECT EXTRACT(MONTH FROM test_time)::int AS m, COUNT(*) AS n FROM {UNIT}
                         WHERE {where} AND test_time >= %s AND test_time < %s GROUP BY 1""",
                     (*params, dt(date(today.year, 1, 1)), dt(date(today.year + 1, 1, 1))))
    by_month = {int(r["m"]): int(r["n"]) for r in months}
    monthly = [{"month": m, "units": by_month.get(m, 0) if m <= today.month else None,
                "projected_extra": max(month["projected"] - month["done"], 0) if m == today.month else None} for m in range(1, 13)]

    last = db.rows(f"SELECT MAX(test_time) AS t FROM {UNIT} WHERE {where}", tuple(params))[0]["t"]
    recent = _units_between(where, params, dt(window[-1]) if window else dt(today), dt(today))
    return {
        "kind": "overall", "product_model": product_model, "today": today.isoformat(),
        "week": week, "month": month,
        "average": {"working_days": len(window), "per_working_day": avg_wd, "per_test_day": avg_test_day,
                    "tested_days": tested_in_window, "from": window[-1].isoformat() if window else None,
                    "to": window[0].isoformat() if window else None},
        "recent_yield_pct": recent["yield_pct"], "recent_units": recent["units"],
        "last_test": last, "idle_days": (today - parse_dt(last).date()).days if last else None,
        "daily": daily, "monthly": monthly, "year": today.year,
    }


def forecast(work_order: Optional[str], product_model: Optional[str], now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or local_now(config.LOCAL_TZ)
    s = settings.get()
    out = work_order_forecast(work_order, product_model, now, s) if work_order else overall_forecast(product_model, now, s)
    out["now"] = now.isoformat(timespec="seconds")
    out["assumptions"] = {k: s[k] for k in ("work_start", "work_end", "work_days")}
    return out
