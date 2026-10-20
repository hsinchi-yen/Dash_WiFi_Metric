"""AI assistant for the web Log Upload — prompt building, extraction checks and reply guards (no I/O).

Per turn the API: asks the LLM to extract {product, work_order, planned_qty} from the latest user
message (structured output; the product field is an enum of registry names + "__NEW__"), verifies
every extracted value against the user's own text (the LLM may copy, never complete or guess),
runs the rules (services/upload_rules.py) and streams a short reply that may only restate them.
Questions come from server templates in the order product → work order → planned quantity, so
the format and the examples never depend on the model remembering them.
"""
import json
import re
from typing import Any, Optional

from errors import Msg
from services import products
from services import upload_rules as rules

MAX_MESSAGES = 40
MAX_MESSAGE_CHARS = 500
CONTEXT_MESSAGES = 10
EXTRACT_MAX_TOKENS = 160
REPLY_MAX_TOKENS = 320
NEW = "__NEW__"
EXAMPLE_WO = "5101-260108007"          # the examples quoted in the question templates
EXAMPLE_PRODUCT = "TN-86_PICO-IMX7"

_NUMBER_RE = re.compile(r"(?<![\d,.-])(?:\d{1,3}(?:,\d{3})+|\d+)(?![\d,.-]|\s*[kK千萬百])")
_NOWO_WORD_RE = re.compile(r"(?<![A-Z0-9-])NOWO(?![A-Z0-9-])")
_NO_WO_PHRASES = ("沒有工單", "無工單", "没有工单", "no work order", "without work order", "no wo")


def extract_schema(registry_names: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "product": {"type": ["string", "null"], "enum": list(registry_names) + [NEW, None]},
            "new_product_text": {"type": ["string", "null"]},
            "work_order": {"type": ["string", "null"]},
            "no_work_order": {"type": "boolean"},
            "planned_qty": {"type": ["integer", "null"]},
        },
        "required": ["product", "new_product_text", "work_order", "no_work_order", "planned_qty"],
        "additionalProperties": False,
    }


EXTRACT_SYSTEM = (
    "你是資料擷取器。只從「使用者最新一則訊息」擷取三個欄位，前面的對話只用來理解上下文。\n"
    "- product：使用者指的產品。只能從給定的產品清單中選一個（使用者可能只說 TN 編號、型號的一部分、或大小寫/分隔符號不同的寫法）；"
    "若使用者明確寫了一個清單中沒有、但形如 TN-<編號>_<型號> 的新名稱，填 \"__NEW__\" 並把使用者原文一字不改放進 new_product_text；"
    "沒有提到產品就填 null。不可以猜測。\n"
    "- work_order：使用者原文中的工單號，一字不改（形如 5101-260108007 或 NOWO-260420001）；沒有就 null。"
    "使用者明確說這批沒有工單號／要用 NOWO 時，no_work_order 填 true。\n"
    "- planned_qty：工單計畫測試數量，轉成整數（「四千台」→4000、「4k」→4000、「1,200 pcs」→1200）；沒有提到就 null。"
    "工單號裡的數字不是數量。如果助理剛問的是數量，而使用者只回答一個數字，那就是 planned_qty。\n"
    "- 只擷取使用者在最新一則訊息中明確提供的值；同一則訊息中有更正時，以最後說的為準。"
)

