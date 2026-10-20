"""Dashboard statistics, all read from mv_unit (one row per SN: its latest test).

Every function returns exactly what the corresponding endpoint returned before the
refactor (tests/golden.py guards that); only the data source changed from a per-request
window sort over test_record to the materialized view.
"""
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import config
import db
from services import filters
from services.filters import UNIT, resolve_dashboard_window, resolve_rf_window
from services.migrations import REFRESHED_AT_KEY, meta_set, now_iso

log = logging.getLogger("wifi.stats")

FAIL_REASON_SQL = """
      CASE
        WHEN COALESCE(band_result_5g, TRUE) = FALSE THEN '5G check failed'
        WHEN COALESCE(band_result_24g, TRUE) = FALSE THEN '2.4G check failed'
        WHEN COALESCE(bt_result, TRUE) = FALSE THEN 'BT ping failed'
        WHEN avg_5g IS NULL THEN 'Missing 5G throughput'
        WHEN avg_24g IS NULL THEN 'Missing 2.4G throughput'
        ELSE 'Final test failed'
      END"""

RETRY_FILTER_SQL = "sn_runs > 1 OR attempt_count_24g > 1 OR attempt_count_5g > 1"

EMPTY_SUMMARY = {
    "total_units": 0, "pass_units": 0, "fail_units": 0, "retry_units": 0,
    "avg_24g": 0, "avg_5g": 0, "retry_rate_pct": 0, "yield_pct": 0,
}


# ─── Summary numbers ─────────────────────────────────────────────────────────

def summary(work_order, product_model, scope, year) -> dict[str, Any]:
    window = resolve_dashboard_window(scope, year, work_order, product_model)
    if not window:
        return {**EMPTY_SUMMARY, "scope": scope, "year": year, "day": None,
                "window_start": None, "window_end": None, "window_label": "N/A"}
    where, params = filters.window_where(window, work_order, product_model)
    rows = db.rows(f"""
    SELECT
      COUNT(*) AS total_units,
      COUNT(*) FILTER (WHERE result = TRUE) AS pass_units,
      COUNT(*) FILTER (WHERE result = FALSE) AS fail_units,
      COUNT(*) FILTER (WHERE {RETRY_FILTER_SQL}) AS retry_units,
      ROUND(COALESCE(AVG(avg_24g), 0)::numeric, 2) AS avg_24g,
      ROUND(COALESCE(AVG(avg_5g), 0)::numeric, 2) AS avg_5g,
      ROUND(COALESCE(COUNT(*) FILTER (WHERE {RETRY_FILTER_SQL}) * 100.0 / NULLIF(COUNT(*), 0), 0), 2) AS retry_rate_pct,
      ROUND(COALESCE(COUNT(*) FILTER (WHERE result = TRUE) * 100.0 / NULLIF(COUNT(*), 0), 0), 2) AS yield_pct
    FROM {UNIT}
    {where}
    """, tuple(params))
    payload = rows[0] if rows else dict(EMPTY_SUMMARY)
    payload["scope"] = window["scope"]
    payload["year"] = window["year"]
    payload["day"] = window["day"]
    payload["window_start"] = window["window_start"].isoformat() if isinstance(window["window_start"], datetime) else None
    payload["window_end"] = window["window_end"].isoformat() if isinstance(window["window_end"], datetime) else None
    payload["window_label"] = window["window_label"]
    return payload


def pass_fail_split(work_order, product_model, scope, year) -> dict[str, Any]:
    window = resolve_dashboard_window(scope, year, work_order, product_model)
    if not window:
        return {"pass_units": 0, "fail_units": 0, "total_units": 0}
    where, params = filters.window_where(window, work_order, product_model)
    rows = db.rows(f"""
    SELECT
      COUNT(*) FILTER (WHERE result = TRUE) AS pass_units,
      COUNT(*) FILTER (WHERE result = FALSE) AS fail_units,
      COUNT(*) AS total_units
    FROM {UNIT}
    {where}
    """, tuple(params))
    return rows[0] if rows else {"pass_units": 0, "fail_units": 0, "total_units": 0}


