"""RD advanced analysis (5F-2, Final_Improvement_Plan §2.9, Q31–Q38): query, group, triage and explain FAIL runs.

Filters (every one optional, see parse_filters): date range, work order, product, category, band, SSID group, port,
retest outcome, classifier, RD status, stage, throughput and RSSI ranges, free text (SN / MAC / work order / note).

listing(f, page, size)     FAIL runs with classification, RD fields, measurements and the unit's MAC
facets(f)                  the values present in the scope (the filter selects offer only these)
group(f, by)               runs / units per value of one dimension (Pareto order, cumulative %)
crosstab(f, rows, cols)    runs per value pair of two dimensions (top values only)
update(conn, …)            batch triage: category (→ classifier rd), stage, RD status, owner, note, line action; audited
reset(conn, …)             undo the triage: the rules classify the runs again on the next scan
sn_history(sn)             every test of one unit with its classification
log_view(record_id)        the raw log with the evidence line numbers (log viewer)
similar(record_id)         same signature (first evidence line, digits ignored) and same station within 2 hours
facts(f) / ask(f, …)       L2b: aggregates of the filtered FAILs only (no SN, MAC or log text) → the LLM writes common
                           points, possible causes and next checks; sentences with numbers that are not facts are dropped
Anything RD has touched (classifier rd, or an RD status other than new) is never re-classified by the rules.
"""
import csv
import io
import json
import re
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import config
import db
from services import fail_taxonomy, llm
from services.wo_rules import numbers_ok

RD_STATUSES = ("new", "confirmed", "reclassified", "closed")
TEXT_LIMITS = {"rd_owner": 64, "rd_note": 500, "rd_action": 200}
EXPORT_LOGS_MAX = 500
LIST_MAX = 500

STATION_SQL = "COALESCE(f.ssid_group, '—') || ' · ' || COALESCE(replace(f.station_port, '/dev/tty', ''), '—')"
# Grouping dimensions (whitelist: user input never reaches the SQL text).
DIMS = {
    "category": "f.category",
    "product": "COALESCE(f.product_model, '—')",
    "work_order": "COALESCE(f.work_order, '—')",
    "station": STATION_SQL,
    "ssid_group": "COALESCE(f.ssid_group, '—')",
    "band": "COALESCE(f.band, '—')",
    "retest": "COALESCE(f.retest_outcome, '—')",
    "stage": "COALESCE(f.stage, '—')",
    "rd_status": "f.rd_status",
    "classifier": "f.classifier",
    "week": "to_char(date_trunc('week', f.test_time), 'YYYY-MM-DD')",
    "month": "to_char(f.test_time, 'YYYY-MM')",
    "day": "to_char(f.test_time, 'YYYY-MM-DD')",
    "hour": "lpad(EXTRACT(HOUR FROM f.test_time)::int::text, 2, '0')",
    "weekday": "EXTRACT(ISODOW FROM f.test_time)::int::text",
}
LIST_FILTERS = {           # query parameter → column (exact match, comma-separated values allowed)
    "category": "f.category", "band": "f.band", "ssid_group": "f.ssid_group", "station_port": "f.station_port",
    "retest": "f.retest_outcome", "classifier": "f.classifier", "rd_status": "f.rd_status", "stage": "f.stage",
}
RANGE_FILTERS = {"mbps_min": ("f.mbps", ">="), "mbps_max": ("f.mbps", "<="), "rssi_min": ("f.rssi", ">="), "rssi_max": ("f.rssi", "<=")}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_filters(q: dict) -> dict[str, Any]:
    """Query parameters → a clean filter dict (unknown keys ignored, bad values dropped)."""
    out: dict[str, Any] = {}
    for k in ("date_from", "date_to"):
        if q.get(k) and DATE_RE.match(str(q[k])):
            out[k] = str(q[k])
    for k in ("work_order", "product_model", "search"):
        if q.get(k) and str(q[k]) != "ALL":
            out[k] = str(q[k])[:128]
    for k in LIST_FILTERS:
        if q.get(k):
            values = [v.strip()[:64] for v in str(q[k]).split(",") if v.strip()]
            if values:
                out[k] = values[:20]
    for k in RANGE_FILTERS:
        try:
            if q.get(k) not in (None, ""):
                out[k] = float(q[k])
        except (TypeError, ValueError):
            pass
    if str(q.get("still_failing") or "") in ("1", "true"):
        out["still_failing"] = True
    return out


