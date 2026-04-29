import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlparse

import psycopg2
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

import log_parser

app = FastAPI(title="WiFi Dashboard API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GENERIC_PARENT_DIRS = (
    "downloads",
    "desktop",
    "documents",
    "logs",
    "tmp",
    "temp",
)

LATEST_SN_CTE = """
WITH ranked AS (
  SELECT
    tr.*,
    ROW_NUMBER() OVER (PARTITION BY tr.sn ORDER BY tr.test_time DESC, tr.id DESC) AS rn,
    COUNT(*) OVER (PARTITION BY tr.sn) AS sn_runs
  FROM test_record tr
),
latest AS (
  SELECT *
  FROM ranked
  WHERE rn = 1
)
"""


def _resolve_rf_window(work_order: Optional[str] = None, product_model: Optional[str] = None) -> Optional[dict[str, Any]]:
  """Use nearest available day to today for RF analysis (today preferred when present)."""
  where = "WHERE test_time < date_trunc('day', NOW()) + INTERVAL '1 day'"
  params: list[Any] = []
  if work_order and work_order != "ALL":
    where += " AND work_order = %s"
    params.append(work_order)
  if product_model and product_model != "ALL":
    where += " AND product_model = %s"
    params.append(product_model)

  day_rows = query_all(
    LATEST_SN_CTE
    + f"""
    SELECT
      date_trunc('day', test_time) AS day_bucket,
      COUNT(DISTINCT date_trunc('hour', test_time)) AS hour_slots
    FROM latest
    {where}
    GROUP BY 1
    ORDER BY day_bucket DESC
    LIMIT 1
    """,
    tuple(params),
  )
  if not day_rows:
    return None

  day_bucket = day_rows[0]["day_bucket"]
  day_bucket_dt = datetime.fromisoformat(day_bucket) if isinstance(day_bucket, str) else day_bucket
  if not isinstance(day_bucket_dt, datetime):
    return None

  window_start = day_bucket_dt
  window_end = day_bucket_dt + timedelta(days=1)

  wo_where = "WHERE test_time >= %s AND test_time < %s"
  wo_params: list[Any] = [window_start, window_end]
  if work_order and work_order != "ALL":
    wo_where += " AND work_order = %s"
    wo_params.append(work_order)
  if product_model and product_model != "ALL":
    wo_where += " AND product_model = %s"
    wo_params.append(product_model)

  wo_rows = query_all(
    LATEST_SN_CTE
    + f"""
    SELECT work_order
    FROM latest
    {wo_where}
      AND work_order IS NOT NULL
      AND work_order <> ''
    ORDER BY test_time DESC
    LIMIT 1
    """,
    tuple(wo_params),
  )
  target_wo = wo_rows[0]["work_order"] if wo_rows else None

  return {
    "work_order": target_wo,
    "day_bucket": day_bucket_dt,
    "window_start": window_start,
    "window_end": window_end,
    "hour_slots": int(day_rows[0].get("hour_slots") or 0),
  }


def _resolve_dashboard_window(
    scope: str,
    year: Optional[int],
    work_order: Optional[str] = None,
  product_model: Optional[str] = None,
) -> Optional[dict[str, Any]]:
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

    where = ""
    params_list: list[Any] = []
    if work_order and work_order != "ALL":
      where = "WHERE work_order = %s"
      params_list.append(work_order)
    if product_model and product_model != "ALL":
      where += (" AND" if where else "WHERE") + " product_model = %s"
      params_list.append(product_model)

    latest_day_sql = LATEST_SN_CTE + f"""
    SELECT date_trunc('day', MAX(test_time)) AS latest_day
    FROM latest
    {where}
    """
    rows = query_all(latest_day_sql, tuple(params_list))
    latest_day = rows[0].get("latest_day") if rows else None
    if latest_day is None:
        return None

    latest_day_dt = datetime.fromisoformat(latest_day) if isinstance(latest_day, str) else latest_day
    if not isinstance(latest_day_dt, datetime):
        return None

    return {
        "scope": "latest_day",
        "year": None,
        "day": latest_day_dt.strftime("%Y-%m-%d"),
        "window_start": latest_day_dt + timedelta(hours=7),
        "window_end": latest_day_dt + timedelta(hours=19),
        "window_label": f"{latest_day_dt.strftime('%Y-%m-%d')} 07:00-19:00",
    }


def get_dsn() -> str:
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError("DATABASE_URL is not configured")
    return dsn


def query_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with psycopg2.connect(get_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [c.name for c in cur.description]
            rows = cur.fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {}
        for i, value in enumerate(row):
            if isinstance(value, datetime):
                item[cols[i]] = value.isoformat()
            else:
                item[cols[i]] = value
        out.append(item)
    return out


def _build_user_dsn(username: str, password: str) -> str:
    base = urlparse(get_dsn())
    host = base.hostname or "localhost"
    port = base.port or 5432
    dbname = (base.path or "").lstrip("/") or "postgres"
    return f"postgresql://{quote(username)}:{quote(password)}@{host}:{port}/{dbname}"


def query_all_user(sql: str, params: tuple[Any, ...], username: str, password: str) -> list[dict[str, Any]]:
    dsn = _build_user_dsn(username, password)
    try:
        with psycopg2.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [c.name for c in cur.description]
                rows = cur.fetchall()
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"PostgreSQL login failed: {exc}") from exc

    out: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {}
        for i, value in enumerate(row):
            if isinstance(value, datetime):
                item[cols[i]] = value.isoformat()
            else:
                item[cols[i]] = value
        out.append(item)
    return out


class DbLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class DbRecordListRequest(DbLoginRequest):
    work_order: Optional[str] = None
    product_model: Optional[str] = None
    limit: int = Field(default=500, ge=1, le=2000)


class DbWorkOrderListRequest(DbLoginRequest):
    product_model: Optional[str] = None


class DbDeleteRecordRequest(DbLoginRequest):
    id: int = Field(ge=1)


class DbDeleteWorkOrderRequest(DbLoginRequest):
    work_order: str = Field(min_length=1, max_length=256)


class DbDownloadRecordLogRequest(DbLoginRequest):
  id: int = Field(ge=1)


def _resolve_downloadable_log_path(source_file: str) -> Path:
  raw = str(source_file or '').strip()
  if not raw:
    raise HTTPException(status_code=404, detail='source_file is empty')

  original = Path(raw)
  candidates: list[Path] = []
  search_roots: list[Path] = []
  repo_root = Path(__file__).resolve().parents[1]

  if original.is_absolute():
    candidates.append(original)
  else:
    candidates.append(Path.cwd() / original)
    candidates.append(repo_root / original)

  normalized_raw = raw.replace('\\', '/')
  name_only = Path(normalized_raw).name
  if name_only and name_only != raw and name_only != normalized_raw:
    candidates.extend([
      Path('/app') / raw,
      Path('/app/logs') / raw,
      Path('/app/WiFiTestLogs') / raw,
      repo_root / raw,
      repo_root / 'logs' / raw,
      repo_root / 'WiFiTestLogs' / raw,
      Path.cwd() / raw,
      Path.cwd() / 'logs' / raw,
      Path.cwd() / 'WiFiTestLogs' / raw,
    ])

  if name_only:
    candidates.extend([
      Path('/app/logs') / name_only,
      Path('/app/WiFiTestLogs') / name_only,
      repo_root / 'logs' / name_only,
      repo_root / 'WiFiTestLogs' / name_only,
      Path.cwd() / 'logs' / name_only,
      Path.cwd() / 'WiFiTestLogs' / name_only,
    ])
    search_roots.extend([
      Path('/app/logs'),
      Path('/app/WiFiTestLogs'),
      repo_root / 'logs',
      repo_root / 'WiFiTestLogs',
      Path.cwd() / 'logs',
      Path.cwd() / 'WiFiTestLogs',
      repo_root,
      Path.cwd(),
    ])

  for candidate in candidates:
    resolved = candidate.resolve()
    if resolved.exists() and resolved.is_file():
      return resolved

  if name_only:
    seen: set[str] = set()
    for root in search_roots:
      key = str(root.resolve()) if root.exists() else str(root)
      if key in seen:
        continue
      seen.add(key)
      if not root.exists() or not root.is_dir():
        continue
      try:
        for matched in root.rglob(name_only):
          if matched.is_file():
            return matched.resolve()
      except OSError:
        continue

  raise HTTPException(status_code=404, detail=f'Log file not found for source: {raw}')


@app.get("/")
def index():
    return FileResponse("/app/wifi_dashboard.html")


@app.get("/health")
def health() -> dict[str, str]:
    try:
        with psycopg2.connect(get_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}") from exc
    return {"status": "ok"}


@app.get("/api/dashboard/summary")
def dashboard_summary(
    work_order: Optional[str] = None,
    product_model: Optional[str] = None,
    scope: str = Query(default="latest_day", pattern="^(latest_day|year)$"),
    year: Optional[int] = Query(default=None, ge=2000, le=2100),
) -> dict[str, Any]:
    window = _resolve_dashboard_window(scope, year, work_order, product_model)
    if not window:
        return {
            "total_units": 0,
            "pass_units": 0,
            "fail_units": 0,
            "retry_units": 0,
            "avg_24g": 0,
            "avg_5g": 0,
            "retry_rate_pct": 0,
            "yield_pct": 0,
            "scope": scope,
            "year": year,
            "day": None,
            "window_start": None,
            "window_end": None,
            "window_label": "N/A",
        }

    where = "WHERE test_time >= %s AND test_time < %s"
    params_list: list[Any] = [window["window_start"], window["window_end"]]
    if work_order and work_order != "ALL":
        where += " AND work_order = %s"
        params_list.append(work_order)
    if product_model and product_model != "ALL":
      where += " AND product_model = %s"
      params_list.append(product_model)

    sql = LATEST_SN_CTE + f"""
    SELECT
      COUNT(*) AS total_units,
      COUNT(*) FILTER (WHERE result = TRUE) AS pass_units,
      COUNT(*) FILTER (WHERE result = FALSE) AS fail_units,
      COUNT(*) FILTER (
        WHERE sn_runs > 1 OR attempt_count_24g > 1 OR attempt_count_5g > 1
      ) AS retry_units,
      ROUND(COALESCE(AVG(avg_24g), 0)::numeric, 2) AS avg_24g,
      ROUND(COALESCE(AVG(avg_5g), 0)::numeric, 2) AS avg_5g,
      ROUND(
        COALESCE(
          COUNT(*) FILTER (
            WHERE sn_runs > 1 OR attempt_count_24g > 1 OR attempt_count_5g > 1
          ) * 100.0 / NULLIF(COUNT(*), 0),
          0
        ),
        2
      ) AS retry_rate_pct,
      ROUND(
        COALESCE(COUNT(*) FILTER (WHERE result = TRUE) * 100.0 / NULLIF(COUNT(*), 0), 0),
        2
      ) AS yield_pct
    FROM latest
    {where}
    """
    rows = query_all(sql, tuple(params_list))
    payload = rows[0] if rows else {
        "total_units": 0,
        "pass_units": 0,
        "fail_units": 0,
        "retry_units": 0,
        "avg_24g": 0,
        "avg_5g": 0,
        "retry_rate_pct": 0,
        "yield_pct": 0,
    }
    payload["scope"] = window["scope"]
    payload["year"] = window["year"]
    payload["day"] = window["day"]
    payload["window_start"] = window["window_start"].isoformat() if isinstance(window["window_start"], datetime) else None
    payload["window_end"] = window["window_end"].isoformat() if isinstance(window["window_end"], datetime) else None
    payload["window_label"] = window["window_label"]
    return payload


