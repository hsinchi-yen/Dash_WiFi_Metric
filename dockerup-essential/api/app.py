"""
WiFi YFP Dashboard — FastAPI entry point.

    routers/   HTTP endpoints, one module per area (analytics, reports, settings, system, auth,
               accounts, products, upload, tweak, maintenance)
    services/  queries and business logic (mv_unit statistics, filters, LLM, scheduler, migrations,
               sessions, product registry, web upload rules/ingest, upload AI chat)
    db.py      connection pool;  config.py  settings;  errors.py  {"error", "code", "detail"} responses

Shared with the desktop tool (one directory up): log_parser.py. The single-file dashboard
(wifi_dashboard.html) is served at "/" until the web/ front end replaces it.
"""
import logging
import os
import sys
import time
from contextlib import asynccontextmanager

_HERE = os.path.dirname(os.path.abspath(__file__))
for _path in (_HERE, os.path.dirname(_HERE)):   # api/ and the shared module beside it
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402

import config  # noqa: E402
import db  # noqa: E402
import errors  # noqa: E402
from routers import accounts, analytics, auth, failures, maintenance, products, reports, settings, system, tweak, upload, workorder  # noqa: E402
from services import migrations, scheduler  # noqa: E402

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("wifi.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    migrations.run()
    if os.environ.get("WIFI_DISABLE_SCHEDULER") != "1":
        scheduler.start()
    if not (config.TWEAK_USER and config.TWEAK_PASS):
        log.warning("TWEAK_USER / TWEAK_PASS not set: nobody can sign in as administrator")
    log.info("WiFi Dashboard API %s ready", config.APP_VERSION)
    yield
    scheduler.stop()
    db.close_pool()


app = FastAPI(
    title="WiFi Dashboard API",
    version=config.APP_VERSION,
    description="WiFi module-on-SoM production test analytics. Errors are `{\"error\": …, \"code\": …}`.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
errors.install(app)


@app.middleware("http")
async def timing(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000
    response.headers["X-Response-Time-Ms"] = f"{ms:.0f}"
    path = request.url.path
    if path != "/health" and path.startswith("/api"):
        level = logging.WARNING if ms >= config.SLOW_QUERY_MS * 2 else logging.INFO
        log.log(level, "%s %s → %s in %.0f ms", request.method, path, response.status_code, ms)
    return response


for module in (system, analytics, reports, settings, workorder, failures, auth, accounts, products, upload, tweak, maintenance):
    app.include_router(module.router)


def _dashboard_html() -> str:
    for candidate in (config.WEB_HTML, os.path.join(os.path.dirname(_HERE), "wifi_dashboard.html")):
        if os.path.isfile(candidate):
            return candidate
    return config.WEB_HTML


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(_dashboard_html())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
