"""Public dashboard data.

Two families:
- the original endpoints (/api/dashboard/*, /api/products, /api/workorders, /api/fails,
  /api/retries): signatures and responses unchanged (tests/golden.py), still used by the
  single-file dashboard;
- range-scoped endpoints for the web/ front end (/api/overview, /api/filter-options,
  /api/work-order-summary, /api/fail-list, /api/rf, /api/raw-log/{id}): date_from / date_to
  (inclusive ISO dates, both empty = all data) + work_order + product_model.
"""
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Query
from fastapi.responses import Response

from errors import not_found, Msg
from services import analytics, fail_classify, fail_disposition, forecast, stats, wo_notes, wo_status

router = APIRouter(tags=["analytics"])

Scope = Query(default="latest_day", pattern="^(latest_day|year)$")
Year = Query(default=None, ge=2000, le=2100)
# A new Query() per parameter: one shared Query object for date_from AND date_to makes FastAPI see a single
# parameter, so both received date_from (every date range shrank to its first day).
def iso_date():
    return Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


def _opt(value: Optional[str]) -> Optional[str]:
    return value if value and value != "ALL" else None


# ─── Original endpoints (golden-guarded) ─────────────────────────────────────

@router.get("/api/dashboard/summary")
def dashboard_summary(work_order: Optional[str] = None, product_model: Optional[str] = None,
                      scope: str = Scope, year: Optional[int] = Year) -> dict[str, Any]:
    return stats.summary(work_order, product_model, scope, year)


@router.get("/api/dashboard/yield-trend")
def yield_trend(bucket: str = Query(default="hour", pattern="^(hour|day|month)$"),
                work_order: Optional[str] = None, product_model: Optional[str] = None,
                scope: str = Scope, year: Optional[int] = Year) -> list[dict[str, Any]]:
    _ = bucket  # the bucket follows the scope (year → month, latest_day → hour), as before
    return stats.yield_trend(work_order, product_model, scope, year)


@router.get("/api/dashboard/pass-fail-split")
def pass_fail_split(work_order: Optional[str] = None, product_model: Optional[str] = None,
                    scope: str = Scope, year: Optional[int] = Year) -> dict[str, Any]:
    return stats.pass_fail_split(work_order, product_model, scope, year)


@router.get("/api/dashboard/throughput-distribution")
def throughput_distribution(work_order: Optional[str] = None, product_model: Optional[str] = None,
                            scope: str = Scope, year: Optional[int] = Year) -> dict[str, Any]:
    return stats.throughput_distribution(work_order, product_model, scope, year)


@router.get("/api/dashboard/available-years")
def available_years(work_order: Optional[str] = None, product_model: Optional[str] = None) -> dict[str, Any]:
    return stats.available_years(work_order, product_model)


@router.get("/api/dashboard/rf-window")
def rf_window(work_order: Optional[str] = None, product_model: Optional[str] = None) -> dict[str, Any]:
    return stats.rf_window(work_order, product_model)


@router.get("/api/dashboard/rf-throughput-trend")
def rf_throughput_trend(work_order: Optional[str] = None, product_model: Optional[str] = None) -> list[dict[str, Any]]:
    return stats.rf_throughput_trend(work_order, product_model)


@router.get("/api/dashboard/rf-percentiles")
def rf_percentiles(work_order: Optional[str] = None, product_model: Optional[str] = None) -> list[dict[str, Any]]:
    return stats.rf_percentiles(work_order, product_model)


@router.get("/api/dashboard/rf-stddev")
def rf_stddev(work_order: Optional[str] = None, product_model: Optional[str] = None,
              limit: int = Query(default=20, ge=1, le=200)) -> list[dict[str, Any]]:
    return stats.rf_stddev(work_order, product_model, limit)


@router.get("/api/dashboard/latest-workorder-hourly")
def latest_workorder_hourly(work_order: Optional[str] = None, product_model: Optional[str] = None,
                            lookback_days: int = Query(default=2, ge=1, le=2)) -> dict[str, Any]:
    return stats.latest_workorder_hourly(work_order, product_model, lookback_days)


@router.get("/api/products")
def products(limit: int = Query(default=200, ge=1, le=500), scope: str = Scope,
             year: Optional[int] = Year) -> list[dict[str, Any]]:
    return stats.products(limit, scope, year)


