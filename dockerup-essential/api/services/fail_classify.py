"""FAIL classification (5F-1): every FAIL test run gets one fail_analysis row.

scan()           classify FAIL runs without a row (or from an older rule version, never RD's), refresh the
                 retest outcome of every row, remove rows whose test run was deleted. Rules only: milliseconds
                 per log. Runs every minute (scheduler), after a web upload, and on the first start (backfill).
llm_once()       one UNKNOWN row → the LLM picks a code from the taxonomy and must cite log lines; the server
                 checks every cited line against the log (Q36: only for rule misses).
summary(...)     categories × retest outcome, stations — for the pages and the final report.
"""
import json
import logging
import time
from typing import Any, Optional

import psycopg2.extras

import config
import db
from services import fail_rules, llm

log = logging.getLogger("wifi.fail")

SCAN_EVERY_SEC = 60
LLM_RETRY_SEC = 3600
_last = {"scan": 0.0}

UPSERT = """
INSERT INTO fail_analysis (record_id, sn, work_order, product_model, test_time, category, secondary, band, mbps, threshold,
                           rssi, attempts, measures, evidence, station_port, ssid_group, target_ssid, classifier,
                           classifier_version, confidence, updated_at)
VALUES (%(record_id)s, %(sn)s, %(work_order)s, %(product_model)s, %(test_time)s, %(category)s, %(secondary)s, %(band)s,
        %(mbps)s, %(threshold)s, %(rssi)s, %(attempts)s, %(measures)s, %(evidence)s, %(station_port)s, %(ssid_group)s,
        %(target_ssid)s, %(classifier)s, %(classifier_version)s, %(confidence)s, NOW())
ON CONFLICT (record_id) DO UPDATE SET
    sn = EXCLUDED.sn, work_order = EXCLUDED.work_order, product_model = EXCLUDED.product_model, test_time = EXCLUDED.test_time,
    category = EXCLUDED.category, secondary = EXCLUDED.secondary, band = EXCLUDED.band, mbps = EXCLUDED.mbps,
    threshold = EXCLUDED.threshold, rssi = EXCLUDED.rssi, attempts = EXCLUDED.attempts, measures = EXCLUDED.measures,
    evidence = EXCLUDED.evidence, station_port = EXCLUDED.station_port, ssid_group = EXCLUDED.ssid_group,
    target_ssid = EXCLUDED.target_ssid, classifier = EXCLUDED.classifier, classifier_version = EXCLUDED.classifier_version,
    confidence = EXCLUDED.confidence, updated_at = NOW()
WHERE fail_analysis.classifier <> 'rd'
"""


def _json(o):
    return psycopg2.extras.Json(o, dumps=lambda x: json.dumps(x, ensure_ascii=False, default=str))


def scan(limit: int = 500) -> dict[str, int]:
    """Classify new FAIL runs, refresh retest outcomes, sweep orphans (one scan at a time across workers;
    a scan already running elsewhere is skipped). Returns counts."""
    out = {"classified": 0, "retest_updates": 0, "removed": 0, "skipped": False}
    with db.db() as conn:
        if not db.fetch_one(conn, "SELECT pg_try_advisory_xact_lock(%s) AS ok", (config.FAIL_SCAN_LOCK_KEY,))["ok"]:
            out["skipped"] = True
            return out
        todo = db.fetch_all(conn, """
            SELECT t.id, t.sn, t.work_order, t.product_model, t.test_time, t.raw_log,
                   t.band_result_24g, t.band_result_5g, t.bt_result
            FROM test_record t LEFT JOIN fail_analysis f ON f.record_id = t.id
            WHERE t.result = FALSE
              AND (f.record_id IS NULL OR (f.classifier = 'rule' AND COALESCE(f.classifier_version, 0) < %s))
            ORDER BY t.id LIMIT %s
        """, (fail_rules.CLASSIFIER_VERSION, limit))
        for r in todo:
            c = fail_rules.classify(r["raw_log"] or "", r)
            db.execute(conn, UPSERT, {
                **c, "record_id": r["id"], "sn": r["sn"], "work_order": r["work_order"], "product_model": r["product_model"],
                "test_time": r["test_time"], "secondary": c["secondary"],
                "measures": _json({**c["measures"], "target_ssids": c["target_ssids"], "band_priority": c["band_priority"]}),
                "evidence": _json(c["evidence"]),
            })
            out["classified"] += 1

        # Retest outcome of every FAIL run: what the same SN did afterwards.
        runs = db.fetch_all(conn, """
            SELECT sn, id, result FROM test_record
            WHERE sn IN (SELECT sn FROM fail_analysis)
            ORDER BY sn, test_time, id
        """)
        by_sn: dict[str, list] = {}
        for r in runs:
            by_sn.setdefault(r["sn"], []).append((r["id"], bool(r["result"])))
        current = {r["record_id"]: r for r in db.fetch_all(conn, "SELECT record_id, retest_outcome, run_seq, run_index FROM fail_analysis")}
        for sn, items in by_sn.items():
            results = [ok for _, ok in items]
            seq = fail_rules.run_sequence(results)
            for i, (rid, ok) in enumerate(items):
                if ok or rid not in current:
                    continue
                outcome = fail_rules.retest_outcome(results, i)
                row = current[rid]
                if (row["retest_outcome"], row["run_seq"], row["run_index"]) != (outcome, seq, i):
                    db.execute(conn, "UPDATE fail_analysis SET retest_outcome = %s, run_seq = %s, run_index = %s, updated_at = NOW() WHERE record_id = %s",
                               (outcome, seq, i, rid))
                    out["retest_updates"] += 1
        out["removed"] = db.execute(conn, """
            DELETE FROM fail_analysis f WHERE NOT EXISTS (SELECT 1 FROM test_record t WHERE t.id = f.record_id AND t.result = FALSE)
        """)
    if out["classified"] or out["removed"]:
        log.info("fail scan: %(classified)d classified, %(retest_updates)d retest updates, %(removed)d removed", out)
    return out


