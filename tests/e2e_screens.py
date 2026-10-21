"""Browser smoke run of the web/ dashboard: visits every page, tab, filter, theme and language,
saves screenshots and reports console errors / failed requests.

    pip install playwright && python -m playwright install chromium   # once
    python3 tests/e2e_screens.py http://127.0.0.1:8000 /tmp/shots
"""
import json, sys, time
from playwright.sync_api import sync_playwright
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = sys.argv[2] if len(sys.argv) > 2 else "docs-screenshots"
PAGES = ["overview", "workorders", "fails", "rf", "production", "logupload", "products", "dbtweak", "accounts", "settings"]
problems = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
    page = ctx.new_page()
    page.on("console", lambda m: problems.append(f"console.{m.type}: {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on("response", lambda r: problems.append(f"HTTP {r.status} {r.url}") if r.status >= 400 else None)

    def shot(name):
        page.wait_for_timeout(1800)
        page.screenshot(path=f"{OUT}/{name}.png")
        print("shot", name, "| title:", page.title(), "| scope:", page.locator("#pageScope").inner_text()[:60])

    page.goto(f"{BASE}/#overview", wait_until="networkidle")
    shot("01_overview_dark_en")
    print("  numbers:", [page.locator(s).inner_text() for s in ("#ovYield", "#ovUnits", "#ovPass", "#ovFail", "#ovRetry", "#ovAvg24", "#ovAvg5")])
    print("  freshness:", page.locator("#freshText").inner_text(), "|", page.locator("#apiText").inner_text(), "|", page.locator("#llmText").inner_text())
    for pg in PAGES[1:]:
        page.goto(f"{BASE}/#{pg}", wait_until="networkidle")
        shot(f"02_{pg}_dark_en")
    # RF tabs
    page.goto(f"{BASE}/#rf", wait_until="networkidle")
    page.click("[data-rf-tab='5g']"); shot("03_rf_5g")
    page.click("[data-rf-tab='bt']"); shot("03_rf_bt")
    # filter: All data on overview
    page.goto(f"{BASE}/#overview", wait_until="networkidle")
    page.click("#rangeSeg button[data-range='all']")
    page.select_option("#woSelect", "")
    shot("04_overview_all_data")
    # product filter
    page.select_option("#productSelect", "TN-99_EDM1-IMX6P")
    shot("04_overview_product_tn99")
    # light theme + Chinese
    page.click("#btnTheme"); shot("05_overview_light_en")
    page.click("#btnLang"); shot("06_overview_light_zh")
    page.goto(f"{BASE}/#workorders", wait_until="networkidle"); shot("06_workorders_light_zh")
    page.click("#btnTheme")   # back to dark, stay zh
    # AI report modal (do not wait for the LLM; just the window)
    page.goto(f"{BASE}/#workorders", wait_until="networkidle")
    page.click("#woRows [data-report]"); page.wait_for_timeout(2500); page.screenshot(path=f"{OUT}/07_report_modal.png"); print("shot 07_report_modal", page.locator("#rpTitle").inner_text())
    page.keyboard.press("Escape")
    # phone
    phone = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
    phone.goto(f"{BASE}/#overview", wait_until="networkidle"); phone.wait_for_timeout(1500)
    phone.screenshot(path=f"{OUT}/08_phone_overview.png", full_page=True); print("shot 08_phone_overview")
    # legacy page reachable
    r = page.goto(f"{BASE}/legacy/", wait_until="domcontentloaded"); print("legacy:", r.status, page.title())
    browser.close()
print("\n== problems:", len(problems))
for x in problems[:30]: print("  ", x[:200])
