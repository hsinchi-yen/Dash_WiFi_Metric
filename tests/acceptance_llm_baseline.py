#!/usr/bin/env python3
"""A new batch of a product does not make the LLM rewrite AI texts of the product's finished work orders that are still true.

Every batch moves the product's baselines (the usual 2.4G / 5G medians, the hourly target). The final reports and the
one-line notes of finished work orders carry them, so before the fix each batch made the LLM rewrite the AI-written
reports and notes of that product's finished work orders (production, 2026-10-22: 6 report calls for one mock batch
and its undo). Now an AI report / note of a finished work order is rewritten only when its own facts change or a number
it states is no longer a fact (rule 3); otherwise it stays.

    docker run --rm --network host -v "$PWD/dockerup-essential/api:/app:ro" -v "$PWD/tests:/tests:ro" \\
        -v "$PWD/dockerup-essential/log_parser.py:/app/log_parser.py:ro" -v "$PWD/_remote_snapshot/<ts>/dockerup-essential:/old:ro" \\
        -e DATABASE_URL=postgresql://qc:qcpass@127.0.0.1:5432/wifitest -w /app wifitest-api:0.2.0 \\
        python3 /tests/acceptance_llm_baseline.py [BASE]

The administrator uploads a mock batch through the web upload API (31 units, all PASS, synthetic SNs 9999999902xx,
work order 9999-261022921, TN-99_EDM1-IMX6P), the test waits for the report and note workers, undoes the batch, waits
again, and removes what the dashboard cached about the mock work order. (The mock work order itself is in progress and
below its hourly target, amber: an AI note about it is allowed.)
"""
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import datetime, timedelta

sys.path.insert(0, "/app")
import config  # noqa: E402
import db  # noqa: E402
from services import wo_notes, wo_report, wo_status  # noqa: E402
from services.wo_rules import local_now, note_facts  # noqa: E402

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
ADMIN = (os.environ.get("TWEAK_USER", "qc"), os.environ.get("TWEAK_PASS", "qcpass"))
WO, PM, SN = "9999-261022921", "TN-99_EDM1-IMX6P", "9999999902"
WAIT = 150                          # the workers scan every 30 s; an LLM report takes 10–15 s
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail != "" else ""), flush=True)


def call(method, path, body=None, raw=None, headers=None, token=None):
    h = dict(headers or {"Content-Type": "application/json"})
    if token:
        h["X-Tweak-Token"] = token
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            text = r.read().decode()
            return r.status, text
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def llm_calls(since):
    return {r["purpose"]: r["n"] for r in db.rows(
        "SELECT purpose, COUNT(*) AS n FROM llm_usage WHERE at >= %s AND purpose IN ('wo_report', 'wo_note') GROUP BY 1", (since,))}


def ai_texts():
    """The product's finished work orders whose report / note is AI-written, with the stored row."""
    st = wo_status.statuses()
    done = {k for k, s in st.items() if k[1] == PM and s["state"] == "done"}
    reps = {(r["work_order"], r["product_model"]): r for r in db.rows(
        "SELECT * FROM wo_report WHERE product_model = %s AND source = 'llm'", (PM,)) if (r["work_order"], r["product_model"]) in done}
    notes = {(r["work_order"], r["product_model"]): r for r in db.rows(
        "SELECT * FROM wo_status_note WHERE product_model = %s AND source = 'llm'", (PM,)) if (r["work_order"], r["product_model"]) in done}
    return st, reps, notes


def still_shown(label):
    """Every AI report / note of the product's finished work orders is still the one shown (not stale, no fallback)."""
    st, reps, notes = ai_texts()
    stale = [k[0] for k in reps if json.loads(call("GET", f"/api/wo-report?work_order={k[0]}&product_model={PM}")[1])["state"] != "ready"]
    shown = {x["work_order"]: x["note"]["source"] for x in json.loads(call("GET", "/api/wo-status")[1])["items"] if x["product_model"] == PM}
    fallback = [k[0] for k in notes if shown.get(k[0]) != "llm"]
    check(f"{label}: the {len(reps)} AI reports are shown as current (not 'stale')", not stale, stale)
    check(f"{label}: the {len(notes)} AI notes are still shown (no rule-sentence fallback)", not fallback, fallback)


s, body = call("POST", "/api/auth/login", {"username": ADMIN[0], "password": ADMIN[1]})
token = json.loads(body)["token"]
st, reps0, notes0 = ai_texts()
check(f"{PM} has finished work orders with AI-written reports / notes to protect", reps0 and notes0,
      (sorted(k[0] for k in reps0), sorted(k[0] for k in notes0)))
still_shown("before the batch")
since = datetime.now().astimezone()

