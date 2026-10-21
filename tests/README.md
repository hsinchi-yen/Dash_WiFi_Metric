# Tests

| Command | What it does |
|---|---|
| `python3 tests/golden.py capture [BASE]` | Records the JSON of 40 data-API calls into `tests/golden/` (git-ignored: the rows carry device SNs/MACs). Run it against a known-good version **before** changing queries. |
| `python3 tests/golden.py check [BASE]` | Compares the running API with the recorded files. Any changed number fails. List endpoints whose original ORDER BY had ties are compared as multisets. |

`BASE` defaults to `http://127.0.0.1:8000` (the local `wifi-local` compose stack).

History: the 2026-10-20 backend refactor (routers/services, `mv_unit`) was verified with
40/40 identical responses against the pre-refactor capture (tag `baseline-20261020`).

| `node tests/js/i18n_keys.mjs` | Every `t('key')` / `data-i18n` key used by `web/` exists in both dictionaries; both have the same keys and `{variables}`; no Chinese in the English strings. |
| `python3 tests/e2e_screens.py [BASE] [OUT_DIR]` | Playwright smoke run of the web/ dashboard (every page, RF tabs, filters, Dark/Light, EN/中文, phone); fails on console errors or HTTP ≥ 400. |
| `python3 tests/acceptance_upload.py [BASE] <sample.zip> [admin pass]` | Phase 4 acceptance (93 checks): accounts and roles, product registry resolution (R16 same-TN block, new product), zip / folder analyze, rules R1–R16, duplicate-only commit, real insert + revert (new product and work order, `wo_plan`, `mv_unit` refresh), NOWO numbering, cancel, per-file CSV, AI chat turns. Needs the sample delivery zip (`5101-251216015` / `TN-99_EDM1-IMX6P`) whose 252 logs are already in the DB; every inserted row is reverted again. Admin login defaults to `TWEAK_USER` / `TWEAK_PASS` from the environment. |
| `python3 tests/e2e_phase4.py [BASE] [OUT_DIR] <sample.zip> [admin pass]` | Browser pass of the Phase 4 pages: sign-in, Accounts (create / reset an uploader), Products (check a name), DB Maintenance as admin, then as the uploader: upload the sample zip through the page (one AI turn, R16 typo blocked, alignment tiles, commit, result list), Dark/Light + 中文 screenshots. |
| `python3 tests/boundary_upload.py [BASE] <sample.zip> [admin pass] [--json out]` | Upload boundary tests (149): wrong work orders and NOWO, wrong / retired / ambiguous product names, quantities, malformed zips (bad dates in log names, empty logs, traversal, symlinks, nesting, zip bomb), folder limits, access, concurrency, expiry, the AI assistant. Every refused case is also sent straight to `/commit` (must be 400) and the row count is compared at the end; a commit that leaks is undone. |
| `python3 tests/boundary_upload_ui.py [BASE] [OUT_DIR] <sample.zip>` | Browser side: wrong values show ❌ and keep Upload disabled; a force-enabled Upload is refused by the server and the page shows the error; HTML in fields renders as text. |
| `tests/db_compat.sh <backup.dump> [old deployment dir]` | Old-database compatibility on a scratch PostgreSQL container (≈ 5 min, never touches `wifitest-db`): restores the production backup twice, starts the built API image on one copy (its startup migration upgrades it), then checks that the old schema objects and data are unchanged, the desktop uploader's own `log_parser.py` writes identically, the Grafana panel queries match, the **old** `app.py` answers its 40 read endpoints and its ingest / DB Tweak writes identically on both copies, `rollback_web_additions.sql` restores the exact old schema, and a re-upgrade works. A backup that is already upgraded (every deployment after the first, or the local stack) first becomes the old database through the rollback script, which must give exactly `tests/old_schema.sql` (the old system's schema, from the pre-refactor production backup). Must print `COMPATIBLE` before a deployment. |
| `python3 tests/db_compat.py db OLD_DSN NEW_DSN [--mode migrated\|rollback] [--schema-only] …` / `oldapi OLD_URL NEW_URL …` | The checks `db_compat.sh` runs (`db` needs psycopg2: run it inside the API image). |
| `tests/switch_rehearsal.sh <replica dir> <predeploy dump>` | `releases/switch.sh` on a copy of production on this machine, after `deploy.sh --replica <dir>` (stop the local stack first: same ports; ≈ 10 min). Switches new → old → new and checks each version: title, containers, golden 40/40, `/legacy/`, `.env` kept. Also checks: a row written by the old parser while the old dashboard is live; a release that does not come up (it must switch back by itself); a release without its image (refused, nothing touched); the lock; `old --drop-web-tables` (schema identical to the pre-deployment production dump, every row kept); `restore-db` (refused without `yes` or with a non-dump; then a safety dump, rows and schema back to the backup, golden 40/40). |

