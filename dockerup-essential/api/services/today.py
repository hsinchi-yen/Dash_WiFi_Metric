"""Today page (5C): the line's day at a glance. It is the home page and the TV shows it in turn with the Trend
page (Q1); the line leader only watches it (Q2), so it has no controls that matter. Single shift (Q3): "today"
is the calendar day on the test stations' clock (LOCAL_TZ); the working hours come from Settings.

today(day) → everything the page shows, in one response (read only; indexed queries on mv_unit / fail_analysis):
  summary      units / PASS / FAIL / yield / runs / 2.4G and 5G average of the day (as the Trend page's "Today")
  previous     the previous test day's units and yield (the yield tile's ▲▼)
  pending      the day's failed units still to handle (open / retest) or handled, and the 7 days before
  last_hour    units in the 60 minutes up to now (today) or up to the day's last test, against the hourly target
  last_test    the newest test of any day; how long ago, and whether the line has been idle too long
  work_orders  every work order tested that day: today's units, its whole-work-order status (wo_rules) and note (L1)
  banner       the overall light and the reasons behind it, most serious first
  hourly       units per hour in working hours (PASS / FAIL) and each hour's target (that hour's main work order's)
  trend        the last 14 test days up to the day: units and yield
  fail_top     FAIL runs by category on the day and in the 7 days up to it
  monthly      units per month of the day's year, all products
No LLM call: the notes are the stored L1 notes (written only for red / amber work orders).
"""
from datetime import date, datetime, timedelta
from typing import Any, Optional

import config
import db
from services import analytics, settings, wo_notes, wo_status
from services.analytics import DISPOSITION_SQL, PENDING_SQL
from services.filters import UNIT
from services.wo_rules import MIN_UNITS_YIELD, local_now, parse_dt

