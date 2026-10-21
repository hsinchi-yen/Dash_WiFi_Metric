"""L3: the structured final report of a work order (5B), stored in wo_report.

Automatic reports use the LLM only for a work order with a red / amber flag or a notable change; a normal one
gets the rule summary. A report someone asks for (Write / Rewrite) is always AI-written.

The facts table is built by the server and shown as it is. The LLM writes only four parts, in Chinese and
English in one call (Q24): summary, anomalies, possible causes, actions. Every sentence must use only
numbers from the facts (Q26): a list item that does not is dropped, a summary that does not is replaced
by the rule summary. A normal work order gets a short report. Written automatically when a work order is
done (Q23: no test for done_idle_hours, or its plan reached), newest first, after the one-line notes;
any work order on request. Follow-up questions are answered from the same facts.

    facts(key)            → (facts, hash)
    get(key)              → stored report + state for the page
    request(key, who)     → queue a (re)write
    process_once()        → worker step
    ask(key, question, history, lang) → {answer, refused}
"""
import hashlib
import json
import logging
import re
import time
from typing import Any, Optional

import psycopg2.extras

import config
import db
from services import llm, wo_detail, wo_status
from services.wo_rules import _clean, flag_text, numbers_ok, rule_note

log = logging.getLogger("wifi.wo_report")

REPORT_VERSION = 3
TIMEOUT_SEC = 180
RETRY_SEC = 600
SCAN_EVERY_SEC = 30
_scan = {"at": 0.0, "pending": True}
CJK = re.compile(r"[一-鿿]")

SUGGESTED_ACTIONS = {
    "yield_bad": ("檢查 FAIL 清單的原因分佈，確認是否集中在同一現象或時段。",
                  "Check the fail list: are the failures concentrated in one symptom or time window?"),
    "yield_warn": ("檢查 FAIL 清單的原因分佈，確認是否集中在同一現象或時段。",
                   "Check the fail list: are the failures concentrated in one symptom or time window?"),
    "fail_streak": ("確認連續 FAIL 的時段是否來自同一測試站或夾具。",
                    "Check whether the consecutive FAILs came from the same station or fixture."),
    "tp_low_24g": ("比對同產品其他工單的 2.4G 吞吐量與測試站設定（AP、頻道）。",
                   "Compare 2.4G throughput and station settings (AP, channel) with this product's other work orders."),
    "tp_low_5g": ("比對同產品其他工單的 5G 吞吐量與測試站設定（AP、頻道）。",
                  "Compare 5G throughput and station settings (AP, channel) with this product's other work orders."),
    "behind_target": ("確認產線是否停線，或測試站是否異常。", "Check whether the line stopped or a station has a problem."),
}

PART = {"type": "object", "properties": {
    "summary": {"type": "string", "maxLength": 400},
    "anomalies": {"type": "array", "items": {"type": "string", "maxLength": 300}, "maxItems": 4},
    "causes": {"type": "array", "items": {"type": "string", "maxLength": 300}, "maxItems": 3},
    "actions": {"type": "array", "items": {"type": "string", "maxLength": 300}, "maxItems": 3},
}, "required": ["summary", "anomalies", "causes", "actions"], "additionalProperties": False}
SCHEMA = {"type": "object", "properties": {"zh": PART, "en": PART}, "required": ["zh", "en"], "additionalProperties": False}

