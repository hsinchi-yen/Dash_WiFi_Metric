"""After a web upload (5D, L7): how this batch compares with the product's history — rules first.

facts(conn, upload_id)   the batch's units, yield, median 2.4G / 5G and FAIL categories, and the same figures for the
                         product's other units (everything already in the database except this batch)
read(f, s)               the points worth saying (pure): yield against the thresholds and the history, throughput
                         below the history by more than the Settings "throughput drop %", the main FAIL category,
                         a small batch, a product without history
explain(f, points, lang) the same in two or three plain sentences by the LLM — only when someone asks (LLM budget);
                         a sentence with a number that is not in the facts is dropped
"""
import json
import re
import statistics
from typing import Any, Optional

import db
from services import llm
from services.wo_rules import numbers_ok

MIN_UNITS = 10
CJK = re.compile(r"[一-鿿]")


def _median(values):
    values = [float(v) for v in values if v is not None]
    return round(statistics.median(values), 1) if values else None


def _group(rows) -> dict[str, Any]:
    units = len(rows)
    passed = sum(1 for r in rows if r["result"])
    return {"units": units, "pass": passed, "fail": units - passed,
            "yield_pct": round(passed * 100.0 / units, 2) if units else None,
            "p50_24g": _median(r["avg_24g"] for r in rows), "p50_5g": _median(r["avg_5g"] for r in rows)}


def facts(conn, upload_id: str) -> Optional[dict[str, Any]]:
    log = db.fetch_one(conn, "SELECT id, work_order, product_model, status, uploaded FROM upload_log WHERE id = %s", (upload_id,))
    if not log:
        return None
    # The batch: its own rows, latest per SN within the batch.
    batch = db.fetch_all(conn, """
        SELECT DISTINCT ON (sn) id, sn, result, avg_24g, avg_5g FROM test_record
        WHERE upload_id = %s ORDER BY sn, test_time DESC, id DESC
    """, (upload_id,))
    ids = [r["id"] for r in batch]
    # The product's history: its units in mv_unit that this batch did not write.
    history = db.fetch_all(conn, """
        SELECT result, avg_24g, avg_5g FROM mv_unit
        WHERE product_model = %s AND NOT (id = ANY(%s))
    """, (log["product_model"], ids or [0])) if log["product_model"] else []
    fails = db.fetch_all(conn, """
        SELECT f.category, x.name_zh, x.name_en, COUNT(*) AS runs FROM fail_analysis f
        LEFT JOIN fail_taxonomy x ON x.code = f.category
        WHERE f.record_id IN (SELECT id FROM test_record WHERE upload_id = %s)
        GROUP BY 1, 2, 3 ORDER BY 4 DESC
    """, (upload_id,))
    return {"upload_id": upload_id, "work_order": log["work_order"], "product_model": log["product_model"],
            "status": log["status"], "batch": _group(batch), "history": _group(history),
            "fails": [{**r, "runs": int(r["runs"])} for r in fails]}


def read(f: dict[str, Any], s: dict[str, Any]) -> dict[str, Any]:
    """{"level": good | warn | bad | none, "points": [{"code", "level", "values"}]} — pure."""
    b, h = f["batch"], f["history"]
    points = []

    def add(code, level, **values):
        points.append({"code": code, "level": level, "values": values})

    if not b["units"]:
        return {"level": "none", "points": [{"code": "no_rows", "level": "none", "values": {}}]}
    if b["units"] < MIN_UNITS:
        add("small", "info", n=b["units"], min=MIN_UNITS)
    else:
        y = b["yield_pct"]
        level = "bad" if y < s["yield_warn"] else "warn" if y < s["yield_good"] else "good"
        add("yield", level, y=y, hist=h["yield_pct"], good=s["yield_good"], warn=s["yield_warn"])
    if not h["units"]:
        add("no_history", "info")
    else:
        for band in ("24g", "5g"):
            mine, usual = b[f"p50_{band}"], h[f"p50_{band}"]
            if mine is None or not usual or b["units"] < MIN_UNITS:
                continue
            drop = round((1 - mine / usual) * 100)
            if drop >= s["tp_drop_pct"]:
                add(f"tp_low_{band}", "warn", p50=mine, hist=usual, pct=drop)
            else:
                add(f"tp_ok_{band}", "good", p50=mine, hist=usual)
    if f["fails"]:
        top = f["fails"][0]
        add("fails", "warn" if b["fail"] else "info", runs=sum(x["runs"] for x in f["fails"]), top=top["category"],
            top_zh=top["name_zh"], top_en=top["name_en"], top_runs=top["runs"], units=b["fail"])
    levels = {p["level"] for p in points}
    overall = "bad" if "bad" in levels else "warn" if "warn" in levels else "good"
    return {"level": overall, "points": points}


EXPLAIN_SYSTEM = (
    "You explain to a production test uploader how the batch of WiFi / Bluetooth module test logs they just uploaded "
    "compares with the product's earlier units. Use only the facts JSON and the rule points. Write {lang_name}, 2 or 3 "
    "plain sentences, no markdown, no field names or codes. Every number must appear in the facts; do not compute new "
    "numbers. Causes are possibilities, never facts. If everything is normal, say so in one sentence."
)
EXPLAIN_SCHEMA = {"type": "object", "properties": {"text": {"type": "string", "maxLength": 400}},
                  "required": ["text"], "additionalProperties": False}


def explain(f: dict[str, Any], points: dict[str, Any], lang: str) -> dict[str, Any]:
    lang_name = "in Traditional Chinese (full-width punctuation, count units with 台)" if lang == "zh" else "in English"
    messages = [{"role": "system", "content": EXPLAIN_SYSTEM.format(lang_name=lang_name)},
                {"role": "user", "content": json.dumps({"facts": f, "rule_points": points["points"]}, ensure_ascii=False, default=str)}]
    out = llm.complete_json(messages, EXPLAIN_SCHEMA, name="upload_reading", max_tokens=300, temperature=0.2)
    text = (out.get("text") or "").strip()
    # Sentence ends: 。！？ (Chinese), or . ! ? followed by a space / the end (English) — never the point of 15.5.
    sentences = [x for x in re.split(r"(?<=[。！？])\s*|(?<=[.!?])\s+", text) if x.strip()]
    extra = tuple(x for x in (f.get("work_order"), f.get("product_model")) if x)
    kept, dropped = [], []
    for sentence in sentences:
        ok, bad = numbers_ok(sentence, {**f, "points": points}, extra)
        wrong_lang = (lang == "en" and CJK.search(sentence)) or (lang == "zh" and not CJK.search(sentence))
        (kept if ok and not wrong_lang else dropped).append(sentence)
    return {"text": "".join(kept) if lang == "zh" else " ".join(kept), "dropped": dropped, "model": llm.model_name()}