def yield_trend(work_order, product_model, scope, year) -> list[dict[str, Any]]:
    window = resolve_dashboard_window(scope, year, work_order, product_model)
    if not window:
        return []
    if window["scope"] == "year":
        return db.rows(f"""
        WITH month_raw AS (
          SELECT
            date_trunc('month', test_time) AS month_bucket,
            COUNT(*) AS total_units,
            COUNT(*) FILTER (WHERE result = TRUE) AS pass_units
          FROM {UNIT}
          WHERE test_time >= %s
            AND test_time < %s
            AND (%s IS NULL OR %s = 'ALL' OR work_order = %s)
            AND (%s IS NULL OR %s = 'ALL' OR product_model = %s)
          GROUP BY 1
        ),
        bounds AS (
          SELECT MIN(month_bucket) AS first_month, MAX(month_bucket) AS last_month FROM month_raw
        ),
        months AS (
          SELECT generate_series(
            COALESCE((SELECT first_month FROM bounds), %s::timestamp),
            COALESCE((SELECT last_month FROM bounds), %s::timestamp),
            INTERVAL '1 month'
          ) AS month_bucket
        ),
        month_joined AS (
          SELECT m.month_bucket, COALESCE(r.total_units, 0) AS total_units, COALESCE(r.pass_units, 0) AS pass_units
          FROM months m
          LEFT JOIN month_raw r ON r.month_bucket = m.month_bucket
        )
        SELECT
          month_bucket AS bucket_time,
          ROUND(COALESCE(pass_units * 100.0 / NULLIF(total_units, 0), 0), 2) AS yield_pct,
          total_units
        FROM month_joined
        ORDER BY month_bucket
        """, (window["window_start"], window["window_end"],
              work_order, work_order, work_order, product_model, product_model, product_model,
              window["window_start"], window["window_start"]))

    return db.rows(f"""
    WITH hours AS (
      SELECT generate_series(%s::timestamp, %s::timestamp - INTERVAL '1 hour', INTERVAL '1 hour') AS hour_bucket
    ),
    hour_raw AS (
      SELECT
        date_trunc('hour', test_time) AS hour_bucket,
        COUNT(*) AS total_units,
        COUNT(*) FILTER (WHERE result = TRUE) AS pass_units
      FROM {UNIT}
      WHERE test_time >= %s
        AND test_time < %s
        AND (%s IS NULL OR %s = 'ALL' OR work_order = %s)
        AND (%s IS NULL OR %s = 'ALL' OR product_model = %s)
      GROUP BY 1
    )
    SELECT
      h.hour_bucket AS bucket_time,
      ROUND(COALESCE(r.pass_units * 100.0 / NULLIF(r.total_units, 0), 0), 2) AS yield_pct,
      COALESCE(r.total_units, 0) AS total_units
    FROM hours h
    LEFT JOIN hour_raw r ON r.hour_bucket = h.hour_bucket
    ORDER BY h.hour_bucket
    """, (window["window_start"], window["window_end"], window["window_start"], window["window_end"],
          work_order, work_order, work_order, product_model, product_model, product_model))


def throughput_distribution(work_order, product_model, scope, year) -> dict[str, Any]:
    window = resolve_dashboard_window(scope, year, work_order, product_model)
    if not window:
        return {"bands_24g": [], "bands_5g": []}
    where, params = filters.window_where(window, work_order, product_model)
    rows_24g = db.rows(f"""
    SELECT FLOOR(avg_24g / 10) * 10 AS bin_start, COUNT(*) AS cnt
    FROM {UNIT}
    {where} AND avg_24g IS NOT NULL
    GROUP BY 1
    ORDER BY 1
    """, tuple(params))
    rows_5g = db.rows(f"""
    SELECT FLOOR(avg_5g / 50) * 50 AS bin_start, COUNT(*) AS cnt
    FROM {UNIT}
    {where} AND avg_5g IS NOT NULL
    GROUP BY 1
    ORDER BY 1
    """, tuple(params))
    return {
        "bands_24g": [{"bin": f"{int(r['bin_start'])}-{int(r['bin_start']) + 10}", "count": r["cnt"]} for r in rows_24g],
        "bands_5g": [{"bin": f"{int(r['bin_start'])}-{int(r['bin_start']) + 50}", "count": r["cnt"]} for r in rows_5g],
    }


def available_years(work_order, product_model) -> dict[str, Any]:
    where, params = filters.scoped(work_order, product_model)
    rows = db.rows(f"""
    SELECT DISTINCT EXTRACT(YEAR FROM test_time)::int AS year
    FROM {UNIT}
    {where}
    ORDER BY year DESC
    """, tuple(params))
    years = [int(r["year"]) for r in rows if r.get("year") is not None]
    now_year = datetime.now().year
    return {"years": years or [now_year], "current_year": now_year}


# ─── RF analysis (latest test day) ───────────────────────────────────────────