SYSTEM = (
    "You write the final report of one work order of a WiFi module production test line, for managers and engineers. "
    "You get a facts JSON. Return JSON {zh: {...}, en: {...}} with the same content in Traditional Chinese and English. "
    "Each language has: summary (1-2 sentences), anomalies (list), causes (list), actions (list).\n"
    "Rules:\n"
    "- Use only the facts. Every number you write must appear in the facts (changes_vs_previous already holds the "
    "differences; do not compute new numbers, percentages or rates). Dates only as given in first_test / last_test. No names.\n"
    "- summary: units tested, yield against the thresholds, and how it compares with the previous work order of the same "
    "product when `previous` is given.\n"
    "- anomalies: one item per bad or warn flag (flag_sentences describe them), plus each change in changes_vs_previous "
    "marked notable=true. Changes that are not notable are not anomalies. Empty when there are none.\n"
    "- causes: possible causes based on fail_runs_by_category (passed_on_retest = the unit passed when retested, i.e. "
    "likely transient or station related; failed_again / not_retested = persistent), fail_stations (failures concentrated "
    "on one station point to the station) and the flags, always phrased as possibilities (zh: 可能…; en: may / possibly). "
    "Empty when there are no anomalies.\n"
    "- actions: concrete next checks; suggested_actions are good starting points. Empty when nothing needs doing.\n"
    "- A normal work order (no bad or warn flag and no notable change) gets a short summary and empty lists.\n"
    "- Words: zh 良率 (yield), 重測率 (retry rate), 每小時產出 / 台／時 (units per hour, never 'UPH'), 吞吐量 (throughput), "
    "中位數 (median); en use 'units per hour', not 'UPH'.\n"
    "- Never show field names or codes (fail_streak, yield_pct, ...): use plain words.\n"
    "- Chinese: full-width punctuation, count units with 台. English: no Chinese characters. Plain text, no markdown."
)


def _key(key):
    return (key[0], key[1] or "")


_facts_cache: dict[str, Any] = {"key": None, "data": {}}


def facts(key: tuple) -> tuple[dict, str]:
    """Cached until the statuses are recomputed (new data or settings)."""
    ck = wo_status.cache_key()
    if _facts_cache["key"] != ck:
        _facts_cache.update(key=ck, data={})
    if key not in _facts_cache["data"]:
        _facts_cache["data"][key] = _build_facts(key)
    return _facts_cache["data"][key]


def _build_facts(key: tuple) -> tuple[dict, str]:
    statuses = wo_status.statuses()
    st = statuses[key]
    ex = wo_detail.extra(key)
    mine = wo_detail.metrics(st, ex)
    prev_key = wo_detail.previous_key(key)
    f = {
        "work_order": key[0], "product_model": key[1], "state": st["state"], "light": st["light"],
        "first_test": mine["first_test"], "last_test": mine["last_test"],
        "duration_h": mine["duration_h"], "active_hours": ex["active_hours"],
        "total": st["total"], "passed": st.get("passed"), "failed": st.get("failed"), "yield_pct": st.get("yield_pct"),
        "thresholds": st.get("thresholds"), "planned": st.get("planned"), "achieved_pct": st.get("achieved_pct"),
        "test_runs": ex["test_runs"], "retry_units": ex["retry_units"], "retry_rate": mine["retry_rate"],
        "avg_24g": ex["avg_24g"], "avg_5g": ex["avg_5g"], "p50_24g": st.get("p50_24g"), "p50_5g": st.get("p50_5g"),
        "usual_p50_24g": st.get("hist_p50_24g"), "usual_p50_5g": st.get("hist_p50_5g"),
        "uph": st.get("uph"), "uph_target": st.get("uph_target"), "max_fail_streak": st.get("max_fail_streak"),
        "failing_units_by_category": [{"category": r["name_en"] or r["code"], "units": r["units"]} for r in ex["fail_reasons"]],
        "fail_runs_by_category": [{"category": c["name_en"] or c["category"], "fail_runs": c["runs"], "units": c["units"],
                                   "passed_on_retest": c["pass_on_retest"], "failed_again": c["failed_again"],
                                   "not_retested": c["not_retested"]} for c in ex["fail_runs"]["categories"]],
        "fail_stations": [{"ssid_group": s["ssid_group"], "port": s["station_port"], "fail_runs": s["runs"]}
                          for s in ex["fail_runs"]["stations"][:3]],
        "flags": [{"code": x["code"], "level": x["level"], **x["values"]} for x in st.get("flags", [])],
        "previous": None, "changes_vs_previous": None,
    }
    if prev_key:
        pm = wo_detail.metrics(statuses[prev_key], wo_detail.extra(prev_key))
        f["previous"] = {k: pm[k] for k in ("work_order", "last_test", "total", "yield_pct", "failed", "retry_rate",
                                            "p50_24g", "p50_5g", "uph")}
        f["changes_vs_previous"] = wo_detail.changes(mine, pm)
    f = _clean(f)
    digest = hashlib.sha1(json.dumps({"v": REPORT_VERSION, **f}, sort_keys=True, default=str).encode()).hexdigest()
    return f, digest


def _status(key):
    return wo_status.statuses()[key]