@app.get("/api/dashboard/yield-trend")
def yield_trend(
    bucket: str = Query(default="hour", pattern="^(hour|day|month)$"),
    work_order: Optional[str] = None,
    product_model: Optional[str] = None,
    scope: str = Query(default="latest_day", pattern="^(latest_day|year)$"),
    year: Optional[int] = Query(default=None, ge=2000, le=2100),
) -> list[dict[str, Any]]:
    _ = bucket
    window = _resolve_dashboard_window(scope, year, work_order, product_model)
    if not window:
        return []

    if window["scope"] == "year":
        sql = LATEST_SN_CTE + """
        , month_raw AS (
          SELECT
            date_trunc('month', test_time) AS month_bucket,
            COUNT(*) AS total_units,
            COUNT(*) FILTER (WHERE result = TRUE) AS pass_units
          FROM latest
          WHERE test_time >= %s
            AND test_time < %s
            AND (%s IS NULL OR %s = 'ALL' OR work_order = %s)
            AND (%s IS NULL OR %s = 'ALL' OR product_model = %s)
          GROUP BY 1
        ),
        bounds AS (
          SELECT
            MIN(month_bucket) AS first_month,
            MAX(month_bucket) AS last_month
          FROM month_raw
        ),
        months AS (
          SELECT generate_series(
            COALESCE((SELECT first_month FROM bounds), %s::timestamp),
            COALESCE((SELECT last_month FROM bounds), %s::timestamp),
            INTERVAL '1 month'
          ) AS month_bucket
        ),
        month_joined AS (
          SELECT
            m.month_bucket,
            COALESCE(r.total_units, 0) AS total_units,
            COALESCE(r.pass_units, 0) AS pass_units
          FROM months m
          LEFT JOIN month_raw r ON r.month_bucket = m.month_bucket
        )
        SELECT
          month_bucket AS bucket_time,
          ROUND(COALESCE(pass_units * 100.0 / NULLIF(total_units, 0), 0), 2) AS yield_pct,
          total_units
        FROM month_joined
        ORDER BY month_bucket
        """
        return query_all(
            sql,
            (
                window["window_start"],
                window["window_end"],
                work_order,
                work_order,
                work_order,
                product_model,
                product_model,
                product_model,
              window["window_start"],
              window["window_start"],
            ),
        )

    sql = LATEST_SN_CTE + """
    , hours AS (
      SELECT generate_series(%s::timestamp, %s::timestamp - INTERVAL '1 hour', INTERVAL '1 hour') AS hour_bucket
    ),
    hour_raw AS (
      SELECT
        date_trunc('hour', test_time) AS hour_bucket,
        COUNT(*) AS total_units,
        COUNT(*) FILTER (WHERE result = TRUE) AS pass_units
      FROM latest
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
    """
    return query_all(
        sql,
        (
            window["window_start"],
            window["window_end"],
            window["window_start"],
            window["window_end"],
            work_order,
            work_order,
            work_order,
            product_model,
            product_model,
            product_model,
        ),
    )


@app.get("/api/dashboard/pass-fail-split")
def pass_fail_split(
    work_order: Optional[str] = None,
    product_model: Optional[str] = None,
    scope: str = Query(default="latest_day", pattern="^(latest_day|year)$"),
    year: Optional[int] = Query(default=None, ge=2000, le=2100),
) -> dict[str, Any]:
    window = _resolve_dashboard_window(scope, year, work_order, product_model)
    if not window:
        return {"pass_units": 0, "fail_units": 0, "total_units": 0}

    where = "WHERE test_time >= %s AND test_time < %s"
    params_list: list[Any] = [window["window_start"], window["window_end"]]
    if work_order and work_order != "ALL":
        where += " AND work_order = %s"
        params_list.append(work_order)
    if product_model and product_model != "ALL":
      where += " AND product_model = %s"
      params_list.append(product_model)

    sql = LATEST_SN_CTE + f"""
    SELECT
      COUNT(*) FILTER (WHERE result = TRUE) AS pass_units,
      COUNT(*) FILTER (WHERE result = FALSE) AS fail_units,
      COUNT(*) AS total_units
    FROM latest
    {where}
    """
    rows = query_all(sql, tuple(params_list))
    if not rows:
        return {"pass_units": 0, "fail_units": 0, "total_units": 0}
    return rows[0]


