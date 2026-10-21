#!/usr/bin/env python3
"""Unit tests for the FAIL next step and disposition rules (services/fail_actions.py: pure, no database).

    python3 tests/unit_fail_actions.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dockerup-essential" / "api" / "services"))
import fail_actions as A  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail and not cond else ""))


def a(category="WIFI_TP_LOW_24G", outcome="not_retested", seq="F", idx=0):
    return {"category": category, "retest_outcome": outcome, "run_seq": seq, "run_index": idx}


# ── fail_tail ──
check("fail_tail: F → 1", A.fail_tail("F", 0) == 1)
check("fail_tail: PFF, last → 2", A.fail_tail("PFF", 2) == 2)
check("fail_tail: FPPPF, last → 1 (a PASS in between resets it)", A.fail_tail("FPPPF", 4) == 1)
check("fail_tail: FFF at index 1 → 2 (counts up to this run only)", A.fail_tail("FFF", 1) == 2)
check("fail_tail: unknown history → 1", A.fail_tail(None, None) == 1 and A.fail_tail("FF", 5) == 1)

# ── suggest ──
s = A.suggest(a(outcome="pass_on_retest", seq="FP"))
check("passed on retest → done", s["code"] == "done", s)
s = A.suggest(a(outcome="failed_again", seq="FF", idx=0))
check("failed again later → see_latest", s["code"] == "see_latest", s)
s = A.suggest(a())
check("first 2.4G throughput FAIL → retest once", s["code"] == "retest", s)
for cat in ("WIFI_TP_LOW_5G", "WIFI_NO_TP_DATA", "WIFI_ASSOC_FAIL", "BT_PING_FAIL", "BT_DEV_NOT_FOUND", "LOG_INCOMPLETE"):
    check(f"first {cat} → retest once", A.suggest(a(cat))["code"] == "retest")
s = A.suggest(a("UNKNOWN"))
check("UNKNOWN → to_rd at once", s["code"] == "to_rd" and s["values"]["n"] == 1, s)
s = A.suggest(a(seq="FF", idx=1))
check("second FAIL in a row → to_rd with n = 2", s["code"] == "to_rd" and s["values"]["n"] == 2, s)
s = A.suggest(a(seq="FPPPF", idx=4))
check("FAIL again after PASSes → retest (not a streak)", s["code"] == "retest", s)
s = A.suggest(a(), cluster=3, streak=3)
check("3 units with the same failure within 60 min (= Settings streak) → check_env", s["code"] == "check_env"
      and s["values"] == {"n": 3, "minutes": A.CLUSTER_MINUTES}, s)
s = A.suggest(a(), cluster=2, streak=3)
check("2 units (below the streak) → still retest", s["code"] == "retest", s)
s = A.suggest(a(seq="FF", idx=1), cluster=4, streak=3)
check("a burst wins over the unit's own streak (check the station first)", s["code"] == "check_env", s)
s = A.suggest(a("UNKNOWN"), cluster=5, streak=3)
check("UNKNOWN bursts are not called a station problem", s["code"] == "to_rd", s)
s = A.suggest(a(outcome="pass_on_retest", seq="FP"), cluster=9, streak=3)
check("passed on retest stays done whatever the burst", s["code"] == "done", s)
check("no classification yet → retest", A.suggest(None)["code"] == "retest")
s = A.suggest({**a(seq="FF", idx=1), "rd_action": " Replace the BT module ", "rd_by": "rd1"}, cluster=5, streak=3)
check("RD's instruction wins over the rules (write-back, Q37)", s == {"code": "rd", "values": {"text": "Replace the BT module", "by": "rd1"}}, s)
s = A.suggest({**a(outcome="pass_on_retest", seq="FP"), "rd_action": "x"})
check("…except after a passing retest (done)", s["code"] == "done", s)
check("a blank RD instruction is ignored", A.suggest({**a(), "rd_action": "  "})["code"] == "retest")

# ── effective ──
e = A.effective(a(), None)
check("no row → open, not automatic", e["status"] == "open" and not e["auto"] and A.is_pending(e), e)
e = A.effective(a(), {"status": "repair", "note": "x", "updated_by": "u1"})
check("hand-set repair → repair, handled", e["status"] == "repair" and not A.is_pending(e) and e["updated_by"] == "u1", e)
e = A.effective(a(), {"status": "retest"})
check("retest queued is still pending", e["status"] == "retest" and A.is_pending(e))
e = A.effective(a(outcome="pass_on_retest", seq="FP"), {"status": "repair"})
check("a later PASS closes it automatically, the hand-set value kept as `manual`",
      e["status"] == "retest_pass" and e["auto"] and e["manual"] == "repair" and not A.is_pending(e), e)
e = A.effective(a(outcome="failed_again", seq="FF"), None)
check("an earlier FAIL of a unit tested again → superseded", e["status"] == "superseded" and e["auto"], e)
e = A.effective(a(), {"status": "bogus"})
check("an unknown stored status reads as open", e["status"] == "open")
check("no classification yet + no row → open", A.effective(None, None)["status"] == "open")

# ── check_change ──
check("valid statuses accepted", all(A.check_change(s, None) is None for s in ("open", "retest", "repair", "rd")))
check("closed needs a note", A.check_change("closed", None) == "note_required" and A.check_change("closed", "  ") == "note_required")
check("closed with a note accepted", A.check_change("closed", "scrapped") is None)
check("automatic statuses cannot be set by hand", A.check_change("retest_pass", None) == "bad_status"
      and A.check_change("superseded", None) == "bad_status")
check("unknown status refused", A.check_change("done", None) == "bad_status" and A.check_change(None, None) == "bad_status")
check("note longer than 300 refused", A.check_change("repair", "x" * 301) == "bad_note" and A.check_change("repair", "x" * 300) is None)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