def needs_llm(key: tuple, f: dict) -> bool:
    """An automatic report is AI-written only when there is something to explain: a red or amber flag or a
    notable change from the previous work order. A normal work order gets the rule summary (LLM usage)."""
    st = _status(key)
    notable = any(c.get("notable") for c in (f.get("changes_vs_previous") or {}).values())
    return notable or any(x["level"] in ("bad", "warn") for x in st.get("flags", []))


def _previous_sentence(f: dict, lang: str) -> str:
    p = f.get("previous")
    if not p:
        return ""
    y = p.get("yield_pct")
    if lang == "zh":
        return f"上一張同產品工單 {p['work_order']}：良率 {y}%，{p['total']:,} 台。"
    return f"Previous work order of this product, {p['work_order']}: yield {y}%, {p['total']:,} units."


def rule_report(key: tuple, f: dict) -> dict:
    st = _status(key)
    out = {}
    for i, lang in enumerate(("zh", "en")):
        main = [x for x in st.get("flags", []) if x["level"] in ("bad", "warn")]
        actions = []
        for x in main:
            text = SUGGESTED_ACTIONS.get(x["code"], (None, None))[i]
            if text and text not in actions:
                actions.append(text)
        summary = rule_note(st, lang)
        prev = _previous_sentence(f, lang)
        out[lang] = {"summary": f"{summary} {prev}".strip() if lang == "en" else summary + prev,
                     "anomalies": [flag_text(x, lang) for x in main], "causes": [], "actions": actions}
    return out


def _check(report: dict, f: dict) -> tuple[dict, list]:
    """Keep only sentences whose numbers are facts; a failing summary falls back to the rule summary."""
    rejected = []
    extra = (f.get("work_order") or "", f.get("product_model") or "")
    clean = {}
    for lang in ("zh", "en"):
        part = report.get(lang) or {}
        out = {"summary": "", "anomalies": [], "causes": [], "actions": []}
        for field in ("summary", "anomalies", "causes", "actions"):
            items = [part.get(field) or ""] if field == "summary" else list(part.get(field) or [])
            kept = []
            for text in items:
                text = (text or "").strip()
                if not text:
                    continue
                ok, bad = numbers_ok(text, f, extra)
                wrong_lang = (lang == "en" and CJK.search(text)) or (lang == "zh" and not CJK.search(text))
                if ok and not wrong_lang:
                    kept.append(text)
                else:
                    rejected.append({"lang": lang, "field": field, "text": text, "numbers": bad, "language": bool(wrong_lang)})
            out[field] = kept[0] if field == "summary" and kept else (kept if field != "summary" else "")
        clean[lang] = out
    return clean, rejected


def generate(key: tuple, use_llm: bool = True) -> dict:
    f, digest = facts(key)
    rule = rule_report(key, f)
    base = {"hash": digest, "facts": f, "model": None, "elapsed": 0.0}
    if not use_llm:                      # automatic report of a normal work order: rules only
        return {**base, "report": rule, "source": "rule", "error": None, "rejected": None}
    if not llm.status()["connected"]:
        return {**base, "report": rule, "source": "rule", "error": "llm_offline", "rejected": None}
    st = _status(key)
    hints = {
        "flag_sentences": {lang: [flag_text(x, lang) for x in st.get("flags", []) if x["level"] in ("bad", "warn")] for lang in ("zh", "en")},
        "suggested_actions": {lang: sorted({SUGGESTED_ACTIONS[x["code"]][i] for x in st.get("flags", []) if x["code"] in SUGGESTED_ACTIONS})
                              for i, lang in enumerate(("zh", "en"))},
    }
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps({"facts": f, **hints}, ensure_ascii=False, default=str)}]
    t0 = time.perf_counter()
    try:
        out = llm.complete_json(messages, SCHEMA, name="wo_report", timeout=TIMEOUT_SEC, max_tokens=1400, temperature=0.2)
    except Exception as exc:
        log.warning("report %s failed: %s", key[0], exc)
        return {**base, "report": rule, "source": "rule", "error": f"llm_error: {str(exc)[:200]}",
                "rejected": None, "elapsed": time.perf_counter() - t0}
    elapsed = time.perf_counter() - t0
    report, rejected = _check(out, f)
    notable = any(c.get("notable") for c in (f.get("changes_vs_previous") or {}).values())
    flagged = any(x["level"] in ("bad", "warn") for x in st.get("flags", []))
    for lang in ("zh", "en"):
        if not (flagged or notable):       # a normal work order: the summary only (no invented concerns)
            report[lang].update(anomalies=[], causes=[], actions=[])
        if not report[lang]["summary"]:
            report[lang]["summary"] = rule[lang]["summary"]
        # a flagged work order must not end up with no anomaly listed
        if rule[lang]["anomalies"] and not report[lang]["anomalies"]:
            report[lang]["anomalies"] = rule[lang]["anomalies"]
    return {**base, "report": report, "source": "llm", "model": config.LLM_MODEL,
            "error": f"{len(rejected)} sentences dropped" if rejected else None,
            "rejected": rejected or None, "elapsed": elapsed}


