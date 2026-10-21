"""Web Log Upload back end: temp storage, safe zip extraction, facts, DB context and the commit thread.

Flow   analyze (zip) or folder/start → files… → finish   ⇒ facts (nothing written to test_record)
       validate / chat                                    ⇒ rules R1–R16 (services/upload_rules.py)
       commit                                             ⇒ NDJSON progress from a background thread
       revert                                             ⇒ delete only the rows this batch inserted

Writing follows the desktop tool's "Database Upload" (wifi_stress_log_analyzer.on_upload_to_db):
log_parser.ingest_file() per file, product/work order from the user (never from paths),
INSERT … ON CONFLICT (file_hash) DO NOTHING (duplicates are skipped, never overwritten), and a
per-file result list (inserted / duplicate / error / cancelled) — here kept in upload_log_item
instead of db_upload_log_*.csv. The one difference: rows carry upload_id and commits happen
every 50 files instead of after each file.

Temp layout   UPLOAD_TMP_DIR/<upload_id>/files/<idx>/<original basename>   (logs only)
              UPLOAD_TMP_DIR/<upload_id>/manifest.json                      (every received item)
File names from the browser or a zip are untrusted: only the basename is used, only after
it matches the log name format, and every path is built from server-side ids.
"""
import collections
import hashlib
import io
import json
import logging
import os
import queue
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from datetime import date, datetime
from typing import Any, Optional

import psycopg2.extras

import config
import db
import log_parser
from errors import ApiError, Msg
from services import auth_utils, products, stats
from services import upload_rules as rules

log = logging.getLogger("wifi.upload")

UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")
LOG_NAME_RE = log_parser.FILENAME_RE
IGNORED_LIST_MAX = 50
COMMIT_EVERY = 50
ITEM_COLUMNS = ("upload_id", "idx", "filename", "status", "message", "record_id")

# test_record insert = log_parser.DB_INSERT's column list + upload_id (the web upload's one addition).
_RECORD_COLUMNS = (
    "product_model", "work_order", "sn", "wifi_mac", "bt_mac", "test_time", "result",
    "avg_24g", "min_24g", "max_24g", "std_24g", "samples_24g", "rssi_before_24g", "rssi_after_24g", "rssi_delta_24g",
    "attempt_count_24g", "band_result_24g",
    "avg_5g", "min_5g", "max_5g", "std_5g", "samples_5g", "rssi_before_5g", "rssi_after_5g", "rssi_delta_5g",
    "attempt_count_5g", "band_result_5g",
    "bt_avg_latency", "bt_min_latency", "bt_max_latency", "bt_loss_rate", "bt_result",
    "raw_log", "file_hash", "source_file", "upload_id",
)
INSERT_SQL = (f"INSERT INTO test_record ({', '.join(_RECORD_COLUMNS)}) VALUES ({', '.join(f'%({c})s' for c in _RECORD_COLUMNS)}) "
              f"ON CONFLICT (file_hash) DO NOTHING RETURNING id")


class LimitError(Exception):
    """A safety limit was hit; .msg is the user-facing Msg."""

    def __init__(self, msg: Msg, code: str):
        super().__init__(str(msg))
        self.msg = msg
        self.code = code


# ─── Temp storage ────────────────────────────────────────────────────────────

def valid_upload_id(upload_id) -> bool:
    return isinstance(upload_id, str) and bool(UPLOAD_ID_RE.match(upload_id))


def upload_dir(upload_id: str) -> str:
    return os.path.join(config.UPLOAD_TMP_DIR, upload_id)


def new_upload_id() -> str:
    upload_id = uuid.uuid4().hex
    os.makedirs(os.path.join(upload_dir(upload_id), "files"), exist_ok=True)
    return upload_id


def remove_upload_files(upload_id: str) -> None:
    shutil.rmtree(upload_dir(upload_id), ignore_errors=True)


def cleanup_stale() -> None:
    if not os.path.isdir(config.UPLOAD_TMP_DIR):
        return
    cutoff = time.time() - config.UPLOAD_TTL_SEC
    for name in os.listdir(config.UPLOAD_TMP_DIR):
        path = os.path.join(config.UPLOAD_TMP_DIR, name)
        try:
            if os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) else os.remove(path)
        except OSError:
            pass


