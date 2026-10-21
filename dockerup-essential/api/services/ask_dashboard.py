"""Ask the dashboard (5E, L6; Q25: open to everyone, rate limited, aggregates only).

The LLM only turns the question into a restricted query object (json_schema with enums: metric, period, product,
work order, grouping); it never writes SQL and never sees data. The server checks the object, runs it on mv_unit /
fail_analysis and writes the answer from the numbers by rules — one LLM call per question, and every number shown
comes from the database. Nothing per unit (SN, MAC, logs) can be asked for.

parse(question, lang)  → query object (LLM), checked by normalise()
run(q, now)            → {"query", "period", "value(s)", "rows", "answer": {zh, en}}
"""
import json
from datetime import date, datetime, timedelta
from typing import Any, Optional

import config
import db
from services import analytics, llm
from services.filters import UNIT, range_where
from services.stats import RETRY_FILTER_SQL
from services.wo_rules import local_now

METRICS = ("units", "yield", "pass", "fail", "fail_rate", "retry_rate", "avg_24g", "avg_5g", "work_orders", "fail_categories", "unsupported")
PERIODS = ("all", "today", "yesterday", "this_week", "last_week", "this_month", "last_month", "this_year", "last_year",
           "last_n_days", "month", "custom")
GROUPS = ("none", "day", "week", "month", "product", "work_order")
MAX_ROWS = 31

SYSTEM = (
    "You turn a question about a WiFi / Bluetooth module production test line into a query object for its dashboard. "
    "Today is {today} (the test stations' clock). Metrics: units (units tested), yield (% passed), pass, fail (failed units), "
    "fail_rate, retry_rate (% of units retested), avg_24g / avg_5g (average throughput, Mbps), work_orders (how many), "
    "fail_categories (failures by category). Periods: all, today, yesterday, this_week, last_week, this_month, last_month, "
    "this_year, last_year, last_n_days (set n), month (set year and month), custom (set date_from and date_to, YYYY-MM-DD). "
    "group_by is 'none' unless the question asks for a split (per day / week / month / product / work order, 'by …', "
    "'each …', 'trend'); a single total or rate is group_by 'none'. product must be one of the listed products "
    "or null; work_order one of the listed work orders, 'latest', or null. compare_previous asks for the period before. "
    "Questions about single units, serial numbers, MAC addresses, logs, people, or anything else the metrics cannot answer: "
    "metric 'unsupported' with a short reason. Never invent products or work orders."
)


