"""Web Log Upload — input formats and the validation rules R1–R16 (pure functions, no I/O).

Work order   <4 digits>-<YYMMDD><3-digit sequence>  or  NOWO-<YYMMDD><seq>   e.g. 5101-260108007
             The date must exist on the calendar and the sequence is never 000. A bare "NOWO"
             means "no work order": the service expands it to NOWO-<today><next sequence>.
Product      TN-<n>_<model> through the product registry (services/products.py).
Planned qty  1–99,999 (PLAN_QTY_MAX); > PLAN_QTY_WARN is probably an extra zero.

The rules decide; the AI assistant only phrases their result. Every issue carries both
languages: {"code": "R7", "level": "error|warn|info", "message": zh, "message_en": en, ...}.
"""
import calendar
import re
import unicodedata
from datetime import date
from typing import Any, Optional

import config

DASHES = dict.fromkeys(map(ord, "‐‑‒–—−－"), "-")
WO_RE = re.compile(r"^(\d{4}|NOWO)-(\d{2})(\d{2})(\d{2})(\d{3})$")
WO_SEARCH_RE = re.compile(r"(?<![0-9A-Z])(?:\d{4}|NOWO)-\d{9}(?![0-9])")
NOWO_PREFIX = "NOWO"
_QTY_RE = re.compile(r"^\d{1,3}(?:,\d{3})+$|^\d+$")


# ─── Normalisation and shape checks ──────────────────────────────────────────

def normalize_wo(text: Any) -> str:
    s = unicodedata.normalize("NFKC", str(text or "")).translate(DASHES)
    return re.sub(r"\s+", "", s).upper()


def check_wo(text: Any) -> dict:
    """{'value': normalized, 'code': ok | empty | nowo | wo_bad_shape | wo_bad_date | wo_seq_zero}."""
    s = normalize_wo(text)
    if not s:
        return {"value": s, "code": "empty"}
    if s == NOWO_PREFIX:
        return {"value": s, "code": "nowo"}
    m = WO_RE.match(s)
    if not m:
        return {"value": s, "code": "wo_bad_shape"}
    yy, mm, dd, seq = int(m.group(2)), int(m.group(3)), int(m.group(4)), m.group(5)
    if not 1 <= mm <= 12 or not 1 <= dd <= calendar.monthrange(2000 + yy, mm)[1]:
        return {"value": s, "code": "wo_bad_date"}
    if seq == "000":
        return {"value": s, "code": "wo_seq_zero"}
    return {"value": s, "code": "ok", "date": date(2000 + yy, mm, dd), "nowo": s.startswith(NOWO_PREFIX)}


def find_work_orders(text: Any) -> list[str]:
    """Every work-order-shaped token in free text (normalised), in order."""
    return WO_SEARCH_RE.findall(normalize_wo(text))


