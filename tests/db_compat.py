#!/usr/bin/env python3
"""Old-database compatibility checks: does a database the new API has migrated still serve
every old consumer exactly as before, and does the rollback script return it to the old schema?

    python3 tests/db_compat.py db OLD_DSN NEW_DSN [--mode migrated|rollback] [--schema-only]
                                [--grafana DASHBOARD.json] [--parser-dir DIR --sample LOG]
    python3 tests/db_compat.py oldapi OLD_URL NEW_URL --sample-dir /samples [--user qc --password qcpass]

OLD_DSN is an untouched restore of the production backup (a backup this project already upgraded:
that restore plus rollback_web_additions.sql), NEW_DSN the same backup after the new API's startup
migration (--mode migrated) or after that plus rollback_web_additions.sql (--mode rollback). `db` needs psycopg2 (run it inside the API image); `oldapi` only needs HTTP.
tests/db_compat.sh drives the whole run on a scratch PostgreSQL container.

Checks (`db`):
  schema   every object of the old database exists unchanged: tables, columns (name, type,
           NOT NULL, default, order), constraints, indexes, views, triggers, functions,
           extensions. In migrated mode the only change allowed on an old object is
           ALLOWED_COLUMNS / ALLOWED_INDEXES (nullable, no default); no foreign key may point at
           an old table and no trigger may sit on one (the old DB Tweak deletes rows directly).
           In rollback mode the two schemas must be identical.
  data     test_record (old columns only) and every old view hash to the same value.
  grafana  every rawSql of the provisioned dashboard returns the same rows.
  writer   the desktop uploader's own code (log_parser.write_record / upsert_record) inserts,
           detects the duplicate and upserts a sample log on both databases with identical
           results; the new row has upload_id NULL; mv_unit picks it up; then it is deleted.
Checks (`oldapi`): the old API's write endpoints (/api/ingest, /api/db-tweak/*) behave the same
on both databases. Its read endpoints are compared with tests/golden.py (GOLDEN_DIR=…).
"""
import argparse
import json
import sys
import urllib.request

ALLOWED_COLUMNS = {("test_record", "upload_id")}
ALLOWED_INDEXES = {"idx_test_record_upload_id"}

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""), flush=True)
    return ok


# ─── Catalog ────────────────────────────────────────────────────────────────

def describe(conn):
    cur = conn.cursor()

    def q(sql):
        cur.execute(sql)
        return cur.fetchall()

    rels = {r[0]: r[1] for r in q("""
        SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v', 'm', 'S', 'i', 'p')""")}
    cols = {}
    for rel, name, typ, notnull, default in q("""
        SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
               pg_get_expr(d.adbin, d.adrelid)
        FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v', 'm') AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY c.relname, a.attnum"""):
        cols.setdefault(rel, []).append((name, typ, notnull, default))
    cons = {r[0]: r[1:] for r in q("""
        SELECT con.conname, cl.relname, con.contype, pg_get_constraintdef(con.oid), rc.relname
        FROM pg_constraint con JOIN pg_class cl ON cl.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = cl.relnamespace
        LEFT JOIN pg_class rc ON rc.oid = con.confrelid
        WHERE n.nspname = 'public'""")}
    idx = {r[0]: (r[1], r[2]) for r in q("SELECT indexname, tablename, indexdef FROM pg_indexes WHERE schemaname = 'public'")}
    views = {r[0]: r[1] for r in q("SELECT viewname, definition FROM pg_views WHERE schemaname = 'public'")}
    trig = {r[0]: (r[1], r[2]) for r in q("""
        SELECT t.tgname, c.relname, pg_get_triggerdef(t.oid) FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND NOT t.tgisinternal""")}
    funcs = {f"{r[0]}({r[1]})": r[2] for r in q("""
        SELECT p.proname, pg_get_function_identity_arguments(p.oid), md5(pg_get_functiondef(p.oid))
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.prokind IN ('f', 'p')""")}
    exts = {r[0] for r in q("SELECT extname FROM pg_extension")}
    return {"rels": rels, "cols": cols, "cons": cons, "idx": idx, "views": views,
            "trig": trig, "funcs": funcs, "exts": exts}


