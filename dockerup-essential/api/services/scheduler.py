"""Background loops, started once per uvicorn worker (advisory locks keep the work single).

maintenance (every 15 s): refresh mv_unit when STATS_REFRESH_SEC has passed.
reports     (every 5 s):  write one due work-order note (L1), else generate one queued AI report;
                          once a minute, queue a missing or stale report for one of the newest work
                          orders (REPORT_AUTO).
"""
import logging
import threading

import config
from services import fail_classify, period_reports, reports, stats, wo_notes, wo_report

log = logging.getLogger("wifi.scheduler")

_stop = threading.Event()
_threads: list[threading.Thread] = []


def _prune_llm_usage():
    import db
    with db.db() as conn:
        db.execute(conn, "DELETE FROM llm_usage WHERE at < NOW() - INTERVAL '90 days'")


def _maintenance():
    ticks = 0
    while not _stop.wait(15):
        ticks += 1
        if ticks % 240 == 1:                      # hourly
            try:
                _prune_llm_usage()
            except Exception:
                log.exception("llm usage prune failed")
        try:
            stats.refresh_if_due()
        except Exception:
            log.exception("stats refresh failed")
        try:
            fail_classify.scan_if_due()          # classify new FAIL runs (rules), refresh retest outcomes
        except Exception:
            log.exception("fail classification failed")


def _report_worker():
    ticks = 0
    while not _stop.wait(5):
        ticks += 1
        try:
            if wo_notes.process_once():        # one-line notes first: short, and the Work Orders page shows them
                continue
            if fail_classify.llm_once():       # a FAIL run no rule recognised
                continue
            if reports.process_queue_once():
                continue
            if wo_report.process_once():       # structured final reports (done work orders, requests)
                continue
            if period_reports.process_once():  # the last finished week's / month's report, once
                continue
            if config.REPORT_AUTO and ticks % 12 == 0:
                reports.warm_once()
        except Exception:
            log.exception("report worker failed")


def start():
    if _threads:
        return
    _stop.clear()
    for target, name in ((_maintenance, "wifi-maintenance"), (_report_worker, "wifi-reports")):
        t = threading.Thread(target=target, name=name, daemon=True)
        t.start()
        _threads.append(t)


def stop():
    _stop.set()
    for t in _threads:
        t.join(timeout=2)
    _threads.clear()
