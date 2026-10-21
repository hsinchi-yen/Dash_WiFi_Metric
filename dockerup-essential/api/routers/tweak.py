"""DB Maintenance (DB Tweak): browse, export and delete test records — administrator session only.
Before any delete the page shows /impact (5D): what the delete removes and how each work order changes.

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


def _scope(work_order: Optional[str], product_model: Optional[str], record_id: Optional[int], upload_id: Optional[str] = None):
    """WHERE for one record, one web upload batch, or a work order (and product), as the delete endpoints use them."""
    if record_id is not None:
        return "WHERE id = %s", [record_id]
    if upload_id:
        return "WHERE upload_id = %s", [upload_id]
    where, params = "", []
    if work_order and work_order not in ("ALL", "N/A"):
        where, params = "WHERE work_order = %s", [work_order]
    elif work_order == "N/A":
        where = "WHERE (work_order IS NULL OR work_order = '')"
    return _product_where(product_model, where, params), params


@router.get("/impact")
def impact(work_order: Optional[str] = Query(None, max_length=64), product_model: Optional[str] = Query(None, max_length=128),
           record_id: Optional[int] = None, upload_id: Optional[str] = Query(None, max_length=64)) -> dict[str, Any]:
    """What a delete would remove (5D, Q19 C) — one record, one web upload batch (its undo) or a work order: rows, units,
    web-uploaded rows and their batches, FAIL classifications, dispositions, RD triage, and each work order's units /
    yield before and after."""
    if record_id is None and not work_order and not upload_id:
        raise ApiError(400, Msg("請指定工單、上傳批次或一筆資料。", "Choose a work order, an upload batch or one record."), "no_scope")
    where, params = _scope(work_order, product_model, record_id, upload_id)
    ids_sql = f"SELECT id FROM test_record {where}"
    row = db.rows(f"""SELECT COUNT(*) AS rows, COUNT(DISTINCT sn) AS units, COUNT(*) FILTER (WHERE upload_id IS NOT NULL) AS web_rows,
                             COUNT(*) FILTER (WHERE result = FALSE) AS fail_rows, MIN(test_time) AS first_test, MAX(test_time) AS last_test,
                             array_agg(DISTINCT work_order) AS work_orders
                      FROM test_record {where}""", tuple(params))[0]
    batches = db.rows(f"""SELECT l.id, l.created_at, l.username, l.source_name, l.status, COUNT(*) AS rows
                          FROM test_record t JOIN upload_log l ON l.id = t.upload_id
                          WHERE t.id IN ({ids_sql}) GROUP BY 1, 2, 3, 4, 5 ORDER BY 2""", tuple(params))
    side = db.rows(f"""SELECT (SELECT COUNT(*) FROM fail_analysis WHERE record_id IN ({ids_sql})) AS fail_analyses,
                              (SELECT COUNT(*) FROM fail_analysis WHERE record_id IN ({ids_sql}) AND rd_status <> 'new') AS rd_triaged,
                              (SELECT COUNT(*) FROM fail_disposition WHERE record_id IN ({ids_sql})) AS dispositions""",
                   tuple(params) * 3)[0]
    wos = [w for w in (row["work_orders"] or []) if w]
    effect = []
    for wo in wos[:5]:
        before = db.rows("""WITH r AS (SELECT result, ROW_NUMBER() OVER (PARTITION BY sn ORDER BY test_time DESC, id DESC) AS rn
                                       FROM test_record WHERE work_order = %s)
                            SELECT COUNT(*) AS units, COUNT(*) FILTER (WHERE result) AS pass FROM r WHERE rn = 1""", (wo,))[0]
        after = db.rows(f"""WITH r AS (SELECT result, ROW_NUMBER() OVER (PARTITION BY sn ORDER BY test_time DESC, id DESC) AS rn
                                       FROM test_record WHERE work_order = %s AND id NOT IN ({ids_sql}))
                            SELECT COUNT(*) AS units, COUNT(*) FILTER (WHERE result) AS pass FROM r WHERE rn = 1""", (wo, *params))[0]
        plan = db.rows("SELECT planned_qty FROM wo_plan WHERE work_order = %s", (wo,))
        pct = lambda x: round(int(x["pass"]) * 100.0 / int(x["units"]), 2) if int(x["units"]) else None  # noqa: E731
        effect.append({"work_order": wo, "units_before": int(before["units"]), "yield_before": pct(before),
                       "units_after": int(after["units"]), "yield_after": pct(after), "planned_qty": plan[0]["planned_qty"] if plan else None})
    return {"rows": int(row["rows"]), "units": int(row["units"]), "web_rows": int(row["web_rows"]), "fail_rows": int(row["fail_rows"]),
            "first_test": row["first_test"], "last_test": row["last_test"], "batches": [{**b, "rows": int(b["rows"])} for b in batches],
            **{k: int(v) for k, v in side.items()}, "work_orders": effect}


EXPORT_COLUMNS = ("id, test_time, work_order, product_model, sn, wifi_mac, bt_mac, result, band_result_24g, band_result_5g, bt_result, "
                  "avg_24g, min_24g, max_24g, std_24g, rssi_after_24g, avg_5g, min_5g, max_5g, std_5g, rssi_after_5g, "
                  "bt_avg_latency, bt_loss_rate, source_file, file_hash, created_at, upload_id")


@router.get("/export.csv")
def export_csv(work_order: Optional[str] = Query(None, max_length=64), product_model: Optional[str] = Query(None, max_length=128)):
    """The filtered records as CSV (5D, Q19 B): every column except the raw log."""
    import csv
    import io
    where, params = _scope(work_order, product_model, None)
    rows = db.rows(f"SELECT {EXPORT_COLUMNS} FROM test_record {where} ORDER BY test_time, id", tuple(params))
    buf = io.StringIO()
    buf.write("\ufeff")
    out = csv.writer(buf)
    cols = [c.strip() for c in EXPORT_COLUMNS.split(",")]
    out.writerow(cols)
    for r in rows:
        out.writerow([r.get(c) for c in cols])
    name = f"test_record_{(work_order or 'all').replace('/', '_')}.csv"
    return Response(content=buf.getvalue().encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


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
