"""AI reports: generated in the background, stored in ai_reports, served instantly.

A report belongs to (work order, product filter, language). Its data_hash is a hash of the
numbers it was written from (totals, yield, averages, top failure reasons); when the work
order's numbers change (new tests), the stored report is "stale" and a new one is queued.

get()                 → the stored report (+ queues generation when missing/stale)
request()             → queue a (re)generation on demand
process_queue_once()  → worker: generate one queued report (scheduler, every few seconds)
warm_once()           → worker: keep the newest REPORT_WARM_LATEST work orders current
"""
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import config
import db
from ai_summary_helper import build_summary_messages
from services import llm, stats
from services.filters import UNIT

log = logging.getLogger("wifi.reports")

LANGS = ("zh", "en")
PRIORITY_WARM, PRIORITY_OTHER_LANG, PRIORITY_OPEN, PRIORITY_MANUAL = 0, 5, 10, 20
KEY_WHERE = "work_order = %s AND product_model = %s AND lang = %s"


def facts(work_order: str, product_model: Optional[str]) -> tuple[Optional[dict], list, str]:
    """The numbers a report is written from, and their hash."""
    stats_row, fail_rows = stats.work_order_facts(work_order, product_model or None)
    if stats_row is None:
        return None, [], ""
    digest = hashlib.sha1(json.dumps({"s": stats_row, "f": fail_rows}, sort_keys=True, default=str).encode()).hexdigest()
    return stats_row, fail_rows, digest


def _key(product_model: Optional[str]) -> str:
    return product_model if product_model and product_model != "ALL" else ""


def _latest(conn, work_order, product, lang, status: Optional[str] = None):
    extra = " AND status = %s" if status else ""
    params = (work_order, product, lang) + ((status,) if status else ())
    row = db.fetch_one(conn, f"SELECT * FROM ai_reports WHERE {KEY_WHERE}{extra} ORDER BY requested_at DESC, id DESC LIMIT 1", params)
    return db.jsonable([row])[0] if row else None


def _pending(conn, work_order, product, lang):
    return db.fetch_one(conn, f"SELECT id, status FROM ai_reports WHERE {KEY_WHERE} AND status IN ('queued', 'running') LIMIT 1",
                        (work_order, product, lang))


def enqueue(conn, work_order, product, lang, priority: int) -> bool:
    """Queue one generation unless one is already queued or running; raise priority otherwise."""
    pending = _pending(conn, work_order, product, lang)
    if pending:
        if pending["status"] == "queued":
            db.execute(conn, "UPDATE ai_reports SET priority = GREATEST(priority, %s) WHERE id = %s", (priority, pending["id"]))
        return False
    db.execute(conn, "INSERT INTO ai_reports (work_order, product_model, lang, status, priority) VALUES (%s, %s, %s, 'queued', %s)",
               (work_order, product, lang, priority))
    return True


def _state(conn, work_order, product, lang, digest, auto_queue: bool, priority: int) -> dict[str, Any]:
    done = _latest(conn, work_order, product, lang, "done")
    pending = _pending(conn, work_order, product, lang)
    last = _latest(conn, work_order, product, lang)
    if done and done["data_hash"] == digest:
        status = "ready"
    elif done:
        status = "stale"
    else:
        status = "none"

    recently_failed = (last and last["status"] == "failed" and last.get("finished_at")
                       and (datetime.now(timezone.utc) - datetime.fromisoformat(last["finished_at"])).total_seconds() < config.REPORT_FAILED_RETRY_SEC)
    if status != "ready" and not pending and auto_queue and not recently_failed:
        enqueue(conn, work_order, product, lang, priority)
        pending = {"status": "queued"}
        # The other language is wanted too (the window has a 中文 / EN switch), at lower priority.
        for other in LANGS:
            if other != lang:
                other_done = _latest(conn, work_order, product, other, "done")
                if not other_done or other_done["data_hash"] != digest:
                    enqueue(conn, work_order, product, other, PRIORITY_OTHER_LANG)
    return {
        "work_order": work_order, "product_model": product or None, "lang": lang,
        "status": status, "generating": bool(pending), "stale": status == "stale",
        "text": done["text"] if done else None,
        "generated_at": done["finished_at"] if done else None,
        "model": done["model"] if done else None,
        "elapsed_sec": done["elapsed_sec"] if done else None,
        "error": last["error"] if (last and last["status"] == "failed" and status != "ready") else None,
    }


def get(work_order: str, product_model: Optional[str], lang: str) -> dict[str, Any]:
    """Stored report for the window; missing or stale reports are queued (status says so)."""
    product = _key(product_model)
    stats_row, _, digest = facts(work_order, product or None)
    if stats_row is None:
        return {"work_order": work_order, "product_model": product or None, "lang": lang, "status": "no_data",
                "generating": False, "stale": False, "text": None, "generated_at": None, "model": None, "elapsed_sec": None, "error": None}
    with db.db() as conn:
        return _state(conn, work_order, product, lang, digest, auto_queue=True, priority=PRIORITY_OPEN)


