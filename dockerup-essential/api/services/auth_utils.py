"""Dashboard accounts — password hashing, session tokens and input checks.

Pure functions only (no DB access) so the unit suite can import them.
Roles: 'admin' is the single TWEAK_USER / TWEAK_PASS account from the environment;
'uploader' accounts live in the app_users table.
"""
import hashlib
import hmac
import re
import secrets

from errors import Msg

PBKDF2_ITERATIONS = 600_000
PASSWORD_MIN_LEN = 8
PASSWORD_MAX_LEN = 128
USERNAME_RE = re.compile(r'^[a-z0-9._-]{3,32}$')
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
PROFILE_MAX_LEN = 50
EMAIL_MAX_LEN = 120

_DUMMY_HASH = None


def hash_password(password, iterations=PBKDF2_ITERATIONS, salt=None):
    """'pbkdf2_sha256$<iterations>$<salt hex>$<digest hex>' — the iteration count travels with the hash."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
    return f'pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}'


def verify_password(password, stored):
    try:
        algo, iterations, salt_hex, digest_hex = stored.split('$')
        if algo != 'pbkdf2_sha256':
            return False
        digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt_hex), int(iterations))
    except (AttributeError, TypeError, ValueError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def dummy_hash():
    """Hash to verify against when the account does not exist, so unknown users take as long as wrong passwords."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_hex(16))
    return _DUMMY_HASH


def new_session_token():
    return secrets.token_urlsafe(32)


def token_hash(token):
    """Only this digest is stored; the raw token stays in the browser."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def normalize_username(name):
    return (name or '').strip().lower()


def username_error(username, reserved=()):
    """username must already be normalized. Returns a message, or None when valid."""
    if not USERNAME_RE.fullmatch(username or ''):
        return Msg('帳號只能使用英文、數字和 . _ -，長度 3 到 32 字（不分大小寫）。',
                   'Usernames use letters, digits and . _ - only, 3 to 32 characters (not case-sensitive).')
    if username in {normalize_username(r) for r in reserved if r}:
        return Msg('這個帳號名稱保留給管理者，請換一個。', 'This username is reserved for the administrator. Please choose another.')
    return None


def password_error(password):
    if not isinstance(password, str) or len(password) < PASSWORD_MIN_LEN:
        return Msg(f'密碼至少需要 {PASSWORD_MIN_LEN} 碼。', f'Passwords need at least {PASSWORD_MIN_LEN} characters.')
    if len(password) > PASSWORD_MAX_LEN:
        return Msg(f'密碼最多 {PASSWORD_MAX_LEN} 碼。', f'Passwords can have at most {PASSWORD_MAX_LEN} characters.')
    return None


def clean_profile(display_name, department, email):
    """Trim the profile fields. Returns ({display_name, department, email}, error message or None)."""
    profile = {
        'display_name': str(display_name or '').strip(),
        'department': str(department or '').strip() or None,
        'email': str(email or '').strip() or None,
    }
    if not profile['display_name']:
        return profile, Msg('請輸入顯示名稱（姓名）。', 'Please enter a display name.')
    if len(profile['display_name']) > PROFILE_MAX_LEN or len(profile['department'] or '') > PROFILE_MAX_LEN:
        return profile, Msg(f'姓名和部門最多 {PROFILE_MAX_LEN} 字。', f'Name and department can have at most {PROFILE_MAX_LEN} characters.')
    if profile['email'] and (len(profile['email']) > EMAIL_MAX_LEN or not EMAIL_RE.fullmatch(profile['email'])):
        return profile, Msg('Email 格式不正確。', 'Invalid email address.')
    return profile, None


def can_access_upload(identity, owner):
    """Admins reach every upload batch; uploaders only their own."""
    return identity.get('role') == 'admin' or (owner is not None and identity.get('username') == owner)
