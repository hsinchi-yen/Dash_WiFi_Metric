"""AI reports.

GET  /api/reports/{wo}            the stored report (instant); queues generation when missing/stale
POST /api/reports/{wo}/generate   queue a regeneration now
GET  /api/workorders/{wo}/ai-summary   legacy page: the stored final report as text — no LLM call any more;
                                       mode=carousel (the terminal carousel) is switched off (410)
GET  /api/llm-usage               LLM completions per day and purpose
"""
import logging
from typing import Any, Optional

from fastapi import APIRouter, Query

import config
import db
from errors import ApiError, Msg, not_found
from services import llm, reports, wo_detail, wo_report

log = logging.getLogger("wifi.reports")
router = APIRouter(tags=["reports"])

Lang = Query(default="zh", pattern="^(zh|en)$")


@router.get("/api/llm-status")
def llm_status():
    s = llm.status()
    return {"status": s["status"], "connected": s["connected"], "model": s["model"]}


@router.get("/api/reports/{wo}")
def get_report(wo: str, lang: str = Lang, product_model: Optional[str] = None) -> dict[str, Any]:
    return reports.get(wo, product_model, lang)


@router.post("/api/reports/{wo}/generate")
def generate_report(wo: str, lang: str = Lang, product_model: Optional[str] = None) -> dict[str, Any]:
    if not llm.status()["connected"]:
        raise ApiError(503, Msg("AI 服務離線，目前無法產生報告", "The AI service is offline; reports cannot be generated now"), "llm_offline")
    return reports.request(wo, product_model, lang)


@router.get("/api/reports-queue")
def reports_queue() -> dict[str, int]:
    return reports.queue_stats()


@router.get("/api/workorders/{wo}/ai-summary")
def ai_summary(wo: str, product_model: Optional[str] = None, lang: str = "zh", mode: str = "normal"):
    """The legacy page's AI summary: the stored final report (or its rule summary) as text. It used to call the
    LLM synchronously on every request; the terminal carousel (mode=carousel) is switched off."""
    if mode == "carousel":
        raise ApiError(410, Msg("AI 報告輪播已停用。", "The AI report carousel is switched off."), "carousel_disabled")
    key = wo_detail.find(wo, product_model if product_model and product_model != "ALL" else None)
    if not key:
        raise not_found(Msg("找不到此工單或沒有資料", "Work order not found or has no data"))
    r = wo_report.get(key)
    lang = "en" if lang == "en" else "zh"
    part = (r.get("report") or {}).get(lang) or wo_report.rule_report(key, r["facts"])[lang]
    titles = {"zh": ("異常", "可能原因", "建議動作"), "en": ("Anomalies", "Possible causes", "Suggested actions")}[lang]
    lines = [part["summary"]]
    for title, field in zip(titles, ("anomalies", "causes", "actions")):
        if part.get(field):
            lines += ["", f"**{title}**", *[f"- {x}" for x in part[field]]]
    return {"summary": "\n".join(lines), "source": r.get("source") or "rule"}


@router.get("/api/llm-usage")
def llm_usage(days: int = Query(default=7, ge=1, le=90)):
    """LLM completions per day and purpose (Asia/Taipei days), and the last 24 hours."""
    rows = db.rows("""
        SELECT to_char(at AT TIME ZONE %s, 'YYYY-MM-DD') AS day, purpose, COUNT(*) AS calls,
               COUNT(*) FILTER (WHERE NOT ok) AS failed, ROUND(AVG(ms)) AS avg_ms
        FROM llm_usage WHERE at > NOW() - make_interval(days => %s)
        GROUP BY 1, 2 ORDER BY 1 DESC, 3 DESC
    """, (config.LOCAL_TZ, days))
    last24 = db.rows("SELECT purpose, COUNT(*) AS calls FROM llm_usage WHERE at > NOW() - INTERVAL '24 hours' GROUP BY 1 ORDER BY 2 DESC")
    for r in rows:
        r.update(calls=int(r["calls"]), failed=int(r["failed"]), avg_ms=int(r["avg_ms"] or 0))
    return {"days": rows, "last_24h": {r["purpose"]: int(r["calls"]) for r in last24},
            "last_24h_total": sum(int(r["calls"]) for r in last24)}