def request(work_order: str, product_model: Optional[str], lang: str) -> dict[str, Any]:
    """Queue a regeneration now (the window's Regenerate button)."""
    product = _key(product_model)
    stats_row, _, digest = facts(work_order, product or None)
    if stats_row is None:
        return get(work_order, product_model, lang)
    with db.db() as conn:
        enqueue(conn, work_order, product, lang, PRIORITY_MANUAL)
        for other in LANGS:
            if other != lang:
                enqueue(conn, work_order, product, other, PRIORITY_OTHER_LANG)
        return _state(conn, work_order, product, lang, digest, auto_queue=False, priority=PRIORITY_MANUAL)


# ─── Worker ──────────────────────────────────────────────────────────────────

def _generate(cur, row) -> None:
    work_order, product, lang = row["work_order"], row["product_model"], row["lang"]
    stats_row, fail_rows, digest = facts(work_order, product or None)
    if stats_row is None:
        cur.execute("UPDATE ai_reports SET status = 'failed', error = %s, finished_at = NOW() WHERE id = %s", ("no data", row["id"]))
        return
    if fail_rows:
        fails_text = ", ".join(f"{r['fail_reason']}({r['count']} units)" for r in fail_rows)
    else:
        fails_text = "No specific failures (all passed)" if lang == "en" else "無特定異常(或全數Pass)"
    messages = build_summary_messages(stats_row, fails_text, work_order, lang, "normal")
    t0 = time.perf_counter()
    try:
        text = llm.chat(messages, timeout=config.LLM_SUMMARY_TIMEOUT, max_tokens=config.LLM_REPORT_MAX_TOKENS).strip()
    except Exception as exc:
        log.warning("report %s/%s failed: %s", work_order, lang, exc)
        cur.execute("UPDATE ai_reports SET status = 'failed', error = %s, finished_at = NOW(), elapsed_sec = %s WHERE id = %s",
                    (str(exc)[:500], time.perf_counter() - t0, row["id"]))
        return
    elapsed = time.perf_counter() - t0
    cur.execute("""
        UPDATE ai_reports SET status = 'done', text = %s, data_hash = %s, model = %s, error = NULL,
                              finished_at = NOW(), elapsed_sec = %s
        WHERE id = %s
    """, (text, digest, config.LLM_MODEL, elapsed, row["id"]))
    log.info("report %s/%s generated in %.0f s", work_order, lang, elapsed)


def process_queue_once() -> bool:
    """Generate the highest-priority queued report, one at a time across workers.
    Returns True when a report was processed."""
    conn = db.connect()
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s) AS ok", (config.REPORT_WORKER_LOCK_KEY,))
        if not cur.fetchone()["ok"]:
            return False
        try:
            cur.execute("SELECT * FROM ai_reports WHERE status = 'queued' ORDER BY priority DESC, requested_at LIMIT 1")
            row = cur.fetchone()
            if not row:
                return False
            cur.execute("UPDATE ai_reports SET status = 'running', started_at = NOW() WHERE id = %s", (row["id"],))
            _generate(cur, dict(row))
            return True
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (config.REPORT_WORKER_LOCK_KEY,))
    finally:
        conn.close()


def warm_once() -> bool:
    """Queue one missing/stale report among the newest work orders. Returns True when queued."""
    targets = db.rows(f"""
        SELECT work_order, product_model
        FROM {UNIT}
        WHERE work_order IS NOT NULL AND work_order <> ''
        GROUP BY work_order, product_model
        ORDER BY MAX(test_time) DESC
        LIMIT %s
    """, (config.REPORT_WARM_LATEST,))
    with db.db() as conn:
        for target in targets:
            work_order, product = target["work_order"], _key(target["product_model"])
            _, _, digest = facts(work_order, product or None)
            for lang in LANGS:
                done = _latest(conn, work_order, product, lang, "done")
                if done and done["data_hash"] == digest:
                    continue
                last = _latest(conn, work_order, product, lang)
                if last and last["status"] == "failed" and last.get("finished_at") and \
                        (datetime.now(timezone.utc) - datetime.fromisoformat(last["finished_at"])).total_seconds() < config.REPORT_FAILED_RETRY_SEC:
                    continue
                if enqueue(conn, work_order, product, lang, PRIORITY_WARM):
                    return True
    return False


def queue_stats() -> dict[str, int]:
    rows = db.rows("SELECT status, COUNT(*) AS n FROM ai_reports GROUP BY status")
    return {r["status"]: int(r["n"]) for r in rows}
