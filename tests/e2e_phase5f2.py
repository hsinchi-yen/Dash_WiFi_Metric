#!/usr/bin/env python3
"""Browser pass for Phase 5F-2: the Advanced Analysis window — menu and landing by role, filters, Pareto, cross
table, the FAIL list with batch triage, the detail window's RD section and the instruction on the Fail List, the
unit history with two logs side by side, similar cases, the taxonomy tab, both languages.

    python3 tests/e2e_phase5f2.py [BASE] [OUT_DIR] [admin_user admin_pass]

Needs test_rd and test_uploader. Every triage it makes is undone through the API reset afterwards.
"""
import json
import os
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = sys.argv[2] if len(sys.argv) > 2 else "docs-screenshots"
ADMIN = (sys.argv[3], sys.argv[4]) if len(sys.argv) > 4 else (os.environ.get("TWEAK_USER", "qc"), os.environ.get("TWEAK_PASS", "qcpass"))
RD = ("test_rd", "rd-account-pass-2026")
UPLOADER = ("test_uploader", "uploader-pass-2026")
os.makedirs(OUT, exist_ok=True)
results, problems = [], []
touched = set()


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""), flush=True)


def api(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(), method=method,
                                 headers={"Content-Type": "application/json", **({"X-Tweak-Token": token} if token else {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


rd_token = api("POST", "/api/auth/login", {"username": RD[0], "password": RD[1]})["token"]

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(locale="zh-TW", viewport={"width": 1920, "height": 1080})
    page = ctx.new_page()
    page.on("console", lambda m: problems.append(f"console.{m.type}: {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on("response", lambda r: problems.append(f"HTTP {r.status} {r.url}") if r.status >= 400 and "/api/rd/" not in r.url else None)
    page.on("dialog", lambda d: d.accept())

    def shot(name, full=False):
        page.wait_for_timeout(900)
        page.screenshot(path=f"{OUT}/{name}.png", full_page=full)
        print("shot", name)

    def sign_in(user, password, where="#logupload", u="#luUser", pw="#luPass", btn="#btnLuLogin"):
        page.goto(f"{BASE}/{where}", wait_until="networkidle")
        page.fill(u, user); page.fill(pw, password); page.click(btn)
        page.wait_for_selector("#authBox:not([hidden])", timeout=10000)
        page.wait_for_timeout(500)

    # ── who sees it ──
    page.goto(f"{BASE}/#rd", wait_until="networkidle")
    page.wait_for_timeout(600)
    check("signed out: the menu has no Advanced Analysis, the page asks to sign in",
          page.locator(".nav-item[data-page='rd']").is_hidden() and page.locator("#rdLogin").is_visible())
    sign_in(*UPLOADER)
    page.goto(f"{BASE}/#rd", wait_until="networkidle"); page.wait_for_timeout(600)
    check("uploader: not in the menu, the page says it is for RD and the administrator",
          page.locator(".nav-item[data-page='rd']").is_hidden() and page.locator("#rdDenied").is_visible())
    page.click("#btnLogout"); page.wait_for_timeout(400)
    page.goto(f"{BASE}/#rd", wait_until="networkidle")
    page.fill("#rdUser", RD[0]); page.fill("#rdPass", RD[1]); page.click("#btnRdLogin")
    page.wait_for_selector("#rdPanel:not([hidden])", timeout=10000)
    page.goto(f"{BASE}/", wait_until="networkidle"); page.wait_for_timeout(1200)
    check("RD: in the menu, and the home address lands on Advanced Analysis", page.locator(".nav-item[data-page='rd']").is_visible()
          and page.inner_text("#pageTitle") == "進階分析", page.inner_text("#pageTitle"))

    # ── overview ──
    page.select_option("#woSelect", "")
    page.wait_for_timeout(2000)
    check("all data: 223 FAIL runs", "223" in page.inner_text("#rdCount"), page.inner_text("#rdCount"))
    check("Pareto drawn, the cross table lists the six categories", page.locator("#chRdPareto").count() == 1
          and page.locator("#rdCross tbody tr").count() == 7)       # six categories + the total row
    shot("70_rd_overview_zh")
    page.select_option("#rdF_category", "WIFI_TP_LOW_24G"); page.wait_for_timeout(1500)
    check("category filter: 185 runs", "185" in page.inner_text("#rdCount"), page.inner_text("#rdCount"))
    page.select_option("#rdGroupBy", "hour"); page.wait_for_timeout(1200)
    page.select_option("#rdCrossCols", "ssid_group"); page.wait_for_timeout(1200)
    check("cross table by SSID group for 2.4G low (grpa, grpb, solo)", all(g in page.inner_text("#rdCross") for g in ("grpa", "grpb")))
    shot("71_rd_24g_by_hour")
    page.click("#btnRdReset"); page.wait_for_timeout(1200)
    check("clear filters: 223 again", "223" in page.inner_text("#rdCount"))

    # ── list + batch triage ──
    page.click("[data-rd-tab='list']"); page.wait_for_timeout(1200)
    page.select_option("#rdF_category", "BT_DEV_NOT_FOUND"); page.wait_for_timeout(1500)
    check("list: the 7 'BT device not found' runs", page.locator("#rdRows tr").count() == 7)
    ids = [int(x) for x in page.locator("#rdRows [data-pick]").evaluate_all("els => els.map(e => e.dataset.pick)")]
    page.locator("#rdRows [data-pick]").nth(0).check(); page.locator("#rdRows [data-pick]").nth(1).check()
    check("two picked: the batch bar shows 2 selected", page.locator("#rdBatch").is_visible() and "2" in page.inner_text("#rdBatchCount"))
    page.select_option("#rdB_stage", "board")
    page.select_option("#rdB_status", "confirmed")
    page.fill("#rdB_note", "e2e batch: BT module")
    touched.update(ids[:2])
    page.click("#btnRdApply"); page.wait_for_timeout(1500)
    check("batch applied: both rows confirmed with stage board and the note", page.locator("#rdRows .badge", has_text="已確認").count() == 2
          and page.locator("#rdRows", has_text="e2e batch").count() == 1)
    shot("72_rd_list_batch")

    # ── detail window: RD section → the line's next step ──
    page.select_option("#rdF_category", ""); page.fill("#rdSearch", "513126333154"); page.wait_for_timeout(1500)
    rid = int(page.locator("#rdRows [data-pick]").first.get_attribute("data-pick"))
    touched.add(rid)
    page.locator("#rdRows [data-detail]").first.click()
    page.wait_for_selector("#fdRdAction", timeout=10000)
    page.fill("#fdRdAction", "e2e: reseat the antenna cable, then retest")
    page.select_option("#fdRdStage", "antenna")
    page.click("#btnFdRdSave")
    page.wait_for_function("document.querySelector('#fdBody .fl-next.rd')", timeout=10000)
    check("detail window (RD): saving an instruction makes it the next step", "e2e: reseat the antenna cable" in page.inner_text("#fdBody .fd-next"))
    check("…and the history shows the RD change", "RD ·" in page.inner_text("#fdBody .fd-history"))
    shot("73_rd_detail_section", full=True)
    page.keyboard.press("Escape")
    page.goto(f"{BASE}/#fails", wait_until="networkidle"); page.select_option("#woSelect", ""); page.wait_for_timeout(1500)
    check("Fail List: the unit's next step is RD's instruction", "RD：e2e: reseat the antenna cable" in page.inner_text("#failRows"))
    page.goto(f"{BASE}/#workorders", wait_until="networkidle"); page.select_option("#woSelect", ""); page.wait_for_timeout(1500)
    check("Work Orders: the work order's note carries RD's instruction", "RD：e2e: reseat the antenna cable" in page.inner_text("#woRows"))

    # ── unit history + logs ──
    page.goto(f"{BASE}/#rd", wait_until="networkidle"); page.wait_for_timeout(800)
    page.click("[data-rd-tab='unit']")
    page.fill("#rdUnitSn", "513126022699"); page.click("#btnRdUnit")          # FPPPF
    page.wait_for_selector(".rd-log", timeout=15000)
    page.wait_for_timeout(1000)
    check("unit history: the five tests of 513126022699 (F P P P F)", page.locator("#rdUnitRuns tbody tr").count() == 5)
    check("two logs side by side (the latest FAIL and the PASS before it), evidence highlighted",
          page.locator(".rd-logs.two .rd-log").count() == 2 and page.locator(".rd-log-body .ev").count() >= 1)
    page.check("#rdSignalOnly"); page.wait_for_timeout(600)
    check("'result and error lines only' shortens the logs", page.locator(".rd-log").first.locator(".rd-log-body > div").count() < 200)
    page.locator("#rdUnitRuns [data-similar]").first.click(); page.wait_for_timeout(1200)
    check("similar cases listed", "相同簽章" in page.inner_text("#rdSimilar"))
    shot("74_rd_unit_logs")

    # ── taxonomy tab, English ──
    page.click("#btnLang"); page.wait_for_timeout(800)
    page.click("[data-rd-tab='taxonomy']"); page.wait_for_timeout(800)
    check("taxonomy tab (English): eight categories with run counts, edit / merge", page.locator("#rdTaxRows tr").count() == 8
          and page.locator("[data-tax-merge]").count() == 7)
    page.locator("[data-tax-edit='WIFI_NO_TP_DATA']").click()
    check("edit form prefilled", page.input_value("#fm_name_en") == "No valid iperf data")
    page.click("#btnFormModalCancel")
    shot("75_rd_taxonomy_en")
    page.click("#btnLogout")
    browser.close()

# undo the triage through the API (the rules classify the runs again)
api("POST", "/api/rd/fails/reset", {"ids": sorted(touched)}, rd_token)
left = api("GET", "/api/rd/fails?rd_status=confirmed,reclassified,closed", token=rd_token)["total"]
check("cleanup: every triaged run reset to the rules", left == 0, left)

passed = sum(results)
print(f"\n{passed}/{len(results)} Phase 5F-2 browser checks passed; problems: {len(problems)}")
for x in problems:
    print("  ", x)
sys.exit(0 if passed == len(results) and not problems else 1)
