#!/usr/bin/env python3
"""5F-1 on the real database: stored classifications, the desktop-uploader path, the old DB Tweak delete,
and the LLM path for a log no rule recognises.

    docker run --rm --network host -v "$PWD/dockerup-essential/api:/app:ro" \\
        -v "$PWD/dockerup-essential/log_parser.py:/app/log_parser.py:ro" -v "$PWD/tests:/tests:ro" \\
        -e DATABASE_URL=postgresql://qc:qcpass@127.0.0.1:5432/wifitest -w /app wifitest-api:0.2.0 \\
        python3 /tests/check_fail_classification.py

Writes two synthetic test runs (SN 999999999901, work order 9999-261021801) through the desktop uploader's
own write_record and deletes them again; the statistics view is refreshed at the end.
"""
import os
import sys
import tempfile
import time

import psycopg2

sys.path.insert(0, "/app")
sys.path.insert(0, "/tests")
import db  # noqa: E402
import log_parser  # noqa: E402
from fail_log_samples import LOW24, OK5, PAD, bt, header, log, summary  # noqa: E402
from services import fail_classify, fail_rules, stats  # noqa: E402

results = []
SN = "999999999901"
WO = "9999-261021801"


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""), flush=True)


# ── stored classifications ──
rows = db.rows("""SELECT t.id, t.raw_log, t.band_result_24g, t.band_result_5g, t.bt_result, f.category, f.secondary, f.band, f.classifier
                  FROM test_record t JOIN fail_analysis f ON f.record_id = t.id WHERE t.result = FALSE""")
total_fail = db.rows("SELECT COUNT(*) AS n FROM test_record WHERE result = FALSE")[0]["n"]
check("every FAIL run has a classification", len(rows) == total_fail, f"{len(rows)} of {total_fail}")
diff = [r["id"] for r in rows if r["classifier"] == "rule" and
        (lambda c: (c["category"], c["secondary"], c["band"]) != (r["category"], list(r["secondary"]), r["band"]))(fail_rules.classify(r["raw_log"] or "", r))]
check("stored rule classifications = the current rules", not diff, diff[:5])
mism = []
for r in rows:
    codes = [r["category"], *r["secondary"]]
    if r["band_result_24g"] is False and not ({"WIFI_TP_LOW_24G", "LOG_INCOMPLETE"} & set(codes) or (r["band"] == "24g") or
                                              ("WIFI_NO_TP_DATA" in codes) or ("WIFI_ASSOC_FAIL" in codes)):
        mism.append((r["id"], "24g", codes))
    if r["band_result_5g"] is False and not ({"WIFI_TP_LOW_5G", "WIFI_NO_TP_DATA", "WIFI_ASSOC_FAIL", "LOG_INCOMPLETE"} & set(codes)):
        mism.append((r["id"], "5g", codes))
    if r["bt_result"] is False and not ({"BT_PING_FAIL", "BT_DEV_NOT_FOUND", "LOG_INCOMPLETE"} & set(codes)):
        mism.append((r["id"], "bt", codes))
check("every band the desktop parser marked failed has a code for it", not mism, mism[:5])
nr = db.rows("SELECT COUNT(*) AS n FROM fail_analysis WHERE retest_outcome = 'not_retested'")[0]["n"]
units = db.rows("SELECT COUNT(*) AS n FROM mv_unit WHERE result = FALSE")[0]["n"]
check("runs not retested = units still failing (their last run)", nr == units, f"{nr} vs {units}")
unknown = db.rows("SELECT COUNT(*) AS n FROM fail_analysis WHERE category = 'UNKNOWN'")[0]["n"]
check("no unclassified run on the production copy", unknown == 0, unknown)


def write(kind, text, stamp):
    """Insert one run the way the desktop uploader does (log_parser.write_record)."""
    d = tempfile.mkdtemp()
    path = os.path.join(d, "TN-98_FAILTEST", WO)
    os.makedirs(path)
    f = os.path.join(path, f"20261021_{stamp}_{SN}_001F7B000001_001F7B000002_{kind}.txt")
    with open(f, "w") as fh:
        fh.write(text)
    record = log_parser.ingest_file(f)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])     # a plain connection, as the desktop tool opens it
    try:
        return log_parser.write_record(conn, record)
    finally:
        conn.close()


