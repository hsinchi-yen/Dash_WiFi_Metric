"""Range-scoped statistics for the web/ front end (filter bar: date range + work order +
product). Everything reads mv_unit, so a full page load is a handful of millisecond queries.

The legacy endpoints (services.stats, golden-guarded) are untouched; these functions are the
data behind /api/overview, /api/work-order-summary, /api/fail-list, /api/rf and
/api/filter-options.
"""
import math
from datetime import datetime, timedelta
from typing import Any, Optional

import db
from services.filters import UNIT, range_where, resolve_range_window
from services.stats import FAIL_REASON_SQL, RETRY_FILTER_SQL

HOUR_BUCKET_MAX_DAYS = 2      # ≤ 2 days of data → hourly trend, otherwise daily
STDDEV_TOP = 20


def _num(value, digits=2):
    """Decimal/None → float rounded (the web front end wants plain numbers)."""
    return None if value is None else round(float(value), digits)


def _bounds(window: dict, work_order, product_model) -> Optional[tuple[datetime, datetime]]:
    """Half-open time bounds to draw a trend over: the window, or the data's own extent."""
    if window.get("window_start") is not None and window.get("window_end") is not None:
        return window["window_start"], window["window_end"]
    where, params = range_where(window, work_order, product_model)
    row = db.rows(f"SELECT MIN(test_time) AS lo, MAX(test_time) AS hi FROM {UNIT} {where}", tuple(params))
    if not row or not row[0]["lo"]:
        return None
    lo = datetime.fromisoformat(row[0]["lo"]).replace(hour=0, minute=0, second=0, microsecond=0)
    hi = datetime.fromisoformat(row[0]["hi"]).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return lo, hi


# ─── Overview ────────────────────────────────────────────────────────────────

def summary(window: dict, work_order, product_model) -> dict[str, Any]:
    where, params = range_where(window, work_order, product_model)
    row = db.rows(f"""
    SELECT
      COUNT(*) AS total,
      COUNT(*) FILTER (WHERE result = TRUE) AS pass,
      COUNT(*) FILTER (WHERE result = FALSE) AS fail,
      COUNT(*) FILTER (WHERE {RETRY_FILTER_SQL}) AS retry_units,
      AVG(avg_24g) AS avg_24g,
      AVG(avg_5g) AS avg_5g,
      MIN(test_time) AS first_test,
      MAX(test_time) AS last_test
    FROM {UNIT}
    {where}
    """, tuple(params))[0]
    runs_where, runs_params = range_where(window, work_order, product_model)
    runs = db.rows(f"SELECT COUNT(*) AS n FROM test_record {runs_where}", tuple(runs_params))[0]["n"]
    total = int(row["total"] or 0)
    return {
        "total": total,
        "pass": int(row["pass"] or 0),
        "fail": int(row["fail"] or 0),
        "yield_pct": _num(row["pass"] * 100.0 / total) if total else None,
        "retry_units": int(row["retry_units"] or 0),
        "retry_rate": _num(row["retry_units"] * 100.0 / total) if total else None,
        "avg_24g": _num(row["avg_24g"]),
        "avg_5g": _num(row["avg_5g"]),
        "test_runs": int(runs or 0),
        "first_test": row["first_test"],
        "last_test": row["last_test"],
    }


def trend(window: dict, work_order, product_model) -> dict[str, Any]:
    bounds = _bounds(window, work_order, product_model)
    if not bounds:
        return {"unit": "day", "points": []}
    lo, hi = bounds
    unit = "hour" if (hi - lo) <= timedelta(days=HOUR_BUCKET_MAX_DAYS) else "day"
    where, params = range_where(window, work_order, product_model)
    rows = db.rows(f"""
    WITH buckets AS (
      SELECT generate_series(%s::timestamp, %s::timestamp - INTERVAL '1 {unit}', INTERVAL '1 {unit}') AS b
    ),
    agg AS (
      SELECT date_trunc('{unit}', test_time) AS b,
             COUNT(*) AS total,
             COUNT(*) FILTER (WHERE result = TRUE) AS pass
      FROM {UNIT}
      {where}
      GROUP BY 1
    )
    SELECT buckets.b AS t, COALESCE(agg.total, 0) AS total, agg.pass
    FROM buckets LEFT JOIN agg ON agg.b = buckets.b
    ORDER BY buckets.b
    """, (lo, hi, *params))
    points = []
    for r in rows:
        total = int(r["total"] or 0)
        points.append({"t": r["t"], "total": total,
                       "yield_pct": _num(int(r["pass"] or 0) * 100.0 / total) if total else None})
    # Hourly view of a single day: drop the leading/trailing empty hours so the chart reads well.
    if unit == "hour":
        idx = [i for i, p in enumerate(points) if p["total"]]
        if idx:
            points = points[max(0, idx[0] - 1): idx[-1] + 2]
    return {"unit": unit, "points": points}


