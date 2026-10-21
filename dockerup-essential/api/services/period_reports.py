"""Weekly and monthly reports (5E, L5): facts by rules, one short LLM summary per report, Markdown download.

facts(kind, start)          units, yield, PASS / FAIL, retry rate, throughput, every work order and product of the
                            period, FAIL categories and dispositions, the red / amber work orders with their notes, and
                            the period before for comparison
generate(kind, start, llm)  store the report (period_report, one row per kind + period); the summary is written by the
                            LLM when asked and online (numbers checked, a sentence with a number not in the facts is
                            dropped), otherwise by rules
process_once()              scheduler: the last finished week (Monday) and month (1st) — only those, LLM budget
markdown(row, lang)         the report as Markdown
"""
import json
import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Optional

import psycopg2.extras

import config
import db
from services import llm, settings, wo_status
from services.filters import UNIT
from services.stats import RETRY_FILTER_SQL
from services.wo_rules import MIN_UNITS_YIELD, local_now, numbers_ok

log = logging.getLogger("wifi.period_reports")
KINDS = ("week", "month")
CJK = re.compile(r"[一-鿿]")


def bounds(kind: str, start: date) -> tuple[date, date]:
    """Normalised (first, last) day of the week (Monday–Sunday) or month containing `start`. Pure."""
    if kind == "week":
        first = start - timedelta(days=start.isoweekday() - 1)
        return first, first + timedelta(days=6)
    first = start.replace(day=1)
    return first, (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def previous_start(kind: str, first: date) -> date:
    return first - timedelta(days=7) if kind == "week" else (first - timedelta(days=1)).replace(day=1)


def _dt(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time())


def _totals(a: date, b: date) -> dict[str, Any]:
    r = db.rows(f"""SELECT COUNT(*) AS units, COUNT(*) FILTER (WHERE result = TRUE) AS pass, COUNT(*) FILTER (WHERE result = FALSE) AS fail,
                           COUNT(*) FILTER (WHERE {RETRY_FILTER_SQL}) AS retry, AVG(avg_24g) AS a24, AVG(avg_5g) AS a5,
                           COUNT(DISTINCT work_order) AS wos, COUNT(DISTINCT test_time::date) AS days
                    FROM {UNIT} WHERE test_time >= %s AND test_time < %s""", (_dt(a), _dt(b + timedelta(days=1))))[0]
    units = int(r["units"])
    return {"units": units, "pass": int(r["pass"]), "fail": int(r["fail"]),
            "yield_pct": round(int(r["pass"]) * 100.0 / units, 2) if units else None,
            "retry_rate": round(int(r["retry"]) * 100.0 / units, 2) if units else None,
            "avg_24g": round(float(r["a24"]), 1) if r["a24"] is not None else None,
            "avg_5g": round(float(r["a5"]), 1) if r["a5"] is not None else None,
            "work_orders": int(r["wos"]), "test_days": int(r["days"])}


def facts(kind: str, start: date) -> dict[str, Any]:
    a, b = bounds(kind, start)
    pa, pb = bounds(kind, previous_start(kind, a))
    lo, hi = _dt(a), _dt(b + timedelta(days=1))
    totals, prev = _totals(a, b), _totals(pa, pb)
    statuses = wo_status.statuses()
    wos = db.rows(f"""SELECT work_order, product_model, COUNT(*) AS units, COUNT(*) FILTER (WHERE result = TRUE) AS pass,
                             MIN(test_time) AS first, MAX(test_time) AS last
                      FROM {UNIT} WHERE test_time >= %s AND test_time < %s AND work_order IS NOT NULL AND work_order <> ''
                      GROUP BY 1, 2 ORDER BY 3 DESC""", (lo, hi))
    work_orders = []
    for w in wos:
        st = statuses.get((w["work_order"], w["product_model"])) or {}
        units = int(w["units"])
        # Fewer than MIN_UNITS_YIELD units in the period: no yield (one failed unit is not "0% yield"), as the status rules.
        work_orders.append({"work_order": w["work_order"], "product_model": w["product_model"], "units": units,
                            "yield_pct": round(int(w["pass"]) * 100.0 / units, 2) if units >= MIN_UNITS_YIELD else None,
                            "fail": units - int(w["pass"]),
                            "first_test": str(w["first"])[:16], "last_test": str(w["last"])[:16],
                            "light": st.get("light"), "state": st.get("state"), "total": st.get("total"), "planned": st.get("planned"),
                            "flags": [f["code"] for f in st.get("flags", []) if f["level"] in ("bad", "warn")]})
    products = db.rows(f"""SELECT product_model, COUNT(*) AS units, COUNT(*) FILTER (WHERE result = TRUE) AS pass
                           FROM {UNIT} WHERE test_time >= %s AND test_time < %s GROUP BY 1 ORDER BY 2 DESC""", (lo, hi))
    cats = db.rows("""SELECT f.category, x.name_zh, x.name_en, COUNT(*) AS runs, COUNT(DISTINCT f.sn) AS units,
                             COUNT(*) FILTER (WHERE f.retest_outcome = 'pass_on_retest') AS passed_retest
                      FROM fail_analysis f LEFT JOIN fail_taxonomy x ON x.code = f.category
                      WHERE f.test_time >= %s AND f.test_time < %s GROUP BY 1, 2, 3 ORDER BY 4 DESC""", (lo, hi))
    still = int(db.rows(f"SELECT COUNT(*) AS n FROM {UNIT} WHERE result = FALSE AND test_time >= %s AND test_time < %s", (lo, hi))[0]["n"])
    handled = int(db.rows(f"""SELECT COUNT(*) AS n FROM {UNIT} m WHERE result = FALSE AND test_time >= %s AND test_time < %s
                              AND EXISTS (SELECT 1 FROM fail_disposition d WHERE d.record_id = m.id AND d.status <> 'retest')""", (lo, hi))[0]["n"])
    return {
        "kind": kind, "from": a.isoformat(), "to": b.isoformat(), "previous_from": pa.isoformat(), "previous_to": pb.isoformat(),
        "totals": totals, "previous": prev,
        "change": {"units": totals["units"] - prev["units"],
                   "yield_pct": round(totals["yield_pct"] - prev["yield_pct"], 2) if totals["yield_pct"] is not None and prev["yield_pct"] is not None else None},
        "work_orders": work_orders,
        "products": [{"product_model": p["product_model"], "units": int(p["units"]),
                      "yield_pct": round(int(p["pass"]) * 100.0 / int(p["units"]), 2) if int(p["units"]) else None} for p in products],
        "fail_categories": [{**c, "runs": int(c["runs"]), "units": int(c["units"]), "passed_retest": int(c["passed_retest"])} for c in cats],
        "still_failing": still, "handled": handled,
        "attention": [w for w in work_orders if w["light"] in ("bad", "warn")],
        "thresholds": {k: settings.get()[k] for k in ("yield_good", "yield_warn")},
    }


def rule_summary(f: dict[str, Any]) -> dict[str, str]:
    t, p, c = f["totals"], f["previous"], f["change"]
    if not t["units"]:
        return {"zh": "這段期間沒有測試。", "en": "No tests in this period."}
    att = f["attention"]
    zh = f"共測 {t['units']:,} 台、{t['work_orders']} 張工單，良率 {t['yield_pct']}%"
    en = f"{t['units']:,} units in {t['work_orders']} work orders, yield {t['yield_pct']}%"
    if p["units"]:
        zh += f"（前期 {p['units']:,} 台、{p['yield_pct']}%）"
        en += f" (the period before: {p['units']:,} units, {p['yield_pct']}%)"
    zh += "。"
    en += "."
    if f["fail_categories"]:
        top = f["fail_categories"][0]
        zh += f"FAIL 最多的是{top['name_zh'] or top['category']}（{top['runs']} 次，其中 {top['passed_retest']} 次重測後通過）。"
        en += f" The most common failure: {top['name_en'] or top['category']} ({top['runs']} runs, {top['passed_retest']} passed on retest)."
    if att:
        zh += f"需要注意的工單：{'、'.join(w['work_order'] for w in att[:3])}。"
        en += f" Work orders to look at: {', '.join(w['work_order'] for w in att[:3])}."
    if f["still_failing"]:
        zh += f"仍有 {f['still_failing']} 台失敗，已處置 {f['handled']} 台。"
        en += f" {f['still_failing']} units still failing, {f['handled']} handled."
    return {"zh": zh, "en": en}


SYSTEM = (
    "You write the summary of a {kind} report of a WiFi / Bluetooth module production test line from the facts JSON. "
    "Return Traditional Chinese (full-width punctuation, count units with 台) and English: 3 to 5 plain sentences each — "
    "output and yield against the period before, the work orders that need attention and why, the main failure "
    "categories and how many passed on retest. Every number must appear in the facts; do not compute new numbers. "
    "A work order without a yield (yield_pct null: too few units in the period) is not judged on yield. "
    "Causes are possibilities. No markdown, no field names or codes."
)
SCHEMA = {"type": "object", "properties": {"zh": {"type": "string", "maxLength": 600}, "en": {"type": "string", "maxLength": 900}},
          "required": ["zh", "en"], "additionalProperties": False}


def _sentences(text: str) -> list[str]:
    return [x for x in re.split(r"(?<=[。！？])\s*|(?<=[.!?])\s+", text or "") if x.strip()]


def llm_summary(f: dict[str, Any]) -> tuple[dict[str, str], list]:
    messages = [{"role": "system", "content": SYSTEM.format(kind="weekly" if f["kind"] == "week" else "monthly")},
                {"role": "user", "content": json.dumps(f, ensure_ascii=False, default=str)}]
    out = llm.complete_json(messages, SCHEMA, name="period_report", timeout=config.LLM_SUMMARY_TIMEOUT, max_tokens=900, temperature=0.2)
    clean, dropped = {}, []
    extra = tuple(w["work_order"] for w in f["work_orders"]) + tuple(p["product_model"] or "" for p in f["products"]) + (f["from"], f["to"])
    for lang in ("zh", "en"):
        kept = []
        for s in _sentences(out.get(lang) or ""):
            ok, bad = numbers_ok(s, f, extra)
            wrong = (lang == "en" and CJK.search(s)) or (lang == "zh" and not CJK.search(s))
            (kept if ok and not wrong else dropped).append(s if ok and not wrong else {"lang": lang, "text": s, "numbers": bad})
        clean[lang] = ("" if lang == "zh" else " ").join(kept)
    return clean, dropped


def generate(kind: str, start: date, use_llm: bool = True, requested_by: Optional[str] = None) -> dict[str, Any]:
    a, b = bounds(kind, start)
    f = facts(kind, a)
    summary, source, dropped, model, t0 = rule_summary(f), "rule", [], None, time.perf_counter()
    if use_llm and f["totals"]["units"] and llm.status()["connected"]:
        try:
            s, dropped = llm_summary(f)
            if s.get("zh") and s.get("en"):
                summary, source, model = s, "llm", llm.model_name()
        except Exception as exc:
            log.warning("period report summary failed: %s", exc)
    with db.db() as conn:
        db.execute(conn, """
            INSERT INTO period_report (kind, period_start, period_end, facts, summary, source, model, rejected, elapsed_sec, generated_at, requested_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
            ON CONFLICT (kind, period_start) DO UPDATE SET period_end = EXCLUDED.period_end, facts = EXCLUDED.facts,
                summary = EXCLUDED.summary, source = EXCLUDED.source, model = EXCLUDED.model, rejected = EXCLUDED.rejected,
                elapsed_sec = EXCLUDED.elapsed_sec, generated_at = NOW(), requested_by = EXCLUDED.requested_by
        """, (kind, a, b, psycopg2.extras.Json(f, dumps=lambda x: json.dumps(x, ensure_ascii=False, default=str)),
              psycopg2.extras.Json(summary), source, model, psycopg2.extras.Json(dropped), round(time.perf_counter() - t0, 1), requested_by))
    return get(kind, a)


def get(kind: str, start: date) -> Optional[dict[str, Any]]:
    a, _ = bounds(kind, start)
    rows = db.rows("SELECT * FROM period_report WHERE kind = %s AND period_start = %s", (kind, a))
    return rows[0] if rows else None


def listing(limit: int = 60) -> list[dict[str, Any]]:
    return db.rows("""SELECT kind, period_start, period_end, source, generated_at, (facts -> 'totals' ->> 'units')::int AS units,
                             (facts -> 'totals' ->> 'yield_pct')::float AS yield_pct, jsonb_array_length(facts -> 'attention') AS attention
                      FROM period_report ORDER BY period_start DESC, kind LIMIT %s""", (limit,))


def due(now: datetime) -> list[tuple[str, date]]:
    """The last finished week and month, if their report is missing (pure apart from the lookup)."""
    today = now.date()
    last_week = today - timedelta(days=today.isoweekday() - 1) - timedelta(days=7)
    last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    return [(k, s) for k, s in (("week", last_week), ("month", last_month)) if not get(k, s)]


_last = {"check": 0.0}


def process_once() -> bool:
    """Write one missing report (the latest finished week / month); checked at most every 10 minutes."""
    if time.time() - _last["check"] < 600:
        return False
    _last["check"] = time.time()
    with db.db() as conn:          # one writer across the API workers (the lock ends with this transaction)
        if not db.fetch_one(conn, "SELECT pg_try_advisory_xact_lock(%s) AS ok", (config.PERIOD_REPORT_LOCK_KEY,))["ok"]:
            return False
        for kind, start in due(local_now(config.LOCAL_TZ)):
            generate(kind, start, use_llm=True, requested_by="schedule")
            log.info("period report %s %s written", kind, start)
            return True
    return False


def markdown(row: dict[str, Any], lang: str = "zh") -> str:
    f = row["facts"]
    zh = lang == "zh"
    t, p = f["totals"], f["previous"]
    title = (f"{'週報' if f['kind'] == 'week' else '月報'} {f['from']} ~ {f['to']}" if zh else
             f"{'Weekly' if f['kind'] == 'week' else 'Monthly'} report {f['from']} – {f['to']}")
    fmt = lambda v, d=2: "—" if v is None else (f"{v:,}" if isinstance(v, int) else f"{v:,.{d}f}")  # noqa: E731
    lines = [f"# {title}", "", (row.get("summary") or {}).get(lang, ""), "",
             "## 數字" if zh else "## Numbers", "",
             "| | " + ("本期 | 前期" if zh else "This period | Period before") + " |", "|---|---:|---:|"]
    for key, label_zh, label_en, d in (("units", "測試台數", "Units", 0), ("yield_pct", "良率 %", "Yield %", 2), ("fail", "FAIL 台數", "FAIL units", 0),
                                       ("retry_rate", "重測率 %", "Retry rate %", 2), ("avg_24g", "2.4G 平均 Mbps", "Avg 2.4G Mbps", 1),
                                       ("avg_5g", "5G 平均 Mbps", "Avg 5G Mbps", 1), ("work_orders", "工單數", "Work orders", 0)):
        lines.append(f"| {label_zh if zh else label_en} | {fmt(t[key], d)} | {fmt(p[key], d)} |")
    lines += ["", "## 工單" if zh else "## Work orders", "",
              "| " + ("工單 | 產品 | 本期台數 | 良率 % | 狀態" if zh else "Work order | Product | Units | Yield % | Status") + " |", "|---|---|---:|---:|---|"]
    light = {"bad": "🔴", "warn": "🟡", "good": "🟢"}
    for w in f["work_orders"]:
        lines.append(f"| {w['work_order']} | {w['product_model'] or '—'} | {fmt(w['units'])} | {fmt(w['yield_pct'])} | {light.get(w['light'], '')} |")
    if any(w["yield_pct"] is None for w in f["work_orders"]):
        lines += ["", "_少於 10 台的工單不計良率。_" if zh else "_No yield for work orders with fewer than 10 units in the period._"]
    if f["fail_categories"]:
        lines += ["", "## FAIL 現象" if zh else "## Failures", "",
                  "| " + ("現象 | 次數 | 台數 | 重測後通過" if zh else "Category | Runs | Units | Passed on retest") + " |", "|---|---:|---:|---:|"]
        for c in f["fail_categories"]:
            lines.append(f"| {(c['name_zh'] if zh else c['name_en']) or c['category']} | {c['runs']} | {c['units']} | {c['passed_retest']} |")
    lines += ["", (f"_產生時間 {str(row.get('generated_at'))[:16]}，摘要{'由 AI 撰寫（數字已核對）' if row.get('source') == 'llm' else '由規則產生'}。_" if zh else
                   f"_Generated {str(row.get('generated_at'))[:16]}; summary {'written by the AI (numbers checked)' if row.get('source') == 'llm' else 'by rules'}._")]
    return "\n".join(lines) + "\n"
