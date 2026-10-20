"""Uploader account management, account audit and login log (administrator only)."""
from fastapi import APIRouter, Depends, Query, Request

import config
import db
from errors import Msg, bad_request, not_found
from schemas import PasswordBody, UserCreate, UserUpdate
from services import auth, auth_utils

router = APIRouter(prefix="/api/admin", tags=["accounts"])

USER_FIELDS = ("username", "display_name", "department", "email", "enabled", "created_at", "created_by",
               "updated_at", "password_changed_at", "last_login_at", "last_login_ip")


def _upload_stats_sql(where):
    return f"""
        SELECT username,
               COUNT(*) FILTER (WHERE status = 'done')                                   AS uploads,
               COALESCE(SUM(uploaded) FILTER (WHERE status IN ('done', 'cancelled')), 0) AS uploaded_rows,
               COUNT(*) FILTER (WHERE status = 'reverted')                               AS reverted_batches,
               MAX(created_at) FILTER (WHERE status IN ('done', 'failed', 'cancelled', 'reverted')) AS last_upload_at
        FROM upload_log {where} GROUP BY username
    """


@router.get("/users")
def list_users(_admin=Depends(auth.require_admin)):
    """Uploader accounts with their upload statistics; `admin` carries the administrator's own totals."""
    with db.db() as conn:
        items = db.jsonable(db.fetch_all(conn, f"""
            SELECT {', '.join('u.' + f for f in USER_FIELDS)},
                   COALESCE(s.uploads, 0) AS uploads, COALESCE(s.uploaded_rows, 0) AS uploaded_rows,
                   COALESCE(s.reverted_batches, 0) AS reverted_batches, s.last_upload_at
            FROM app_users u
            LEFT JOIN ({_upload_stats_sql('')}) s ON s.username = u.username
            ORDER BY u.enabled DESC, u.username
        """))
        admin_stats = db.fetch_one(conn, _upload_stats_sql("WHERE username = %s"), (config.TWEAK_USER,))
        admin_login = db.fetch_one(conn, "SELECT MAX(at) AS last_login_at FROM auth_log WHERE username = %s AND event = 'login' AND success",
                                   (config.TWEAK_USER,))["last_login_at"]
    admin = {"username": config.TWEAK_USER, "uploads": 0, "uploaded_rows": 0, "reverted_batches": 0, "last_upload_at": None,
             **(admin_stats or {}), "last_login_at": admin_login}
    return {"items": items, "admin": db.jsonable([admin])[0]}


@router.post("/users")
def create_user(request: Request, body: UserCreate, admin=Depends(auth.require_admin)):
    username = auth_utils.normalize_username(body.username)
    profile, error = auth_utils.clean_profile(body.display_name, body.department, body.email)
    error = auth_utils.username_error(username, reserved=[config.TWEAK_USER]) or error or auth_utils.password_error(body.password)
    if error:
        raise bad_request(error, "invalid_user")
    with db.db() as conn:
        n = db.execute(conn, """
            INSERT INTO app_users (username, display_name, department, email, password_hash, created_by, password_changed_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (username) DO NOTHING
        """, (username, profile["display_name"], profile["department"], profile["email"],
              auth_utils.hash_password(body.password), admin["username"]))
        if n == 0:
            raise bad_request(Msg(f"帳號 {username} 已存在。", f"Username {username} already exists."), "duplicate_user")
        auth.audit(conn, request, admin["username"], "create", username, profile)
    return {"ok": True, "username": username}


@router.patch("/users/{username}")
def update_user(username: str, request: Request, body: UserUpdate, admin=Depends(auth.require_admin)):
    """Edit the profile and/or enable/disable. Disabling signs the account out everywhere."""
    username = auth_utils.normalize_username(username)
    sent = body.model_dump(exclude_unset=True)
    with db.db() as conn:
        before = db.fetch_one(conn, "SELECT display_name, department, email, enabled FROM app_users WHERE username = %s", (username,))
        if not before:
            raise not_found(Msg("找不到這個帳號。", "Account not found."), "no_user")
        merged = {k: sent.get(k, before[k]) for k in ("display_name", "department", "email")}
        profile, error = auth_utils.clean_profile(merged["display_name"], merged["department"], merged["email"])
        if error:
            raise bad_request(error, "invalid_user")
        enabled = before["enabled"] if sent.get("enabled") is None else bool(sent["enabled"])
        changed = {k: (before[k], profile[k]) for k in profile if before[k] != profile[k]}
        if changed:
            auth.audit(conn, request, admin["username"], "update", username,
                       {"before": {k: v[0] for k, v in changed.items()}, "after": {k: v[1] for k, v in changed.items()}})
        if enabled != before["enabled"]:
            auth.audit(conn, request, admin["username"], "enable" if enabled else "disable", username)
            if not enabled:
                auth.revoke_sessions(conn, username)
        db.execute(conn, """
            UPDATE app_users SET display_name = %s, department = %s, email = %s, enabled = %s, updated_at = NOW() WHERE username = %s
        """, (profile["display_name"], profile["department"], profile["email"], enabled, username))
    return {"ok": True, "username": username, "enabled": enabled}


@router.post("/users/{username}/reset-password")
def reset_password(username: str, request: Request, body: PasswordBody, admin=Depends(auth.require_admin)):
    username = auth_utils.normalize_username(username)
    error = auth_utils.password_error(body.password)
    if error:
        raise bad_request(error, "weak_password")
    with db.db() as conn:
        n = db.execute(conn, "UPDATE app_users SET password_hash = %s, password_changed_at = NOW(), updated_at = NOW() WHERE username = %s",
                       (auth_utils.hash_password(body.password), username))
        if n == 0:
            raise not_found(Msg("找不到這個帳號。", "Account not found."), "no_user")
        auth.revoke_sessions(conn, username)
        auth.audit(conn, request, admin["username"], "reset_password", username)
    return {"ok": True}


def _paged(conn, base_sql, where, params, order, limit, offset):
    total = db.fetch_one(conn, f"SELECT COUNT(*) AS n FROM ({base_sql} {where}) t", params)["n"]
    items = db.jsonable(db.fetch_all(conn, f"{base_sql} {where} ORDER BY {order} LIMIT %s OFFSET %s", list(params) + [limit, offset]))
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/audit")
def audit_log(username: str = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), _admin=Depends(auth.require_admin)):
    """Account and product changes; `username` matches either the actor or the target."""
    where, params = "", []
    if username:
        where, params = "WHERE actor = %s OR target = %s", [username, username]
    with db.db() as conn:
        return _paged(conn, "SELECT id, at, actor, action, target, detail, client_ip FROM account_audit", where, params, "at DESC, id DESC", limit, offset)


@router.get("/login-log")
def login_log(username: str = None, success: str = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
              _admin=Depends(auth.require_admin)):
    clauses, params = [], []
    if username:
        clauses.append("username = %s")
        params.append(username)
    if success in ("true", "false"):
        clauses.append("success = %s")
        params.append(success == "true")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with db.db() as conn:
        return _paged(conn, "SELECT id, at, username, event, success, reason, client_ip, user_agent FROM auth_log", where, params, "at DESC, id DESC", limit, offset)
