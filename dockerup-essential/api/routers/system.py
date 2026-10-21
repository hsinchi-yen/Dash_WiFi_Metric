"""Health, metadata and the statistics refresh."""
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import config
import db
from errors import ApiError, Msg
from services import llm, settings, stats

router = APIRouter(tags=["system"])


@router.get("/health")
def health():
    """DB round-trip, view freshness, LLM status, version. 503 when the DB is unreachable."""
    t0 = time.perf_counter()
    try:
        with db.db() as conn:
            db.fetch_one(conn, "SELECT 1 AS ok")
            refreshed = stats.refreshed_at(conn)
    except Exception:
        return JSONResponse({"status": "error", "db": "unreachable", "version": config.APP_VERSION,
                             "detail": "Database unavailable"}, status_code=503)
    return {
        "status": "ok",
        "version": config.APP_VERSION,
        "db_ms": round((time.perf_counter() - t0) * 1000, 1),
        "stats_refreshed_at": refreshed,
        "llm": {k: v for k, v in llm.status().items() if k in ("connected", "model")},
    }


@router.get("/api/meta")
def meta():
    """What the web front end needs once per page load."""
    return {
        "version": config.APP_VERSION,
        "stats_refreshed_at": stats.refreshed_at(),
        "stats_refresh_sec": config.STATS_REFRESH_SEC,
        "thresholds": settings.thresholds(),
        "settings_version": settings.version(),
        "llm": {k: v for k, v in llm.status().items() if k in ("connected", "model")},
    }


MANUAL_REFRESH_MIN_SEC = 10


@router.post("/api/stats/refresh")
def refresh_stats():
    """「立即更新」: refresh mv_unit now, at most once every 10 seconds. The age check reads
    the timestamp from the database, so it holds across both uvicorn workers."""
    if stats.refreshed_age_sec() < MANUAL_REFRESH_MIN_SEC:
        raise ApiError(429, Msg("剛剛才更新過，請稍候再試", "Just refreshed. Please try again in a few seconds."), "too_soon")
    return {"stats_refreshed_at": stats.refresh(wait=True)}
