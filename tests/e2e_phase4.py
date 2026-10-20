"""Browser pass for Phase 4 (accounts, Log Upload, product registry) with Playwright.

    python3 tests/e2e_phase4.py http://127.0.0.1:8000 <out_dir> <sample.zip> [admin_user admin_pass]

Signs in as the administrator, creates / reuses the uploader `e2e_uploader` through the Accounts page,
signs in as that uploader, uploads the sample zip through the page (form mode; all 252 logs are
already in the database, so nothing is inserted), and screenshots every new page in both themes.
Reports console errors and failed requests; exit code 1 if any.
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = sys.argv[2] if len(sys.argv) > 2 else "docs-screenshots"
SAMPLE = sys.argv[3] if len(sys.argv) > 3 else ""
ADMIN_USER = sys.argv[4] if len(sys.argv) > 4 else os.environ.get("TWEAK_USER", "qc")
ADMIN_PASS = sys.argv[5] if len(sys.argv) > 5 else os.environ.get("TWEAK_PASS", "qcpass")
UPLOADER, UPLOADER_PASS = "e2e_uploader", "e2e-uploader-pass"
os.makedirs(OUT, exist_ok=True)
problems = []
IGNORED_HTTP = ("/api/auth/login",)   # a deliberate wrong-password attempt
expecting_401 = False                 # set around that attempt: the browser logs the 401 as a console error


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = ctx.new_page()
        page.on("console", lambda m: problems.append(f"console.{m.type}: {m.text}") if m.type == "error" and not (expecting_401 and "401" in m.text) else None)
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        page.on("response", lambda r: problems.append(f"HTTP {r.status} {r.url}") if r.status >= 400 and not any(x in r.url for x in IGNORED_HTTP) else None)
        page.on("dialog", lambda d: d.accept())

        def shot(name, wait=1200):
            page.wait_for_timeout(wait)
            page.screenshot(path=f"{OUT}/{name}.png", full_page=True)
            print("shot", name)

        def sign_in(prefix, user, password):
            page.fill(f"#{prefix}User", user)
            page.fill(f"#{prefix}Pass", password)
            page.click(f"#btn{prefix[0].upper()}{prefix[1:]}Login")
            page.wait_for_selector("#authBox:not([hidden])", timeout=10000)

        # Log Upload page asks to sign in
        page.goto(f"{BASE}/#logupload", wait_until="networkidle")
        shot("10_logupload_signin")
        global expecting_401
        expecting_401 = True
        page.fill("#luUser", ADMIN_USER)
        page.fill("#luPass", "wrong-password")
        page.click("#btnLuLogin")
        page.wait_for_timeout(800)
        print("  wrong password message:", page.locator("#luLoginMsg").inner_text())
        expecting_401 = False
        sign_in("lu", ADMIN_USER, ADMIN_PASS)
        print("  signed in as:", page.locator("#authName").inner_text(), "/", page.locator("#authRole").inner_text())
        print("  admin-only nav visible:", page.locator(".nav-item[data-page='accounts']").is_visible())

        # Accounts: create (or keep) the uploader
        page.goto(f"{BASE}/#accounts", wait_until="networkidle")
        shot("11_accounts_users")
        existing = page.locator(f"#acctUserRows td:text-is('{UPLOADER}')").count()
        if not existing:
            page.click("#btnAcctNew")
            page.fill("#fm_username", UPLOADER)
            page.fill("#fm_display_name", "E2E Uploader")
            page.fill("#fm_department", "QA line 1")
            page.fill("#fm_password", UPLOADER_PASS)
            page.fill("#fm_password2", UPLOADER_PASS)
            shot("12_accounts_new_form", 300)
            page.click("#btnFormModalSubmit")
            page.wait_for_selector("#formModal", state="hidden", timeout=10000)
            page.wait_for_timeout(800)
        else:   # make sure the known password applies
            row = page.locator("#acctUserRows tr", has_text=UPLOADER)
            row.locator("[data-op='reset']").click()
            page.fill("#fm_password", UPLOADER_PASS)
            page.fill("#fm_password2", UPLOADER_PASS)
            page.click("#btnFormModalSubmit")
            page.wait_for_selector("#formModal", state="hidden", timeout=10000)
            page.wait_for_timeout(500)
        print("  uploader row present:", page.locator(f"#acctUserRows td:text-is('{UPLOADER}')").count() == 1)
        page.click("[data-acct-tab='audit']"); shot("13_accounts_audit")
        page.click("[data-acct-tab='logins']"); shot("13_accounts_logins")

        # Products page (admin): list + check a name
        page.goto(f"{BASE}/#products", wait_until="networkidle")
        page.fill("#prodCheck", "TN-86_PICO-IMX6")
        page.wait_for_timeout(900)
        print("  check TN-86_PICO-IMX6 →", page.locator("#prodCheckResult").inner_text())
        shot("14_products_admin")
        print("  product rows:", page.locator("#prodRows tr").count())

        # DB maintenance as admin
        page.goto(f"{BASE}/#dbtweak", wait_until="networkidle")
        shot("15_dbtweak_admin")
        print("  dbtweak rows:", page.locator("#twRows tr").count(), page.locator("#twCount").inner_text())

        # Sign out, sign in as the uploader
        page.click("#btnLogout")
        page.wait_for_timeout(500)
        page.goto(f"{BASE}/#logupload", wait_until="networkidle")
        sign_in("lu", UPLOADER, UPLOADER_PASS)
        print("  uploader signed in:", page.locator("#authName").inner_text(), "/", page.locator("#authRole").inner_text(),
              "| accounts nav hidden:", not page.locator(".nav-item[data-page='accounts']").is_visible())
        page.goto(f"{BASE}/#dbtweak", wait_until="networkidle")
        print("  uploader sees DB maintenance denied:", page.locator("#twDenied").is_visible())
        page.goto(f"{BASE}/#accounts", wait_until="networkidle")
        print("  uploader sees accounts denied:", page.locator("#acctDenied").is_visible())

        # Upload the sample zip through the page
        page.goto(f"{BASE}/#logupload", wait_until="networkidle")
        shot("20_logupload_empty")
        if SAMPLE:
            page.set_input_files("#luFile", SAMPLE)
            page.wait_for_selector("#luStepConfirm:not([hidden])", timeout=120000)
            page.wait_for_timeout(1500)
            print("  analyze status:", page.locator("#luAnalyzeStatus").inner_text())
            print("  facts:", [x.strip() for x in page.locator("#luFactGrid .tile-value").all_inner_texts()])
            print("  inferred:", page.locator("#luInferred").inner_text())
            print("  mode:", page.locator("#luModeLabel").inner_text())
            shot("21_logupload_analyzed")
            if not page.locator("#luChatPanel").is_hidden():
                # one AI turn, then the form
                page.fill("#luChatInput", "TN-99_EDM1-IMX6P")
                page.click("#btnLuChatSend")
                page.wait_for_selector("#luChatLog .msg.typing", state="detached", timeout=90000)
                page.wait_for_timeout(500)
                print("  chat replies:", [m[:80] for m in page.locator("#luChatLog .msg.assistant").all_inner_texts()])
                print("  extract line:", page.locator("#luExtract").inner_text())
                shot("22_logupload_chat")
                page.click("#btnLuMode")
                page.wait_for_timeout(300)
            # Form: a typo first (R16), then the right values
            page.select_option("#luProduct", "__new__")
            page.fill("#luProductNew", "TN-86_PICO-IMX6")
            page.fill("#luWo", "5101-251216015")
            page.fill("#luQty", "300")
            page.wait_for_timeout(1200)
            print("  issues (typo):", [x[:90] for x in page.locator("#luIssues li").all_inner_texts()])
            print("  upload button disabled with R16:", page.locator("#btnLuUpload").is_disabled())
            shot("23_logupload_form_r16")
            page.click("#luIssues [data-pick='TN-86_PICO-IMX7']")
            page.wait_for_timeout(1200)
            print("  after pick:", page.locator("#luProduct").input_value(), "| issues:", [x[:60] for x in page.locator("#luIssues li").all_inner_texts()])
            page.select_option("#luProduct", "TN-99_EDM1-IMX6P")
            page.wait_for_timeout(1200)
            print("  issues (ok):", [x[:90] for x in page.locator("#luIssues li").all_inner_texts()])
            print("  alignment:", [x.strip() for x in page.locator("#luAlignGrid .tile-value").all_inner_texts()])
            print("  ack needed:", not page.locator("#luAckRow").is_hidden(), "| upload enabled:", not page.locator("#btnLuUpload").is_disabled())
            shot("24_logupload_form_ready")
            page.click("#btnLuUpload")
            page.wait_for_selector("#btnLuNext:not([hidden])", timeout=180000)
            page.wait_for_timeout(600)
            print("  progress:", page.locator("#luProgressText").inner_text())
            print("  results:", [x.strip() for x in page.locator("#luResultGrid .tile-value").all_inner_texts()])
            shot("25_logupload_done")
            print("  history rows:", page.locator("#luHistoryRows tr").count())
            # light theme + Chinese
            page.click("#btnTheme"); page.click("#btnLang"); shot("26_logupload_light_zh")
            page.click("#btnTheme"); page.click("#btnLang")
        # products as uploader (can add, cannot retire)
        page.goto(f"{BASE}/#products", wait_until="networkidle")
        print("  uploader retire buttons:", page.locator("#prodRows [data-op='retire']").count(), "| new product button:", page.locator("#btnProdNew").is_visible())
        shot("27_products_uploader")
        # Undo this run's batches so the planned quantity it set does not stay on the sample work order.
        undone = page.evaluate("""async () => {
            const t = sessionStorage.getItem('wifi-auth-v1'), h = { 'X-Tweak-Token': t };
            const hist = await (await fetch('/api/log-upload/history?limit=50', { headers: h })).json();
            let n = 0;
            for (const r of hist.items) if (r.status === 'done') { await fetch('/api/log-upload/' + r.id, { method: 'DELETE', headers: h }); n++; }
            return n;
        }""")
        print("  batches undone after the run:", undone)
        page.click("#btnLogout")
        page.wait_for_timeout(400)
        print("  signed out:", page.locator("#authBox").is_hidden())
        browser.close()


main()
print("\nproblems:", len(problems))
for x in problems:
    print("  ", x)
sys.exit(1 if problems else 0)