def compare_schema(old, new, mode):
    kinds = {"r": "table", "v": "view", "m": "materialized view", "S": "sequence", "i": "index", "p": "table"}
    old_tables = {n for n, k in old["rels"].items() if k in ("r", "p")}

    missing = [n for n in old["rels"] if n not in new["rels"]]
    check("schema: every old object still exists", not missing, ", ".join(missing))
    changed_kind = [n for n, k in old["rels"].items() if n in new["rels"] and new["rels"][n] != k]
    check("schema: no old object changed kind", not changed_kind, ", ".join(changed_kind))

    for rel, ocols in sorted(old["cols"].items()):
        ncols = new["cols"].get(rel, [])
        nmap = {c[0]: c for c in ncols}
        diff = [c[0] for c in ocols if nmap.get(c[0]) != c]
        order_ok = [c[0] for c in ncols if c[0] in {o[0] for o in ocols}] == [o[0] for o in ocols]
        check(f"schema: {kinds.get(old['rels'].get(rel), 'relation')} {rel} keeps its {len(ocols)} columns (type, NOT NULL, default, order)",
              not diff and order_ok, ", ".join(diff) or ("" if order_ok else "order changed"))
        extra = [c for c in ncols if c[0] not in {o[0] for o in ocols}]
        if mode == "rollback":
            check(f"schema: {rel} has no added columns", not extra, ", ".join(c[0] for c in extra))
        else:
            bad = [c[0] for c in extra if (rel, c[0]) not in ALLOWED_COLUMNS or c[2] or c[3] is not None]
            check(f"schema: {rel} added columns are only the allowed nullable ones",
                  not bad, ", ".join(bad) or ", ".join(f"{c[0]} {c[1]} NULL" for c in extra) or "none")

    cons_diff = [n for n, v in old["cons"].items() if new["cons"].get(n) != v]
    check("schema: old constraints unchanged", not cons_diff, ", ".join(cons_diff))
    added_cons = [n for n, v in new["cons"].items() if n not in old["cons"] and v[0] in old_tables]
    check("schema: no constraint added on an old table", not added_cons, ", ".join(added_cons))
    fks = [n for n, v in new["cons"].items() if v[1] == "f" and v[3] in old_tables]
    check("schema: no foreign key points at an old table (old row deletes never blocked)", not fks, ", ".join(fks))

    idx_diff = [n for n, v in old["idx"].items() if new["idx"].get(n) != v]
    check("schema: old indexes unchanged", not idx_diff, ", ".join(idx_diff))
    added_idx = [n for n, v in new["idx"].items() if n not in old["idx"] and v[0] in old_tables]
    allowed = set() if mode == "rollback" else ALLOWED_INDEXES
    check("schema: indexes added on old tables are only the allowed ones",
          not [n for n in added_idx if n not in allowed], ", ".join(added_idx) or "none")

    view_diff = [n for n, v in old["views"].items() if new["views"].get(n) != v]
    check(f"schema: the {len(old['views'])} old views have identical definitions", not view_diff, ", ".join(view_diff))
    old_trig = [n for n, v in new["trig"].items() if v[0] in old_tables]
    check("schema: no trigger on an old table", not old_trig, ", ".join(old_trig))
    func_diff = [n for n, v in old["funcs"].items() if new["funcs"].get(n) != v]
    check("schema: old functions unchanged", not func_diff, ", ".join(func_diff))
    check("schema: no new extension required", new["exts"] <= old["exts"] | {"plpgsql"}, ", ".join(sorted(new["exts"] - old["exts"])))

    added = sorted(f"{kinds[k]} {n}" for n, k in new["rels"].items() if n not in old["rels"] and k in ("r", "p", "m", "v"))
    if mode == "rollback":
        extra_rels = sorted(n for n in new["rels"] if n not in old["rels"])
        check("schema: rollback leaves no added object", not extra_rels, ", ".join(extra_rels))
        same = all(old[k] == new[k] for k in ("rels", "cols", "cons", "idx", "views", "trig", "funcs"))
        check("schema: rollback schema identical to the old database", same)
    else:
        print(f"     added objects ({len(added)}): {', '.join(added)}")
    return added


# ─── Data ───────────────────────────────────────────────────────────────────

def table_hash(conn, table, columns, order="id"):
    cur = conn.cursor()
    collist = ", ".join(f'"{c}"' for c in columns)
    cur.execute(f'SELECT COUNT(*), md5(string_agg(md5(ROW({collist})::text), \'\' ORDER BY {order})) FROM "{table}"')
    return cur.fetchone()


