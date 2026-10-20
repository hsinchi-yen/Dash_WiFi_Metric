#!/usr/bin/env python3
"""Golden-output guard for the data API.

    python tests/golden.py capture [BASE]   # record every endpoint's JSON into tests/golden/
    python tests/golden.py check   [BASE]   # compare the running API against the recorded files

BASE defaults to http://127.0.0.1:8000. The refactor (routers/services, mv_unit) must not
change a single number: `check` fails on any difference. List endpoints are compared
order-insensitively where the original ORDER BY had ties (same rows, possibly different
tie order), and exactly otherwise.
"""
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
WO = "5101-251216015"
PM = "TN-99_EDM1-IMX6P"
PM2 = "TN-86_PICO-IMX7"
NOWO = "NOWO-260420001"

# name -> (path, query); the query mirrors what wifi_dashboard.html sends.
CASES: dict[str, tuple[str, dict]] = {
    "summary_year": ("/api/dashboard/summary", {"scope": "year", "year": 2026}),
    "summary_year_pm": ("/api/dashboard/summary", {"scope": "year", "year": 2026, "product_model": PM2}),
    "summary_year_wo": ("/api/dashboard/summary", {"scope": "year", "year": 2026, "work_order": WO}),
    "summary_year_nowo": ("/api/dashboard/summary", {"scope": "year", "year": 2026, "work_order": NOWO}),
    "summary_latest": ("/api/dashboard/summary", {"scope": "latest_day"}),
    "summary_latest_pm": ("/api/dashboard/summary", {"scope": "latest_day", "product_model": PM}),
    "summary_empty_year": ("/api/dashboard/summary", {"scope": "year", "year": 2025}),
    "years": ("/api/dashboard/available-years", {"scope": "year", "year": 2026}),
    "years_pm": ("/api/dashboard/available-years", {"product_model": PM}),
    "products_year": ("/api/products", {"scope": "year", "year": 2026, "limit": 200}),
    "products_latest": ("/api/products", {"scope": "latest_day", "limit": 200}),
    "workorders_year": ("/api/workorders", {"scope": "year", "year": 2026, "limit": 200}),
    "workorders_year_pm": ("/api/workorders", {"scope": "year", "year": 2026, "limit": 200, "product_model": PM}),
    "workorders_latest": ("/api/workorders", {"scope": "latest_day", "limit": 200}),
    "fails": ("/api/fails", {"limit": 300}),
    "fails_pm": ("/api/fails", {"limit": 300, "product_model": PM2}),
    "retries": ("/api/retries", {"limit": 300}),
    "retries_pm": ("/api/retries", {"limit": 300, "product_model": PM2}),
    "trend_latest_hour": ("/api/dashboard/yield-trend", {"scope": "latest_day", "bucket": "hour"}),
    "trend_latest_hour_pm": ("/api/dashboard/yield-trend", {"scope": "latest_day", "bucket": "hour", "product_model": PM}),
    "trend_year_month": ("/api/dashboard/yield-trend", {"scope": "year", "year": 2026, "bucket": "month"}),
    "trend_year_month_wo": ("/api/dashboard/yield-trend", {"scope": "year", "year": 2026, "bucket": "month", "work_order": WO}),
    "split_year": ("/api/dashboard/pass-fail-split", {"scope": "year", "year": 2026}),
    "split_latest": ("/api/dashboard/pass-fail-split", {"scope": "latest_day"}),
    "split_year_pm": ("/api/dashboard/pass-fail-split", {"scope": "year", "year": 2026, "product_model": PM2}),
    "hourly": ("/api/dashboard/latest-workorder-hourly", {"scope": "year", "year": 2026, "lookback_days": 2}),
    "hourly_wo": ("/api/dashboard/latest-workorder-hourly", {"work_order": WO, "lookback_days": 2}),
    "hourly_pm": ("/api/dashboard/latest-workorder-hourly", {"product_model": PM, "lookback_days": 1}),
    "tp_year": ("/api/dashboard/throughput-distribution", {"scope": "year", "year": 2026}),
    "tp_latest": ("/api/dashboard/throughput-distribution", {"scope": "latest_day"}),
    "tp_year_pm": ("/api/dashboard/throughput-distribution", {"scope": "year", "year": 2026, "product_model": PM}),
    "rf_window": ("/api/dashboard/rf-window", {"scope": "year", "year": 2026}),
    "rf_window_pm": ("/api/dashboard/rf-window", {"product_model": PM}),
    "rf_window_wo": ("/api/dashboard/rf-window", {"work_order": WO}),
    "rf_trend": ("/api/dashboard/rf-throughput-trend", {"bucket": "hour"}),
    "rf_trend_pm": ("/api/dashboard/rf-throughput-trend", {"bucket": "hour", "product_model": PM}),
    "rf_pct": ("/api/dashboard/rf-percentiles", {"bucket": "hour"}),
    "rf_pct_wo": ("/api/dashboard/rf-percentiles", {"bucket": "hour", "work_order": WO}),
    "rf_std": ("/api/dashboard/rf-stddev", {"limit": 20}),
    "rf_std_pm": ("/api/dashboard/rf-stddev", {"limit": 20, "product_model": PM}),
}