@app.get("/api/dashboard/throughput-distribution")
def throughput_distribution(
    work_order: Optional[str] = None,
    product_model: Optional[str] = None,
    scope: str = Query(default="latest_day", pattern="^(latest_day|year)$"),
    year: Optional[int] = Query(default=None, ge=2000, le=2100),
) -> dict[str, Any]:
    window = _resolve_dashboard_window(scope, year, work_order, product_model)
    if not window:
        return {"bands_24g": [], "bands_5g": []}

    where = "WHERE test_time >= %s AND test_time < %s"
    params_list: list[Any] = [window["window_start"], window["window_end"]]
    if work_order and work_order != "ALL":
        where += " AND work_order = %s"
        params_list.append(work_order)
    if product_model and product_model != "ALL":
      where += " AND product_model = %s"
      params_list.append(product_model)

    sql_24g = LATEST_SN_CTE + f"""
    SELECT
      FLOOR(avg_24g / 10) * 10 AS bin_start,
      COUNT(*) AS cnt
    FROM latest
    {where} AND avg_24g IS NOT NULL
    GROUP BY 1
    ORDER BY 1
    """
    rows_24g = query_all(sql_24g, tuple(params_list))

    sql_5g = LATEST_SN_CTE + f"""
    SELECT
      FLOOR(avg_5g / 50) * 50 AS bin_start,
      COUNT(*) AS cnt
    FROM latest
    {where} AND avg_5g IS NOT NULL
    GROUP BY 1
    ORDER BY 1
    """
    rows_5g = query_all(sql_5g, tuple(params_list))

    return {
        "bands_24g": [{"bin": f"{int(r['bin_start'])}-{int(r['bin_start'])+10}", "count": r["cnt"]} for r in rows_24g],
        "bands_5g": [{"bin": f"{int(r['bin_start'])}-{int(r['bin_start'])+50}", "count": r["cnt"]} for r in rows_5g],
    }


@app.get("/api/dashboard/available-years")
def available_years(work_order: Optional[str] = None, product_model: Optional[str] = None) -> dict[str, Any]:
    where = ""
    params: tuple[Any, ...] = ()
    if work_order and work_order != "ALL":
        where = "WHERE work_order = %s"
        params = (work_order,)
    if product_model and product_model != "ALL":
        where += (" AND" if where else "WHERE") + " product_model = %s"
        params = params + (product_model,)

    sql = LATEST_SN_CTE + f"""
    SELECT DISTINCT EXTRACT(YEAR FROM test_time)::int AS year
    FROM latest
    {where}
    ORDER BY year DESC
    """
    rows = query_all(sql, params)
    years = [int(r["year"]) for r in rows if r.get("year") is not None]
    now_year = datetime.now().year
    if not years:
        years = [now_year]
    return {
        "years": years,
        "current_year": now_year,
    }


@app.get("/api/dashboard/rf-window")
def rf_window(work_order: Optional[str] = None, product_model: Optional[str] = None) -> dict[str, Any]:
    info = _resolve_rf_window(work_order, product_model)
    if not info:
        return {
            "work_order": None,
            "day": None,
            "window_start": None,
            "window_end": None,
            "hour_slots": 0,
            "window_label": "N/A",
        }

    day_text = info["day_bucket"].strftime("%Y-%m-%d") if isinstance(info["day_bucket"], datetime) else str(info["day_bucket"])
    start_text = "00:00"
    end_text = "24:00"
    slots = int(info.get("hour_slots") or 0)
    wo = info.get("work_order") or "N/A"
    return {
        "work_order": wo,
        "day": day_text,
        "window_start": info["window_start"],
        "window_end": info["window_end"],
        "hour_slots": slots,
      "window_label": f"RF day {day_text} | {start_text}-{end_text} | {slots}h",
    }


@app.get("/api/dashboard/rf-throughput-trend")
def rf_throughput_trend(work_order: Optional[str] = None, product_model: Optional[str] = None) -> list[dict[str, Any]]:
    rf_window = _resolve_rf_window(work_order, product_model)
    if not rf_window:
        return []

    where = "WHERE test_time >= %s AND test_time < %s"
    params: list[Any] = [rf_window["window_start"], rf_window["window_end"]]
    if work_order and work_order != "ALL":
        where += " AND work_order = %s"
        params.append(work_order)
    if product_model and product_model != "ALL":
        where += " AND product_model = %s"
        params.append(product_model)

    sql = LATEST_SN_CTE + f"""
    , hours AS (
      SELECT generate_series(%s::timestamp, %s::timestamp - INTERVAL '1 hour', INTERVAL '1 hour') AS hour_bucket
    ),
    agg AS (
      SELECT
        date_trunc('hour', test_time) AS bucket_time,
        ROUND(AVG(avg_24g)::numeric, 2) AS avg_24g,
        ROUND(AVG(avg_5g)::numeric, 2) AS avg_5g
      FROM latest
      {where}
      GROUP BY 1
    )
    SELECT
      h.hour_bucket AS bucket_time,
      COALESCE(a.avg_24g, 0) AS avg_24g,
      COALESCE(a.avg_5g, 0) AS avg_5g
    FROM hours h
    LEFT JOIN agg a ON a.bucket_time = h.hour_bucket
    ORDER BY h.hour_bucket
    """
    return query_all(
        sql,
      (
        rf_window["window_start"],
        rf_window["window_end"],
        *params,
      ),
    )


@app.get("/api/dashboard/rf-percentiles")
def rf_percentiles(work_order: Optional[str] = None, product_model: Optional[str] = None) -> list[dict[str, Any]]:
    rf_window = _resolve_rf_window(work_order, product_model)
    if not rf_window:
        return []

    where = "WHERE test_time >= %s AND test_time < %s AND avg_5g IS NOT NULL"
    params: list[Any] = [rf_window["window_start"], rf_window["window_end"]]
    if work_order and work_order != "ALL":
        where += " AND work_order = %s"
        params.append(work_order)
    if product_model and product_model != "ALL":
        where += " AND product_model = %s"
        params.append(product_model)

    sql = LATEST_SN_CTE + f"""
    , hours AS (
      SELECT generate_series(%s::timestamp, %s::timestamp - INTERVAL '1 hour', INTERVAL '1 hour') AS hour_bucket
    ),
    agg AS (
      SELECT
        date_trunc('hour', test_time) AS bucket_time,
        ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY avg_5g)::numeric, 2) AS p50_5g,
        ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY avg_5g)::numeric, 2) AS p90_5g,
        ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY avg_5g)::numeric, 2) AS p95_5g
      FROM latest
      {where}
      GROUP BY 1
    )
    SELECT
      h.hour_bucket AS bucket_time,
      COALESCE(a.p50_5g, 0) AS p50_5g,
      COALESCE(a.p90_5g, 0) AS p90_5g,
      COALESCE(a.p95_5g, 0) AS p95_5g
    FROM hours h
    LEFT JOIN agg a ON a.bucket_time = h.hour_bucket
    ORDER BY h.hour_bucket
    """
    return query_all(
        sql,
      (
        rf_window["window_start"],
        rf_window["window_end"],
        *params,
      ),
    )