def view_hash(conn, view):
    cur = conn.cursor()
    cur.execute(f'SELECT COUNT(*), md5(string_agg(h, \'\' ORDER BY h)) FROM (SELECT md5(v::text) AS h FROM "{view}" v) s')
    return cur.fetchone()


def compare_data(old_conn, new_conn, old):
    cols = [c[0] for c in old["cols"]["test_record"]]
    a, b = table_hash(old_conn, "test_record", cols), table_hash(new_conn, "test_record", cols)
    check(f"data: test_record identical on its {len(cols)} old columns", a == b, f"{a[0]:,} rows, md5 {a[1][:12]}…" if a == b else f"{a} vs {b}")
    for view in sorted(old["views"]):
        a, b = view_hash(old_conn, view), view_hash(new_conn, view)
        check(f"data: view {view} returns the same rows", a == b, f"{a[0]:,} rows" if a == b else f"{a} vs {b}")


def compare_grafana(old_conn, new_conn, path):
    with open(path, encoding="utf-8") as f:
        dash = json.load(f)
    sqls = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "rawSql" and isinstance(v, str):
                    sqls.append(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(dash)
    for i, sql in enumerate(sqls, 1):
        run = sql.replace("$__timeFilter(test_time)", "test_time >= '2026-01-01' AND test_time < '2027-01-01'")
        out = []
        for conn in (old_conn, new_conn):
            cur = conn.cursor()
            cur.execute(run)
            out.append([tuple(str(x) for x in r) for r in cur.fetchall()])
        check(f"grafana: panel query {i} returns the same rows", out[0] == out[1], f"{len(out[0])} rows · {sql[:60]}…")


# ─── Desktop uploader ───────────────────────────────────────────────────────

def refresh_mv(conn):
    """REFRESH … CONCURRENTLY cannot run inside a transaction block."""
    conn.commit()
    conn.autocommit = True
    try:
        conn.cursor().execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_unit")
    finally:
        conn.autocommit = False


def writer_test(old_conn, new_conn, parser_dir, sample):
    sys.path.insert(0, parser_dir)
    import log_parser  # the deployed desktop/batch parser, not the API's copy
    record = log_parser.ingest_file(sample)
    if not check("writer: the desktop parser reads the sample log", bool(record), sample):
        return
    rows = []
    for label, conn in (("old", old_conn), ("new", new_conn)):
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM test_record")
        before = cur.fetchone()[0]
        cur.execute("SELECT id FROM test_record WHERE file_hash = %s", (record["file_hash"],))
        if cur.fetchone():
            check(f"writer[{label}]: sample log not already in the database", False, record["file_hash"])
            return
        conn.commit()
        rid = log_parser.write_record(conn, dict(record))
        dup = log_parser.write_record(conn, dict(record))
        rid2 = log_parser.upsert_record(conn, dict(record))
        check(f"writer[{label}]: write_record inserts, a second call is a duplicate, upsert keeps the id",
              rid and dup is None and rid2 == rid, f"id {rid}, dup {dup}, upsert {rid2}")
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'test_record' AND table_schema = 'public'")
        names = {r[0] for r in cur.fetchall()}
        keep = sorted(names - {"id", "created_at", "upload_id"})
        cur.execute(f"SELECT {', '.join(keep)} FROM test_record WHERE id = %s", (rid,))
        rows.append(tuple(str(x) for x in cur.fetchone()))
        if "upload_id" in names:
            cur.execute("SELECT upload_id FROM test_record WHERE id = %s", (rid,))
            check(f"writer[{label}]: the desktop row has upload_id NULL", cur.fetchone()[0] is None)
        cur.execute("SELECT 1 FROM pg_matviews WHERE matviewname = 'mv_unit'")
        has_mv = cur.fetchone() is not None
        if has_mv:
            refresh_mv(conn)
            cur.execute("SELECT COUNT(*) FROM mv_unit WHERE sn = %s", (record["sn"],))
            check(f"writer[{label}]: mv_unit refresh picks up the desktop row", cur.fetchone()[0] == 1)
        cur.execute("DELETE FROM test_record WHERE id = %s", (rid,))
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM test_record")
        after = cur.fetchone()[0]
        if has_mv:
            refresh_mv(conn)
        check(f"writer[{label}]: the test row is removed again", after == before, f"{before:,} rows")
    check("writer: the inserted row is identical on both databases", len(rows) == 2 and rows[0] == rows[1])


def cmd_db(args):
    import psycopg2
    old_conn, new_conn = psycopg2.connect(args.old), psycopg2.connect(args.new)
    old, new = describe(old_conn), describe(new_conn)
    compare_schema(old, new, args.mode)
    if args.schema_only:
        return
    compare_data(old_conn, new_conn, old)
    if args.grafana:
        compare_grafana(old_conn, new_conn, args.grafana)
    if args.parser_dir and args.sample:
        writer_test(old_conn, new_conn, args.parser_dir, args.sample)


# ─── Old API write endpoints ────────────────────────────────────────────────

def post(base, path, body=None):
    req = urllib.request.Request(base + path, data=json.dumps(body or {}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def cmd_oldapi(args):
    cred = {"username": args.user, "password": args.password}
    seen = {}
    for label, base in (("old", args.old), ("new", args.new)):
        out = {}
        out["login"] = post(base, "/api/db-tweak/login", cred)
        out["dry"] = post(base, f"/api/ingest?path={args.sample_dir}&dry_run=true")
        out["ingest"] = post(base, f"/api/ingest?path={args.sample_dir}")
        out["again"] = post(base, f"/api/ingest?path={args.sample_dir}")
        out["wos"] = post(base, "/api/db-tweak/workorders", cred)
        recs = post(base, "/api/db-tweak/records", {**cred, "work_order": args.work_order, "limit": 50})
        out["records"] = recs
        ids = [r["id"] for r in (recs[1].get("records") or recs[1].get("items") or [])] if recs[0] == 200 and isinstance(recs[1], dict) else []
        out["delete"] = [post(base, "/api/db-tweak/delete-record", {**cred, "id": i}) for i in ids]
        out["backfill"] = post(base, "/api/admin/backfill-metadata?dry_run=true")
        ok = (out["login"][0] == 200 and out["ingest"][0] == 200 and out["ingest"][1].get("inserted") == args.expect
              and out["again"][1].get("inserted") == 0 and len(ids) == args.expect
              and all(d[0] == 200 and d[1].get("deleted") == 1 for d in out["delete"]))
        check(f"old API [{label} DB]: sign-in, ingest {args.expect} log(s), re-ingest = duplicates, list and delete them",
              ok, f"ingest {out['ingest'][1]}, records {len(ids)}, delete {[d[1].get('deleted') for d in out['delete']]}")
        seen[label] = out

    def strip(o):   # ids and timings differ between runs; everything else must match
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if k not in ("id", "elapsed_sec", "created_at", "db_name")}
        if isinstance(o, (list, tuple)):
            return [strip(v) for v in o]
        return o
    for key in ("login", "dry", "ingest", "again", "records", "backfill"):
        check(f"old API: {key} answers the same on both databases", strip(seen["old"][key]) == strip(seen["new"][key]),
              "" if strip(seen["old"][key]) == strip(seen["new"][key]) else f"{seen['old'][key]} vs {seen['new'][key]}"[:300])


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("db")
    d.add_argument("old")
    d.add_argument("new")
    d.add_argument("--mode", choices=("migrated", "rollback"), default="migrated")
    d.add_argument("--schema-only", action="store_true", help="compare the schemas only (OLD may hold no data)")
    d.add_argument("--grafana")
    d.add_argument("--parser-dir")
    d.add_argument("--sample")
    d.add_argument("--json")
    o = sub.add_parser("oldapi")
    o.add_argument("old")
    o.add_argument("new")
    o.add_argument("--sample-dir", required=True)
    o.add_argument("--work-order", required=True)
    o.add_argument("--expect", type=int, default=1)
    o.add_argument("--user", default="qc")
    o.add_argument("--password", default="qcpass")
    o.add_argument("--json")
    args = p.parse_args()
    (cmd_db if args.cmd == "db" else cmd_oldapi)(args)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} compatibility checks passed")
    if getattr(args, "json", None):
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump([{"check": n, "ok": ok, "detail": d} for n, ok, d in results], f, ensure_ascii=False, indent=1)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
