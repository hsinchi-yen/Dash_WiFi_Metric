"""Work-order status rules — pure functions (no database), unit-tested on the host.

evaluate(facts, settings, now) → status: state, light, flags, achievement, hourly rate vs target.
note_facts(status)             → the numbers a one-line note may use (and their hash)
rule_note(status, lang)        → the note written from the rules alone (LLM offline / fallback)
numbers_ok(text, facts)        → every number in an LLM sentence exists in the facts (Q26)

States: active (a test within active_minutes), paused, done (no test for done_idle_hours, or the
planned quantity reached and no longer testing), no_data. Light: bad / warn / good / none.
"""
import hashlib
import json
import re
from datetime import datetime, time as dtime, timedelta
from typing import Any, Optional

MIN_UNITS_YIELD = 10      # fewer units → the yield is not judged yet
MIN_UNITS_TP = 30         # fewer units → throughput is not compared with the product's history

LEVEL = {"yield_bad": "bad", "fail_streak": "bad", "yield_warn": "warn", "tp_low_24g": "warn", "tp_low_5g": "warn",
         "behind_target": "warn", "over_plan": "info", "plan_reached": "info", "small_sample": "info"}
ORDER = list(LEVEL)        # most important first


def _hm(text: str) -> dtime:
    h, m = text.split(":")
    return dtime(int(h), int(m))


def _r(value, digits=1):
    return None if value is None else round(float(value), digits)


def evaluate(f: dict[str, Any], s: dict[str, Any], now: datetime) -> dict[str, Any]:
    total = int(f.get("total") or 0)
    status: dict[str, Any] = {"work_order": f.get("work_order"), "product_model": f.get("product_model"),
                              "total": total, "flags": []}
    if not total:
        status.update(state="no_data", light="none")
        return status

    last = f["last_test"]
    idle_min = (now - last).total_seconds() / 60
    planned = f.get("planned")
    reached = bool(planned) and total >= planned
    if idle_min <= s["active_minutes"]:
        state = "active"
    elif reached or idle_min >= s["done_idle_hours"] * 60:
        state = "done"
    else:
        state = "paused"

    flags = []

    def flag(code, **values):
        flags.append({"code": code, "level": LEVEL[code], "values": values})

    yield_pct = _r(f["passed"] * 100.0 / total, 2)
    if total >= MIN_UNITS_YIELD:
        if yield_pct < s["yield_warn"]:
            flag("yield_bad", yield_pct=yield_pct, threshold=s["yield_warn"])
        elif yield_pct < s["yield_good"]:
            flag("yield_warn", yield_pct=yield_pct, threshold=s["yield_good"])
    else:
        flag("small_sample", n=total, min=MIN_UNITS_YIELD)

    streak = int(f.get("max_fail_streak") or 0)
    if streak >= s["fail_streak"]:
        flag("fail_streak", n=streak)

    if total >= MIN_UNITS_TP:
        for band in ("24g", "5g"):
            p50, hist = f.get(f"p50_{band}"), f.get(f"hist_p50_{band}")
            if p50 is not None and hist and p50 < hist * (1 - s["tp_drop_pct"] / 100):
                flag(f"tp_low_{band}", p50=_r(p50), hist=_r(hist), pct=round((1 - p50 / hist) * 100))

    target = f.get("uph_target")
    if state == "active" and target and f.get("first_test") and now - f["first_test"] >= timedelta(hours=1):
        if _hm(s["work_start"]) <= now.time() <= _hm(s["work_end"]):
            recent = int(f.get("recent_units") or 0)
            if recent < target * (1 - s["behind_pct"] / 100):
                flag("behind_target", recent=recent, target=_r(target))

    if planned:
        if total > planned:
            flag("over_plan", n=total - planned, planned=planned)
        elif reached:
            flag("plan_reached", planned=planned)

    flags.sort(key=lambda x: ORDER.index(x["code"]))
    levels = {x["level"] for x in flags}
    light = "bad" if "bad" in levels else "warn" if "warn" in levels else "good"
    status.update(
        state=state, light=light, flags=flags,
        passed=int(f["passed"]), failed=int(f["failed"]), yield_pct=yield_pct,
        planned=planned, achieved_pct=_r(total * 100.0 / planned) if planned else None,
        uph=_r(f.get("uph")), uph_target=_r(target), uph_target_source=f.get("uph_target_source"),
        max_fail_streak=streak, p50_24g=_r(f.get("p50_24g")), p50_5g=_r(f.get("p50_5g")),
        hist_p50_24g=_r(f.get("hist_p50_24g")), hist_p50_5g=_r(f.get("hist_p50_5g")),
        first_test=f.get("first_test"), last_test=last,
        thresholds={"yield_good": s["yield_good"], "yield_warn": s["yield_warn"]},
    )
    return status


# ─── One-line note ───────────────────────────────────────────────────────────

NOTE_VERSION = 3          # bump when the note wording rules change: every note is rewritten once
NOTE_KEYS = ("state", "light", "total", "passed", "failed", "yield_pct", "planned", "achieved_pct",
             "uph", "uph_target", "max_fail_streak", "p50_24g", "p50_5g", "hist_p50_24g", "hist_p50_5g", "thresholds")


