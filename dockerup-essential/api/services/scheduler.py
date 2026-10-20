"""Background loop, started once per uvicorn worker (an advisory lock keeps the work single).

maintenance (every 15 s): refresh mv_unit when STATS_REFRESH_SEC has passed.
"""
import logging
import threading

from services import stats

log = logging.getLogger("wifi.scheduler")

_stop = threading.Event()
_thread = None


def _maintenance():
    while not _stop.wait(15):
        try:
            stats.refresh_if_due()
        except Exception:
            log.exception("stats refresh failed")


def start():
    global _thread
    if _thread is not None:
        return
    _stop.clear()
    _thread = threading.Thread(target=_maintenance, name="wifi-maintenance", daemon=True)
    _thread.start()


def stop():
    global _thread
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=2)
        _thread = None
