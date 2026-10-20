"""DB Maintenance (DB Tweak): browse and delete test records — administrator session only.

Replaces the per-request PostgreSQL credentials of the previous version; queries run through the
API's pool, deletes are written to account_audit, and the statistics view refreshes after a delete.
"""
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, Response

import db
from errors import ApiError, Msg
from services import auth, stats
from services.logfiles import resolve_downloadable_log_path

router = APIRouter(prefix="/api/db-tweak", tags=["db-tweak"], dependencies=[Depends(auth.require_admin)])


def _product_where(product_model: Optional[str], where: str, params: list) -> str:
    if product_model and product_model not in ("ALL", "N/A"):
        params.append(product_model)
        return where + (" AND" if where else "WHERE") + " product_model = %s"
    if product_model == "N/A":
        return where + (" AND" if where else "WHERE") + " (product_model IS NULL OR product_model = '')"
    return where


@router.get("/workorders")
def work_orders(product_model: Optional[str] = None) -> dict[str, Any]:
    params: list = []
    where = _product_where(product_model, "", params)
    rows = db.rows(f"""
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


@router.get("/products")
def products_list() -> dict[str, Any]:
    rows = db.rows("""
    SELECT COALESCE(NULLIF(product_model, ''), 'N/A') AS product_model, COUNT(*) AS total, MAX(test_time) AS last_test
    FROM test_record
    GROUP BY COALESCE(NULLIF(product_model, ''), 'N/A')
    ORDER BY MAX(test_time) DESC
    """)
    return {"items": rows, "count": len(rows)}


@router.get("/records")
def records(work_order: Optional[str] = None, product_model: Optional[str] = None,
            limit: int = Query(500, ge=1, le=2000)) -> dict[str, Any]:
    where, params = "", []
    if work_order and work_order not in ("ALL", "N/A"):
        where, params = "WHERE work_order = %s", [work_order]
    elif work_order == "N/A":
        where = "WHERE work_order IS NULL OR work_order = ''"
    where = _product_where(product_model, where, params)
    rows = db.rows(f"""
    SELECT
      id, test_time, COALESCE(NULLIF(work_order, ''), 'N/A') AS work_order, product_model, sn, result,
      CASE
        WHEN COALESCE(band_result_24g, TRUE) = FALSE OR COALESCE(band_result_5g, TRUE) = FALSE THEN FALSE
        WHEN band_result_24g IS TRUE OR band_result_5g IS TRUE THEN TRUE
        ELSE NULL
      END AS wifi_result,
      bt_result, avg_24g, avg_5g, source_file, upload_id
    FROM test_record
    {where}
    ORDER BY test_time DESC, id DESC
    LIMIT %s
    """, (*params, limit))
    return {"items": rows, "count": len(rows), "work_order": work_order or "ALL", "product_model": product_model or "ALL"}


@router.delete("/records/{record_id}")
def delete_record(record_id: int, request: Request, admin=Depends(auth.require_admin)) -> dict[str, Any]:
    with db.db() as conn:
        row = db.fetch_one(conn, "SELECT sn, work_order, product_model FROM test_record WHERE id = %s", (record_id,))
        deleted = db.execute(conn, "DELETE FROM test_record WHERE id = %s", (record_id,))
        if deleted:
            auth.audit(conn, request, admin["username"], "delete_record", str(record_id), row)
    if deleted:
        stats.refresh(wait=False)
    return {"deleted": deleted, "id": record_id}


@router.delete("/workorders/{work_order}")
def delete_work_order(work_order: str, request: Request, admin=Depends(auth.require_admin)) -> dict[str, Any]:
    with db.db() as conn:
        if work_order == "N/A":
            deleted = db.execute(conn, "DELETE FROM test_record WHERE work_order IS NULL OR work_order = ''")
        else:
            deleted = db.execute(conn, "DELETE FROM test_record WHERE work_order = %s", (work_order,))
        if deleted:
            auth.audit(conn, request, admin["username"], "delete_work_order", work_order, {"deleted": deleted})
    if deleted:
        stats.refresh(wait=False)
    return {"deleted": deleted, "work_order": work_order}


@router.get("/records/{record_id}/log")
def download_record_log(record_id: int):
    with db.db() as conn:
        row = db.fetch_one(conn, "SELECT source_file, raw_log FROM test_record WHERE id = %s LIMIT 1", (record_id,))
    if not row:
        raise ApiError(404, Msg(f"找不到資料：id={record_id}", f"Record not found: id={record_id}"), "not_found")
    source_file, raw_log = row.get("source_file"), row.get("raw_log")
    if not source_file and not raw_log:
        raise ApiError(404, Msg("此筆資料沒有 log", "No log data for this record"), "not_found")
    filename = Path(str(source_file or f"log_{record_id}.txt").replace("\\", "/")).name
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
