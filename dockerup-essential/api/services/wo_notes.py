"""L1: one-line status note per work order (中文 + English in one LLM call), stored in wo_status_note.

LLM use is kept low: only a work order with a red or amber flag gets an AI-worded note; a normal one gets the
rule sentence (it says the same). An AI note is rewritten at most every LLM_MIN_INTERVAL_SEC unless its flags
change; in between the rule sentence with the current numbers is shown.

The rules (wo_rules) decide state, light and flags; the LLM only words them. A note is
regenerated when its facts change (new data, a state change, new settings). Every number in the
LLM sentence must be one of the facts, otherwise the rule sentence is stored instead (Q26).
LLM offline → the rule sentence, retried later. In-progress work orders go first.

process_once() → worker step (scheduler); notes_for(keys) → {key: note}; current_note(status)
"""
import json
import logging
import re
import time
from typing import Any, Optional

import psycopg2.extras

import config
import db
from services import llm, wo_status
from services.wo_rules import FLAG_TEXT, STATE_TEXT, note_facts, numbers_ok, rule_note

log = logging.getLogger("wifi.notes")

RETRY_SEC = 300                 # a rule fallback (LLM offline / rejected) is retried after this long
LLM_MIN_INTERVAL_SEC = 1800     # an AI note is rewritten at most this often while its flags stay the same
TIMEOUT_SEC = 120               # background work: may wait behind interactive LLM calls
SCAN_EVERY_SEC = 30
_scan = {"at": 0.0, "pending": True}

SCHEMA = {
    "type": "object",
    "properties": {"zh": {"type": "string", "maxLength": 120}, "en": {"type": "string", "maxLength": 260}},
    "required": ["zh", "en"],
    "additionalProperties": False,
}

SYSTEM = (
    "You write the one-line status note for a work order on a WiFi module production-test dashboard that line "
    "leaders and managers glance at. Return JSON {\"zh\": ..., \"en\": ...}: the same single sentence in Traditional "
    "Chinese (at most 60 characters) and in English (at most 30 words).\n"
    "Rules:\n"
    "- Use only the facts given. Every number you write must appear in the facts (you may drop trailing zeros). "
    "Do not compute new numbers (no differences, no fail rates), and do not write dates, names or causes.\n"
    "- Lead with the most serious flag (flags are listed most serious first); flag_sentences describe each flag. "
    "Combine at most two flags. With no bad or warn flag, say briefly that the work order is normal and give "
    "yield and units tested.\n"
    "- Order: the state word given in state_text, then units tested, then yield, then the flags. "
    "Do not repeat the work order number or the product name.\n"
    "- Write each number once (yield is already given before the flags).\n"
    "- Chinese uses full-width punctuation (，：；。).\n"
    "- In Chinese, count units with 台 (never 件 or 個).\n"
    "- Plain text, no markdown, no emoji."
)


def _key(wo, product):
    return (wo, product or "")


def flagged(status: dict) -> bool:
    """A red or amber check: worth an AI-worded note."""
    return any(f["level"] in ("bad", "warn") for f in status.get("flags", []))


def _codes(flags):
    return sorted(f.get("code") for f in flags or [])


# The product's baselines (usual medians, hourly target, and the flag values derived from them) move with every new
# batch of that product. An AI note of a finished work order is not rewritten for that: it still describes the work
# order as long as its own facts and flags are the same and every number in it is still a fact (LLM usage; numbers only
# from the database). In-progress work orders keep the 30-minute rule.
BASELINE_KEYS = ("uph_target", "hist_p50_24g", "hist_p50_5g")


def _own(facts: Optional[dict]) -> str:
    own = {k: v for k, v in (facts or {}).items() if k not in BASELINE_KEYS}
    own["flags"] = _codes((facts or {}).get("flags"))
    return json.dumps(own, sort_keys=True, default=str)


def up_to_date(stored: dict, status: dict, facts: dict, digest: str) -> bool:
    """The stored note still describes the work order: written from these facts, or an AI note of a finished work order
    whose own facts are unchanged and whose numbers are all still facts."""
    if stored["facts_hash"] == digest:
        return True
    if not (stored["source"] == "llm" and status.get("state") == "done" and _own(stored.get("facts")) == _own(facts)):
        return False
    extra = (status.get("work_order") or "", status.get("product_model") or "")
    return all(numbers_ok(stored.get(c) or "", facts, extra)[0] for c in ("text_zh", "text_en"))


