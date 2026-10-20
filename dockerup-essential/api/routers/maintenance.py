"""Administrator-only maintenance: batch ingest from a folder inside the container and the
one-off metadata backfill. Both need an administrator session (services.auth.require_admin)."""
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query

import db
import log_parser
from errors import bad_request, Msg
from services import stats
from services.auth import require_admin

router = APIRouter(tags=["maintenance"], dependencies=[Depends(require_admin)])

GENERIC_PARENT_DIRS = ("downloads", "desktop", "documents", "logs", "tmp", "temp")
WARNING_KEYS = ("warn_missing_tp_24g", "warn_missing_tp_5g", "warn_missing_result_24g",
                "warn_missing_result_5g", "warn_missing_bt_result")


@router.post("/api/ingest")
def ingest(path: str = "/app/logs", dry_run: bool = False,
           duplicate_mode: str = Query(default="skip", pattern="^(skip|overwrite)$")) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise bad_request(Msg(f"路徑不存在：{path}", f"Path not found: {path}"))

    files = log_parser.scan_directory(str(target)) if target.is_dir() else [str(target)]
    if not files:
        return {"found": 0, "inserted": 0, "duplicates": 0, "errors": 0}

    counts = {"inserted": 0, "overwritten": 0, "would_insert": 0, "would_overwrite": 0, "duplicates": 0, "errors": 0}
    error_details: list[dict[str, str]] = []
    sanitized_files = sanitized_nul_bytes = 0
    warning_totals = {k: 0 for k in WARNING_KEYS}

    def note_error(file_path, message):
        counts["errors"] += 1
        if len(error_details) < 20:
            error_details.append({"file": str(file_path), "error": message})

    with db.db() as conn:
        with conn.cursor() as cur:
            for file_path in files:
                record = log_parser.ingest_file(file_path)
                if not record:
                    note_error(file_path, "Filename/parse mismatch")
                    continue
                for key in WARNING_KEYS:
                    warning_totals[key] += int(record.get(key, 0) or 0)
                nul = int(record.get("sanitized_nul_bytes", 0) or 0)
                if nul > 0:
                    sanitized_files += 1
                    sanitized_nul_bytes += nul

                if dry_run:
                    try:
                        cur.execute("SELECT 1 FROM test_record WHERE file_hash = %s LIMIT 1", (record.get("file_hash"),))
                        if cur.fetchone() is not None:
                            counts["would_overwrite" if duplicate_mode == "overwrite" else "duplicates"] += 1
                        else:
                            counts["would_insert"] += 1
                    except Exception as exc:
                        note_error(file_path, f"Dry-run DB check failed: {exc}")
                    continue

                try:
                    if duplicate_mode == "overwrite":
                        cur.execute("SELECT 1 FROM test_record WHERE file_hash = %s LIMIT 1", (record.get("file_hash"),))
                        existed_before = cur.fetchone() is not None
                        rid = log_parser.upsert_record(conn, record)
                        if rid and existed_before:
                            counts["overwritten"] += 1
                        elif rid:
                            counts["inserted"] += 1
                    else:
                        rid = log_parser.write_record(conn, record)
                        if rid:
                            counts["inserted"] += 1
                        else:
                            counts["duplicates"] += 1
                except Exception as exc:
                    note_error(file_path, f"DB write failed: {exc}")

    if not dry_run and (counts["inserted"] or counts["overwritten"]):
        stats.refresh(wait=True)

    return {
        "found": len(files),
        "inserted": counts["inserted"],
        "overwritten": counts["overwritten"],
        "would_insert": counts["would_insert"] if dry_run else counts["inserted"],
        "would_overwrite": counts["would_overwrite"] if dry_run else counts["overwritten"],
        "duplicates": counts["duplicates"],
        "errors": counts["errors"],
        "dry_run": dry_run,
        "duplicate_mode": duplicate_mode,
        "error_details": error_details,
        "sanitized_files": sanitized_files,
        "sanitized_nul_bytes": sanitized_nul_bytes,
        "warnings": warning_totals,
    }


@router.post("/api/admin/backfill-metadata")
def backfill_metadata(dry_run: bool = True) -> dict[str, Any]:
    """Rows from an earlier path-mapping rule: if product_model is a generic parent folder
    and work_order is set, promote work_order → product_model and clear work_order."""
    placeholders = ", ".join(["%s"] * len(GENERIC_PARENT_DIRS))
    where_clause = f"LOWER(COALESCE(product_model, '')) IN ({placeholders}) AND COALESCE(work_order, '') <> ''"
    rule = "generic parent folder product_model -> use work_order as product_model; clear work_order"

    with db.db() as conn:
        row = db.fetch_one(conn, f"SELECT COUNT(*) AS n FROM test_record WHERE {where_clause}", GENERIC_PARENT_DIRS)
        candidates = int(row["n"] or 0)
        if dry_run:
            return {"dry_run": True, "candidates": candidates, "rule": rule}
        updated = db.execute(conn, f"""
            UPDATE test_record SET product_model = work_order, work_order = NULL WHERE {where_clause}
        """, GENERIC_PARENT_DIRS)
    if updated:
        stats.refresh(wait=True)
    return {"dry_run": False, "candidates": candidates, "updated": int(updated or 0), "rule": rule}
