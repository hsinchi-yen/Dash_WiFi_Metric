#!/usr/bin/env python3
"""Browser pass for the Trend page's Forecast view (2026-10-21): the Trend ↔ Forecast switch, the latest work order's
completion forecast (with a planned quantity set for the test and cleared again), the output forecast for all data,
1080p without scrolling in both languages, and the TV rotation (?tv=1): latest work order Trend → Forecast → all work
orders Trend → Forecast, 30 s each.

    python3 tests/e2e_forecast.py [BASE] [OUT_DIR] [admin_user admin_pass]

Takes about three minutes (the rotation waits 30 s per step; a full cycle is four steps).
"""
import json
import os
import re
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = sys.argv[2] if len(sys.argv) > 2 else "docs-screenshots"
ADMIN = (sys.argv[3], sys.argv[4]) if len(sys.argv) > 4 else (os.environ.get("TWEAK_USER", "qc"), os.environ.get("TWEAK_PASS", "qcpass"))
WO, PM = "5101-260820009", "TN-250_EDM-G-8MP"
os.makedirs(OUT, exist_ok=True)
results, problems = [], []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""), flush=True)


def api(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(), method=method,
                                 headers={"Content-Type": "application/json", **({"X-Tweak-Token": token} if token else {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


token = api("POST", "/api/auth/login", {"username": ADMIN[0], "password": ADMIN[1]})["token"]
api("PUT", "/api/wo-plan", {"work_order": WO, "product_model": PM, "planned_qty": 800}, token)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()

        def new_page(locale):
            pg = browser.new_context(locale=locale, viewport={"width": 1920, "height": 1080}).new_page()
            pg.on("console", lambda m: problems.append(f"console.{m.type}: {m.text}") if m.type in ("error", "warning") else None)
            pg.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
            pg.on("response", lambda r: problems.append(f"HTTP {r.status} {r.url}") if r.status >= 400 else None)
            return pg

        page = new_page("en-US")

        def shot(name, pg=None):
            (pg or page).wait_for_timeout(900)
            (pg or page).screenshot(path=f"{OUT}/{name}.png")
            print("shot", name)

        no_scroll = lambda pg: pg.evaluate("document.documentElement.scrollHeight <= window.innerHeight + 1")  # noqa: E731

        page.goto(f"{BASE}/", wait_until="networkidle")
        page.wait_for_timeout(1200)
        check("home = Trend, with the Trend | Forecast switch (Trend pressed)", page.inner_text("#pageTitle") == "Trend"
              and page.locator("#ovViewSeg").is_visible() and page.get_attribute("#ovViewSeg [data-view='trend']", "aria-pressed") == "true")
        page.click("#ovViewSeg [data-view='forecast']")
        page.wait_for_function("document.querySelector('#fc5 .label').innerText.length > 0", timeout=15000)
        page.wait_for_timeout(1000)
        check("Forecast: the address says so and the title reads Forecast", page.url.endswith("#overview?view=forecast")
              and page.inner_text("#pageTitle") == "Forecast")
        check("the latest work order's completion: 500 / 800 tested, 300 remaining",
              re.sub(r"\s+", "", page.inner_text("#fc1 .value")) == "500/800" and page.inner_text("#fc2 .value") == "300",
              [page.inner_text(f"#fc{i} .value") for i in (1, 2)])
        check("an estimated finish with the working days it needs", re.match(r"\d\d/\d\d \d\d:\d\d", page.inner_text("#fc5 .value"))
              and "working days left" in page.inner_text("#fc5 .sub"), page.inner_text("#fc5"))
        check("the note says the work order is idle and the finish assumes testing resumes now", "assumes testing resumes now" in page.inner_text("#fcNote"))
        check("Trend charts hidden, the two forecast charts drawn", page.locator("#page-overview .ov-charts").is_hidden()
              and page.locator("#chFc1").is_visible() and page.locator("#chFc2").is_visible())
        check("fits 1920×1080 without scrolling", no_scroll(page))
        shot("80_forecast_wo_en")
        page.select_option("#woSelect", "")
        page.wait_for_function("document.querySelector('#fc1 .label').innerText.includes('Week')", timeout=15000)
        page.wait_for_timeout(800)
        check("all work orders → the output forecast (this week / this month so far and projected)",
              [page.inner_text(f"#fc{i} .label") for i in (1, 2, 3, 4)] == ["This Week So Far", "This Week Projected", "This Month So Far", "This Month Projected"])
        check("the note explains the average and that the line has been idle", "working days" in page.inner_text("#fcNote") and "no test for" in page.inner_text("#fcNote"))
        check("fits 1920×1080 without scrolling (all data)", no_scroll(page))
        shot("81_forecast_all_en")
        page.click("#ovViewSeg [data-view='trend']")
        page.wait_for_timeout(1200)
        check("back to Trend: the trend numbers and charts again", page.url.endswith("#overview") and page.locator("#ovYield").is_visible()
              and page.locator("#page-overview [data-ov-view='forecast']").first.is_hidden())
        page.goto(f"{BASE}/#workorders", wait_until="networkidle")
        check("the switch is only on the Trend page", page.locator("#ovViewSeg").is_hidden())
        page.close()

        zh = new_page("zh-TW")
        zh.goto(f"{BASE}/#overview?view=forecast", wait_until="networkidle")
        zh.wait_for_function("document.querySelector('#fc5 .label').innerText.length > 0", timeout=15000)
        zh.wait_for_timeout(1000)
        check("中文: 預測、預計完成、還需 N 個工作天", zh.inner_text("#pageTitle") == "預測" and zh.inner_text("#fc5 .label") == "預計完成"
              and "個工作天" in zh.inner_text("#fc5 .sub"))
        check("中文 fits 1080p", no_scroll(zh))
        shot("82_forecast_wo_zh", zh)
        zh.close()

        tv = new_page("zh-TW")
        tv.goto(f"{BASE}/?tv=1&lang=zh", wait_until="networkidle")
        latest_wo = tv.input_value("#woSelect")
        check("TV address opens Trend on the latest work order, its product named in the header",
              tv.inner_text("#pageTitle") == "趨勢" and latest_wo and tv.locator("#pageScope .scope-product").count() == 1,
              (latest_wo, tv.inner_text("#pageScope")))
        tv.wait_for_function("location.hash === '#overview?view=forecast'", timeout=45000)
        tv.wait_for_timeout(1500)
        check("after 30 s: Forecast, still the latest work order", tv.inner_text("#pageTitle") == "預測" and tv.input_value("#woSelect") == latest_wo)
        tv.wait_for_function("location.hash === '#overview'", timeout=45000)
        tv.wait_for_timeout(1500)
        check("after 60 s: Trend for all work orders (全部產品 · 全部工單)", tv.inner_text("#pageTitle") == "趨勢" and tv.input_value("#woSelect") == ""
              and "全部工單" in tv.inner_text("#pageScope"), (tv.input_value("#woSelect"), tv.inner_text("#pageScope")))
        tv.wait_for_function("location.hash === '#overview?view=forecast'", timeout=45000)
        tv.wait_for_timeout(1500)
        check("after 90 s: Forecast for all work orders", tv.inner_text("#pageTitle") == "預測" and tv.input_value("#woSelect") == "")
        tv.wait_for_function("location.hash === '#overview'", timeout=45000)
        tv.wait_for_timeout(1500)
        check("after 120 s: back to Trend on the latest work order", tv.inner_text("#pageTitle") == "趨勢" and tv.input_value("#woSelect") == latest_wo)
        check("the TV never scrolls (1920×1080, one-row header)", tv.evaluate("document.documentElement.scrollHeight <= 1080 && "
              "document.documentElement.scrollWidth <= 1920 && document.querySelector('.topbar').getBoundingClientRect().height < 70"))
        tv.close()
        browser.close()
finally:
    api("DELETE", f"/api/wo-plan?work_order={WO}", None, token)

passed = sum(results)
print(f"\n{passed}/{len(results)} forecast browser checks passed; problems: {len(problems)}")
for x in problems:
    print("  ", x)
sys.exit(0 if passed == len(results) and not problems else 1)
