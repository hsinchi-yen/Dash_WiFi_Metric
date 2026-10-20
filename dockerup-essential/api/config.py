"""Settings, read once from the environment (docker-compose passes them from .env)."""
import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))
APP_VERSION = os.environ.get("APP_VERSION", "0.2.0")

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DB_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "6"))          # per uvicorn worker
SLOW_QUERY_MS = int(os.environ.get("SLOW_QUERY_MS", "500"))

# Interim administrator guard for /api/ingest and /api/admin/* (X-Admin-Token header).
# Unset = those endpoints are disabled. Replaced by accounts in a later phase.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()

# Local LLM (llama.cpp, OpenAI-compatible). Defaults keep the pre-refactor behaviour.
LLM_API_BASE = os.environ.get("LLM_API_BASE", "http://10.20.30.23:8000/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "tn8227")
LLM_MODEL = os.environ.get("LLM_MODEL", "Qwen3.6-35B-A3B-Q6_K")
LLM_STATUS_TIMEOUT = 3          # seconds for the /models probe
LLM_STATUS_CACHE_SEC = 20       # the old page polls every 30 s per tab; one probe serves them all
LLM_SUMMARY_TIMEOUT = 180
# Thinking off: the same report prompt took 57 s with thinking and 18 s without (2026-10-20).
LLM_ENABLE_THINKING = os.environ.get("LLM_ENABLE_THINKING", "false").lower() == "true"
LLM_REPORT_MAX_TOKENS = int(os.environ.get("LLM_REPORT_MAX_TOKENS", "1024"))

# AI reports are generated in the background and stored (ai_reports); opening one is a read.
# REPORT_AUTO keeps the newest REPORT_WARM_LATEST work orders' reports current in both languages
# (regenerated when their data changes); reports for other work orders are generated on first open.
REPORT_AUTO = os.environ.get("REPORT_AUTO", "true").lower() == "true"
REPORT_WARM_LATEST = int(os.environ.get("REPORT_WARM_LATEST", "30"))
REPORT_FAILED_RETRY_SEC = 300   # a failed generation is not retried automatically for this long

# mv_unit (one row per SN: its latest test) is refreshed on this interval and right after
# ingest / DB Tweak deletes. Rows written by the desktop uploader show up within one interval.
STATS_REFRESH_SEC = int(os.environ.get("STATS_REFRESH_SEC", "300"))

CORS_ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("CORS_ALLOWED_ORIGINS", "*").split(",") if o.strip()] or ["*"]

# The single-file dashboard (served by the API until the web/ front end replaces it).
WEB_HTML = os.environ.get("WEB_HTML", os.path.join(APP_DIR, "wifi_dashboard.html"))

# Shared quality thresholds — dashboard colours and the AI report use the same values.
YIELD_GOOD = 98.0          # ≥ green
YIELD_WARN = 95.0          # ≥ amber, below → red

# Postgres advisory lock keys (shared by both uvicorn workers)
MIGRATION_LOCK_KEY = 7361001
STATS_REFRESH_LOCK_KEY = 7361002
REPORT_WORKER_LOCK_KEY = 7361003   # one report generation at a time across workers
