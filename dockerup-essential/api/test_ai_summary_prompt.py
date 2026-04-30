"""
Prove-It tests for ai_summary prompt language enforcement + carousel mode.

Bug: pressing EN button still returns Chinese content from local LLM.
Root cause: Chinese-fine-tuned local LLMs ignore English-only instructions
written in English; they require the language directive to be stated IN Chinese.
"""
import pytest
from ai_summary_helper import build_summary_messages

SAMPLE_STATS = {
    "total": 100,
    "passed": 98,
    "failed": 2,
    "yield_pct": 98.0,
    "avg_24g": 450.0,
    "avg_5g": 820.0,
}
SAMPLE_FAILS = "5G check failed(2 units)"
WO = "WO-TEST-001"


# ── ZH sanity ──────────────────────────────────────────────────────────────

def test_zh_system_msg_is_chinese():
    messages = build_summary_messages(SAMPLE_STATS, SAMPLE_FAILS, WO, "zh")
    system = messages[0]["content"]
    assert any(ord(c) > 0x4E00 for c in system), \
        "ZH system message must contain Chinese characters"


def test_zh_prompt_starts_with_chinese_instruction():
    messages = build_summary_messages(SAMPLE_STATS, SAMPLE_FAILS, WO, "zh")
    prompt = messages[1]["content"]
    assert prompt.strip().startswith("請"), \
        "ZH prompt must start with a Chinese instruction"


def test_zh_prompt_has_no_english_only_directive():
    messages = build_summary_messages(SAMPLE_STATS, SAMPLE_FAILS, WO, "zh")
    prompt = messages[1]["content"]
    assert "ENGLISH ONLY" not in prompt.upper(), \
        "ZH prompt must not contain English-only directive"


# ── EN language enforcement ────────────────────────────────────────────────

def test_en_system_msg_contains_english_only_in_english():
    messages = build_summary_messages(SAMPLE_STATS, SAMPLE_FAILS, WO, "en")
    system = messages[0]["content"]
    assert "english only" in system.lower(), \
        "EN system message must contain 'English only'"


def test_en_system_msg_contains_chinese_language_override():
    """
    Chinese-fine-tuned LLMs require the language directive stated IN Chinese.
    Without '请用英文回复' in the system message the model ignores English-only rules.
    This test FAILS before the fix and PASSES after.
    """
    messages = build_summary_messages(SAMPLE_STATS, SAMPLE_FAILS, WO, "en")
    system = messages[0]["content"]
    assert "请用英文" in system or "英文回" in system, \
        "EN system message must include Chinese-language instruction '请用英文回复' " \
        "so Chinese-fine-tuned LLMs comply"


def test_en_prompt_contains_chinese_language_override():
    """
    The user prompt must also carry a Chinese-language English directive.
    This test FAILS before the fix and PASSES after.
    """
    messages = build_summary_messages(SAMPLE_STATS, SAMPLE_FAILS, WO, "en")
    prompt = messages[1]["content"]
    assert "请用英文" in prompt or "英文回" in prompt, \
        "EN prompt must include Chinese-language instruction '请用英文回复' " \
        "to override the model's Chinese bias"


def test_en_prompt_has_english_only_at_start_and_end():
    messages = build_summary_messages(SAMPLE_STATS, SAMPLE_FAILS, WO, "en")
    prompt = messages[1]["content"]
    lower = prompt.lower()
    assert lower.find("english") < 200, \
        "EN prompt must have English instruction near the top"
    # last 300 chars should also reinforce it
    assert "english" in lower[-300:] or "中文" in prompt[-300:], \
        "EN prompt must reinforce language at the end"


# ── Alert level logic ──────────────────────────────────────────────────────

@pytest.mark.parametrize("yield_pct,expected_en,expected_zh", [
    (100.0,  "NORMAL",  "正常"),
    (99.2,   "NORMAL",  "正常"),
    (99.19,  "WARNING", "警告"),
    (98.5,   "WARNING", "警告"),
    (98.49,  "ALARM",   "告警"),
    (0.0,    "ALARM",   "告警"),
])
def test_alert_level_thresholds(yield_pct, expected_en, expected_zh):
    stats = {**SAMPLE_STATS, "yield_pct": yield_pct}
    msg_en = build_summary_messages(stats, "", WO, "en")[1]["content"]
    msg_zh = build_summary_messages(stats, "", WO, "zh")[1]["content"]
    assert expected_en in msg_en, \
        f"EN prompt for yield {yield_pct}% must contain alert level '{expected_en}'"
    assert expected_zh in msg_zh, \
        f"ZH prompt for yield {yield_pct}% must contain alert level '{expected_zh}'"


# ── Carousel mode ──────────────────────────────────────────────────────────

def test_carousel_system_msg_enforces_english_and_no_markdown():
    messages = build_summary_messages(SAMPLE_STATS, SAMPLE_FAILS, WO, "en", mode="carousel")
    system = messages[0]["content"]
    assert "english only" in system.lower(), \
        "carousel system message must enforce English"
    assert "no markdown" in system.lower() or "markdown" in system.lower(), \
        "carousel system message must forbid markdown"


def test_carousel_system_msg_has_chinese_language_override():
    messages = build_summary_messages(SAMPLE_STATS, SAMPLE_FAILS, WO, "en", mode="carousel")
    system = messages[0]["content"]
    assert "请" in system and "英文" in system, \
        "carousel system message must include Chinese-language English directive"


def test_carousel_prompt_has_no_markdown_symbols():
    messages = build_summary_messages(SAMPLE_STATS, SAMPLE_FAILS, WO, "en", mode="carousel")
    prompt = messages[1]["content"]
    # Requirements line should NOT ask for markdown
    assert "markdown format" not in prompt.lower(), \
        "carousel prompt must NOT request markdown format"


def test_carousel_prompt_has_required_section_headers():
    """Carousel prompt must embed the exact section-header template so the LLM
    outputs numbered sections that parseTerminalSegments() can classify."""
    messages = build_summary_messages(SAMPLE_STATS, SAMPLE_FAILS, WO, "en", mode="carousel")
    prompt = messages[1]["content"]
    for header in [
        "WiFi Test Summary Report",
        "1. General Information",
        "2. Test Statistics",
        "3. RF Performance Metrics",
        "4. Yield Analysis",
        "5. Failure Analysis",
    ]:
        assert header in prompt, \
            f"carousel prompt must contain section header '{header}' for terminal formatting"


def test_carousel_alert_level_in_prompt():
    for yield_pct, expected in [(100.0, "NORMAL"), (99.0, "WARNING"), (98.0, "ALARM")]:
        stats = {**SAMPLE_STATS, "yield_pct": yield_pct}
        prompt = build_summary_messages(stats, SAMPLE_FAILS, WO, "en", mode="carousel")[1]["content"]
        assert expected in prompt, \
            f"carousel prompt for yield {yield_pct}% must show alert level '{expected}'"


def test_carousel_mode_ignores_lang_zh():
    """carousel mode is always English regardless of lang parameter."""
    messages_en = build_summary_messages(SAMPLE_STATS, SAMPLE_FAILS, WO, "en", mode="carousel")
    messages_zh = build_summary_messages(SAMPLE_STATS, SAMPLE_FAILS, WO, "zh", mode="carousel")
    # Both should produce English system messages
    assert "english" in messages_en[0]["content"].lower()
    assert "english" in messages_zh[0]["content"].lower()