def monthly(year: Optional[int], work_order, product_model) -> dict[str, Any]:
    """PASS / FAIL units per month of one calendar year (all data in that year, scoped by
    work order / product only)."""
    if not year:
        row = db.rows(f"SELECT EXTRACT(YEAR FROM MAX(test_time))::int AS y FROM {UNIT}")[0]
        year = int(row["y"]) if row["y"] else datetime.now().year
    window = {"window_start": datetime(year, 1, 1), "window_end": datetime(year + 1, 1, 1)}
    where, params = range_where(window, work_order, product_model)
    rows = db.rows(f"""
    SELECT EXTRACT(MONTH FROM test_time)::int AS month,
           COUNT(*) FILTER (WHERE result = TRUE) AS passed,
           COUNT(*) FILTER (WHERE result = FALSE) AS failed
    FROM {UNIT} {where}
    GROUP BY 1
    """, tuple(params))
    by_month = {int(r["month"]): r for r in rows}
    months = []
    for m in range(1, 13):
        r = by_month.get(m, {})
        passed, failed = int(r.get("passed") or 0), int(r.get("failed") or 0)
        months.append({"month": m, "passed": passed, "failed": failed, "total": passed + failed})
    return {"year": year, "months": months}


def throughput_bands(window: dict, work_order, product_model) -> dict[str, Any]:
    out = {}
    for key, col, width in (("bands_24g", "avg_24g", 10), ("bands_5g", "avg_5g", 50)):
        where, params = range_where(window, work_order, product_model, f"{col} IS NOT NULL")
        rows = db.rows(f"""
        SELECT FLOOR({col} / {width}) * {width} AS bin_start, COUNT(*) AS cnt
        FROM {UNIT} {where}
        GROUP BY 1 ORDER BY 1
        """, tuple(params))
        out[key] = [{"bin": f"{int(r['bin_start'])}-{int(r['bin_start']) + width}", "lo": int(r["bin_start"]),
                     "count": int(r["cnt"])} for r in rows]
    return out


def overview(date_from, date_to, work_order, product_model) -> dict[str, Any]:
    window = resolve_range_window(date_from, date_to)
    s = summary(window, work_order, product_model)
    year = window["window_end"].year if window.get("window_end") else None
    if year is None and s["last_test"]:
        year = datetime.fromisoformat(s["last_test"]).year
    return {
        "window": {k: window[k] for k in ("scope", "date_from", "date_to", "window_label")},
        "summary": s,
        "split": {"pass": s["pass"], "fail": s["fail"]},
        "trend": trend(window, work_order, product_model) if s["total"] else {"unit": "day", "points": []},
        "monthly": monthly(year, work_order, product_model),
        "throughput": throughput_bands(window, work_order, product_model),
        "plan": None,   # planned quantity vs tested: added with the uploader (wo_plan)
    }


# ─── Work orders ─────────────────────────────────────────────────────────────

def work_order_summary(date_from, date_to, work_order, product_model) -> list[dict[str, Any]]:
    window = resolve_range_window(date_from, date_to)
    where, params = range_where(window, work_order, product_model, "work_order IS NOT NULL AND work_order <> ''")
    rows = db.rows(f"""
    SELECT
      work_order,
      product_model,
      MIN(test_time) AS start_time,
      MAX(test_time) AS end_time,
      COUNT(*) AS total,
      COUNT(*) FILTER (WHERE result = TRUE) AS passed,
      COUNT(*) FILTER (WHERE result = FALSE) AS failed,
      COUNT(*) FILTER (WHERE {RETRY_FILTER_SQL}) AS retry_units,
      AVG(avg_24g) AS avg_24g,
      AVG(avg_5g) AS avg_5g,
      EXTRACT(EPOCH FROM (MAX(test_time) - MIN(test_time))) AS duration_sec
    FROM {UNIT}
    {where}
    GROUP BY work_order, product_model
    ORDER BY MAX(test_time) DESC
    """, tuple(params))
    out = []
    for r in rows:
        total = int(r["total"] or 0)
        duration = float(r["duration_sec"] or 0)
        hours = max(duration / 3600.0, 1 / 60)          # at least one minute, as the old page did
        out.append({
            "work_order": r["work_order"],
            "product_model": r["product_model"],
            "start_time": r["start_time"],
            "end_time": r["end_time"],
            "total": total,
            "passed": int(r["passed"] or 0),
            "failed": int(r["failed"] or 0),
            "yield_pct": _num(r["passed"] * 100.0 / total) if total else None,
            "retry_units": int(r["retry_units"] or 0),
            "retry_rate": _num(r["retry_units"] * 100.0 / total) if total else None,
            "avg_24g": _num(r["avg_24g"]),
            "avg_5g": _num(r["avg_5g"]),
            "duration_sec": round(duration),
            "units_per_hour": _num(total / hours, 1) if total else None,
            "min_per_unit": _num(hours * 60 / total) if total else None,
        })
    return out


