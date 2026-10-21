#!/usr/bin/env python3
"""Unit tests for the work-order status rules (services/wo_rules.py: pure, no database).

    python3 tests/unit_wo_rules.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dockerup-essential" / "api" / "services"))
import wo_rules as R  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail and not cond else ""))


S = {"yield_good": 98.0, "yield_warn": 95.0, "work_start": "07:00", "work_end": "19:00", "active_minutes": 120,
     "done_idle_hours": 24, "fail_streak": 3, "tp_drop_pct": 20, "behind_pct": 20}
NOW = datetime(2026, 10, 21, 14, 0)


def facts(**kw):
    base = {"work_order": "5101-261021001", "product_model": "TN-86_PICO-IMX7", "total": 300, "passed": 300, "failed": 0,
            "first_test": NOW - timedelta(hours=6), "last_test": NOW - timedelta(minutes=10), "uph": 50.0,
            "p50_24g": 18.0, "p50_5g": 90.0, "hist_p50_24g": 18.5, "hist_p50_5g": 89.0, "max_fail_streak": 0,
            "planned": None, "recent_units": 52, "uph_target": 54.1, "uph_target_source": "history"}
    base.update(kw)
    return base


def codes(st):
    return [f["code"] for f in st["flags"]]


# ── States ──
check("a test 10 minutes ago → in progress", R.evaluate(facts(), S, NOW)["state"] == "active")
check("last test 5 hours ago → paused", R.evaluate(facts(last_test=NOW - timedelta(hours=5)), S, NOW)["state"] == "paused")
check("no test for 25 hours → done", R.evaluate(facts(last_test=NOW - timedelta(hours=25)), S, NOW)["state"] == "done")
check("plan reached and idle 3 h → done",
      R.evaluate(facts(planned=300, last_test=NOW - timedelta(hours=3)), S, NOW)["state"] == "done")
check("plan reached but still testing → in progress", R.evaluate(facts(planned=300), S, NOW)["state"] == "active")
check("a clock slightly behind the station (test in the future) → in progress",
      R.evaluate(facts(last_test=NOW + timedelta(minutes=3)), S, NOW)["state"] == "active")
check("no units → no data", R.evaluate(facts(total=0, passed=0), S, NOW)["state"] == "no_data")

# ── Yield ──
st = R.evaluate(facts(passed=284, failed=16), S, NOW)       # 94.67 %
check("yield 94.67 % → red with yield_bad", st["light"] == "bad" and codes(st)[0] == "yield_bad", st)
st = R.evaluate(facts(passed=291, failed=9), S, NOW)        # 97.0 %
check("yield 97 % → amber with yield_warn", st["light"] == "warn" and codes(st) == ["yield_warn"], st)
check("yield 98 % exactly → green", R.evaluate(facts(passed=294, failed=6), S, NOW)["light"] == "good")
st = R.evaluate(facts(total=8, passed=6, failed=2), S, NOW)
check("8 units at 75 % → yield not judged (small sample, stays green)", st["light"] == "good" and codes(st) == ["small_sample"], st)
st = R.evaluate(facts(passed=291, failed=9), {**S, "yield_good": 99.2, "yield_warn": 98.5}, NOW)
check("thresholds follow the settings (97 % under 98.5 → red)", st["light"] == "bad", st)

# ── FAIL streak ──
check("3 units failing in a row → red", R.evaluate(facts(max_fail_streak=3), S, NOW)["light"] == "bad")
check("2 in a row → no flag", "fail_streak" not in codes(R.evaluate(facts(max_fail_streak=2), S, NOW)))

# ── Throughput vs the product's usual ──
st = R.evaluate(facts(p50_24g=13.8, hist_p50_24g=18.5), S, NOW)
check("2.4G median 13.8 vs usual 18.5 (−25 %) → amber tp_low_24g",
      "tp_low_24g" in codes(st) and st["flags"][0]["values"]["pct"] == 25, st["flags"])
check("−16 % → no flag", "tp_low_24g" not in codes(R.evaluate(facts(p50_24g=15.5, hist_p50_24g=18.5), S, NOW)))
check("fewer than 30 units → throughput not compared",
      "tp_low_24g" not in codes(R.evaluate(facts(total=25, passed=25, p50_24g=10.0), S, NOW)))
check("no usual value (product has < 2 other work orders) → no flag",
      "tp_low_5g" not in codes(R.evaluate(facts(p50_5g=40.0, hist_p50_5g=None), S, NOW)))

# ── Hourly target ──
check("in progress, 10 units in the last hour vs 54.1 → behind_target",
      "behind_target" in codes(R.evaluate(facts(recent_units=10), S, NOW)))
check("44 units (−19 %) → on target", "behind_target" not in codes(R.evaluate(facts(recent_units=44), S, NOW)))
check("outside working hours → not checked",
      "behind_target" not in codes(R.evaluate(facts(recent_units=10), S, datetime(2026, 10, 21, 21, 0))))
check("started 30 minutes ago → not checked yet",
      "behind_target" not in codes(R.evaluate(facts(recent_units=10, first_test=NOW - timedelta(minutes=30)), S, NOW)))
check("paused work order → not checked",
      "behind_target" not in codes(R.evaluate(facts(recent_units=0, last_test=NOW - timedelta(hours=3)), S, NOW)))
check("no target → not checked", "behind_target" not in codes(R.evaluate(facts(recent_units=0, uph_target=None), S, NOW)))

# ── Plan ──
st = R.evaluate(facts(planned=250), S, NOW)
check("300 tested of 250 planned → over_plan (info, still green)",
      codes(st) == ["over_plan"] and st["light"] == "good" and st["achieved_pct"] == 120.0, st)
check("achievement 300 / 400 = 75 %", R.evaluate(facts(planned=400), S, NOW)["achieved_pct"] == 75.0)

# ── Flag order and light ──
st = R.evaluate(facts(passed=291, failed=9, max_fail_streak=4, p50_5g=60.0), S, NOW)
check("flags most serious first (fail_streak before yield_warn before tp_low_5g)",
      codes(st) == ["fail_streak", "yield_warn", "tp_low_5g"], codes(st))

# ── Note facts / hash ──
a = R.note_facts(R.evaluate(facts(), S, NOW))[1]
b = R.note_facts(R.evaluate(facts(), S, NOW + timedelta(minutes=30)))[1]
check("note hash does not change with the clock while the state is the same", a == b)
c = R.note_facts(R.evaluate(facts(last_test=NOW - timedelta(hours=5)), S, NOW))[1]
check("note hash changes when the state changes (active → paused)", a != c)
d = R.note_facts(R.evaluate(facts(), {**S, "yield_good": 99.0}, NOW))[1]
check("note hash changes when the thresholds change", a != d)

# ── Rule notes ──
st = R.evaluate(facts(passed=284, failed=16, last_test=NOW - timedelta(hours=30)), S, NOW)
zh, en = R.rule_note(st, "zh"), R.rule_note(st, "en")
check("rule note (zh) names state, units, yield and the flag", zh.startswith("已完成") and "300" in zh and "94.67%" in zh and "低於 95% 門檻" in zh, zh)
check("rule note (en)", en.startswith("Done: 300 units tested, yield 94.67%") and "below the 95% threshold" in en, en)
check("rule note writes the yield once", zh.count("94.67") == 1 and en.count("94.67") == 1, zh)
st = R.evaluate(facts(planned=400), S, NOW)
check("rule note mentions plan progress", "達成計畫 75%" in R.rule_note(st, "zh"), R.rule_note(st, "zh"))
check("rule note: 100.0 % is written 100 %", "良率 100%" in R.rule_note(R.evaluate(facts(), S, NOW), "zh"))

# ── Number check (LLM sentences) ──
st = R.evaluate(facts(passed=297, failed=3, total=300), S, NOW)    # 99.0 %
f, _ = R.note_facts(st)
ok = lambda text: R.numbers_ok(text, f, ("5101-261021001", "TN-86_PICO-IMX7"))[0]   # noqa: E731
check("accepts the fact numbers", ok("進行中,已測 300 台,良率 99%,3 台 FAIL。"))
check("accepts 2.4G / 5G band names", ok("2.4G 與 5G 吞吐量正常,已測 300 台。"))
check("accepts thousands separators", R.numbers_ok("7,020 units", {"total": 7020})[0])
check("accepts a value rounded to one decimal", R.numbers_ok("yield 99.2%", {"yield_pct": 99.21})[0])
check("rejects a computed fail rate (1%)", not ok("良率 99%,不良率 1.0%"))
check("rejects an invented threshold", not ok("yield 99% is below the 99.5% target"))
check("rejects an invented date", not ok("Done on 10/27 with 300 units."))
check("product number is allowed (TN-86)", ok("TN-86 已測 300 台"))
check("the size of a negative change is allowed (down 3.7 for −3.7)", R.numbers_ok("units per hour down 3.7", {"diff": -3.7})[0])

# ── Clock ──
n = R.local_now("Asia/Taipei")
check("local clock is naive and close to UTC+8", n.tzinfo is None and abs((n - datetime.utcnow()).total_seconds() - 8 * 3600) < 120)

passed = sum(results)
print(f"\n{passed}/{len(results)} rule checks passed")
sys.exit(0 if passed == len(results) else 1)
