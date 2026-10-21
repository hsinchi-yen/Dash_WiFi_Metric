# WiFi YFP Dashboard

Chinese version: [README_CHT.md](README_CHT.md)

## Project Purpose

This project is designed for WiFi product factory QA data visualization and tracking. It converts scattered test logs into an operational dashboard system for live monitoring, traceable analysis, and maintainable day-to-day operations, helping production and engineering teams detect yield risks earlier and reduce troubleshooting time.

## Problems It Solves

1. Test results are scattered across text logs, making manual investigation and summarization slow.
2. It is difficult to continuously track work-order-level yield, throughput, and retry risk.
3. Deployment and data recovery can become inconsistent after reboot or machine migration.
4. On-duty teams need a resilient monitoring setup that can recover quickly.

## Core Capabilities

1. Production data integration: parse test logs and import data into PostgreSQL (desktop uploader, or the web
   Log Upload wizard with accounts and undo).
2. Web dashboard (`http://<host>:8000`): Trend and Forecast views (the line TV rotates them: `/?lang=zh&tv=1`), work
   orders with status lights and one-line notes, work-order detail with final reports, the Fail List with failure
   categories, next steps and dispositions, RF and production analysis, an anomaly banner, Ask the dashboard,
   weekly / monthly reports, and an Advanced Analysis window for RD.
3. The edge LLM only explains (status notes, reports, answers) — numbers always come from the database.
4. Operations toolkit: backup, restore, deployment (`dockerup-docs/01_Deployment/deploy.sh`), mount, permission-fix,
   and auto-start scripts; Grafana keeps working on the same database.
5. Containerized deployment: PostgreSQL, the API, nginx (web front end) and Grafana with Docker Compose.

Manuals per role: `dockerup-docs/06_Manuals/`. Working on the code: `AGENTS.md`, tests in `tests/README.md`.

## Typical Use Cases

1. Real-time monitoring on manufacturing test lines.
2. Fast trace-back during process anomalies (Fail list / Retry behavior).
3. Data source for daily or monthly quality reports.
4. New-machine migration and disaster recovery using backup/restore SOPs.

## Repository Structure

- `dockerup-essential/`: runtime core (API, web front end, compose, schema, parser)
- `dockerup-docs/`: deployment and maintenance docs, scripts, and SOPs
- `DB_backups/`: backup storage (not recommended to commit to Git)

## Non-Goals and Constraints

1. This project does not replace full MES/ERP workflows; it focuses on test-data observability and analysis.
2. Dashboard quality depends on test-log correctness and complete ingestion.
3. Production environments should enforce backup strategy and permission controls (see `dockerup-docs/`).

## Suggested Success Metrics

1. Reduced MTTD/MTTR for problematic work orders.
2. Daily line inspection shifted from manual log checks to dashboard monitoring.
3. Services recover automatically within expected time after reboot.
4. New-machine restore can be completed within a controlled SOP timeframe.
