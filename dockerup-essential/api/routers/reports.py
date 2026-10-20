"""AI summary of a work order (LLM status + generation). Same request/response as before;
the structured report (facts table, json_schema output, storage) comes in a later phase."""
import logging
from typing import Optional

from fastapi import APIRouter

from ai_summary_helper import build_summary_messages
from errors import ApiError, Msg, not_found
from services import llm, stats

log = logging.getLogger("wifi.reports")
router = APIRouter(tags=["reports"])


@router.get("/api/llm-status")
def llm_status():
    s = llm.status()
    return {"status": s["status"], "connected": s["connected"], "model": s["model"]}


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
        return {"summary": llm.chat(messages)}
    except Exception as exc:
        log.warning("LLM summary failed for %s: %s", wo, exc)
        raise ApiError(500, Msg(f"LLM 請求失敗：{exc}", f"LLM request failed: {exc}"), "llm_failed") from exc
