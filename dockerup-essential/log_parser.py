"""
WiFi & BT Test Log Parser
Parses: YYYYMMDD_HHMMSS_SN_MAC1_MAC2_RESULT.txt
Extracts: 2.4G/5G iperf KPIs + BT l2ping metrics + RSSI before/after
"""

import re
import os
import hashlib
import numpy as np
from datetime import datetime
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# ─── Regex patterns ────────────────────────────────────────────────────────────
THROUGHPUT_RE = re.compile(r"\[SUM\].*?(\d+(?:\.\d+)?)\s+Mbits/sec")
RSSI_RE       = re.compile(r"RSSI[:\s]+(-\d+)\s*dBm")
RESULT_LINE   = re.compile(r"Result:\s+(PASSED|FAILED).*?(\d+(?:\.\d+)?)\s*MBits/sec.*?RSSI after test:\s*(-\d+)\s*dBm", re.IGNORECASE)
BT_LATENCY_RE = re.compile(r"time\s+(\d+(?:\.\d+)?)ms")
BT_LOSS_RE    = re.compile(r"(\d+)\s+sent,\s+(\d+)\s+received,\s+(\d+)%\s+loss")
BAND_2G_RE    = re.compile(r"(2\.4G|2G|bgn|24g)", re.IGNORECASE)
BAND_5G_RE    = re.compile(r"(5G|ax|5GHz|wifi5g)", re.IGNORECASE)
ATTEMPT_RE    = re.compile(r"Attempt\s+(\d+)/(\d+)")

# ─── Filename parser ───────────────────────────────────────────────────────────
FILENAME_RE = re.compile(
    r"(\d{8})_(\d{6})_(\w+)_([0-9A-Fa-f]+)_([0-9A-Fa-f]+)_(PASS|FAIL)\.txt",
    re.IGNORECASE
)

def parse_filename(filename: str) -> Optional[dict]:
    """
    Extract metadata from filename.
    Pattern: 20260203_082710_513126033667_001F7B6C11E4_001F7B6C11E5_PASS.txt
    """
    m = FILENAME_RE.match(Path(filename).name)
    if not m:
        logger.warning(f"Filename does not match pattern: {filename}")
        return None
    date_str, time_str, sn, mac1, mac2, result = m.groups()
    dt = datetime.strptime(date_str + time_str, "%Y%m%d%H%M%S")
    return {
        "test_time": dt,
        "sn": sn,
        "wifi_mac": mac1.upper(),
        "bt_mac": mac2.upper(),
        "result": result.upper() == "PASS",
    }

def extract_path_meta(filepath: str) -> dict:
    """
    Extract product_model and work_order from directory structure.

    Supported layouts:
    1) .../<product_model>/<work_order>/<filename.txt>
    2) .../<product_model>/<filename.txt>   (work_order unavailable)
    """
    parts = Path(filepath).parts
    meta = {"product_model": None, "work_order": None}
    if len(parts) < 2:
        return meta

    parent = parts[-2]
    # Typical work-order format: 5101-260108007 (or similar numeric token + dash)
    is_work_order = bool(re.match(r"^\d{3,6}-\d{5,}$", parent))

    if is_work_order and len(parts) >= 3:
        meta["work_order"] = parent
        meta["product_model"] = parts[-3]
    else:
        # Fallback: files directly under product-model folder.
        meta["product_model"] = parent
    return meta

# ─── Log section detector ──────────────────────────────────────────────────────
class BandSection:
    def __init__(self):
        self.band: Optional[str] = None
        self.throughputs: list[float] = []
        self.rssi_before: Optional[float] = None
        self.rssi_after:  Optional[float] = None
        self.attempt_count: int = 1
        self.passed: Optional[bool] = None
        self._in_iperf: bool = False

# ─── Core log parser ──────────────────────────────────────────────────────────
WARMUP_SKIP_SECONDS = 15  # skip first N seconds of iperf (connection ramp-up)