# ── the mock batch: 31 units, all PASS ──
tmpl = open("/old/logs/20260203_082710_513126033667_001F7B6C11E4_001F7B6C11E5_PASS.txt", "rb").read().decode("utf-8", "replace")
t0 = local_now(config.LOCAL_TZ) - timedelta(minutes=100)
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    for i in range(1, 32):
        stamp = (t0 + timedelta(minutes=3 * i)).strftime("%Y%m%d_%H%M%S")
        sn = f"{SN}{i:02d}"
        z.writestr(f"{WO}/{stamp}_{sn}_001F7B04{sn[-4:]}_001F7B05{sn[-4:]}_PASS.txt", tmpl + f"\n# llm-baseline check {sn} {stamp}\n")
boundary = "----llmbase" + uuid.uuid4().hex
form = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"mock.zip\"\r\n"
        "Content-Type: application/octet-stream\r\n\r\n").encode() + buf.getvalue() + f"\r\n--{boundary}--\r\n".encode()
s, body = call("POST", "/api/log-upload/analyze", raw=form, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, token=token)
uid = json.loads(body)["upload_id"]
s, body = call("POST", "/api/log-upload/commit", {"upload_id": uid, "work_order": WO, "product": PM, "planned_qty": "31",
                                                 "ack_warnings": True, "mode": "form"}, token=token)
done = [json.loads(line) for line in body.splitlines() if line.strip()]
done = next((e for e in done if e.get("type") in ("done", "error")), {})
check("the mock batch is uploaded: 31 new, 0 errors", done.get("type") == "done" and done.get("uploaded") == 31, done)

def classify():
    """The product's finished work orders' AI texts whose facts moved: kept (every number still a fact: no rewrite
    needed) or due (a number it states changed: rewritten, as rule 3 wants)."""
    st = wo_status.statuses()
    out = {"moved": [], "kept": [], "due": []}
    for k, row in reps0.items():
        f, d = wo_report.facts(k)
        if d != row["facts_hash"]:
            out["moved"].append(("report", k[0]))
            out["kept" if wo_report.up_to_date(row, f, d) else "due"].append(("report", k[0]))
    for k, row in notes0.items():
        f, d = note_facts(st[k])
        if d != row["facts_hash"]:
            out["moved"].append(("note", k[0]))
            out["kept" if wo_notes.up_to_date(row, st[k], f, d) else "due"].append(("note", k[0]))
    return out


def untouched(kept):
    """The kept AI texts were not rewritten (same created_at) and are shown as current."""
    rows = {("report", r["work_order"]): r for r in db.rows("SELECT work_order, created_at, source FROM wo_report WHERE product_model = %s", (PM,))}
    rows.update({("note", r["work_order"]): r for r in db.rows("SELECT work_order, created_at, source FROM wo_status_note WHERE product_model = %s", (PM,))})
    before = {("report", k[0]): r["created_at"] for k, r in reps0.items()} | {("note", k[0]): r["created_at"] for k, r in notes0.items()}
    rewritten = [x for x in kept if rows[x]["created_at"] != before[x] or rows[x]["source"] != "llm"]
    shown = {x["work_order"]: x["note"]["source"] for x in json.loads(call("GET", "/api/wo-status")[1])["items"] if x["product_model"] == PM}
    not_shown = [x for x in kept if (x[0] == "note" and shown.get(x[1]) != "llm") or (x[0] == "report" and json.loads(
        call("GET", f"/api/wo-report?work_order={x[1]}&product_model={PM}")[1])["state"] != "ready")]
    return rewritten, not_shown


time.sleep(5)
c = classify()
check("control: the batch moved the baselines in the facts of the product's AI texts (the old rule rewrote all of them)",
      c["moved"], c["moved"])
check("…and some of them still state only true numbers: those need no LLM call", c["kept"], {"kept": c["kept"], "due": c["due"]})
time.sleep(WAIT)
rw, ns = untouched(c["kept"])
check(f"after the batch: the {len(c['kept'])} AI texts whose numbers are still facts were not rewritten", not rw, rw)
check("…and are shown as current (report ready, AI note shown)", not ns, ns)
print(f"     (LLM calls in the window, any work order: {llm_calls(since)}; texts whose stated numbers changed: {c['due']})")

# ── undo ──
s, body = call("DELETE", f"/api/log-upload/{uid}", token=token)
check("the batch is undone", s == 200 and json.loads(body).get("reverted") == 31, body[:200])
time.sleep(WAIT)
rw, ns = untouched(c["kept"])
check("after the undo: still not rewritten", not rw, rw)
check("…and still shown as current", not ns, ns)

with db.db() as conn:
    for t in ("wo_status_note", "wo_report", "wo_plan"):
        db.execute(conn, f"DELETE FROM {t} WHERE work_order = %s", (WO,))
left = db.rows("SELECT (SELECT COUNT(*) FROM test_record WHERE sn LIKE %s) + (SELECT COUNT(*) FROM mv_unit WHERE work_order = %s) AS n",
               (SN + "%", WO))[0]["n"]
check("nothing of the mock batch is left", left == 0, left)
print(f"\n{sum(results)}/{len(results)} LLM-baseline checks passed")
sys.exit(0 if all(results) else 1)
