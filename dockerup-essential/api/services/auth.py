"""Sessions and permissions.

The X-Tweak-Token header carries a random session token (only its SHA-256 is stored).
role 'admin'    = TWEAK_USER / TWEAK_PASS (DB maintenance, accounts, every upload batch, ingest)
role 'uploader' = an app_users row (Log Upload: own batches only; products: may add)

Routes declare what they need with FastAPI dependencies:
    user = Depends(current_user)     # any signed-in account, else 401
    user = Depends(require_admin)    # administrator, else 403
"""
import hashlib

import psycopg2.extras
from fastapi import Depends, Request
from fastapi.security import APIKeyHeader

import config
import db
from errors import ApiError, Msg
from services import auth_utils

token_header = APIKeyHeader(name="X-Tweak-Token", auto_error=False,
                            description="Session token from POST /api/auth/login")


def client_ip(request: Request):
    return request.headers.get("x-real-ip") or (request.client.host if request.client else None)


def admin_configured():
    return bool(config.TWEAK_USER and config.TWEAK_PASS)


def admin_fingerprint():
    """Bound into admin sessions so changing TWEAK_USER/TWEAK_PASS signs the administrator out."""
    return hashlib.sha256(f"{config.TWEAK_USER}\0{config.TWEAK_PASS}".encode()).hexdigest()[:16]


def session_identity(token):
    """Session token → {username, role, display_name, expires_at, token_hash}, or None."""
    if not token or len(token) > 128:
        return None
    digest = auth_utils.token_hash(token)
    with db.db() as conn:
        row = db.fetch_one(conn, """
            SELECT s.username, s.role, s.cred_fp, s.expires_at, u.display_name, u.enabled
            FROM app_sessions s LEFT JOIN app_users u ON u.username = s.username
            WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > NOW()
        """, (digest,))
    if not row:
        return None
    if row["role"] == "admin":
        if row["username"] != config.TWEAK_USER or row["cred_fp"] != admin_fingerprint():
            return None
    elif not row["enabled"]:
        return None
    return {"username": row["username"], "role": row["role"],
            "display_name": row["display_name"] or row["username"],
            "expires_at": row["expires_at"], "token_hash": digest}


def optional_user(token: str = Depends(token_header)):
    return session_identity(token)


def current_user(token: str = Depends(token_header)):
    identity = session_identity(token)
    if not identity:
        raise ApiError(401, Msg("請先登入，或登入已失效", "Please sign in (or your session has expired)."), "unauthorized")
    return identity


def require_admin(user=Depends(current_user)):
    if user["role"] != "admin":
        raise ApiError(403, Msg("此功能僅限管理者使用", "Administrators only."), "forbidden")
    return user


def public_identity(identity):
    return {k: identity[k] for k in ("username", "role", "display_name", "expires_at")}


def auth_log(conn, request, username, event, success, reason=None):
    db.execute(conn, """
        INSERT INTO auth_log (username, event, success, reason, client_ip, user_agent)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, ((username or "")[:64], event, success, reason, client_ip(request),
          (request.headers.get("user-agent") or "")[:200]))


def audit(conn, request, actor, action, target, detail=None):
    db.execute(conn, """
        INSERT INTO account_audit (actor, action, target, detail, client_ip)
        VALUES (%s, %s, %s, %s, %s)
    """, (actor, action, target, psycopg2.extras.Json(detail) if detail else None, client_ip(request)))


def revoke_sessions(conn, username, keep_token_hash=None):
    db.execute(conn, """
        UPDATE app_sessions SET revoked_at = NOW()
        WHERE username = %s AND revoked_at IS NULL AND token_hash IS DISTINCT FROM %s
    """, (username, keep_token_hash))
