"""Locating a record's original log file on disk (DB Tweak download)."""
from pathlib import Path

import config
from errors import ApiError, Msg

APP_ROOT = Path(config.APP_DIR)
LOG_DIRS = ("logs", "WiFiTestLogs")


def resolve_downloadable_log_path(source_file: str) -> Path:
    raw = str(source_file or "").strip()
    if not raw:
        raise ApiError(404, Msg("此筆資料沒有來源檔名", "source_file is empty"), "not_found")

    original = Path(raw)
    normalized = raw.replace("\\", "/")
    name_only = Path(normalized).name
    roots = [APP_ROOT, Path.cwd()]

    candidates: list[Path] = [original] if original.is_absolute() else [r / original for r in roots]
    if name_only and name_only != raw and name_only != normalized:
        candidates += [r / raw for r in roots] + [r / d / raw for r in roots for d in LOG_DIRS]
    search_roots: list[Path] = []
    if name_only:
        candidates += [r / d / name_only for r in roots for d in LOG_DIRS]
        search_roots = [r / d for r in roots for d in LOG_DIRS] + roots

    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved

    seen: set[str] = set()
    for root in search_roots:
        key = str(root.resolve()) if root.exists() else str(root)
        if key in seen or not root.is_dir():
            continue
        seen.add(key)
        try:
            for matched in root.rglob(name_only):
                if matched.is_file():
                    return matched.resolve()
        except OSError:
            continue

    raise ApiError(404, Msg(f"找不到 log 檔案：{raw}", f"Log file not found for source: {raw}"), "not_found")