def parse_log(content: str) -> dict:
    """
    Parse log content into structured KPI dict.
    Handles: 2.4G iperf, 5G iperf, BT l2ping sections.
    """
    lines = content.splitlines()
    
    data = {
        "2.4G": BandSection(),
        "5G":   BandSection(),
    }
    current_band: Optional[str] = None
    throughput_lines_seen = {"2.4G": 0, "5G": 0}
    result_lines_seen = {"2.4G": 0, "5G": 0}
    
    # BT metrics
    bt_latencies: list[float] = []
    bt_loss_rate: Optional[float] = None
    bt_result: Optional[bool] = None
    bt_result_line_seen = False
    
    # RSSI global (first seen = before test)
    global_rssi_before: Optional[float] = None
    in_bt_section = False
    
    for line in lines:
        # ── Band detection ──────────────────────────────────────────────────
        if "Starting 2.4G Band Test" in line or "Testing 2.4G" in line:
            current_band = "2.4G"
            in_bt_section = False
        elif "Starting 5G Band Test" in line or "Testing 5G" in line or "Test Summary for 5G" in line:
            current_band = "5G"
            in_bt_section = False
        elif "bt_ping" in line.lower() or "l2ping" in line.lower() or "Bluetooth" in line:
            in_bt_section = True
            current_band = None
        
        # ── RSSI capture ────────────────────────────────────────────────────
        rssi_m = RSSI_RE.search(line)
        if rssi_m:
            val = float(rssi_m.group(1))
            if current_band:
                sec = data[current_band]
                if sec.rssi_before is None:
                    sec.rssi_before = val
                else:
                    sec.rssi_after = val
        
        # ── Attempt count ───────────────────────────────────────────────────
        att_m = ATTEMPT_RE.search(line)
        if att_m and current_band:
            data[current_band].attempt_count = int(att_m.group(1))
        
        # ── Result line (band level) ────────────────────────────────────────
        res_m = RESULT_LINE.search(line)
        if res_m and current_band:
            result_lines_seen[current_band] += 1
            data[current_band].passed    = res_m.group(1).upper() == "PASSED"
            data[current_band].rssi_after = float(res_m.group(3))
        
        # ── Iperf throughput ────────────────────────────────────────────────
        tp_m = THROUGHPUT_RE.search(line)
        if tp_m and current_band and not in_bt_section:
            throughput_lines_seen[current_band] += 1
            # detect interval start time to skip warmup
            interval_m = re.search(r"\[\s*SUM\]\s+([\d.]+)-([\d.]+)\s+sec", line)
            if interval_m:
                t_start = float(interval_m.group(1))
                if t_start >= WARMUP_SKIP_SECONDS:
                    data[current_band].throughputs.append(float(tp_m.group(1)))
            else:
                # summary line (0.00-120.xx) — use separately, skip from per-second
                pass
        
        # ── BT latency ──────────────────────────────────────────────────────
        if in_bt_section:
            bt_m = BT_LATENCY_RE.search(line)
            if bt_m:
                bt_latencies.append(float(bt_m.group(1)))
            loss_m = BT_LOSS_RE.search(line)
            if loss_m:
                bt_loss_rate = float(loss_m.group(3))
            if "BT Test Result" in line or "Bluetooth Test Result" in line:
                bt_result_line_seen = True
                bt_result = "PASS" in line.upper()
    
    # ── KPI calculation ─────────────────────────────────────────────────────
    def calc_kpi(arr: list[float]) -> dict:
        if not arr:
            return {"avg": None, "min": None, "max": None, "std": None, "count": 0}
        a = np.array(arr)
        return {
            "avg":   round(float(np.mean(a)), 2),
            "min":   round(float(np.min(a)), 2),
            "max":   round(float(np.max(a)), 2),
            "std":   round(float(np.std(a)), 2),
            "count": len(a),
        }
    
    kpi_24g = calc_kpi(data["2.4G"].throughputs)
    kpi_5g  = calc_kpi(data["5G"].throughputs)
    
    bt_kpi = {}
    if bt_latencies:
        a = np.array(bt_latencies)
        bt_kpi = {
            "bt_avg_latency": round(float(np.mean(a)), 2),
            "bt_min_latency": round(float(np.min(a)), 2),
            "bt_max_latency": round(float(np.max(a)), 2),
            "bt_loss_rate":   bt_loss_rate if bt_loss_rate is not None else 0.0,
            "bt_result":      bt_result,
        }
    
    rssi_24g = data["2.4G"]
    rssi_5g  = data["5G"]
    
    return {
        # 2.4G
        "avg_24g":          kpi_24g["avg"],
        "min_24g":          kpi_24g["min"],
        "max_24g":          kpi_24g["max"],
        "std_24g":          kpi_24g["std"],
        "samples_24g":      kpi_24g["count"],
        "rssi_before_24g":  rssi_24g.rssi_before,
        "rssi_after_24g":   rssi_24g.rssi_after,
        "rssi_delta_24g":   round(rssi_24g.rssi_after - rssi_24g.rssi_before, 1)
                            if rssi_24g.rssi_before is not None and rssi_24g.rssi_after is not None else None,
        "attempt_count_24g": rssi_24g.attempt_count,
        "band_result_24g":  rssi_24g.passed,
        # 5G
        "avg_5g":           kpi_5g["avg"],
        "min_5g":           kpi_5g["min"],
        "max_5g":           kpi_5g["max"],
        "std_5g":           kpi_5g["std"],
        "samples_5g":       kpi_5g["count"],
        "rssi_before_5g":   rssi_5g.rssi_before,
        "rssi_after_5g":    rssi_5g.rssi_after,
        "rssi_delta_5g":    round(rssi_5g.rssi_after - rssi_5g.rssi_before, 1)
                            if rssi_5g.rssi_before is not None and rssi_5g.rssi_after is not None else None,
        "attempt_count_5g": rssi_5g.attempt_count,
        "band_result_5g":   rssi_5g.passed,
        # BT
        **bt_kpi,
        # Parse observability
        "warn_missing_tp_24g": 1 if throughput_lines_seen["2.4G"] == 0 else 0,
        "warn_missing_tp_5g":  1 if throughput_lines_seen["5G"] == 0 else 0,
        "warn_missing_result_24g": 1 if result_lines_seen["2.4G"] == 0 else 0,
        "warn_missing_result_5g":  1 if result_lines_seen["5G"] == 0 else 0,
        "warn_missing_bt_result":  1 if (bt_latencies and not bt_result_line_seen) else 0,
    }