# List endpoints whose original ORDER BY can tie: compare as multisets.
UNORDERED = {"fails", "fails_pm", "retries", "retries_pm", "rf_std", "rf_std_pm"}
# Fields that legitimately depend on the clock.
VOLATILE = {"current_year"}


def fetch(base: str, path: str, query: dict):
    url = f"{base}{path}?{urllib.parse.urlencode(query)}"
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def canon(obj):
    """Stable form for comparison: drop volatile fields, keep everything else verbatim."""
    if isinstance(obj, dict):
        return {k: canon(v) for k, v in obj.items() if k not in VOLATILE}
    if isinstance(obj, list):
        return [canon(v) for v in obj]
    return obj


def as_multiset(rows):
    return sorted(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows)


def capture(base: str) -> int:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name, (path, query) in CASES.items():
        status, body = fetch(base, path, query)
        if status != 200:
            print(f"!! {name}: HTTP {status}")
            return 1
        (GOLDEN_DIR / f"{name}.json").write_text(
            json.dumps({"path": path, "query": query, "body": body}, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        size = len(json.dumps(body))
        print(f"   {name:<24} {path:<45} {size:>7} bytes")
    print(f"captured {len(CASES)} cases into {GOLDEN_DIR}")
    return 0


def check(base: str) -> int:
    failures = 0
    for name, (path, query) in CASES.items():
        f = GOLDEN_DIR / f"{name}.json"
        if not f.exists():
            print(f"!! {name}: no golden file (run capture first)")
            failures += 1
            continue
        expected = canon(json.loads(f.read_text(encoding="utf-8"))["body"])
        status, body = fetch(base, path, query)
        actual = canon(body)
        if status != 200:
            print(f"FAIL {name}: HTTP {status}")
            failures += 1
            continue
        if name in UNORDERED and isinstance(expected, list):
            same = as_multiset(expected) == as_multiset(actual)
        else:
            same = expected == actual
        if same:
            print(f"ok   {name}")
        else:
            failures += 1
            print(f"FAIL {name}: {path} {query}")
            exp_s, act_s = json.dumps(expected, sort_keys=True), json.dumps(actual, sort_keys=True)
            i = next((i for i, (a, b) in enumerate(zip(exp_s, act_s)) if a != b), min(len(exp_s), len(act_s)))
            print(f"     expected …{exp_s[max(0, i-80):i+120]}…")
            print(f"     actual   …{act_s[max(0, i-80):i+120]}…")
    print(f"{len(CASES) - failures}/{len(CASES)} identical")
    return 1 if failures else 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    base = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8000"
    sys.exit(capture(base) if mode == "capture" else check(base))
