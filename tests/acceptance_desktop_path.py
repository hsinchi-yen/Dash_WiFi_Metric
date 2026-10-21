#!/usr/bin/env python3
"""The old ways of putting test logs into the database still work after the upgrade, and the new dashboard analyses
what they write without anything else being done.

    docker run --rm --network host -v "$PWD/dockerup-essential/api:/app:ro" -v "$PWD/tests:/tests:ro" \\
        -v "$PWD/dockerup-essential/log_parser.py:/app/log_parser.py:ro" -v "$PWD/_remote_snapshot/<ts>/dockerup-essential:/old:ro" \\
        -e DATABASE_URL=postgresql://qc:qcpass@127.0.0.1:5432/wifitest -w /app wifitest-api:0.2.0 \\
        python3 /tests/acceptance_desktop_path.py [BASE] [--keep | --cleanup]

The writing code is the production deployment's own log_parser.py (/old), used exactly as
  1. the desktop tool's "Database Upload" (wifi_stress_log_analyzer.on_upload_to_db): scan_directory → ingest_file →
     product / work order from the form → write_record on one plain psycopg2 connection; duplicates are skipped;
  2. the batch command `python log_parser.py <folder> --dsn …` (docker-compose.yml, usage note 3);
  3. POST /api/ingest — the old API had no sign-in for it; the new one needs an administrator (checked as a dry run).
Then, without forcing anything, it waits for the running stack's own scheduler (statistics refresh, FAIL scan) and
checks every place that analyses the data. Synthetic SNs 9999999900xx, work orders 9999-261022901 (an existing
product) and 9999-261022902 (a product name the registry does not know). The rows are deleted at the end the way the
old DB Tweak deletes them (plain DELETE); --keep leaves them for screenshots, --cleanup only deletes them.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import psycopg2

sys.path.insert(0, "/app")
sys.path.insert(0, "/tests")
import config  # noqa: E402
import db  # noqa: E402
from fail_log_samples import LOW24, OK5, bt, log  # noqa: E402
from services import fail_classify, stats  # noqa: E402
from services.wo_rules import local_now  # noqa: E402

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE = (ARGS[0] if ARGS else "http://127.0.0.1:8000").rstrip("/")
KEEP, ONLY_CLEANUP = "--keep" in sys.argv, "--cleanup" in sys.argv
DSN = os.environ["DATABASE_URL"]
ADMIN = (os.environ.get("TWEAK_USER", "qc"), os.environ.get("TWEAK_PASS", "qcpass"))
WO_A, PM_A = "9999-261022901", "TN-86_PICO-IMX7"            # an existing product, as on a normal day
WO_B, PM_B = "9999-261022902", "TN-98_DESKTOP-CHECK"        # a product name typed only in the desktop tool
SN_PREFIX = "9999999900"
OLD_PARSER = "/old/log_parser.py"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail != "" else ""), flush=True)
    return cond


def call(method, path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Tweak-Token"] = token
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(), method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except ValueError:
            return e.code, None


def get(path, token=None, **kw):
    status, body = call("GET", path + ("?" + urlencode({k: v for k, v in kw.items() if v is not None}) if kw else ""), token=token)
    assert status == 200, (path, status, body)
    return body


def cleanup():
    """Delete the synthetic rows as the old DB Tweak does, then let the new tables follow (FAIL scan, statistics)."""
    with db.db() as conn:
        n = db.execute(conn, "DELETE FROM test_record WHERE sn LIKE %s", (SN_PREFIX + "%",))
        for t in ("wo_status_note", "wo_report", "wo_plan"):
            db.execute(conn, f"DELETE FROM {t} WHERE work_order IN (%s, %s)", (WO_A, WO_B))
    fail_classify.scan()
    stats.refresh(wait=True)
    left = db.rows("SELECT (SELECT COUNT(*) FROM test_record WHERE sn LIKE %s) + (SELECT COUNT(*) FROM fail_analysis WHERE sn LIKE %s) "
                   "+ (SELECT COUNT(*) FROM mv_unit WHERE sn LIKE %s) AS n", (SN_PREFIX + "%",) * 3)[0]["n"]
    return n, left


if ONLY_CLEANUP:
    n, left = cleanup()
    print(f"deleted {n} synthetic rows; left: {left}")
    sys.exit(0 if left == 0 else 1)

spec = importlib.util.spec_from_file_location("old_log_parser", OLD_PARSER)
old_parser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old_parser)
check("the writing code is production's own log_parser.py, byte for byte the one in the new image",
      os.path.exists("/app/log_parser.py") and open(OLD_PARSER, "rb").read() == open("/app/log_parser.py", "rb").read())

cleanup()
real_pass = open("/old/logs/20260203_082710_513126033667_001F7B6C11E4_001F7B6C11E5_PASS.txt", "rb").read().decode("utf-8", "replace")
now = local_now(config.LOCAL_TZ).replace(tzinfo=None, microsecond=0)
t0 = now - timedelta(minutes=150)
today = now.date().isoformat()
before_today = get("/api/overview", date_from=today, date_to=today)["summary"]["total"]


def write_file(root, product, wo, sn, minutes, result, text):
    stamp = (t0 + timedelta(minutes=minutes)).strftime("%Y%m%d_%H%M%S")
    folder = os.path.join(root, product, wo)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{stamp}_{sn}_001F7B00{sn[-4:]}_001F7B01{sn[-4:]}_{result}.txt")
    with open(path, "w") as fh:
        fh.write(text + f"\n# desktop-path check {sn} {stamp}\n")     # a unique line → its own file_hash
    return path


# ── 1. the desktop tool's Database Upload ──
desk = tempfile.mkdtemp()
pass_sns = [f"{SN_PREFIX}{i:02d}" for i in range(1, 13)]       # 14 units: the yield is judged from 10 units on
F1, F2 = f"{SN_PREFIX}13", f"{SN_PREFIX}14"
for i, sn in enumerate(pass_sns):
    write_file(desk, "anything-typed", "folder", sn, 10 + i * 8, "PASS", real_pass)
write_file(desk, "anything-typed", "folder", F1, 40, "FAIL", log(LOW24, OK5, bt()))
write_file(desk, "anything-typed", "folder", F2, 70, "FAIL", log(LOW24, OK5, bt()))
write_file(desk, "anything-typed", "folder", F2, 105, "PASS", real_pass)


def desktop_upload(log_dir, product_name, work_order):
    """wifi_stress_log_analyzer.on_upload_to_db, minus the window: duplicate policy 'skip'."""
    ok = skip = err = 0
    ids = []
    with psycopg2.connect(DSN) as conn:
        for f in old_parser.scan_directory(log_dir):
            record = old_parser.ingest_file(f)
            if not record:
                err += 1
                continue
            record["product_model"] = product_name            # GUI fields are the source of truth
            record["work_order"] = work_order
            rid = old_parser.write_record(conn, record)
            if rid:
                ok += 1
                ids.append(rid)
            else:
                skip += 1
    return ok, skip, err, ids


ok, skip, err, ids = desktop_upload(desk, PM_A, WO_A)
written_at = datetime.now(timezone.utc)
check("desktop Database Upload: 15 logs of 14 units inserted into the upgraded database", (ok, skip, err) == (15, 0, 0), (ok, skip, err))
ok2, skip2, err2, _ = desktop_upload(desk, PM_A, WO_A)
check("uploading the same folder again: all 15 skipped as duplicates, nothing written", (ok2, skip2, err2) == (0, 15, 0), (ok2, skip2, err2))

# ── 2. the batch command ──
batch = tempfile.mkdtemp()
for i, sn in enumerate((f"{SN_PREFIX}21", f"{SN_PREFIX}22")):
    write_file(batch, PM_B, WO_B, sn, 30 + i * 20, "PASS", real_pass)
out = subprocess.run([sys.executable, OLD_PARSER, batch, "--dsn", DSN], capture_output=True, text=True)
check("batch command `python log_parser.py <folder> --dsn …`: 2 logs inserted (product / work order from the folders)",
      out.returncode == 0 and "Done: 2 inserted, 0 duplicates, 0 errors" in out.stdout, out.stdout.strip().splitlines()[-1:])
shutil.rmtree(desk)
shutil.rmtree(batch)

rows = db.rows("SELECT work_order, product_model, COUNT(*) AS runs, COUNT(DISTINCT sn) AS units, COUNT(upload_id) AS with_upload_id "
               "FROM test_record WHERE sn LIKE %s GROUP BY 1, 2 ORDER BY 1", (SN_PREFIX + "%",))
check("test_record: rows written in the old column layout; upload_id stays empty (not a web batch)",
      [(r["work_order"], r["product_model"], r["runs"], r["units"], r["with_upload_id"]) for r in rows] == [(WO_A, PM_A, 15, 14, 0), (WO_B, PM_B, 2, 2, 0)], rows)
v = db.rows("SELECT * FROM v_yield_by_wo WHERE work_order = %s", (WO_A,))
check("old view v_yield_by_wo (Grafana) shows the work order at once", bool(v), v[:1])

# ── 3. the old HTTP ingest ──
status, _ = call("POST", "/api/ingest?" + urlencode({"path": "/app/logs", "dry_run": "true"}))
check("POST /api/ingest without signing in → 401 (the old API had no sign-in here; the new one needs an administrator)", status == 401, status)
s, body = call("POST", "/api/auth/login", {"username": ADMIN[0], "password": ADMIN[1]})
admin = body.get("token") if s == 200 else None
status, data = call("POST", "/api/ingest?" + urlencode({"path": "/app/logs", "dry_run": "true"}), token=admin)
check("POST /api/ingest as administrator (dry run on /app/logs) works as before", status == 200 and data.get("found", 0) >= 1, (status, data))

# ── the running stack picks it up by itself ──
deadline = time.time() + config.STATS_REFRESH_SEC + 90
waited = time.time()
while time.time() < deadline:
    ref = get("/api/meta").get("stats_refreshed_at")
    if ref and datetime.fromisoformat(ref) > written_at:
        break
    time.sleep(5)
check(f"the statistics refresh by itself (every {config.STATS_REFRESH_SEC // 60} min) — nothing forced",
      ref and datetime.fromisoformat(ref) > written_at, f"after {time.time() - waited:.0f} s")
while time.time() < deadline + 120:
    fl = get("/api/fail-list", work_order=WO_A, page_size=20)
    if fl["items"] and (fl["items"][0].get("analysis") or {}).get("category"):
        break
    time.sleep(5)

st = {x["work_order"]: x for x in get("/api/wo-status")["items"]}
a, b = st.get(WO_A), st.get(WO_B)
check("Work Orders / status light: the desktop work order is listed — 14 units, 1 failed, red (92.9 % < 95 %), in production",
      a and (a["total"], a["failed"], a["light"]) == (14, 1, "bad") and a["state"] != "done",
      a and {k: a[k] for k in ("total", "failed", "yield_pct", "light", "state", "flags")})
check("…with its one-sentence note in both languages", a and a["note"]["zh"] and a["note"]["en"], a and a["note"])
check("the work order of the product the registry does not know is analysed too (2 units, green)",
      b and (b["total"], b["failed"], b["product_model"]) == (2, 0, PM_B), b and {k: b[k] for k in ("total", "failed", "light", "product_model")})
d = get("/api/wo-detail", work_order=WO_A)
check("work-order detail: metrics, trend and throughput of the desktop rows",
      d["metrics"]["total"] == 14 and d["metrics"]["failed"] == 1 and d["extra"]["retry_units"] == 1 and d["metrics"]["p50_24g"],
      {k: d["metrics"][k] for k in ("total", "failed", "yield_pct", "retry_rate", "p50_24g", "p50_5g")})
ov = get("/api/overview", date_from=today, date_to=today)
check("Trend page (today): the 16 new units are counted", ov["summary"]["total"] - before_today == 16,
      f"{before_today} → {ov['summary']['total']}")
fc = get("/api/forecast", work_order=WO_A)
check("Forecast view of the work order", fc.get("found") and fc["total"] == 14, {k: fc.get(k) for k in ("state", "total", "yield_pct")})
item = fl["items"][0] if fl["items"] else {}
an = item.get("analysis") or {}
check("Fail List: the unit still failing is there, classified by the FAIL scan (60 s) with a next step",
      fl["counts"]["pending"] == 1 and item.get("sn") == F1 and an.get("category") == "WIFI_TP_LOW_24G" and item["suggest"]["code"],
      (fl["counts"], item.get("sn"), an.get("category"), item.get("suggest", {}).get("code")))
rt = db.rows("SELECT retest_outcome, run_seq FROM fail_analysis f JOIN test_record t ON t.id = f.record_id WHERE t.sn = %s", (F2,))
check("the FAIL that passed on the retest is marked passed-on-retest", rt and rt[0]["retest_outcome"] == "pass_on_retest", rt)
al = get("/api/alerts")
codes = {x["code"] for x in al["items"]}
check("alert banner: the red work order and the FAIL waiting for a disposition are raised",
      any(x["code"] == "wo_red" and x["values"].get("wo") == WO_A for x in al["items"]) and "pending_fail" in codes, sorted(codes))
n_recs = get("/api/db-tweak/records", token=admin, work_order=WO_A)["count"]
check("DB maintenance lists the desktop rows (they can be corrected / deleted there)", n_recs == 15, n_recs)
hist = get("/api/log-upload/history", token=admin)
check("Log Upload history does not claim them (they are not web batches)", WO_A not in json.dumps(hist), "")
reg = get("/api/products/registry", token=admin)
known = PM_B in json.dumps(reg, ensure_ascii=False)
print(f"     note: {PM_B} {'is' if known else 'is not'} in the product registry (it is seeded once at the first start; "
      "a name typed only in the desktop tool is analysed everywhere but is listed on the Products page only after it is added there)")

if KEEP:
    print("\n--keep: the synthetic rows stay; remove them with --cleanup")
else:
    n, left = cleanup()
    st = {x["work_order"] for x in get("/api/wo-status")["items"]}
    check("deleted as the old DB Tweak does: rows, classifications and statistics follow", left == 0 and WO_A not in st and WO_B not in st,
          f"{n} rows deleted, {left} left")

passed = sum(results)
print(f"\n{passed}/{len(results)} desktop-path checks passed")
sys.exit(0 if passed == len(results) else 1)
