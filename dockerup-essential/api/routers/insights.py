"""Ask the dashboard, weekly / monthly reports and the anomaly banner (5E).

GET  /api/alerts                          the banner: anomalies of the last 24 hours (rules; ?now= replays a moment)
POST /api/ask                             a question → a restricted query (LLM) → numbers from the database (everyone,
                                          rate limited; aggregates only, Q25)
GET  /api/period-reports                  weekly and monthly reports written so far
GET  /api/period-reports/{kind}/{start}   one report (facts + summary); …/{start}.md?lang= as Markdown
POST /api/period-reports/generate         administrator: write (or rewrite) one report now
"""
from datetime import date, datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

from errors import ApiError, Msg, bad_request, not_found
from services import alerts, ask_dashboard, auth, llm, period_reports, ratelimit

router = APIRouter(tags=["insights"])


@router.get("/api/alerts")
def alert_list(now: Optional[datetime] = Query(None, description="replay: the moment to judge (station clock)")):
    return alerts.alerts(now)


class AskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: StrictStr = Field(..., min_length=1, max_length=300)
    lang: Literal["zh", "en"] = "zh"


@router.post("/api/ask")
def ask(request: Request, body: AskBody):
    if not llm.status()["connected"]:
        raise ApiError(503, Msg("AI 服務離線，目前無法回答。", "The AI service is offline; questions cannot be answered now."), "llm_offline")
    ratelimit.limit(request, "ask_dashboard", 6)
    try:
        return ask_dashboard.ask(body.question.strip(), body.lang)
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(503, Msg("AI 回答失敗，請稍後再試。", "The AI could not answer. Please try again later."), "llm_failed") from exc


def _start(kind: str, start: str) -> date:
    if kind not in period_reports.KINDS:
        raise bad_request(Msg("報表種類只有週報與月報。", "Reports are weekly or monthly."), "bad_kind")
    try:
        return date.fromisoformat(start)
    except ValueError:
        raise bad_request(Msg("日期格式為 YYYY-MM-DD。", "Use YYYY-MM-DD."), "bad_date") from None


@router.get("/api/period-reports")
def report_list():
    return {"items": period_reports.listing()}


@router.get("/api/period-reports/{kind}/{start}.md")
def report_markdown(kind: str, start: str, lang: Literal["zh", "en"] = "zh"):
    row = period_reports.get(kind, _start(kind, start))
    if not row:
        raise not_found(Msg("這份報表還沒有產生。", "This report has not been written yet."), "no_report")
    name = f"{'weekly' if kind == 'week' else 'monthly'}_{row['period_start']}_{lang}.md"
    return Response(content=period_reports.markdown(row, lang).encode("utf-8"), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/api/period-reports/{kind}/{start}")
def report_get(kind: str, start: str):
    row = period_reports.get(kind, _start(kind, start))
    if not row:
        raise not_found(Msg("這份報表還沒有產生。", "This report has not been written yet."), "no_report")
    return row


class GenerateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["week", "month"]
    start: StrictStr = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    use_llm: StrictBool = True


@router.post("/api/period-reports/generate")
def report_generate(request: Request, body: GenerateBody, admin=Depends(auth.require_admin)):
    if body.use_llm:
        ratelimit.limit(request, "period_report", 6)
    return period_reports.generate(body.kind, _start(body.kind, body.start), use_llm=body.use_llm, requested_by=admin["username"])