# ─── File ingestion ────────────────────────────────────────────────────────────
def file_hash(filepath: str) -> str:
    """SHA256 of file content for duplicate detection."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _sanitize_text_for_postgres(text: str) -> tuple[str, int]:
    """PostgreSQL text columns cannot store NUL bytes."""
    nul_count = text.count("\x00")
    if nul_count == 0:
        return text, 0
    return text.replace("\x00", ""), nul_count

def ingest_file(filepath: str) -> Optional[dict]:
    """
    Full pipeline: path → filename meta → log parse → unified record.
    Returns None if filename doesn't match expected pattern.
    """
    fn_meta = parse_filename(filepath)
    if not fn_meta:
        return None
    
    path_meta = extract_path_meta(filepath)
    
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        logger.error(f"Cannot read {filepath}: {e}")
        return None

    content, sanitized_nul_bytes = _sanitize_text_for_postgres(content)
    
    log_kpis = parse_log(content)
    
    record = {
        **fn_meta,
        **path_meta,
        **log_kpis,
        "raw_log":      content,
        "sanitized_nul_bytes": sanitized_nul_bytes,
        "file_hash":    file_hash(filepath),
        "source_file":  os.path.basename(filepath),
    }
    return record

def scan_directory(root: str, extensions=(".txt",)) -> list[str]:
    """Recursively find all log files under root."""
    found = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.lower().endswith(extensions):
                if FILENAME_RE.match(f):
                    found.append(os.path.join(dirpath, f))
    return sorted(found)

# ─── DB writer (psycopg2) ──────────────────────────────────────────────────────
DB_INSERT = """
INSERT INTO test_record (
    product_model, work_order, sn, wifi_mac, bt_mac,
    test_time, result,
    avg_24g, min_24g, max_24g, std_24g, samples_24g,
    rssi_before_24g, rssi_after_24g, rssi_delta_24g,
    attempt_count_24g, band_result_24g,
    avg_5g, min_5g, max_5g, std_5g, samples_5g,
    rssi_before_5g, rssi_after_5g, rssi_delta_5g,
    attempt_count_5g, band_result_5g,
    bt_avg_latency, bt_min_latency, bt_max_latency,
    bt_loss_rate, bt_result,
    raw_log, file_hash, source_file
)
VALUES (
    %(product_model)s, %(work_order)s, %(sn)s, %(wifi_mac)s, %(bt_mac)s,
    %(test_time)s, %(result)s,
    %(avg_24g)s, %(min_24g)s, %(max_24g)s, %(std_24g)s, %(samples_24g)s,
    %(rssi_before_24g)s, %(rssi_after_24g)s, %(rssi_delta_24g)s,
    %(attempt_count_24g)s, %(band_result_24g)s,
    %(avg_5g)s, %(min_5g)s, %(max_5g)s, %(std_5g)s, %(samples_5g)s,
    %(rssi_before_5g)s, %(rssi_after_5g)s, %(rssi_delta_5g)s,
    %(attempt_count_5g)s, %(band_result_5g)s,
    %(bt_avg_latency)s, %(bt_min_latency)s, %(bt_max_latency)s,
    %(bt_loss_rate)s, %(bt_result)s,
    %(raw_log)s, %(file_hash)s, %(source_file)s
)
ON CONFLICT (file_hash) DO NOTHING
RETURNING id;
"""

DB_UPSERT = """
INSERT INTO test_record (
    product_model, work_order, sn, wifi_mac, bt_mac,
    test_time, result,
    avg_24g, min_24g, max_24g, std_24g, samples_24g,
    rssi_before_24g, rssi_after_24g, rssi_delta_24g,
    attempt_count_24g, band_result_24g,
    avg_5g, min_5g, max_5g, std_5g, samples_5g,
    rssi_before_5g, rssi_after_5g, rssi_delta_5g,
    attempt_count_5g, band_result_5g,
    bt_avg_latency, bt_min_latency, bt_max_latency,
    bt_loss_rate, bt_result,
    raw_log, file_hash, source_file
)
VALUES (
    %(product_model)s, %(work_order)s, %(sn)s, %(wifi_mac)s, %(bt_mac)s,
    %(test_time)s, %(result)s,
    %(avg_24g)s, %(min_24g)s, %(max_24g)s, %(std_24g)s, %(samples_24g)s,
    %(rssi_before_24g)s, %(rssi_after_24g)s, %(rssi_delta_24g)s,
    %(attempt_count_24g)s, %(band_result_24g)s,
    %(avg_5g)s, %(min_5g)s, %(max_5g)s, %(std_5g)s, %(samples_5g)s,
    %(rssi_before_5g)s, %(rssi_after_5g)s, %(rssi_delta_5g)s,
    %(attempt_count_5g)s, %(band_result_5g)s,
    %(bt_avg_latency)s, %(bt_min_latency)s, %(bt_max_latency)s,
    %(bt_loss_rate)s, %(bt_result)s,
    %(raw_log)s, %(file_hash)s, %(source_file)s
)
ON CONFLICT (file_hash) DO UPDATE SET
    product_model = EXCLUDED.product_model,
    work_order = EXCLUDED.work_order,
    sn = EXCLUDED.sn,
    wifi_mac = EXCLUDED.wifi_mac,
    bt_mac = EXCLUDED.bt_mac,
    test_time = EXCLUDED.test_time,
    result = EXCLUDED.result,
    avg_24g = EXCLUDED.avg_24g,
    min_24g = EXCLUDED.min_24g,
    max_24g = EXCLUDED.max_24g,
    std_24g = EXCLUDED.std_24g,
    samples_24g = EXCLUDED.samples_24g,
    rssi_before_24g = EXCLUDED.rssi_before_24g,
    rssi_after_24g = EXCLUDED.rssi_after_24g,
    rssi_delta_24g = EXCLUDED.rssi_delta_24g,
    attempt_count_24g = EXCLUDED.attempt_count_24g,
    band_result_24g = EXCLUDED.band_result_24g,
    avg_5g = EXCLUDED.avg_5g,
    min_5g = EXCLUDED.min_5g,
    max_5g = EXCLUDED.max_5g,
    std_5g = EXCLUDED.std_5g,
    samples_5g = EXCLUDED.samples_5g,
    rssi_before_5g = EXCLUDED.rssi_before_5g,
    rssi_after_5g = EXCLUDED.rssi_after_5g,
    rssi_delta_5g = EXCLUDED.rssi_delta_5g,
    attempt_count_5g = EXCLUDED.attempt_count_5g,
    band_result_5g = EXCLUDED.band_result_5g,
    bt_avg_latency = EXCLUDED.bt_avg_latency,
    bt_min_latency = EXCLUDED.bt_min_latency,
    bt_max_latency = EXCLUDED.bt_max_latency,
    bt_loss_rate = EXCLUDED.bt_loss_rate,
    bt_result = EXCLUDED.bt_result,
    raw_log = EXCLUDED.raw_log,
    source_file = EXCLUDED.source_file,
    created_at = NOW()