def _manifest_path(upload_id: str) -> str:
    return os.path.join(upload_dir(upload_id), "manifest.json")


def manifest_exists(upload_id: str) -> bool:
    """False once the temp files are gone (TTL clean-up, server restart without the volume)."""
    return os.path.isfile(_manifest_path(upload_id))


def load_manifest(upload_id: str) -> dict:
    try:
        with open(_manifest_path(upload_id), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"items": [], "bytes": 0, "zips": 0, "limits": {}}


def save_manifest(upload_id: str, manifest: dict) -> None:
    tmp = _manifest_path(upload_id) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)
    os.replace(tmp, _manifest_path(upload_id))


# ─── Receiving files ─────────────────────────────────────────────────────────

class Receiver:
    """Adds files (from a zip or a folder batch) to an upload's manifest, enforcing the limits."""

    def __init__(self, upload_id: str, manifest: dict):
        self.upload_id = upload_id
        self.m = manifest
        self.m.setdefault("items", [])
        self.m.setdefault("bytes", 0)
        self.m.setdefault("zips", 0)
        self.m.setdefault("limits", {})

    def _check_limits(self, add_bytes: int) -> bool:
        """False when the next file would exceed a limit (the limit is recorded, the file dropped)."""
        if len(self.m["items"]) >= config.UPLOAD_MAX_FILES:
            self.m["limits"]["files"] = True
            return False
        if self.m["bytes"] + add_bytes > config.UPLOAD_MAX_EXTRACT_BYTES:
            self.m["limits"]["bytes"] = True
            return False
        return True

    def _ignore(self, name: str, reason: str, size: int = 0) -> None:
        self.m["items"].append({"idx": len(self.m["items"]), "name": name, "kind": "ignored", "reason": reason, "size": size})

    @staticmethod
    def _unsafe(name: str) -> bool:
        parts = name.replace("\\", "/").split("/")
        return name.startswith(("/", "\\")) or ":" in parts[0] or any(p in ("..", "") for p in parts[:-1]) or parts[-1] in ("..", "")

    def add_stream(self, name: str, stream, size_hint: int = 0, depth: int = 0) -> None:
        """Classify one file by name; store it only when it is a log (or a zip to expand)."""
        display = name.replace("\\", "/")
        base = display.rsplit("/", 1)[-1]
        if self._unsafe(display):
            self._ignore(display, "unsafe_path", size_hint)
            return
        if base.lower().endswith(".zip"):
            if depth >= config.UPLOAD_ZIP_DEPTH:
                self._ignore(display, "zip_too_deep", size_hint)
                return
            self._expand_zip(display, stream, depth + 1)
            return
        if not LOG_NAME_RE.fullmatch(base):
            self._ignore(display, "not_log_name", size_hint)
            return
        try:
            meta = log_parser.parse_filename(base) or {}
        except ValueError:          # the right shape, but no such date/time (20261340_250000_…)
            self._ignore(display, "bad_log_date", size_hint)
            return
        if not self._check_limits(size_hint):
            self._ignore(display, "limit", size_hint)
            return
        idx = len(self.m["items"])
        folder = os.path.join(upload_dir(self.upload_id), "files", f"{idx:06d}")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, base)
        digest = hashlib.sha256()
        size = 0
        has_content = False
        with open(path, "wb") as out:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if self.m["bytes"] + size > config.UPLOAD_MAX_EXTRACT_BYTES:
                    out.close()
                    shutil.rmtree(folder, ignore_errors=True)
                    self.m["limits"]["bytes"] = True
                    self._ignore(display, "limit", size)
                    return
                has_content = has_content or bool(chunk.strip(b"\x00\r\n\t "))
                digest.update(chunk)
                out.write(chunk)
        if not has_content:
            # Empty or NUL-only (a crashed write): no test data, yet the name says PASS/FAIL.
            # The desktop tool imports these as units; the web upload lists them and skips them.
            shutil.rmtree(folder, ignore_errors=True)
            self._ignore(display, "empty_log", size)
            return
        self.m["bytes"] += size
        self.m["items"].append({
            "idx": idx, "name": display, "kind": "log", "size": size, "hash": digest.hexdigest(),
            "stored": os.path.join("files", f"{idx:06d}", base),
            "sn": meta.get("sn"), "test_time": meta["test_time"].isoformat(sep=" ") if meta.get("test_time") else None,
            "result": meta.get("result"),
        })

    def _expand_zip(self, display: str, stream, depth: int) -> None:
        """Expand a zip member/file into the manifest. Nested zips go through a temp file (never fully in memory)."""
        with tempfile.NamedTemporaryFile(dir=upload_dir(self.upload_id), suffix=".zip", delete=False) as tmp:
            total = 0
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > config.UPLOAD_MAX_UPLOAD_BYTES:
                    tmp.close()
                    os.remove(tmp.name)
                    raise LimitError(Msg("壓縮檔超過 512 MB 上限", "The zip exceeds the 512 MB limit."), "too_large")
                tmp.write(chunk)
            tmp_path = tmp.name
        try:
            self.add_zip_file(tmp_path, display, depth)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def add_zip_file(self, zip_path: str, display: str, depth: int) -> None:
        try:
            zf = zipfile.ZipFile(zip_path)
        except zipfile.BadZipFile:
            if depth <= 1:
                raise
            self._ignore(display, "bad_zip")
            return
        with zf:
            self.m["zips"] += 1
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if (info.external_attr >> 16) & 0o170000 == 0o120000:      # symlink
                    self._ignore(f"{display}/{info.filename}", "symlink", info.file_size)
                    continue
                if info.compress_size and info.file_size > 65536 and info.file_size / info.compress_size > config.UPLOAD_MAX_ZIP_RATIO:
                    raise LimitError(Msg(f"壓縮檔內 {info.filename} 的壓縮比超過 {config.UPLOAD_MAX_ZIP_RATIO} 倍，已拒絕",
                                         f"{info.filename} in the zip has a compression ratio above {config.UPLOAD_MAX_ZIP_RATIO}; rejected."),
                                     "zip_ratio")
                with zf.open(info) as member:
                    self.add_stream(f"{display}/{info.filename}", member, info.file_size, depth)