def _stored(conn, keys=None) -> dict:
    rows = db.fetch_all(conn, "SELECT * FROM wo_status_note") if keys is None else db.fetch_all(
        conn, "SELECT * FROM wo_status_note WHERE (work_order, product_model) IN %s", (tuple(keys),)) if keys else []
    return {(r["work_order"], r["product_model"]): r for r in rows}


def current_note(status: dict, stored: Optional[dict]) -> dict:
    """The note to show: the stored one when it was written from the current facts, else the rule sentence."""
    facts, digest = note_facts(status)
    if stored and up_to_date(stored, status, facts, digest):
        return {"zh": stored["text_zh"], "en": stored["text_en"], "source": stored["source"],
                "generated_at": stored["created_at"].isoformat() if stored["created_at"] else None, "pending": False}
    return {"zh": rule_note(status, "zh"), "en": rule_note(status, "en"), "source": "rule", "generated_at": None,
            "pending": config.WO_NOTE_AUTO and flagged(status)}


def rd_conclusions(conn, keys) -> dict:
    """RD's latest instruction for the line per work order (5F-2 write-back, Q37): shown after the one-line note,
    as RD wrote it (no LLM)."""
    if not keys:
        return {}
    rows = db.fetch_all(conn, """
        SELECT DISTINCT ON (work_order, COALESCE(product_model, '')) work_order, COALESCE(product_model, '') AS product_model,
               rd_action, rd_by, rd_at, COUNT(*) OVER (PARTITION BY work_order, COALESCE(product_model, '')) AS n
        FROM fail_analysis
        WHERE rd_action IS NOT NULL AND rd_action <> '' AND (work_order, COALESCE(product_model, '')) IN %s
        ORDER BY work_order, COALESCE(product_model, ''), rd_at DESC NULLS LAST
    """, (tuple(keys),))
    return {(r["work_order"], r["product_model"]): {"text": r["rd_action"], "by": r["rd_by"], "n": int(r["n"]),
            "at": r["rd_at"].isoformat() if r["rd_at"] else None} for r in rows}


def notes_for(statuses: dict) -> dict:
    """{key: note} for the given statuses (two queries); `rd` = RD's latest instruction for the work order, if any."""
    keys = [_key(*k) for k in statuses]
    with db.db() as conn:
        stored = _stored(conn, keys)
        rd = rd_conclusions(conn, keys)
    out = {}
    for k, st in statuses.items():
        note = current_note(st, stored.get(_key(*k)))
        note["rd"] = rd.get(_key(*k))
        out[k] = note
    return out


def _prompt(status: dict, facts: dict) -> list[dict]:
    hints = {lang: [FLAG_TEXT[lang][f["code"]].format(**{k: v for k, v in f.items() if k != "code"})
                    for f in facts["flags"]] for lang in ("zh", "en")}
    payload = {"state_text": {lang: STATE_TEXT[lang][status["state"]] for lang in ("zh", "en")},
               "facts": facts, "flag_sentences": hints}
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}]


def generate(status: dict) -> dict:
    """{zh, en, source, model, error, elapsed}: the LLM sentence when it passes the checks, else the rule one."""
    facts, digest = note_facts(status)
    fallback = {"zh": rule_note(status, "zh"), "en": rule_note(status, "en"), "source": "rule", "model": None}
    if not flagged(status):              # normal work order: the rule sentence, no LLM call
        return {**fallback, "error": None, "elapsed": 0.0, "hash": digest}
    if not llm.status()["connected"]:
        return {**fallback, "error": "llm_offline", "elapsed": 0.0, "hash": digest}
    t0 = time.perf_counter()
    try:
        out = llm.complete_json(_prompt(status, facts), SCHEMA, name="wo_note", timeout=TIMEOUT_SEC,
                                max_tokens=300, temperature=0.2)
    except Exception as exc:
        log.warning("note %s failed: %s", status["work_order"], exc)
        return {**fallback, "error": f"llm_error: {str(exc)[:200]}", "elapsed": time.perf_counter() - t0, "hash": digest}
    elapsed = time.perf_counter() - t0
    zh, en = (out.get("zh") or "").strip(), (out.get("en") or "").strip()
    extra = (status.get("work_order") or "", status.get("product_model") or "")
    problems = []
    for lang, text in (("zh", zh), ("en", en)):
        ok, bad = numbers_ok(text, facts, extra)
        if not text:
            problems.append(f"{lang}: empty")
        elif not ok:
            problems.append(f"{lang}: numbers not in facts {bad}")
        elif re.search(r"[一-鿿]", en) and lang == "en":
            problems.append("en: Chinese text")
    if problems:
        log.info("note %s rejected: %s", status["work_order"], "; ".join(problems))
        return {**fallback, "error": "rejected: " + "; ".join(problems), "elapsed": elapsed, "hash": digest,
                "rejected": {"zh": zh, "en": en}}
    return {"zh": zh, "en": en, "source": "llm", "model": llm.model_name(), "error": None, "elapsed": elapsed, "hash": digest}


