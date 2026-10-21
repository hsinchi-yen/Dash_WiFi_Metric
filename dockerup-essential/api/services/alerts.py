"""Anomaly banner (5E, L8; Q22: shown on the dashboard only, no e-mail / chat). Rules only, no LLM.

alerts(now) → {"level", "items": [{code, level, values, link}]} about the last ALERT_HOURS of testing:
  wo_red        a work order tested in that time is red (yield below the amber threshold, or FAILs in a row)
  burst         ≥ Settings "FAILs in a row" units of one work order failed the same way within 60 minutes
  tp_drop       a work order's median throughput is below the product's usual by more than the Settings %
  retry_high    a work order retested more than RETRY_WARN % of its units
  behind        an active work order's last hour was below its hourly target (Settings "behind %")
  paused        a work order tested earlier today has had no test for longer than Settings "in progress" minutes,
                during working hours on a working day
  pending_fail  failed units of that time still waiting for a disposition
Every item links to the page that shows it (work-order detail, Fail List). `now` can be given to replay a past day.
"""
from datetime import datetime, timedelta
from typing import Any, Optional

import config
import db
from services import fail_actions, settings, wo_status
from services.analytics import PENDING_SQL
from services.filters import UNIT
from services.stats import RETRY_FILTER_SQL
from services.wo_rules import local_now, parse_dt

ALERT_HOURS = 24
RETRY_WARN = 5.0
MIN_UNITS_RETRY = 30
ORDER = {"bad": 0, "warn": 1, "info": 2}


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def alerts(now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or local_now(config.LOCAL_TZ)
    s = settings.get()
    since = now - timedelta(hours=ALERT_HOURS)
    items: list[dict[str, Any]] = []

    def add(code, level, link=None, **values):
        items.append({"code": code, "level": level, "values": values, "link": link})

    recent = db.rows(f"""SELECT work_order, product_model, COUNT(*) AS n, MAX(test_time) AS last,
                                COUNT(*) FILTER (WHERE {RETRY_FILTER_SQL}) AS retry
                         FROM {UNIT} WHERE test_time > %s AND test_time <= %s AND work_order IS NOT NULL AND work_order <> ''
                         GROUP BY 1, 2""", (since, now))
    statuses = wo_status.statuses() if recent else {}
    for r in recent:
        key = (r["work_order"], r["product_model"])
        st = statuses.get(key) or {}
        link = {"wo": key[0], "pm": key[1]}
        flags = {f["code"]: f for f in st.get("flags", [])}
        if st.get("light") == "bad":
            add("wo_red", "bad", link, wo=key[0], pm=key[1], flags=[f["code"] for f in st["flags"] if f["level"] == "bad"],
                yield_pct=st.get("yield_pct"), streak=st.get("max_fail_streak"))
        for band in ("24g", "5g"):
            f = flags.get(f"tp_low_{band}")
            if f:
                add("tp_drop", "warn", link, wo=key[0], band=band, **f["values"])
        if "behind_target" in flags:
            add("behind", "warn", link, wo=key[0], **flags["behind_target"]["values"])
        total = int(st.get("total") or 0)
        if total >= MIN_UNITS_RETRY:
            wo_retry = db.rows(f"SELECT COUNT(*) FILTER (WHERE {RETRY_FILTER_SQL}) AS r FROM {UNIT} WHERE work_order = %s AND product_model IS NOT DISTINCT FROM %s",
                               key)[0]["r"]
            pct = round(int(wo_retry) * 100.0 / total, 1)
            if pct > RETRY_WARN:
                add("retry_high", "warn", link, wo=key[0], pct=pct, threshold=RETRY_WARN)
        last = parse_dt(r["last"])
        idle = (now - last).total_seconds() / 60
        in_hours = now.isoweekday() in s["work_days"] and _minutes(s["work_start"]) <= now.hour * 60 + now.minute <= _minutes(s["work_end"])
        if in_hours and last.date() == now.date() and idle > s["active_minutes"]:
            add("paused", "info", link, wo=key[0], minutes=int(idle))

    # Bursts: same category, same work order, ≥ streak units within 60 minutes (the latest burst per work order and category).
    streak = int(s["fail_streak"])
    bursts = db.rows("""
        SELECT f.work_order, f.product_model, f.category, x.name_zh, x.name_en, MAX(f.test_time) AS last,
               (SELECT COUNT(DISTINCT g.sn) FROM fail_analysis g WHERE g.work_order = f.work_order AND g.category = f.category
                  AND g.test_time BETWEEN MAX(f.test_time) - make_interval(mins => %s) AND MAX(f.test_time)) AS units
        FROM fail_analysis f LEFT JOIN fail_taxonomy x ON x.code = f.category
        WHERE f.test_time > %s AND f.test_time <= %s AND f.category <> 'UNKNOWN'
        GROUP BY 1, 2, 3, 4, 5
    """, (fail_actions.CLUSTER_MINUTES, since, now))
    for b in bursts:
        if int(b["units"]) >= streak:
            add("burst", "warn", {"fails": True, "wo": b["work_order"], "pm": b["product_model"]}, wo=b["work_order"], category=b["category"],
                name_zh=b["name_zh"], name_en=b["name_en"], units=int(b["units"]), minutes=fail_actions.CLUSTER_MINUTES, last=b["last"])

    pending = int(db.rows(f"SELECT COUNT(*) AS n FROM {UNIT} WHERE result = FALSE AND test_time > %s AND test_time <= %s AND {PENDING_SQL}",
                          (since, now))[0]["n"])
    if pending:
        add("pending_fail", "info", {"fails": True}, n=pending)

    items.sort(key=lambda x: ORDER[x["level"]])
    level = items[0]["level"] if items else "none"
    return {"level": level, "items": items, "now": now.isoformat(timespec="seconds"), "hours": ALERT_HOURS}
