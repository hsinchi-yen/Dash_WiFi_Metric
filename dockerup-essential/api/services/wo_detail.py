"""Work-order detail (5B): one work order's status, extra numbers, plan, trend and the comparison with
the previous work order of the same product (or any other work order). Read only.

    find(wo, product)       → status key or None
    detail(wo, product)     → everything the detail page shows except the report and the FAIL list
    compare(key_a, key_b)   → metrics of both and the change from b to a
"""
from typing import Any, Optional

import db
from services import analytics, settings, wo_status
from services.filters import UNIT, resolve_range_window
from services.stats import FAIL_REASON_SQL, RETRY_FILTER_SQL
from services.wo_rules import parse_dt

# metric → (higher is better?, decimals); None = neither better nor worse
METRICS = {
    "total": (None, 0), "yield_pct": (True, 2), "failed": (False, 0), "retry_rate": (False, 2),
    "p50_24g": (True, 1), "p50_5g": (True, 1), "uph": (True, 1), "max_fail_streak": (False, 0),
    "active_hours": (None, 0), "duration_h": (None, 1),
}


def find(work_order: str, product_model: Optional[str]) -> Optional[tuple]:
    keys = [k for k in wo_status.statuses() if k[0] == work_order]
    if product_model:
        keys = [k for k in keys if (k[1] or "") == product_model]
    return keys[0] if len(keys) == 1 else None


def extra(key: tuple) -> dict[str, Any]:
    wo, product = key
    pm_sql, params = ("product_model = %s", [product]) if product else ("product_model IS NULL", [])
    row = db.rows(f"""
        SELECT COUNT(*) FILTER (WHERE {RETRY_FILTER_SQL}) AS retry_units,
               AVG(avg_24g) AS avg_24g, AVG(avg_5g) AS avg_5g,
               COUNT(DISTINCT date_trunc('hour', test_time)) AS active_hours
        FROM {UNIT} WHERE work_order = %s AND {pm_sql}
    """, (wo, *params))[0]
    runs = db.rows(f"SELECT COUNT(*) AS n FROM test_record WHERE work_order = %s AND {pm_sql}", (wo, *params))[0]["n"]
    reasons = db.rows(f"""
        SELECT {FAIL_REASON_SQL} AS reason, COUNT(*) AS units
        FROM {UNIT} WHERE work_order = %s AND {pm_sql} AND result = FALSE
        GROUP BY 1 ORDER BY 2 DESC, 1
    """, (wo, *params))
    return {
        "retry_units": int(row["retry_units"] or 0),
        "avg_24g": None if row["avg_24g"] is None else round(float(row["avg_24g"]), 1),
        "avg_5g": None if row["avg_5g"] is None else round(float(row["avg_5g"]), 1),
        "active_hours": int(row["active_hours"] or 0),
        "test_runs": int(runs or 0),
        "fail_reasons": [{"reason": r["reason"], "units": int(r["units"])} for r in reasons],
    }


def metrics(status: dict, ex: dict) -> dict[str, Any]:
    first, last = parse_dt(status.get("first_test")), parse_dt(status.get("last_test"))
    total = status.get("total") or 0
    return {
        "work_order": status["work_order"], "product_model": status.get("product_model"),
        "first_test": first.isoformat(sep=" ", timespec="minutes") if first else None,
        "last_test": last.isoformat(sep=" ", timespec="minutes") if last else None,
        "total": total, "yield_pct": status.get("yield_pct"), "failed": status.get("failed"),
        "retry_rate": round(ex["retry_units"] * 100.0 / total, 2) if total else None,
        "p50_24g": status.get("p50_24g"), "p50_5g": status.get("p50_5g"), "uph": status.get("uph"),
        "max_fail_streak": status.get("max_fail_streak"), "active_hours": ex["active_hours"],
        "duration_h": round((last - first).total_seconds() / 3600, 1) if first and last else None,
    }


def previous_key(key: tuple) -> Optional[tuple]:
    """The newest other work order of the same product that finished before this one's last test."""
    statuses = wo_status.statuses()
    me = statuses.get(key)
    if not me or not me.get("last_test"):
        return None
    mine = parse_dt(me["last_test"])
    peers = [(parse_dt(st["last_test"]), k) for k, st in statuses.items()
             if k != key and k[1] == key[1] and st.get("last_test") and parse_dt(st["last_test"]) < mine]
    return max(peers)[1] if peers else None


def _notable(name: str, diff: float, a_value: float, b_value: float, s: dict) -> bool:
    """A worse change big enough to report (the same limits as the status checks)."""
    if name == "yield_pct":
        return abs(diff) >= 1.0                                # percentage points
    if name == "retry_rate":
        return abs(diff) >= 2.0
    if name in ("p50_24g", "p50_5g"):
        return bool(b_value) and abs(diff) >= b_value * s["tp_drop_pct"] / 100
    if name == "uph":
        return bool(b_value) and abs(diff) >= b_value * s["behind_pct"] / 100
    if name == "max_fail_streak":
        return a_value >= s["fail_streak"]
    return False


def changes(a: dict, b: dict) -> dict[str, Any]:
    """a − b for every comparable metric (rounded): better / worse, and `notable` when a worse change passes
    the Settings limits (yield −1 point, retry +2 points, throughput / hourly rate down by the check %)."""
    s = settings.get()
    out = {}
    for name, (higher_better, digits) in METRICS.items():
        va, vb = a.get(name), b.get(name)
        if va is None or vb is None:
            continue
        diff = round(float(va) - float(vb), digits)
        if digits == 0:
            diff = int(diff)
        verdict = None if higher_better is None or diff == 0 else ("better" if (diff > 0) == higher_better else "worse")
        out[name] = {"diff": diff, "verdict": verdict,
                     "notable": verdict == "worse" and _notable(name, diff, float(va), float(vb), s)}
    return out


def compare(key_a: tuple, key_b: tuple) -> dict[str, Any]:
    statuses = wo_status.statuses()
    a = metrics(statuses[key_a], extra(key_a))
    b = metrics(statuses[key_b], extra(key_b))
    return {"a": a, "b": b, "changes": changes(a, b)}


def plan(work_order: str) -> Optional[dict]:
    rows = db.rows("""
        SELECT planned_qty, updated_by, updated_at, upload_id, admin_qty, admin_by, admin_at
        FROM wo_plan WHERE work_order = %s
    """, (work_order,))
    if not rows:
        return None
    r = rows[0]
    r["source"] = "admin" if r["upload_id"] is None else "upload"
    return r


def detail(key: tuple) -> dict[str, Any]:
    statuses = wo_status.statuses()
    st = statuses[key]
    ex = extra(key)
    wo, product = key
    window = resolve_range_window(None, None)
    prev = previous_key(key)
    mine = metrics(st, ex)
    out = {
        "status": st, "extra": ex, "metrics": mine, "plan": plan(wo),
        "trend": analytics.trend(window, wo, product),
        "throughput": analytics.throughput_bands(window, wo, product),
        "previous": None,
        "others": sorted(({"work_order": k[0], "product_model": k[1], "last_test": s.get("last_test")}
                          for k, s in statuses.items() if k != key),
                         key=lambda r: (r["product_model"] != product, -(parse_dt(r["last_test"]).timestamp() if r["last_test"] else 0))),
    }
    if prev:
        pm = metrics(statuses[prev], extra(prev))
        out["previous"] = {"metrics": pm, "changes": changes(mine, pm)}
    return out
