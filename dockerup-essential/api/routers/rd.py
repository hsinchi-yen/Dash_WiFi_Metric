"""RD advanced analysis (5F-2): RD and the administrator only (Q34: SN, MAC and raw logs are not for everyone).

Filters are query parameters (or `filters` in a body): date_from, date_to, work_order, product_model, category, band,
ssid_group, station_port, retest, classifier, rd_status, stage (comma-separated values), mbps_min / mbps_max,
rssi_min / rssi_max, search, still_failing=1.

GET   /api/rd/fails              FAIL runs (paged) + the filter values present in the scope
GET   /api/rd/group?by=          runs per value of one dimension (Pareto order with cumulative %)
GET   /api/rd/crosstab?rows=&cols=   runs per pair of values
PATCH /api/rd/fails              batch triage {ids, changes}: category, stage, rd_status, rd_owner, rd_note, rd_action
GET   /api/rd/history/{id}       RD and disposition changes of one FAIL run
GET   /api/rd/sn/{sn}            every test of one unit
GET   /api/rd/log/{id}           one raw log as lines, with the evidence / signal line numbers (log viewer)
GET   /api/rd/similar/{id}       FAIL runs with the same signature, and on the same station within 2 hours
GET   /api/rd/export.csv         the filtered FAIL runs as CSV
GET   /api/rd/export-logs.zip    their raw logs (newest 500)
POST  /api/rd/ask                L2b: the LLM on the filtered group's aggregates (rate limited)
POST  /api/rd/fails/reset        undo RD's triage of some runs: the rules classify them again
POST  /api/fail-taxonomy         add a code;  PATCH /api/fail-taxonomy/{code}  edit / disable;  POST …/{code}/merge;
DELETE /api/fail-taxonomy/{code} a code RD added that nothing uses
"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

import db
from errors import ApiError, Msg, bad_request, not_found
from services import auth, fail_taxonomy, llm, ratelimit, rd

router = APIRouter(tags=["rd"])

CHANGE_ERRORS = {
    "no_change": Msg("沒有要變更的欄位。", "Nothing to change."),
    "bad_category": Msg("分類不存在或已停用。", "Unknown or disabled category."),
    "bad_stage": Msg("環節不正確。", "Unknown stage."),
    "bad_status": Msg("RD 狀態不正確。", "Unknown RD status."),
    "bad_rd_owner": Msg("負責人最多 64 字。", "The owner can be at most 64 characters."),
    "bad_rd_note": Msg("備註最多 500 字。", "The note can be at most 500 characters."),
    "bad_rd_action": Msg("給產線的指示最多 200 字。", "The instruction for the line can be at most 200 characters."),
    "bad_field": Msg("有不能變更的欄位。", "A field that cannot be changed."),
}
TAX_ERRORS = {
    "bad_code": Msg("代碼為 3–32 個大寫英文、數字或底線，以英文字母開頭。", "Codes are 3–32 capital letters, digits or underscores, starting with a letter."),
    "name_required": Msg("請填中英文名稱。", "Enter the Chinese and English names."),
    "bad_stage": Msg("環節不正確。", "Unknown stage."),
    "bad_severity": Msg("嚴重度為 1–9。", "Severity is 1–9."),
    "bad_sort": Msg("排序為 0–999。", "Sort order is 0–999."),
}


def _filters(request: Request) -> dict:
    return rd.parse_filters(dict(request.query_params))


@router.get("/api/rd/fails")
def fails(request: Request, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=rd.LIST_MAX),
          sort: str = Query("time", pattern="^(time|mbps|category|station)$"), _user=Depends(auth.require_rd)):
    f = _filters(request)
    return {**rd.listing(f, page, page_size, sort), "facets": rd.facets(f), "filters": f}


@router.get("/api/rd/group")
def group(request: Request, by: str = Query("category", max_length=16), _user=Depends(auth.require_rd)):
    return rd.group(_filters(request), by)


@router.get("/api/rd/crosstab")
def crosstab(request: Request, rows: str = Query("category", max_length=16), cols: str = Query("station", max_length=16),
             top: int = Query(12, ge=2, le=40), _user=Depends(auth.require_rd)):
    return rd.crosstab(_filters(request), rows, cols, top)


class RdChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Optional[StrictStr] = Field(None, max_length=32)
    stage: Optional[StrictStr] = Field(None, max_length=16)
    rd_status: Optional[StrictStr] = Field(None, max_length=16)
    rd_owner: Optional[StrictStr] = Field(None, max_length=200)
    rd_note: Optional[StrictStr] = Field(None, max_length=1000)
    rd_action: Optional[StrictStr] = Field(None, max_length=400)


class RdUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[StrictInt] = Field(..., min_length=1, max_length=rd.LIST_MAX)
    changes: RdChanges


@router.patch("/api/rd/fails")
def update(request: Request, body: RdUpdateBody, user=Depends(auth.require_rd)):
    changes = body.changes.model_dump(exclude_unset=True)
    codes = {t["code"] for t in fail_taxonomy.listing() if t["enabled"]}
    error = rd.check_changes(changes, codes)
    if error:
        raise ApiError(400, CHANGE_ERRORS[error], error)
    ids = sorted(set(body.ids))
    with db.db() as conn:
        found = {int(r["record_id"]) for r in db.fetch_all(conn, "SELECT record_id FROM fail_analysis WHERE record_id = ANY(%s)", (ids,))}
        missing = [i for i in ids if i not in found]
        if missing:
            raise not_found(Msg(f"找不到 {len(missing)} 筆 FAIL 分類記錄。", f"{len(missing)} FAIL runs were not found."), "no_fail")
        n = rd.update(conn, request, user, ids, changes)
    return {"ok": True, "updated": n}


class RdIdsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[StrictInt] = Field(..., min_length=1, max_length=rd.LIST_MAX)


@router.post("/api/rd/fails/reset")
def reset(request: Request, body: RdIdsBody, user=Depends(auth.require_rd)):
    """Back to the rules' classification (RD's category, stage, status, owner, note and instruction are removed)."""
    ids = sorted(set(body.ids))
    with db.db() as conn:
        n = rd.reset(conn, request, user, ids)
    from services import fail_classify
    fail_classify.scan()          # classify them again now instead of within the minute
    return {"ok": True, "reset": n}


@router.get("/api/rd/history/{record_id}")
def history(record_id: int, _user=Depends(auth.require_rd)):
    return {"items": rd.history(record_id)}


@router.get("/api/rd/sn/{sn}")
def sn_history(sn: str, _user=Depends(auth.require_rd)):
    if len(sn) > 64:
        raise bad_request(Msg("SN 太長。", "The SN is too long."), "bad_sn")
    runs = rd.sn_history(sn)
    if not runs:
        raise not_found(Msg("找不到這個 SN 的測試。", "No tests of this SN."), "no_sn")
    return {"sn": sn, "runs": runs}


@router.get("/api/rd/log/{record_id}")
def log_view(record_id: int, _user=Depends(auth.require_rd)):
    out = rd.log_view(record_id)
    if not out:
        raise not_found(Msg("找不到這筆測試。", "Test run not found."), "no_record")
    return out


@router.get("/api/rd/similar/{record_id}")
def similar(record_id: int, _user=Depends(auth.require_rd)):
    return rd.similar(record_id)


@router.get("/api/rd/export.csv")
def export_csv(request: Request, _user=Depends(auth.require_rd)):
    return Response(content=rd.export_csv(_filters(request)).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="fail_analysis.csv"'})


@router.get("/api/rd/export-logs.zip")
def export_logs(request: Request, _user=Depends(auth.require_rd)):
    data, included, total = rd.export_logs(_filters(request))
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="fail_logs.zip"',
                             "X-Included": str(included), "X-Total": str(total)})


class AskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: dict = Field(default_factory=dict)
    question: Optional[StrictStr] = Field(None, max_length=300)
    lang: Literal["zh", "en"] = "zh"


@router.post("/api/rd/ask")
def ask(request: Request, body: AskBody, _user=Depends(auth.require_rd)):
    if not llm.status()["connected"]:
        raise ApiError(503, Msg("AI 服務離線，目前無法回答。", "The AI service is offline; questions cannot be answered now."), "llm_offline")
    ratelimit.limit(request, "rd_ask", 6)
    try:
        return rd.ask(rd.parse_filters(body.filters), (body.question or "").strip(), body.lang)
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(503, Msg("AI 回答失敗，請稍後再試。", "The AI could not answer. Please try again later."), "llm_failed") from exc


# ─── Taxonomy (RD maintains it) ──────────────────────────────────────────────

class TaxonomyFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name_zh: Optional[StrictStr] = Field(None, max_length=120)
    name_en: Optional[StrictStr] = Field(None, max_length=160)
    description_zh: Optional[StrictStr] = Field(None, max_length=600)
    description_en: Optional[StrictStr] = Field(None, max_length=800)
    action_zh: Optional[StrictStr] = Field(None, max_length=400)
    action_en: Optional[StrictStr] = Field(None, max_length=600)
    likely_stage: Optional[StrictStr] = Field(None, max_length=16)
    severity: Optional[StrictInt] = None
    sort: Optional[StrictInt] = None
    enabled: Optional[StrictBool] = None


class TaxonomyCreate(TaxonomyFields):
    code: StrictStr = Field(..., max_length=40)


class MergeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    into: StrictStr = Field(..., max_length=40)


def _tax_error(code):
    raise ApiError(400, TAX_ERRORS.get(code, Msg("欄位內容不正確。", "Invalid field.")), code)


@router.post("/api/fail-taxonomy")
def taxonomy_create(request: Request, body: TaxonomyCreate, user=Depends(auth.require_rd)):
    values = body.model_dump(exclude_unset=True)
    values["code"] = (values.get("code") or "").strip().upper()
    error = fail_taxonomy.check_fields(values, creating=True)
    if error:
        _tax_error(error)
    with db.db() as conn:
        if db.fetch_one(conn, "SELECT 1 FROM fail_taxonomy WHERE code = %s", (values["code"],)):
            raise ApiError(409, Msg("這個代碼已存在。", "This code already exists."), "code_exists")
        cols = ["code", "name_zh", "name_en", "description_zh", "description_en", "action_zh", "action_en", "likely_stage", "severity", "sort", "enabled"]
        row = {"severity": 5, "sort": 100, "enabled": True, "likely_stage": "unknown", **values}
        db.execute(conn, f"INSERT INTO fail_taxonomy ({', '.join(cols)}, updated_by) VALUES ({', '.join(['%s'] * len(cols))}, %s)",
                   (*[row.get(c) for c in cols], user["username"]))
        auth.audit(conn, request, user["username"], "taxonomy_add", values["code"], {k: v for k, v in values.items() if k != "code"})
    return {"ok": True, "items": fail_taxonomy.listing()}


@router.patch("/api/fail-taxonomy/{code}")
def taxonomy_update(request: Request, code: str, body: TaxonomyFields, user=Depends(auth.require_rd)):
    values = body.model_dump(exclude_unset=True)
    if not values:
        raise bad_request(Msg("沒有要變更的欄位。", "Nothing to change."), "no_change")
    error = fail_taxonomy.check_fields(values, creating=False)
    if error:
        _tax_error(error)
    if code == "UNKNOWN" and values.get("enabled") is False:
        raise bad_request(Msg("「未分類」不能停用。", "Unclassified cannot be disabled."), "keep_unknown")
    with db.db() as conn:
        before = db.fetch_one(conn, "SELECT * FROM fail_taxonomy WHERE code = %s FOR UPDATE", (code,))
        if not before:
            raise not_found(Msg("找不到這個分類。", "Category not found."), "no_category")
        sets = ", ".join(f"{k} = %s" for k in values)
        db.execute(conn, f"UPDATE fail_taxonomy SET {sets}, updated_by = %s, updated_at = NOW() WHERE code = %s",
                   (*values.values(), user["username"], code))
        auth.audit(conn, request, user["username"], "taxonomy_edit", code,
                   {k: f"{before.get(k)} → {v}" for k, v in values.items() if before.get(k) != v})
    return {"ok": True, "items": fail_taxonomy.listing()}


@router.delete("/api/fail-taxonomy/{code}")
def taxonomy_delete(request: Request, code: str, user=Depends(auth.require_rd)):
    """Remove a category added by mistake: only one RD added (not a seeded one) that no FAIL run uses."""
    if code in {c[0] for c in fail_taxonomy.SEED}:
        raise bad_request(Msg("內建分類不能刪除，可以停用。", "Built-in categories cannot be deleted; disable them instead."), "seeded")
    with db.db() as conn:
        if not db.fetch_one(conn, "SELECT 1 FROM fail_taxonomy WHERE code = %s", (code,)):
            raise not_found(Msg("找不到這個分類。", "Category not found."), "no_category")
        used = db.fetch_one(conn, "SELECT COUNT(*) AS n FROM fail_analysis WHERE category = %s OR %s = ANY(secondary)", (code, code))["n"]
        if used:
            raise ApiError(409, Msg(f"還有 {used} 筆 FAIL 用這個分類，請先合併。", f"{used} FAIL runs use this category; merge it instead."), "in_use")
        db.execute(conn, "UPDATE fail_taxonomy SET merged_into = NULL WHERE merged_into = %s", (code,))
        db.execute(conn, "DELETE FROM fail_taxonomy WHERE code = %s", (code,))
        auth.audit(conn, request, user["username"], "taxonomy_delete", code, None)
    return {"ok": True, "items": fail_taxonomy.listing()}


@router.post("/api/fail-taxonomy/{code}/merge")
def taxonomy_merge(request: Request, code: str, body: MergeBody, user=Depends(auth.require_rd)):
    into = body.into.strip().upper()
    if code == into:
        raise bad_request(Msg("請選另一個分類。", "Choose a different category."), "same_category")
    if code == "UNKNOWN":
        raise bad_request(Msg("「未分類」不能合併。", "Unclassified cannot be merged."), "keep_unknown")
    with db.db() as conn:
        rows = {r["code"]: r for r in db.fetch_all(conn, "SELECT code, enabled FROM fail_taxonomy WHERE code IN (%s, %s) FOR UPDATE", (code, into))}
        if code not in rows or into not in rows:
            raise not_found(Msg("找不到這個分類。", "Category not found."), "no_category")
        if not rows[into]["enabled"]:
            raise bad_request(Msg("不能合併到已停用的分類。", "Cannot merge into a disabled category."), "disabled_target")
        n = fail_taxonomy.merge(conn, code, into, user["username"])
        auth.audit(conn, request, user["username"], "taxonomy_merge", code, {"into": into, "runs": n})
    return {"ok": True, "moved": n, "items": fail_taxonomy.listing()}