@app.get("/api/dashboard/rf-stddev")
def rf_stddev(work_order: Optional[str] = None, product_model: Optional[str] = None, limit: int = Query(default=20, ge=1, le=200)) -> list[dict[str, Any]]:
    rf_window = _resolve_rf_window(work_order, product_model)
    if not rf_window:
        return []

    where = "WHERE std_5g IS NOT NULL AND test_time >= %s AND test_time < %s"
    params: list[Any] = [rf_window["window_start"], rf_window["window_end"]]
    if work_order and work_order != "ALL":
        where += " AND work_order = %s"
        params.append(work_order)
    if product_model and product_model != "ALL":
        where += " AND product_model = %s"
        params.append(product_model)

    sql = LATEST_SN_CTE + f"""
    SELECT
      sn,
      work_order,
      ROUND(COALESCE(std_5g, 0)::numeric, 2) AS std_5g
    FROM latest
    {where}
    ORDER BY std_5g DESC, test_time DESC
    LIMIT %s
    """
    params.append(limit)
    return query_all(sql, tuple(params))


@app.get("/api/dashboard/latest-workorder-hourly")
def latest_workorder_hourly(
  work_order: Optional[str] = None,
  product_model: Optional[str] = None,
  lookback_days: int = Query(default=2, ge=1, le=2),
) -> dict[str, Any]:
  if work_order and work_order != "ALL":
    extra_where = ""
    params: list[Any] = [work_order]
    if product_model and product_model != "ALL":
      extra_where = " AND product_model = %s"
      params.append(product_model)
    target_sql = LATEST_SN_CTE + """
    SELECT work_order, product_model, test_time AS ref_time
    FROM latest
    WHERE work_order = %s""" + extra_where + """
    ORDER BY test_time DESC, id DESC
    LIMIT 1
    """
    target_rows = query_all(target_sql, tuple(params))
  else:
    extra_where = ""
    params: tuple[Any, ...] = ()
    if product_model and product_model != "ALL":
      extra_where = " AND product_model = %s"
      params = (product_model,)
    target_sql = LATEST_SN_CTE + """
    SELECT work_order, product_model, test_time AS ref_time
    FROM latest
    WHERE work_order IS NOT NULL AND work_order <> ''""" + extra_where + """
    ORDER BY test_time DESC, id DESC
    LIMIT 1
    """
    target_rows = query_all(target_sql, params)

  if not target_rows:
    return {"work_order": None, "product_model": None, "window_start": None, "window_end": None, "hourly": []}

  target_wo = target_rows[0]["work_order"]
  target_model = target_rows[0].get("product_model")
  day_extra_where = ""
  day_params: list[Any] = [target_wo]
  if product_model and product_model != "ALL":
    day_extra_where = " AND product_model = %s"
    day_params.append(product_model)
  day_sql = LATEST_SN_CTE + """
  SELECT DISTINCT date_trunc('day', test_time) AS day_bucket
  FROM latest
  WHERE work_order = %s""" + day_extra_where + """
  ORDER BY day_bucket DESC
  LIMIT %s
  """
  day_params.append(lookback_days)
  day_rows = query_all(day_sql, tuple(day_params))
  if not day_rows:
    return {
      "work_order": target_wo,
      "product_model": target_model,
      "window_start": None,
      "window_end": None,
      "lookback_days": lookback_days,
      "days": [],
      "hourly": [],
    }

  day_values = [row["day_bucket"] for row in day_rows]
  newest_day = day_values[0]
  oldest_day = day_values[-1]

  hourly_model_where = ""
  hourly_params: list[Any] = [day_values, target_wo]
  if product_model and product_model != "ALL":
    hourly_model_where = " AND l.product_model = %s"
    hourly_params.append(product_model)
  hourly_by_day_sql = """
  WITH day_values AS (
    SELECT unnest(%s::timestamp[]) AS day_bucket
  ),
  hours AS (
    SELECT
      dv.day_bucket,
      generate_series(
        dv.day_bucket,
        dv.day_bucket + INTERVAL '23 hours',
        INTERVAL '1 hour'
      ) AS hour_bucket
    FROM day_values dv
  ),
  ranked AS (
    SELECT
      tr.*,
      ROW_NUMBER() OVER (PARTITION BY tr.sn ORDER BY tr.test_time DESC, tr.id DESC) AS rn,
      COUNT(*) OVER (PARTITION BY tr.sn) AS sn_runs
    FROM test_record tr
  ),
  latest AS (
    SELECT *
    FROM ranked
    WHERE rn = 1
  )
  SELECT
    h.day_bucket,
    h.hour_bucket,
    COALESCE(COUNT(l.id), 0) AS units
  FROM hours h
  LEFT JOIN latest l
    ON date_trunc('hour', l.test_time) = h.hour_bucket
    AND l.work_order = %s""" + hourly_model_where + """
  GROUP BY h.day_bucket, h.hour_bucket
  ORDER BY h.day_bucket DESC, h.hour_bucket
  """
  hourly_rows = query_all(hourly_by_day_sql, tuple(hourly_params))

  daily_map: dict[str, list[dict[str, Any]]] = {}
  for row in hourly_rows:
    day_key = str(row["day_bucket"])
    daily_map.setdefault(day_key, []).append({"hour": row["hour_bucket"], "units": row["units"]})

  daily_blocks: list[dict[str, Any]] = []
  for day in day_values:
    day_key = str(day)
    daily_blocks.append(
      {
        "date": day_key[:10],
        "hourly": daily_map.get(day_key, []),
      }
    )

  normalized_hourly = daily_blocks[0]["hourly"] if daily_blocks else []
  return {
    "work_order": target_wo,
    "product_model": target_model,
    "window_start": oldest_day,
    "window_end": newest_day,
    "lookback_days": lookback_days,
    "days": daily_blocks,
    "hourly": normalized_hourly,
  }


