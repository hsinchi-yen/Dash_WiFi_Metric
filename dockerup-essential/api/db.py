"""PostgreSQL access: a small per-worker connection pool plus dedicated connections.

    with db() as conn:                  # pooled; commit on success, rollback on error
        rows = fetch_all(conn, "SELECT …", params)
    rows(sql, params)                   # pooled one-liner; datetimes as ISO strings (API shape)

Work that holds a session advisory lock (view refresh, migrations) uses `connect()`,
so a lock never leaks back into the pool. DB Tweak runs as the user's own PostgreSQL
login through `connect_as()`.
"""
import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import quote, urlparse

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

import config

log = logging.getLogger("wifi.db")

_pool = None
_pool_lock = threading.Lock()
_slots = threading.BoundedSemaphore(config.DB_POOL_MAX)


class TimedCursor(psycopg2.extras.RealDictCursor):
    """Dict rows; logs statements slower than SLOW_QUERY_MS."""

    def execute(self, query, vars=None):
        t0 = time.perf_counter()
        try:
            return super().execute(query, vars)
        finally:
            ms = (time.perf_counter() - t0) * 1000
            if ms >= config.SLOW_QUERY_MS:
                text = " ".join(str(query).split())
                log.warning("slow query %.0f ms: %s", ms, text[:300])


def _dsn():
    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return config.DATABASE_URL


def connect():
    """A dedicated (non-pooled) connection; the caller closes it."""
    return psycopg2.connect(_dsn(), cursor_factory=TimedCursor)


def connect_as(username: str, password: str):
    """A connection with the caller's own PostgreSQL login (DB Tweak); same host/db as the API."""
    base = urlparse(_dsn())
    host = base.hostname or "localhost"
    port = base.port or 5432
    dbname = (base.path or "").lstrip("/") or "postgres"
    return psycopg2.connect(f"postgresql://{quote(username)}:{quote(password)}@{host}:{port}/{dbname}",
                            cursor_factory=TimedCursor)


def _get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadedConnectionPool(1, config.DB_POOL_MAX, _dsn(), cursor_factory=TimedCursor)
        return _pool


@contextmanager
def db():
    """Pooled connection. Waits for a free slot instead of failing when all are busy."""
    _slots.acquire()
    pool = _get_pool()
    conn = None
    broken = False
    try:
        conn = pool.getconn()
        if conn.closed:
            pool.putconn(conn, close=True)
            conn = pool.getconn()
        yield conn
        conn.commit()
    except (psycopg2.InterfaceError, psycopg2.OperationalError):
        broken = True
        raise
    except BaseException:
        if conn is not None and not conn.closed:
            try:
                conn.rollback()
            except psycopg2.Error:
                broken = True
        raise
    finally:
        if conn is not None:
            try:
                pool.putconn(conn, close=broken or bool(conn.closed))
            except psycopg2.pool.PoolError:
                pass
        _slots.release()


def fetch_all(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def fetch_one(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


def execute(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def jsonable(rows):
    """The API's row shape: datetimes as ISO-8601 strings, everything else verbatim
    (Decimal stays Decimal, which FastAPI serialises as a string — unchanged from before)."""
    out = []
    for row in rows:
        out.append({k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()})
    return out


def rows(sql, params=None):
    with db() as conn:
        return jsonable(fetch_all(conn, sql, params))


def close_pool():
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None
