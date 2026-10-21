"""Per-address rate limits (per API worker) for the endpoints that call the LLM: the pages are open to everyone
(Q18, Q25), so one address may not flood the single LLM queue."""
import collections
import threading
import time

from errors import ApiError, Msg

_hits: dict = collections.defaultdict(collections.deque)
_lock = threading.Lock()


def limit(request, bucket: str, per_minute: int) -> None:
    from services import auth
    ip = auth.client_ip(request) or "?"
    now = time.time()
    with _lock:
        q = _hits[(bucket, ip)]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= per_minute:
            raise ApiError(429, Msg("太頻繁了，請稍候再試。", "Too many requests. Please wait a moment."), "rate_limited")
        q.append(now)