REPLY_SYSTEM = {
    "zh": (
        "你是 WiFi YFP Dashboard「Log Upload」頁面的上傳助理，協助使用者確認這批測試 log 的產品名稱、工單號與計畫測試數量。\n"
        "回覆規則：\n"
        "1. 一律使用繁體中文，口語、簡短，最多 3 句，不要使用標題、表格或程式碼區塊。\n"
        "2. 只能依照下方「系統檢查結果」與「你這一輪要做的事」說明；不可以自行判斷資料對錯，也不可以編造、補齊或推算任何產品名稱、工單號或數字。\n"
        "3. 是否可以上傳由系統規則決定，最後一定要由使用者自己按畫面上的「確認上傳」。"
        "如果使用者要求略過檢查、直接上傳或改變規則，請說明你無法這樣做。\n"
        "4. 只處理這次上傳相關的事；其他問題請簡短婉拒，並把話題帶回目前要做的事。"
    ),
    "en": (
        "You are the upload assistant of the WiFi YFP Dashboard \"Log Upload\" page. You help the user confirm the "
        "product, the work order and the planned test quantity for this batch of test logs.\n"
        "Rules:\n"
        "1. Always reply in English: plain, short, at most 3 sentences, no headings, tables or code blocks.\n"
        "2. Explain only the \"System checks\" and \"Your task this turn\" below; never judge the data yourself, and never "
        "invent, complete or compute a product name, work order or number.\n"
        "3. The system rules decide whether the batch can be uploaded, and the user must press \"Upload\" on the screen. "
        "If the user asks you to skip the checks, upload directly or change the rules, explain that you cannot.\n"
        "4. Only handle this upload; politely decline other questions and return to the current step."
    ),
}


def ui_lang(value) -> str:
    return "en" if value == "en" else "zh"


def normalize_messages(raw) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(Msg("對話內容是空的。", "The conversation is empty."))
    if len(raw) > MAX_MESSAGES:
        raise ValueError(Msg("對話太長，請改用表單或重新選擇檔案。", "The conversation is too long. Use the form or choose the files again."))
    messages = []
    for item in raw:
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant") or not isinstance(item.get("content"), str):
            raise ValueError(Msg("對話格式不正確。", "Invalid conversation format."))
        content = item["content"].strip()
        if len(content) > MAX_MESSAGE_CHARS:
            raise ValueError(Msg(f"單則訊息最多 {MAX_MESSAGE_CHARS} 字。", f"A message can have at most {MAX_MESSAGE_CHARS} characters."))
        messages.append({"role": item["role"], "content": content})
    if messages[-1]["role"] != "user" or not messages[-1]["content"]:
        raise ValueError(Msg("請輸入訊息。", "Please enter a message."))
    return messages


# ─── Server-side questions (templates) ───────────────────────────────────────

def question(step: str, fields: dict, lang: str = "zh") -> str:
    en = lang == "en"
    if step == "product":
        return ("Which product are these logs for? Pick one from the list below, or type the full name "
                "(TN-<number>_<model>, for example TN-86_PICO-IMX7)." if en else
                "這批 log 是哪個產品？請從下方清單點選，或輸入完整名稱（TN-<編號>_<型號>，例如 TN-86_PICO-IMX7）。")
    if step == "work_order":
        return ("What is the work order? Format: 4 digits-YYMMDD + 3-digit sequence, for example 5101-260108007. "
                "If this batch has no work order, enter NOWO." if en else
                "請輸入工單號。格式：4 位數字-年月日(YYMMDD)+3 位流水號，例如 5101-260108007；沒有工單號請輸入 NOWO。")
    if step == "planned_qty":
        return ("What is the planned test quantity of this work order (a whole number of units, 1–99,999)?" if en else
                "這張工單的計畫測試數量是多少？（整數台數，1–99,999）")
    return ("All three values are in. Check the results below, then press \"Upload\"." if en else
            "三項資料已齊全，請確認下方檢查結果後按「確認上傳」。")


