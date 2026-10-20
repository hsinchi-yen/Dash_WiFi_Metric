"""Acceptance test for Phase 4 (accounts, product registry, web Log Upload) against a running stack.

    python3 tests/acceptance_upload.py http://127.0.0.1:8000 <sample.zip> [admin_user admin_pass]

Needs the sample delivery zip (5101-251216015 / TN-99_EDM1-IMX6P, 252 logs already in the DB).
Creates/updates the uploader account `test_uploader`; every row it inserts is reverted again.
Exit code 0 = every check passed. Checks are printed as  ok / FAIL  lines.
"""
import io
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import date

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"
SAMPLE = sys.argv[2] if len(sys.argv) > 2 else ""
ADMIN_USER = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("TWEAK_USER", "qc")
ADMIN_PASS = sys.argv[4] if len(sys.argv) > 4 else os.environ.get("TWEAK_PASS", "qcpass")
UPLOADER, UPLOADER_PASS = "test_uploader", "uploader-pass-2026"
TODAY = date.today().strftime("%y%m%d")
NEW_WO = f"5101-{TODAY}001"
NEW_PRODUCT = "TN-88_NEW-BOARD"

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"{'ok  ' if cond else 'FAIL'} {name}{(' — ' + str(detail)[:300]) if detail and not cond else ''}")
    return cond


class Http:
    def __init__(self):
        self.token = None

    def call(self, method, path, body=None, raw=None, headers=None, stream=False):
        h = dict(headers or {})
        if self.token:
            h["X-Tweak-Token"] = self.token
        data = None
        if raw is not None:
            data = raw
        elif body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{BASE}{path}", data=data, method=method, headers=h)
        try:
            resp = urllib.request.urlopen(req, timeout=180)
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            try:
                return exc.code, json.loads(payload)
            except ValueError:
                return exc.code, {"raw": payload[:200]}
        if stream:
            return resp.status, resp
        payload = resp.read()
        ctype = resp.headers.get("Content-Type", "")
        if "json" in ctype:
            return resp.status, json.loads(payload)
        return resp.status, {"raw": payload, "headers": dict(resp.headers)}

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, body=None):
        return self.call("POST", path, body)

    def delete(self, path):
        return self.call("DELETE", path)

    def multipart(self, path, files):
        """files: [(field, filename, bytes)] plus [("paths", None, text)] form fields."""
        boundary = "----wifiacc" + uuid.uuid4().hex
        buf = io.BytesIO()
        for field, filename, content in files:
            buf.write(f"--{boundary}\r\n".encode())
            if filename is None:
                buf.write(f'Content-Disposition: form-data; name="{field}"\r\n\r\n'.encode())
                buf.write(content.encode() if isinstance(content, str) else content)
            else:
                buf.write(f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode())
                buf.write(b"Content-Type: application/octet-stream\r\n\r\n")
                buf.write(content)
            buf.write(b"\r\n")
        buf.write(f"--{boundary}--\r\n".encode())
        return self.call("POST", path, raw=buf.getvalue(), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})

    def ndjson(self, path, body, on_event=None):
        status, resp = self.call("POST", path, body, stream=True)
        if status != 200:
            return status, resp, []
        events = []
        for line in resp:
            line = line.decode("utf-8").strip()
            if not line:
                continue
            ev = json.loads(line)
            events.append(ev)
            if on_event:
                on_event(ev)
        return status, None, events


def login(user, password):
    h = Http()
    status, data = h.post("/api/auth/login", {"username": user, "password": password})
    if status == 200:
        h.token = data["token"]
    return status, data, h


