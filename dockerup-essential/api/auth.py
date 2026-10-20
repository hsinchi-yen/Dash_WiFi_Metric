"""Authorisation dependencies.

Interim: a single administrator token (config.ADMIN_TOKEN) sent as `X-Admin-Token`.
The account system (administrator / uploader logins, sessions, audit) replaces this.
"""
import secrets

from fastapi import Header

import config
from errors import ApiError, Msg


def require_admin(x_admin_token: str = Header(default="")) -> None:
    """Guard for endpoints that write or rewrite the whole table. With no token
    configured the endpoints are disabled rather than open."""
    if not config.ADMIN_TOKEN or not secrets.compare_digest(x_admin_token, config.ADMIN_TOKEN):
        raise ApiError(403, Msg("需要管理者權限", "Administrator token required"), "forbidden")