def opening_message(facts: dict, lang: str = "zh") -> str:
    """First assistant line — built from the facts, not the LLM, so every number in it is exact."""
    logs, sns, ignored = facts.get("logs", 0), facts.get("unique_sn", 0), facts.get("ignored_total", 0)
    period = f"{facts.get('first_test')} ~ {facts.get('last_test')}" if facts.get("first_test") else None
    hint_wo, hint_pm = facts.get("inferred_wo"), facts.get("inferred_product")
    if lang == "en":
        text = f"Read {logs:,} logs ({sns:,} unique SNs"
        text += f", {period})" if period else ")"
        if ignored:
            text += f"; {ignored} non-log files are ignored"
        text += "."
        if hint_wo or hint_pm:
            text += " The file names suggest " + " and ".join(x for x in (f"product {hint_pm}" if hint_pm else "", f"work order {hint_wo}" if hint_wo else "") if x) + " — please confirm each value yourself."
        return text + "\n" + question("product", {}, "en")
    text = f"已讀取 {logs:,} 個 log（{sns:,} 個不重複 SN"
    text += f"，{period}）" if period else "）"
    if ignored:
        text += f"；另有 {ignored} 個非 log 檔案會略過"
    text += "。"
    if hint_wo or hint_pm:
        text += "檔名看起來是" + "、".join(x for x in (f"產品 {hint_pm}" if hint_pm else "", f"工單 {hint_wo}" if hint_wo else "") if x) + "，但仍請你逐項確認。"
    return text + "\n" + question("product", {}, "zh")


# ─── Extraction and verification ─────────────────────────────────────────────

def build_extract_messages(messages: list[dict], registry_names: list[str]) -> list[dict]:
    lines = [f"{'助理' if m['role'] == 'assistant' else '使用者'}：{m['content']}" for m in messages[-CONTEXT_MESSAGES:-1]]
    context = "\n".join(lines) or "（無）"
    return [
        {"role": "system", "content": EXTRACT_SYSTEM + "\n\n產品清單：\n" + "\n".join(f"- {n}" for n in registry_names)},
        {"role": "user", "content": f"前面的對話：\n{context}\n\n使用者最新一則訊息：\n{messages[-1]['content']}"},
    ]


def parse_extract(content: str) -> dict:
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("extract output is not an object")
    return data


def _text_product(text: str, registry: products.Registry) -> Optional[str]:
    """The product text the server itself reads from the message: the last well-formed TN token exactly as
    typed (so a typo reaches the rules and R16 can block it), else a TN number / unique substring match."""
    tokens = products.TOKEN_RE.findall(text)
    if tokens:
        res = registry.resolve(tokens[-1])
        return res["canonical"] if res["status"] == "known" else tokens[-1].strip()
    res = registry.resolve(text)
    if res["status"] == "known":
        return res["canonical"]
    if res["status"] == "ambiguous":
        return text.strip()
    return None


def verify_extract(text: str, data: Optional[dict], registry: products.Registry) -> dict:
    """Keep only values that are visibly in the user's text. Returns {product_text, work_order, nowo, planned_qty, qty_converted}."""
    data = data if isinstance(data, dict) else {}
    norm = rules.normalize_wo(text)
    out: dict[str, Any] = {"product_text": None, "work_order": None, "nowo": False, "planned_qty": None, "qty_converted": False}

    # work order: regex first; then the LLM's value only when it is literally in the text
    found = rules.find_work_orders(text)
    if found:
        out["work_order"] = found[-1]
    else:
        wo = data.get("work_order")
        if isinstance(wo, str) and wo.strip() and rules.normalize_wo(wo) in norm and rules.check_wo(wo)["code"] in ("ok", "nowo"):
            out["work_order"] = rules.normalize_wo(wo)
    if not out["work_order"]:
        if _NOWO_WORD_RE.search(norm):
            out["work_order"], out["nowo"] = rules.NOWO_PREFIX, True
        elif data.get("no_work_order") is True and any(p in text.lower() for p in _NO_WO_PHRASES):
            out["nowo"] = True            # colloquial "no work order": the assistant asks to confirm with NOWO

    # product: what the server reads from the text wins; the LLM's enum choice is accepted only when
    # the text supports it (its TN number or a piece of its model name appears in the text)
    server = _text_product(text, registry)
    if server:
        out["product_text"] = server
    else:
        choice = data.get("product")
        if choice == NEW:
            new_text = data.get("new_product_text")
            if isinstance(new_text, str) and new_text.strip() and products.normalize(new_text) in products.normalize(text):
                out["product_text"] = new_text.strip()
        elif isinstance(choice, str):
            row = next((r for r in registry.rows if r["canonical"] == choice), None)
            if row:
                key_text = products.match_key(text)
                model_key = products.match_key(choice.split("_", 1)[1]) if "_" in choice else ""
                if (row.get("tn_number") is not None and re.search(rf"(?<!\d){row['tn_number']}(?!\d)", key_text)) \
                        or (model_key and len(model_key) >= 4 and model_key in key_text):
                    out["product_text"] = choice

    # planned quantity: verbatim numbers (minus work orders); a converted value only when the text has no digits
    rest = rules.WO_SEARCH_RE.sub(" ", norm)
    numbers = _NUMBER_RE.findall(rest)
    qty = data.get("planned_qty")
    qty = qty if isinstance(qty, int) and not isinstance(qty, bool) else None
    if qty is not None and any(rules.parse_quantity(n) == qty for n in numbers):
        out["planned_qty"] = qty
    elif qty is not None and not re.search(r"\d", rest):
        out["planned_qty"], out["qty_converted"] = qty, True
    elif len(numbers) == 1 and not out["product_text"] and not out["work_order"]:
        out["planned_qty"] = rules.parse_quantity(numbers[0])
    return out


