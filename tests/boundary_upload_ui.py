"""Browser boundary pass for Log Upload: the page must stop the user and say why.

    python3 tests/boundary_upload_ui.py http://127.0.0.1:8000 <out_dir> <sample.zip> [user pass]

Signs in as an uploader, analyzes the sample zip and, in form mode, enters wrong work orders / products /
quantities: each must show its ❌ message and keep "Upload" disabled. Then the Upload button is
force-enabled with JavaScript and clicked with a wrong work order — the server must refuse and the page
must show the error, with nothing written. HTML typed into the fields must render as text.
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = sys.argv[2] if len(sys.argv) > 2 else "docs-screenshots"
SAMPLE = sys.argv[3] if len(sys.argv) > 3 else ""
USER = sys.argv[4] if len(sys.argv) > 4 else "test_uploader"
PASS = sys.argv[5] if len(sys.argv) > 5 else "uploader-pass-2026"
os.makedirs(OUT, exist_ok=True)
results, problems, dialogs = [], [], []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_context(viewport={"width": 1600, "height": 1100}).new_page()
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    page.goto(f"{BASE}/#logupload", wait_until="networkidle")
    page.fill("#luUser", USER)
    page.fill("#luPass", PASS)
    page.click("#btnLuLogin")
    page.wait_for_selector("#luPanel:not([hidden])", timeout=10000)
    page.set_input_files("#luFile", SAMPLE)
    page.wait_for_selector("#luStepConfirm:not([hidden])", timeout=120000)
    page.wait_for_timeout(800)
    if page.locator("#luFormPanel").is_hidden():
        page.click("#btnLuMode")
    page.wait_for_timeout(300)

    def fill(product=None, new_product=None, wo=None, qty=None):
        if new_product is not None:
            page.select_option("#luProduct", "__new__")
            page.fill("#luProductNew", new_product)
        elif product is not None:
            page.select_option("#luProduct", product)
        if wo is not None:
            page.fill("#luWo", wo)
        if qty is not None:
            page.fill("#luQty", qty)
        page.wait_for_timeout(900)
        return [x.strip() for x in page.locator("#luIssues li.error").all_inner_texts()]

    def blocked(label, shot, **kw):
        errs = fill(**kw)
        disabled = page.locator("#btnLuUpload").is_disabled()
        check(f"{label}: ❌ shown, Upload disabled", errs and disabled, errs[0][:110] if errs else "no error shown")
        if shot:
            page.screenshot(path=f"{OUT}/{shot}.png", full_page=True)

    blocked("work order with month 13", "b01_wo_bad_date", product="TN-99_EDM1-IMX6P", wo="5101-261340007", qty="252")
    blocked("work order without hyphen", None, wo="5101251216015")
    blocked("NOWO without a number (NOWO001)", "b02_nowo_bad", wo="NOWO001")
    blocked("product typo with a taken TN (TN-86_PICO-IMX6)", "b03_pm_same_tn", new_product="TN-86_PICO-IMX6", wo="5101-251216015")
    sugg = page.locator("#luIssues [data-pick]").all_inner_texts()
    check("  … offers the existing product as a one-click fix", "TN-86_PICO-IMX7" in sugg, ", ".join(sugg))
    blocked("product with a trailing hyphen", None, new_product="TN-99_EDM1-IMX6P-")
    blocked("desktop placeholder TNXXX-XXXX-XXXXXX", None, new_product="TNXXX-XXXX-XXXXXX")
    blocked("quantity 0", "b04_qty_zero", product="TN-99_EDM1-IMX6P", qty="0")
    blocked("quantity 100000", None, qty="100000")

    # HTML in the fields must stay text
    fill(wo="<img src=x onerror=alert(1)>", qty="252")
    html_text = page.locator("#luIssues li.error").first.inner_text()
    check("HTML typed into the work order renders as text (no script)", "<img src=x" in html_text and not dialogs, html_text[:80])

    # Force the button on and submit a wrong work order: the server must refuse
    fill(wo="5101-261340007", qty="252")
    page.evaluate("document.getElementById('btnLuUpload').disabled = false")
    page.click("#btnLuUpload")                 # the confirm() dialog is accepted by the handler
    page.wait_for_timeout(2500)
    # The wizard (5D) goes back to step 2 with the server's refusal on top of the checks, so the values can be fixed.
    msg = page.locator("#luIssues li.error").first.inner_text() if page.locator("#luIssues li.error").count() else ""
    check("forced Upload with a wrong work order: server refuses, the page is back on step 2 with the error",
          page.locator("#luStepConfirm").is_visible() and page.locator("#luStepProgress").is_hidden() and "❌" in msg, msg[:110])
    page.screenshot(path=f"{OUT}/b05_forced_upload_refused.png", full_page=True)
    hist = page.locator("#luHistoryRows").inner_text()
    check("nothing recorded as uploaded", "5101-261340007" not in hist)

    # Correct values afterwards: the same batch is still usable
    errs = fill(wo="5101-251216015", qty="252")
    check("after fixing the values the batch can still be uploaded", not errs and not page.locator("#btnLuUpload").is_disabled())
    page.click("#btnLuReset")
    browser.close()

passed = sum(results)
print(f"\n{passed}/{len(results)} UI boundary checks passed; page errors: {len(problems)}; unexpected dialogs: {len(dialogs) - 1 if dialogs else 0}")
for x in problems:
    print("  ", x)
sys.exit(0 if passed == len(results) and not problems else 1)