@app.get("/api/products")
def products(
    limit: int = Query(default=200, ge=1, le=500),
    scope: str = Query(default="latest_day", pattern="^(latest_day|year)$"),
    year: Optional[int] = Query(default=None, ge=2000, le=2100),
) -> list[dict[str, Any]]:
    window = _resolve_dashboard_window(scope, year, None, None)
    if not window:
        return []

    sql = LATEST_SN_CTE + """
    SELECT
      product_model,
      MAX(test_time) AS latest_test_time,
      COUNT(*) AS total
    FROM latest
    WHERE test_time >= %s
      AND test_time < %s
      AND product_model IS NOT NULL
      AND product_model <> ''
    GROUP BY product_model
    ORDER BY MAX(test_time) DESC
    LIMIT %s
    """
    return query_all(sql, (window["window_start"], window["window_end"], limit))


@app.get("/api/workorders")
def work_orders(
    limit: int = Query(default=200, ge=1, le=500),
    product_model: Optional[str] = None,
    scope: str = Query(default="latest_day", pattern="^(latest_day|year)$"),
    year: Optional[int] = Query(default=None, ge=2000, le=2100),
) -> list[dict[str, Any]]:
    window = _resolve_dashboard_window(scope, year, None, product_model)
    if not window:
        return []

    where = "WHERE test_time >= %s AND test_time < %s"
    params: list[Any] = [window["window_start"], window["window_end"]]
    if product_model and product_model != "ALL":
        where += " AND product_model = %s"
        params.append(product_model)
    sql = LATEST_SN_CTE + f"""
    , scoped AS (
      SELECT *
      FROM latest
      {where}
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
      ROUND(
        COALESCE(
          COUNT(*) FILTER (
            WHERE s.sn_runs > 1 OR s.attempt_count_24g > 1 OR s.attempt_count_5g > 1
          ) * 100.0 / NULLIF(COUNT(*), 0),
          0
        ),
        2
      ) AS retry_rate_pct
    FROM scoped s
    GROUP BY s.work_order, s.product_model
    ORDER BY MAX(s.test_time) DESC
    LIMIT %s
    """
    params.append(limit)
    return query_all(sql, tuple(params))


@app.get("/api/fails")
def fails(limit: int = Query(default=200, ge=1, le=1000), product_model: Optional[str] = None) -> list[dict[str, Any]]:
    where = "WHERE result = FALSE"
    params: list[Any] = []
    if product_model and product_model != "ALL":
        where += " AND product_model = %s"
        params.append(product_model)
    sql = LATEST_SN_CTE + f"""
    SELECT
      test_time,
      sn,
      work_order,
      product_model,
      avg_24g,
      avg_5g,
      rssi_before_5g,
      rssi_after_5g,
      rssi_delta_5g,
      band_result_24g,
      band_result_5g,
      bt_result,
      CASE
        WHEN COALESCE(band_result_5g, TRUE) = FALSE THEN '5G check failed'
        WHEN COALESCE(band_result_24g, TRUE) = FALSE THEN '2.4G check failed'
        WHEN COALESCE(bt_result, TRUE) = FALSE THEN 'BT ping failed'
        WHEN avg_5g IS NULL THEN 'Missing 5G throughput'
        WHEN avg_24g IS NULL THEN 'Missing 2.4G throughput'
        ELSE 'Final test failed'
      END AS fail_reason
    FROM latest
    {where}
    ORDER BY test_time DESC
    LIMIT %s
    """
    params.append(limit)
    return query_all(sql, tuple(params))


@app.get("/api/retries")
def retries(limit: int = Query(default=200, ge=1, le=1000), product_model: Optional[str] = None) -> list[dict[str, Any]]:
    where = "WHERE GREATEST(sn_runs, attempt_count_24g, attempt_count_5g) > 1"
    params: list[Any] = []
    if product_model and product_model != "ALL":
        where += " AND product_model = %s"
        params.append(product_model)
    sql = LATEST_SN_CTE + f"""
    SELECT
      test_time,
      sn,
      work_order,
      product_model,
      GREATEST(sn_runs, attempt_count_24g, attempt_count_5g) AS attempts,
      avg_5g,
      rssi_delta_5g,
      CASE
        WHEN GREATEST(sn_runs, attempt_count_24g, attempt_count_5g) >= 3 THEN 'high'
        WHEN rssi_delta_5g <= -20 THEN 'high'
        WHEN GREATEST(sn_runs, attempt_count_24g, attempt_count_5g) = 2 THEN 'medium'
        ELSE 'low'
      END AS retry_risk
    FROM latest
    {where}
    ORDER BY test_time DESC
    LIMIT %s
    """
    params.append(limit)
    return query_all(sql, tuple(params))


