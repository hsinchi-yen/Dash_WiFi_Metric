"""Work-order detail page (5B).

GET    /api/wo-detail          status, numbers, plan, trend, throughput, previous same-product work order
GET    /api/wo-compare         this work order against any other one
PUT    /api/wo-plan            administrator: set the planned quantity (kept when upload batches are undone)
DELETE /api/wo-plan            administrator: clear it (falls back to the newest upload batch's value)
GET    /api/wo-report          the structured final report (L3) and its facts
POST   /api/wo-report/generate (re)write the report now
POST   /api/wo-report/ask      a follow-up question, answered from this work order's facts only
GET    /api/wo-batches         web upload batches of the work order (signed in; uploaders see their own)
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

import config
import db
from errors import ApiError, Msg, bad_request, not_found
from schemas import AskBody, PlanBody, WorkOrderKey
from services import auth, llm, ratelimit, wo_detail, wo_notes, wo_report, wo_status

router = APIRouter(tags=["work order"])

NOT_FOUND = Msg("找不到這張工單。", "Work order not found.")
_limit = ratelimit.limit       # the pages are open to everyone (Q18, Q25): per-address limits on LLM calls


def _key(work_order: str, product_model: Optional[str]):
    key = wo_detail.find(work_order, product_model or None)
    if not key:
        raise not_found(NOT_FOUND, "no_work_order")
    return key


@router.get("/api/wo-detail")
def detail(work_order: str = Query(..., max_length=64), product_model: Optional[str] = Query(None, max_length=128)):
    key = _key(work_order, product_model)
    out = wo_detail.detail(key)
    out["status"] = {**out["status"], "note": wo_notes.notes_for({key: out["status"]})[key]}
    return out


@router.get("/api/wo-compare")
def compare(a: str = Query(..., max_length=64), b: str = Query(..., max_length=64),
            pa: Optional[str] = Query(None, max_length=128), pb: Optional[str] = Query(None, max_length=128)):
    ka, kb = _key(a, pa), _key(b, pb)
    if ka == kb:
        raise bad_request(Msg("請選另一張工單比較。", "Choose a different work order to compare with."), "same_work_order")
    return wo_detail.compare(ka, kb)


@router.put("/api/wo-plan")
def set_plan(request: Request, body: PlanBody, admin=Depends(auth.require_admin)):
    key = _key(body.work_order, body.product_model)
    if not 1 <= body.planned_qty <= config.PLAN_QTY_MAX:
        raise bad_request(Msg(f"計畫數請輸入 1 到 {config.PLAN_QTY_MAX:,}。", f"Enter a planned quantity from 1 to {config.PLAN_QTY_MAX:,}."), "bad_qty")
    with db.db() as conn:
        before = db.fetch_one(conn, "SELECT planned_qty FROM wo_plan WHERE work_order = %s", (key[0],))
        db.execute(conn, """
            INSERT INTO wo_plan (work_order, product_model, planned_qty, updated_by, updated_at, upload_id, admin_qty, admin_by, admin_at)
            VALUES (%s, %s, %s, %s, NOW(), NULL, %s, %s, NOW())
            ON CONFLICT (work_order) DO UPDATE SET
                product_model = EXCLUDED.product_model, planned_qty = EXCLUDED.planned_qty, updated_by = EXCLUDED.updated_by,
                updated_at = NOW(), upload_id = NULL, admin_qty = EXCLUDED.admin_qty, admin_by = EXCLUDED.admin_by, admin_at = NOW()
        """, (key[0], key[1], body.planned_qty, admin["username"], body.planned_qty, admin["username"]))
        auth.audit(conn, request, admin["username"], "plan_set", key[0],
                   {"planned_qty": f"{before['planned_qty'] if before else '—'} → {body.planned_qty}"})
    wo_status.invalidate()
    return {"ok": True, "plan": wo_detail.plan(key[0])}


@router.delete("/api/wo-plan")
def clear_plan(request: Request, work_order: str = Query(..., max_length=64), admin=Depends(auth.require_admin)):
    with db.db() as conn:
        row = db.fetch_one(conn, "SELECT planned_qty, upload_id, admin_qty FROM wo_plan WHERE work_order = %s", (work_order,))
        if not row:
            raise not_found(Msg("這張工單沒有計畫數。", "This work order has no planned quantity."), "no_plan")
        after = None
        if row["upload_id"] is None:      # the administrator's value is in use: fall back to the newest batch
            newest = db.fetch_one(conn, """
                SELECT id, planned_qty, product_model, username FROM upload_log
                WHERE work_order = %s AND status = 'done' AND planned_qty IS NOT NULL
                ORDER BY finished_at DESC NULLS LAST, created_at DESC LIMIT 1
            """, (work_order,))
            if newest:
                db.execute(conn, """
                    UPDATE wo_plan SET planned_qty = %s, upload_id = %s, updated_by = %s, updated_at = NOW(),
                                       admin_qty = NULL, admin_by = NULL, admin_at = NULL WHERE work_order = %s
                """, (newest["planned_qty"], newest["id"], newest["username"], work_order))
                after = newest["planned_qty"]
            else:
                db.execute(conn, "DELETE FROM wo_plan WHERE work_order = %s", (work_order,))
        else:
            db.execute(conn, "UPDATE wo_plan SET admin_qty = NULL, admin_by = NULL, admin_at = NULL WHERE work_order = %s", (work_order,))
            after = row["planned_qty"]
        auth.audit(conn, request, admin["username"], "plan_clear", work_order,
                   {"planned_qty": f"{row['planned_qty']} → {after if after is not None else '—'}"})
    wo_status.invalidate()
    return {"ok": True, "plan": wo_detail.plan(work_order)}


@router.get("/api/wo-report")
def get_report(work_order: str = Query(..., max_length=64), product_model: Optional[str] = Query(None, max_length=128)):
    return wo_report.get(_key(work_order, product_model))


@router.post("/api/wo-report/generate")
def generate_report(request: Request, body: WorkOrderKey, user=Depends(auth.optional_user)):
    key = _key(body.work_order, body.product_model)
    if not llm.status()["connected"]:
        raise ApiError(503, Msg("AI 服務離線，目前無法產生報告。", "The AI service is offline; reports cannot be written now."), "llm_offline")
    _limit(request, "report", 6)
    wo_report.request(key, user["username"] if user else None)
    return wo_report.get(key)


@router.post("/api/wo-report/ask")
def ask(request: Request, body: AskBody):
    key = _key(body.work_order, body.product_model)
    if not llm.status()["connected"]:
        raise ApiError(503, Msg("AI 服務離線，目前無法回答。", "The AI service is offline; questions cannot be answered now."), "llm_offline")
    _limit(request, "ask", 8)
    try:
        return wo_report.ask(key, body.question.strip(), [t.model_dump() for t in body.history], body.lang)
    except Exception as exc:
        raise ApiError(503, Msg("AI 回答失敗，請稍後再試。", "The AI could not answer. Please try again later."), "llm_failed") from exc


@router.get("/api/wo-batches")
def batches(work_order: str = Query(..., max_length=64), user=Depends(auth.current_user)):
    where, params = "WHERE l.work_order = %s AND l.status NOT IN ('analyzed', 'receiving')", [work_order]
    if user["role"] not in ("admin", "rd"):      # uploaders see their own batches; RD reads everything
        where += " AND l.username = %s"
        params.append(user["username"])
    with db.db() as conn:
        items = db.jsonable(db.fetch_all(conn, f"""
            SELECT l.id, l.created_at, l.finished_at, l.username, COALESCE(u.display_name, l.username) AS display_name,
                   l.source_name, l.total_files, l.planned_qty, l.status, l.uploaded, l.skipped, l.failed, l.reverted, l.mode
            FROM upload_log l LEFT JOIN app_users u ON u.username = l.username
            {where} ORDER BY l.created_at DESC LIMIT 50
        """, params))
    return {"items": items}
