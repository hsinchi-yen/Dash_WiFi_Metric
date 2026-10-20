"""Public dashboard data. Signatures and responses are unchanged from the single-file
API (tests/golden.py); /api/overview is new and bundles what one page load needs."""
from typing import Any, Optional

from fastapi import APIRouter, Query

from services import stats

router = APIRouter(tags=["analytics"])

Scope = Query(default="latest_day", pattern="^(latest_day|year)$")
Year = Query(default=None, ge=2000, le=2100)


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


@router.get("/api/overview")
def overview(work_order: Optional[str] = None, product_model: Optional[str] = None,
             scope: str = Query(default="year", pattern="^(latest_day|year)$"), year: Optional[int] = Year,
             limit: int = Query(default=200, ge=1, le=500)) -> dict[str, Any]:
    """Everything the dashboard page loads, in one response (replaces 15 parallel requests)."""
    wo = work_order if work_order and work_order != "ALL" else None
    pm = product_model if product_model and product_model != "ALL" else None
    return {
        "summary": stats.summary(wo, pm, scope, year),
        "split": stats.pass_fail_split(wo, pm, scope, year),
        "trend_hour": stats.yield_trend(wo, pm, "latest_day", None),
        "trend_month": stats.yield_trend(wo, pm, "year", year),
        "throughput": stats.throughput_distribution(wo, pm, scope, year),
        "latest_work_order": stats.latest_workorder_hourly(wo, pm, 2),
        "years": stats.available_years(wo, pm),
        "products": stats.products(limit, scope, year),
        "work_orders": stats.work_orders(limit, pm, scope, year),
        "fails": stats.fails(300, pm),
        "retries": stats.retries(300, pm),
        "rf": {
            "window": stats.rf_window(wo, pm),
            "trend": stats.rf_throughput_trend(wo, pm),
            "percentiles": stats.rf_percentiles(wo, pm),
            "stddev": stats.rf_stddev(wo, pm, 20),
        },
        "refreshed_at": stats.refreshed_at(),
    }