def store(conn, key: tuple, out: dict) -> None:
    dumps = lambda o: json.dumps(o, ensure_ascii=False, default=str)   # noqa: E731
    db.execute(conn, """
        INSERT INTO wo_report (work_order, product_model, facts_hash, facts, report, source, model, error, rejected,
                               elapsed_sec, created_at, requested_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NULL)
        ON CONFLICT (work_order, product_model) DO UPDATE SET
            facts_hash = EXCLUDED.facts_hash, facts = EXCLUDED.facts, report = EXCLUDED.report, source = EXCLUDED.source,
            model = EXCLUDED.model, error = EXCLUDED.error, rejected = EXCLUDED.rejected, elapsed_sec = EXCLUDED.elapsed_sec,
            created_at = NOW(), requested_at = NULL
    """, (key[0], key[1] or "", out["hash"], psycopg2.extras.Json(out["facts"], dumps=dumps),
          psycopg2.extras.Json(out["report"], dumps=dumps), out["source"], out["model"], out.get("error"),
          psycopg2.extras.Json(out["rejected"], dumps=dumps) if out.get("rejected") else None, out.get("elapsed")))


def _row(conn, key):
    return db.fetch_one(conn, "SELECT * FROM wo_report WHERE work_order = %s AND product_model = %s", _key(key))


def get(key: tuple) -> dict[str, Any]:
    f, digest = facts(key)
    with db.db() as conn:
        row = _row(conn, key)
    st = _status(key)
    auto = config.WO_REPORT_AUTO and st["state"] == "done"
    if row and row["report"]:
        state = "ready" if row["facts_hash"] == digest else "stale"
    else:
        state = "none"
    pending = bool(row and row["requested_at"]) or (auto and state != "ready")
    return {
        "work_order": key[0], "product_model": key[1], "state": state, "pending": pending, "auto": auto,
        "facts": f, "report": row["report"] if row else None, "source": row["source"] if row else None,
        "model": row["model"] if row else None, "error": row["error"] if row else None,
        "dropped": len(row["rejected"] or []) if row else 0,
        "generated_at": row["created_at"].isoformat() if row and row["created_at"] else None,
        "llm": llm.status()["connected"],
    }


def request(key: tuple, who: Optional[str]) -> None:
    with db.db() as conn:
        db.execute(conn, """
            INSERT INTO wo_report (work_order, product_model, requested_at, requested_by) VALUES (%s, %s, NOW(), %s)
            ON CONFLICT (work_order, product_model) DO UPDATE SET requested_at = NOW(), requested_by = EXCLUDED.requested_by
        """, (*_key(key), who))
    _scan["pending"] = True


def _due(conn) -> list:
    statuses = wo_status.statuses()
    rows = {(r["work_order"], r["product_model"]): r for r in db.fetch_all(conn, "SELECT work_order, product_model, facts_hash, source, error, created_at, requested_at FROM wo_report")}
    requested, auto = [], []
    now = time.time()
    for key, st in statuses.items():
        row = rows.get(_key(key))
        if row and row["requested_at"]:
            requested.append((row["requested_at"].timestamp(), key))
            continue
        if not (config.WO_REPORT_AUTO and st["state"] == "done"):
            continue
        _, digest = facts(key) if row else (None, None)
        if row and row["facts_hash"] == digest:
            if row["source"] == "llm" or (row["created_at"] and now - row["created_at"].timestamp() < RETRY_SEC):
                continue
        auto.append((-(st["last_test"].timestamp() if st.get("last_test") else 0), key))
    return [(k, True) for _, k in sorted(requested)] + [(k, None) for _, k in sorted(auto)]