def parse_quantity(value: Any) -> Optional[int]:
    """int from '1200', '1,200' or an int; None for anything else (never a bool)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    s = unicodedata.normalize("NFKC", str(value or "")).strip().replace("，", ",")
    if not _QTY_RE.match(s):
        return None
    return int(s.replace(",", ""))


def nowo_value(today: date, seq: int) -> str:
    return f"{NOWO_PREFIX}-{today:%y%m%d}{seq:03d}"


# ─── Issues ──────────────────────────────────────────────────────────────────

def _issue(code: str, level: str, zh: str, en: str, **extra) -> dict:
    return {"code": code, "level": level, "message": zh, "message_en": en, **extra}


def has_errors(issues) -> bool:
    return any(i["level"] == "error" for i in issues)


def has_warnings(issues) -> bool:
    return any(i["level"] == "warn" for i in issues)


def _wo_error(check: dict) -> dict:
    code = check["code"]
    value = check["value"]
    if code == "empty":
        return _issue("R3", "error", "請輸入工單號；沒有工單號請輸入 NOWO。",
                      "Please enter the work order, or NOWO when there is none.", reason="wo_empty")
    if code == "wo_bad_date":
        return _issue("R3", "error", f"工單號「{value}」的日期不是有效日期（格式為 年月日 YYMMDD）。",
                      f"The date in work order \"{value}\" is not a valid calendar date (YYMMDD).", reason=code)
    if code == "wo_seq_zero":
        return _issue("R3", "error", f"工單號「{value}」的流水號不可為 000。",
                      f"The sequence of work order \"{value}\" cannot be 000.", reason=code)
    return _issue("R3", "error",
                  f"工單號「{value}」格式不正確。格式為 4 位數字-年月日(YYMMDD)+3 位流水號，例如 5101-260108007；沒有工單號請輸入 NOWO。",
                  f"Work order \"{value}\" has the wrong format. Use 4 digits, a hyphen, YYMMDD and a 3-digit sequence, "
                  f"for example 5101-260108007; enter NOWO when there is none.", reason="wo_bad_shape")


def validate(facts: dict, fields: dict, ctx: dict) -> list[dict]:
    """Run R1–R16.

    fields: work_order (final normalised value or None), wo_check (check_wo result),
            product (products.Registry.resolve result), planned_qty (int or None), planned_raw
    ctx:    today, existing_same, existing_other [{work_order, product_model, n}], wo_products [..],
            cumulative_sn (DB ∪ batch), prev_plan, nowo_preview
    """
    issues: list[dict] = []
    logs = int(facts.get("logs") or 0)
    product = fields.get("product") or {}
    wo_check = fields.get("wo_check") or {"code": "empty", "value": ""}
    work_order = fields.get("work_order")
    qty = fields.get("planned_qty")

    # R1 — any valid log at all
    if logs == 0:
        issues.append(_issue("R1", "error", "沒有任何檔名符合格式的 log（YYYYMMDD_HHMMSS_SN_MAC1_MAC2_PASS|FAIL.txt）。",
                             "No log has a valid file name (YYYYMMDD_HHMMSS_SN_MAC1_MAC2_PASS|FAIL.txt)."))

    # R2 / R4 / R16 — product
    status = product.get("status")
    if status == "known":
        if product.get("input") and product["input"].strip() != product["canonical"]:
            issues.append(_issue("R2", "info", f"產品名稱「{product['input'].strip()}」已對應為正式名稱 {product['canonical']}。",
                                 f"Product \"{product['input'].strip()}\" resolved to {product['canonical']}.", reason="product_resolved"))
    elif status == "new":
        issues.append(_issue("R4", "warn", f"「{product['proposal']}」是新產品（辨認庫中沒有，TN 編號也未使用過）。確認後會建立這個產品名稱。",
                             f"\"{product['proposal']}\" is a new product (not in the registry; its TN number is unused). "
                             f"Confirm to create it.", proposal=product["proposal"], near=product.get("near") or []))
    elif status == "blocked_same_tn":
        same = product.get("same_tn") or []
        issues.append(_issue("R16", "error",
                             f"產品名稱「{product['input'].strip()}」不在辨認庫，而且 TN 編號已屬於 {', '.join(same)}。請改選既有產品。",
                             f"Product \"{product['input'].strip()}\" is not in the registry and its TN number already belongs to "
                             f"{', '.join(same)}. Please choose the existing product.",
                             reason="product_same_tn", suggestions=list(dict.fromkeys(same + (product.get("near") or [])))))
    elif status == "ambiguous":
        cands = product.get("candidates") or []
        issues.append(_issue("R2", "error", f"「{product['input'].strip()}」對應到多個產品：{', '.join(cands)}。請選擇一個。",
                             f"\"{product['input'].strip()}\" matches several products: {', '.join(cands)}. Please choose one.",
                             reason="product_ambiguous", suggestions=cands))
    elif not (product.get("input") or "").strip():
        issues.append(_issue("R2", "error", "請選擇或輸入產品名稱（格式 TN-<編號>_<型號>，例如 TN-86_PICO-IMX7）。",
                             "Please choose or enter the product (TN-<number>_<model>, for example TN-86_PICO-IMX7).", reason="product_empty"))
    else:
        issues.append(_issue("R2", "error", f"產品名稱「{product['input'].strip()}」格式不正確。格式為 TN-<編號>_<型號>，例如 TN-86_PICO-IMX7。",
                             f"Product \"{product['input'].strip()}\" has the wrong format. Use TN-<number>_<model>, for example TN-86_PICO-IMX7.",
                             reason="product_bad_format"))

    # R3 / R5 / R6 — work order
    if wo_check["code"] == "nowo":
        preview = ctx.get("nowo_preview") or f"{NOWO_PREFIX}-…"
        issues.append(_issue("R5", "info", f"此批無工單號，將以 {preview} 儲存（每批獨立的編號）；此批不與生產工單關聯，工單類統計僅供參考。",
                             f"No work order: this batch is stored as {preview} (one number per batch). It is not linked to a "
                             f"production work order, so work-order statistics are for reference only.", value=preview))
    elif wo_check["code"] != "ok":
        issues.append(_wo_error(wo_check))
    else:
        if wo_check.get("nowo"):
            issues.append(_issue("R5", "info", f"{work_order} 是無工單號批次；此批不與生產工單關聯，工單類統計僅供參考。",
                                 f"{work_order} is a no-work-order batch; it is not linked to a production work order.", value=work_order))
        today = ctx.get("today") or date.today()
        if wo_check.get("date") and wo_check["date"] > today:
            issues.append(_issue("R6", "warn", f"工單號 {work_order} 的日期（{wo_check['date']:%Y-%m-%d}）晚於今天，請確認沒有打錯。",
                                 f"The date of work order {work_order} ({wo_check['date']:%Y-%m-%d}) is in the future. Please double-check."))
        if (fields.get("wo_input") or "") and normalize_wo(fields["wo_input"]) != str(fields["wo_input"]).strip():
            issues.append(_issue("R3", "info", f"工單號已由「{str(fields['wo_input']).strip()}」轉為 {work_order}。",
                                 f"Work order \"{str(fields['wo_input']).strip()}\" normalised to {work_order}.", reason="wo_normalized"))

    canonical = product.get("canonical") or (product.get("proposal") if status == "new" else None)

    # R7 — the work order already has a different product
    if work_order and canonical:
        others = [p for p in (ctx.get("wo_products") or []) if p and p != canonical]
        if others:
            issues.append(_issue("R7", "warn", f"工單 {work_order} 在資料庫中的產品是 {', '.join(others)}，與輸入的 {canonical} 不同。",
                                 f"Work order {work_order} is stored with product {', '.join(others)}, not {canonical}."))

    # R8 — differs from what the package says
    inferred_wo, inferred_pm = facts.get("inferred_wo"), facts.get("inferred_product")
    if work_order and inferred_wo and inferred_wo != work_order:
        issues.append(_issue("R8", "warn", f"檔案/資料夾名稱看起來是工單 {inferred_wo}，但輸入的是 {work_order}。",
                             f"The file or folder names suggest work order {inferred_wo}, but {work_order} was entered."))
    if canonical and inferred_pm and inferred_pm != canonical:
        issues.append(_issue("R8", "warn", f"檔案/資料夾名稱看起來是產品 {inferred_pm}，但輸入的是 {canonical}。",
                             f"The file or folder names suggest product {inferred_pm}, but {canonical} was entered."))

    # R9 / R10 — hashes already in the database
    if work_order and canonical:
        other = ctx.get("existing_other") or []
        n_other = sum(int(r["n"]) for r in other)
        if n_other:
            where_zh = "、".join(f"{r['work_order'] or '—'} / {r['product_model'] or '—'}（{r['n']}）" for r in other[:5])
            where_en = ", ".join(f"{r['work_order'] or '—'} / {r['product_model'] or '—'} ({r['n']})" for r in other[:5])
            issues.append(_issue("R9", "warn", f"{n_other} 個 log 已存在於其他工單/產品：{where_zh}。這些檔案會被略過，不會改標。",
                                 f"{n_other} logs already exist under another work order / product: {where_en}. "
                                 f"They will be skipped, not relabelled.", n=n_other))
        n_same = int(ctx.get("existing_same") or 0)
        if n_same and logs and n_same + n_other >= logs:
            issues.append(_issue("R10", "info", f"這批 {logs} 個 log 都已經在資料庫裡（同一工單/產品），不會新增任何資料。",
                                 f"All {logs} logs are already in the database under this work order / product; nothing will be added.", n=n_same))
        elif n_same:
            issues.append(_issue("R10", "info", f"{n_same} 個 log 已存在於同一工單/產品，視同重傳，會被略過。",
                                 f"{n_same} logs already exist under this work order / product and will be skipped.", n=n_same))

    # R11 — informational facts
    if facts.get("dup_in_batch"):
        issues.append(_issue("R11", "info", f"這批有 {facts['dup_in_batch']} 個內容相同的重複檔案，只會寫入一次。",
                             f"{facts['dup_in_batch']} files in this batch have identical content; each is written once."))
    if facts.get("ignored_total"):
        issues.append(_issue("R11", "info", f"{facts['ignored_total']} 個非 log 檔案會被忽略（CSV、報表、不符格式的檔名等）。",
                             f"{facts['ignored_total']} non-log files are ignored (CSV, reports, names not in the log format)."))
    if facts.get("first_test"):
        issues.append(_issue("R11", "info", f"測試時間 {facts['first_test']} ~ {facts['last_test']}，{facts.get('unique_sn', 0)} 個不重複 SN，"
                             f"PASS {facts.get('pass', 0)} / FAIL {facts.get('fail', 0)}。",
                             f"Tests from {facts['first_test']} to {facts['last_test']}; {facts.get('unique_sn', 0)} unique SNs, "
                             f"PASS {facts.get('pass', 0)} / FAIL {facts.get('fail', 0)}."))
    for key, zh, en in (("files", "檔案數", "file count"), ("bytes", "解壓後總大小", "extracted size")):
        if (facts.get("limits") or {}).get(key):
            issues.append(_issue("R11", "warn", f"已達{zh}上限，超過的檔案沒有讀入。", f"The {en} limit was reached; files beyond it were not read."))

    # R12 / R13 — planned quantity
    if qty is None:
        raw = str(fields.get("planned_raw") or "").strip()
        issues.append(_issue("R12", "error", "請輸入計畫測試數量（1–99,999 的整數）。" if not raw else
                             f"計畫測試數量「{raw}」不是 1–99,999 的整數。",
                             "Please enter the planned test quantity (a whole number from 1 to 99,999)." if not raw else
                             f"Planned quantity \"{raw}\" is not a whole number from 1 to 99,999.", reason="qty_empty" if not raw else "qty_bad"))
    elif not 1 <= qty <= config.PLAN_QTY_MAX:
        issues.append(_issue("R12", "error", f"計畫測試數量必須是 1–{config.PLAN_QTY_MAX:,} 的整數（輸入 {qty:,}）。",
                             f"The planned quantity must be between 1 and {config.PLAN_QTY_MAX:,} (got {qty:,}).", reason="qty_range"))
    else:
        if qty > config.PLAN_QTY_WARN:
            issues.append(_issue("R13", "warn", f"計畫測試數量 {qty:,} 超過 {config.PLAN_QTY_WARN:,}，請確認沒有多打一個 0。",
                                 f"Planned quantity {qty:,} is above {config.PLAN_QTY_WARN:,}; please check for an extra zero."))
        # R14 — alignment (shown, never judged)
        batch_sn = int(facts.get("unique_sn") or 0)
        cumulative = int(ctx.get("cumulative_sn") if ctx.get("cumulative_sn") is not None else batch_sn)
        gap = cumulative - qty
        state = "met" if gap == 0 else ("over" if gap > 0 else "short")
        zh_state = {"met": "達成", "over": f"超出 {gap:,}", "short": f"尚缺 {-gap:,}"}[state]
        en_state = {"met": "met", "over": f"{gap:,} over", "short": f"{-gap:,} short"}[state]
        issues.append(_issue("R14", "info", f"數量對齊：計畫 {qty:,}、本批不重複 SN {batch_sn:,}、上傳後工單累計 {cumulative:,} → {zh_state}。",
                             f"Alignment: planned {qty:,}, unique SNs in this batch {batch_sn:,}, cumulative after upload {cumulative:,} → {en_state}.",
                             planned=qty, batch_sn=batch_sn, cumulative=cumulative, gap=gap, state=state))
        # R15 — a different plan is already stored
        prev = ctx.get("prev_plan")
        if work_order and prev is not None and prev != qty:
            issues.append(_issue("R15", "info", f"工單 {work_order} 已有計畫數 {prev:,}，上傳後將更新為 {qty:,}（撤銷時還原）。",
                                 f"Work order {work_order} already has a plan of {prev:,}; it becomes {qty:,} after upload (restored on undo).",
                                 prev=prev))
    return issues


def issue_summary(issues) -> dict:
    return {"issues": issues, "can_upload": not has_errors(issues), "needs_ack": has_warnings(issues)}