`GOLDEN_DIR=<dir>` makes `golden.py` record / compare somewhere else (used to compare two old-API runs).

Phase 5A (settings, work-order status, one-line AI notes):

| Command | What it does |
|---|---|
| `python3 tests/unit_wo_rules.py` | The status rules (`services/wo_rules.py`, no database): states, lights, every check, note hash, rule sentences, the number check on AI sentences. |
| `python3 tests/acceptance_phase5a.py [BASE]` | Settings API (read, 401 / 403, eleven invalid inputs, change → status / thresholds / hourly targets follow, restore, audit), the statuses of all 77 work orders on the production copy, and every stored AI note against the number rule. Restores the settings it changes. |
| `python3 tests/e2e_phase5a.py [BASE] [OUT_DIR]` | Browser: language default (browser language, `?lang=`), Work Orders status / note / column switch, Settings sign-in / denied / form / invalid input / save and restore. |

Phase 5B (Dashboard hourly output, work-order detail, comparison, administrator plan, final report):

| Command | What it does |
|---|---|
| `python3 tests/acceptance_phase5b.py [BASE]` | Dashboard hourly output (07:00–19:00, sums, target), detail numbers and the previous same-product work order, comparisons (any two, same → 400, unknown → 404), planned quantity (401 / 403 / invalid / set / clear / audit), batches, the final report of a normal and a red work order (every number a fact, normal = summary only, causes phrased as possibilities), follow-up questions, rate limit. Sets and clears one plan. |
| `python3 tests/e2e_phase5b.py [BASE] [OUT_DIR]` | Browser: Dashboard split tile + hourly chart (no scrolling at 1080p), Work Orders → detail, comparison select, report, suggested question, 中文, compare-two from the list, administrator plan set / clear, Fail List link. |
| `docker run … python3 /tests/unit_plan_revert.py` | The planned quantity when upload batches are undone, in seven orders of batches and administrator changes, against the real schema in a rolled-back transaction (command in the file). |

Phase 5F-1 (failure classification, RD role):

| Command | What it does |
|---|---|
| `python3 tests/unit_fail_rules.py` | The FAIL log classifier (`services/fail_rules.py`, no database) on synthetic logs in the production format: every category, severity order, untested Bluetooth, a reboot mid-test, noise lines never evidence, station, retest outcome, the LLM excerpt and the evidence check. Shared samples: `tests/fail_log_samples.py`. |
| `docker run … python3 /tests/check_fail_classification.py` | On the real database (command in the file): all FAIL runs classified and equal to the current rules, consistent with the desktop parser's band flags, not-retested = units still failing; a FAIL run written with the desktop `log_parser.write_record` is classified, a later PASS turns it into "passed on retest", deleting it (old DB Tweak) removes its classification; a log no rule knows goes to the LLM and only verbatim evidence lines are accepted. Synthetic rows are removed again. |
| `docker run … python3 /tests/acceptance_desktop_path.py [BASE] [--keep \| --cleanup]` | The old upload ways still feed the new dashboard (command in the file; needs `_remote_snapshot/*/dockerup-essential` mounted at `/old`; ≈ 6 min). With the production deployment's own `log_parser.py`: the desktop tool's Database Upload (15 logs, re-upload skipped as duplicates), the batch command, `/api/ingest` (administrator only now). Without forcing anything it waits for the stack's own statistics refresh and FAIL scan, then checks Work Orders (status light, note), detail, Trend, Forecast, Fail List (classification, next step, passed on retest), the alert banner, DB Maintenance, a product name only the desktop tool knows; deleting the rows as the old DB Tweak does leaves nothing behind. `--keep` leaves the rows for screenshots. |
| `python3 tests/acceptance_phase5f1.py [BASE]` | Taxonomy, the 223 FAIL runs by category and retest outcome, stations, FAIL list / detail / report facts, date ranges on every date-filtered endpoint (regression), and the RD role (creates `test_rd`). |
| `python3 tests/e2e_phase5f1.py [BASE] [OUT_DIR]` | Browser: custom September range on the Dashboard, Fail List categories / run sequences / stations, the detail page's failure categories, the RD account's view, the role select in Accounts. |

LLM budget (2026-10-21):

| Command | What it does |
|---|---|
| `python3 tests/acceptance_llm_budget.py [BASE]` | The legacy AI report carousel is refused (410) and the legacy summary serves the stored report without an LLM call; opening an old report queues nothing; the legacy page has no AI report menu item or carousel step; AI-worded notes only for red / amber work orders; `/api/llm-usage` counts calls by purpose. |

Phase 5C (FAIL disposition; its Today page was removed on 2026-10-21):