def process_once() -> bool:
    if not _scan["pending"] and time.time() - _scan["at"] < SCAN_EVERY_SEC:
        return False
    conn = db.connect()
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s) AS ok", (config.REPORT_WORKER_LOCK_KEY,))
        if not cur.fetchone()["ok"]:
            return False
        try:
            due = _due(conn)
            plain = [k for k, asked in due if not asked and not needs_llm(k, facts(k)[0])]
            for k in plain:                         # normal work orders: rule reports, no LLM, all at once
                store(conn, k, generate(k, use_llm=False))
            due = [k for k, asked in due if asked or k not in plain]
            _scan.update(at=time.time(), pending=len(due) > 1)
            if not due:
                return bool(plain)
            key = due[0]
            out = generate(key)
            store(conn, key, out)
            log.info("final report %s (%s) in %.1f s%s", key[0], out["source"], out.get("elapsed") or 0,
                     f", {out['error']}" if out.get("error") else "")
            return True
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (config.REPORT_WORKER_LOCK_KEY,))
    finally:
        conn.close()


# ─── Follow-up questions ─────────────────────────────────────────────────────

ASK_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string", "maxLength": 800},
                                               "answerable": {"type": "boolean"}},
              "required": ["answer", "answerable"], "additionalProperties": False}
ASK_SYSTEM = (
    "You answer questions about ONE work order of a WiFi module production test line, using only the facts JSON "
    "(and the report written from it). Answer in {lang_name}, in at most 4 plain sentences, no markdown. Every number "
    "you write must appear in the facts; do not compute new numbers. If the facts cannot answer the question, set "
    "answerable to false and say briefly what the facts do cover (yield, failures and their reasons, throughput, "
    "speed, plan, the previous work order). Do not guess causes as facts; phrase them as possibilities. Never show field "
    "names, codes or the word 'facts' (for example fail_streak, yield_pct): describe them in plain words and call the "
    "facts 'this work order's data' (zh: 這張工單的資料)."
)
REFUSAL = {
    "zh": "這個問題需要事實表以外的數字，無法可靠回答。可以改問良率、FAIL 原因、吞吐量，或與上一張工單的差異。",
    "en": "That needs numbers that are not in this work order's facts, so I can't answer it reliably. Try asking about "
          "yield, failure reasons, throughput or the change from the previous work order.",
}


def ask(key: tuple, question: str, history: list, lang: str) -> dict:
    f, _ = facts(key)
    with db.db() as conn:
        row = _row(conn, key)
    report = (row or {}).get("report") or {}
    lang_name = "Traditional Chinese (full-width punctuation)" if lang == "zh" else "English"
    messages = [{"role": "system", "content": ASK_SYSTEM.format(lang_name=lang_name)},
                {"role": "user", "content": json.dumps({"facts": f, "report": report.get(lang)}, ensure_ascii=False, default=str)},
                {"role": "assistant", "content": "OK."}]
    for turn in history[-3:]:
        messages += [{"role": "user", "content": str(turn.get("q", ""))[:300]},
                     {"role": "assistant", "content": str(turn.get("a", ""))[:800]}]
    messages.append({"role": "user", "content": question})
    extra = (key[0], key[1] or "")
    t0 = time.perf_counter()
    for attempt in range(2):
        out = llm.complete_json(messages, ASK_SCHEMA, name="wo_answer", timeout=config.LLM_CHAT_TIMEOUT, max_tokens=500, temperature=0.2)
        answer = (out.get("answer") or "").strip()
        ok, bad = numbers_ok(answer, f, extra)
        if ok and answer:
            return {"answer": answer, "answerable": bool(out.get("answerable", True)), "refused": False,
                    "elapsed": round(time.perf_counter() - t0, 1)}
        messages += [{"role": "assistant", "content": json.dumps(out, ensure_ascii=False)},
                     {"role": "user", "content": f"These numbers are not in the facts: {bad}. Answer again without them."}]
    return {"answer": REFUSAL[lang], "answerable": False, "refused": True, "elapsed": round(time.perf_counter() - t0, 1)}