def _where(f: dict) -> tuple[str, list]:
    w, p = ["TRUE"], []
    if f.get("date_from"):
        w.append("f.test_time >= %s"); p.append(f["date_from"])
    if f.get("date_to"):
        w.append("f.test_time < (%s::date + 1)"); p.append(f["date_to"])
    if f.get("work_order"):
        w.append("f.work_order = %s"); p.append(f["work_order"])
    if f.get("product_model"):
        w.append("f.product_model = %s"); p.append(f["product_model"])
    for k, col in LIST_FILTERS.items():
        if f.get(k):
            values = [None if v == "—" else v for v in f[k]]
            parts = []
            if any(v is None for v in values):
                parts.append(f"{col} IS NULL")
            real = [v for v in values if v is not None]
            if real:
                parts.append(f"{col} = ANY(%s)"); p.append(real)
            w.append("(" + " OR ".join(parts) + ")")
    for k, (col, op) in RANGE_FILTERS.items():
        if f.get(k) is not None:
            w.append(f"{col} {op} %s"); p.append(f[k])
    if f.get("still_failing"):
        w.append("f.retest_outcome = 'not_retested'")
    if f.get("search"):
        s = f"%{f['search']}%"
        w.append("(f.sn ILIKE %s OR t.wifi_mac ILIKE %s OR f.work_order ILIKE %s OR f.rd_note ILIKE %s OR f.rd_action ILIKE %s)")
        p += [s] * 5
    return " AND ".join(w), p


FROM = "FROM fail_analysis f JOIN test_record t ON t.id = f.record_id LEFT JOIN fail_taxonomy x ON x.code = f.category"

LIST_COLUMNS = """f.record_id, f.test_time, f.work_order, f.product_model, f.sn, t.wifi_mac, f.category, f.secondary, f.band,
    f.mbps, f.threshold, f.rssi, f.attempts, f.station_port, f.ssid_group, f.target_ssid, f.retest_outcome, f.run_seq,
    f.run_index, f.classifier, f.confidence, f.stage, f.rd_status, f.rd_owner, f.rd_note, f.rd_action, f.rd_by, f.rd_at,
    f.evidence, x.name_zh, x.name_en"""


