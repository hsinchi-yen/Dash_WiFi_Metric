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