def scan_if_due() -> None:
    if time.time() - _last["scan"] >= SCAN_EVERY_SEC:
        _last["scan"] = time.time()
        scan()


# ─── LLM for rule misses ─────────────────────────────────────────────────────

LLM_SYSTEM = (
    "You classify ONE failed WiFi / Bluetooth production test log. The rules found no known failure signal. "
    "You get the allowed categories (code + description) and numbered excerpt lines (result and error lines; lines "
    "that also appear in passing logs are already removed). Choose exactly one code. Cite the 1-3 excerpt lines that "
    "show it, by their line number and their exact text. If nothing in the excerpt supports a category, choose UNKNOWN. "
    "confidence is 0-1. Do not invent lines."
)


def _llm_schema(codes):
    return {"type": "object", "properties": {
        "code": {"type": "string", "enum": codes},
        "evidence": {"type": "array", "items": {"type": "object", "properties": {"n": {"type": "integer"}, "text": {"type": "string"}},
                                                "required": ["n", "text"], "additionalProperties": False}, "maxItems": 3},
        "confidence": {"type": "number"},
        "reason": {"type": "string", "maxLength": 300},
    }, "required": ["code", "evidence", "confidence", "reason"], "additionalProperties": False}


def llm_classify(log_text: str, taxonomy: list) -> dict:
    codes = [t["code"] for t in taxonomy if t["enabled"]]
    allowed = [{"code": t["code"], "description": t["description_en"]} for t in taxonomy if t["enabled"]]
    messages = [{"role": "system", "content": LLM_SYSTEM},
                {"role": "user", "content": json.dumps({"categories": allowed, "excerpt": fail_rules.excerpt(log_text)}, ensure_ascii=False)}]
    out = llm.complete_json(messages, _llm_schema(codes), name="fail_category", timeout=120, max_tokens=400, temperature=0)
    evidence = fail_rules.verify_evidence(out.get("evidence"), log_text)
    code = out.get("code") if out.get("code") in codes else "UNKNOWN"
    if code != "UNKNOWN" and not evidence:
        code = "UNKNOWN"          # a category without verifiable evidence is not accepted
    return {"code": code, "evidence": evidence, "confidence": max(0.0, min(1.0, float(out.get("confidence") or 0))),
            "reason": (out.get("reason") or "")[:300], "cited": out.get("evidence")}


