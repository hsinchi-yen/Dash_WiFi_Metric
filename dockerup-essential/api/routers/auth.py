"""Login, logout, current user, own password."""
import hmac

from fastapi import APIRouter, Depends, Request

import config
import db
from errors import ApiError, Msg, bad_request
from schemas import ChangePasswordBody, Identity, LoginBody, LoginResult
from services import auth, auth_utils

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResult)
def login(request: Request, body: LoginBody):
    """Sign in as the administrator (TWEAK_USER / TWEAK_PASS) or an uploader. The token lasts SESSION_TTL_HOURS."""
    raw_user = body.username.strip()
    password = body.password
    with db.db() as conn:
        if auth.admin_configured() and raw_user == config.TWEAK_USER:
            username, role, display_name = config.TWEAK_USER, "admin", config.TWEAK_USER
            ok = hmac.compare_digest(password.encode(), config.TWEAK_PASS.encode())
            reason = None if ok else "bad_password"
        else:
            username = auth_utils.normalize_username(raw_user)
            user = db.fetch_one(conn, "SELECT display_name, password_hash, enabled, role FROM app_users WHERE username = %s", (username,))
            role = (user or {}).get("role") or "uploader"
            ok = auth_utils.verify_password(password, user["password_hash"] if user else auth_utils.dummy_hash())
            display_name = user["display_name"] if user else None
            if not user:
                ok, reason = False, "unknown_user"
            elif not ok:
                reason = "bad_password"
            elif not user["enabled"]:
                ok, reason = False, "disabled"
            else:
                reason = None
        if not ok:
            auth.auth_log(conn, request, raw_user, "login", False, reason)
            conn.commit()
            if reason == "disabled":
                raise ApiError(403, Msg("此帳號已停用，請聯絡管理者", "This account is disabled. Please contact the administrator."), "disabled")
            raise ApiError(401, Msg("帳號或密碼錯誤", "Wrong username or password."), "bad_credentials")

        db.execute(conn, "DELETE FROM app_sessions WHERE expires_at < NOW() - INTERVAL '7 days'")
        token = auth_utils.new_session_token()
        row = db.fetch_one(conn, """
            INSERT INTO app_sessions (token_hash, username, role, cred_fp, expires_at, client_ip)
            VALUES (%s, %s, %s, %s, NOW() + make_interval(secs => %s), %s)
            RETURNING expires_at
        """, (auth_utils.token_hash(token), username, role, auth.admin_fingerprint() if role == "admin" else None,
              config.SESSION_TTL_HOURS * 3600, auth.client_ip(request)))
        if role != "admin":
            db.execute(conn, "UPDATE app_users SET last_login_at = NOW(), last_login_ip = %s WHERE username = %s",
                       (auth.client_ip(request), username))
        auth.auth_log(conn, request, username, "login", True)
    return {"ok": True, "token": token, "role": role, "username": username, "display_name": display_name, "expires_at": row["expires_at"]}


@router.post("/logout")
def logout(request: Request, token: str = Depends(auth.token_header)):
    identity = auth.session_identity(token)
    if identity:
        with db.db() as conn:
            db.execute(conn, "UPDATE app_sessions SET revoked_at = NOW() WHERE token_hash = %s", (identity["token_hash"],))
            auth.auth_log(conn, request, identity["username"], "logout", True)
    return {"ok": True}


@router.get("/me", response_model=Identity)
def me(user=Depends(auth.current_user)):
    return auth.public_identity(user)


@router.post("/change-password")
def change_password(request: Request, body: ChangePasswordBody, user=Depends(auth.current_user)):
    """Uploaders change their own password; their other sessions are signed out, this one stays."""
    if user["role"] == "admin":
        raise bad_request(Msg("管理者密碼由伺服器環境變數設定，無法在網頁上修改",
                              "The administrator password is set on the server and cannot be changed here."), "admin_password")
    error = auth_utils.password_error(body.new_password)
    if error:
        raise bad_request(error, "weak_password")
    if body.new_password == body.old_password:
        raise bad_request(Msg("新密碼不可以和舊密碼相同。", "The new password must differ from the current one."), "same_password")
    username = user["username"]
    with db.db() as conn:
        row = db.fetch_one(conn, "SELECT password_hash FROM app_users WHERE username = %s", (username,))
        if not row or not auth_utils.verify_password(body.old_password, row["password_hash"]):
            auth.auth_log(conn, request, username, "change_password", False, "bad_password")
            conn.commit()
            raise bad_request(Msg("舊密碼不正確。", "The current password is incorrect."), "bad_password")
        db.execute(conn, "UPDATE app_users SET password_hash = %s, password_changed_at = NOW(), updated_at = NOW() WHERE username = %s",
                   (auth_utils.hash_password(body.new_password), username))
        auth.revoke_sessions(conn, username, keep_token_hash=user["token_hash"])
        auth.audit(conn, request, username, "change_password", username)
        auth.auth_log(conn, request, username, "change_password", True)
    return {"ok": True}