def schema(products: list[str], work_orders: list[str]) -> dict:
    nullable = lambda values: {"anyOf": [{"type": "string", "enum": values}, {"type": "null"}]}  # noqa: E731
    return {"type": "object", "properties": {
        "metric": {"type": "string", "enum": list(METRICS)},
        "period": {"type": "string", "enum": list(PERIODS)},
        "n": {"anyOf": [{"type": "integer", "minimum": 1, "maximum": 366}, {"type": "null"}]},
        "year": {"anyOf": [{"type": "integer", "minimum": 2020, "maximum": 2100}, {"type": "null"}]},
        "month": {"anyOf": [{"type": "integer", "minimum": 1, "maximum": 12}, {"type": "null"}]},
        "date_from": {"anyOf": [{"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}, {"type": "null"}]},
        "date_to": {"anyOf": [{"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}, {"type": "null"}]},
        "product": nullable(products) if products else {"type": "null"},
        "work_order": nullable(work_orders + ["latest"]),
        "group_by": {"type": "string", "enum": list(GROUPS)},
        "compare_previous": {"type": "boolean"},
        "reason": {"type": "string", "maxLength": 200},
    }, "required": ["metric", "period", "group_by", "compare_previous", "product", "work_order"], "additionalProperties": False}


def choices() -> tuple[list[str], list[str]]:
    opts = analytics.filter_options()
    return [p["product_model"] for p in opts["products"]], [w["work_order"] for w in opts["work_orders"][:40]]


def parse(question: str, lang: str, now: datetime) -> dict:
    products, wos = choices()
    messages = [{"role": "system", "content": SYSTEM.format(today=now.date().isoformat())},
                {"role": "user", "content": json.dumps({"products": products, "work_orders": wos, "question": question}, ensure_ascii=False)}]
    out = llm.complete_json(messages, schema(products, wos), name="ask_dashboard", max_tokens=300, temperature=0)
    return normalise(out, products, wos, now)


def normalise(q: dict, products: list[str], wos: list[str], now: datetime) -> dict:
    """Keep only allowed values (pure apart from the lists): the LLM cannot widen what the server runs."""
    out = {"metric": q.get("metric") if q.get("metric") in METRICS else "unsupported",
           "period": q.get("period") if q.get("period") in PERIODS else "all",
           "group_by": q.get("group_by") if q.get("group_by") in GROUPS else "none",
           "compare_previous": bool(q.get("compare_previous")), "reason": (q.get("reason") or "")[:200],
           "product": q.get("product") if q.get("product") in products else None,
           "work_order": None}
    wo = q.get("work_order")
    if wo == "latest" and wos:
        out["work_order"] = wos[0]
    elif wo in wos:
        out["work_order"] = wo
    for k, lo, hi in (("n", 1, 366), ("year", 2020, 2100), ("month", 1, 12)):
        v = q.get(k)
        out[k] = v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else None
    for k in ("date_from", "date_to"):
        try:
            out[k] = date.fromisoformat(q.get(k)).isoformat() if q.get(k) else None
        except (TypeError, ValueError):
            out[k] = None
    if out["period"] == "last_n_days" and not out["n"]:
        out["n"] = 7
    if out["period"] == "month" and not (out["year"] and out["month"]):
        out["period"] = "this_month"
    if out["period"] == "custom" and not (out["date_from"] or out["date_to"]):
        out["period"] = "all"
    # A split that can only give one group is no split (the LLM likes to add one): answer with the single number.
    one_group = {"work_order": bool(out["work_order"]), "product": bool(out["product"]) or bool(out["work_order"]),
                 "month": out["period"] in ("this_month", "last_month", "month", "today", "yesterday", "this_week", "last_week"),
                 "week": out["period"] in ("this_week", "last_week", "today", "yesterday"),
                 "day": out["period"] in ("today", "yesterday")}
    if one_group.get(out["group_by"]) or out["compare_previous"] or out["metric"] == "fail_categories":
        out["group_by"] = "none"
    return out


def window(q: dict, today: date) -> tuple[Optional[date], Optional[date]]:
    """Inclusive (from, to) dates of the query's period; (None, None) = all data. Pure."""
    p = q["period"]
    if p == "today":
        return today, today
    if p == "yesterday":
        d = today - timedelta(days=1)
        return d, d
    if p in ("this_week", "last_week"):
        mon = today - timedelta(days=today.isoweekday() - 1) - (timedelta(days=7) if p == "last_week" else timedelta())
        return mon, mon + timedelta(days=6)
    if p in ("this_month", "last_month", "month"):
        first = today.replace(day=1)
        if p == "last_month":
            first = (first - timedelta(days=1)).replace(day=1)
        if p == "month":
            first = date(q["year"], q["month"], 1)
        nxt = (first + timedelta(days=32)).replace(day=1)
        return first, nxt - timedelta(days=1)
    if p in ("this_year", "last_year"):
        y = today.year - (p == "last_year")
        return date(y, 1, 1), date(y, 12, 31)
    if p == "last_n_days":
        return today - timedelta(days=q["n"] - 1), today
    if p == "custom":
        a = date.fromisoformat(q["date_from"]) if q.get("date_from") else None
        b = date.fromisoformat(q["date_to"]) if q.get("date_to") else None
        return a, b
    return None, None


def previous(q: dict, a: Optional[date], b: Optional[date]) -> tuple[Optional[date], Optional[date]]:
    """The period before: the previous calendar month / week / year for those periods, else the same number of days
    just before. Pure."""
    if not (a and b):
        return None, None
    p = q["period"]
    if p in ("this_month", "last_month", "month"):
        first = (a - timedelta(days=1)).replace(day=1)
        return first, a - timedelta(days=1)
    if p in ("this_year", "last_year"):
        return date(a.year - 1, 1, 1), date(a.year - 1, 12, 31)
    span = (b - a).days + 1
    return a - timedelta(days=span), a - timedelta(days=1)


def _win(a, b):
    return {"window_start": datetime.combine(a, datetime.min.time()) if a else None,
            "window_end": datetime.combine(b + timedelta(days=1), datetime.min.time()) if b else None}


def _scalar(metric, a, b, wo, pm) -> Optional[float]:
    s = analytics.summary(_win(a, b), wo, pm)
    if metric == "work_orders":
        where, params = range_where(_win(a, b), wo, pm, "work_order IS NOT NULL AND work_order <> ''")
        return int(db.rows(f"SELECT COUNT(DISTINCT work_order) AS n FROM {UNIT} {where}", tuple(params))[0]["n"])
    if not s["total"]:
        return None if metric not in ("units", "pass", "fail") else 0
    return {"units": s["total"], "yield": s["yield_pct"], "pass": s["pass"], "fail": s["fail"],
            "fail_rate": round(s["fail"] * 100.0 / s["total"], 2), "retry_rate": s["retry_rate"],
            "avg_24g": s["avg_24g"], "avg_5g": s["avg_5g"]}[metric]


GROUP_SQL = {"day": "to_char(test_time, 'YYYY-MM-DD')", "week": "to_char(date_trunc('week', test_time), 'YYYY-MM-DD')",
             "month": "to_char(test_time, 'YYYY-MM')", "product": "COALESCE(product_model, '—')", "work_order": "COALESCE(work_order, '—')"}
METRIC_SQL = {"units": "COUNT(*)", "pass": "COUNT(*) FILTER (WHERE result = TRUE)", "fail": "COUNT(*) FILTER (WHERE result = FALSE)",
              "yield": "ROUND((COUNT(*) FILTER (WHERE result = TRUE) * 100.0 / NULLIF(COUNT(*), 0))::numeric, 2)",
              "fail_rate": "ROUND((COUNT(*) FILTER (WHERE result = FALSE) * 100.0 / NULLIF(COUNT(*), 0))::numeric, 2)",
              "retry_rate": f"ROUND((COUNT(*) FILTER (WHERE {RETRY_FILTER_SQL}) * 100.0 / NULLIF(COUNT(*), 0))::numeric, 2)",
              "avg_24g": "ROUND(AVG(avg_24g)::numeric, 2)", "avg_5g": "ROUND(AVG(avg_5g)::numeric, 2)",
              "work_orders": "COUNT(DISTINCT work_order)"}


def _grouped(metric, group, a, b, wo, pm) -> list[dict]:
    where, params = range_where(_win(a, b), wo, pm)
    order = "1" if group in ("day", "week", "month") else "3 DESC"
    rows = db.rows(f"SELECT {GROUP_SQL[group]} AS key, {METRIC_SQL[metric]} AS value, COUNT(*) AS units FROM {UNIT} {where} "
                   f"GROUP BY 1 ORDER BY {order} LIMIT %s", (*params, MAX_ROWS if group in ('product', 'work_order') else 400))
    out = [{"key": r["key"], "value": float(r["value"]) if r["value"] is not None else None, "units": int(r["units"])} for r in rows]
    return out[-MAX_ROWS:] if group in ("day", "week", "month") else out


def _categories(a, b, wo, pm) -> list[dict]:
    w, p = ["TRUE"], []
    if a:
        w.append("f.test_time >= %s"); p.append(datetime.combine(a, datetime.min.time()))
    if b:
        w.append("f.test_time < %s"); p.append(datetime.combine(b + timedelta(days=1), datetime.min.time()))
    if wo:
        w.append("f.work_order = %s"); p.append(wo)
    if pm:
        w.append("f.product_model = %s"); p.append(pm)
    rows = db.rows(f"""SELECT f.category, x.name_zh, x.name_en, COUNT(*) AS runs, COUNT(DISTINCT f.sn) AS units
                       FROM fail_analysis f LEFT JOIN fail_taxonomy x ON x.code = f.category WHERE {' AND '.join(w)}
                       GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 10""", tuple(p))
    return [{**r, "runs": int(r["runs"]), "units": int(r["units"])} for r in rows]


UNIT_TEXT = {"yield": "%", "fail_rate": "%", "retry_rate": "%", "avg_24g": " Mbps", "avg_5g": " Mbps"}
NAMES = {"zh": {"units": "測試台數", "yield": "良率", "pass": "PASS 台數", "fail": "FAIL 台數", "fail_rate": "FAIL 比例",
                "retry_rate": "重測率", "avg_24g": "2.4G 平均吞吐量", "avg_5g": "5G 平均吞吐量", "work_orders": "工單數",
                "fail_categories": "失敗現象"},
         "en": {"units": "units tested", "yield": "yield", "pass": "PASS units", "fail": "FAIL units", "fail_rate": "FAIL rate",
                "retry_rate": "retry rate", "avg_24g": "average 2.4G throughput", "avg_5g": "average 5G throughput",
                "work_orders": "work orders", "fail_categories": "failure categories"}}


def _fmt(metric, v):
    if v is None:
        return "—"
    if metric in ("units", "pass", "fail", "work_orders"):
        return f"{int(v):,}"
    return f"{float(v):,.2f}{UNIT_TEXT.get(metric, '')}"


def _scope_text(q, a, b, lang):
    when = "全部資料" if lang == "zh" else "all data"
    if a or b:
        when = f"{a.isoformat() if a else '…'} ~ {b.isoformat() if b else '…'}" if a != b else a.isoformat()
    what = [x for x in (q.get("product"), q.get("work_order") and (f"工單 {q['work_order']}" if lang == "zh" else f"work order {q['work_order']}")) if x]
    return ("，".join([when, *what]) if lang == "zh" else ", ".join([when, *what]))


def answer(q: dict, res: dict) -> dict[str, str]:
    """The answer sentence from the numbers (pure)."""
    a, b = res["from"], res["to"]
    a = date.fromisoformat(a) if a else None
    b = date.fromisoformat(b) if b else None
    out = {}
    for lang in ("zh", "en"):
        scope = _scope_text(q, a, b, lang)
        name = NAMES[lang][q["metric"]]
        if q["metric"] == "fail_categories":
            cats = res.get("categories") or []
            if not cats:
                out[lang] = f"{scope}：沒有 FAIL。" if lang == "zh" else f"{scope}: no FAIL."
            else:
                top = "、".join(f"{c['name_zh'] or c['category']} {c['runs']} 次" for c in cats[:3]) if lang == "zh" else \
                    ", ".join(f"{c['name_en'] or c['category']} {c['runs']}" for c in cats[:3])
                out[lang] = f"{scope}：FAIL 共 {sum(c['runs'] for c in cats)} 次，最多的是 {top}。" if lang == "zh" else \
                    f"{scope}: {sum(c['runs'] for c in cats)} FAIL runs; most common: {top}."
            continue
        if res.get("rows") is not None:
            rows = res["rows"]
            if not rows:
                out[lang] = f"{scope}：沒有資料。" if lang == "zh" else f"{scope}: no data."
                continue
            best = max(rows, key=lambda r: r["value"] if r["value"] is not None else float("-inf"))
            out[lang] = (f"{scope}的{name}分 {len(rows)} 組，最高是 {best['key']}（{_fmt(q['metric'], best['value'])}）。" if lang == "zh" else
                         f"{scope}: {name} in {len(rows)} groups; highest {best['key']} ({_fmt(q['metric'], best['value'])}).")
            continue
        v = res.get("value")
        units = res.get("units")
        text = (f"{scope}：{name} {_fmt(q['metric'], v)}" if lang == "zh" else f"{scope}: {name} {_fmt(q['metric'], v)}")
        if q["metric"] not in ("units", "work_orders") and units is not None:
            text += f"（{units:,} 台）" if lang == "zh" else f" ({units:,} units)"
        if res.get("previous") is not None:
            pv, span = res["previous"], f"{res['previous_from']} ~ {res['previous_to']}"
            text += (f"；前一期（{span}）{_fmt(q['metric'], pv)}" if lang == "zh" else f"; the period before ({span}) {_fmt(q['metric'], pv)}")
        out[lang] = text + ("。" if lang == "zh" else ".")
    return out


def run(q: dict, now: datetime) -> dict[str, Any]:
    a, b = window(q, now.date())
    wo, pm = q.get("work_order"), q.get("product")
    res: dict[str, Any] = {"from": a.isoformat() if a else None, "to": b.isoformat() if b else None}
    if q["metric"] == "fail_categories":
        res["categories"] = _categories(a, b, wo, pm)
    elif q["group_by"] != "none":
        res["rows"] = _grouped(q["metric"], q["group_by"], a, b, wo, pm)
    else:
        res["value"] = _scalar(q["metric"], a, b, wo, pm)
        res["units"] = analytics.summary(_win(a, b), wo, pm)["total"]
        if q["compare_previous"]:
            pa, pb = previous(q, a, b)
            res["previous"] = _scalar(q["metric"], pa, pb, wo, pm) if pa else None
            res["previous_from"], res["previous_to"] = (pa.isoformat(), pb.isoformat()) if pa else (None, None)
    res["answer"] = answer(q, res)
    return res


def ask(question: str, lang: str, now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or local_now(config.LOCAL_TZ)
    q = parse(question, lang, now)
    if q["metric"] == "unsupported":
        return {"query": q, "supported": False}
    return {"query": q, "supported": True, **run(q, now)}