def receive_zip(upload_id: str, fileobj, name: str) -> int:
    """Store an uploaded zip under the upload dir and expand it. Returns the zip size."""
    zip_path = os.path.join(upload_dir(upload_id), "source.zip")
    size = 0
    with open(zip_path, "wb") as out:
        while True:
            chunk = fileobj.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > config.UPLOAD_MAX_UPLOAD_BYTES:
                raise LimitError(Msg("壓縮檔超過 512 MB 上限", "The zip exceeds the 512 MB limit."), "too_large")
            out.write(chunk)
    manifest = load_manifest(upload_id)
    receiver = Receiver(upload_id, manifest)
    try:
        receiver.add_zip_file(zip_path, name, 1)
    finally:
        save_manifest(upload_id, manifest)
        try:
            os.remove(zip_path)
        except OSError:
            pass
    return size


def receive_folder_batch(upload_id: str, files: list, paths: list[str]) -> dict:
    """One batch of a folder upload (≤ UPLOAD_BATCH_MAX_FILES). Paths are display-only."""
    manifest = load_manifest(upload_id)
    receiver = Receiver(upload_id, manifest)
    received = 0
    try:
        for i, f in enumerate(files):
            name = paths[i] if i < len(paths) and paths[i] else (f.filename or f"file{i}")
            receiver.add_stream(name, f.file, 0, 0)
            received += 1
    finally:
        save_manifest(upload_id, manifest)
    return {"received": received, "items": len(manifest["items"]), "limits": manifest["limits"]}


# ─── Facts ───────────────────────────────────────────────────────────────────

def _infer(manifest: dict, source_name: str, registry: products.Registry) -> tuple[Optional[str], Optional[str]]:
    """(work order, product) suggested by the package: counted over every name, never trusted."""
    names = [source_name] + [it["name"] for it in manifest["items"]]
    wos: collections.Counter = collections.Counter()
    pms: collections.Counter = collections.Counter()
    for name in names:
        for part in name.replace("\\", "/").split("/"):
            for wo in rules.find_work_orders(part):
                wos[wo] += 1
            for token in products.TOKEN_RE.findall(part):
                res = registry.resolve(token)
                if res["status"] == "known":
                    pms[res["canonical"]] += 1
                elif res["status"] in ("new", "blocked_same_tn") and res.get("proposal"):
                    pms[res["proposal"]] += 1
    return (wos.most_common(1)[0][0] if wos else None), (pms.most_common(1)[0][0] if pms else None)


