#!/usr/bin/env python3
"""Unit tests for Ask the dashboard's checks (services/ask_dashboard.py: normalise, window, previous, answer) and the
report period bounds (services/period_reports.bounds). No LLM, no database queries — but the modules import the API's
database layer, so this runs inside the API image:

    docker run --rm --network host -v "$PWD/dockerup-essential/api:/app:ro" -v "$PWD/tests:/tests:ro" \\
        -v "$PWD/dockerup-essential/log_parser.py:/app/log_parser.py:ro" \\
        -e DATABASE_URL=postgresql://qc:qcpass@127.0.0.1:5432/wifitest -w /app wifitest-api:0.2.0 \\
        python3 /tests/unit_ask_dashboard.py
"""
import sys
from datetime import date, datetime

sys.path.insert(0, "/app")
from services import ask_dashboard as A  # noqa: E402
from services import period_reports as P  # noqa: E402

results = []
PRODUCTS = ["TN-86_PICO-IMX7", "TN-73_PICO-IMX6"]
WOS = ["5101-260820009", "5101-260102014"]
LATEST = {"TN-250_EDM-G-8MP": "5101-260820009", "TN-86_PICO-IMX7": "5101-260731002"}   # each product's newest work order
NOW = datetime(2026, 10, 21, 13, 0)


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail and not cond else ""))


def norm(**q):
    base = {"metric": "units", "period": "all", "group_by": "none", "compare_previous": False, "product": None, "work_order": None}
    return A.normalise({**base, **q}, PRODUCTS, WOS, NOW, LATEST)


check("an unknown metric becomes unsupported", norm(metric="sn_lookup")["metric"] == "unsupported")
check("an unknown product is dropped (never widened to a guess)", norm(product="TN-99_FAKE")["product"] is None)
check("an unknown work order is dropped", norm(work_order="9999-999999999")["work_order"] is None)
check("'latest' → the newest work order", norm(work_order="latest")["work_order"] == WOS[0])
check("'latest' with a product → that product's newest work order, not the newest overall (最近 TN410 …)",
      norm(work_order="latest", product="TN-86_PICO-IMX7")["work_order"] == "5101-260731002")
check("'latest' with a product that has no work order → the newest overall",
      norm(work_order="latest", product="TN-73_PICO-IMX6")["work_order"] == WOS[0])
check("output per hour (台/時) is a metric", norm(metric="uph")["metric"] == "uph" and "uph" in A.METRIC_SQL)
check("last_n_days without n → 7 days", norm(period="last_n_days")["n"] == 7)
check("month without year / month → this month", norm(period="month")["period"] == "this_month")
check("custom without dates → all data", norm(period="custom")["period"] == "all")
check("a bad date is dropped", norm(period="custom", date_from="2026-13-40")["date_from"] is None)
check("n out of range dropped", norm(period="last_n_days", n=999)["n"] == 7)
check("a split that gives one group is dropped (work order filter + by work order)", norm(work_order=WOS[0], group_by="work_order")["group_by"] == "none")
check("…one product + by product", norm(product=PRODUCTS[0], group_by="product")["group_by"] == "none")
check("…last month + by month", norm(period="last_month", group_by="month")["group_by"] == "none")
check("a real split stays (this year by month)", norm(period="this_year", group_by="month")["group_by"] == "month")
check("compare with the period before → no split", norm(period="this_year", group_by="month", compare_previous=True)["group_by"] == "none")

today = NOW.date()
check("window: today", A.window(norm(period="today"), today) == (today, today))
check("window: last week = Mon 10/12 – Sun 10/18", A.window(norm(period="last_week"), today) == (date(2026, 10, 12), date(2026, 10, 18)))
check("window: last month = September", A.window(norm(period="last_month"), today) == (date(2026, 9, 1), date(2026, 9, 30)))
check("window: month 2026-02 (28 days)", A.window(norm(period="month", year=2026, month=2), today) == (date(2026, 2, 1), date(2026, 2, 28)))
check("window: last 30 days ends today", A.window(norm(period="last_n_days", n=30), today) == (date(2026, 9, 22), today))
check("window: all data → no bounds", A.window(norm(), today) == (None, None))
q = norm(period="month", year=2026, month=9, compare_previous=True)
check("previous of a month = the calendar month before (August, 31 days)", A.previous(q, date(2026, 9, 1), date(2026, 9, 30)) == (date(2026, 8, 1), date(2026, 8, 31)))
q = norm(period="this_year")
check("previous of a year = last year", A.previous(q, date(2026, 1, 1), date(2026, 12, 31)) == (date(2025, 1, 1), date(2025, 12, 31)))
q = norm(period="last_n_days", n=7)
check("previous of 7 days = the 7 days before", A.previous(q, date(2026, 10, 15), date(2026, 10, 21)) == (date(2026, 10, 8), date(2026, 10, 14)))

ans = A.answer(norm(metric="yield", product=PRODUCTS[0]), {"from": "2026-09-01", "to": "2026-09-30", "value": 99.6, "units": 4734,
                                                          "previous": 99.9, "previous_from": "2026-08-01", "previous_to": "2026-08-31"})
check("answer (zh): scope, metric, value, units, the period before", "2026-09-01 ~ 2026-09-30" in ans["zh"] and "TN-86_PICO-IMX7" in ans["zh"]
      and "良率 99.60%" in ans["zh"] and "4,734 台" in ans["zh"] and "前一期（2026-08-01 ~ 2026-08-31）99.90%" in ans["zh"], ans["zh"])
check("answer (en) has no Chinese", not any("一" <= ch <= "鿿" for ch in ans["en"]) and "yield 99.60%" in ans["en"], ans["en"])
ans = A.answer(norm(metric="units"), {"from": None, "to": None, "value": 64181, "units": 63881})
check("answer of units does not repeat the units in brackets", "（" not in ans["zh"] and "64,181" in ans["zh"], ans["zh"])
ans = A.answer(norm(metric="uph", product="TN-86_PICO-IMX7", work_order="latest"),
               {"from": None, "to": None, "value": 52.0, "units": 7020, "hours": 135})
check("answer of output per hour: 台/時 with units and hours (zh)", "每小時產出 52.0 台/時" in ans["zh"] and "7,020 台、135 個有測試的小時" in ans["zh"]
      and "工單 5101-260731002" in ans["zh"], ans["zh"])
check("…and in English, no Chinese", "output per hour 52.0 units/h" in ans["en"] and "135 hours with testing" in ans["en"]
      and not any("一" <= ch <= "鿿" for ch in ans["en"]), ans["en"])
ans = A.answer(norm(metric="fail_categories"), {"from": "2026-09-22", "to": "2026-10-21", "categories": []})
check("no FAIL → says so", "沒有 FAIL" in ans["zh"] and "no FAIL" in ans["en"])

check("report bounds: a week runs Monday–Sunday", P.bounds("week", date(2026, 10, 21)) == (date(2026, 10, 19), date(2026, 10, 25)))
check("report bounds: a month", P.bounds("month", date(2026, 2, 14)) == (date(2026, 2, 1), date(2026, 2, 28)))
check("report: the period before a week / month", P.previous_start("week", date(2026, 10, 19)) == date(2026, 10, 12)
      and P.previous_start("month", date(2026, 3, 1)) == date(2026, 2, 1))

print(f"\n{sum(results)}/{len(results)} ask / report checks passed")
sys.exit(0 if all(results) else 1)
