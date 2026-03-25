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

1. Production data integration: parse test logs and import data into PostgreSQL.
2. Live dashboard: show overall yield, fail list, throughput trends, and work order metrics.
3. Auto-rotation display mode: rotate between Dashboard / Work Orders / Throughput pages.
4. Operations toolkit: backup, restore, mount, permission-fix, and auto-start scripts.
5. Containerized deployment: run API, PostgreSQL, and Grafana with Docker Compose.

## Typical Use Cases

1. Real-time monitoring on manufacturing test lines.
2. Fast trace-back during process anomalies (Fail list / Retry behavior).
3. Data source for daily or monthly quality reports.
4. New-machine migration and disaster recovery using backup/restore SOPs.

## Repository Structure

- `dockerup-essential/`: runtime core (API, dashboard, compose, schema, parser)
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