def modified_zip(src_path):
    """The sample with one comment line appended to every log → new hashes, same names."""
    out = io.BytesIO()
    with zipfile.ZipFile(src_path) as outer, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in outer.infolist():
            if info.is_dir():
                continue
            data = outer.read(info)
            if info.filename.lower().endswith(".zip"):
                inner_out = io.BytesIO()
                with zipfile.ZipFile(io.BytesIO(data)) as inner, zipfile.ZipFile(inner_out, "w", zipfile.ZIP_DEFLATED) as inner_dst:
                    for i2 in inner.infolist():
                        if i2.is_dir():
                            continue
                        content = inner.read(i2)
                        if i2.filename.lower().endswith(".txt"):
                            content += b"\n# acceptance-test copy\n"
                        inner_dst.writestr(i2.filename, content)
                data = inner_out.getvalue()
            dst.writestr(info.filename, data)
    return out.getvalue()


def main():
    status, health = Http().get("/health")
    check("health", status == 200 and health.get("status") == "ok", health)
    llm_ok = bool(health.get("llm", {}).get("connected"))

    # ── accounts ──
    status, data, admin = login(ADMIN_USER, ADMIN_PASS)
    if not check("admin login", status == 200 and data.get("role") == "admin", data):
        return finish()
    status, _ = Http().get("/api/admin/users")
    check("accounts need a session (401)", status == 401)
    status, _ = login("nobody", "wrong-password")[:2]
    check("wrong login → 401", status == 401)
    status, data = admin.post("/api/admin/users", {"username": UPLOADER, "display_name": "Test Uploader", "department": "QA",
                                                   "email": "uploader@example.com", "password": UPLOADER_PASS})
    if status == 400 and data.get("code") == "duplicate_user":
        status, data = admin.post(f"/api/admin/users/{UPLOADER}/reset-password", {"password": UPLOADER_PASS})
        admin.call("PATCH", f"/api/admin/users/{UPLOADER}", {"enabled": True})
    check("create / reset uploader", status == 200, data)
    status, data = admin.post("/api/admin/users", {"username": "ab", "display_name": "x", "password": "short"})
    check("bad username / password rejected", status == 400, data)
    status, data, up = login(UPLOADER, UPLOADER_PASS)
    if not check("uploader login", status == 200 and data.get("role") == "uploader", data):
        return finish()
    status, me = up.get("/api/auth/me")
    check("auth/me", status == 200 and me.get("username") == UPLOADER and me.get("role") == "uploader", me)
    status, _ = up.get("/api/admin/users")
    check("uploader cannot manage accounts (403)", status == 403)
    status, _ = up.get("/api/db-tweak/records?limit=5")
    check("uploader cannot use DB maintenance (403)", status == 403)
    status, data = admin.get("/api/db-tweak/records?limit=5")
    check("admin DB maintenance records", status == 200 and len(data.get("items", [])) == 5, data)
    status, data = admin.get("/api/db-tweak/workorders")
    check("admin DB maintenance work orders", status == 200 and data.get("count", 0) >= 77, data)
    status, data = admin.post("/api/ingest?path=/app/logs&dry_run=true")
    check("ingest (admin session) dry run", status == 200 and data.get("dry_run") is True, data)
    status, _ = up.post("/api/ingest?path=/app/logs&dry_run=true")
    check("ingest denied to uploader (403)", status == 403)
    status, data = up.post("/api/auth/change-password", {"old_password": UPLOADER_PASS, "new_password": UPLOADER_PASS})
    check("same password rejected", status == 400 and data.get("code") == "same_password", data)

    # ── product registry ──
    status, data = up.get("/api/products/registry")
    names = [p["canonical"] for p in data.get("items", [])]
    check("registry lists the 11 seeded products", status == 200 and len(names) >= 11 and "TN-99_EDM1-IMX6P" in names, names)
    cases = [
        ("TN73_PICO_IMX6", "known", "TN-73_PICO-IMX6"), ("tn-175_pico-imx8m_mini", "known", "TN-175_PICO-IMX8M MINI"),
        ("86", "known", "TN-86_PICO-IMX7"), ("TN-86", "known", "TN-86_PICO-IMX7"), ("TN-99_EDM1-IMX6P ", "known", "TN-99_EDM1-IMX6P"),
        ("TN-86_PICO-IMX6", "blocked_same_tn", None), ("TN-99_EDM1", "blocked_same_tn", None),
        ("TN-88_NEW_BOARD", "new", NEW_PRODUCT), ("TNXXX-XXXX-XXXXXX", "bad_format", None), ("TN-9_EDM", "bad_format", None),
        ("TN-99_", "bad_format", None), ("", "bad_format", None),
    ]
    for text, expect_status, expect_name in cases:
        status, res = up.post("/api/products/resolve", {"text": text})
        got_name = res.get("canonical") or res.get("proposal")
        ok = status == 200 and res.get("status") == expect_status and (expect_name is None or got_name == expect_name)
        check(f"resolve {text!r} → {expect_status}" + (f" {expect_name}" if expect_name else ""), ok, res)
    status, res = up.post("/api/products/resolve", {"text": "TN-86_PICO-IMX6"})
    check("R16 suggests the existing product", "TN-86_PICO-IMX7" in (res.get("same_tn") or []), res)
    status, res = up.post("/api/products", {"name": "TN-86_PICO-IMX6"})
    check("adding a same-TN product is blocked", status == 400 and res.get("code") == "same_tn", res)
    status, res = up.post("/api/products", {"name": "TN-99_EDM1-IMX6P"})
    check("adding an existing product → 409", status == 409, res)

    if not SAMPLE or not os.path.isfile(SAMPLE):
        check("sample zip given", False, "pass the sample zip path as the second argument")
        return finish()
    with open(SAMPLE, "rb") as f:
        sample_bytes = f.read()

    # ── analyze (zip) ──
    status, data = up.multipart("/api/log-upload/analyze", [("file", "5101-251216015.zip", sample_bytes)])
    if not check("analyze sample zip", status == 200 and data.get("upload_id"), data):
        return finish()
    uid = data["upload_id"]
    f = data["facts"]
    check("facts: 252 logs / 252 unique SN / all PASS", f.get("logs") == 252 and f.get("unique_sn") == 252 and f.get("pass") == 252 and f.get("fail") == 0, f)
    check("facts: 9 non-log files ignored, nested zip expanded", f.get("ignored_total") == 9 and f.get("zips") == 2, {k: f.get(k) for k in ("ignored_total", "zips", "ignored")})
    check("facts: inferred WO / product from the package", f.get("inferred_wo") == "5101-251216015" and f.get("inferred_product") == "TN-99_EDM1-IMX6P", f)
    check("facts: all 252 already in the DB", f.get("existing_total") == 252, f.get("existing"))
    check("chat opening built from facts", "252" in data.get("chat_opening", "") and "TN-99_EDM1-IMX6P" in data.get("chat_opening_en", ""), data.get("chat_opening"))
    status, other = admin.get(f"/api/log-upload/{uid}")
    check("admin can read an uploader's batch", status == 200, other)
    status, _, up2 = login(ADMIN_USER, ADMIN_PASS)

    def validate(h, wo, pm, qty):
        return h.post("/api/log-upload/validate", {"upload_id": uid, "work_order": wo, "product": pm, "planned_qty": qty})[1]

    def codes(v, level=None):
        return [i["code"] for i in v.get("issues", []) if level is None or i["level"] == level]

    v = validate(up, "5101-251216015", "TN-99_EDM1-IMX6P", "252")
    check("A: same WO/product/252 → no error, no ack, R10 all-exist, R14 met",
          v["can_upload"] and not v["needs_ack"] and "R10" in codes(v) and next(i for i in v["issues"] if i["code"] == "R14")["state"] == "met", v)
    v = validate(up, "5101-261020099", "TN-99_EDM1-IMX6P", "252")
    check("B: other WO → R9 + R8 warnings need ack", v["can_upload"] and v["needs_ack"] and "R9" in codes(v, "warn") and "R8" in codes(v, "warn"), v)
    v = validate(up, "5101-251216015", "TN-86_PICO-IMX6", "252")
    check("C: product typo (same TN) → R16 blocks", not v["can_upload"] and "R16" in codes(v, "error"), v)
    v = validate(up, "5101-251216015", "TN-88_NEW_BOARD", "252")
    check("D: new product → R4 warning + resolved proposal", v["can_upload"] and "R4" in codes(v, "warn") and v["resolved"]["product"] == NEW_PRODUCT and v["resolved"]["new_product"], v)
    v = validate(up, "TN-99_EDM1-IMX6P", "", "252")
    check("E: empty product → R2 error", not v["can_upload"] and "R2" in codes(v, "error"), v)
    v = validate(up, "NOWO", "TN-99_EDM1-IMX6P", "252")
    r5 = next((i for i in v["issues"] if i["code"] == "R5"), {})
    check("F: NOWO → expands to NOWO-<today>001 (R5) + R8", r5.get("value") == f"NOWO-{TODAY}001" and v["resolved"]["work_order"] == f"NOWO-{TODAY}001" and "R8" in codes(v, "warn"), v)
    for wo, reason in (("5101-260230001", "wo_bad_date"), ("5101-261340007", "wo_bad_date"), ("5101-260108000", "wo_seq_zero"),
                       ("5101-26010807", "wo_bad_shape"), ("5101251216015", "wo_bad_shape"), ("NOWO-", "wo_bad_shape"), ("", "wo_empty")):
        v = validate(up, wo, "TN-99_EDM1-IMX6P", "252")
        r3 = next((i for i in v["issues"] if i["code"] == "R3" and i["level"] == "error"), {})
        check(f"G: work order {wo!r} → R3 {reason}", not v["can_upload"] and r3.get("reason") == reason, v.get("issues"))
    v = validate(up, "５１０１－２５１２１６０１５", "tn-99_edm1-imx6p", "252")
    check("H: full-width / lower-case input normalised", v["can_upload"] and v["resolved"]["work_order"] == "5101-251216015" and v["resolved"]["product"] == "TN-99_EDM1-IMX6P", v)
    v = validate(up, "9999-261020001", "TN-99_EDM1-IMX6P", "252")
    check("I: any 4-digit prefix accepted without warning about the prefix", v["can_upload"] and "R3" not in codes(v, "error"), v)
    v = validate(up, "5101-251216015", "TN-99_EDM1-IMX6P", "")
    check("J: empty quantity → R12", not v["can_upload"] and "R12" in codes(v, "error"), v)
    v = validate(up, "5101-251216015", "TN-99_EDM1-IMX6P", "abc")
    check("J2: non-numeric quantity → R12", not v["can_upload"] and "R12" in codes(v, "error"), v)
    v = validate(up, "5101-251216015", "TN-99_EDM1-IMX6P", "20000")
    check("K: quantity 20,000 → R13 warning", v["can_upload"] and "R13" in codes(v, "warn"), v)
    v = validate(up, "5101-251216015", "TN-99_EDM1-IMX6P", "300")
    r14 = next(i for i in v["issues"] if i["code"] == "R14")
    check("L: plan 300 → short 48 (cumulative 252)", r14["state"] == "short" and r14["gap"] == -48 and r14["cumulative"] == 252, r14)
    v = validate(up, "5101-251216015", "TN-99_EDM1-IMX6P", "200")
    r14 = next(i for i in v["issues"] if i["code"] == "R14")
    check("M: plan 200 → over 52 (info only)", r14["state"] == "over" and r14["gap"] == 52 and r14["level"] == "info" and not v["needs_ack"], r14)
    v = validate(up, "5101-251216015", "TN-99_EDM1-IMX6P", "1,200")
    check("N: '1,200' parsed as 1200", v["resolved"]["planned_qty"] == 1200, v["resolved"])

    # ── commit: duplicates only (same WO / product) ──
    status, err, events = up.ndjson("/api/log-upload/commit", {"upload_id": uid, "work_order": "5101-251216015", "product": "TN-99_EDM1-IMX6P",
                                                              "planned_qty": "252", "ack_warnings": False, "mode": "form"})
    done = next((e for e in events if e["type"] in ("done", "error")), {})
    check("commit duplicates → done, 0 new, 252 skipped", status == 200 and done.get("type") == "done" and done.get("uploaded") == 0 and done.get("skipped") == 252, done or err)
    status, items = up.get(f"/api/log-upload/{uid}/items?format=csv")
    csv = items.get("raw", b"").decode("utf-8", "replace")
    check("items csv: header + 252 duplicate rows", status == 200 and csv.startswith("Index,Filename,Status,Message") and csv.count(",duplicate,") == 252, csv[:200])
    status, hist = up.get("/api/log-upload/history")
    check("history shows the batch (uploader view)", status == 200 and any(r["id"] == uid and r["status"] == "done" for r in hist.get("items", [])), hist)
    status, ov = up.get("/api/overview?work_order=5101-251216015")
    check("overview plan for WO = 252 planned / 252 tested / gap 0", ov.get("plan", {}) and ov["plan"]["planned"] == 252 and ov["plan"]["gap"] == 0, ov.get("plan"))
    status, rv = up.delete(f"/api/log-upload/{uid}")
    check("revert duplicate-only batch → 0 rows", status == 200 and rv.get("reverted") == 0, rv)
    status, ov = up.get("/api/overview?work_order=5101-251216015")
    check("plan removed after revert", ov.get("plan") is None, ov.get("plan"))
    status, _ = up.delete(f"/api/log-upload/{uid}")
    check("second revert refused", status == 400)

    # ── commit: real insert with a new product and a new WO, then revert ──
    mod = modified_zip(SAMPLE)
    status, data = up.multipart("/api/log-upload/analyze", [("file", "5101-251216015.zip", mod)])
    uid2 = data.get("upload_id")
    check("analyze modified zip → 0 existing", status == 200 and data["facts"]["existing_total"] == 0 and data["facts"]["logs"] == 252, data.get("facts"))
    status, err, events = up.ndjson("/api/log-upload/commit", {"upload_id": uid2, "work_order": NEW_WO, "product": "TN-88_NEW_BOARD",
                                                              "planned_qty": "300", "ack_warnings": False, "mode": "form"})
    check("commit without ack → 400 needs_ack", status == 400 and err.get("code") == "needs_ack", err)
    t0 = time.time()
    status, err, events = up.ndjson("/api/log-upload/commit", {"upload_id": uid2, "work_order": NEW_WO, "product": "TN-88_NEW_BOARD",
                                                              "planned_qty": "300", "ack_warnings": True, "mode": "form"})
    done = next((e for e in events if e["type"] in ("done", "error")), {})
    check("commit new product/WO → 252 inserted", status == 200 and done.get("type") == "done" and done.get("uploaded") == 252 and done.get("failed") == 0,
          done or err)
    print(f"     (252 files written in {time.time() - t0:.1f} s; progress events: {sum(1 for e in events if e['type'] == 'progress')})")
    status, reg = up.get("/api/products/registry")
    check("new product now in the registry", NEW_PRODUCT in [p["canonical"] for p in reg.get("items", [])], reg)
    status, ov = up.get(f"/api/overview?work_order={NEW_WO}")
    check("overview: new WO visible with plan 300 / tested 252 / gap -48",
          ov.get("summary", {}).get("total") == 252 and ov.get("plan", {}).get("gap") == -48, {"summary": ov.get("summary"), "plan": ov.get("plan")})
    status, opts = up.get("/api/filter-options")
    check("filter options list the new WO and product", any(w["work_order"] == NEW_WO for w in opts.get("work_orders", [])) and
          any(p["product_model"] == NEW_PRODUCT for p in opts.get("products", [])), opts.get("latest_work_order"))
    status, items = up.get(f"/api/log-upload/{uid2}/items")
    statuses = {}
    for it in items.get("items", []):
        statuses[it["status"]] = statuses.get(it["status"], 0) + 1
    check("items: 252 inserted with record ids", statuses.get("inserted") == 252 and all(it["record_id"] for it in items["items"]), statuses)
    status, _ = admin.get(f"/api/log-upload/history?username={UPLOADER}")
    check("admin history filtered by uploader", status == 200)
    status, rv = up.delete(f"/api/log-upload/{uid2}")
    check("revert → 252 rows deleted", status == 200 and rv.get("reverted") == 252, rv)
    status, reg = up.get("/api/products/registry")
    check("new product removed with its only batch", NEW_PRODUCT not in [p["canonical"] for p in reg.get("items", [])], reg)
    status, ov = up.get(f"/api/overview?work_order={NEW_WO}")
    check("WO gone from statistics after revert", ov.get("summary", {}).get("total") in (0, None) and ov.get("plan") is None, ov.get("summary"))

    # ── NOWO commit + cancel mid-way ──
    status, data = up.multipart("/api/log-upload/analyze", [("file", "logs.zip", mod)])
    uid3 = data.get("upload_id")
    done = {}
    cancel_resp = {}

    def run():
        nonlocal done
        _, _, evs = up.ndjson("/api/log-upload/commit", {"upload_id": uid3, "work_order": "nowo", "product": "TN-99_EDM1-IMX6P",
                                                        "planned_qty": "252", "ack_warnings": True, "mode": "form"})
        done = next((e for e in evs if e["type"] in ("done", "error")), {})

    th = threading.Thread(target=run)
    th.start()
    time.sleep(0.6)
    cancel_resp = up.post(f"/api/log-upload/{uid3}/cancel")[1]
    th.join(timeout=240)
    total = sum(done.get(k, 0) for k in ("uploaded", "skipped", "failed", "cancelled"))
    check("NOWO commit stream finished", done.get("type") == "done" and total == 252, done)
    status, row = up.get(f"/api/log-upload/{uid3}")
    check("NOWO stored as NOWO-<today>001", row.get("work_order") == f"NOWO-{TODAY}001", row.get("work_order"))
    check("cancel request accepted and honoured at a 50-file boundary (or batch already finished)",
          (cancel_resp.get("ok") and done.get("status") == "cancelled" and done.get("uploaded") % 50 == 0 and done.get("cancelled") > 0)
          or (done.get("status") == "done" and done.get("uploaded") == 252), {"cancel": cancel_resp, "done": done})
    status, rv = up.delete(f"/api/log-upload/{uid3}")
    check("revert NOWO batch", status == 200 and rv.get("reverted") == done.get("uploaded"), rv)

    # ── folder mode (batched multipart) ──
    status, data = up.post("/api/log-upload/folder/start", {"name": "5101-251216015", "files": 300, "bytes": 0})
    uid4 = data.get("upload_id")
    check("folder start", status == 200 and uid4, data)
    with zipfile.ZipFile(io.BytesIO(sample_bytes)) as outer:
        entries = []
        for info in outer.infolist():
            if info.is_dir():
                continue
            entries.append((info.filename, outer.read(info)))
    batch, n_sent = [], 0
    for name, content in entries:
        batch.append(("files", name.rsplit("/", 1)[-1], content))
        batch.append(("paths", None, name))
    status, data = up.multipart(f"/api/log-upload/folder/{uid4}/files", batch)
    check("folder batch received (inner zip expanded on the server)", status == 200 and data.get("items", 0) >= 252, data)
    status, data = up.post(f"/api/log-upload/folder/{uid4}/finish")
    check("folder finish → same facts as the zip", status == 200 and data["facts"]["logs"] == 252 and data["facts"]["unique_sn"] == 252
          and data["facts"]["inferred_product"] == "TN-99_EDM1-IMX6P" and data["facts"]["existing_total"] == 252, data.get("facts"))
    v = up.post("/api/log-upload/validate", {"upload_id": uid4, "work_order": "5101-251216015", "product": "TN-99_EDM1-IMX6P", "planned_qty": "252"})[1]
    check("folder batch validates like the zip", v["can_upload"] and "R10" in codes(v), v)

    # ── AI chat (server-driven questions; the LLM only phrases) ──
    status, data = up.multipart("/api/log-upload/analyze", [("file", "5101-251216015.zip", sample_bytes)])
    uid5 = data.get("upload_id")
    chat = []
    known = {"product": "", "work_order": "", "planned_qty": ""}

    def turn(text):
        chat.append({"role": "user", "content": text})
        t0 = time.time()
        status, err, evs = up.ndjson("/api/log-upload/chat", {"upload_id": uid5, "messages": chat, "known": known, "lang": "zh"})
        ex = next((e for e in evs if e["type"] == "extract"), {})
        val = next((e for e in evs if e["type"] == "validation"), {})
        dn = next((e for e in evs if e["type"] == "done"), {})
        for k in ("product", "work_order", "planned_qty"):
            known[k] = ex.get(k) or ""
        chat.append({"role": "assistant", "content": dn.get("reply", "")})
        return status, ex, val, dn, time.time() - t0, err

    status, ex, val, dn, dt, err = turn("TN-99_EDM1-IMX6P")
    check("chat turn 1: product read, next step = work order", status == 200 and ex.get("product") == "TN-99_EDM1-IMX6P" and ex.get("step") == "work_order", (ex, err))
    print(f"     reply ({dt:.1f}s, templated={dn.get('templated')}): {dn.get('reply', '')[:120]!r}")
    status, ex, val, dn, dt, err = turn("5101-251216015")
    check("chat turn 2: work order read, next step = planned qty", ex.get("work_order") == "5101-251216015" and ex.get("step") == "planned_qty", ex)
    print(f"     reply ({dt:.1f}s, templated={dn.get('templated')}): {dn.get('reply', '')[:120]!r}")
    status, ex, val, dn, dt, err = turn("252")
    check("chat turn 3: quantity read, step = confirm, can upload", ex.get("planned_qty") == "252" and ex.get("step") == "confirm" and val.get("can_upload"), (ex, val.get("issues")))
    print(f"     reply ({dt:.1f}s, templated={dn.get('templated')}): {dn.get('reply', '')[:120]!r}")
    status, ex, val, dn, dt, err = turn("改成 TN-86_PICO-IMX6")
    check("chat turn 4: same-TN typo blocked (R16), step back to product", ex.get("step") == "product" and "R16" in [i["code"] for i in val.get("issues", [])], (ex, val.get("issues")))
    print(f"     reply ({dt:.1f}s, templated={dn.get('templated')}): {dn.get('reply', '')[:160]!r}")
    status, ex, val, dn, dt, err = turn("86")
    check("chat turn 5: TN number alone resolves to TN-86_PICO-IMX7", ex.get("product") == "TN-86_PICO-IMX7", ex)
    status, ex, val, dn, dt, err = turn("這批沒有工單")
    check("chat turn 6: colloquial 'no work order' is not accepted silently", ex.get("work_order") == "5101-251216015" and ex.get("nowo") in (True, False), ex)
    status, ex, val, dn, dt, err = turn("NOWO")
    check("chat turn 7: typed NOWO expands", ex.get("work_order") == "NOWO" and val.get("resolved", {}).get("work_order") == f"NOWO-{TODAY}001", (ex, val.get("resolved")))
    if not llm_ok:
        print("     (LLM offline: replies were templates)")

    # ── sign out ──
    status, _ = up.post("/api/auth/logout")
    status, _ = up.get("/api/auth/me")
    check("token invalid after logout (401)", status == 401)
    return finish()


def finish():
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