def store(conn, status: dict, note: dict) -> None:
    facts, _ = note_facts(status)
    db.execute(conn, """
        INSERT INTO wo_status_note (work_order, product_model, facts_hash, state, light, facts, text_zh, text_en,
                                    source, model, error, rejected, elapsed_sec, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (work_order, product_model) DO UPDATE SET
            facts_hash = EXCLUDED.facts_hash, state = EXCLUDED.state, light = EXCLUDED.light, facts = EXCLUDED.facts,
            text_zh = EXCLUDED.text_zh, text_en = EXCLUDED.text_en, source = EXCLUDED.source, model = EXCLUDED.model,
            error = EXCLUDED.error, rejected = EXCLUDED.rejected, elapsed_sec = EXCLUDED.elapsed_sec, created_at = NOW()
    """, (status["work_order"], status.get("product_model") or "", note["hash"], status["state"], status["light"],
          psycopg2.extras.Json(facts, dumps=lambda o: json.dumps(o, default=str)),
          note["zh"], note["en"], note["source"], note["model"], note.get("error"),
          psycopg2.extras.Json(note["rejected"]) if note.get("rejected") else None, note.get("elapsed")))


def _due(statuses: dict, stored: dict) -> list:
    """Work orders whose note is missing / out of date / a fallback due for a retry; in-progress first."""
    now = time.time()
    due = []
    for k, st in statuses.items():
        if st.get("state") == "no_data":
            continue
        facts, digest = note_facts(st)
        row = stored.get(_key(*k))
        if row and up_to_date(row, st, facts, digest):
            if row["source"] == "llm" or not row["error"]:
                continue
            if row["created_at"] and now - row["created_at"].timestamp() < RETRY_SEC:
                continue
        elif row and flagged(st) and row["source"] == "llm" and row["created_at"] \
                and now - row["created_at"].timestamp() < LLM_MIN_INTERVAL_SEC \
                and _codes((row["facts"] or {}).get("flags")) == _codes(note_facts(st)[0]["flags"]):
            continue      # same flags, rewritten recently: wait (the rule sentence shows the new numbers)
        due.append(st)
    rank = {"active": 0, "paused": 1, "done": 2}
    due.sort(key=lambda st: (rank.get(st["state"], 3), -(st["last_test"].timestamp() if st.get("last_test") else 0)))
    return due


def process_once() -> bool:
    """Write one due note (holding the shared LLM lock). Returns True when one was written."""
    if not config.WO_NOTE_AUTO:
        return False
    if not _scan["pending"] and time.time() - _scan["at"] < SCAN_EVERY_SEC:
        return False
    statuses = wo_status.statuses()
    conn = db.connect()
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s) AS ok", (config.REPORT_WORKER_LOCK_KEY,))
        if not cur.fetchone()["ok"]:
            return False
        try:
            due = _due(statuses, _stored(conn))
            plain = [st for st in due if not flagged(st)]
            for status in plain:                    # rule sentences: no LLM, all at once
                store(conn, status, generate(status))
            due = [st for st in due if flagged(st)]
            _scan.update(at=time.time(), pending=len(due) > 1)
            if not due:
                return bool(plain)
            if not llm.status()["connected"]:
                # Offline: every due note gets its rule sentence now (no LLM wait); retried later.
                for status in due:
                    store(conn, status, generate(status))
                _scan["pending"] = False
                log.info("LLM offline: %d rule notes written", len(due))
                return True
            status = due[0]
            note = generate(status)
            store(conn, status, note)
            log.info("note %s (%s, %s) in %.1f s", status["work_order"], status["state"], note["source"], note.get("elapsed") or 0)
            return True
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (config.REPORT_WORKER_LOCK_KEY,))
    finally:
        conn.close()


def queue_stats() -> dict:
    statuses = wo_status.statuses()
    with db.db() as conn:
        stored = _stored(conn)
    due = _due(statuses, stored)
    by_source: dict[str, int] = {}
    for r in stored.values():
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1
    return {"work_orders": len(statuses), "stored": len(stored), "due": len(due), "by_source": by_source}
