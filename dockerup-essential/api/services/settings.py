"""Dashboard settings the administrator changes on the Settings page.

Stored as one JSON document in app_meta (key "settings"), so no table is added. Missing keys fall
back to DEFAULTS, so older documents keep working when a setting is added.

    get()                     → the current settings (cached a few seconds per worker: for reading)
    load(conn, lock=True)     → the stored settings, read now (a save compares against these, never the cache)
    validate(changes, current)→ (merged settings, errors {field: Msg}) for a partial update
    save(conn, values)        → store and return the full settings
"""
import json
import re
import time
from typing import Any, Optional

import config
import db
from errors import Msg

KEY = "settings"
CACHE_SEC = 5

DEFAULTS: dict[str, Any] = {
    "yield_good": config.YIELD_GOOD,     # yield ≥ this → green
    "yield_warn": config.YIELD_WARN,     # ≥ this → amber, below → red
    "work_start": "07:00",               # single shift (Q3): the hours the line normally tests
    "work_end": "19:00",
    "active_minutes": 120,               # a test within this many minutes → "in progress"
    "idle_warn_minutes": 30,             # Today page: no new test for this long during working hours → amber
    "done_idle_hours": 24,               # no new test for this long → "done" (the final report is written then)
    "fail_streak": 3,                    # this many different units failing in a row → flag
    "tp_drop_pct": 20,                   # median throughput this far below the product's usual → flag
    "behind_pct": 20,                    # last hour this far below the hourly target → flag
    "uph_targets": {},                   # product → units per hour; unset = the product's history median
}

INT_RANGES = {"active_minutes": (10, 1440), "idle_warn_minutes": (5, 600), "done_idle_hours": (1, 720), "fail_streak": (2, 50),
              "tp_drop_pct": (5, 90), "behind_pct": (5, 90)}
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

_cache = {"at": 0.0, "value": None, "version": None}


def _read(conn) -> tuple[dict[str, Any], Optional[str]]:
    row = db.fetch_one(conn, "SELECT value, updated_at FROM app_meta WHERE key = %s", (KEY,))
    stored = {}
    if row and row["value"]:
        try:
            stored = json.loads(row["value"])
        except ValueError:
            stored = {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}, (row["updated_at"].isoformat() if row else None)


def get() -> dict[str, Any]:
    now = time.time()
    if _cache["value"] is not None and now - _cache["at"] < CACHE_SEC:
        return _cache["value"]
    with db.db() as conn:
        value, ver = _read(conn)
    _cache.update(at=now, value=value, version=ver)
    return value


def load(conn, lock: bool = True) -> dict[str, Any]:
    """The stored settings, read in this transaction; with lock, concurrent saves wait their turn."""
    if lock:
        db.execute(conn, "SELECT pg_advisory_xact_lock(%s)", (config.SETTINGS_LOCK_KEY,))
    return _read(conn)[0]


def version() -> str:
    """Changes whenever the settings are saved (part of cache keys)."""
    get()
    return _cache["version"] or "defaults"


def thresholds() -> dict[str, float]:
    s = get()
    return {"yield_good": float(s["yield_good"]), "yield_warn": float(s["yield_warn"])}


def validate(changes: dict[str, Any], current: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Msg]]:
    """Check a partial update against the current settings. Returns (merged settings, errors)."""
    merged = {**current}
    errors: dict[str, Msg] = {}
    for key, value in changes.items():
        if key not in DEFAULTS:
            continue
        if key in ("yield_good", "yield_warn"):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < float(value) <= 100:
                errors[key] = Msg("請輸入 0 到 100 之間的百分比。", "Enter a percentage between 0 and 100.")
                continue
            merged[key] = round(float(value), 2)
        elif key in ("work_start", "work_end"):
            if not isinstance(value, str) or not TIME_RE.match(value):
                errors[key] = Msg("時間格式為 HH:MM,例如 07:00。", "Use HH:MM, for example 07:00.")
                continue
            merged[key] = value
        elif key in INT_RANGES:
            lo, hi = INT_RANGES[key]
            if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
                errors[key] = Msg(f"請輸入 {lo} 到 {hi} 之間的整數。", f"Enter a whole number from {lo} to {hi}.")
                continue
            merged[key] = value
        elif key == "uph_targets":
            if not isinstance(value, dict):
                errors[key] = Msg("格式不正確。", "Invalid format.")
                continue
            clean = {}
            for product, target in value.items():
                if target in (None, ""):
                    continue      # cleared → back to the history median
                if isinstance(target, bool) or not isinstance(target, (int, float)) or not 0 < float(target) <= 10000:
                    errors[key] = Msg(f"{product}:每小時目標請輸入 1 到 10000。", f"{product}: enter an hourly target from 1 to 10000.")
                    break
                clean[str(product)[:128]] = round(float(target), 1)
            else:
                merged[key] = clean
    if not errors and merged["yield_warn"] >= merged["yield_good"]:
        errors["yield_warn"] = Msg("「注意」門檻必須低於「正常」門檻。", "The amber threshold must be below the green one.")
    if not errors and merged["work_start"] >= merged["work_end"]:
        errors["work_end"] = Msg("下班時間必須晚於上班時間。", "The end time must be after the start time.")
    return merged, errors


def save(conn, values: dict[str, Any]) -> dict[str, Any]:
    stored = {k: values[k] for k in DEFAULTS}
    db.execute(conn, """
        INSERT INTO app_meta (key, value, updated_at) VALUES (%s, %s, NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
    """, (KEY, json.dumps(stored, ensure_ascii=False, sort_keys=True)))
    return stored


def clear_cache():
    """Call after the saving transaction committed (this worker re-reads at once; the other within CACHE_SEC)."""
    _cache.update(at=0.0, value=None)