def build_facts(conn, upload_id: str, source_kind: str, source_name: str, source_bytes: int) -> dict:
    manifest = load_manifest(upload_id)
    logs = [it for it in manifest["items"] if it["kind"] == "log"]
    ignored = [it for it in manifest["items"] if it["kind"] == "ignored"]
    hashes = [it["hash"] for it in logs]
    sns = {it["sn"] for it in logs if it.get("sn")}
    times = sorted(it["test_time"] for it in logs if it.get("test_time"))
    registry = products.load(conn)
    inferred_wo, inferred_pm = _infer(manifest, source_name, registry)
    existing = db.fetch_all(conn, """
        SELECT COALESCE(work_order, '') AS work_order, COALESCE(product_model, '') AS product_model, COUNT(*) AS n
        FROM test_record WHERE file_hash = ANY(%s) GROUP BY 1, 2 ORDER BY n DESC
    """, (list(set(hashes)),)) if hashes else []
    return {
        "source_kind": source_kind, "source_name": source_name, "source_bytes": source_bytes,
        "total_files": len(manifest["items"]), "logs": len(logs), "zips": manifest.get("zips", 0),
        "unique_sn": len(sns), "pass": sum(1 for it in logs if it.get("result")), "fail": sum(1 for it in logs if it.get("result") is False),
        "first_test": times[0] if times else None, "last_test": times[-1] if times else None,
        "dup_in_batch": len(hashes) - len(set(hashes)),
        "ignored": [{"name": it["name"], "reason": it.get("reason")} for it in ignored[:IGNORED_LIST_MAX]],
        "ignored_total": len(ignored), "limits": manifest.get("limits", {}),
        "inferred_wo": inferred_wo, "inferred_product": inferred_pm,
        "existing": [{"work_order": r["work_order"], "product_model": r["product_model"], "n": int(r["n"])} for r in existing],
        "existing_total": sum(int(r["n"]) for r in existing),
    }


# ─── DB context + validation ─────────────────────────────────────────────────

def fetch_upload(conn, upload_id):
    if not valid_upload_id(upload_id):
        return None
    return db.fetch_one(conn, "SELECT * FROM upload_log WHERE id = %s", (upload_id,))


def owned_upload(conn, upload_id, identity, not_found=Msg("找不到這個上傳批次", "Upload batch not found.")):
    row = fetch_upload(conn, upload_id)
    if not row:
        raise ApiError(404, not_found, "no_upload")
    if not auth_utils.can_access_upload(identity, row["username"]):
        raise ApiError(403, Msg("這不是你的上傳批次", "This upload batch belongs to another user."), "not_owner")
    return row


def next_nowo(conn, today: Optional[date] = None) -> str:
    """NOWO-<today><seq>: the first sequence not used by test_record, wo_plan or a running/finished upload."""
    today = today or date.today()
    prefix = f"{rules.NOWO_PREFIX}-{today:%y%m%d}"
    rows = db.fetch_all(conn, """
        SELECT work_order FROM test_record WHERE work_order LIKE %s
        UNION SELECT work_order FROM wo_plan WHERE work_order LIKE %s
        UNION SELECT work_order FROM upload_log WHERE work_order LIKE %s AND status IN ('running', 'done', 'cancelled', 'failed')
    """, (prefix + "%", prefix + "%", prefix + "%"))
    used = {int(r["work_order"][-3:]) for r in rows if r["work_order"] and r["work_order"][-3:].isdigit()}
    seq = 1
    while seq in used:
        seq += 1
    return rules.nowo_value(today, min(seq, 999))


def resolve_fields(conn, registry: products.Registry, work_order_in, product_in, planned_in, today: Optional[date] = None) -> dict:
    """Normalise the three inputs. The bare NOWO is expanded to its preview value (final value assigned at commit)."""
    wo_check = rules.check_wo(work_order_in)
    nowo_preview = None
    if wo_check["code"] == "nowo":
        nowo_preview = next_nowo(conn, today)
    work_order = nowo_preview if nowo_preview else (wo_check["value"] if wo_check["code"] == "ok" else None)
    product = registry.resolve(product_in or "")
    return {"wo_input": work_order_in, "wo_check": wo_check, "work_order": work_order, "nowo_preview": nowo_preview,
            "product": product, "planned_raw": planned_in, "planned_qty": rules.parse_quantity(planned_in)}