@app.post("/api/db-tweak/login")
def db_tweak_login(req: DbLoginRequest) -> dict[str, Any]:
    rows = query_all_user(
        "SELECT current_user AS db_user, current_database() AS db_name",
        (),
        req.username,
        req.password,
    )
    row = rows[0] if rows else {}
    return {
        "ok": True,
        "db_user": row.get("db_user"),
        "db_name": row.get("db_name"),
    }


@app.post("/api/db-tweak/workorders")
def db_tweak_workorders(req: DbWorkOrderListRequest) -> dict[str, Any]:
    where = ""
    params: list[Any] = []
    if req.product_model and req.product_model not in ("ALL", "N/A"):
        where = "WHERE product_model = %s"
        params.append(req.product_model)
    elif req.product_model == "N/A":
        where = "WHERE product_model IS NULL OR product_model = ''"

    sql = f"""
    SELECT
      COALESCE(NULLIF(work_order, ''), 'N/A') AS work_order,
      COALESCE(NULLIF(product_model, ''), 'N/A') AS product_model,
      COUNT(*) AS total,
      COUNT(*) FILTER (WHERE result = TRUE) AS passed,
      COUNT(*) FILTER (WHERE result = FALSE) AS failed,
      MIN(test_time) AS first_test,
      MAX(test_time) AS last_test
    FROM test_record
    {where}
    GROUP BY COALESCE(NULLIF(work_order, ''), 'N/A'), COALESCE(NULLIF(product_model, ''), 'N/A')
    ORDER BY MAX(test_time) DESC
    """
    rows = query_all_user(sql, tuple(params), req.username, req.password)
    return {
        "items": rows,
        "count": len(rows),
    }


@app.post("/api/db-tweak/products")
def db_tweak_products(req: DbLoginRequest) -> dict[str, Any]:
    sql = """
    SELECT
      COALESCE(NULLIF(product_model, ''), 'N/A') AS product_model,
      COUNT(*) AS total,
      MAX(test_time) AS last_test
    FROM test_record
    GROUP BY COALESCE(NULLIF(product_model, ''), 'N/A')
    ORDER BY MAX(test_time) DESC
    """
    rows = query_all_user(sql, (), req.username, req.password)
    return {
        "items": rows,
        "count": len(rows),
    }


@app.post("/api/db-tweak/records")
def db_tweak_records(req: DbRecordListRequest) -> dict[str, Any]:
    where = ""
    params: list[Any] = []
    if req.work_order and req.work_order not in ("ALL", "N/A"):
        where = "WHERE work_order = %s"
        params.append(req.work_order)
    elif req.work_order == "N/A":
        where = "WHERE work_order IS NULL OR work_order = ''"

    if req.product_model and req.product_model not in ("ALL", "N/A"):
        where += (" AND" if where else "WHERE") + " product_model = %s"
        params.append(req.product_model)
    elif req.product_model == "N/A":
        where += (" AND" if where else "WHERE") + " (product_model IS NULL OR product_model = '')"

    sql = f"""
    SELECT
      id,
      test_time,
      COALESCE(NULLIF(work_order, ''), 'N/A') AS work_order,
      product_model,
      sn,
      result,
      CASE
        WHEN COALESCE(band_result_24g, TRUE) = FALSE OR COALESCE(band_result_5g, TRUE) = FALSE THEN FALSE
        WHEN band_result_24g IS TRUE OR band_result_5g IS TRUE THEN TRUE
        ELSE NULL
      END AS wifi_result,
      bt_result,
      avg_24g,
      avg_5g,
      source_file
    FROM test_record
    {where}
    ORDER BY test_time DESC, id DESC
    LIMIT %s
    """
    params.append(req.limit)
    rows = query_all_user(sql, tuple(params), req.username, req.password)
    return {
        "items": rows,
        "count": len(rows),
        "work_order": req.work_order or "ALL",
        "product_model": req.product_model or "ALL",
    }


@app.post("/api/db-tweak/delete-record")
def db_tweak_delete_record(req: DbDeleteRecordRequest) -> dict[str, Any]:
    dsn = _build_user_dsn(req.username, req.password)
    try:
        with psycopg2.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM test_record WHERE id = %s", (req.id,))
                deleted = int(cur.rowcount or 0)
                conn.commit()
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Delete failed: {exc}") from exc

    return {
        "deleted": deleted,
        "id": req.id,
    }


@app.post("/api/db-tweak/delete-workorder")
def db_tweak_delete_workorder(req: DbDeleteWorkOrderRequest) -> dict[str, Any]:
    dsn = _build_user_dsn(req.username, req.password)
    try:
        with psycopg2.connect(dsn) as conn:
            with conn.cursor() as cur:
                if req.work_order == "N/A":
                    cur.execute("DELETE FROM test_record WHERE work_order IS NULL OR work_order = ''")
                else:
                    cur.execute("DELETE FROM test_record WHERE work_order = %s", (req.work_order,))
                deleted = int(cur.rowcount or 0)
                conn.commit()
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Delete failed: {exc}") from exc

    return {
        "deleted": deleted,
        "work_order": req.work_order,
    }