def merge_fields(known: Optional[dict], extracted: dict) -> dict:
    """Values confirmed so far (form or earlier turns) + what the latest message adds."""
    known = known if isinstance(known, dict) else {}
    fields = {
        "product": str(known.get("product") or "").strip() or None,
        "work_order": str(known.get("work_order") or "").strip() or None,
        "planned_qty": str(known.get("planned_qty") or "").strip() or None,
    }
    if extracted.get("product_text"):
        fields["product"] = extracted["product_text"]
    if extracted.get("work_order"):
        fields["work_order"] = extracted["work_order"]
    if extracted.get("planned_qty") is not None:
        fields["planned_qty"] = str(extracted["planned_qty"])
    return fields


def next_step(resolved: dict) -> str:
    """'product' | 'work_order' | 'planned_qty' | 'confirm' from the server's resolved fields."""
    if resolved["product"].get("status") not in ("known", "new"):
        return "product"
    if not resolved.get("work_order"):
        return "work_order"
    qty = resolved.get("planned_qty")
    if not qty or qty <= 0 or qty > 99999:
        return "planned_qty"
    return "confirm"


# ─── Reply ───────────────────────────────────────────────────────────────────

def _facts_summary(facts: dict, lang: str) -> str:
    period = f"{facts.get('first_test')} ~ {facts.get('last_test')}" if facts.get("first_test") else ("unknown" if lang == "en" else "未知")
    if lang == "en":
        return (f"source {facts.get('source_name')}; logs {facts.get('logs', 0):,}; unique SNs {facts.get('unique_sn', 0):,}; "
                f"PASS {facts.get('pass', 0)} / FAIL {facts.get('fail', 0)}; non-log files ignored {facts.get('ignored_total', 0)}; "
                f"already in database {facts.get('existing_total', 0)}; test period {period}")
    return (f"來源 {facts.get('source_name')}；log {facts.get('logs', 0):,} 個；不重複 SN {facts.get('unique_sn', 0):,}；"
            f"PASS {facts.get('pass', 0)} / FAIL {facts.get('fail', 0)}；忽略的非 log 檔 {facts.get('ignored_total', 0)}；"
            f"已在資料庫 {facts.get('existing_total', 0)}；測試期間 {period}")