def db_context(conn, facts: dict, fields: dict, manifest: Optional[dict] = None) -> dict:
    work_order = fields.get("work_order")
    canonical = fields["product"].get("canonical") or (fields["product"].get("proposal") if fields["product"].get("status") == "new" else None)
    ctx: dict[str, Any] = {"today": date.today(), "nowo_preview": fields.get("nowo_preview"),
                           "existing_same": 0, "existing_other": [], "wo_products": [], "prev_plan": None, "cumulative_sn": None}
    for r in facts.get("existing") or []:
        if work_order and canonical and r["work_order"] == work_order and r["product_model"] == canonical:
            ctx["existing_same"] += r["n"]
        else:
            ctx["existing_other"].append(r)
    if work_order:
        ctx["wo_products"] = [r["product_model"] for r in db.fetch_all(
            conn, "SELECT DISTINCT product_model FROM test_record WHERE work_order = %s", (work_order,))]
        plan = db.fetch_one(conn, "SELECT planned_qty FROM wo_plan WHERE work_order = %s", (work_order,))
        ctx["prev_plan"] = plan["planned_qty"] if plan else None
        db_sns = {r["sn"] for r in db.fetch_all(conn, "SELECT DISTINCT sn FROM test_record WHERE work_order = %s", (work_order,))}
        batch_sns = {it["sn"] for it in (manifest or {}).get("items", []) if it["kind"] == "log" and it.get("sn")}
        ctx["cumulative_sn"] = len(db_sns | batch_sns)
    return ctx


def validate(conn, row: dict, work_order_in, product_in, planned_in) -> tuple[dict, list]:
    """(fields, issues) for one upload_log row and the three inputs."""
    registry = products.load(conn)
    fields = resolve_fields(conn, registry, work_order_in, product_in, planned_in)
    manifest = load_manifest(row["id"])
    ctx = db_context(conn, row["facts"], fields, manifest)
    return fields, rules.validate(row["facts"], fields, ctx)


def validation_payload(fields: dict, issues: list) -> dict:
    product = fields["product"]
    return {
        **rules.issue_summary(issues),
        "resolved": {
            "work_order": fields.get("work_order"),
            "product": product.get("canonical") or (product.get("proposal") if product.get("status") == "new" else None),
            "product_status": product.get("status"),
            "planned_qty": fields.get("planned_qty"),
            "new_product": product.get("status") == "new",
        },
    }


# ─── NDJSON helpers ──────────────────────────────────────────────────────────

def ndjson(event) -> str:
    return json.dumps(event, ensure_ascii=False, default=str) + "\n"


def ndjson_events(events: "queue.Queue"):
    while True:
        try:
            event = events.get(timeout=15)
        except queue.Empty:
            yield ndjson({"type": "heartbeat"})
            continue
        yield ndjson(event)
        if event["type"] in ("done", "error"):
            break


# ─── Commit thread ───────────────────────────────────────────────────────────

def _flush_items(cur, items: list) -> None:
    if not items:
        return
    psycopg2.extras.execute_values(cur, """
        INSERT INTO upload_log_item (upload_id, idx, filename, status, message, record_id) VALUES %s
        ON CONFLICT (upload_id, idx) DO UPDATE SET status = EXCLUDED.status, message = EXCLUDED.message, record_id = EXCLUDED.record_id
    """, items)
    items.clear()


