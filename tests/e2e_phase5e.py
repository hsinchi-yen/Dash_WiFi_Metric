#!/usr/bin/env python3
"""Browser pass for Phase 5E: the anomaly banner (replaying 01/13 11:00: items, link to the Fail List, dismiss, still
1080p without scrolling), Ask the dashboard (an example question, how it was understood, "open on Trend"), and the
Reports page (list, a report, Markdown download, writing one as the administrator, nothing to write for others).

    python3 tests/e2e_phase5e.py [BASE] [OUT_DIR] [admin_user admin_pass]
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = sys.argv[2] if len(sys.argv) > 2 else "docs-screenshots"
ADMIN = (sys.argv[3], sys.argv[4]) if len(sys.argv) > 4 else (os.environ.get("TWEAK_USER", "qc"), os.environ.get("TWEAK_PASS", "qcpass"))
os.makedirs(OUT, exist_ok=True)
results, problems = [], []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""), flush=True)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_context(locale="zh-TW", viewport={"width": 1920, "height": 1080}, accept_downloads=True).new_page()
    # A failed resource load is reported by the response handler below (the shared LLM may answer 503 once: retried).
    page.on("console", lambda m: problems.append(f"console.{m.type}: {m.text}")
            if m.type in ("error", "warning") and not m.text.startswith("Failed to load resource") else None)
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on("response", lambda r: problems.append(f"HTTP {r.status} {r.url}") if r.status >= 400 and "/api/ask" not in r.url else None)

    def shot(name, full=False):
        page.wait_for_timeout(800)
        page.screenshot(path=f"{OUT}/{name}.png", full_page=full)
        print("shot", name)

    # ── banner (replay) ──
    page.goto(f"{BASE}/#overview", wait_until="networkidle")
    page.wait_for_timeout(1000)
    check("no banner now (nothing in the last 24 h of the local copy)", page.locator("#alertBar").is_hidden())
    page.goto(f"{BASE}/?replay=2026-01-13T11:00#overview", wait_until="networkidle")
    page.wait_for_selector("#alertBar:not([hidden]) .alert-item", timeout=10000)
    text = page.inner_text("#alertBar")
    check("replay 01/13 11:00: the banner names both red work orders and the 2.4G burst", "5101-251126008" in text and "5105-260102001" in text
          and "先查測試站" in text and "回放" in text, text[:160])
    check("the Trend page with the banner still fits 1920×1080", page.evaluate("document.documentElement.scrollHeight <= window.innerHeight + 1"))
    shot("90_banner_replay_zh")
    page.locator("#alertBar .alert-item", has_text="先查測試站").first.click()
    page.wait_for_selector("#page-fails:not([hidden])", timeout=10000)
    page.wait_for_timeout(1200)
    check("a burst links to the Fail List of that work order", page.input_value("#woSelect") == "5101-251126008", page.input_value("#woSelect"))
    page.click("#btnAlertClose")
    check("× hides the banner", page.locator("#alertBar").is_hidden())
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1500)
    check("…and it stays hidden for the same alerts", page.locator("#alertBar").is_hidden())

    # ── ask the dashboard ──
    page.goto(f"{BASE}/#overview", wait_until="networkidle")
    page.click("#btnAsk")
    check("💬 opens Ask the dashboard with example questions", page.locator("#askModal").is_visible() and page.locator("#askChips button").count() == 4)
    page.click("#askChips button >> nth=0")       # 上個月測了幾台？
    page.wait_for_selector("#askLog .msg.assistant:not(.typing)", timeout=150000)
    page.wait_for_timeout(500)
    ans = page.locator("#askLog .msg.assistant").last.inner_text()
    if "失敗" in ans or "離線" in ans:
        print("  (the shared LLM was busy; one retry)", flush=True)
        page.wait_for_timeout(15000)
        page.click("#askChips button >> nth=0")
        page.wait_for_function("document.querySelectorAll('#askLog .msg.assistant:not(.typing)').length >= 2", timeout=150000)
        ans = page.locator("#askLog .msg.assistant").last.inner_text()
    check("the answer gives September's 4,734 units and how the question was understood", "4,734" in ans and "理解為" in ans, ans[:160])
    shot("91_ask_zh")
    page.locator("#askLog [data-scope]").last.click()
    page.wait_for_timeout(1500)
    check("'open on Trend' shows that scope: custom 09/01–09/30, 4,734 units", page.locator("#askModal").is_hidden()
          and page.input_value("#dateFrom") == "2026-09-01" and page.inner_text("#ovUnits") == "4,734", page.inner_text("#ovUnits"))
    page.click("#rangeSeg button[data-range='all']")

    # ── reports ──
    page.goto(f"{BASE}/#reports", wait_until="networkidle")
    page.wait_for_selector("#repRows [data-open]", timeout=10000)
    page.wait_for_timeout(800)
    import json
    import urllib.request
    items = json.load(urllib.request.urlopen(f"{BASE}/api/period-reports", timeout=30))["items"]
    newest = next(x for x in items if x["units"])
    check("Reports: the list, and the newest report with tests is opened", page.locator("#repRows tr").count() == len(items) >= 2
          and newest["period_start"] in page.inner_text("#repTitle"), (page.inner_text("#repTitle"), newest["period_start"]))
    check("signed out: no 'write a report' panel", page.locator("#repAdmin").is_hidden())
    with page.expect_download() as dl:
        page.click("#btnRepMdZh")
    check("Markdown download (中文)", dl.value.suggested_filename.endswith("_zh.md"), dl.value.suggested_filename)
    shot("92_reports_zh", full=True)
    page.goto(f"{BASE}/#settings", wait_until="networkidle")
    page.fill("#setUser", ADMIN[0]); page.fill("#setPass", ADMIN[1]); page.click("#btnSetLogin")
    page.wait_for_selector("#setPanel:not([hidden])", timeout=10000)
    page.goto(f"{BASE}/#reports", wait_until="networkidle")
    page.wait_for_selector("#repAdmin:not([hidden])", timeout=10000)
    page.select_option("#repGenKind", "week")
    page.fill("#repGenDate", "2026-09-16")
    page.click("#btnRepGenerate")
    page.wait_for_function("document.querySelector('#repTitle').innerText.includes('2026-09-14')", timeout=200000)
    check("administrator writes the week of 09/16 (→ 09/14 ~ 09/20) and it opens", "2026-09-14 ~ 2026-09-20" in page.inner_text("#repTitle"))
    page.click("#btnLang"); page.wait_for_timeout(800)
    check("English: the report view switches language", "Weekly" in page.inner_text("#repTitle") or "Week" in page.inner_text("#repTitle"))
    shot("93_reports_en")
    page.click("#btnLogout")
    browser.close()

passed = sum(results)
print(f"\n{passed}/{len(results)} Phase 5E browser checks passed; problems: {len(problems)}")
for x in problems:
    print("  ", x)
sys.exit(0 if passed == len(results) and not problems else 1)