def filter_options() -> dict[str, Any]:
    """Work orders (newest first) and products for the filter bar; index 0 is the latest work order."""
    work_orders = db.rows(f"""
    SELECT work_order, product_model, MIN(test_time)::date AS first_date, MAX(test_time)::date AS last_date,
           COUNT(*) AS units
    FROM {UNIT}
    WHERE work_order IS NOT NULL AND work_order <> ''
    GROUP BY work_order, product_model
    ORDER BY MAX(test_time) DESC
    """)
    products = db.rows(f"""
    SELECT product_model, COUNT(*) AS units, MAX(test_time)::date AS last_date
    FROM {UNIT}
    WHERE product_model IS NOT NULL AND product_model <> ''
    GROUP BY product_model
    ORDER BY MAX(test_time) DESC
    """)
    for r in work_orders + products:
        for k in ("first_date", "last_date"):
            if r.get(k) is not None and not isinstance(r[k], str):
                r[k] = r[k].isoformat()
        r["units"] = int(r["units"])
    return {"work_orders": work_orders, "products": products,
            "latest_work_order": work_orders[0]["work_order"] if work_orders else None}


# ─── Fail list (paged) ───────────────────────────────────────────────────────

def fail_list(date_from, date_to, work_order, product_model, search: Optional[str],
              page: int, page_size: int) -> dict[str, Any]:
    window = resolve_range_window(date_from, date_to)
    where, params = range_where(window, work_order, product_model, "result = FALSE")
    if search:
        where += " AND (sn ILIKE %s OR wifi_mac ILIKE %s OR bt_mac ILIKE %s OR work_order ILIKE %s OR product_model ILIKE %s)"
        params += [f"%{search}%"] * 5
    total = int(db.rows(f"SELECT COUNT(*) AS n FROM {UNIT} {where}", tuple(params))[0]["n"])
    pages = max(1, math.ceil(total / page_size))
    page = min(max(1, page), pages)
    items = db.rows(f"""
    SELECT id, test_time, sn, wifi_mac, bt_mac, work_order, product_model, avg_24g, avg_5g,
           rssi_delta_24g, rssi_delta_5g, band_result_24g, band_result_5g, bt_result, sn_runs,
           {FAIL_REASON_SQL} AS fail_reason
    FROM {UNIT}
    {where}
    ORDER BY test_time DESC, id DESC
    LIMIT %s OFFSET %s
    """, (*params, page_size, (page - 1) * page_size))
    for r in items:
        for k in ("avg_24g", "avg_5g", "rssi_delta_24g", "rssi_delta_5g"):
            r[k] = _num(r[k])
    return {"items": items, "total": total, "page": page, "pages": pages, "page_size": page_size}


def raw_log(record_id: int) -> Optional[dict[str, Any]]:
    rows = db.rows("SELECT id, source_file, raw_log FROM test_record WHERE id = %s", (record_id,))
    return rows[0] if rows else None


# ─── RF analysis ─────────────────────────────────────────────────────────────

