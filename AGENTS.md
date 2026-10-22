# AGENTS.md — working on the WiFi YFP Dashboard

Production test dashboard for the WiFi / Bluetooth module test line. Read this before changing anything.

## Layout

| Path | What |
|---|---|
| `dockerup-essential/api/` | FastAPI app (`app.py`), `routers/` (HTTP), `services/` (queries, rules, LLM, scheduler, migrations), `config.py`, `db.py` |
| `dockerup-essential/web/` | The web front end (no build step): `index.html`, `css/`, `js/main.js` (shell, routing, TV rotation), `js/core/` (api, auth, i18n + `strings.en.js` / `strings.zh.js`, charts, format), `js/pages/` (one module per page) |
| `dockerup-essential/log_parser.py` | Shared with the desktop uploader — the desktop tool writes `test_record` with it directly |
| `dockerup-essential/docker-compose.yml` | postgres (`wifitest-db`), grafana, api, nginx (serves `web/`, proxies `/api`) on :8000 |
| `dockerup-docs/` | Deployment (`01_Deployment/`), maintenance scripts incl. `rollback_web_additions.sql` (`02_Maintenance/`), reports per phase (`04_Reports/`), manuals (`06_Manuals/`) |
| `tests/` | Every check; `tests/README.md` lists them, `tests/run_all.sh` runs them all |

## Rules that must not be broken

1. **The old database stays compatible.** Never alter, drop or constrain `test_record` or the old views (`v_yield_by_wo`,
   `v_unstable_units`, `v_fail_detail`); the nullable `upload_id` column is the one verified exception. No foreign key
   to `test_record` and no trigger on it (the old DB Tweak deletes rows directly; orphans are swept by the FAIL scan).
   Every new table / column goes into `services/migrations.py` **and** `rollback_web_additions.sql`.
   `tests/db_compat.sh <backup.dump>` must print `COMPATIBLE` before any deployment.
2. **Keep LLM calls low.** The edge LLM (llama.cpp, one queue, shared with other systems) is called only where it
   explains something: red / amber work-order notes (throttled), final reports of flagged work orders, explicit user
   requests (report, follow-up questions, Ask the dashboard, RD Ask AI, upload reading), and FAIL logs no rule
   recognises. Never add background LLM work for normal cases; never add a carousel. Every call is logged in
   `llm_usage` (`purpose` argument); `tests/acceptance_llm_budget.py` guards this.
3. **Numbers come from the database, never from the LLM.** LLM output is constrained by json_schema; every sentence is
   checked with `wo_rules.numbers_ok` and dropped when it carries a number that is not a fact.
4. **Both languages.** Every user-visible string lives in both dictionaries (same keys, same `{variables}`, no Chinese
   in English); Chinese uses full-width punctuation and counts units with 台. `node tests/js/i18n_keys.mjs` checks it.
5. **The TV.** The Trend page must fit 1920×1080 without scrolling, header on one row. The TV rotates every 30 s: latest
   work order Trend → Forecast → all work orders Trend → Forecast (`?tv=1` at once, other screens after 5 idle minutes);
   the header names the product on screen.
6. **No deployment without explicit approval** from the owner, and never write credentials into files.

## Running it locally

```
cd dockerup-essential
docker compose -p wifi-local -f docker-compose.yml -f docker-compose.dev.yml up -d   # sources mounted
docker restart wifitest-api                                                           # after Python edits
```
Front-end edits show up on reload (nginx serves the folder). Administrator: `TWEAK_USER` / `TWEAK_PASS` (qc / qcpass).

## Production

`sit@10.20.31.40:/mnt/md127/WIFI_YFP_DashBoard` (not git). Deployed with `dockerup-docs/01_Deployment/deploy.sh --host …`
(rehearse first with `--replica` and `tests/switch_rehearsal.sh`). Every deployed version stays in `releases/` there
(`old` = the pre-refactor dashboard, `new_<commit>`, `CURRENT`); `releases/switch.sh old|new|status` switches in about
10 s without touching PostgreSQL or Grafana (`SWITCH_RELEASE.md`). Pre-deployment backups: `DB_backups/predeploy_<ts>/` on
the host and here. The host also runs unrelated stacks (pixi-test on 8001/5433, iw-solo-pixi on 8003/5434): never touch them.

## Tests

`tests/run_all.sh` (set `SAMPLE_ZIP` for the upload suites, `--quick` to skip the slow ones, `--compat <dump>` for the
database check). Pure rule tests run anywhere: `unit_wo_rules`, `unit_fail_rules`, `unit_fail_actions`,
`unit_forecast_rules`. Golden: `tests/golden.py check` compares the legacy endpoints with the recorded responses.

## Where things are decided

Plan, answers and their reasons: `dockerup-docs/04_Reports/Final_Improvement_Plan.md` §6–§9; one report per phase in
the same folder.