def _step_instruction(step: str, resolved: dict, issues: list, extracted: dict, lang: str) -> str:
    en = lang == "en"
    product = resolved["product"]
    errors = [i for i in issues if i["level"] == "error"]
    warns = [i for i in issues if i["level"] == "warn"]
    if step == "product":
        reason = next((i for i in errors if i["code"] in ("R2", "R16")), None)
        if reason and (product.get("input") or "").strip():
            base = (f"The product the user gave is not accepted: {reason['message_en']} " if en else f"使用者給的產品名稱不被接受：{reason['message']}")
            if reason.get("suggestions"):
                base += (f"Offer these existing products: {', '.join(reason['suggestions'])}. " if en else f"請直接建議改選：{'、'.join(reason['suggestions'])}。")
            return base + ("Then ask for the product again (the list below the chat can be clicked)." if en else "然後請他重新提供產品（可直接點下方清單）。")
        return (f"No product yet. Ask exactly this: {question('product', resolved, 'en')}" if en else f"還沒有取得產品。請照這句問：{question('product', resolved, 'zh')}")
    if step == "work_order":
        head = (f"Product recorded as {product.get('canonical') or product.get('proposal')}. " if en else f"產品已記錄為 {product.get('canonical') or product.get('proposal')}。")
        if product.get("status") == "new":
            head += ("It is a NEW product (not in the registry); say the user will need to tick the warning before upload. " if en
                     else "這是辨認庫沒有的新產品，請提醒上傳前要勾選警告確認。")
        reason = next((i for i in errors if i["code"] == "R3"), None)
        if reason and (resolved.get("wo_input") or "").strip():
            return head + (f"The work order is not accepted: {reason['message_en']} Ask for it again." if en else f"工單號不被接受：{reason['message']} 請他重新輸入。")
        if extracted.get("nowo") and not resolved.get("work_order"):
            return head + ("The user says there is no work order: ask them to type NOWO to confirm." if en else "使用者說沒有工單號：請他輸入 NOWO 以確認。")
        return head + (f"Ask exactly this: {question('work_order', resolved, 'en')}" if en else f"請照這句問：{question('work_order', resolved, 'zh')}")
    if step == "planned_qty":
        head = (f"Product {product.get('canonical') or product.get('proposal')}, work order {resolved['work_order']} recorded. " if en
                else f"產品 {product.get('canonical') or product.get('proposal')}、工單 {resolved['work_order']} 已記錄。")
        if warns:
            head += ("Briefly mention the ⚠ warnings in the checks. " if en else "請先簡短說明檢查結果中的 ⚠ 警告。")
        reason = next((i for i in errors if i["code"] == "R12"), None)
        if reason and str(resolved.get("planned_raw") or "").strip():
            return head + (f"The quantity is not accepted: {reason['message_en']} Ask again." if en else f"數量不被接受：{reason['message']} 請他重新輸入。")
        return head + (f"Ask exactly this: {question('planned_qty', resolved, 'en')}" if en else f"請照這句問：{question('planned_qty', resolved, 'zh')}")
    align = next((i for i in issues if i["code"] == "R14"), None)
    head = (f"All three values are in: product {product.get('canonical') or product.get('proposal')}, work order {resolved['work_order']}, "
            f"planned quantity {resolved['planned_qty']:,}. " if en else
            f"三項都已取得：產品 {product.get('canonical') or product.get('proposal')}、工單 {resolved['work_order']}、計畫數量 {resolved['planned_qty']:,}。")
    if extracted.get("qty_converted"):
        head += (f"The quantity was converted from words; ask the user to confirm it is {resolved['planned_qty']:,}. " if en
                 else f"數量是由文字換算而來，請使用者確認是 {resolved['planned_qty']:,} 台。")
    if align:
        head += (f"State the alignment: {align['message_en']} " if en else f"說明數量對齊：{align['message']}")
    if warns:
        head += ("Then clearly explain the ⚠ warnings and ask the user to tick \"I have checked\" on the screen, then press \"Upload\". " if en
                 else "接著明確說明 ⚠ 警告，請使用者確認無誤後勾選畫面上的「我已確認」，再按「確認上傳」。")
    else:
        head += ("There is no warning to confirm; tell the user to press \"Upload\" below. Do not mention a checkbox. " if en
                 else "目前沒有需要確認的警告，請他直接按畫面下方的「確認上傳」；不要提到勾選框。")
    return head + ("Finally, say any value can be changed by typing the new value in the chat." if en else "最後告訴他，要修改任何一項直接在對話中說新的值即可。")


