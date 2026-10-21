"""FAIL disposition (5C, Q13): what the line did with a failed unit.

One fail_disposition row per FAIL test run someone set by hand (record_id = the test_record id, no foreign key:
the old DB Tweak deletes test rows directly; the FAIL scan sweeps orphans). What is shown comes from
fail_actions.effective(): a later PASS closes it automatically, otherwise the hand-set status, otherwise open.
Any signed-in account may set it (the plan's §9.2 assumption: no separate line-leader role); every change is audited.

for_records(ids)          {record_id: stored row}
clusters(ids)             {record_id: units with the same category within CLUSTER_MINUTES of that run}
decorate(items, ...)      add analysis-derived `disposition` and `suggest` to FAIL list rows
set_status(...)           store one change (validated by the caller), audited; "open" removes the row
history(record_id)        the audited changes of one FAIL run
"""
from typing import Any, Optional

import db
from services import fail_actions, settings


def for_records(ids: list) -> dict[int, dict]:
    if not ids:
        return {}
    rows = db.rows("SELECT record_id, sn, status, note, updated_by, updated_at FROM fail_disposition WHERE record_id = ANY(%s)",
                   (list(ids),))
    return {int(r["record_id"]): r for r in rows}


def clusters(ids: list) -> dict[int, int]:
    """Different units with the same failure category in the same work order within CLUSTER_MINUTES of each run
    (either side: once the later ones arrive, the first FAILs of a burst point to the station too)."""
    if not ids:
        return {}
    rows = db.rows("""
        SELECT f.record_id,
               (SELECT COUNT(DISTINCT g.sn) FROM fail_analysis g
                WHERE g.category = f.category AND g.work_order IS NOT DISTINCT FROM f.work_order
                  AND g.test_time BETWEEN f.test_time - make_interval(mins => %s) AND f.test_time + make_interval(mins => %s)) AS n
        FROM fail_analysis f WHERE f.record_id = ANY(%s)
    """, (fail_actions.CLUSTER_MINUTES, fail_actions.CLUSTER_MINUTES, list(ids)))
    return {int(r["record_id"]): int(r["n"] or 0) for r in rows}


def decorate(items: list[dict], analysis: dict[int, dict]) -> None:
    """items carry `id` (a FAIL test run); adds `disposition` (effective) and `suggest` in place."""
    ids = [int(r["id"]) for r in items]
    stored = for_records(ids)
    near = clusters(ids)
    streak = int(settings.get()["fail_streak"])
    for r in items:
        rid = int(r["id"])
        a = analysis.get(rid)
        r["disposition"] = fail_actions.effective(a, stored.get(rid))
        r["suggest"] = fail_actions.suggest(a, near.get(rid, 0), streak)


def fail_run(conn, record_id: int) -> Optional[dict]:
    """The FAIL test run (None when it does not exist or did not fail)."""
    return db.fetch_one(conn, "SELECT id, sn, work_order, product_model, test_time FROM test_record WHERE id = %s AND result = FALSE",
                        (record_id,))


def set_status(conn, request, user: dict, record_id: int, sn: str, status: str, note: Optional[str]) -> dict:
    from services import auth
    before = db.fetch_one(conn, "SELECT status, note FROM fail_disposition WHERE record_id = %s FOR UPDATE", (record_id,))
    note = (note or "").strip() or None
    if status == "open":      # back to the default: no row (the audit keeps what was there)
        db.execute(conn, "DELETE FROM fail_disposition WHERE record_id = %s", (record_id,))
    else:
        db.execute(conn, """
            INSERT INTO fail_disposition (record_id, sn, status, note, updated_by, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (record_id) DO UPDATE SET status = EXCLUDED.status, note = EXCLUDED.note,
                                                  updated_by = EXCLUDED.updated_by, updated_at = NOW()
        """, (record_id, sn, status, note, user["username"]))
    auth.audit(conn, request, user["username"], "fail_disposition", str(record_id), {
        "sn": sn, "status": f"{before['status'] if before else 'open'} → {status}",
        **({"note": note} if note != (before or {}).get("note") else {}),
    })
    return db.fetch_one(conn, "SELECT record_id, sn, status, note, updated_by, updated_at FROM fail_disposition WHERE record_id = %s",
                        (record_id,))


def history(record_id: int, limit: int = 20) -> list[dict[str, Any]]:
    return db.rows("""
        SELECT a.at, a.actor, COALESCE(u.display_name, a.actor) AS display_name, a.detail
        FROM account_audit a LEFT JOIN app_users u ON u.username = a.actor
        WHERE a.action = 'fail_disposition' AND a.target = %s
        ORDER BY a.at DESC LIMIT %s
    """, (str(record_id), limit))


def runs_of(sn: str, limit: int = 30) -> list[dict[str, Any]]:
    """Every test of one unit, oldest first (the FAIL detail's run history)."""
    rows = db.rows("""
        SELECT t.id, t.test_time, t.result, t.work_order, t.product_model, t.avg_24g, t.avg_5g, t.bt_result,
               f.category, x.name_zh, x.name_en, f.ssid_group, f.station_port
        FROM test_record t LEFT JOIN fail_analysis f ON f.record_id = t.id LEFT JOIN fail_taxonomy x ON x.code = f.category
        WHERE t.sn = %s ORDER BY t.test_time, t.id LIMIT %s
    """, (sn, limit))
    for r in rows:
        for k in ("avg_24g", "avg_5g"):
            r[k] = None if r[k] is None else round(float(r[k]), 1)
    return rows
