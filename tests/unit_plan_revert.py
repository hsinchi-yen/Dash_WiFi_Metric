#!/usr/bin/env python3
"""The planned quantity when upload batches are undone (services/upload.revert), against the real schema.

    docker run --rm --network host -v "$PWD/dockerup-essential/api:/app:ro" -v "$PWD/tests:/tests:ro" \
        -v "$PWD/dockerup-essential/log_parser.py:/app/log_parser.py:ro" \
        -e DATABASE_URL=postgresql://qc:qcpass@127.0.0.1:5432/wifitest -w /app wifitest-api:0.2.0 \
        python3 /tests/unit_plan_revert.py

Everything runs in one transaction that is rolled back: nothing is written. Batches are fake
upload_log rows (no test rows), so only the plan logic is exercised.
"""
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "/app")
import db  # noqa: E402
from services import upload  # noqa: E402

WO = "9999-261021999"
T0 = datetime(2026, 10, 21, 8, 0)
results = []
USER = {"username": "test", "role": "admin"}


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail and not cond else ""))


def batch(cur, bid, qty, minutes):
    cur.execute("""INSERT INTO upload_log (id, created_at, finished_at, username, work_order, product_model, planned_qty, status)
                   VALUES (%s, %s, %s, 'test', %s, 'TN-98_TEST', %s, 'done')""",
                (bid, T0 + timedelta(minutes=minutes), T0 + timedelta(minutes=minutes), WO, qty))
    cur.execute("""INSERT INTO wo_plan (work_order, product_model, planned_qty, updated_by, updated_at, upload_id)
                   VALUES (%s, 'TN-98_TEST', %s, 'test', NOW(), %s)
                   ON CONFLICT (work_order) DO UPDATE SET planned_qty = EXCLUDED.planned_qty, upload_id = EXCLUDED.upload_id""",
                (WO, qty, bid))


def admin(cur, qty, minutes):
    cur.execute("""INSERT INTO wo_plan (work_order, product_model, planned_qty, updated_by, updated_at, upload_id, admin_qty, admin_by, admin_at)
                   VALUES (%s, 'TN-98_TEST', %s, 'qc', NOW(), NULL, %s, 'qc', %s)
                   ON CONFLICT (work_order) DO UPDATE SET planned_qty = EXCLUDED.planned_qty, upload_id = NULL,
                       admin_qty = EXCLUDED.admin_qty, admin_by = 'qc', admin_at = EXCLUDED.admin_at""",
                (WO, qty, qty, T0 + timedelta(minutes=minutes)))


def revert(conn, bid):
    upload.revert(conn, db.fetch_one(conn, "SELECT * FROM upload_log WHERE id = %s", (bid,)), USER)


def plan(conn):
    row = db.fetch_one(conn, "SELECT planned_qty, upload_id FROM wo_plan WHERE work_order = %s", (WO,))
    return (row["planned_qty"], row["upload_id"]) if row else None


def scenario(conn, name, steps, expect):
    cur = conn.cursor()
    cur.execute("SAVEPOINT s")
    for step in steps:
        step(cur)
    got = plan(conn)
    check(name, got == expect, f"plan {got}, expected {expect}")
    cur.execute("ROLLBACK TO SAVEPOINT s")


conn = db.connect()
try:
    scenario(conn, "one batch (300) undone → no plan", [
        lambda c: batch(c, "A", 300, 0), lambda c: revert(conn, "A")], None)
    scenario(conn, "batch 300, then the administrator 400, then batch 500; undo 500 → the administrator's 400", [
        lambda c: batch(c, "A", 300, 0), lambda c: admin(c, 400, 10), lambda c: batch(c, "B", 500, 20),
        lambda c: revert(conn, "B")], (400, None))
    scenario(conn, "administrator 400, then batch 300; undo it → 400", [
        lambda c: admin(c, 400, 0), lambda c: batch(c, "A", 300, 10), lambda c: revert(conn, "A")], (400, None))
    scenario(conn, "batches 300 then 500; undo the older one → 500 stays", [
        lambda c: batch(c, "A", 300, 0), lambda c: batch(c, "B", 500, 10), lambda c: revert(conn, "A")], (500, "B"))
    scenario(conn, "batches 300 then 500; undo the newer one → 300 (as before 5B)", [
        lambda c: batch(c, "A", 300, 0), lambda c: batch(c, "B", 500, 10), lambda c: revert(conn, "B")], (300, "A"))
    scenario(conn, "batch 300, then the administrator 400; undo the batch → 400 stays", [
        lambda c: batch(c, "A", 300, 0), lambda c: admin(c, 400, 10), lambda c: revert(conn, "A")], (400, None))
    scenario(conn, "batch 300, administrator 400, batch 500; undo 500 then 300 → 400", [
        lambda c: batch(c, "A", 300, 0), lambda c: admin(c, 400, 10), lambda c: batch(c, "B", 500, 20),
        lambda c: revert(conn, "B"), lambda c: revert(conn, "A")], (400, None))
finally:
    conn.rollback()
    conn.close()

passed = sum(results)
print(f"\n{passed}/{len(results)} plan-revert checks passed (all rolled back)")
sys.exit(0 if passed == len(results) else 1)