def listing(f: dict, page: int = 1, size: int = 50, sort: str = "time") -> dict[str, Any]:
    w, p = _where(f)
    total = int(db.rows(f"SELECT COUNT(*) AS n {FROM} WHERE {w}", tuple(p))[0]["n"])
    size = max(1, min(size, LIST_MAX))
    pages = max(1, -(-total // size))
    page = min(max(1, page), pages)
    order = {"time": "f.test_time DESC, f.record_id DESC", "mbps": "f.mbps NULLS LAST, f.test_time DESC",
             "category": "x.sort, f.test_time DESC", "station": f"{STATION_SQL}, f.test_time DESC"}.get(sort, "f.test_time DESC, f.record_id DESC")
    items = db.rows(f"SELECT {LIST_COLUMNS} {FROM} WHERE {w} ORDER BY {order} LIMIT %s OFFSET %s", (*p, size, (page - 1) * size))
    for r in items:
        r["evidence"] = (r.get("evidence") or [])[:3]
    return {"items": items, "total": total, "page": page, "pages": pages, "page_size": size}


def facets(f: dict) -> dict[str, list]:
    """Values present within the date / work-order / product scope (the other filters do not narrow them)."""
    base = {k: f[k] for k in ("date_from", "date_to", "work_order", "product_model") if k in f}
    w, p = _where(base)
    out = {}
    for key, col in (("category", "f.category"), ("band", "f.band"), ("ssid_group", "f.ssid_group"), ("station_port", "f.station_port"),
                     ("retest", "f.retest_outcome"), ("classifier", "f.classifier"), ("rd_status", "f.rd_status"), ("stage", "f.stage")):
        rows = db.rows(f"SELECT {col} AS v, COUNT(*) AS n {FROM} WHERE {w} GROUP BY 1 ORDER BY 2 DESC", tuple(p))
        out[key] = [{"value": r["v"] if r["v"] is not None else "—", "n": int(r["n"])} for r in rows]
    return out


def group(f: dict, by: str) -> dict[str, Any]:
    if by not in DIMS:
        by = "category"
    w, p = _where(f)
    rows = db.rows(f"""SELECT {DIMS[by]} AS key, COUNT(*) AS runs, COUNT(DISTINCT f.sn) AS units,
                              COUNT(*) FILTER (WHERE f.retest_outcome = 'pass_on_retest') AS passed_retest
                       {FROM} WHERE {w} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 40""", tuple(p))
    total = sum(int(r["runs"]) for r in rows)
    cum = 0
    out = []
    for r in rows:
        cum += int(r["runs"])
        out.append({"key": r["key"], "runs": int(r["runs"]), "units": int(r["units"]), "passed_retest": int(r["passed_retest"]),
                    "cum_pct": round(cum * 100.0 / total, 1) if total else 0})
    if by in ("week", "month", "day", "hour", "weekday"):
        out.sort(key=lambda x: x["key"])
    return {"by": by, "total": total, "items": out}


def crosstab(f: dict, rows_by: str = "category", cols_by: str = "station", top: int = 12) -> dict[str, Any]:
    rows_by = rows_by if rows_by in DIMS else "category"
    cols_by = cols_by if cols_by in DIMS else "station"
    w, p = _where(f)
    data = db.rows(f"SELECT {DIMS[rows_by]} AS r, {DIMS[cols_by]} AS c, COUNT(*) AS n {FROM} WHERE {w} GROUP BY 1, 2", tuple(p))
    col_tot: dict = {}
    row_tot: dict = {}
    for d in data:
        col_tot[d["c"]] = col_tot.get(d["c"], 0) + int(d["n"])
        row_tot[d["r"]] = row_tot.get(d["r"], 0) + int(d["n"])
    ordered = sorted(col_tot, key=lambda k: (-col_tot[k], k))
    if cols_by in ("hour", "weekday", "week", "month", "day"):
        ordered = sorted(col_tot)
    cols = ordered[:top]
    other = sum(col_tot[c] for c in ordered[top:])
    cells: dict = {}
    for d in data:
        cells.setdefault(d["r"], {})[d["c"]] = int(d["n"])
    rows = [{"key": r, "total": row_tot[r], "cells": [cells.get(r, {}).get(c, 0) for c in cols],
             "other": sum(v for c, v in cells.get(r, {}).items() if c not in cols)}
            for r in sorted(row_tot, key=lambda k: (-row_tot[k], k))]
    return {"rows_by": rows_by, "cols_by": cols_by, "cols": cols, "col_totals": [col_tot[c] for c in cols],
            "other_total": other, "rows": rows, "total": sum(row_tot.values())}


# ─── Triage ──────────────────────────────────────────────────────────────────

def check_changes(changes: dict, codes: set) -> Optional[str]:
    """None when every change is valid, else an error code (pure: unit-tested)."""
    if not changes:
        return "no_change"
    for k, v in changes.items():
        if k == "category":
            if v not in codes:
                return "bad_category"
        elif k == "stage":
            if v is not None and v not in fail_taxonomy.STAGES:
                return "bad_stage"
        elif k == "rd_status":
            if v not in RD_STATUSES:
                return "bad_status"
        elif k in TEXT_LIMITS:
            if v is not None and (not isinstance(v, str) or len(v) > TEXT_LIMITS[k]):
                return f"bad_{k}"
        else:
            return "bad_field"
    return None


def update(conn, request, user: dict, ids: list[int], changes: dict) -> int:
    """Apply one set of changes to FAIL runs; one audit row per run (its history). Returns rows changed."""
    from services import auth
    sets, params = [], []
    changes = {k: (v.strip() or None) if isinstance(v, str) else v for k, v in changes.items()}
    if "category" in changes:
        sets += ["category = %s", "classifier = 'rd'"]
        params.append(changes["category"])
        if "rd_status" not in changes:
            changes["rd_status"] = "reclassified"
    for k in ("stage", "rd_status", "rd_owner", "rd_note", "rd_action"):
        if k in changes:
            sets.append(f"{k} = %s"); params.append(changes[k])
    sets += ["rd_by = %s", "rd_at = NOW()", "updated_at = NOW()"]
    params.append(user["username"])
    before = {int(r["record_id"]): r for r in db.fetch_all(conn, f"""
        SELECT record_id, sn, category, stage, rd_status, rd_owner, rd_note, rd_action FROM fail_analysis
        WHERE record_id = ANY(%s) FOR UPDATE""", (ids,))}
    n = db.execute(conn, f"UPDATE fail_analysis SET {', '.join(sets)} WHERE record_id = ANY(%s)", (*params, ids))
    for rid, b in before.items():
        detail = {k: f"{b.get(k) if b.get(k) is not None else '—'} → {v if v is not None else '—'}"
                  for k, v in changes.items() if b.get(k) != v}
        if detail:
            auth.audit(conn, request, user["username"], "fail_rd", str(rid), {"sn": b["sn"], **detail})
    return n


def reset(conn, request, user: dict, ids: list[int]) -> int:
    """Undo RD's triage: the rules classify these runs again on the next scan (within a minute)."""
    from services import auth
    rows = db.fetch_all(conn, "SELECT record_id, sn, category, classifier, rd_status FROM fail_analysis WHERE record_id = ANY(%s) FOR UPDATE", (ids,))
    n = db.execute(conn, """UPDATE fail_analysis SET classifier = 'rule', classifier_version = 0, confidence = NULL, llm_model = NULL,
                                   llm_tried_at = NULL, stage = NULL, rd_status = 'new', rd_owner = NULL, rd_note = NULL,
                                   rd_action = NULL, rd_by = NULL, rd_at = NULL, updated_at = NOW()
                            WHERE record_id = ANY(%s)""", (ids,))
    for r in rows:
        auth.audit(conn, request, user["username"], "fail_rd", str(r["record_id"]),
                   {"sn": r["sn"], "reset": f"{r['classifier']} / {r['rd_status']} → rule / new"})
    return n


def history(record_id: int, limit: int = 30) -> list[dict]:
    return db.rows("""
        SELECT a.at, a.actor, COALESCE(u.display_name, a.actor) AS display_name, a.action, a.detail
        FROM account_audit a LEFT JOIN app_users u ON u.username = a.actor
        WHERE a.action IN ('fail_rd', 'fail_disposition') AND a.target = %s ORDER BY a.at DESC LIMIT %s
    """, (str(record_id), limit))


# ─── Unit history, log viewer, similar cases ────────────────────────────────

def sn_history(sn: str) -> list[dict]:
    rows = db.rows("""
        SELECT t.id, t.test_time, t.result, t.work_order, t.product_model, t.wifi_mac, t.bt_mac, t.avg_24g, t.avg_5g,
               t.rssi_after_24g, t.rssi_after_5g, t.bt_result, t.source_file, f.category, x.name_zh, x.name_en,
               f.ssid_group, f.station_port, f.rd_status, f.retest_outcome
        FROM test_record t LEFT JOIN fail_analysis f ON f.record_id = t.id LEFT JOIN fail_taxonomy x ON x.code = f.category
        WHERE t.sn = %s ORDER BY t.test_time, t.id LIMIT 100
    """, (sn,))
    for r in rows:
        for k in ("avg_24g", "avg_5g", "rssi_after_24g", "rssi_after_5g"):
            r[k] = None if r[k] is None else round(float(r[k]), 1)
        r["source_file"] = Path(str(r["source_file"] or "").replace("\\", "/")).name or None
    return rows


def log_view(record_id: int) -> Optional[dict]:
    rows = db.rows("""SELECT t.id, t.sn, t.test_time, t.result, t.work_order, t.product_model, t.source_file, t.raw_log,
                             f.evidence, f.category FROM test_record t LEFT JOIN fail_analysis f ON f.record_id = t.id
                      WHERE t.id = %s""", (record_id,))
    if not rows:
        return None
    r = rows[0]
    from services import fail_rules
    text = r.pop("raw_log") or ""
    r["lines"] = text.splitlines()
    r["evidence_lines"] = sorted({int(e["n"]) for e in (r.pop("evidence") or []) if isinstance(e.get("n"), int)})
    r["signal_lines"] = [x["n"] for x in fail_rules.excerpt(text, limit=400)]
    r["source_file"] = Path(str(r["source_file"] or "").replace("\\", "/")).name or None
    return r


SIGNATURE_SQL = "regexp_replace(COALESCE(f.evidence -> 0 ->> 'text', ''), '[0-9]+', '#', 'g')"


def similar(record_id: int, limit: int = 20) -> dict[str, Any]:
    me = db.rows(f"SELECT f.record_id, f.category, f.ssid_group, f.station_port, f.test_time, {SIGNATURE_SQL} AS sig FROM fail_analysis f WHERE f.record_id = %s",
                 (record_id,))
    if not me:
        return {"signature": None, "same_signature": [], "same_station": []}
    m = me[0]
    cols = "f.record_id, f.test_time, f.work_order, f.product_model, f.sn, f.category, x.name_zh, x.name_en, f.ssid_group, f.station_port, f.retest_outcome, f.rd_status"
    same_sig = db.rows(f"""SELECT {cols}, COUNT(*) OVER () AS total {FROM} WHERE f.record_id <> %s AND {SIGNATURE_SQL} = %s AND %s <> ''
                           ORDER BY f.test_time DESC LIMIT %s""", (record_id, m["sig"], m["sig"], limit))
    same_station = db.rows(f"""SELECT {cols} {FROM} WHERE f.record_id <> %s AND f.ssid_group IS NOT DISTINCT FROM %s
                               AND f.station_port IS NOT DISTINCT FROM %s
                               AND f.test_time BETWEEN %s::timestamp - INTERVAL '2 hours' AND %s::timestamp + INTERVAL '2 hours'
                               ORDER BY f.test_time LIMIT %s""",
                           (record_id, m["ssid_group"], m["station_port"], m["test_time"], m["test_time"], limit))
    return {"signature": m["sig"] or None, "same_signature_total": int(same_sig[0]["total"]) if same_sig else 0,
            "same_signature": [{k: v for k, v in r.items() if k != "total"} for r in same_sig], "same_station": same_station}


# ─── Export ──────────────────────────────────────────────────────────────────

CSV_FIELDS = ["record_id", "test_time", "work_order", "product_model", "sn", "wifi_mac", "category", "secondary", "band", "mbps",
              "threshold", "rssi", "attempts", "ssid_group", "station_port", "target_ssid", "retest_outcome", "run_seq",
              "classifier", "confidence", "stage", "rd_status", "rd_owner", "rd_note", "rd_action", "rd_by", "rd_at", "evidence"]


def export_csv(f: dict) -> str:
    w, p = _where(f)
    rows = db.rows(f"SELECT {LIST_COLUMNS} {FROM} WHERE {w} ORDER BY f.test_time, f.record_id", tuple(p))
    buf = io.StringIO()
    buf.write("﻿")          # Excel opens UTF-8 with a BOM
    out = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    out.writeheader()
    for r in rows:
        r["secondary"] = " ".join(r.get("secondary") or [])
        r["evidence"] = " | ".join(f"#{e.get('n')} {e.get('text')}" for e in (r.get("evidence") or [])[:3])
        out.writerow(r)
    return buf.getvalue()


def export_logs(f: dict) -> tuple[bytes, int, int]:
    """A zip of the raw logs of the filtered FAIL runs (newest EXPORT_LOGS_MAX). Returns (bytes, included, total)."""
    w, p = _where(f)
    total = int(db.rows(f"SELECT COUNT(*) AS n {FROM} WHERE {w}", tuple(p))[0]["n"])
    rows = db.rows(f"""SELECT f.record_id, f.category, t.source_file, t.raw_log {FROM} WHERE {w}
                       ORDER BY f.test_time DESC, f.record_id DESC LIMIT %s""", (*p, EXPORT_LOGS_MAX))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in rows:
            name = Path(str(r["source_file"] or "").replace("\\", "/")).name or f"record_{r['record_id']}.txt"
            z.writestr(f"{r['category']}/{r['record_id']}_{name}", r["raw_log"] or "")
    return buf.getvalue(), len(rows), total


# ─── Ask AI about the filtered FAILs (L2b) ───────────────────────────────────

ASK_SYSTEM = (
    "You help an RD engineer triage failed WiFi / Bluetooth production tests. You get the aggregates of ONE filtered "
    "group of FAIL test runs (counts by failure category, product, station, hour, retest outcome, throughput figures) "
    "and the failure category descriptions. Write in {lang_name}, plain sentences, no markdown: a one-sentence summary, "
    "up to 3 common points, up to 3 possible causes (phrased as possibilities, never as facts), up to 3 next checks. "
    "Every number you write must appear in the aggregates; do not compute new numbers. Never show field names or codes "
    "(for example WIFI_TP_LOW_24G, pass_on_retest): use the category names and plain words."
)
ASK_SCHEMA = {"type": "object", "properties": {
    "summary": {"type": "string", "maxLength": 300},
    "common": {"type": "array", "items": {"type": "string", "maxLength": 240}, "maxItems": 3},
    "causes": {"type": "array", "items": {"type": "string", "maxLength": 240}, "maxItems": 3},
    "checks": {"type": "array", "items": {"type": "string", "maxLength": 240}, "maxItems": 3},
}, "required": ["summary", "common", "causes", "checks"], "additionalProperties": False}
CJK = re.compile(r"[一-鿿]")
OUTCOME_CODES = ("pass_on_retest", "failed_again", "not_retested")


def _codes_in(text: str, facts: dict) -> Optional[str]:
    """A category code, retest code or field name in a sentence (the reader gets words, not codes)."""
    for token in [*fail_taxonomy.names().keys(), *OUTCOME_CODES, *facts.keys()]:
        if "_" in token and re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", text):
            return token
    return None


def facts(f: dict) -> dict[str, Any]:
    """What the LLM may see: aggregates only (no SN, MAC or log text)."""
    names = fail_taxonomy.names()

    def top(by, n=8):
        g = group(f, by)
        return [{"value": names.get(i["key"], {}).get("name_en", i["key"]) if by == "category" else i["key"],
                 "runs": i["runs"], "units": i["units"], "passed_on_retest": i["passed_retest"]} for i in g["items"][:n]]
    w, p = _where(f)
    agg = db.rows(f"""SELECT COUNT(*) AS runs, COUNT(DISTINCT f.sn) AS units, MIN(f.test_time) AS first, MAX(f.test_time) AS last,
                             COUNT(*) FILTER (WHERE f.retest_outcome = 'pass_on_retest') AS passed_on_retest,
                             COUNT(*) FILTER (WHERE f.retest_outcome = 'failed_again') AS failed_again,
                             COUNT(*) FILTER (WHERE f.retest_outcome = 'not_retested') AS not_retested
                      {FROM} WHERE {w}""", tuple(p))[0]
    tp = db.rows(f"""SELECT f.category, f.band, COUNT(*) AS n, ROUND(MIN(f.mbps)::numeric, 1) AS min_mbps,
                            ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY f.mbps))::numeric, 1) AS median_mbps,
                            ROUND(MAX(f.mbps)::numeric, 1) AS max_mbps, MAX(f.threshold) AS threshold,
                            ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY f.rssi))::numeric, 0) AS median_rssi
                     {FROM} WHERE {w} AND f.mbps IS NOT NULL GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 6""", tuple(p))
    cats = {r["category"] for r in db.rows(f"SELECT DISTINCT f.category {FROM} WHERE {w}", tuple(p))}
    return {
        "fail_runs": int(agg["runs"]), "units": int(agg["units"]),
        "first_test": str(agg["first"])[:16] if agg["first"] else None, "last_test": str(agg["last"])[:16] if agg["last"] else None,
        "passed_on_retest": int(agg["passed_on_retest"]), "failed_again": int(agg["failed_again"]), "not_retested": int(agg["not_retested"]),
        "by_category": top("category"), "by_product": top("product", 6), "by_station": top("station", 8),
        "by_hour": top("hour", 24), "by_week": top("week", 12),
        "throughput": [{"category": names.get(r["category"], {}).get("name_en", r["category"]), "band": r["band"], "runs": int(r["n"]),
                        "min_mbps": float(r["min_mbps"]) if r["min_mbps"] is not None else None,
                        "median_mbps": float(r["median_mbps"]) if r["median_mbps"] is not None else None,
                        "max_mbps": float(r["max_mbps"]) if r["max_mbps"] is not None else None,
                        "threshold": float(r["threshold"]) if r["threshold"] is not None else None,
                        "median_rssi": float(r["median_rssi"]) if r["median_rssi"] is not None else None} for r in tp],
        "categories": [{"name": names[c]["name_en"], "description": names[c]["description_en"]} for c in sorted(cats) if c in names],
    }


def ask(f: dict, question: str, lang: str) -> dict[str, Any]:
    fx = facts(f)
    if not fx["fail_runs"]:
        return {"facts": fx, "answer": None, "rejected": [], "elapsed": 0.0, "empty": True}
    lang_name = "Traditional Chinese (full-width punctuation)" if lang == "zh" else "English"
    user = {"aggregates": fx}
    if question:
        user["question"] = question
    messages = [{"role": "system", "content": ASK_SYSTEM.format(lang_name=lang_name)},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False, default=str)}]
    t0 = time.perf_counter()
    out = llm.complete_json(messages, ASK_SCHEMA, name="rd_ask", timeout=config.LLM_SUMMARY_TIMEOUT, max_tokens=900, temperature=0.2)
    rejected = []
    clean: dict[str, Any] = {}
    extra = tuple(x for x in (f.get("work_order"), f.get("product_model"), f.get("date_from"), f.get("date_to")) if x)
    for field in ("summary", "common", "causes", "checks"):
        items = [out.get(field) or ""] if field == "summary" else list(out.get(field) or [])
        kept = []
        for text in items:
            text = (text or "").strip()
            if not text:
                continue
            ok, bad = numbers_ok(text, fx, extra)
            wrong_lang = (lang == "en" and CJK.search(text)) or (lang == "zh" and not CJK.search(text))
            code = _codes_in(text, fx)
            if ok and not wrong_lang and not code:
                kept.append(text)
            else:
                rejected.append({"field": field, "text": text, "numbers": bad, "language": bool(wrong_lang), "code": code})
        clean[field] = (kept[0] if kept else "") if field == "summary" else kept
    return {"facts": fx, "answer": clean, "rejected": rejected, "elapsed": round(time.perf_counter() - t0, 1),
            "model": llm.model_name(), "at": datetime.now().isoformat(timespec="seconds")}
