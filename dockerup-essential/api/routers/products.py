"""Product registry: the selection list, name resolution, and adding / retiring products.

Uploaders and administrators may add a product (R4: a well-formed new name whose TN number is
unused); only the administrator retires or edits one. Retired products stay on historical rows.
"""
from fastapi import APIRouter, Depends, Request

import db
from errors import Msg, bad_request, conflict, not_found
from schemas import ProductCreateBody, ProductResolveBody, ProductUpdateBody
from services import auth, products

router = APIRouter(prefix="/api/products", tags=["products"])


@router.get("/registry")
def registry(_user=Depends(auth.current_user)):
    """Active products for selection lists, most recently used first."""
    items = [r for r in products.listing() if r["status"] == "active"]
    items.sort(key=lambda r: (r["last_test"] or "", r["canonical"]), reverse=True)
    return {"items": items}


@router.get("/list")
def listing(_user=Depends(auth.current_user)):
    """Every product (active and retired) with usage counts — the Products page.
    (GET /api/products itself is the legacy product list used by the old dashboard.)"""
    return {"items": products.listing()}


@router.post("/resolve")
def resolve(body: ProductResolveBody, _user=Depends(auth.current_user)):
    """What a typed name means: known (→ canonical), new, blocked_same_tn (R16), ambiguous or bad_format."""
    with db.db() as conn:
        result = products.load(conn).resolve(body.text)
    return result


@router.post("")
def create(request: Request, body: ProductCreateBody, user=Depends(auth.current_user)):
    with db.db() as conn:
        result = products.load(conn).resolve(body.name)
        if result["status"] == "known":
            raise conflict(Msg(f"產品 {result['canonical']} 已存在。", f"Product {result['canonical']} already exists."), "duplicate_product", canonical=result["canonical"])
        if result["status"] == "retired":
            raise conflict(Msg(f"產品 {result['canonical']} 已存在但已停用，請管理者在本頁重新啟用。",
                               f"Product {result['canonical']} exists but is retired; the administrator can activate it on this page."),
                           "retired_product", canonical=result["canonical"])
        if result["status"] == "blocked_same_tn":
            raise bad_request(Msg(f"TN 編號已屬於 {', '.join(result['same_tn'])}，請改選既有產品。",
                                  f"The TN number already belongs to {', '.join(result['same_tn'])}. Please choose the existing product."),
                              "same_tn", suggestions=result["same_tn"] + result.get("near", []))
        if result["status"] != "new":
            raise bad_request(Msg("產品名稱格式不正確。格式為 TN-<編號>_<型號>，例如 TN-86_PICO-IMX7。",
                                  "Invalid product name. Use TN-<number>_<model>, for example TN-86_PICO-IMX7."), "bad_format")
        added = products.add(conn, result["proposal"], user["username"], source="web", note=(body.note or "").strip() or None)
        auth.audit(conn, request, user["username"], "product_add", added["canonical"], {"input": body.name.strip()})
    return {"ok": True, **added}


@router.patch("/{product_id}")
def update(product_id: int, request: Request, body: ProductUpdateBody, admin=Depends(auth.require_admin)):
    sent = body.model_dump(exclude_unset=True)
    with db.db() as conn:
        row = db.fetch_one(conn, "SELECT canonical, status, note, aliases FROM product_registry WHERE id = %s", (product_id,))
        if not row:
            raise not_found(Msg("找不到這個產品。", "Product not found."), "no_product")
        status = sent.get("status") or row["status"]
        note = sent["note"] if "note" in sent else row["note"]
        aliases = row["aliases"]
        if "aliases" in sent and sent["aliases"] is not None:
            aliases = sorted({a.strip() for a in sent["aliases"] if a and a.strip()})
        db.execute(conn, "UPDATE product_registry SET status = %s, note = %s, aliases = %s WHERE id = %s",
                   (status, note, aliases, product_id))
        changed = {k: v for k, v in (("status", status), ("note", note), ("aliases", aliases)) if row[k] != v}
        if changed:
            auth.audit(conn, request, admin["username"], "product_update", row["canonical"], changed)
    return {"ok": True, "canonical": row["canonical"], "status": status}
