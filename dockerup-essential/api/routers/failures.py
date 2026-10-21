"""Failure classification (5F-1) and FAIL disposition (5C).

GET /api/fail-taxonomy          categories (names, descriptions, likely stage, suggested action) and the stage list
GET /api/fail-summary           FAIL runs by category × retest outcome and by station, in a scope
GET /api/fail-analysis/{id}     one FAIL run: classification, measurements, evidence lines, station, retest,
                                suggested next step, disposition and its history, the unit's runs
PUT /api/fail-disposition       any signed-in account: set what the line did with a FAIL run (audited)
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

import db
from errors import ApiError, Msg, not_found
from schemas import DispositionBody
from services import auth, fail_actions, fail_classify, fail_disposition, fail_taxonomy, rd, settings

router = APIRouter(tags=["failures"])
# A new Query() per parameter: one shared Query object for date_from AND date_to makes FastAPI see a single
# parameter, so both received date_from (every date range shrank to its first day).
def iso_date():
    return Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


CHANGE_ERRORS = {
    "bad_status": Msg("處置狀態不正確。", "Unknown disposition."),
    "bad_note": Msg(f"備註最多 {fail_actions.NOTE_MAX} 字。", f"The note can be at most {fail_actions.NOTE_MAX} characters."),
    "note_required": Msg("結案請在備註寫明原因（例如報廢）。", "Closing needs a note saying why (for example scrapped)."),
}


@router.get("/api/fail-taxonomy")
def taxonomy():
    return {"items": fail_taxonomy.listing(),
            "stages": [{"code": k, "name_zh": v[0], "name_en": v[1]} for k, v in fail_taxonomy.STAGES.items()],
            "dispositions": list(fail_actions.STATUSES)}


@router.get("/api/fail-summary")
def summary(work_order: Optional[str] = Query(None, max_length=64), product_model: Optional[str] = Query(None, max_length=128),
            date_from: Optional[str] = iso_date(), date_to: Optional[str] = iso_date()):
    return fail_classify.summary(work_order or None, product_model or None, date_from, date_to)


@router.get("/api/fail-analysis/{record_id}")
def analysis(record_id: int):
    rows = db.rows("""
        SELECT f.*, t.name_zh, t.name_en, t.description_zh, t.description_en, t.action_zh, t.action_en, t.likely_stage
        FROM fail_analysis f LEFT JOIN fail_taxonomy t ON t.code = f.category WHERE f.record_id = %s
    """, (record_id,))
    if not rows:
        raise not_found(Msg("這筆測試沒有失敗分類。", "This test run has no failure classification."), "no_analysis")
    a = rows[0]
    stored = fail_disposition.for_records([record_id]).get(record_id)
    near = fail_disposition.clusters([record_id]).get(record_id, 0)
    a["disposition"] = fail_actions.effective(a, stored)
    a["suggest"] = fail_actions.suggest(a, near, int(settings.get()["fail_streak"]))
    a["history"] = rd.history(record_id)          # disposition changes and RD triage, newest first
    a["runs"] = fail_disposition.runs_of(a["sn"]) if a.get("sn") else []
    mac = db.rows("SELECT wifi_mac, bt_mac FROM test_record WHERE id = %s", (record_id,))
    a.update(mac[0] if mac else {"wifi_mac": None, "bt_mac": None})
    return a


@router.put("/api/fail-disposition")
def set_disposition(request: Request, body: DispositionBody, user=Depends(auth.current_user)):
    error = fail_actions.check_change(body.status, body.note)
    if error:
        raise ApiError(400, CHANGE_ERRORS[error], error)
    with db.db() as conn:
        run = fail_disposition.fail_run(conn, body.record_id)
        if not run:
            raise not_found(Msg("找不到這筆 FAIL 測試（可能已被刪除）。", "This FAIL test run was not found (it may have been deleted)."), "no_fail")
        a = db.fetch_one(conn, "SELECT retest_outcome FROM fail_analysis WHERE record_id = %s", (body.record_id,))
        if a and a["retest_outcome"] == "pass_on_retest":
            raise ApiError(409, Msg("這台重測已通過，已自動結案。", "This unit passed on retest; it is closed automatically."), "closed_by_retest")
        if a and a["retest_outcome"] == "failed_again":
            raise ApiError(409, Msg("這台之後又測過，請在最後一次 FAIL 登記處置。", "This unit was tested again later; set the disposition on its latest FAIL."), "superseded")
        row = fail_disposition.set_status(conn, request, user, body.record_id, run["sn"], body.status, body.note)
    return {"ok": True, "disposition": fail_actions.effective(a, row), "history": rd.history(body.record_id)}