| Command | What it does |
|---|---|
| `python3 tests/unit_fail_actions.py` | The next-step and disposition rules (`services/fail_actions.py`, no database): retest first, bursts → check the station, repeated FAILs → RD, RD's own instruction wins, automatic states after a later PASS / FAIL, which changes are valid. |
| `python3 tests/acceptance_phase5c.py [BASE]` | The Fail List's counts and pending / handled views; dispositions (401, validation, 404 / 409 for PASS runs and FAILs closed by a retest, set → counted → history → back to open, no row left); the next step of the 15 units still failing; the working days in Settings; `/api/today` is gone. Reads two record ids with `docker exec` (PSQL_CONTAINER). |
| `python3 tests/e2e_phase5c.py [BASE] [OUT_DIR]` | Browser: home = Trend, no Today in the menu; the Fail List's views, tiles, next steps and detail window signed out and as RD (set and put back); role landing; the detail page's FAIL table; the Settings working days. |

Trend page Forecast view (2026-10-21):

| Command | What it does |
|---|---|
| `python3 tests/unit_forecast_rules.py` | The forecast arithmetic (`services/forecast_rules.py`, no database): remaining share of today, working days to a date, working time between two moments, the finish time across nights and weekends, the recent pace, the last working days. |
| `python3 tests/acceptance_forecast.py [BASE]` | Output forecast against the Trend page's own numbers (this week / month so far, last month, the 10-working-day average, projected = done + average × days left), one product, the date range not applying; a work order's completion without a plan, with plan 800 (remaining, finish inside working hours, projection up to the plan) and 300 (reached). Sets and clears the plan of 5101-260820009. |
| `python3 tests/e2e_forecast.py [BASE] [OUT_DIR]` | Browser: the Trend / Forecast switch, the latest work order's completion and the all-data output forecast at 1080p without scrolling, 中文, the TV rotation Trend ↔ Forecast (`?tv=1`). About a minute and a half. |

Phase 5D (upload wizard, DB Maintenance):

| Command | What it does |
|---|---|
| `python3 tests/acceptance_phase5d.py [BASE] <sample.zip>` | `/validate` offers the stored plan (an earlier batch's, then the administrator's), the batch reading against the product's history (rules; the AI wording on request, decimals kept whole), DB Maintenance's impact preview for a record / batch / work order, CSV export, batch undo; the row count is back to where it was. |
| `tests/e2e_phase4.py`, `tests/boundary_upload_ui.py` | Also cover the wizard (the same element ids; a refused forced upload returns to step 2 with the server's error). |

Phase 5F-2 (Advanced Analysis, RD):

| Command | What it does |
|---|---|
| `python3 tests/acceptance_phase5f2.py [BASE]` | Access (401 / 403 / 200), every filter, Pareto and cross tables that add up, three real triage questions on the production copy, triage validation, one run confirmed with an instruction that reaches the Fail List and the work-order note, a batch reclassification that survives the FAIL scan, taxonomy add / rename / merge / delete, reset back to the rules, unit history, log viewer, similar cases, CSV and log export, Ask AI (aggregates only). Waits 70 s for one scan. |
| `python3 tests/e2e_phase5f2.py [BASE] [OUT_DIR]` | Browser: who sees the window, filters, Pareto, cross table, batch triage, the detail window's RD section and its instruction on the Fail List and Work Orders, a unit's five tests with two logs side by side, similar cases, the taxonomy tab in English. Resets what it triaged. |

Phase 5E (anomaly banner, Ask the dashboard, weekly / monthly reports):

| Command | What it does |
|---|---|
| `docker run … python3 /tests/unit_ask_dashboard.py` | Ask the dashboard's checks without the LLM: what the server accepts from the LLM's query object (unknown metrics / products / work orders dropped, one-group splits removed), period windows and the period before, the answer sentences; report period bounds (command in the file). |
| `python3 tests/acceptance_phase5e.py [BASE]` | The banner now and replayed on 01/13 11:00 and 10/01 17:00; six real questions whose answers equal the Trend page's numbers, a unit-level question refused; reports listed, September written by rules (= the Trend page's numbers, August as the period before, small work orders without a yield), Markdown in both languages, 401 / 403 / 400 / 404, a summary by the AI. About 7 LLM calls. |
| `python3 tests/e2e_phase5e.py [BASE] [OUT_DIR]` | Browser: the banner replayed (items, link to the Fail List, dismiss, 1080p), Ask with an example question and "open on Trend", the Reports page (list, the newest report, Markdown download, the administrator writes a week). |

All suites in one go: `tests/run_all.sh [--quick] [--compat <backup.dump>]` (environment: BASE, SAMPLE_ZIP, SHOTS, LOGS).