def rf_window(work_order, product_model) -> dict[str, Any]:
    info = resolve_rf_window(work_order, product_model)
    if not info:
        return {"work_order": None, "day": None, "window_start": None, "window_end": None,
                "hour_slots": 0, "window_label": "N/A"}
    day_text = info["day_bucket"].strftime("%Y-%m-%d") if isinstance(info["day_bucket"], datetime) else str(info["day_bucket"])
    slots = int(info.get("hour_slots") or 0)
    return {
        "work_order": info.get("work_order") or "N/A",
        "day": day_text,
        "window_start": info["window_start"],
        "window_end": info["window_end"],
        "hour_slots": slots,
        "window_label": f"RF day {day_text} | 00:00-24:00 | {slots}h",
    }


def _rf_hourly(work_order, product_model, select_sql: str, extra_where: str, columns=()):
    """Hour-by-hour aggregate over the RF day; every hour is present (0 where no tests)."""
    info = resolve_rf_window(work_order, product_model)
    if not info:
        return []
    where, params = filters.scoped(work_order, product_model,
                                   "WHERE test_time >= %s AND test_time < %s" + extra_where,
                                   [info["window_start"], info["window_end"]])
    return db.rows(f"""
    WITH hours AS (
      SELECT generate_series(%s::timestamp, %s::timestamp - INTERVAL '1 hour', INTERVAL '1 hour') AS hour_bucket
    ),
    agg AS (
      SELECT date_trunc('hour', test_time) AS bucket_time, {select_sql}
      FROM {UNIT}
      {where}
      GROUP BY 1
    )
    SELECT h.hour_bucket AS bucket_time, {", ".join(f"COALESCE(a.{c}, 0) AS {c}" for c in columns)}
    FROM hours h
    LEFT JOIN agg a ON a.bucket_time = h.hour_bucket
    ORDER BY h.hour_bucket
    """, (info["window_start"], info["window_end"], *params))


def rf_throughput_trend(work_order, product_model) -> list[dict[str, Any]]:
    return _rf_hourly(work_order, product_model,
                      "ROUND(AVG(avg_24g)::numeric, 2) AS avg_24g, ROUND(AVG(avg_5g)::numeric, 2) AS avg_5g",
                      "", ("avg_24g", "avg_5g"))


def rf_percentiles(work_order, product_model) -> list[dict[str, Any]]:
    return _rf_hourly(work_order, product_model,
                      "ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY avg_5g)::numeric, 2) AS p50_5g, "
                      "ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY avg_5g)::numeric, 2) AS p90_5g, "
                      "ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY avg_5g)::numeric, 2) AS p95_5g",
                      " AND avg_5g IS NOT NULL", ("p50_5g", "p90_5g", "p95_5g"))


def rf_stddev(work_order, product_model, limit: int) -> list[dict[str, Any]]:
    info = resolve_rf_window(work_order, product_model)
    if not info:
        return []
    where, params = filters.scoped(work_order, product_model,
                                   "WHERE std_5g IS NOT NULL AND test_time >= %s AND test_time < %s",
                                   [info["window_start"], info["window_end"]])
    # `, id DESC` makes the tie order deterministic (the original ORDER BY left it to the planner).
    return db.rows(f"""
    SELECT sn, work_order, ROUND(COALESCE(std_5g, 0)::numeric, 2) AS std_5g
    FROM {UNIT}
    {where}
    ORDER BY std_5g DESC, test_time DESC, id DESC
    LIMIT %s
    """, (*params, limit))


# ─── Latest work order, hour by hour ─────────────────────────────────────────