def run_commit(conn, events: "queue.Queue", upload_id: str, work_order: str, product: str, planned_qty: int, user: dict) -> None:
    """Insert every log of the batch with upload_id; commit every 50. Owns `conn` (and its advisory lock)."""
    manifest = load_manifest(upload_id)
    logs = [it for it in manifest["items"] if it["kind"] == "log"]
    st = {"total": len(logs), "done": 0, "uploaded": 0, "skipped": 0, "failed": 0, "cancelled": 0, "nul_files": 0, "nul_bytes": 0}
    failures: list[dict] = []
    items: list[tuple] = []
    t0 = time.time()
    base = upload_dir(upload_id)
    try:
        cur = conn.cursor()
        events.put({"type": "start", "upload_id": upload_id, "work_order": work_order, "product": product, "total": st["total"]})
        cancelled = False
        for n, it in enumerate(logs, 1):
            if cancelled:
                st["cancelled"] += 1
                items.append((upload_id, it["idx"], it["name"], "cancelled", "cancelled by user", None))
                continue
            path = os.path.join(base, it["stored"])
            cur.execute("SAVEPOINT rec")
            try:
                record = log_parser.ingest_file(path)
                if not record:
                    raise ValueError("Filename/parse mismatch")
                record["product_model"] = product
                record["work_order"] = work_order
                record["upload_id"] = upload_id
                for k in ("bt_avg_latency", "bt_min_latency", "bt_max_latency", "bt_loss_rate", "bt_result"):
                    record.setdefault(k, None)
                nul = int(record.get("sanitized_nul_bytes") or 0)
                if nul:
                    st["nul_files"] += 1
                    st["nul_bytes"] += nul
                cur.execute(INSERT_SQL, record)
                row = cur.fetchone()
                cur.execute("RELEASE SAVEPOINT rec")
                if row:
                    st["uploaded"] += 1
                    items.append((upload_id, it["idx"], it["name"], "inserted", f"id={row['id']}", row["id"]))
                else:
                    st["skipped"] += 1
                    items.append((upload_id, it["idx"], it["name"], "duplicate", "file_hash already in database", None))
            except Exception as exc:
                cur.execute("ROLLBACK TO SAVEPOINT rec")
                st["failed"] += 1
                message = str(exc)[:300]
                items.append((upload_id, it["idx"], it["name"], "error", message, None))
                if len(failures) < 50:
                    failures.append({"filename": it["name"], "error": message})
            st["done"] += 1
            if n % COMMIT_EVERY == 0 or n == st["total"]:
                _flush_items(cur, items)
                conn.commit()
                events.put({"type": "progress", **st})
                cur.execute("SELECT cancel_requested FROM upload_log WHERE id = %s", (upload_id,))
                flag = cur.fetchone()
                cancelled = bool(flag and flag["cancel_requested"])
        _flush_items(cur, items)
        status = "cancelled" if cancelled else "done"
        if not cancelled:
            cur.execute("""
                INSERT INTO wo_plan (work_order, product_model, planned_qty, updated_by, updated_at, upload_id)
                VALUES (%s, %s, %s, %s, NOW(), %s)
                ON CONFLICT (work_order) DO UPDATE
                SET product_model = EXCLUDED.product_model, planned_qty = EXCLUDED.planned_qty,
                    updated_by = EXCLUDED.updated_by, updated_at = NOW(), upload_id = EXCLUDED.upload_id
            """, (work_order, product, planned_qty, user["username"], upload_id))
        elapsed = round(time.time() - t0, 1)
        cur.execute("""
            UPDATE upload_log
            SET status = %s, finished_at = NOW(), uploaded = %s, skipped = %s, failed = %s, failures = %s, elapsed_sec = %s,
                cancel_requested = FALSE
            WHERE id = %s
        """, (status, st["uploaded"], st["skipped"], st["failed"], psycopg2.extras.Json(failures), elapsed, upload_id))
        conn.commit()
        remove_upload_files(upload_id)
        _after_change()
        events.put({"type": "done", "status": status, **st, "elapsed_sec": elapsed, "failures": failures})
    except Exception as exc:
        log.exception("log upload %s failed", upload_id)
        try:
            conn.rollback()
            cur = conn.cursor()
            _flush_items(cur, items)
            cur.execute("""
                UPDATE upload_log
                SET status = 'failed', finished_at = NOW(), uploaded = %s, skipped = %s, failed = %s, failures = %s,
                    elapsed_sec = %s, error = %s, cancel_requested = FALSE
                WHERE id = %s
            """, (st["uploaded"], st["skipped"], st["failed"], psycopg2.extras.Json(failures),
                  round(time.time() - t0, 1), str(exc)[:500], upload_id))
            conn.commit()
        except Exception:
            pass
        _after_change()
        events.put({"type": "error", "message": "上傳中斷，已寫入的資料可撤銷或重新上傳",
                    "message_en": "Upload interrupted. Rows already written can be undone or uploaded again.", **st})
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _after_change() -> None:
    try:
        stats.refresh(wait=True)
    except Exception:
        log.exception("statistics refresh after upload failed")


