"""Failure classification (5F-1), read side.

GET /api/fail-taxonomy          categories (names, descriptions, likely stage, suggested action) and the stage list
GET /api/fail-summary           FAIL runs by category × retest outcome and by station, in a scope
GET /api/fail-analysis/{id}     one FAIL run's classification: measurements, evidence lines, station, retest
"""
from typing import Optional

from fastapi import APIRouter, Query

import db
from errors import Msg, not_found
from services import fail_classify, fail_taxonomy

router = APIRouter(tags=["failures"])
# A new Query() per parameter: one shared Query object for date_from AND date_to makes FastAPI see a single
# parameter, so both received date_from (every date range shrank to its first day).
def iso_date():
    return Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


@router.get("/api/fail-taxonomy")
def taxonomy():
    return {"items": fail_taxonomy.listing(),
            "stages": [{"code": k, "name_zh": v[0], "name_en": v[1]} for k, v in fail_taxonomy.STAGES.items()]}


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
    return rows[0]
