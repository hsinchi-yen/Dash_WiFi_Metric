"""Failure taxonomy (5F): the categories a FAIL test run is classified into.

Seeded from the draft in Final_Improvement_Plan §2.9 (checked against the 223 FAIL runs of the production
copy); RD renames, merges, adds or disables codes later (5F-2). Seeding only inserts codes that are missing,
so RD's changes are never overwritten. A record with several signals keeps the most severe one as its
category and the others as `secondary` (this replaces the draft's MULTI code).

STAGES are the "presumed stage" RD confirms per record (Q14 + Q32): the rules never set it.
RD edits the taxonomy in the advanced-analysis window: new codes, names / descriptions / actions, disable, and
merge (every run of one code moves to another; the rules map the old code to the new one from then on).
"""
import re
from typing import Any, Optional

import db

STAGES = {
    "board": ("板子", "Board"), "antenna": ("天線", "Antenna"), "fixture": ("夾具", "Fixture"),
    "ap": ("AP 環境", "AP / environment"), "firmware": ("韌體", "Firmware"), "station": ("測試站", "Test station"),
    "unknown": ("未知", "Unknown"),
}

# code, severity, sort, likely stage, (name zh, en), (description zh, en), (action zh, en)
SEED = [
    ("BT_DEV_NOT_FOUND", 1, 70, "board", ("找不到 BT 裝置", "BT device not found"),
     ("hci0 不存在或 Bluetooth 初始化失敗。", "No hci0 interface, or Bluetooth failed to initialise."),
     ("板子 BT 模組或韌體問題，交 RD。", "Bluetooth module or firmware on the board: hand it to RD.")),
    ("WIFI_ASSOC_FAIL", 2, 40, "ap", ("連不上 AP", "Cannot connect to the AP"),
     ("WiFi 連線或取得 IP 失敗，沒有進行吞吐量測試。", "WiFi association or DHCP failed; no throughput test ran."),
     ("檢查 AP 與 SSID 設定；只有單台出現則可能是板子。", "Check the AP and SSID settings; a single unit only points to the board.")),
    ("WIFI_TP_LOW_24G", 3, 10, "antenna", ("2.4G 吞吐量低於門檻", "2.4G throughput below threshold"),
     ("有連線、有量到，但 2.4G 每次嘗試都低於門檻。", "Connected and measured, but every 2.4G attempt was below the threshold."),
     ("重測一次；重測仍 FAIL 交 RD 檢查天線 / 射頻匹配。同站連續出現先查 AP。",
      "Retest once; still failing → RD (antenna / RF matching). Several in a row on one station: check the AP first.")),
    ("WIFI_TP_LOW_5G", 3, 20, "antenna", ("5G 吞吐量低於門檻", "5G throughput below threshold"),
     ("有連線、有量到，但 5G 每次嘗試都低於門檻。", "Connected and measured, but every 5G attempt was below the threshold."),
     ("重測一次；重測仍 FAIL 交 RD 檢查天線 / 射頻匹配。同站連續出現先查 AP。",
      "Retest once; still failing → RD (antenna / RF matching). Several in a row on one station: check the AP first.")),
    ("BT_PING_FAIL", 4, 60, "antenna", ("BT ping 全部失敗", "BT ping failed"),
     ("有 BT 裝置，但 l2ping 每次都失敗（例如 Host is down）。", "Bluetooth is up but every l2ping attempt failed (for example Host is down)."),
     ("檢查對端 BT 參考裝置；只有單台出現則查板子 BT 天線。", "Check the reference Bluetooth device; a single unit only: the board's BT antenna.")),
    ("WIFI_NO_TP_DATA", 5, 30, "station", ("iperf 沒有有效資料", "No valid iperf data"),
     ("已連線，但 iperf 量不到有效的吞吐量資料。", "Connected, but iperf returned no valid throughput data."),
     ("檢查測試站的 iperf 伺服器與 AP；同站連續出現屬站別問題。", "Check the station's iperf server and AP; repeated on one station = a station problem.")),
    ("LOG_INCOMPLETE", 6, 80, "station", ("log 不完整 / 測試中斷", "Log incomplete / test interrupted"),
     ("log 太短、沒有結果段，或測試中途重開機。", "The log is too short, has no result section, or the board restarted during the test."),
     ("測試站當機、拔線或板子重開機；重測。", "Station hang, cable pulled or board restart: retest.")),
    ("UNKNOWN", 9, 90, "unknown", ("未分類", "Unclassified"),
     ("規則沒有命中；由 AI 歸類或交 RD 確認。", "No rule matched; the AI classifies it or RD decides."),
     ("交 RD 確認。", "Hand it to RD.")),
]