def _clean(value):
    """100.0 → 100 (so a note says "100%", not "100.0%"), recursively."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def note_facts(status: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """What a note is written from (no clock-derived values, so it changes only with the data,
    the state or the settings) and its hash."""
    facts = _clean({k: status.get(k) for k in NOTE_KEYS})
    facts["flags"] = _clean([{"code": x["code"], **x["values"]} for x in status.get("flags", [])])
    digest = hashlib.sha1(json.dumps({"v": NOTE_VERSION, "wo": status.get("work_order"), "pm": status.get("product_model"), **facts},
                                     sort_keys=True, default=str).encode()).hexdigest()
    return facts, digest


STATE_TEXT = {"zh": {"active": "進行中", "paused": "暫停中", "done": "已完成", "no_data": "沒有資料"},
              "en": {"active": "In progress", "paused": "Paused", "done": "Done", "no_data": "No data"}}

FLAG_TEXT = {
    "zh": {
        "yield_bad": "低於 {threshold}% 門檻",
        "yield_warn": "未達 {threshold}% 目標",
        "fail_streak": "曾連續 {n} 台 FAIL",
        "tp_low_24g": "2.4G 吞吐量中位數 {p50} Mbps，比同產品平常低 {pct}%",
        "tp_low_5g": "5G 吞吐量中位數 {p50} Mbps，比同產品平常低 {pct}%",
        "behind_target": "近一小時 {recent} 台，低於每小時目標 {target} 台",
        "over_plan": "已超過計畫數 {n} 台",
        "plan_reached": "已達計畫數 {planned} 台",
        "small_sample": "不到 {min} 台，良率暫不判斷",
    },
    "en": {
        "yield_bad": "below the {threshold}% threshold",
        "yield_warn": "under the {threshold}% target",
        "fail_streak": "{n} units failed in a row",
        "tp_low_24g": "2.4G median throughput {p50} Mbps is {pct}% below this product's usual",
        "tp_low_5g": "5G median throughput {p50} Mbps is {pct}% below this product's usual",
        "behind_target": "{recent} units in the last hour, under the hourly target of {target}",
        "over_plan": "{n} units over the planned quantity",
        "plan_reached": "planned quantity of {planned} reached",
        "small_sample": "fewer than {min} units, yield not judged yet",
    },
}


def flag_text(flag: dict[str, Any], lang: str) -> str:
    return FLAG_TEXT[lang][flag["code"]].format(**_clean(flag["values"]))


def _fmt(v):
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return f"{v:,}" if isinstance(v, int) else str(v)


def rule_note(status: dict[str, Any], lang: str) -> str:
    state = STATE_TEXT[lang][status.get("state", "no_data")]
    if status.get("state") == "no_data":
        return state + ("。" if lang == "zh" else ".")
    main = [x for x in status["flags"] if x["level"] in ("bad", "warn")]
    info = [x for x in status["flags"] if x["level"] == "info"]
    if lang == "zh":
        base = f"{state}：已測 {_fmt(status['total'])} 台，良率 {_fmt(status['yield_pct'])}%"
        if status.get("achieved_pct") is not None and not info:
            base += f"，達成計畫 {_fmt(status['achieved_pct'])}%"
        tail = "；".join(flag_text(x, "zh") for x in (main or info)[:2]) or "正常"
        return f"{base}；{tail}。"
    base = f"{state}: {_fmt(status['total'])} units tested, yield {_fmt(status['yield_pct'])}%"
    if status.get("achieved_pct") is not None and not info:
        base += f", {_fmt(status['achieved_pct'])}% of plan"
    tail = "; ".join(flag_text(x, "en") for x in (main or info)[:2]) or "normal"
    return f"{base}; {tail}."


# ─── Number check for LLM sentences (Q26: numbers only from the facts) ───────

NUM_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
ALWAYS = {2.4, 5.0}                # the band names "2.4G" and "5G"; anything else must be a fact


def _numbers(value, out: set):
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        out.add(round(float(value), 2))
        out.add(round(abs(float(value)), 2))    # "down 3.7" for a change of −3.7 (the sign is in the words)
    elif isinstance(value, str):
        for m in NUM_RE.findall(value):
            try:
                out.add(round(float(m.replace(",", "")), 2))
            except ValueError:
                pass
    elif isinstance(value, dict):
        for v in value.values():
            _numbers(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _numbers(v, out)


def numbers_ok(text: str, facts: dict[str, Any], extra: tuple = ()) -> tuple[bool, list[str]]:
    """True when every number in `text` is a fact value (or that value rounded to 0–1 decimals)."""
    allowed: set = set(ALWAYS)
    _numbers(facts, allowed)
    _numbers(list(extra), allowed)
    rounded = set(allowed)
    for a in allowed:
        rounded.update({round(a), round(a, 1)})
    bad = []
    for token in NUM_RE.findall(text):
        try:
            v = round(float(token.replace(",", "")), 2)
        except ValueError:
            continue
        if v not in rounded:
            bad.append(token)
    return not bad, bad


def local_now(tz_name: str) -> datetime:
    """Naive local time on the test stations' clock (test_time has no zone)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)
    except Exception:   # no tz database: Taiwan has no DST
        from datetime import timezone
        return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


def parse_dt(value) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
