"""Time windows the dashboard queries use.

Both resolvers keep the exact semantics of the original single-file API:
  - dashboard window: `scope=year` is the calendar year; `scope=latest_day` is 07:00–19:00
    of the latest test day (within the optional work order / product filter).
  - RF window: the latest test day not after today, 00:00–24:00, plus the newest work order
    tested that day.
"""
from datetime import datetime, timedelta
from typing import Any, Optional

import db

UNIT = "mv_unit"


def scoped(work_order: Optional[str], product_model: Optional[str], where: str = "", params: Optional[list] = None):
    """Append the optional work order / product filters to a WHERE clause."""
    params = list(params or [])
    if work_order and work_order != "ALL":
        where += (" AND" if where else "WHERE") + " work_order = %s"
        params.append(work_order)
    if product_model and product_model != "ALL":
        where += (" AND" if where else "WHERE") + " product_model = %s"
        params.append(product_model)
    return where, params


def window_where(window: dict, work_order: Optional[str], product_model: Optional[str]):
    return scoped(work_order, product_model, "WHERE test_time >= %s AND test_time < %s",
                  [window["window_start"], window["window_end"]])


def _as_datetime(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value if isinstance(value, datetime) else None


def resolve_dashboard_window(scope: str, year: Optional[int], work_order: Optional[str] = None,
                             product_model: Optional[str] = None) -> Optional[dict[str, Any]]:
    if scope == "year":
        target_year = int(year or datetime.now().year)
        return {
            "scope": "year",
            "year": target_year,
            "day": None,
            "window_start": datetime(target_year, 1, 1),
            "window_end": datetime(target_year + 1, 1, 1),
            "window_label": f"{target_year} cumulative",
        }

    where, params = scoped(work_order, product_model)
    rows = db.rows(f"SELECT date_trunc('day', MAX(test_time)) AS latest_day FROM {UNIT} {where}", tuple(params))
    latest_day = _as_datetime(rows[0].get("latest_day")) if rows else None
    if latest_day is None:
        return None
    return {
        "scope": "latest_day",
        "year": None,
        "day": latest_day.strftime("%Y-%m-%d"),
        "window_start": latest_day + timedelta(hours=7),
        "window_end": latest_day + timedelta(hours=19),
        "window_label": f"{latest_day.strftime('%Y-%m-%d')} 07:00-19:00",
    }


def resolve_rf_window(work_order: Optional[str] = None, product_model: Optional[str] = None) -> Optional[dict[str, Any]]:
    where, params = scoped(work_order, product_model, "WHERE test_time < date_trunc('day', NOW()) + INTERVAL '1 day'")
    day_rows = db.rows(f"""
        SELECT date_trunc('day', test_time) AS day_bucket,
               COUNT(DISTINCT date_trunc('hour', test_time)) AS hour_slots
        FROM {UNIT}
        {where}
        GROUP BY 1
        ORDER BY day_bucket DESC
        LIMIT 1
    """, tuple(params))
    if not day_rows:
        return None
    day_bucket = _as_datetime(day_rows[0]["day_bucket"])
    if day_bucket is None:
        return None

    window_start, window_end = day_bucket, day_bucket + timedelta(days=1)
    wo_where, wo_params = scoped(work_order, product_model, "WHERE test_time >= %s AND test_time < %s",
                                 [window_start, window_end])
    wo_rows = db.rows(f"""
        SELECT work_order
        FROM {UNIT}
        {wo_where}
          AND work_order IS NOT NULL
          AND work_order <> ''
        ORDER BY test_time DESC
        LIMIT 1
    """, tuple(wo_params))
    return {
        "work_order": wo_rows[0]["work_order"] if wo_rows else None,
        "day_bucket": day_bucket,
        "window_start": window_start,
        "window_end": window_end,
        "hour_slots": int(day_rows[0].get("hour_slots") or 0),
    }
