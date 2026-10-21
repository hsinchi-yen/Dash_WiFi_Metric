#!/usr/bin/env python3
"""Browser pass for Phase 5B: Dashboard (split left of yield, hourly output), Work Orders → detail page, comparison,
final report, follow-up question, planned quantity (administrator), Fail List link.

    python3 tests/e2e_phase5b.py [BASE] [OUT_DIR] [admin_user admin_pass]

Sets and clears one planned quantity. Fails on console errors / warnings, page errors or unexpected HTTP ≥ 400.
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = sys.argv[2] if len(sys.argv) > 2 else "docs-screenshots"
ADMIN = (sys.argv[3], sys.argv[4]) if len(sys.argv) > 4 else (os.environ.get("TWEAK_USER", "qc"), os.environ.get("TWEAK_PASS", "qcpass"))
NORMAL, RED = "5101-260820009", "5101-260102014"
os.makedirs(OUT, exist_ok=True)
results, problems = [], []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""), flush=True)


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(locale="en-US", viewport={"width": 1920, "height": 1080})
    page = ctx.new_page()
    page.on("console", lambda m: problems.append(f"console.{m.type}: {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on("response", lambda r: problems.append(f"HTTP {r.status} {r.url}") if r.status >= 400 else None)

    def shot(name, full=False, wait=900):
        page.wait_for_timeout(wait)
        page.screenshot(path=f"{OUT}/{name}.png", full_page=full)
        print("shot", name)

    # ── Dashboard ──
    page.goto(f"{BASE}/#overview", wait_until="networkidle")
    page.wait_for_timeout(1500)
    tiles = page.locator(".ov-numbers > div")
    check("the PASS / FAIL split is the first tile, left of the yield",
          tiles.nth(0).get_attribute("class") == "ov-split" and tiles.nth(1).get_attribute("class") == "ov-hero")
    check("split legend shows PASS and FAIL counts", "PASS" in page.inner_text("#ovSplitLegend") and "FAIL" in page.inner_text("#ovSplitLegend"))
    check("Hourly Output takes the split's old place (top right)", page.locator(".ov-charts .card").nth(1).locator("#chOvHourly").count() == 1)
    check("hourly note: day and 07:00–19:00 with the target", "07:00–19:00" in page.inner_text("#ovHourlyNote") and "target" in page.inner_text("#ovHourlyNote"),
          page.inner_text("#ovHourlyNote"))
    check("the Dashboard still fits 1920×1080 without scrolling", page.evaluate("document.documentElement.scrollHeight") <= 1080)
    shot("40_dashboard_split_hourly_en")
    page.click("#btnLang"); page.click("#btnTheme")
    shot("41_dashboard_light_zh")
    page.click("#btnLang"); page.click("#btnTheme")

    # ── Work Orders → detail ──
    page.goto(f"{BASE}/#workorders", wait_until="networkidle")
    page.select_option("#woSelect", "")
    page.wait_for_selector("#woRows tr.wo-row", timeout=15000)
    page.wait_for_timeout(1200)
    page.locator("#woRows a.wo-link", has_text=RED).click()
    page.wait_for_selector("#wdPanel:not([hidden])", timeout=15000)
    page.wait_for_timeout(1200)
    check("the work order number opens the detail page", page.inner_text("#wdWo") == RED and "#wo?" in page.url)
    check("header: red status and the AI note", "light-bad" in page.get_attribute("#wdState", "class") and "4" in page.inner_text("#wdNote"))
    check("8 tiles", page.locator("#wdTiles .tile").count() == 8)
    checks = page.locator("#wdChecks li").all_inner_texts()
    check("checks list the FAIL streak and the low 2.4G", len(checks) == 2 and "row" in checks[0], checks)
    check("no earlier TN-86 work order → the comparison says so", "No earlier" in page.inner_text("#wdCmpRows"))
    page.select_option("#wdCompareSel", index=1)
    page.wait_for_timeout(1200)
    check("choosing another work order fills the comparison (10 rows)", page.locator("#wdCmpRows tr").count() == 10)
    page.wait_for_selector("#wdReport .wd-summary", timeout=240000)
    check("the final report shows summary, anomalies, possible causes, actions",
          page.locator("#wdReport h4").count() == 3 and page.locator("#wdReport li").count() >= 4, page.inner_text("#wdReport")[:160])
    check("the report says it is AI-written and number-checked", "checked" in page.inner_text("#wdReportMeta"))
    check("FAIL list of the work order: 7 units", page.locator("#wdFailRows tr").count() == 7)
    shot("42_detail_red_en", full=True)
    page.click("#wdAskChips button >> nth=0")
    page.wait_for_selector("#wdAskLog .msg.assistant:not(.typing)", timeout=90000)
    answer = page.locator("#wdAskLog .msg.assistant").last.inner_text()
    check("a suggested question gets an answer", len(answer) > 10, answer[:120])
    shot("43_detail_ask", full=True)
    page.click("#btnLang")
    page.wait_for_timeout(1200)
    check("中文: report and tiles switch", "異常" in page.inner_text("#wdReport") and "最多連續 FAIL" in page.inner_text("#wdTiles"))
    shot("44_detail_red_zh", full=True)
    page.click("#btnLang")

    # ── Normal work order: previous comparison, short report ──
    page.goto(f"{BASE}/#wo?wo={NORMAL}&pm=TN-250_EDM-G-8MP", wait_until="networkidle")
    page.wait_for_selector("#wdReport .wd-summary", timeout=240000)
    page.wait_for_timeout(800)
    check("normal work order: compared with the previous same-product one by default",
          "previous" in page.inner_text("#wdCmpNote") and "5101-260520007" in page.inner_text("#wdCmpNote"))
    check("normal work order: the report is a summary only", page.locator("#wdReport h4").count() == 0)
    shot("45_detail_normal_en", full=True)

    # ── Compare two from the list ──
    page.goto(f"{BASE}/#workorders", wait_until="networkidle")
    page.select_option("#woSelect", "")
    page.wait_for_selector("#woRows tr.wo-row", timeout=15000)
    page.wait_for_timeout(1200)
    check("Compare selected is disabled until two are picked", page.is_disabled("#btnWoCompare"))
    page.check(f"#woRows .wo-pick[data-wo='{NORMAL}']")
    page.check(f"#woRows .wo-pick[data-wo='{RED}']")
    third = page.locator("#woRows .wo-pick:not(:checked)").first
    third.click()
    check("a third pick is refused", not third.is_checked() and page.locator("#woRows .wo-pick:checked").count() == 2)
    page.click("#btnWoCompare")
    page.wait_for_selector("#wdPanel:not([hidden])", timeout=15000)
    page.wait_for_timeout(1500)
    check("Compare selected opens the first with the second compared", page.inner_text("#wdWo") == NORMAL and RED in page.inner_text("#wdCmpB"))

    # ── Planned quantity (administrator) ──
    page.goto(f"{BASE}/#settings", wait_until="networkidle")
    page.fill("#setUser", ADMIN[0]); page.fill("#setPass", ADMIN[1]); page.click("#btnSetLogin")
    page.wait_for_selector("#setPanel:not([hidden])", timeout=10000)
    page.goto(f"{BASE}/#wo?wo={NORMAL}&pm=TN-250_EDM-G-8MP", wait_until="networkidle")
    page.wait_for_selector("#btnWdPlan:not([hidden])", timeout=10000)
    page.click("#btnWdPlan")
    page.fill("#fm_planned_qty", "450")
    page.click("#btnFormModalSubmit")
    page.wait_for_selector("#formModal", state="hidden", timeout=10000)
    page.wait_for_timeout(1500)
    first = page.locator("#wdTiles .tile").first.inner_text()
    check("administrator sets 450: the first tile shows 500 / 450 set by the administrator", "450" in first and "administrator" in first, first)
    shot("46_detail_plan_admin")
    page.click("#btnWdPlan")
    page.fill("#fm_planned_qty", "")
    page.click("#btnFormModalSubmit")
    page.wait_for_selector("#formModal", state="hidden", timeout=10000)
    page.wait_for_timeout(1500)
    check("clearing the plan: back to no plan", "no plan" in page.locator("#wdTiles .tile").first.inner_text())
    page.click("#btnLogout")

    # ── Fail List → detail ──
    page.goto(f"{BASE}/#fails", wait_until="networkidle")
    page.select_option("#woSelect", "")
    page.wait_for_selector("#failRows a.wo-link", timeout=15000)
    page.locator("#failRows a.wo-link").first.click()
    page.wait_for_selector("#wdPanel:not([hidden])", timeout=15000)
    check("Fail List work order opens its detail page", page.inner_text("#wdWo").startswith("5"))
    browser.close()

passed = sum(results)
print(f"\n{passed}/{len(results)} Phase 5B browser checks passed; problems: {len(problems)}")
for x in problems:
    print("  ", x)
sys.exit(0 if passed == len(results) and not problems else 1)