def latest_workorder_hourly(work_order, product_model, lookback_days: int) -> dict[str, Any]:
    if work_order and work_order != "ALL":
        where, params = filters.scoped(None, product_model, "WHERE work_order = %s", [work_order])
    else:
        where, params = filters.scoped(None, product_model, "WHERE work_order IS NOT NULL AND work_order <> ''")
    target_rows = db.rows(f"""
    SELECT work_order, product_model, test_time AS ref_time
    FROM {UNIT}
    {where}
    ORDER BY test_time DESC, id DESC
    LIMIT 1
    """, tuple(params))
    if not target_rows:
        return {"work_order": None, "product_model": None, "window_start": None, "window_end": None, "hourly": []}

    target_wo = target_rows[0]["work_order"]
    target_model = target_rows[0].get("product_model")
    day_where, day_params = filters.scoped(None, product_model, "WHERE work_order = %s", [target_wo])
    day_rows = db.rows(f"""
    SELECT DISTINCT date_trunc('day', test_time) AS day_bucket
    FROM {UNIT}
    {day_where}
    ORDER BY day_bucket DESC
    LIMIT %s
    """, (*day_params, lookback_days))
    if not day_rows:
        return {"work_order": target_wo, "product_model": target_model, "window_start": None, "window_end": None,
                "lookback_days": lookback_days, "days": [], "hourly": []}

    day_values = [row["day_bucket"] for row in day_rows]
    hourly_model_where = ""
    hourly_params: list[Any] = [day_values, target_wo]
    if product_model and product_model != "ALL":
        hourly_model_where = " AND l.product_model = %s"
        hourly_params.append(product_model)
    hourly_rows = db.rows(f"""
    WITH day_values AS (
      SELECT unnest(%s::timestamp[]) AS day_bucket
    ),
    hours AS (
      SELECT dv.day_bucket,
             generate_series(dv.day_bucket, dv.day_bucket + INTERVAL '23 hours', INTERVAL '1 hour') AS hour_bucket
      FROM day_values dv
    )
    SELECT h.day_bucket, h.hour_bucket, COALESCE(COUNT(l.id), 0) AS units
    FROM hours h
    LEFT JOIN {UNIT} l
      ON date_trunc('hour', l.test_time) = h.hour_bucket
      AND l.work_order = %s{hourly_model_where}
    GROUP BY h.day_bucket, h.hour_bucket
    ORDER BY h.day_bucket DESC, h.hour_bucket
    """, tuple(hourly_params))

    daily_map: dict[str, list[dict[str, Any]]] = {}
    for row in hourly_rows:
        daily_map.setdefault(str(row["day_bucket"]), []).append({"hour": row["hour_bucket"], "units": row["units"]})
    daily_blocks = [{"date": str(day)[:10], "hourly": daily_map.get(str(day), [])} for day in day_values]
    return {
        "work_order": target_wo,
        "product_model": target_model,
        "window_start": day_values[-1],
        "window_end": day_values[0],
        "lookback_days": lookback_days,
        "days": daily_blocks,
        "hourly": daily_blocks[0]["hourly"] if daily_blocks else [],
    }


# ─── Lists ───────────────────────────────────────────────────────────────────

def products(limit: int, scope: str, year) -> list[dict[str, Any]]:
    window = resolve_dashboard_window(scope, year, None, None)
    if not window:
        return []
    return db.rows(f"""
    SELECT product_model, MAX(test_time) AS latest_test_time, COUNT(*) AS total
    FROM {UNIT}
    WHERE test_time >= %s
      AND test_time < %s
      AND product_model IS NOT NULL
      AND product_model <> ''
    GROUP BY product_model
    ORDER BY MAX(test_time) DESC
    LIMIT %s
    """, (window["window_start"], window["window_end"], limit))


def work_orders(limit: int, product_model, scope: str, year) -> list[dict[str, Any]]:
    window = resolve_dashboard_window(scope, year, None, product_model)
    if not window:
        return []
    where, params = filters.window_where(window, None, product_model)
    return db.rows(f"""
    WITH scoped AS (
      SELECT * FROM {UNIT} {where}
    )
    SELECT
      s.work_order,
      s.product_model,
      MIN(s.test_time) AS start_time,
      MAX(s.test_time) AS end_time,
      COUNT(*) AS total,
      COUNT(*) FILTER (WHERE s.result = TRUE) AS passed,
      COUNT(*) FILTER (WHERE s.result = FALSE) AS failed,
      ROUND(COALESCE(COUNT(*) FILTER (WHERE s.result = TRUE) * 100.0 / NULLIF(COUNT(*), 0), 0), 2) AS yield_pct,
      ROUND(COALESCE(AVG(s.avg_24g), 0)::numeric, 2) AS avg_24g,
      ROUND(COALESCE(AVG(s.avg_5g), 0)::numeric, 2) AS avg_5g,
      ROUND(COALESCE(COUNT(*) FILTER (WHERE s.sn_runs > 1 OR s.attempt_count_24g > 1 OR s.attempt_count_5g > 1)
                     * 100.0 / NULLIF(COUNT(*), 0), 0), 2) AS retry_rate_pct
    FROM scoped s
    GROUP BY s.work_order, s.product_model
    ORDER BY MAX(s.test_time) DESC
    LIMIT %s
    """, (*params, limit))