def cleanup():
    with db.db() as conn:
        db.execute(conn, "DELETE FROM test_record WHERE sn = %s", (SN,))


cleanup()
try:
    # ── desktop path: FAIL, classified on the next scan ──
    rid = write("FAIL", log(LOW24, OK5, bt()), "090000")
    fail_classify.scan()
    a = db.rows("SELECT * FROM fail_analysis WHERE record_id = %s", (rid,))
    check("a FAIL run from the desktop uploader is classified on the next scan",
          a and a[0]["category"] == "WIFI_TP_LOW_24G" and a[0]["retest_outcome"] == "not_retested" and a[0]["ssid_group"] == "grpb",
          a and (a[0]["category"], a[0]["retest_outcome"]))
    # retest PASS later
    pid = write("PASS", log(band_ok := OK5.replace("5G", "2.4G").replace("PD-RF-QC-5-STA-B", "PD-RF-QC-2-4-STA-B"), OK5, bt(), wifi="PASS"), "100000")
    fail_classify.scan()
    a = db.rows("SELECT retest_outcome, run_seq FROM fail_analysis WHERE record_id = %s", (rid,))[0]
    check("a later PASS of the same SN → passed on retest (FP)", a["retest_outcome"] == "pass_on_retest" and a["run_seq"] == "FP", a)
    # old DB Tweak deletes the FAIL row
    with db.db() as conn:
        db.execute(conn, "DELETE FROM test_record WHERE id = %s", (rid,))
    out = fail_classify.scan()
    check("a deleted test run (old DB Tweak) loses its classification on the next scan",
          not db.rows("SELECT 1 FROM fail_analysis WHERE record_id = %s", (rid,)) and out["removed"] >= 1, out)

    # ── LLM path: no rule recognises this log ──
    odd = header() + PAD + "\nERROR: RF calibration data missing in OTP (cal_status=0x0)\nERROR: wlan firmware refused to start radio\n" + PAD + "\n" + summary("FAIL", None)
    uid = write("FAIL", odd, "110000")
    fail_classify.scan()
    a = db.rows("SELECT category, classifier FROM fail_analysis WHERE record_id = %s", (uid,))[0]
    check("a log no rule recognises → UNKNOWN (rule)", a["category"] == "UNKNOWN" and a["classifier"] == "rule", a)
    end = time.time() + 240
    while time.time() < end:
        fail_classify.llm_once()
        a = db.rows("SELECT category, classifier, evidence, llm_tried_at, confidence FROM fail_analysis WHERE record_id = %s", (uid,))[0]
        if a["llm_tried_at"]:
            break
        time.sleep(3)
    check("the LLM looked at it", a["llm_tried_at"] is not None, a)
    lines = odd.splitlines()
    ok_evidence = all(1 <= e["n"] <= len(lines) and lines[e["n"] - 1].strip() == e["text"] for e in (a["evidence"] or []))
    check("whatever it chose, every evidence line exists in the log verbatim",
          (a["category"] == "UNKNOWN") or (a["classifier"] == "llm" and a["evidence"] and ok_evidence),
          (a["category"], a["classifier"], a["evidence"]))
    print(f"     LLM: {a['category']} ({a['classifier']}, confidence {a['confidence']}) evidence {[e['text'][:60] for e in (a['evidence'] or [])]}")
finally:
    cleanup()
    fail_classify.scan()
    stats.refresh(wait=True)
left = db.rows("SELECT COUNT(*) AS n FROM test_record WHERE sn = %s", (SN,))[0]["n"] + \
    db.rows("SELECT COUNT(*) AS n FROM fail_analysis WHERE sn = %s", (SN,))[0]["n"]
check("synthetic rows removed again", left == 0, left)

passed = sum(results)
print(f"\n{passed}/{len(results)} database checks passed")
sys.exit(0 if passed == len(results) else 1)