def rf(date_from, date_to, work_order, product_model) -> dict[str, Any]:
    window = resolve_range_window(date_from, date_to)
    where, params = range_where(window, work_order, product_model)

    totals = db.rows(f"""
    SELECT
      COUNT(*) AS units,
      AVG(avg_24g) AS avg_24g, AVG(avg_5g) AS avg_5g,
      PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY avg_24g) AS p50_24g,
      PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY avg_24g) AS p5_24g,
      PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY avg_5g) AS p50_5g,
      PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY avg_5g) AS p5_5g,
      AVG(std_24g) AS std_24g, AVG(std_5g) AS std_5g,
      AVG(rssi_before_24g) AS rssi_before_24g, AVG(rssi_after_24g) AS rssi_after_24g, AVG(rssi_delta_24g) AS rssi_delta_24g,
      AVG(rssi_before_5g) AS rssi_before_5g, AVG(rssi_after_5g) AS rssi_after_5g, AVG(rssi_delta_5g) AS rssi_delta_5g,
      COUNT(*) FILTER (WHERE band_result_24g = FALSE) AS fail_24g,
      COUNT(*) FILTER (WHERE band_result_5g = FALSE) AS fail_5g,
      COUNT(*) FILTER (WHERE rssi_delta_5g <= -20) AS degraded_5g,
      COUNT(*) FILTER (WHERE avg_24g IS NULL) AS missing_24g,
      COUNT(*) FILTER (WHERE avg_5g IS NULL) AS missing_5g,
      AVG(bt_avg_latency) AS bt_avg_latency, MIN(bt_min_latency) AS bt_min_latency, MAX(bt_max_latency) AS bt_max_latency,
      AVG(bt_loss_rate) AS bt_loss_rate,
      COUNT(*) FILTER (WHERE bt_result = FALSE) AS bt_fail,
      COUNT(*) FILTER (WHERE bt_avg_latency IS NULL) AS bt_missing
    FROM {UNIT} {where}
    """, tuple(params))[0]

    bounds = _bounds(window, work_order, product_model)
    unit, points = "day", []
    if bounds:
        lo, hi = bounds
        unit = "hour" if (hi - lo) <= timedelta(days=HOUR_BUCKET_MAX_DAYS) else "day"
        rows = db.rows(f"""
        WITH buckets AS (
          SELECT generate_series(%s::timestamp, %s::timestamp - INTERVAL '1 {unit}', INTERVAL '1 {unit}') AS b
        ),
        agg AS (
          SELECT date_trunc('{unit}', test_time) AS b, COUNT(*) AS units,
                 AVG(avg_24g) AS avg_24g, AVG(avg_5g) AS avg_5g,
                 PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY avg_5g) AS p5_5g,
                 PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY avg_24g) AS p5_24g
          FROM {UNIT} {where}
          GROUP BY 1
        )
        SELECT buckets.b AS t, COALESCE(agg.units, 0) AS units, agg.avg_24g, agg.avg_5g, agg.p5_5g, agg.p5_24g
        FROM buckets LEFT JOIN agg ON agg.b = buckets.b
        ORDER BY buckets.b
        """, (lo, hi, *params))
        points = [{"t": r["t"], "units": int(r["units"] or 0), "avg_24g": _num(r["avg_24g"]), "avg_5g": _num(r["avg_5g"]),
                   "p5_24g": _num(r["p5_24g"]), "p5_5g": _num(r["p5_5g"])} for r in rows]
        if unit == "hour":
            idx = [i for i, p in enumerate(points) if p["units"]]
            if idx:
                points = points[max(0, idx[0] - 1): idx[-1] + 2]

    std_where, std_params = range_where(window, work_order, product_model, "std_5g IS NOT NULL")
    stddev_top = db.rows(f"""
    SELECT sn, work_order, product_model, test_time, avg_5g, std_5g, std_24g, rssi_delta_5g
    FROM {UNIT} {std_where}
    ORDER BY std_5g DESC, test_time DESC, id DESC
    LIMIT {STDDEV_TOP}
    """, tuple(std_params))
    for r in stddev_top:
        for k in ("avg_5g", "std_5g", "std_24g", "rssi_delta_5g"):
            r[k] = _num(r[k])

    bt_where, bt_params = range_where(window, work_order, product_model, "bt_avg_latency IS NOT NULL")
    bt_bands = db.rows(f"""
    SELECT FLOOR(bt_avg_latency / 5) * 5 AS bin_start, COUNT(*) AS cnt
    FROM {UNIT} {bt_where}
    GROUP BY 1 ORDER BY 1
    """, tuple(bt_params))

    def band(prefix):
        return {
            "avg": _num(totals[f"avg_{prefix}"]), "p50": _num(totals[f"p50_{prefix}"]), "p5": _num(totals[f"p5_{prefix}"]),
            "std": _num(totals[f"std_{prefix}"]),
            "rssi_before": _num(totals[f"rssi_before_{prefix}"]), "rssi_after": _num(totals[f"rssi_after_{prefix}"]),
            "rssi_delta": _num(totals[f"rssi_delta_{prefix}"]),
            "fail": int(totals[f"fail_{prefix}"] or 0), "missing": int(totals[f"missing_{prefix}"] or 0),
        }

    return {
        "window": {k: window[k] for k in ("scope", "date_from", "date_to", "window_label")},
        "units": int(totals["units"] or 0),
        "band_24g": band("24g"),
        "band_5g": {**band("5g"), "degraded": int(totals["degraded_5g"] or 0)},
        "trend": {"unit": unit, "points": points},
        **throughput_bands(window, work_order, product_model),
        "stddev_top": stddev_top,
        "bt": {
            "avg_latency": _num(totals["bt_avg_latency"]), "min_latency": _num(totals["bt_min_latency"]),
            "max_latency": _num(totals["bt_max_latency"]), "avg_loss_rate": _num(totals["bt_loss_rate"]),
            "fail": int(totals["bt_fail"] or 0), "missing": int(totals["bt_missing"] or 0),
            "latency_bands": [{"bin": f"{int(r['bin_start'])}-{int(r['bin_start']) + 5}", "lo": int(r["bin_start"]),
                               "count": int(r["cnt"])} for r in bt_bands],
        },
    }