@router.get("/api/workorders")
def work_orders(limit: int = Query(default=200, ge=1, le=500), product_model: Optional[str] = None,
                scope: str = Scope, year: Optional[int] = Year) -> list[dict[str, Any]]:
    return stats.work_orders(limit, product_model, scope, year)


@router.get("/api/fails")
def fails(limit: int = Query(default=200, ge=1, le=1000), product_model: Optional[str] = None) -> list[dict[str, Any]]:
    return stats.fails(limit, product_model)


@router.get("/api/retries")
def retries(limit: int = Query(default=200, ge=1, le=1000), product_model: Optional[str] = None) -> list[dict[str, Any]]:
    return stats.retries(limit, product_model)


# ─── Range-scoped endpoints for the web/ front end ───────────────────────────

@router.get("/api/filter-options")
def filter_options() -> dict[str, Any]:
    return analytics.filter_options()


@router.get("/api/overview")
def overview(date_from: Optional[str] = iso_date(), date_to: Optional[str] = iso_date(),
             work_order: Optional[str] = None, product_model: Optional[str] = None) -> dict[str, Any]:
    """Everything the dashboard page shows, in one response."""
    data = analytics.overview(date_from, date_to, _opt(work_order), _opt(product_model))
    data["refreshed_at"] = stats.refreshed_at()
    return data


@router.get("/api/forecast")
def forecast_view(work_order: Optional[str] = Query(None, max_length=64), product_model: Optional[str] = Query(None, max_length=128)) -> dict[str, Any]:
    """The Trend page's Forecast view: a work order's completion, or this week's / month's output (rules, no LLM)."""
    data = forecast.forecast(_opt(work_order), _opt(product_model))
    data["refreshed_at"] = stats.refreshed_at()
    return data


@router.get("/api/work-order-summary")
def work_order_summary(date_from: Optional[str] = iso_date(), date_to: Optional[str] = iso_date(),
                       work_order: Optional[str] = None, product_model: Optional[str] = None) -> list[dict[str, Any]]:
    """One row per work order in the range. `status` (state, light, flags, plan, hourly rate, the one-line
    note) describes the whole work order, not just the part inside the range."""
    rows = analytics.work_order_summary(date_from, date_to, _opt(work_order), _opt(product_model))
    statuses = wo_status.statuses()
    picked = {(r["work_order"], r["product_model"]): statuses.get((r["work_order"], r["product_model"])) for r in rows}
    notes = wo_notes.notes_for({k: v for k, v in picked.items() if v})
    for r in rows:
        key = (r["work_order"], r["product_model"])
        r["status"] = {**picked[key], "note": notes.get(key)} if picked[key] else None
    return rows


@router.get("/api/fail-list")
def fail_list(date_from: Optional[str] = iso_date(), date_to: Optional[str] = iso_date(),
              work_order: Optional[str] = None, product_model: Optional[str] = None,
              search: Optional[str] = Query(default=None, max_length=64),
              page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=500),
              disposition: Optional[str] = Query(default=None, pattern="^(pending|handled)$")) -> dict[str, Any]:
    """Failed units (latest test FAIL) with their classification (5F), suggested next step and disposition (5C).
    `counts` = all / pending / handled in the scope and search, whatever `disposition` shows."""
    data = analytics.fail_list(date_from, date_to, _opt(work_order), _opt(product_model), search, page, page_size, disposition)
    found = fail_classify.for_records([r["id"] for r in data["items"]])
    for r in data["items"]:
        r["analysis"] = found.get(int(r["id"]))      # 5F classification of this FAIL run (None until classified)
    fail_disposition.decorate(data["items"], found)
    return data


@router.get("/api/rf")
def rf(date_from: Optional[str] = iso_date(), date_to: Optional[str] = iso_date(),
       work_order: Optional[str] = None, product_model: Optional[str] = None) -> dict[str, Any]:
    return analytics.rf(date_from, date_to, _opt(work_order), _opt(product_model))


@router.get("/api/raw-log/{record_id}")
def raw_log(record_id: int):
    """The original test log of one record, as a text download."""
    row = analytics.raw_log(record_id)
    if not row or not row.get("raw_log"):
        raise not_found(Msg(f"找不到 log：id={record_id}", f"Log not found: id={record_id}"))
    name = Path(str(row.get("source_file") or f"log_{record_id}.txt").replace("\\", "/")).name
    return Response(content=row["raw_log"], media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