def llm_once() -> bool:
    """Ask the LLM about one UNKNOWN row (shares the report worker's LLM lock). True when one was handled."""
    if not llm.status()["connected"]:
        return False
    conn = db.connect()
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s) AS ok", (config.REPORT_WORKER_LOCK_KEY,))
        if not cur.fetchone()["ok"]:
            return False
        try:
            cur.execute("""
                SELECT f.record_id, t.raw_log FROM fail_analysis f JOIN test_record t ON t.id = f.record_id
                WHERE f.category = 'UNKNOWN' AND f.classifier = 'rule'
                  AND (f.llm_tried_at IS NULL OR f.llm_tried_at < NOW() - make_interval(secs => %s))
                ORDER BY f.record_id LIMIT 1
            """, (LLM_RETRY_SEC,))
            row = cur.fetchone()
            if not row:
                return False
            cur.execute("SELECT * FROM fail_taxonomy ORDER BY sort")
            taxonomy = cur.fetchall()
            try:
                r = llm_classify(row["raw_log"] or "", taxonomy)
            except Exception as exc:
                log.warning("LLM classification of %s failed: %s", row["record_id"], exc)
                cur.execute("UPDATE fail_analysis SET llm_tried_at = NOW() WHERE record_id = %s", (row["record_id"],))
                return True
            if r["code"] != "UNKNOWN":
                cur.execute("""
                    UPDATE fail_analysis SET category = %s, evidence = %s, classifier = 'llm', confidence = %s, llm_model = %s,
                                             llm_tried_at = NOW(), updated_at = NOW()
                    WHERE record_id = %s AND classifier = 'rule'
                """, (r["code"], _json(r["evidence"]), r["confidence"], config.LLM_MODEL, row["record_id"]))
            else:
                cur.execute("UPDATE fail_analysis SET llm_tried_at = NOW() WHERE record_id = %s", (row["record_id"],))
            log.info("LLM classified %s as %s (%.2f)", row["record_id"], r["code"], r["confidence"])
            return True
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (config.REPORT_WORKER_LOCK_KEY,))
    finally:
        conn.close()


# ─── Read side ───────────────────────────────────────────────────────────────

def summary(work_order: Optional[str] = None, product_model: Optional[str] = None,
            date_from=None, date_to=None) -> dict[str, Any]:
    """FAIL runs by category × retest outcome, and by station, in a scope."""
    where, params = ["TRUE"], []
    if work_order:
        where.append("work_order = %s"); params.append(work_order)
    if product_model:
        where.append("product_model = %s"); params.append(product_model)
    if date_from:
        where.append("test_time >= %s"); params.append(date_from)
    if date_to:
        where.append("test_time < (%s::date + 1)"); params.append(date_to)
    w = " AND ".join(where)
    cats = db.rows(f"""
        SELECT f.category, COUNT(*) AS runs, COUNT(DISTINCT f.sn) AS units,
               COUNT(*) FILTER (WHERE f.retest_outcome = 'pass_on_retest') AS pass_on_retest,
               COUNT(*) FILTER (WHERE f.retest_outcome = 'failed_again') AS failed_again,
               COUNT(*) FILTER (WHERE f.retest_outcome = 'not_retested') AS not_retested,
               t.name_zh, t.name_en, t.action_zh, t.action_en, t.sort
        FROM fail_analysis f LEFT JOIN fail_taxonomy t ON t.code = f.category
        WHERE {w} GROUP BY f.category, t.name_zh, t.name_en, t.action_zh, t.action_en, t.sort
        ORDER BY runs DESC, t.sort
    """, tuple(params))
    stations = db.rows(f"""
        SELECT ssid_group, station_port, COUNT(*) AS runs, COUNT(DISTINCT sn) AS units
        FROM fail_analysis WHERE {w} GROUP BY 1, 2 ORDER BY runs DESC, 1, 2 LIMIT 12
    """, tuple(params))
    outcome = db.rows(f"SELECT retest_outcome, COUNT(*) AS runs FROM fail_analysis WHERE {w} GROUP BY 1", tuple(params))
    total = sum(int(c["runs"]) for c in cats)
    for c in cats:
        for k in ("runs", "units", "pass_on_retest", "failed_again", "not_retested"):
            c[k] = int(c[k])
    return {"total_runs": total, "categories": cats,
            "stations": [{**s, "runs": int(s["runs"]), "units": int(s["units"])} for s in stations],
            "retest": {r["retest_outcome"] or "unknown": int(r["runs"]) for r in outcome}}


def for_records(ids: list) -> dict[int, dict]:
    """fail_analysis rows by test_record id (for the FAIL lists)."""
    if not ids:
        return {}
    rows = db.rows("""
        SELECT f.record_id, f.category, f.secondary, f.band, f.mbps, f.threshold, f.rssi, f.attempts, f.evidence,
               f.station_port, f.ssid_group, f.target_ssid, f.retest_outcome, f.run_seq, f.classifier, f.confidence,
               f.stage, f.rd_status, t.name_zh, t.name_en, t.action_zh, t.action_en
        FROM fail_analysis f LEFT JOIN fail_taxonomy t ON t.code = f.category
        WHERE f.record_id = ANY(%s)
    """, (list(ids),))
    return {int(r["record_id"]): r for r in rows}
