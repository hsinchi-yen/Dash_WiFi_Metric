"""DB Tweak: the user signs in with their own PostgreSQL login (sent with every request);
queries run as that login. Unchanged behaviour; the account system will replace the
per-request credentials with a session token."""
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

import db
from errors import ApiError, Msg
from services import stats
from services.logfiles import resolve_downloadable_log_path

router = APIRouter(prefix="/api/db-tweak", tags=["db-tweak"])


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


def _rows_as(req: DbLoginRequest, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    try:
        conn = db.connect_as(req.username, req.password)
    except Exception as exc:
        raise ApiError(401, Msg(f"PostgreSQL 登入失敗：{exc}", f"PostgreSQL login failed: {exc}"), "unauthorized") from exc
    try:
        return db.jsonable(db.fetch_all(conn, sql, params))
    except Exception as exc:
        raise ApiError(401, Msg(f"PostgreSQL 查詢失敗：{exc}", f"PostgreSQL login failed: {exc}"), "unauthorized") from exc
    finally:
        conn.close()


def _delete_as(req: DbLoginRequest, sql: str, params: tuple = ()) -> int:
    try:
        conn = db.connect_as(req.username, req.password)
    except Exception as exc:
        raise ApiError(401, Msg(f"刪除失敗：{exc}", f"Delete failed: {exc}"), "unauthorized") from exc
    try:
        deleted = db.execute(conn, sql, params)
        conn.commit()
        return int(deleted or 0)
    except Exception as exc:
        conn.rollback()
        raise ApiError(401, Msg(f"刪除失敗：{exc}", f"Delete failed: {exc}"), "unauthorized") from exc
    finally:
        conn.close()


@router.post("/login")
def login(req: DbLoginRequest) -> dict[str, Any]:
    rows = _rows_as(req, "SELECT current_user AS db_user, current_database() AS db_name")
    row = rows[0] if rows else {}
    return {"ok": True, "db_user": row.get("db_user"), "db_name": row.get("db_name")}


@router.post("/workorders")
def work_orders(req: DbWorkOrderListRequest) -> dict[str, Any]:
    where, params = "", []
    if req.product_model and req.product_model not in ("ALL", "N/A"):
        where, params = "WHERE product_model = %s", [req.product_model]
    elif req.product_model == "N/A":
        where = "WHERE product_model IS NULL OR product_model = ''"
    rows = _rows_as(req, f"""
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
    """, tuple(params))
    return {"items": rows, "count": len(rows)}


@router.post("/products")
def products(req: DbLoginRequest) -> dict[str, Any]:
    rows = _rows_as(req, """
    SELECT COALESCE(NULLIF(product_model, ''), 'N/A') AS product_model, COUNT(*) AS total, MAX(test_time) AS last_test
    FROM test_record
    GROUP BY COALESCE(NULLIF(product_model, ''), 'N/A')
    ORDER BY MAX(test_time) DESC
    """)
    return {"items": rows, "count": len(rows)}


@router.post("/records")
def records(req: DbRecordListRequest) -> dict[str, Any]:
    where, params = "", []
    if req.work_order and req.work_order not in ("ALL", "N/A"):
        where, params = "WHERE work_order = %s", [req.work_order]
    elif req.work_order == "N/A":
        where = "WHERE work_order IS NULL OR work_order = ''"
    if req.product_model and req.product_model not in ("ALL", "N/A"):
        where += (" AND" if where else "WHERE") + " product_model = %s"
        params.append(req.product_model)
    elif req.product_model == "N/A":
        where += (" AND" if where else "WHERE") + " (product_model IS NULL OR product_model = '')"
    rows = _rows_as(req, f"""
    SELECT
      id, test_time, COALESCE(NULLIF(work_order, ''), 'N/A') AS work_order, product_model, sn, result,
      CASE
        WHEN COALESCE(band_result_24g, TRUE) = FALSE OR COALESCE(band_result_5g, TRUE) = FALSE THEN FALSE
        WHEN band_result_24g IS TRUE OR band_result_5g IS TRUE THEN TRUE
        ELSE NULL
      END AS wifi_result,
      bt_result, avg_24g, avg_5g, source_file
    FROM test_record
    {where}
    ORDER BY test_time DESC, id DESC
    LIMIT %s
    """, (*params, req.limit))
    return {"items": rows, "count": len(rows), "work_order": req.work_order or "ALL",
            "product_model": req.product_model or "ALL"}


@router.post("/delete-record")
def delete_record(req: DbDeleteRecordRequest) -> dict[str, Any]:
    deleted = _delete_as(req, "DELETE FROM test_record WHERE id = %s", (req.id,))
    if deleted:
        stats.refresh(wait=False)
    return {"deleted": deleted, "id": req.id}


@router.post("/delete-workorder")
def delete_work_order(req: DbDeleteWorkOrderRequest) -> dict[str, Any]:
    if req.work_order == "N/A":
        deleted = _delete_as(req, "DELETE FROM test_record WHERE work_order IS NULL OR work_order = ''")
    else:
        deleted = _delete_as(req, "DELETE FROM test_record WHERE work_order = %s", (req.work_order,))
    if deleted:
        stats.refresh(wait=False)
    return {"deleted": deleted, "work_order": req.work_order}


@router.post("/download-record-log")
def download_record_log(req: DbDownloadRecordLogRequest):
    rows = _rows_as(req, "SELECT source_file, raw_log FROM test_record WHERE id = %s LIMIT 1", (req.id,))
    if not rows:
        raise ApiError(404, Msg(f"找不到資料：id={req.id}", f"Record not found: id={req.id}"), "not_found")
    source_file, raw_log = rows[0].get("source_file"), rows[0].get("raw_log")
    if not source_file and not raw_log:
        raise ApiError(404, Msg("此筆資料沒有 log", "No log data for this record"), "not_found")

    filename = Path(str(source_file or f"log_{req.id}.txt").replace("\\", "/")).name
    if source_file:
        try:
            file_path = resolve_downloadable_log_path(str(source_file))
            return FileResponse(path=str(file_path), filename=file_path.name, media_type="application/octet-stream")
        except ApiError:
            pass
    if raw_log:
        return Response(content=raw_log, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    raise ApiError(404, Msg(f"磁碟上找不到 log，資料庫的 raw_log 也是空的：{source_file}",
                            f"Log file not found on disk and database raw_log is empty for source: {source_file}"), "not_found")
