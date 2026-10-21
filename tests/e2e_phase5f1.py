#!/usr/bin/env python3
"""Browser pass for Phase 5F-1: failure categories on the Fail List and the work-order detail page, the RD
account's view, the role select in Accounts, and a custom date range on the Dashboard.

    python3 tests/e2e_phase5f1.py [BASE] [OUT_DIR] [admin_user admin_pass]

Needs the account test_rd (role rd; tests/acceptance_phase5f1.py creates it).
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = sys.argv[2] if len(sys.argv) > 2 else "docs-screenshots"
ADMIN = (sys.argv[3], sys.argv[4]) if len(sys.argv) > 4 else (os.environ.get("TWEAK_USER", "qc"), os.environ.get("TWEAK_PASS", "qcpass"))
RD = ("test_rd", "rd-account-pass-2026")
os.makedirs(OUT, exist_ok=True)
results, problems = [], []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""), flush=True)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_context(locale="en-US", viewport={"width": 1920, "height": 1080}).new_page()
    page.on("console", lambda m: problems.append(f"console.{m.type}: {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on("response", lambda r: problems.append(f"HTTP {r.status} {r.url}") if r.status >= 400 else None)

    def shot(name, full=False):
        page.wait_for_timeout(900)
        page.screenshot(path=f"{OUT}/{name}.png", full_page=full)
        print("shot", name)

    # ── Dashboard: a custom September range ──
    page.goto(f"{BASE}/#overview", wait_until="networkidle")
    page.select_option("#woSelect", "")
    page.click("#rangeSeg button[data-range='custom']")
    page.fill("#dateFrom", "2026-09-01"); page.dispatch_event("#dateFrom", "change")
    page.fill("#dateTo", "2026-09-30"); page.dispatch_event("#dateTo", "change")
    page.wait_for_timeout(1800)
    check("Dashboard custom range 09/01–09/30 shows the whole month (4,734 units)", page.inner_text("#ovUnits") == "4,734", page.inner_text("#ovUnits"))
    shot("50_dashboard_september")
    page.click("#rangeSeg button[data-range='all']")

    # ── Fail List ──
    page.goto(f"{BASE}/#fails", wait_until="networkidle")
    page.select_option("#woSelect", "")
    page.wait_for_selector("#failRows .badge.cat", timeout=15000)
    page.wait_for_timeout(800)
    names = set(page.locator("#failRows .badge.cat").all_inner_texts())
    check("every failed unit shows its category (no 'Final test failed')", page.locator("#failRows .badge.cat").count() == 15
          and "Final test failed" not in names, names)
    title = page.locator("#failRows .badge.cat").first.get_attribute("title")
    check("the category tooltip quotes the log lines (#line) and the suggested action", "#" in title and "→" in title, title[:160])
    check("run sequences (F / P) and stations are shown",
          page.locator("#failRows .runseq b").count() >= 15 and "USB" in page.inner_text("#failRows"))
    shot("51_fail_list_categories")
    page.click("#btnLang"); page.wait_for_timeout(1000)
    check("中文: category names switch", "吞吐量低於門檻" in page.inner_text("#failRows"))
    shot("52_fail_list_zh")
    page.click("#btnLang")

    # ── Work-order detail ──
    page.goto(f"{BASE}/#wo?wo=5101-260102014&pm=TN-86_PICO-IMX7", wait_until="networkidle")
    page.wait_for_selector("#wdFailCatCard:not([hidden])", timeout=15000)
    page.wait_for_timeout(800)
    check("detail: Failures by category (6 categories, 74 FAIL runs, 63 passed on retest)",
          page.locator("#wdFailCatRows tr").count() == 6 and "74" in page.inner_text("#wdFailCatNote") and "63" in page.inner_text("#wdFailCatNote"))
    check("detail: stations of the failures", page.locator("#wdStationRows tr").count() >= 3)
    check("detail: the FAIL tile names the categories of the units still failing", "BT device not found" in page.locator("#wdTiles .tile").nth(2).inner_text())
    check("detail: failed units show category and run sequence", page.locator("#wdFailRows .badge.cat").count() == 7)
    shot("53_detail_fail_categories", full=True)

    # ── RD account ──
    page.goto(f"{BASE}/#logupload", wait_until="networkidle")
    page.fill("#luUser", RD[0]); page.fill("#luPass", RD[1]); page.click("#btnLuLogin")
    page.wait_for_selector("#authBox:not([hidden])", timeout=10000)
    page.wait_for_timeout(600)
    check("RD signs in: the role reads RD", "RD" in page.inner_text("#authRole"), page.inner_text("#authRole"))
    check("Log Upload tells RD it cannot upload (no upload panel)", page.locator("#luDenied").is_visible() and not page.locator("#luPanel").is_visible())
    shot("54_logupload_rd_denied")
    page.goto(f"{BASE}/#products", wait_until="networkidle")
    page.wait_for_timeout(800)
    check("Products: RD reads the list, no 'New product' button", page.locator("#prodRows tr").count() >= 11 and not page.locator("#btnProdNew").is_visible())
    check("Accounts and Settings are not in RD's menu",
          not page.locator(".nav-item[data-page='accounts']").is_visible() and not page.locator(".nav-item[data-page='settings']").is_visible())
    page.click("#btnLogout"); page.wait_for_timeout(500)

    # ── Accounts: role select ──
    page.goto(f"{BASE}/#settings", wait_until="networkidle")
    page.fill("#setUser", ADMIN[0]); page.fill("#setPass", ADMIN[1]); page.click("#btnSetLogin")
    page.wait_for_selector("#setPanel:not([hidden])", timeout=10000)
    page.goto(f"{BASE}/#accounts", wait_until="networkidle")
    page.wait_for_timeout(1000)
    check("Accounts marks the RD account", "RD" in page.locator("#acctUserRows tr", has_text=RD[0]).inner_text())
    page.click("#btnAcctNew")
    opts = page.locator("#fm_role option").all_inner_texts()
    check("New account has a role select (Uploader / RD)", opts == ["Uploader", "RD"], opts)
    shot("55_accounts_new_role")
    page.click("#btnFormModalCancel")
    page.locator("#acctUserRows tr", has_text=RD[0]).locator("[data-op='edit']").click()
    check("Edit account shows the current role", page.input_value("#fm_role") == "rd")
    page.click("#btnFormModalCancel")
    page.click("#btnLogout")
    browser.close()

passed = sum(results)
print(f"\n{passed}/{len(results)} Phase 5F-1 browser checks passed; problems: {len(problems)}")
for x in problems:
    print("  ", x)
sys.exit(0 if passed == len(results) and not problems else 1)
