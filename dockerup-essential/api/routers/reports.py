"""AI reports.

GET  /api/reports/{wo}            the stored report (instant); queues generation when missing/stale
POST /api/reports/{wo}/generate   queue a regeneration now
GET  /api/workorders/{wo}/ai-summary   the previous synchronous generator (legacy page)
"""
import logging
from typing import Any, Optional

from fastapi import APIRouter, Query

import config
from ai_summary_helper import build_summary_messages
from errors import ApiError, Msg, not_found
from services import llm, reports, stats

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
    facts, fail_rows = stats.work_order_facts(wo, product_model)
    if facts is None:
        raise not_found(Msg("找不到此工單或沒有資料", "Work order not found or has no data"))
    if fail_rows:
        fails_text = ", ".join(f"{r['fail_reason']}({r['count']} units)" for r in fail_rows)
    else:
        fails_text = "No specific failures (all passed)" if lang == "en" else "無特定異常(或全數Pass)"
    messages = build_summary_messages(facts, fails_text, wo, lang, mode)
    try:
        return {"summary": llm.chat(messages, max_tokens=config.LLM_REPORT_MAX_TOKENS)}
    except Exception as exc:
        log.warning("LLM summary failed for %s: %s", wo, exc)
        raise ApiError(500, Msg(f"LLM 請求失敗：{exc}", f"LLM request failed: {exc}"), "llm_failed") from exc