def seed(cur) -> int:
    n = 0
    for code, severity, sort, stage, name, desc, action in SEED:
        cur.execute("""
            INSERT INTO fail_taxonomy (code, name_zh, name_en, description_zh, description_en, severity, sort,
                                       likely_stage, action_zh, action_en, updated_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'seed')
            ON CONFLICT (code) DO NOTHING
        """, (code, name[0], name[1], desc[0], desc[1], severity, sort, stage, action[0], action[1]))
        n += cur.rowcount
    return n


def listing() -> list[dict[str, Any]]:
    rows = db.rows("""SELECT x.*, (SELECT COUNT(*) FROM fail_analysis f WHERE f.category = x.code) AS runs
                      FROM fail_taxonomy x ORDER BY x.sort, x.code""")
    for r in rows:
        r["runs"] = int(r["runs"])
    return rows


def names() -> dict[str, dict]:
    return {r["code"]: r for r in db.rows("SELECT * FROM fail_taxonomy")}


# ─── RD maintains the taxonomy (5F-2): add, rename / edit, disable, merge ───

CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,31}$")
TEXT_FIELDS = {"name_zh": 60, "name_en": 80, "description_zh": 300, "description_en": 400, "action_zh": 200, "action_en": 300}


def check_fields(values: dict, creating: bool) -> Optional[str]:
    """None when the fields are valid, else an error code (pure)."""
    if creating:
        if not CODE_RE.match(str(values.get("code") or "")):
            return "bad_code"
        if not (values.get("name_zh") or "").strip() or not (values.get("name_en") or "").strip():
            return "name_required"
    for k, limit in TEXT_FIELDS.items():
        v = values.get(k)
        if v is not None and (not isinstance(v, str) or len(v) > limit or (k.startswith("name_") and not v.strip())):
            return f"bad_{k}"
    if values.get("likely_stage") is not None and values["likely_stage"] not in STAGES:
        return "bad_stage"
    for k, lo, hi in (("severity", 1, 9), ("sort", 0, 999)):
        v = values.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi):
            return f"bad_{k}"
    if values.get("enabled") is not None and not isinstance(values["enabled"], bool):
        return "bad_enabled"
    return None


def merge(conn, code: str, into: str, user: str) -> int:
    """Every run classified as `code` becomes `into` (RD's decision: kept by the rules), `code` is disabled and the
    rules map it to `into` from now on. Returns the runs moved."""
    n = db.execute(conn, """UPDATE fail_analysis SET category = %s, classifier = 'rd',
                                   rd_status = CASE WHEN rd_status = 'new' THEN 'reclassified' ELSE rd_status END,
                                   rd_by = %s, rd_at = NOW(), updated_at = NOW() WHERE category = %s""", (into, user, code))
    db.execute(conn, "UPDATE fail_analysis SET secondary = array_replace(secondary, %s, %s) WHERE %s = ANY(secondary)", (code, into, code))
    db.execute(conn, "UPDATE fail_analysis SET secondary = ARRAY(SELECT DISTINCT unnest(secondary)) WHERE %s = ANY(secondary)", (into,))
    db.execute(conn, "UPDATE fail_analysis SET secondary = array_remove(secondary, category) WHERE category = ANY(secondary)")
    db.execute(conn, """UPDATE fail_taxonomy SET enabled = FALSE, merged_into = %s, updated_by = %s, updated_at = NOW()
                        WHERE code = %s""", (into, user, code))
    db.execute(conn, "UPDATE fail_taxonomy SET merged_into = %s WHERE merged_into = %s", (into, code))   # chains point at the end
    return n


def merged_map() -> dict[str, str]:
    return {r["code"]: r["merged_into"] for r in db.rows("SELECT code, merged_into FROM fail_taxonomy WHERE merged_into IS NOT NULL")}
