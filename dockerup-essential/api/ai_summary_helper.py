"""
Pure helper: builds the LLM messages list for ai_summary.
Extracted so it can be unit-tested without touching DB or network.

mode="normal"   : full report with Markdown, ZH or EN
mode="carousel" : concise plain-text English for terminal typewriter display
"""
from typing import Any


def _alert_labels(yield_pct: float):
    if yield_pct >= 99.2:
        return "正常 (NORMAL)", "NORMAL"
    if yield_pct >= 98.5:
        return "警告 (WARNING)", "WARNING"
    return "告警 (ALARM)", "ALARM"


def build_summary_messages(
    stats: dict[str, Any],
    fails_text: str,
    wo: str,
    lang: str,
    mode: str = "normal",
) -> list[dict]:
    yield_pct = float(stats["yield_pct"])
    alert_zh, alert_en = _alert_labels(yield_pct)

    if mode == "carousel":
        # Structured English report for terminal typewriter display.
        # Must follow the exact section-header format so parseTerminalSegments()
        # can classify lines into report-title / section-header / content.
        fails_display = fails_text if fails_text else "No specific failures (all passed)"
        system_msg = (
            "You are a professional manufacturing test engineer. "
            "Respond in English only. Do NOT use markdown symbols (no **, no #, no -). "
            "Use plain text with the exact numbered-section structure shown in the template. "
            "注意：请完全使用英文回复，不要使用中文字符，不要使用markdown符号。"
        )
        prompt = (
            f"[IMPORTANT] 注意：请完全用英文回复，不使用markdown符号，严格按照模板格式输出。\n\n"
            f"Generate a WiFi test quality report for work order {wo}.\n"
            f"You MUST follow this EXACT template structure (plain text, no markdown):\n\n"
            f"WiFi Test Summary Report\n\n"
            f"1. General Information\n"
            f"Work Order: {wo}\n"
            f"Report Status: Completed\n"
            f"Test Type: WiFi RF Performance & Throughput Test\n\n"
            f"2. Test Statistics\n"
            f"Total Units Tested: {stats['total']}\n"
            f"Total Passed: {stats['passed']}\n"
            f"Total Failed: {stats['failed']}\n"
            f"Yield Rate: {yield_pct}%\n"
            f"Alert Level: {alert_en} (Threshold: >=99.2% Normal, 98.5-99.19% Warning, <98.5% Alarm)\n\n"
            f"3. RF Performance Metrics\n"
            f"Average 2.4G Throughput: {stats['avg_24g']} Mbps\n"
            f"Average 5G Throughput: {stats['avg_5g']} Mbps\n\n"
            f"4. Yield Analysis\n"
            f"[Write 2 sentences: assess yield vs alert threshold, production status.]\n\n"
            f"5. Failure Analysis & Recommendations\n"
            f"Failure Root Cause: {fails_display}\n"
            f"[Write 2-3 sentences of follow-up recommendations based on the failure data.]\n\n"
            f"Fill in the bracketed placeholders with your analysis. "
            f"Keep section headers exactly as shown (e.g. '1. General Information'). "
            f"ENGLISH ONLY. No markdown. 请用英文回复。"
        )
        return [
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": prompt},
        ]

    if lang == "en":
        system_msg = (
            "You are a professional manufacturing test engineer and data analysis expert. "
            "You must always respond in English only. Never use Chinese characters in your response. "
            "注意：请完全使用英文回复，绝对不要使用中文字符。"
        )
        prompt = (
            f"[IMPORTANT] 注意：请完全使用英文回复，不要使用任何中文字符。\n"
            f"You MUST write this entire response in English. Do NOT use Chinese characters.\n\n"
            f"Generate a concise WiFi test summary report for work order {wo} "
            f"as a professional manufacturing test engineer.\n\n"
            f"Work order data:\n"
            f"- Total tested: {stats['total']}\n"
            f"- Pass: {stats['passed']}\n"
            f"- Fail: {stats['failed']}\n"
            f"- Yield: {yield_pct}%\n"
            f"- Alert level: {alert_en} (thresholds: >=99.2% Normal, 98.5%-99.19% Warning, <98.5% Alarm)\n"
            f"- Avg 2.4G throughput: {stats['avg_24g']} Mbps\n"
            f"- Avg 5G throughput: {stats['avg_5g']} Mbps\n"
            f"- Main failure reasons: {fails_text}\n\n"
            f"Requirements:\n"
            f"1. Analyze whether the yield meets the target based on the alert level above.\n"
            f"2. Provide brief follow-up recommendations for any failure causes.\n"
            f"3. Use Markdown format with bullet lists and section headers.\n"
            f"4. Do NOT output JSON.\n"
            f"5. 请用英文回复。WRITE IN ENGLISH ONLY. DO NOT USE CHINESE.\n"
        )
    else:
        system_msg = "你是一個專業的製造測試工程師與數據分析專家。請一律以繁體中文回答。"
        fails_display = fails_text if fails_text else "無特定異常(或全數Pass)"
        prompt = (
            f"請以繁體中文且專業的測試工程師口吻，為工單 {wo} 產出一份簡短的測試總結報告。\n"
            f"工單數據如下：\n"
            f"- 總測試數: {stats['total']}\n"
            f"- Pass: {stats['passed']}\n"
            f"- Fail: {stats['failed']}\n"
            f"- 良率: {yield_pct}%\n"
            f"- 告警等級: {alert_zh}（門檻：≥99.2% 正常，98.5%~99.19% 警告，<98.5% 告警）\n"
            f"- 2.4G 平均吞吐量: {stats['avg_24g']} Mbps\n"
            f"- 5G 平均吞吐量: {stats['avg_5g']} Mbps\n"
            f"- 主要失敗原因: {fails_display}\n\n"
            f"請重點分析良率是否達標（依告警等級說明），以及針對失敗原因給予簡短的後續追蹤建議。"
            f"請使用 Markdown 格式，適度使用列表與重點標示。不要輸出JSON。"
        )

    return [
        {"role": "system", "content": system_msg},
        {"role": "user",   "content": prompt},
    ]
