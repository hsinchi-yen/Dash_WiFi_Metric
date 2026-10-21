#!/usr/bin/env python3
"""Browser pass for Phase 5C: the home address and role landing, the Fail List's disposition views, next steps and
FAIL detail window (signed out: read only; signed in: set and put back), the work-order detail's FAIL table, and the
Settings working days. (The Today page was removed on 2026-10-21; tests/e2e_forecast.py covers the Trend page's
Forecast view and the TV rotation.)

    python3 tests/e2e_phase5c.py [BASE] [OUT_DIR] [admin_user admin_pass]

Needs the account test_rd (tests/acceptance_phase5f1.py creates it). The one disposition it sets is put back.
"""
import os
import re
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

    def new_page(locale="en-US", **kw):
        pg = browser.new_context(locale=locale, viewport={"width": 1920, "height": 1080}, **kw).new_page()
        pg.on("console", lambda m: problems.append(f"console.{m.type}: {m.text}") if m.type in ("error", "warning") else None)
        pg.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        pg.on("response", lambda r: problems.append(f"HTTP {r.status} {r.url}") if r.status >= 400 and "/api/fail-disposition" not in r.url else None)
        return pg

    page = new_page()

    def shot(name, pg=None, full=False):
        (pg or page).wait_for_timeout(900)
        (pg or page).screenshot(path=f"{OUT}/{name}.png", full_page=full)
        print("shot", name)

    def no_scroll(pg):
        return pg.evaluate("document.documentElement.scrollHeight <= window.innerHeight + 1")

    # ── Home: the Trend page (the Today page was removed on 2026-10-21) ──
    page.goto(f"{BASE}/", wait_until="networkidle")
    page.wait_for_selector("#page-overview:not([hidden])", timeout=10000)
    check("the home address (no page) opens Trend when signed out", page.inner_text("#pageTitle") == "Trend", page.inner_text("#pageTitle"))
    check("no Today in the menu", page.locator(".nav-item[data-page='today']").count() == 0 and page.locator(".nav-item").first.get_attribute("data-page") == "overview")

    # ── Fail List: disposition views, next steps, detail window ──
    page.goto(f"{BASE}/#fails", wait_until="networkidle")
    page.select_option("#woSelect", "")
    page.wait_for_selector("#failRows [data-detail]", timeout=15000)
    page.wait_for_timeout(800)
    check("Fail List opens on 'To handle' (15 units)", page.get_attribute("#flDispSeg [data-disp='pending']", "aria-pressed") == "true"
          and page.locator("#failRows tr").count() == 15)
    tiles = page.inner_text("#flTiles")
    check("tiles: 15 failed units (15 to handle), 223 FAIL runs with 204 passed on retest, the main failure and station",
          "15" in tiles and "223" in tiles and "204" in tiles and "2.4G throughput below threshold" in tiles, tiles.replace("\n", " | ")[:200])
    nexts = page.locator("#failRows .fl-next").all_inner_texts()
    check("every row has a next step: retest, check the station (bursts), RD", "Retest once" in nexts and any("Check the station" in n for n in nexts)
          and any("To RD" in n for n in nexts), sorted(set(nexts)))
    check("every row has a disposition badge 'To handle'", page.locator("#failRows .badge", has_text="To handle").count() == 15)
    check("Fail List fits the width (no sideways scroll in the table)", page.evaluate(
        "(() => { const w = document.querySelector('#page-fails .table-wrap'); return w.scrollWidth <= w.clientWidth + 1; })()"))
    shot("64_fail_list_to_handle")
    page.locator("#failRows [data-detail]").first.click()
    page.wait_for_selector("#failModal:not([hidden]) .fd-evidence", timeout=10000)
    body = page.inner_text("#fdBody")
    check("FAIL detail: category, next step, measurements, evidence lines, the unit's runs", "Next step" in body and "Evidence lines" in body
          and "Every test of this unit" in body and page.locator("#fdBody .fd-evidence li").count() >= 1)
    check("signed out: no disposition form, a sign-in hint", page.locator("#fdStatus").count() == 0 and "Sign in" in body)
    shot("65_fail_detail_signed_out")
    page.keyboard.press("Escape")
    check("Escape closes the window", page.locator("#failModal").is_hidden())

    # sign in as RD and set a disposition
    page.goto(f"{BASE}/#logupload", wait_until="networkidle")
    page.fill("#luUser", RD[0]); page.fill("#luPass", RD[1]); page.click("#btnLuLogin")
    page.wait_for_selector("#authBox:not([hidden])", timeout=10000)
    page.goto(f"{BASE}/", wait_until="networkidle"); page.wait_for_timeout(800)
    check("role landing: RD on the home address lands on Advanced Analysis", page.inner_text("#pageTitle") == "Advanced Analysis", page.inner_text("#pageTitle"))
    page.goto(f"{BASE}/#fails", wait_until="networkidle")
    page.select_option("#woSelect", "")
    page.wait_for_selector("#failRows [data-detail]", timeout=15000)
    sn = page.locator("#failRows [data-detail]").first.inner_text()
    page.locator("#failRows [data-detail]").first.click()
    page.wait_for_selector("#fdStatus", timeout=10000)
    page.select_option("#fdStatus", "repair")
    page.fill("#fdNote", "e2e: sent to the repair bench")
    page.click("#btnFdSave")
    page.wait_for_selector("#fdBody .fd-history li", timeout=10000)
    check("RD saves 'Sent to repair': the badge and the history update", "Sent to repair" in page.inner_text("#fdBody")
          and "open → repair" in page.inner_text("#fdBody .fd-history"))
    shot("66_fail_detail_saved")
    page.keyboard.press("Escape"); page.wait_for_timeout(1000)
    check("the unit leaves 'To handle' (14 left)", page.locator("#failRows tr").count() == 14 and sn not in page.inner_text("#failRows"))
    page.click("#flDispSeg [data-disp='handled']"); page.wait_for_timeout(1000)
    check("'Handled' shows it with its badge", page.locator("#failRows tr").count() == 1 and "Sent to repair" in page.inner_text("#failRows"))
    page.locator("#failRows [data-detail]").first.click()
    page.wait_for_selector("#fdStatus", timeout=10000)
    page.select_option("#fdStatus", "open"); page.fill("#fdNote", ""); page.click("#btnFdSave")
    page.wait_for_function("document.querySelector('#fdBody .fd-history') && document.querySelector('#fdBody .fd-history').innerText.includes('repair → open')", timeout=10000)
    page.keyboard.press("Escape"); page.wait_for_timeout(800)
    page.click("#flDispSeg [data-disp='pending']"); page.wait_for_timeout(1000)
    check("put back to 'To handle': 15 again", page.locator("#failRows tr").count() == 15)

    # ── Work-order detail: FAIL table with next step and disposition ──
    page.goto(f"{BASE}/#wo?wo=5101-260102014&pm=TN-86_PICO-IMX7", wait_until="networkidle")
    page.wait_for_selector("#wdFailRows [data-detail]", timeout=15000)
    check("detail page FAIL table: next step and disposition per unit", page.locator("#wdFailRows .fl-next").count() == 7
          and page.locator("#wdFailRows .badge", has_text="To handle").count() == 7)
    page.locator("#wdFailRows [data-detail]").first.click()
    page.wait_for_selector("#failModal:not([hidden]) #fdStatus", timeout=10000)
    check("a SN on the detail page opens the same FAIL window", "FAIL ·" in page.inner_text("#fdTitle"))
    page.keyboard.press("Escape")
    page.click("#btnLogout"); page.wait_for_timeout(500)

    # ── Settings: the idle warning field; administrator lands on Trend ──
    page.goto(f"{BASE}/#settings", wait_until="networkidle")
    page.fill("#setUser", ADMIN[0]); page.fill("#setPass", ADMIN[1]); page.click("#btnSetLogin")
    page.wait_for_selector("#setPanel:not([hidden])", timeout=10000)
    page.wait_for_timeout(800)
    check("Settings shows the working days (Mon–Fri ticked)", page.locator("#setWorkDays input:checked").count() == 5
          and not page.locator("#setWorkDays [data-day='6']").is_checked())
    page.goto(f"{BASE}/", wait_until="networkidle"); page.wait_for_timeout(800)
    check("role landing: the administrator lands on Trend", page.inner_text("#pageTitle") == "Trend", page.inner_text("#pageTitle"))
    page.click("#btnLogout")
    browser.close()

passed = sum(results)
print(f"\n{passed}/{len(results)} Phase 5C browser checks passed; problems: {len(problems)}")
for x in problems:
    print("  ", x)
sys.exit(0 if passed == len(results) and not problems else 1)
