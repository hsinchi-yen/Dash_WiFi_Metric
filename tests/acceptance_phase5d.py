#!/usr/bin/env python3
"""Phase 5D acceptance (API): the upload wizard's server side — the stored plan offered by /validate (from an earlier
batch, then the administrator's), the reading of a batch against the product's history (rules; the AI wording on
request), and DB Maintenance's impact preview (one record, a batch, a work order), CSV export and batch undo.

    python3 tests/acceptance_phase5d.py [BASE] <sample.zip> [admin_user admin_pass]

Uses the uploader test_uploader and the sample delivery zip of tests/acceptance_upload.py: a copy with new file hashes
is uploaded once under work order 5101-261021901 and undone again; the plan it leaves is cleared. The row count is
compared at the end.
"""
import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import acceptance_upload as AU  # noqa: E402  (BASE / SAMPLE / admin from the same arguments)

WO = "5101-261021901"
PRODUCT = "TN-99_EDM1-IMX6P"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""), flush=True)


def rows_total(admin):
    status, data = admin.get("/api/db-tweak/products")
    return sum(int(p["total"]) for p in data["items"])


def main():
    if not AU.SAMPLE:
        print("usage: acceptance_phase5d.py BASE sample.zip")
        return 2
    _, _, admin = AU.login(AU.ADMIN_USER, AU.ADMIN_PASS)
    status, _, up = AU.login(AU.UPLOADER, AU.UPLOADER_PASS)
    check("administrator and test_uploader sign in", admin.token and status == 200)
    before = rows_total(admin)
    mod = AU.modified_zip(AU.SAMPLE)

    status, data = up.multipart("/api/log-upload/analyze", [("file", "5101-251216015.zip", mod)])
    uid = data.get("upload_id")
    check("analyze a copy with new hashes: 252 logs, none in the database", status == 200 and data["facts"]["existing_total"] == 0)
    status, v = up.post("/api/log-upload/validate", {"upload_id": uid, "work_order": WO, "product": PRODUCT, "planned_qty": "300"})
    check("validate: a work order without a stored plan → plan null", status == 200 and v.get("plan") is None, v.get("plan"))
    status, err, events = up.ndjson("/api/log-upload/commit", {"upload_id": uid, "work_order": WO, "product": PRODUCT, "planned_qty": "300",
                                                              "ack_warnings": True, "mode": "form"})
    done = next((e for e in events if e["type"] in ("done", "error")), {})
    check("commit: 252 rows inserted", status == 200 and done.get("uploaded") == 252, done or err)
    try:
        # ── reading: this batch against the product's history ──
        status, r = up.get(f"/api/log-upload/{uid}/reading")
        f = r.get("facts") or {}
        check("reading: the batch's 252 units, yield 100%, medians of 2.4G / 5G", status == 200 and f["batch"]["units"] == 252
              and f["batch"]["yield_pct"] == 100.0 and f["batch"]["p50_24g"] and f["batch"]["p50_5g"], f.get("batch"))
        status, ov = admin.get(f"/api/overview?product_model={PRODUCT}")
        check("reading: the product's history = its other units (all its units minus this batch's 252)",
              f["history"]["units"] == ov["summary"]["total"] - 252 and f["history"]["units"] > 0, (f["history"]["units"], ov["summary"]["total"]))
        codes = [p["code"] for p in r["points"]]
        check("reading points: yield, 2.4G and 5G against the usual values, an overall level", "yield" in codes and any(c.endswith("_24g") for c in codes)
              and any(c.endswith("_5g") for c in codes) and r["level"] in ("good", "warn"), (r["level"], codes))
        yld = next(p for p in r["points"] if p["code"] == "yield")
        check("yield point carries the thresholds of Settings (98 / 95) and the history yield", yld["values"]["good"] == 98.0 and yld["values"]["warn"] == 95.0
              and yld["values"]["hist"] is not None, yld)
        status, other = admin.get(f"/api/log-upload/{uid}/reading")
        check("the administrator can read any batch's reading", status == 200)
        status, h = AU.Http().get(f"/api/log-upload/{uid}/reading")
        check("signed out → 401", status == 401, status)
        _, health = AU.Http().get("/health")
        if health.get("llm", {}).get("connected"):
            status, ai = up.post(f"/api/log-upload/{uid}/reading/ai?lang=zh")
            if status == 503:
                status, ai = up.post(f"/api/log-upload/{uid}/reading/ai?lang=zh")
            check("AI wording on request: Chinese text, numbers checked (dropped sentences listed)", status == 200 and "dropped" in ai
                  and (not ai["text"] or any("一" <= ch <= "鿿" for ch in ai["text"])), ai)
            print("     AI:", ai.get("text"), "| dropped:", ai.get("dropped"))
            pieces = ai.get("dropped", []) + ([ai["text"]] if ai.get("text") else [])
            check("no sentence was cut inside a number (a piece ending in a digit means 15.5 became 15. + 5…)",
                  not any(x.rstrip()[-1:].isdigit() or x.rstrip().endswith((".", "．")) and x.rstrip()[-2:-1].isdigit() for x in pieces), pieces)

        # ── the stored plan offered to the next upload ──
        status, data = up.multipart("/api/log-upload/analyze", [("file", "5101-251216015.zip", mod)])
        uid2 = data.get("upload_id")
        status, v = up.post("/api/log-upload/validate", {"upload_id": uid2, "work_order": WO, "product": PRODUCT, "planned_qty": ""})
        plan = v.get("plan") or {}
        check("validate offers the plan stored by the earlier batch: 300, not by the administrator, 252 tested",
              plan.get("planned_qty") == 300 and plan.get("by_admin") is False and plan.get("tested") == 252 and plan.get("updated_by") == AU.UPLOADER, plan)
        admin.call("PUT", "/api/wo-plan", {"work_order": WO, "product_model": PRODUCT, "planned_qty": 350})
        status, v = up.post("/api/log-upload/validate", {"upload_id": uid2, "work_order": WO, "product": PRODUCT, "planned_qty": "350"})
        plan = v.get("plan") or {}
        check("after the administrator sets 350, validate offers 350 marked as the administrator's", plan.get("planned_qty") == 350
              and plan.get("by_admin") is True and plan.get("updated_by") == AU.ADMIN_USER, plan)
        codes = [i["code"] for i in v["issues"]]
        check("entering the stored value raises no 'plan will change' notice (R15)", "R15" not in codes, codes)

        # ── DB Maintenance: impact, export ──
        status, im = admin.get(f"/api/db-tweak/impact?upload_id={uid}")
        check("impact of the batch: 252 rows, all web-uploaded, one batch, the work order goes 252 → 0 units",
              status == 200 and im["rows"] == 252 and im["web_rows"] == 252 and len(im["batches"]) == 1
              and im["work_orders"][0]["units_before"] == 252 and im["work_orders"][0]["units_after"] == 0, im)
        status, im = admin.get(f"/api/db-tweak/impact?work_order={WO}")
        check("impact of the work order = the same 252 rows", status == 200 and im["rows"] == 252 and im["units"] == 252)
        status, recs = admin.get(f"/api/db-tweak/records?work_order={WO}&limit=1")
        rid = recs["items"][0]["id"]
        status, im = admin.get(f"/api/db-tweak/impact?record_id={rid}")
        check("impact of one record: 1 row, the work order 252 → 251 units", status == 200 and im["rows"] == 1
              and im["work_orders"][0]["units_after"] == 251, im.get("work_orders"))
        status, err = admin.get("/api/db-tweak/impact")
        check("impact without a scope → 400", status == 400, status)
        status, err = up.get(f"/api/db-tweak/impact?work_order={WO}")
        check("impact by an uploader → 403", status == 403, status)
        status, out = admin.get(f"/api/db-tweak/export.csv?work_order={WO}")
        text = out["raw"].decode("utf-8")
        rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
        check("CSV export: BOM, header + 252 rows, no raw log column, the batch id on every row",
              text.startswith("﻿") and len(rows) == 253 and "raw_log" not in rows[0] and all(r[rows[0].index("upload_id")] == uid for r in rows[1:]),
              (len(rows), rows[0][:6]))
    finally:
        status, rv = up.delete(f"/api/log-upload/{uid}")
        check("undo the batch: 252 rows removed", status == 200 and rv.get("reverted") == 252, rv)
        admin.call("DELETE", f"/api/wo-plan?work_order={WO}")
    status, r = up.get(f"/api/log-upload/{uid}/reading")
    check("reading of the undone batch: no rows", status == 200 and r["points"][0]["code"] == "no_rows", r.get("points"))
    status, ov = admin.get(f"/api/overview?work_order={WO}")
    check("the test work order is gone, with no plan left", ov["summary"]["total"] == 0 and ov.get("plan") is None)
    check("row count back to where it was", rows_total(admin) == before, (rows_total(admin), before))
    print(f"\n{sum(results)}/{len(results)} Phase 5D checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
