#!/usr/bin/env python3
"""The upload rules and NOWO numbers use the test stations' date (LOCAL_TZ, Asia/Taipei), not the API container's clock,
which runs on UTC: between 00:00 and 08:00 in Taiwan the container's date is still yesterday. Found on production on
2026-10-22 at 07:54: a work order dated that day was warned as "later than today".

    docker run --rm -v "$PWD/dockerup-essential/api:/app:ro" -v "$PWD/tests:/tests:ro" \\
        -v "$PWD/dockerup-essential/log_parser.py:/app/log_parser.py:ro" -w /app wifitest-api:0.2.0 python3 /tests/unit_local_date.py
"""
import sys
from datetime import datetime

sys.path.insert(0, "/app")
from services import upload, upload_rules  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail != "" else ""))


MORNING = datetime(2026, 10, 22, 7, 54)          # Taiwan; the container (UTC) reads 2026-10-21 23:54
upload_rules.local_now = upload.local_now = lambda tz: MORNING


def r6(wo):
    fields = {"work_order": wo, "wo_check": upload_rules.check_wo(wo), "product": {"status": "ok", "canonical": "TN-99_EDM1-IMX6P"},
              "planned_qty": 40, "planned_raw": "40"}
    facts = {"logs": 33, "first_test": "2026-10-22T06:07:46", "last_test": "2026-10-22T07:34:46", "units": 32}
    return [i for i in upload_rules.validate(facts, fields, {}) if i.get("code") == "R6"]


check("a work order dated today (local) is not 'later than today' at 07:54", not r6("9999-261022911"), r6("9999-261022911"))
check("a work order dated tomorrow (local) still is", any(i.get("reason") == "wo_future" for i in r6("9999-261023911")), r6("9999-261023911"))


class FakeDB:
    @staticmethod
    def fetch_all(conn, sql, params):
        return []


upload.db = FakeDB
check("NOWO numbers carry the local date at 07:54", upload.next_nowo(None) == "NOWO-261022001", upload.next_nowo(None))
print(f"\n{sum(results)}/{len(results)} local-date checks passed")
sys.exit(0 if all(results) else 1)
