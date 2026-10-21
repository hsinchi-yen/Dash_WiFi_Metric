"""Settings page and work-order status.

GET /api/settings          settings + every product's hourly rate (history median, override, the one in use)
PUT /api/settings          administrator: partial update (audited); statuses and notes follow at once
GET /api/wo-status         status + one-line note of every work order (the Work Orders page uses
                           /api/work-order-summary, which carries the same `status` per row)
GET /api/wo-notes/queue    how many notes are stored / still to write
"""
from typing import Any

from fastapi import APIRouter, Depends, Request

import db
from errors import ApiError
from schemas import SettingsBody
from services import auth, settings, wo_notes, wo_status

router = APIRouter(tags=["settings"])


def _payload() -> dict[str, Any]:
    s = settings.get()
    targets = s.get("uph_targets") or {}
    uph = wo_status.product_uph()
    products = []
    for product in sorted(set(uph) | set(targets)):
        median = (uph.get(product) or {}).get("median")
        override = targets.get(product)
        products.append({"product_model": product, "history_median": median,
                         "work_orders": (uph.get(product) or {}).get("work_orders", 0),
                         "override": override, "in_use": override or median,
                         "source": "setting" if override else ("history" if median else None)})
    return {"settings": s, "defaults": settings.DEFAULTS, "version": settings.version(), "products": products}


@router.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return _payload()


@router.put("/api/settings")
def put_settings(request: Request, body: SettingsBody, admin=Depends(auth.require_admin)) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True)
    with db.db() as conn:
        # Compare with what is stored now (not this worker's cache), one save at a time.
        current = settings.load(conn, lock=True)
        merged, errors = settings.validate(changes, current)
        if errors:
            first = next(iter(errors.values()))
            raise ApiError(400, first, "invalid_settings",
                           fields={k: {"error": str(v), "error_en": v.en} for k, v in errors.items()})
        changed = {k: f"{current[k]} → {merged[k]}" for k in settings.DEFAULTS
                   if k != "uph_targets" and current.get(k) != merged.get(k)}
        old_t, new_t = current.get("uph_targets") or {}, merged.get("uph_targets") or {}
        for product in sorted(set(old_t) | set(new_t)):
            if old_t.get(product) != new_t.get(product):
                changed[f"uph:{product}"] = f"{old_t.get(product, '—')} → {new_t.get(product, '—')}"
        if changed:
            settings.save(conn, merged)
            auth.audit(conn, request, admin["username"], "settings_update", "settings", changed)
    settings.clear_cache()
    wo_status.invalidate()
    return {"ok": True, "changed": changed, **_payload()}


@router.get("/api/wo-status")
def wo_status_list() -> dict[str, Any]:
    statuses = wo_status.statuses()
    notes = wo_notes.notes_for(statuses)
    items = []
    for key, st in statuses.items():
        items.append({**st, "note": notes[key]})
    items.sort(key=lambda r: r.get("last_test") or "", reverse=True)
    return {"items": items, "settings_version": settings.version()}


@router.get("/api/wo-notes/queue")
def notes_queue() -> dict[str, Any]:
    return wo_notes.queue_stats()
