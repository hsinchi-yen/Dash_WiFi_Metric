#!/usr/bin/env python3
"""Browser pass for Phase 5A: Work Orders status / note / column switch, Settings page, language default.

    python3 tests/e2e_phase5a.py [BASE] [OUT_DIR] [admin_user admin_pass] [uploader uploader_pass]

Changes one setting through the page and puts it back. Fails on console errors / warnings, page
errors or unexpected HTTP ≥ 400.
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = sys.argv[2] if len(sys.argv) > 2 else "docs-screenshots"
ADMIN = (sys.argv[3], sys.argv[4]) if len(sys.argv) > 4 else (os.environ.get("TWEAK_USER", "qc"), os.environ.get("TWEAK_PASS", "qcpass"))
UPLOADER = (sys.argv[5], sys.argv[6]) if len(sys.argv) > 6 else ("test_uploader", "uploader-pass-2026")
os.makedirs(OUT, exist_ok=True)
results, problems = [], []
expected_400 = {"on": False}


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""), flush=True)


def watch(page):
    page.on("console", lambda m: problems.append(f"console.{m.type}: {m.text}")
            if m.type in ("error", "warning") and not (expected_400["on"] and "400" in m.text) else None)
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on("response", lambda r: problems.append(f"HTTP {r.status} {r.url}")
            if r.status >= 400 and not (expected_400["on"] and r.status == 400 and "/api/settings" in r.url) else None)


with sync_playwright() as p:
    browser = p.chromium.launch()

    # ── Language default (Q6): browser language, unless ?lang= or a saved choice ──
    for locale, url, want in (("zh-TW", "/#workorders", "zh"), ("en-US", "/#workorders", "en"), ("en-US", "/?lang=zh#workorders", "zh")):
        ctx = browser.new_context(locale=locale, viewport={"width": 1280, "height": 800})
        pg = ctx.new_page()
        watch(pg)
        pg.goto(BASE + url, wait_until="networkidle")
        lang = pg.evaluate("document.documentElement.lang")
        check(f"browser {locale}, {url} → {'中文' if want == 'zh' else 'English'}", lang.startswith(want), lang)
        ctx.close()

    ctx = browser.new_context(locale="en-US", viewport={"width": 1920, "height": 1080})
    page = ctx.new_page()
    watch(page)

    def shot(name, full=False):
        page.wait_for_timeout(900)
        page.screenshot(path=f"{OUT}/{name}.png", full_page=full)
        print("shot", name)

    # ── Work Orders ──
    page.goto(f"{BASE}/#workorders", wait_until="networkidle")
    page.select_option("#woSelect", "")
    page.wait_for_selector("#woRows tr.wo-row", timeout=15000)
    page.wait_for_timeout(800)
    rows = page.locator("#woRows tr.wo-row")
    check("all 77 work orders listed with a status", rows.count() == 77 and page.locator("#woRows .wo-state").count() == 77, rows.count())
    check("3 red rows, 4 amber rows", page.locator("#woRows tr.light-bad").count() == 3 and page.locator("#woRows tr.light-warn").count() == 4)
    heads = page.locator("#woHead th").all_inner_texts()
    check("basic columns: 10 (compare, status, work order, product, tested / plan, yield, FAIL, note, last test, report)", len(heads) == 10, heads)
    note = page.locator("#woRows tr.light-bad .wo-note").first
    check("a red row's note names its problem", "94.87" in note.inner_text() or "row" in note.inner_text(), note.inner_text())
    check("AI-worded notes (red / amber work orders only) carry the AI tag", page.locator("#woRows .ai-tag").count() == 7, page.locator("#woRows .ai-tag").count())
    title = page.locator("#woRows tr.light-bad .wo-state").first.get_attribute("title")
    check("the status pill's tooltip lists the checks", title.startswith("Checks:"), title)
    shot("30_workorders_basic_en")
    page.click("#woColsSeg [data-cols='all']")
    page.wait_for_timeout(300)
    check("all columns: 16", page.locator("#woHead th").count() == 16)
    shot("31_workorders_all_en")
    page.reload(wait_until="networkidle")
    page.select_option("#woSelect", "")
    page.wait_for_timeout(1200)
    check("the column choice is remembered", page.locator("#woHead th").count() == 16)
    page.click("#woColsSeg [data-cols='basic']")
    page.click("#btnLang")
    page.wait_for_timeout(1200)
    states = set(page.locator("#woRows .wo-state").all_inner_texts())
    check("中文: state words and notes switch", states == {"已完成"} and "已完成" in page.locator("#woRows .wo-note").first.inner_text(), states)
    shot("32_workorders_basic_zh")
    page.click("#btnTheme")
    shot("33_workorders_light_zh")
    page.click("#btnTheme")
    page.click("#btnLang")

    # ── Settings: signed out → sign-in card; uploader → denied; administrator → form ──
    page.goto(f"{BASE}/#settings", wait_until="networkidle")
    check("Settings asks to sign in", page.locator("#setLogin").is_visible() and not page.locator("#setPanel").is_visible())
    check("the menu does not advertise Settings before an administrator signs in",
          not page.locator(".nav-item[data-page='settings']").is_visible())
    page.fill("#setUser", UPLOADER[0]); page.fill("#setPass", UPLOADER[1]); page.click("#btnSetLogin")
    page.wait_for_timeout(1200)
    check("an uploader sees the administrators-only message", page.locator("#setDenied").is_visible() and not page.locator("#setPanel").is_visible())
    page.click("#btnLogout"); page.wait_for_timeout(500)
    page.goto(f"{BASE}/#settings", wait_until="networkidle")
    page.fill("#setUser", ADMIN[0]); page.fill("#setPass", ADMIN[1]); page.click("#btnSetLogin")
    page.wait_for_selector("#setPanel:not([hidden])", timeout=10000)
    page.wait_for_timeout(800)
    check("administrator: the form shows 98 / 95 and 07:00–19:00",
          page.input_value("#setYieldGood") == "98" and page.input_value("#setYieldWarn") == "95"
          and page.input_value("#setWorkStart") == "07:00" and page.input_value("#setWorkEnd") == "19:00",
          [page.input_value(s) for s in ("#setYieldGood", "#setYieldWarn", "#setWorkStart", "#setWorkEnd")])
    check("the hourly-target table lists the 11 products", page.locator("#setUphRows tr").count() == 11)
    check("Settings is in the menu for the administrator", page.locator(".nav-item[data-page='settings']").is_visible())
    shot("34_settings_admin_en", full=True)

    # invalid: amber not below green → message, field marked, nothing saved
    expected_400["on"] = True
    page.fill("#setYieldWarn", "99")
    page.click("#btnSetSave")
    page.wait_for_timeout(1000)
    expected_400["on"] = False
    msg = page.locator("#setMsg").inner_text()
    check("amber ≥ green is refused with a message and the field marked",
          "below" in msg and "invalid" in (page.get_attribute("#setYieldWarn", "class") or ""), msg)
    shot("35_settings_invalid")
    page.fill("#setYieldWarn", "95")

    # change the TN-86 hourly target, then put it back
    target = page.locator("#setUphRows input[data-product='TN-86_PICO-IMX7']")
    target.fill("60")
    page.click("#btnSetSave")
    page.wait_for_timeout(1500)
    row = page.locator("#setUphRows tr", has_text="TN-86_PICO-IMX7").inner_text()
    check("saving the TN-86 target 60: the table shows it in use", "60.0" in row and "setting" in row, row)
    page.goto(f"{BASE}/#workorders", wait_until="networkidle")
    page.select_option("#woSelect", "")
    page.click("#woColsSeg [data-cols='all']")
    page.wait_for_timeout(1500)
    tn86 = page.locator("#woRows tr", has_text="TN-86_PICO-IMX7").first.inner_text()
    check("Work Orders: TN-86 rows show the target 60", "/ 60.0" in tn86, tn86.replace("\n", " ")[:160])
    page.click("#woColsSeg [data-cols='basic']")
    page.goto(f"{BASE}/#settings", wait_until="networkidle")
    page.wait_for_timeout(800)
    page.click("#btnSetDefaults")
    check("Fill in defaults tells you to save", "Save" in page.locator("#setMsg").inner_text())
    page.click("#btnSetSave")
    page.wait_for_timeout(1500)
    row = page.locator("#setUphRows tr", has_text="TN-86_PICO-IMX7").inner_text()
    check("after saving the defaults TN-86 is back on its usual rate", "54.1" in row and "usual" in row, row)
    page.click("#btnLang"); page.wait_for_timeout(800)
    shot("36_settings_admin_zh", full=True)
    page.click("#btnLang")
    page.click("#btnLogout")
    browser.close()

passed = sum(results)
print(f"\n{passed}/{len(results)} Phase 5A browser checks passed; problems: {len(problems)}")
for x in problems:
    print("  ", x)
sys.exit(0 if passed == len(results) and not problems else 1)