def fails(limit: int, product_model) -> list[dict[str, Any]]:
    where, params = filters.scoped(None, product_model, "WHERE result = FALSE")
    return db.rows(f"""
    SELECT
      test_time, sn, work_order, product_model, avg_24g, avg_5g,
      rssi_before_5g, rssi_after_5g, rssi_delta_5g, band_result_24g, band_result_5g, bt_result,
      {FAIL_REASON_SQL} AS fail_reason
    FROM {UNIT}
    {where}
    ORDER BY test_time DESC, id DESC
    LIMIT %s
    """, (*params, limit))


def retries(limit: int, product_model) -> list[dict[str, Any]]:
    where, params = filters.scoped(None, product_model,
                                   "WHERE GREATEST(sn_runs, attempt_count_24g, attempt_count_5g) > 1")
    return db.rows(f"""
    SELECT
      test_time, sn, work_order, product_model,
      GREATEST(sn_runs, attempt_count_24g, attempt_count_5g) AS attempts,
      avg_5g, rssi_delta_5g,
      CASE
        WHEN GREATEST(sn_runs, attempt_count_24g, attempt_count_5g) >= 3 THEN 'high'
        WHEN rssi_delta_5g <= -20 THEN 'high'
        WHEN GREATEST(sn_runs, attempt_count_24g, attempt_count_5g) = 2 THEN 'medium'
        ELSE 'low'
      END AS retry_risk
    FROM {UNIT}
    {where}
    ORDER BY test_time DESC, id DESC
    LIMIT %s
    """, (*params, limit))


def work_order_facts(wo: str, product_model) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]]]:
    """Numbers the AI summary is written from: totals and the top-3 failure reasons."""
    where, params = filters.scoped(None, product_model, "WHERE work_order = %s", [wo])
    stats_rows = db.rows(f"""
    SELECT
      COUNT(*) AS total,
      COUNT(*) FILTER (WHERE result = TRUE) AS passed,
      COUNT(*) FILTER (WHERE result = FALSE) AS failed,
      ROUND(COALESCE(COUNT(*) FILTER (WHERE result = TRUE) * 100.0 / NULLIF(COUNT(*), 0), 0), 2) AS yield_pct,
      ROUND(COALESCE(AVG(avg_24g), 0)::numeric, 2) AS avg_24g,
      ROUND(COALESCE(AVG(avg_5g), 0)::numeric, 2) AS avg_5g
    FROM {UNIT} {where}
    """, tuple(params))
    if not stats_rows or stats_rows[0]["total"] == 0:
        return None, []
    fail_rows = db.rows(f"""
    SELECT {FAIL_REASON_SQL} AS fail_reason, COUNT(*) AS count
    FROM {UNIT} {where} AND result = FALSE
    GROUP BY fail_reason
    ORDER BY count DESC
    LIMIT 3
    """, tuple(params))
    return stats_rows[0], fail_rows


# ─── mv_unit refresh ─────────────────────────────────────────────────────────

def refreshed_at(conn=None) -> Optional[str]:
    if conn is not None:
        row = db.fetch_one(conn, "SELECT value FROM app_meta WHERE key = %s", (REFRESHED_AT_KEY,))
    else:
        with db.db() as c:
            row = db.fetch_one(c, "SELECT value FROM app_meta WHERE key = %s", (REFRESHED_AT_KEY,))
    return row["value"] if row else None


def refresh(wait: bool = True) -> Optional[str]:
    """REFRESH mv_unit CONCURRENTLY (readers keep the old rows until it finishes). One
    refresh at a time across workers; with wait=False a refresh already in progress
    elsewhere is simply skipped."""
    conn = db.connect()
    try:
        conn.autocommit = True
        cur = conn.cursor()
        if wait:
            cur.execute("SELECT pg_advisory_lock(%s)", (config.STATS_REFRESH_LOCK_KEY,))
        else:
            cur.execute("SELECT pg_try_advisory_lock(%s) AS ok", (config.STATS_REFRESH_LOCK_KEY,))
            if not cur.fetchone()["ok"]:
                return refreshed_at(conn)
        try:
            t0 = time.perf_counter()
            cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_unit")
            at = now_iso()
            meta_set(cur, REFRESHED_AT_KEY, at)
            log.info("mv_unit refreshed in %.0f ms", (time.perf_counter() - t0) * 1000)
            return at
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (config.STATS_REFRESH_LOCK_KEY,))
    finally:
        conn.close()


def refreshed_age_sec() -> float:
    """Seconds since the last refresh (infinite when unknown)."""
    last = refreshed_at()
    if not last:
        return float("inf")
    return (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()


def refresh_if_due() -> None:
    if refreshed_age_sec() >= config.STATS_REFRESH_SEC:
        refresh(wait=False)
