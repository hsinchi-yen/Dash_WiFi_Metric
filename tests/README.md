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
| `tests/db_compat.sh <backup.dump> [old deployment dir]` | Old-database compatibility on a scratch PostgreSQL container (≈ 5 min, never touches `wifitest-db`): restores the production backup twice, starts the built API image on one copy (its startup migration upgrades it), then checks that the old schema objects and data are unchanged, the desktop uploader's own `log_parser.py` writes identically, the Grafana panel queries match, the **old** `app.py` answers its 40 read endpoints and its ingest / DB Tweak writes identically on both copies, `rollback_web_additions.sql` restores the exact old schema, and a re-upgrade works. Must print `COMPATIBLE` before a deployment. |
| `python3 tests/db_compat.py db OLD_DSN NEW_DSN [--mode migrated\|rollback] …` / `oldapi OLD_URL NEW_URL …` | The checks `db_compat.sh` runs (`db` needs psycopg2: run it inside the API image). |

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