TREND_DAYS = 14
FAIL_TOP_DAYS = 7
PENDING_LOOKBACK_DAYS = 7
LAST_HOUR_MIN = 60


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _day(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def parse_day(text: Optional[str], now: datetime) -> datetime:
    if text:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            pass
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _neighbours(lo: datetime, hi: datetime) -> dict[str, Optional[str]]:
    row = db.rows(f"""SELECT (SELECT MAX(test_time) FROM {UNIT} WHERE test_time < %s) AS prev,
                             (SELECT MIN(test_time) FROM {UNIT} WHERE test_time >= %s) AS next""", (lo, hi))[0]
    return {"prev": _day(row["prev"]), "next": _day(row["next"])}


def _pending(lo: datetime, hi: datetime) -> dict[str, int]:
    def count(a, b):
        r = db.rows(f"""SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE {PENDING_SQL}) AS pending,
                               COUNT(*) FILTER (WHERE {DISPOSITION_SQL} = 'retest') AS retest
                        FROM {UNIT} WHERE result = FALSE AND test_time >= %s AND test_time < %s""", (a, b))[0]
        return int(r["n"]), int(r["pending"]), int(r["retest"])
    units, pending, retest = count(lo, hi)
    _, older, _ = count(lo - timedelta(days=PENDING_LOOKBACK_DAYS), lo)
    runs = int(db.rows("SELECT COUNT(*) AS n FROM fail_analysis WHERE test_time >= %s AND test_time < %s", (lo, hi))[0]["n"])
    return {"fail_units": units, "pending": pending, "retest": retest, "handled": units - pending,
            "older_pending": older, "older_days": PENDING_LOOKBACK_DAYS, "fail_runs": runs}


def _targets(statuses: dict) -> dict:
    return {k: st.get("uph_target") for k, st in statuses.items()}


def _last_hour(ref: Optional[datetime], targets: dict, fallback_key, s: dict, is_today: bool, now: datetime,
               first_test: Optional[datetime]) -> dict[str, Any]:
    if ref is None:
        return {"units": 0, "target": None, "light": "none", "work_order": None}
    rows = db.rows(f"""SELECT work_order, product_model, COUNT(*) AS n FROM {UNIT}
                       WHERE test_time > %s AND test_time <= %s GROUP BY 1, 2 ORDER BY 3 DESC""",
                   (ref - timedelta(minutes=LAST_HOUR_MIN), ref))
    units = sum(int(r["n"]) for r in rows)
    key = (rows[0]["work_order"], rows[0]["product_model"]) if rows else fallback_key
    target = targets.get(key) if key else None
    light = "none"
    in_hours = _minutes(s["work_start"]) <= now.hour * 60 + now.minute <= _minutes(s["work_end"])
    # Judged only today, inside working hours, once the line has run for an hour (as the status flag).
    if is_today and target and in_hours and first_test and now - first_test >= timedelta(hours=1):
        light = "warn" if units < target * (1 - s["behind_pct"] / 100) else "good"
    return {"units": units, "target": target, "light": light, "from": ref - timedelta(minutes=LAST_HOUR_MIN), "to": ref,
            "work_order": key[0] if key else None, "product_model": key[1] if key else None}


def _hourly(lo: datetime, hi: datetime, s: dict, targets: dict, day_key, is_today: bool, now: datetime) -> dict[str, Any]:
    start, end = _minutes(s["work_start"]), _minutes(s["work_end"])
    rows = db.rows(f"""
        SELECT (EXTRACT(HOUR FROM test_time) * 60 + EXTRACT(MINUTE FROM test_time))::int AS m, work_order, product_model,
               COUNT(*) FILTER (WHERE result = TRUE) AS pass, COUNT(*) FILTER (WHERE result = FALSE) AS fail
        FROM {UNIT} WHERE test_time >= %s AND test_time < %s GROUP BY 1, 2, 3
    """, (lo, hi))
    hours = [h for h in range(24) if h * 60 < end and (h + 1) * 60 > start]
    by_hour = {h: {"pass": 0, "fail": 0, "wos": {}} for h in hours}
    before = after = 0
    for r in rows:
        n = int(r["pass"] or 0) + int(r["fail"] or 0)
        if r["m"] < start:
            before += n
        elif r["m"] >= end:
            after += n
        else:
            b = by_hour[r["m"] // 60]
            b["pass"] += int(r["pass"] or 0)
            b["fail"] += int(r["fail"] or 0)
            key = (r["work_order"], r["product_model"])
            b["wos"][key] = b["wos"].get(key, 0) + n
    points = []
    for h in hours:
        b = by_hour[h]
        future = is_today and h * 60 > now.hour * 60 + now.minute
        main = max(b["wos"], key=b["wos"].get) if b["wos"] else day_key
        points.append({"hour": f"{h:02d}:00", "pass": None if future else b["pass"], "fail": None if future else b["fail"],
                       "units": None if future else b["pass"] + b["fail"],
                       "target": targets.get(main) if main else None, "work_order": main[0] if main else None})
    return {"start": s["work_start"], "end": s["work_end"], "points": points, "before": before, "after": after}


def _trend(hi: datetime) -> list[dict[str, Any]]:
    rows = db.rows(f"""SELECT test_time::date AS d, COUNT(*) AS total, COUNT(*) FILTER (WHERE result = TRUE) AS pass
                       FROM {UNIT} WHERE test_time < %s GROUP BY 1 ORDER BY 1 DESC LIMIT %s""", (hi, TREND_DAYS))
    out = []
    for r in reversed(rows):
        total = int(r["total"])
        # Fewer than MIN_UNITS_YIELD units: no yield point (one failed unit would read as 0%), as the status rules.
        out.append({"day": _day(r["d"]), "total": total, "pass": int(r["pass"]),
                    "yield_pct": round(int(r["pass"]) * 100.0 / total, 2) if total >= MIN_UNITS_YIELD else None})
    return out


def _fail_top(lo: datetime, hi: datetime) -> list[dict[str, Any]]:
    rows = db.rows("""
        SELECT f.category, t.name_zh, t.name_en, MIN(t.sort) AS sort,
               COUNT(*) FILTER (WHERE f.test_time >= %s) AS day, COUNT(*) AS week
        FROM fail_analysis f LEFT JOIN fail_taxonomy t ON t.code = f.category
        WHERE f.test_time >= %s AND f.test_time < %s
        GROUP BY f.category, t.name_zh, t.name_en ORDER BY 5 DESC, 6 DESC, 4 LIMIT 6
    """, (lo, lo - timedelta(days=FAIL_TOP_DAYS - 1), hi))
    return [{**r, "day": int(r["day"]), "week": int(r["week"])} for r in rows]


def _work_orders(lo: datetime, hi: datetime, statuses: dict) -> list[dict[str, Any]]:
    rows = db.rows(f"""
        SELECT work_order, product_model, COUNT(*) AS units, COUNT(*) FILTER (WHERE result = TRUE) AS pass,
               COUNT(*) FILTER (WHERE result = FALSE) AS fail, MIN(test_time) AS first_test, MAX(test_time) AS last_test
        FROM {UNIT} WHERE test_time >= %s AND test_time < %s
        GROUP BY 1, 2 ORDER BY MAX(test_time) DESC
    """, (lo, hi))
    keyed = {(r["work_order"], r["product_model"]): statuses.get((r["work_order"], r["product_model"])) for r in rows}
    notes = wo_notes.notes_for({k: v for k, v in keyed.items() if v})
    out = []
    for r in rows:
        key = (r["work_order"], r["product_model"])
        st = keyed[key]
        units = int(r["units"])
        out.append({
            "work_order": r["work_order"], "product_model": r["product_model"], "units": units,
            "pass": int(r["pass"]), "fail": int(r["fail"]),
            "yield_pct": round(int(r["pass"]) * 100.0 / units, 2) if units else None,
            "first_test": r["first_test"], "last_test": r["last_test"],
            "status": {**{k: st.get(k) for k in ("state", "light", "flags", "total", "yield_pct", "planned", "achieved_pct",
                                                 "uph", "uph_target")}, "note": notes.get(key)} if st else None,
        })
    return out


def _banner(units: int, wos: list, pending: dict, idle: dict, last_hour: dict, last_test) -> dict[str, Any]:
    reasons = []
    for w in wos:
        light = (w["status"] or {}).get("light")
        if light in ("bad", "warn"):
            reasons.append({"level": light, "code": "wo", "work_order": w["work_order"], "product_model": w["product_model"],
                            "note": w["status"].get("note"), "flags": w["status"].get("flags") or []})
    if pending["pending"]:
        reasons.append({"level": "warn", "code": "pending", "n": pending["pending"], "retest": pending["retest"]})
    if idle["light"] == "warn":
        reasons.append({"level": "warn", "code": "idle", "minutes": idle["minutes"]})
    if last_hour["light"] == "warn":
        reasons.append({"level": "warn", "code": "behind", "units": last_hour["units"], "target": last_hour["target"]})
    reasons.sort(key=lambda x: 0 if x["level"] == "bad" else 1)
    if not units:
        return {"light": "none", "reasons": reasons, "code": "no_tests", "last_test": last_test}
    light = "bad" if any(x["level"] == "bad" for x in reasons) else "warn" if reasons else "good"
    return {"light": light, "reasons": reasons, "code": "ok" if not reasons else "attention"}


def today(day_text: Optional[str] = None, now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or local_now(config.LOCAL_TZ)
    s = settings.get()
    lo = parse_day(day_text, now)
    hi = lo + timedelta(days=1)
    is_today = lo.date() == now.date()
    window = {"window_start": lo, "window_end": hi}
    summary = analytics.summary(window, None, None)

    prev_day = _neighbours(lo, hi)
    previous = None
    if prev_day["prev"]:
        p_lo = datetime.strptime(prev_day["prev"], "%Y-%m-%d")
        p = analytics.summary({"window_start": p_lo, "window_end": p_lo + timedelta(days=1)}, None, None)
        previous = {"day": prev_day["prev"], "total": p["total"], "yield_pct": p["yield_pct"]}

    statuses = wo_status.statuses()
    targets = _targets(statuses)
    wos = _work_orders(lo, hi, statuses)
    main = max(wos, key=lambda w: w["units"], default=None)      # the day's main work order: most units
    day_key = (main["work_order"], main["product_model"]) if main else None

    first_test, day_last = parse_dt(summary["first_test"]), parse_dt(summary["last_test"])
    ref = (now if is_today else day_last) if day_last else None
    last_hour = _last_hour(ref, targets, day_key, s, is_today, now, first_test)

    newest = parse_dt(db.rows("SELECT MAX(test_time) AS t FROM test_record")[0]["t"])
    idle = {"minutes": None, "light": "none"}
    if is_today and day_last:
        minutes = max(0, int((now - day_last).total_seconds() // 60))
        in_hours = _minutes(s["work_start"]) <= now.hour * 60 + now.minute <= _minutes(s["work_end"])
        idle = {"minutes": minutes, "light": ("warn" if minutes >= s["idle_warn_minutes"] else "good") if in_hours else "none"}

    pending = _pending(lo, hi)
    return {
        "day": lo.strftime("%Y-%m-%d"), "is_today": is_today, "now": now.isoformat(timespec="seconds"),
        "neighbours": prev_day,
        "settings": {k: s[k] for k in ("work_start", "work_end", "idle_warn_minutes", "behind_pct", "fail_streak")},
        "summary": summary, "previous": previous, "pending": pending, "last_hour": last_hour,
        "last_test": {"at": newest, "day_last": day_last, **idle},
        "work_orders": wos,
        "banner": _banner(summary["total"], wos, pending, idle, last_hour, newest),
        "hourly": _hourly(lo, hi, s, targets, day_key, is_today, now),
        "trend": _trend(hi), "trend_min_units": MIN_UNITS_YIELD,
        "fail_top": _fail_top(lo, hi),
        "fail_top_days": FAIL_TOP_DAYS,
        "monthly": analytics.monthly(lo.year, None, None),
    }