RETURNING id;
"""

def write_record(conn, record: dict) -> Optional[int]:
    """Insert one record. Returns new row id, or None if duplicate."""
    # Fill missing BT keys with None
    for k in ["bt_avg_latency","bt_min_latency","bt_max_latency","bt_loss_rate","bt_result"]:
        record.setdefault(k, None)
    
    with conn.cursor() as cur:
        cur.execute(DB_INSERT, record)
        row = cur.fetchone()
        conn.commit()
        return row[0] if row else None


def upsert_record(conn, record: dict) -> int:
    """Insert or overwrite one record by file_hash. Returns row id."""
    for k in ["bt_avg_latency","bt_min_latency","bt_max_latency","bt_loss_rate","bt_result"]:
        record.setdefault(k, None)

    with conn.cursor() as cur:
        cur.execute(DB_UPSERT, record)
        row = cur.fetchone()
        conn.commit()
        return int(row[0]) if row else 0

# ─── CLI batch runner ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse, sys
    
    parser = argparse.ArgumentParser(description="Ingest WiFi test logs into PostgreSQL")
    parser.add_argument("path", help="File or directory to ingest")
    parser.add_argument("--dsn", default="postgresql://user:pass@localhost/wifitest", help="PostgreSQL DSN")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, do not write to DB")
    args = parser.parse_args()
    
    p = Path(args.path)
    files = scan_directory(str(p)) if p.is_dir() else [str(p)]
    
    if not files:
        print("No matching log files found.")
        sys.exit(0)
    
    print(f"Found {len(files)} log files")
    
    ok = skip = err = 0
    warning_totals = {
        "warn_missing_tp_24g": 0,
        "warn_missing_tp_5g": 0,
        "warn_missing_result_24g": 0,
        "warn_missing_result_5g": 0,
        "warn_missing_bt_result": 0,
    }
    for f in files:
        record = ingest_file(f)
        if not record:
            err += 1
            print(f"  [SKIP] {os.path.basename(f)} — filename pattern mismatch")
            continue

        for key in warning_totals:
            warning_totals[key] += int(record.get(key, 0) or 0)
        
        if args.dry_run:
            print(f"  [DRY]  {record['sn']} | 2.4G avg={record['avg_24g']} | 5G avg={record['avg_5g']} | BT={record.get('bt_avg_latency')}ms | result={record['result']}")
            ok += 1
        else:
            try:
                import psycopg2
                with psycopg2.connect(args.dsn) as conn:
                    rid = write_record(conn, record)
                    if rid:
                        print(f"  [OK]   {record['sn']} → row {rid}")
                        ok += 1
                    else:
                        print(f"  [DUP]  {record['sn']} already exists")
                        skip += 1
            except Exception as e:
                print(f"  [ERR]  {record['sn']}: {e}")
                err += 1
    
    print(f"\nDone: {ok} inserted, {skip} duplicates, {err} errors")
    print(
        "Warnings: "
        f"missing_tp_24g={warning_totals['warn_missing_tp_24g']}, "
        f"missing_tp_5g={warning_totals['warn_missing_tp_5g']}, "
        f"missing_result_24g={warning_totals['warn_missing_result_24g']}, "
        f"missing_result_5g={warning_totals['warn_missing_result_5g']}, "
        f"missing_bt_result={warning_totals['warn_missing_bt_result']}"
    )
