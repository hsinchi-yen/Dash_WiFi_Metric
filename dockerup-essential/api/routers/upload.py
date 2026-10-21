"""Web Log Upload: zip / folder → facts → validate or AI chat → commit (NDJSON) → history / items / revert.

Administrators and uploaders can upload; uploaders only reach their own batches.
"""
import json
import queue
import threading
import zipfile
from datetime import datetime, timezone

import psycopg2.extras
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse

import config
import db
from errors import ApiError, Msg, bad_request, conflict
from schemas import ChatBody, CommitBody, FolderStartBody, ValidateBody
from services import auth, llm, products, upload, upload_chat
from services import upload_rules as rules

router = APIRouter(prefix="/api/log-upload", tags=["log-upload"])

NOT_FOUND_RETRY = Msg("找不到這個上傳批次，請重新選擇檔案", "Upload batch not found. Please choose the files again.")
REUSABLE = ("analyzed", "failed", "cancelled")


def _registry_items(conn):
    return [{"canonical": r["canonical"], "tn_number": r["tn_number"], "units": r["units"], "last_test": r["last_test"]}
            for r in products.listing(conn) if r["status"] == "active"]


def _analyzed_payload(conn, upload_id, facts):
    return {"upload_id": upload_id, "facts": facts,
            "chat_opening": upload_chat.opening_message(facts, "zh"), "chat_opening_en": upload_chat.opening_message(facts, "en"),
            "products": _registry_items(conn)}


