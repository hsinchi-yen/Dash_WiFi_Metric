"""Work-order status for every work order, from mv_unit / test_record / wo_plan (read only).

statuses() → {(work_order, product_model): status}  (wo_rules.evaluate on each)
product_uph() → {product: {"median": units per active hour, "work_orders": n}} for the Settings page

The status of a work order is about the whole work order (not the page's date filter).
Three queries (≈ 0.1 s); cached until the next view refresh, a settings change or
WO_STATUS_CACHE_SEC.
"""
import statistics
import threading
import time
from datetime import timedelta
from typing import Any, Optional

import config
import db
from services import settings, stats
from services.wo_rules import evaluate, local_now, parse_dt

MIN_UNITS_BASELINE = 20      # work orders smaller than this do not set a product's usual values

FACTS_SQL = """
SELECT work_order, product_model,
       COUNT(*) AS total,
       COUNT(*) FILTER (WHERE result = TRUE) AS passed,
       COUNT(*) FILTER (WHERE result = FALSE) AS failed,
       MIN(test_time) AS first_test, MAX(test_time) AS last_test,
       COUNT(DISTINCT date_trunc('hour', test_time)) AS active_hours,
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY avg_24g) AS p50_24g,
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY avg_5g) AS p50_5g
FROM mv_unit
WHERE work_order IS NOT NULL AND work_order <> ''
GROUP BY work_order, product_model
"""

# Consecutive FAIL runs (different units) in test order, per work order.
STREAK_SQL = """
WITH r AS (
  SELECT work_order, product_model, sn, result,
         ROW_NUMBER() OVER (PARTITION BY work_order, product_model ORDER BY test_time, id)
       - ROW_NUMBER() OVER (PARTITION BY work_order, product_model, result ORDER BY test_time, id) AS grp
  FROM test_record
  WHERE work_order IS NOT NULL AND work_order <> ''
)
SELECT work_order, product_model, MAX(units) AS max_fail_streak
FROM (SELECT work_order, product_model, grp, COUNT(DISTINCT sn) AS units
      FROM r WHERE result = FALSE GROUP BY 1, 2, 3) s
GROUP BY 1, 2
"""

RECENT_SQL = """
SELECT work_order, product_model, COUNT(*) AS n FROM mv_unit
WHERE test_time > %s AND test_time <= %s AND work_order IS NOT NULL AND work_order <> ''
GROUP BY 1, 2
"""

_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "at": 0.0, "value": None, "uph": None}


def _median_excluding(values: dict, key) -> Optional[float]:
    """The product's usual value: median over its other work orders (at least 2 of them)."""
    others = [v for k, v in values.items() if k != key and v is not None]
    return statistics.median(others) if len(others) >= 2 else None


def _compute(now) -> tuple[dict, dict]:
    s = settings.get()
    facts = {(r["work_order"], r["product_model"]): r for r in db.rows(FACTS_SQL)}
    streaks = {(r["work_order"], r["product_model"]): int(r["max_fail_streak"]) for r in db.rows(STREAK_SQL)}
    plans = {r["work_order"]: int(r["planned_qty"]) for r in db.rows("SELECT work_order, planned_qty FROM wo_plan")}

    by_product: dict[str, dict] = {}
    for key, f in facts.items():
        f["first_test"], f["last_test"] = parse_dt(f["first_test"]), parse_dt(f["last_test"])
        f["uph"] = f["total"] / f["active_hours"] if f["active_hours"] else None
        for k in ("p50_24g", "p50_5g"):
            f[k] = None if f[k] is None else float(f[k])
        if f["total"] >= MIN_UNITS_BASELINE:
            by_product.setdefault(f["product_model"], {})[key] = f

    uph_by_product = {}
    for product, wos in by_product.items():
        ups = [w["uph"] for w in wos.values() if w["uph"]]
        uph_by_product[product] = {"median": round(statistics.median(ups), 1) if ups else None, "work_orders": len(ups)}

    active_cut = now - timedelta(minutes=s["active_minutes"])
    recent = {}
    if any(f["last_test"] >= active_cut for f in facts.values()):
        recent = {(r["work_order"], r["product_model"]): int(r["n"])
                  for r in db.rows(RECENT_SQL, (now - timedelta(hours=1), now))}

    out = {}
    for key, f in facts.items():
        wo, product = key
        peers = by_product.get(product, {})
        f["max_fail_streak"] = streaks.get(key, 0)
        f["planned"] = plans.get(wo)
        f["recent_units"] = recent.get(key, 0)
        f["hist_p50_24g"] = _median_excluding({k: v["p50_24g"] for k, v in peers.items()}, key)
        f["hist_p50_5g"] = _median_excluding({k: v["p50_5g"] for k, v in peers.items()}, key)
        override = (s.get("uph_targets") or {}).get(product)
        if override:
            f["uph_target"], f["uph_target_source"] = float(override), "setting"
        else:
            hist = _median_excluding({k: v["uph"] for k, v in peers.items()}, key)
            f["uph_target"], f["uph_target_source"] = (hist, "history") if hist else (None, None)
        out[key] = evaluate(f, s, now)
    return out, uph_by_product


def _plan_version():
    """Changes whenever a planned quantity is set, cleared or undone (on either API worker)."""
    row = db.rows("SELECT COUNT(*) AS n, MAX(updated_at) AS at, MAX(admin_at) AS admin_at FROM wo_plan")[0]
    return (row["n"], row["at"], row["admin_at"])


def _ensure(now=None):
    now = now or local_now(config.LOCAL_TZ)
    key = (stats.refreshed_at(), settings.version(), _plan_version())
    with _lock:
        fresh = _cache["key"] == key and time.time() - _cache["at"] < config.WO_STATUS_CACHE_SEC
        if not fresh:
            value, uph = _compute(now)
            _cache.update(key=key, at=time.time(), value=value, uph=uph)
        return _cache["value"], _cache["uph"]


def statuses() -> dict:
    return _ensure()[0]


def cache_key():
    """Changes when the statuses are recomputed (new data, new settings): a key for derived caches."""
    _ensure()
    return (_cache["key"], _cache["at"])


def product_uph() -> dict:
    return _ensure()[1]


def invalidate():
    with _lock:
        _cache.update(key=None, at=0.0)
