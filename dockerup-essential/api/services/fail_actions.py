"""What the line does with a FAIL (5C) — pure functions (no database), unit-tested on the host.

suggest(a, cluster, streak)  → {"code", "values"}: the next step, from the classification, the unit's retest
                               history and same-category FAILs close in time. Rules only: no LLM call
                               (the edge LLM is kept for explanations, see the LLM budget).
effective(a, disposition)    → the disposition shown for a FAIL run: closed automatically once a later test of
                               the same unit passed, else what someone set, else "open".

Suggestion codes (the web front end words them):
  done           a later test passed: nothing to do
  see_latest     this unit failed again later: the latest FAIL carries the next step
  check_env      `n` units of the work order with the same failure within CLUSTER_MINUTES: check the station / AP first
  retest         first failure of the unit: retest once
  to_rd          failed `n` times in a row, or no rule recognised the log: hand it to RD / repair
  rd             RD's own instruction for this FAIL (5F-2 write-back, Q37): `text`, `by`
"""
from typing import Any, Optional

STATUSES = ("open", "retest", "repair", "rd", "closed")
PENDING = ("open", "retest")         # still needs someone on the line
CLUSTER_MINUTES = 60
NOTE_MAX = 300

# Failures worth one retest before anything else (the production copy: 180 of 185 2.4G FAILs passed on retest,
# and every retested "BT not found" unit passed too). UNKNOWN goes straight to RD.
RETEST_FIRST = {"WIFI_TP_LOW_24G", "WIFI_TP_LOW_5G", "WIFI_NO_TP_DATA", "WIFI_ASSOC_FAIL", "BT_PING_FAIL",
                "BT_DEV_NOT_FOUND", "LOG_INCOMPLETE"}


def fail_tail(run_seq: Optional[str], run_index: Optional[int]) -> int:
    """How many FAIL runs in a row end at this run ("PFF", index 2 → 2). Unknown history → 1."""
    if not run_seq or run_index is None or not 0 <= run_index < len(run_seq):
        return 1
    n = 0
    for c in reversed(run_seq[: run_index + 1]):
        if c != "F":
            break
        n += 1
    return max(n, 1)


def suggest(a: Optional[dict[str, Any]], cluster: int = 0, streak: int = 3) -> dict[str, Any]:
    """The next step for one FAIL run. `cluster` = units of the same work order with the same category within
    CLUSTER_MINUTES of this run (this one included); `streak` = Settings "FAILs in a row" (as the status flag)."""
    if not a:
        return {"code": "retest", "values": {}}
    outcome = a.get("retest_outcome")
    if outcome == "pass_on_retest":
        return {"code": "done", "values": {}}
    if outcome == "failed_again":
        return {"code": "see_latest", "values": {}}
    if (a.get("rd_action") or "").strip():          # RD decided what the line does with it
        return {"code": "rd", "values": {"text": a["rd_action"].strip(), "by": a.get("rd_by")}}
    category = a.get("category") or "UNKNOWN"
    if cluster >= streak and category != "UNKNOWN":
        return {"code": "check_env", "values": {"n": int(cluster), "minutes": CLUSTER_MINUTES}}
    tail = fail_tail(a.get("run_seq"), a.get("run_index"))
    if category in RETEST_FIRST and tail == 1:
        return {"code": "retest", "values": {}}
    return {"code": "to_rd", "values": {"n": tail}}


def effective(a: Optional[dict[str, Any]], disposition: Optional[dict[str, Any]]) -> dict[str, Any]:
    """{status, auto, note, updated_by, updated_at}. A later PASS closes the FAIL whatever was set by hand
    (the hand-set value stays in the history); a later FAIL hands the disposition to that run."""
    d = disposition or {}
    base = {"note": d.get("note"), "updated_by": d.get("updated_by"), "updated_at": d.get("updated_at"),
            "manual": d.get("status")}
    outcome = (a or {}).get("retest_outcome")
    if outcome == "pass_on_retest":
        return {**base, "status": "retest_pass", "auto": True}
    if outcome == "failed_again":
        return {**base, "status": "superseded", "auto": True}
    status = d.get("status") if d.get("status") in STATUSES else "open"
    return {**base, "status": status, "auto": False}


def is_pending(eff: dict[str, Any]) -> bool:
    return eff.get("status") in PENDING


def check_change(status: Any, note: Any) -> Optional[str]:
    """None when (status, note) is a valid manual disposition, else an error code."""
    if status not in STATUSES:
        return "bad_status"
    if note is not None and (not isinstance(note, str) or len(note) > NOTE_MAX):
        return "bad_note"
    if status == "closed" and not (note or "").strip():
        return "note_required"      # "closed" covers scrap and anything else: say which
    return None
