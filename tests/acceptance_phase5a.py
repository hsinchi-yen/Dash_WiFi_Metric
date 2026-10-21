#!/usr/bin/env python3
"""Phase 5A acceptance: settings, work-order status, one-line notes (L1), shared thresholds.

    python3 tests/acceptance_phase5a.py [BASE] [admin_user admin_pass] [uploader uploader_pass]

Reads and changes the settings through the API and restores the original values at the end
(the audit keeps both changes). Checks every stored AI note against the number rule.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dockerup-essential" / "api" / "services"))
import wo_rules  # noqa: E402  (pure; the same rules the server runs)

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ADMIN = (sys.argv[2], sys.argv[3]) if len(sys.argv) > 3 else (os.environ.get("TWEAK_USER", "qc"), os.environ.get("TWEAK_PASS", "qcpass"))
UPLOADER = (sys.argv[4], sys.argv[5]) if len(sys.argv) > 5 else ("test_uploader", "uploader-pass-2026")
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""), flush=True)
    return cond


def call(method, path, body=None, token=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Tweak-Token"] = token
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except ValueError:
            return e.code, None


def login(user, password):
    status, body = call("POST", "/api/auth/login", {"username": user, "password": password})
    return body.get("token") if status == 200 else None


def wait_settings(expect: dict, timeout=12):
    """Each uvicorn worker caches settings for 5 s, and requests may all reach one worker: wait the
    cache out, then require every reply to agree."""
    time.sleep(6)
    end = time.time() + timeout
    while time.time() < end:
        replies = [call("GET", "/api/settings")[1]["settings"] for _ in range(6)]
        if all(all(r.get(k) == v for k, v in expect.items()) for r in replies):
            return True
        time.sleep(1)
    return False


admin = login(*ADMIN)
uploader = login(*UPLOADER)
check("administrator signs in", bool(admin))

# ── Settings: read ──
status, base = call("GET", "/api/settings")
original = base["settings"]
check("GET /api/settings works without signing in", status == 200 and "yield_good" in original)
check("defaults are 98 / 95, 07:00–19:00, 120 min, 24 h, 3 in a row",
      base["defaults"]["yield_good"] == 98.0 and base["defaults"]["yield_warn"] == 95.0 and base["defaults"]["work_start"] == "07:00"
      and base["defaults"]["active_minutes"] == 120 and base["defaults"]["done_idle_hours"] == 24 and base["defaults"]["fail_streak"] == 3)
products = {p["product_model"]: p for p in base["products"]}
check("every product has its usual hourly rate", len(products) >= 11 and all(p["history_median"] for p in products.values()),
      f"{len(products)} products")
check("TN-86 usual rate = median units per active hour of its work orders (54.1)",
      products.get("TN-86_PICO-IMX7", {}).get("history_median") == 54.1, products.get("TN-86_PICO-IMX7"))

# ── Settings: who may change them ──
check("PUT without signing in → 401", call("PUT", "/api/settings", {"yield_good": 99})[0] == 401)
if uploader:
    check("PUT as an uploader → 403", call("PUT", "/api/settings", {"yield_good": 99}, uploader)[0] == 403)

# ── Settings: validation ──
bad = [
    ("yield above 100", {"yield_good": 101}, 400, "yield_good"),
    ("amber not below green", {"yield_warn": 98.5, "yield_good": 98}, 400, "yield_warn"),
    ("end before start", {"work_start": "19:00", "work_end": "07:00"}, 400, "work_end"),
    ("time in a wrong format", {"work_start": "7:00"}, 400, "work_start"),
    ("streak of 1", {"fail_streak": 1}, 400, "fail_streak"),
    ("active minutes 5", {"active_minutes": 5}, 400, "active_minutes"),
    ("hourly target 0", {"uph_targets": {"TN-86_PICO-IMX7": 0}}, 400, "uph_targets"),
    ("number sent as text", {"yield_good": "98"}, 422, None),
    ("true as a number", {"fail_streak": True}, 422, None),
    ("decimal where a whole number is needed", {"fail_streak": 3.5}, 422, None),
    ("unknown setting", {"colour": "red"}, 422, None),
]
for label, body, code, fld in bad:
    status, reply = call("PUT", "/api/settings", body, admin)
    ok = status == code and (fld is None or fld in (reply.get("fields") or {}))
    check(f"rejected: {label} → {code}" + (f" ({fld})" if fld else ""), ok, f"{status} {str(reply)[:140]}" if not ok else "")
check("nothing changed by the rejected requests", call("GET", "/api/settings")[1]["settings"] == original)

# ── Work-order status on real data ──
items = call("GET", "/api/wo-status")[1]["items"]
check("every work order has a status (77)", len(items) == 77, len(items))
lights = {k: sum(1 for x in items if x["light"] == k) for k in ("good", "warn", "bad")}
check("lights on the production copy: 70 green, 4 amber, 3 red", lights == {"good": 70, "warn": 4, "bad": 3}, lights)
red = {x["work_order"]: [f["code"] for f in x["flags"]] for x in items if x["light"] == "bad"}
check("red: 5105-260102001 (yield 94.87 %), 5101-251126008 and 5101-260102014 (FAILs in a row)",
      red.get("5105-260102001") == ["yield_bad"] and "fail_streak" in red.get("5101-251126008", [])
      and red.get("5101-260102014", [])[:1] == ["fail_streak"], red)
check("all work orders are done (the newest test is 2026-10-01)", all(x["state"] == "done" for x in items))

rows = call("GET", "/api/work-order-summary")[1]
check("Work Orders rows carry the same status and a note in both languages",
      len(rows) == 77 and all(r["status"] and r["status"]["note"]["zh"] and r["status"]["note"]["en"] for r in rows))
scoped = call("GET", "/api/work-order-summary?date_from=2026-09-01&date_to=2026-09-30")[1]
whole = {(r["work_order"], r["product_model"]): r["status"]["total"] for r in rows}
check("status covers the whole work order even when the page shows a date range",
      scoped and all(r["status"]["total"] == whole[(r["work_order"], r["product_model"])] for r in scoped))

# ── Notes (L1) ──
queue = call("GET", "/api/wo-notes/queue")[1]
check("notes stored for every work order", queue["stored"] >= 77 and queue["due"] == 0, queue)
llm_notes = [x for x in items if x["note"]["source"] == "llm" and not x["note"]["pending"]]
check("notes are AI-worded (LLM online)", len(llm_notes) >= 70, f"{len(llm_notes)} AI notes")
bad_numbers = []
for x in llm_notes:
    facts, _ = wo_rules.note_facts(x)
    for lang in ("zh", "en"):
        ok, extra = wo_rules.numbers_ok(x["note"][lang], facts, (x["work_order"], x["product_model"] or ""))
        if not ok:
            bad_numbers.append((x["work_order"], lang, extra, x["note"][lang]))
check("every number in every AI note is a fact (Q26)", not bad_numbers, bad_numbers[:3])
chinese_in_en = [x["work_order"] for x in llm_notes if any("一" <= ch <= "鿿" for ch in x["note"]["en"])]
check("English notes contain no Chinese", not chinese_in_en, chinese_in_en[:3])
wrong_unit = [x["note"]["zh"] for x in llm_notes if "件" in x["note"]["zh"]]
check("Chinese notes count units with 台", not wrong_unit, wrong_unit[:3])
for wo in ("5105-260102001", "5101-260102014"):
    x = next(i for i in items if i["work_order"] == wo)
    print(f"     {wo} [{x['light']}] {x['note']['source']}: {x['note']['zh']} | {x['note']['en']}")
redn = {x["work_order"]: x["note"] for x in items if x["light"] == "bad"}
check("red notes mention their problem (yield 94.87 / FAIL streak)",
      "94.87" in redn["5105-260102001"]["zh"] and ("連續" in redn["5101-260102014"]["zh"] or "4" in redn["5101-260102014"]["zh"]),
      {k: v["zh"] for k, v in redn.items()})

# ── Settings: change, see the effect, restore ──
status, reply = call("PUT", "/api/settings", {"yield_good": 99.5, "uph_targets": {"TN-86_PICO-IMX7": 60}}, admin)
check("PUT yield_good 99.5 + TN-86 target 60 → saved", status == 200 and "yield_good" in reply["changed"] and "uph:TN-86_PICO-IMX7" in reply["changed"],
      reply.get("changed"))
check("both API workers see the new settings", wait_settings({"yield_good": 99.5}))
meta = call("GET", "/api/meta")[1]
check("/api/meta thresholds follow the settings (colours on every page)", meta["thresholds"]["yield_good"] == 99.5, meta["thresholds"])
items = call("GET", "/api/wo-status")[1]["items"]
flagged = {x["work_order"] for x in items if any(f["code"] in ("yield_warn", "yield_bad") for f in x["flags"])}
under = {x["work_order"] for x in items if x["total"] >= 10 and x["yield_pct"] < 99.5}
check("status follows the thresholds: every work order under 99.5 % is now flagged (1 before → 4)",
      flagged == under and len(under) == 4, f"flagged {sorted(flagged)} / under {sorted(under)}")
tn86 = [x for x in items if x["product_model"] == "TN-86_PICO-IMX7"]
check("TN-86 work orders use the target 60 from Settings", tn86 and all(x["uph_target"] == 60 and x["uph_target_source"] == "setting" for x in tn86))
p86 = {p["product_model"]: p for p in call("GET", "/api/settings")[1]["products"]}["TN-86_PICO-IMX7"]
check("Settings shows the override in use", p86["override"] == 60 and p86["in_use"] == 60 and p86["source"] == "setting", p86)

status, reply = call("PUT", "/api/settings", {"yield_good": original["yield_good"], "uph_targets": original.get("uph_targets") or {}}, admin)
check("restore the original settings", status == 200 and wait_settings({"yield_good": original["yield_good"]}))
check("settings equal the originals again", call("GET", "/api/settings")[1]["settings"] == original)
status, reply = call("PUT", "/api/settings", {"yield_good": original["yield_good"]}, admin)
check("saving the same value again records no change", status == 200 and reply["changed"] == {})
audit = call("GET", "/api/admin/audit?limit=10", token=admin)[1]
rows = [r for r in audit.get("items", []) if r["action"] == "settings_update"]
g = f"{original['yield_good']}"
check("the audit's newest two entries are this change and its restore (before → after)",
      len(rows) >= 2 and rows[1]["detail"].get("yield_good") == f"{g} → 99.5" and rows[0]["detail"].get("yield_good") == f"99.5 → {g}"
      and rows[1]["detail"].get("uph:TN-86_PICO-IMX7") == "— → 60.0", [r["detail"] for r in rows[:2]])

# ── Notes follow a settings change back ──
end = time.time() + 240
while time.time() < end and call("GET", "/api/wo-notes/queue")[1]["due"]:
    time.sleep(5)
queue = call("GET", "/api/wo-notes/queue")[1]
check("after the restore every note is current again", queue["due"] == 0, queue)

passed = sum(results)
print(f"\n{passed}/{len(results)} Phase 5A checks passed")
sys.exit(0 if passed == len(results) else 1)