def _insert_log(conn, request, user, upload_id, kind, name, size, status, facts=None):
    db.execute(conn, """
        INSERT INTO upload_log (id, client_ip, username, user_role, source_kind, source_name, source_bytes, status,
                                inferred_wo, inferred_product, unique_sn, total_files, facts)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (upload_id, auth.client_ip(request), user["username"], user["role"], kind, name, size, status,
          (facts or {}).get("inferred_wo"), (facts or {}).get("inferred_product"), (facts or {}).get("unique_sn"),
          (facts or {}).get("logs"), psycopg2.extras.Json(facts) if facts else None))


def _store_facts(conn, upload_id, facts, size=None):
    db.execute(conn, """
        UPDATE upload_log SET status = 'analyzed', inferred_wo = %s, inferred_product = %s, unique_sn = %s, total_files = %s,
                              facts = %s, source_bytes = COALESCE(%s, source_bytes)
        WHERE id = %s
    """, (facts.get("inferred_wo"), facts.get("inferred_product"), facts.get("unique_sn"), facts.get("logs"),
          psycopg2.extras.Json(facts), size, upload_id))


# ─── Step 1: receive files ───────────────────────────────────────────────────

@router.post("/analyze")
def analyze(request: Request, file: UploadFile = File(...), user=Depends(auth.require_uploader)):
    """One zip (plain logs, or the desktop tool's nested delivery). Expanded and scanned; nothing is written to test_record."""
    name = (file.filename or "upload.zip").replace("\\", "/").rsplit("/", 1)[-1]
    if not name.lower().endswith(".zip"):
        raise bad_request(Msg("請選擇 .zip 壓縮檔，或改用「選擇資料夾」", "Please choose a .zip file, or use \"Choose folder\"."), "not_zip")
    upload.cleanup_stale()
    upload_id = upload.new_upload_id()
    try:
        size = upload.receive_zip(upload_id, file.file, name)
    except upload.LimitError as exc:
        upload.remove_upload_files(upload_id)
        raise bad_request(exc.msg, exc.code)
    except zipfile.BadZipFile:
        upload.remove_upload_files(upload_id)
        raise bad_request(Msg("檔案不是有效的 zip 壓縮檔", "The file is not a valid zip."), "bad_zip")
    except Exception:
        upload.remove_upload_files(upload_id)
        raise bad_request(Msg("無法讀取壓縮檔內容，檔案可能已損壞", "Cannot read the zip; the file may be damaged."), "bad_zip")
    with db.db() as conn:
        facts = upload.build_facts(conn, upload_id, "zip", name, size)
        _insert_log(conn, request, user, upload_id, "zip", name, size, "analyzed", facts)
        return _analyzed_payload(conn, upload_id, facts)


@router.post("/folder/start")
def folder_start(request: Request, body: FolderStartBody, user=Depends(auth.require_uploader)):
    """Begin a folder upload; the browser then sends the files in batches of ≤ UPLOAD_BATCH_MAX_FILES."""
    if body.files > config.UPLOAD_MAX_FILES:
        raise bad_request(Msg(f"資料夾超過 {config.UPLOAD_MAX_FILES:,} 個檔案上限", f"The folder exceeds the {config.UPLOAD_MAX_FILES:,}-file limit."), "too_many")
    upload.cleanup_stale()
    upload_id = upload.new_upload_id()
    with db.db() as conn:
        _insert_log(conn, request, user, upload_id, "folder", body.name.strip() or "folder", body.bytes, "receiving")
    return {"upload_id": upload_id, "batch_max_files": config.UPLOAD_BATCH_MAX_FILES}


@router.post("/folder/{upload_id}/files")
def folder_files(upload_id: str, files: list[UploadFile] = File(...), paths: list[str] = Form(default=[]),
                 user=Depends(auth.require_uploader)):
    with db.db() as conn:
        row = upload.owned_upload(conn, upload_id, user, NOT_FOUND_RETRY)
        if row["status"] != "receiving":
            raise conflict(Msg("這個批次已經結束接收", "This batch is no longer receiving files."), "not_receiving")
    if len(files) > config.UPLOAD_BATCH_MAX_FILES:
        raise bad_request(Msg(f"每批最多 {config.UPLOAD_BATCH_MAX_FILES} 個檔案", f"At most {config.UPLOAD_BATCH_MAX_FILES} files per batch."), "batch_too_big")
    try:
        return upload.receive_folder_batch(upload_id, files, paths)
    except upload.LimitError as exc:
        raise bad_request(exc.msg, exc.code)


@router.post("/folder/{upload_id}/finish")
def folder_finish(upload_id: str, user=Depends(auth.require_uploader)):
    with db.db() as conn:
        row = upload.owned_upload(conn, upload_id, user, NOT_FOUND_RETRY)
        if row["status"] != "receiving":
            raise conflict(Msg("這個批次已經結束接收", "This batch is no longer receiving files."), "not_receiving")
        facts = upload.build_facts(conn, upload_id, "folder", row["source_name"], row["source_bytes"] or 0)
        _store_facts(conn, upload_id, facts)
        return _analyzed_payload(conn, upload_id, facts)


# ─── Step 2: validate (form) / chat (AI) ─────────────────────────────────────

@router.post("/validate")
def validate(body: ValidateBody, user=Depends(auth.require_uploader)):
    """Rules R1–R16 for the entered product / work order / planned quantity."""
    with db.db() as conn:
        row = upload.owned_upload(conn, body.upload_id, user, NOT_FOUND_RETRY)
        if row["status"] == "receiving":
            raise conflict(Msg("資料夾還在接收中", "The folder is still being received."), "receiving")
        fields, issues = upload.validate(conn, row, body.work_order or "", body.product or "", body.planned_qty)
    return upload.validation_payload(fields, issues)


def _chat_extract(messages, registry):
    """LLM extraction → (data or None, status ok | bad_output | unreachable)."""
    try:
        data = llm.complete_json(upload_chat.build_extract_messages(messages, registry.names()),
                                 upload_chat.extract_schema(registry.names()), name="upload_fields",
                                 temperature=0, max_tokens=upload_chat.EXTRACT_MAX_TOKENS)
        return upload_chat.parse_extract(json.dumps(data)), "ok"
    except (ValueError, TypeError):
        return None, "bad_output"
    except Exception:
        return None, "unreachable"


@router.post("/chat")
def chat(body: ChatBody, user=Depends(auth.require_uploader)):
    """One assistant turn → NDJSON: extract, validation, delta…, done | fallback. The rules decide; the LLM phrases."""
    try:
        messages = upload_chat.normalize_messages(body.messages)
    except ValueError as exc:
        raise bad_request(exc.args[0], "bad_messages")
    lang = upload_chat.ui_lang(body.lang)
    with db.db() as conn:
        row = upload.owned_upload(conn, body.upload_id, user, NOT_FOUND_RETRY)
        if row["status"] not in REUSABLE:
            raise conflict(Msg(f"這個批次已經處理過（狀態：{row['status']}）", f"This batch has already been processed (status: {row['status']})."), "already_processed")
        facts = row["facts"]
        registry = products.load(conn)
        data, status = _chat_extract(messages, registry)
        extracted = upload_chat.verify_extract(messages[-1]["content"], data, registry)
        fields = upload_chat.merge_fields(body.known, extracted)
        resolved, issues = upload.validate(conn, row, fields["work_order"] or "", fields["product"] or "", fields["planned_qty"])
    payload = upload.validation_payload(resolved, issues)
    step = upload_chat.next_step(resolved)

    def events():
        yield upload.ndjson({"type": "extract", **fields, "nowo": extracted.get("nowo", False), "step": step, "extract_ok": status == "ok"})
        yield upload.ndjson({"type": "validation", **payload})
        template = upload_chat.fallback_reply(resolved, issues, lang)
        if status == "unreachable":
            yield upload.ndjson({"type": "delta", "text": template})
            yield upload.ndjson({"type": "done", "reply": template, "templated": True})
            yield upload.ndjson({"type": "fallback", "reason": "AI 助理目前無法連線", "reason_en": "The AI assistant is unreachable"})
            return
        parts = []
        try:
            for text in llm.stream_text(upload_chat.build_reply_messages(facts, resolved, issues, extracted, messages, lang),
                                        temperature=0.3, max_tokens=upload_chat.REPLY_MAX_TOKENS, purpose="upload_reply"):
                parts.append(text)
                yield upload.ndjson({"type": "delta", "text": text})
        except Exception:
            yield upload.ndjson({"type": "replace", "text": template})
            _save_transcript(body.upload_id, messages + [{"role": "assistant", "content": template}])
            yield upload.ndjson({"type": "done", "reply": template, "templated": True})
            return
        reply = "".join(parts).strip()
        if not reply or not upload_chat.guard_reply(reply, resolved, issues):
            reply = template
            yield upload.ndjson({"type": "replace", "text": reply})
        _save_transcript(body.upload_id, messages + [{"role": "assistant", "content": reply}])
        yield upload.ndjson({"type": "done", "reply": reply, "templated": reply == template})

    return StreamingResponse(events(), media_type="application/x-ndjson")


def _save_transcript(upload_id, transcript):
    try:
        with db.db() as conn:
            db.execute(conn, "UPDATE upload_log SET chat_transcript = %s WHERE id = %s AND status IN ('analyzed', 'failed', 'cancelled')",
                       (psycopg2.extras.Json(transcript), upload_id))
    except Exception:
        pass


# ─── Step 3: commit ──────────────────────────────────────────────────────────

@router.post("/commit")
def commit(body: CommitBody, user=Depends(auth.require_uploader)):
    """Re-validate against the current DB, then ingest in a background thread and stream progress as NDJSON.
    The ingest keeps running if the browser disconnects; the outcome is recorded in upload_log."""
    upload_id = body.upload_id
    conn = db.connect()
    handed_off = False
    try:
        row = upload.owned_upload(conn, upload_id, user, NOT_FOUND_RETRY)
        if row["status"] not in REUSABLE:
            raise conflict(Msg(f"這個批次已經處理過（狀態：{row['status']}）", f"This batch has already been processed (status: {row['status']})."), "already_processed")
        if not upload.manifest_exists(upload_id):
            raise ApiError(410, Msg("暫存檔已過期，請重新選擇檔案", "The uploaded files have expired. Please choose them again."), "expired")
        # Session-level lock; released when this connection closes (early return or ingest end).
        if not db.fetch_one(conn, "SELECT pg_try_advisory_lock(%s) AS ok", (config.UPLOAD_LOCK_KEY,))["ok"]:
            raise conflict(Msg("已有其他上傳正在進行，請稍後再試", "Another upload is in progress. Please try again later."), "upload_running")

        facts = row["facts"]
        fields, issues = upload.validate(conn, row, body.work_order or "", body.product or "", body.planned_qty)
        payload = upload.validation_payload(fields, issues)
        if not payload["can_upload"]:
            raise bad_request(Msg("輸入資料有誤，請修正後再上傳", "Please fix the errors before uploading."), "invalid", **payload)
        if payload["needs_ack"] and not body.ack_warnings:
            raise bad_request(Msg("有警告尚未確認", "Please confirm the warnings first."), "needs_ack", **payload)

        work_order = upload.next_nowo(conn) if fields["nowo_preview"] else fields["work_order"]
        product = payload["resolved"]["product"]
        new_product = payload["resolved"]["new_product"]
        qty = fields["planned_qty"]
        if new_product:
            products.add(conn, product, user["username"], source="upload", upload_id=upload_id)
        prev = db.fetch_one(conn, "SELECT planned_qty FROM wo_plan WHERE work_order = %s", (work_order,))
        db.execute(conn, """
            UPDATE upload_log
            SET work_order = %s, product_model = %s, planned_qty = %s, prev_plan = %s, new_product = %s, validation = %s,
                mode = %s, status = 'running', error = NULL, cancel_requested = FALSE, finished_at = NULL
            WHERE id = %s
        """, (work_order, product, qty, prev["planned_qty"] if prev else None, new_product, psycopg2.extras.Json(issues),
              body.mode, upload_id))
        conn.commit()

        events = queue.Queue()
        threading.Thread(target=upload.run_commit, args=(conn, events, upload_id, work_order, product, qty, user),
                         daemon=True, name=f"upload-{upload_id[:8]}").start()
        handed_off = True
        return StreamingResponse(upload.ndjson_events(events), media_type="application/x-ndjson")
    finally:
        if not handed_off:
            conn.close()


@router.post("/{upload_id}/cancel")
def cancel(upload_id: str, user=Depends(auth.require_uploader)):
    """Stop at the next 50-file boundary; rows already written stay (revertable) and the rest are marked cancelled."""
    with db.db() as conn:
        row = upload.owned_upload(conn, upload_id, user)
        if row["status"] != "running":
            raise conflict(Msg("這個批次沒有在上傳中", "This batch is not running."), "not_running")
        db.execute(conn, "UPDATE upload_log SET cancel_requested = TRUE WHERE id = %s", (upload_id,))
    return {"ok": True}


# ─── History, detail, per-file results, revert ───────────────────────────────

HISTORY_COLUMNS = """l.id, l.created_at, l.finished_at, l.username, l.user_role, COALESCE(u.display_name, l.username) AS display_name,
                     l.client_ip, l.source_kind, l.source_name, l.source_bytes, l.work_order, l.product_model, l.planned_qty, l.prev_plan,
                     l.new_product, l.unique_sn, l.total_files, l.mode, l.status, l.uploaded, l.skipped, l.failed,
                     l.reverted, l.reverted_by, l.reverted_at, l.elapsed_sec, l.error"""


@router.get("/history")
def history(limit: int = Query(20, ge=1, le=200), username: str = None, user=Depends(auth.require_uploader)):
    """Recent batches. Uploaders always get only their own; the administrator may filter by uploader."""
    if user["role"] != "admin":
        username = user["username"]
    where, params = "WHERE l.status NOT IN ('analyzed', 'receiving')", []
    if username:
        where += " AND l.username = %s"
        params.append(username)
    with db.db() as conn:
        items = db.jsonable(db.fetch_all(conn, f"""
            SELECT {HISTORY_COLUMNS} FROM upload_log l LEFT JOIN app_users u ON u.username = l.username
            {where} ORDER BY l.created_at DESC LIMIT %s
        """, params + [limit]))
    return {"items": items}


@router.get("/{upload_id}")
def detail(upload_id: str, user=Depends(auth.require_uploader)):
    with db.db() as conn:
        row = upload.owned_upload(conn, upload_id, user)
        row.pop("cancel_requested", None)
        return db.jsonable([row])[0]


@router.get("/{upload_id}/items")
def items(upload_id: str, format: str = Query("json", pattern="^(json|csv|txt)$"), user=Depends(auth.require_uploader)):
    """Per-file results, as the desktop tool's db_upload_log_<ts>.csv / .txt (Index, Filename, Status, Message)."""
    with db.db() as conn:
        row = upload.owned_upload(conn, upload_id, user)
        rows = db.fetch_all(conn, "SELECT idx, filename, status, message, record_id FROM upload_log_item WHERE upload_id = %s ORDER BY idx", (upload_id,))
    if format == "json":
        return {"items": rows, "upload_id": upload_id}
    when = row["finished_at"] or row["created_at"] or datetime.now(timezone.utc)
    text = upload.items_text(rows, format, upload_id, when)
    name = f"db_upload_log_{when:%Y%m%d_%H%M%S}.{format}"
    return Response(content=text, media_type="text/csv" if format == "csv" else "text/plain",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.delete("/{upload_id}")
def revert(upload_id: str, user=Depends(auth.require_uploader)):
    """Undo one web upload: delete only the rows it inserted, restore the previous plan, drop a new product left without data."""
    with db.db() as conn:
        row = upload.owned_upload(conn, upload_id, user)
        if row["status"] not in ("done", "failed", "cancelled"):
            raise bad_request(Msg(f"只有已完成、失敗或取消的批次可以撤銷（目前狀態：{row['status']}）",
                                  f"Only finished, failed or cancelled batches can be undone (current status: {row['status']})."), "not_revertible")
        reverted = upload.revert(conn, row, user)
    upload._after_change()
    return {"reverted": reverted, "upload_id": upload_id}