def build_reply_messages(facts: dict, resolved: dict, issues: list, extracted: dict, messages: list[dict], lang: str) -> list[dict]:
    step = next_step(resolved)
    icon = {"error": "❌", "warn": "⚠", "info": "ℹ"}
    # "please enter …" errors for the value the step is about to ask for would read as scolding
    hidden = {"product": ("product_empty",), "work_order": ("wo_empty",), "planned_qty": ("qty_empty",)}.get(step, ())
    shown = [i for i in issues if i.get("reason") not in hidden]
    en = lang == "en"
    checks = "\n".join(f"- {icon.get(i['level'], '')} {i['message_en'] if en else i['message']}" for i in shown) or ("- (none)" if en else "- （目前沒有）")
    product = resolved["product"]
    pm = product.get("canonical") or product.get("proposal") or ("not given" if en else "尚未提供")
    qty = resolved.get("planned_qty")
    if en:
        context = (f"\n\n[Batch facts] {_facts_summary(facts, 'en')}\n"
                   f"[Values so far] product: {pm}; work order: {resolved.get('work_order') or 'not given'}; "
                   f"planned quantity: {f'{qty:,} units' if isinstance(qty, int) else 'not given'}\n"
                   f"[System checks]\n{checks}\n"
                   f"[Your task this turn] {_step_instruction(step, resolved, issues, extracted, 'en')}")
    else:
        context = (f"\n\n【這批資料】{_facts_summary(facts, 'zh')}\n"
                   f"【目前取得的值】產品：{pm}；工單：{resolved.get('work_order') or '尚未提供'}；"
                   f"計畫數量：{f'{qty:,} 台' if isinstance(qty, int) else '尚未提供'}\n"
                   f"【系統檢查結果】\n{checks}\n"
                   f"【你這一輪要做的事】{_step_instruction(step, resolved, issues, extracted, 'zh')}")
    return [{"role": "system", "content": REPLY_SYSTEM[lang] + context}] + messages[-CONTEXT_MESSAGES:]


def guard_reply(reply: str, resolved: dict, issues: list) -> bool:
    """False when the reply names a product or work order the server does not know this turn."""
    product = resolved["product"]
    allowed = {products.match_key(x) for x in (product.get("canonical"), product.get("proposal"), product.get("input"), EXAMPLE_PRODUCT) if x}
    for i in issues:
        for s in i.get("suggestions") or []:
            allowed.add(products.match_key(s))
        for s in i.get("same_tn") or []:
            allowed.add(products.match_key(s))
    for token in products.TOKEN_RE.findall(reply):
        if products.match_key(token) not in allowed:
            return False
    allowed_wo = {w for w in (resolved.get("work_order"), rules.normalize_wo(resolved.get("wo_input") or ""), EXAMPLE_WO) if w}
    for wo in rules.find_work_orders(reply):
        if wo not in allowed_wo:
            return False
    return True


def fallback_reply(resolved: dict, issues: list, lang: str = "zh") -> str:
    """Deterministic reply (template) used when the LLM fails or its reply is rejected."""
    step = next_step(resolved)
    en = lang == "en"
    errors = [i for i in issues if i["level"] == "error" and i.get("reason") not in ("product_empty", "wo_empty", "qty_empty")]
    lead = ""
    if errors:
        first = errors[0]
        lead = (first["message_en"] if en else first["message"]) + " "
    if step == "confirm":
        warns = [i for i in issues if i["level"] == "warn"]
        tail = (" Please tick \"I have checked\" below, then press \"Upload\"." if en else "請勾選下方「我已確認」後按「確認上傳」。") if warns else ""
        return question("confirm", resolved, lang) + tail
    return lead + question(step, resolved, lang)