# ─── Revert ──────────────────────────────────────────────────────────────────

def revert(conn, row: dict, user: dict) -> int:
    """Delete only the rows this batch inserted; recompute the work order's plan; drop a new product left without data.

    Only a plan this batch set is recomputed: it becomes the planned quantity of the newest finished,
    not-undone batch (so batches can be undone in any order) or the administrator's value
    (wo_plan.admin_qty), whichever was set later; with neither the plan row disappears. A plan set later (by another batch or the
    administrator) is left alone."""
    upload_id = row["id"]
    reverted = db.execute(conn, "DELETE FROM test_record WHERE upload_id = %s", (upload_id,))
    db.execute(conn, """
        UPDATE upload_log SET status = 'reverted', reverted = %s, reverted_by = %s, reverted_at = NOW() WHERE id = %s
    """, (reverted, user["username"], upload_id))
    wo = row["work_order"]
    current = db.fetch_one(conn, "SELECT upload_id, admin_qty, admin_at FROM wo_plan WHERE work_order = %s", (wo,)) if wo else None
    if wo and current and current["upload_id"] == upload_id:
        newest = db.fetch_one(conn, """
            SELECT id, planned_qty, product_model, username, finished_at FROM upload_log
            WHERE work_order = %s AND status = 'done' AND planned_qty IS NOT NULL
            ORDER BY finished_at DESC NULLS LAST, created_at DESC LIMIT 1
        """, (wo,))
        admin_later = current["admin_qty"] is not None and (
            not newest or not newest["finished_at"] or (current["admin_at"] and current["admin_at"] > newest["finished_at"]))
        if newest and not admin_later:
            db.execute(conn, """
                INSERT INTO wo_plan (work_order, product_model, planned_qty, updated_by, updated_at, upload_id)
                VALUES (%s, %s, %s, %s, NOW(), %s)
                ON CONFLICT (work_order) DO UPDATE
                SET product_model = EXCLUDED.product_model, planned_qty = EXCLUDED.planned_qty,
                    updated_by = EXCLUDED.updated_by, updated_at = NOW(), upload_id = EXCLUDED.upload_id
            """, (wo, newest["product_model"], newest["planned_qty"], user["username"], newest["id"]))
        elif admin_later:
            db.execute(conn, """
                UPDATE wo_plan SET planned_qty = admin_qty, upload_id = NULL, updated_by = admin_by, updated_at = NOW()
                WHERE work_order = %s
            """, (wo,))
        else:
            db.execute(conn, "DELETE FROM wo_plan WHERE work_order = %s", (wo,))
    if row.get("new_product") and row.get("product_model"):
        left = db.fetch_one(conn, "SELECT COUNT(*) AS n FROM test_record WHERE product_model = %s", (row["product_model"],))["n"]
        if not left:
            db.execute(conn, "DELETE FROM product_registry WHERE canonical = %s AND created_upload_id = %s", (row["product_model"], upload_id))
    return reverted


# ─── Per-file results (the desktop db_upload_log_*.csv / .txt) ───────────────

def items_text(rows: list[dict], fmt: str, upload_id: str, when: datetime) -> str:
    if fmt == "csv":
        out = io.StringIO()
        out.write("Index,Filename,Status,Message\n")
        for r in rows:
            name = str(r["filename"] or "").replace('"', '""')
            msg = str(r["message"] or "").replace('"', '""')
            out.write(f"{r['idx'] + 1},\"{name}\",{r['status']},\"{msg}\"\n")
        return out.getvalue()
    counts = collections.Counter(r["status"] for r in rows)
    lines = [f"Web upload {upload_id}  {when:%Y-%m-%d %H:%M:%S}",
             f"inserted={counts.get('inserted', 0)} duplicate={counts.get('duplicate', 0)} error={counts.get('error', 0)} cancelled={counts.get('cancelled', 0)}", ""]
    lines += [f"{r['idx'] + 1}\t{r['filename']}\t{r['status']}\t{r['message'] or ''}" for r in rows]
    return "\n".join(lines) + "\n"