@app.post('/api/db-tweak/download-record-log')
def db_tweak_download_record_log(req: DbDownloadRecordLogRequest):
    rows = query_all_user(
        'SELECT source_file, raw_log FROM test_record WHERE id = %s LIMIT 1',
        (req.id,),
        req.username,
        req.password,
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f'Record not found: id={req.id}')

    source_file = rows[0].get('source_file')
    raw_log = rows[0].get('raw_log')

    if not source_file and not raw_log:
        raise HTTPException(status_code=404, detail='No log data for this record')

    filename = str(source_file) if source_file else f"log_{req.id}.txt"
    filename = Path(filename.replace('\\', '/')).name

    if source_file:
        try:
            file_path = _resolve_downloadable_log_path(str(source_file))
            return FileResponse(
                path=str(file_path),
                filename=file_path.name,
                media_type='application/octet-stream',
            )
        except HTTPException:
            pass
            
    if raw_log:
        return Response(
            content=raw_log,
            media_type='application/octet-stream',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'}
        )
        
    raise HTTPException(status_code=404, detail=f'Log file not found on disk and database raw_log is empty for source: {source_file}')


@app.post("/api/ingest")
def ingest(
  path: str = "/app/logs",
  dry_run: bool = False,
  duplicate_mode: str = Query(default="skip", pattern="^(skip|overwrite)$"),
) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise HTTPException(status_code=400, detail=f"Path not found: {path}")

    files = log_parser.scan_directory(str(target)) if target.is_dir() else [str(target)]
    if not files:
        return {"found": 0, "inserted": 0, "duplicates": 0, "errors": 0}

    inserted = 0
    overwritten = 0
    would_insert = 0
    would_overwrite = 0
    duplicates = 0
    errors = 0
    error_details: list[dict[str, str]] = []
    sanitized_files = 0
    sanitized_nul_bytes = 0

    warning_keys = [
        "warn_missing_tp_24g",
        "warn_missing_tp_5g",
        "warn_missing_result_24g",
        "warn_missing_result_5g",
        "warn_missing_bt_result",
    ]
    warning_totals = {k: 0 for k in warning_keys}

    with psycopg2.connect(get_dsn()) as conn:
      with conn.cursor() as cur:
        for file_path in files:
          record = log_parser.ingest_file(file_path)
          if not record:
            errors += 1
            if len(error_details) < 20:
                error_details.append({"file": str(file_path), "error": "Filename/parse mismatch"})
            continue

          for key in warning_keys:
            warning_totals[key] += int(record.get(key, 0) or 0)

          sanitized_nul_count = int(record.get("sanitized_nul_bytes", 0) or 0)
          if sanitized_nul_count > 0:
            sanitized_files += 1
            sanitized_nul_bytes += sanitized_nul_count

          if dry_run:
            try:
              cur.execute("SELECT 1 FROM test_record WHERE file_hash = %s LIMIT 1", (record.get("file_hash"),))
              exists = cur.fetchone() is not None
              if exists:
                if duplicate_mode == "overwrite":
                    would_overwrite += 1
                else:
                    duplicates += 1
              else:
                would_insert += 1
            except Exception as exc:
              errors += 1
              if len(error_details) < 20:
                  error_details.append({"file": str(file_path), "error": f"Dry-run DB check failed: {exc}"})
            continue

          try:
            if duplicate_mode == "overwrite":
              cur.execute("SELECT 1 FROM test_record WHERE file_hash = %s LIMIT 1", (record.get("file_hash"),))
              existed_before = cur.fetchone() is not None
              rid = log_parser.upsert_record(conn, record)
              if rid and existed_before:
                overwritten += 1
              elif rid:
                inserted += 1
            else:
              rid = log_parser.write_record(conn, record)
              if rid:
                inserted += 1
              else:
                duplicates += 1
          except Exception as exc:
            errors += 1
            if len(error_details) < 20:
                error_details.append({"file": str(file_path), "error": f"DB write failed: {exc}"})

    return {
        "found": len(files),
      "inserted": inserted,
      "overwritten": overwritten,
      "would_insert": would_insert if dry_run else inserted,
      "would_overwrite": would_overwrite if dry_run else overwritten,
        "duplicates": duplicates,
        "errors": errors,
      "dry_run": dry_run,
      "duplicate_mode": duplicate_mode,
      "error_details": error_details,
        "sanitized_files": sanitized_files,
        "sanitized_nul_bytes": sanitized_nul_bytes,
        "warnings": warning_totals,
    }


@app.post("/api/admin/backfill-metadata")
def backfill_metadata(dry_run: bool = True) -> dict[str, Any]:
    """
    Backfill rows generated by earlier path-mapping logic.
    Rule: if product_model is a generic parent folder and work_order is non-empty,
    promote work_order -> product_model and clear work_order.
    """
    placeholders = ", ".join(["%s"] * len(GENERIC_PARENT_DIRS))
    where_clause = f"""
      LOWER(COALESCE(product_model, '')) IN ({placeholders})
      AND COALESCE(work_order, '') <> ''
    """

    params = tuple(GENERIC_PARENT_DIRS)

    with psycopg2.connect(get_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM test_record WHERE {where_clause}", params)
            candidates = int(cur.fetchone()[0] or 0)

            if dry_run:
                conn.rollback()
                return {
                    "dry_run": True,
                    "candidates": candidates,
                    "rule": "generic parent folder product_model -> use work_order as product_model; clear work_order",
                }

            cur.execute(
                f"""
                UPDATE test_record
                SET
                  product_model = work_order,
                  work_order = NULL
                WHERE {where_clause}
                """,
                params,
            )
            updated = int(cur.rowcount or 0)
            conn.commit()

    return {
        "dry_run": False,
        "candidates": candidates,
        "updated": updated,
        "rule": "generic parent folder product_model -> use work_order as product_model; clear work_order",
    }
