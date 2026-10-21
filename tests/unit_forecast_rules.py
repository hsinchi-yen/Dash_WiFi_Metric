#!/usr/bin/env python3
"""Unit tests for the forecast arithmetic (services/forecast_rules.py: pure, no database).

    python3 tests/unit_forecast_rules.py
"""
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dockerup-essential" / "api" / "services"))
import forecast_rules as F  # noqa: E402

results = []
WD = [1, 2, 3, 4, 5]
WS, WE = "07:00", "19:00"


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail and not cond else ""))


wed = datetime(2026, 10, 21, 13, 0)          # Wednesday 13:00
check("day fraction left: Wednesday 13:00 → 6 of 12 h", F.day_fraction_left(wed, WS, WE, WD) == 0.5)
check("before work → the whole day", F.day_fraction_left(datetime(2026, 10, 21, 6, 0), WS, WE, WD) == 1.0)
check("after work → nothing", F.day_fraction_left(datetime(2026, 10, 21, 19, 30), WS, WE, WD) == 0.0)
check("Saturday (not a working day) → nothing", F.day_fraction_left(datetime(2026, 10, 24, 10, 0), WS, WE, WD) == 0.0)
check("Saturday counts when it is a working day", F.day_fraction_left(datetime(2026, 10, 24, 13, 0), WS, WE, WD + [6]) == 0.5)

check("working days from Wed 13:00 to Fri: 0.5 + 2", F.working_days_between(wed, date(2026, 10, 23), WS, WE, WD) == 2.5)
check("…to Sunday: the weekend adds nothing", F.working_days_between(wed, date(2026, 10, 25), WS, WE, WD) == 2.5)
check("…to month end (Sat 31): Wed ½ + Thu Fri + Mon–Fri", F.working_days_between(wed, date(2026, 10, 31), WS, WE, WD) == 7.5)
check("a past day → 0", F.working_days_between(wed, date(2026, 10, 20), WS, WE, WD) == 0.0)

check("working share Wed 13:00 → Thu 13:00 = 1 day", F.working_share(wed, datetime(2026, 10, 22, 13, 0), WS, WE, WD) == 1.0)
check("working share across a weekend: Fri 13:00 → Mon 13:00 = 1 day", F.working_share(datetime(2026, 10, 23, 13, 0), datetime(2026, 10, 26, 13, 0), WS, WE, WD) == 1.0)
check("working share overnight is 0", F.working_share(datetime(2026, 10, 21, 19, 30), datetime(2026, 10, 22, 6, 30), WS, WE, WD) == 0.0)

e = F.eta(wed, 120, 240, WS, WE, WD)
check("ETA: 120 units at 240 / day from Wed 13:00 → Wed 19:00 (exactly the rest of today)", e["at"] == datetime(2026, 10, 21, 19, 0) and e["working_days"] == 0.5, e)
e = F.eta(wed, 240, 240, WS, WE, WD)
check("ETA: 240 units → Thu 13:00 (half today, half tomorrow)", e["at"] == datetime(2026, 10, 22, 13, 0), e)
e = F.eta(datetime(2026, 10, 23, 13, 0), 240, 240, WS, WE, WD)
check("ETA across a weekend: Fri 13:00 + 1 day → Mon 13:00", e["at"] == datetime(2026, 10, 26, 13, 0), e)
e = F.eta(datetime(2026, 10, 21, 20, 0), 60, 240, WS, WE, WD)
check("ETA after hours starts the next morning: Thu 10:00", e["at"] == datetime(2026, 10, 22, 10, 0), e)
check("ETA with nothing left → now", F.eta(wed, 0, 240, WS, WE, WD)["at"] == wed)
check("ETA without a pace → None", F.eta(wed, 100, 0, WS, WE, WD) is None and F.eta(wed, 100, None, WS, WE, WD) is None)
check("ETA without working days → None", F.eta(wed, 100, 50, WS, WE, []) is None)
check("ETA beyond a year → None", F.eta(wed, 10 ** 7, 1, WS, WE, WD) is None)

hours = [(datetime(2026, 10, 21, h), n) for h, n in ((8, 20), (9, 30), (10, 40), (11, 0), (12, 10), (13, 25))]
uph, used = F.recent_uph(hours, datetime(2026, 10, 21, 13, 20), n=3)
check("recent pace: the last 3 finished active hours (9, 10, 12 — not the empty 11 nor the running 13:00) → 26.7 / h", (uph, used) == (26.7, 3), (uph, used))
uph, used = F.recent_uph(hours, datetime(2026, 10, 22, 9, 0), n=6)
check("recent pace when the day is over: the five active hours (the zero hour skipped)", (uph, used) == (25.0, 5), (uph, used))
check("no hours → no pace", F.recent_uph([], wed) == (None, 0))

lw = F.last_working_days(date(2026, 10, 21), 3, WD)
check("last 3 working days before Wed 10/21: 10/20, 10/19, 10/16", lw == [date(2026, 10, 20), date(2026, 10, 19), date(2026, 10, 16)], lw)

print(f"\n{sum(results)}/{len(results)} forecast checks passed")
sys.exit(0 if all(results) else 1)
