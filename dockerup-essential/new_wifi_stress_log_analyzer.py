#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# new_wifi_stress_log_analyzer.py
#
# Redesigned version — cyber dark UI + Advanced Analysis tab.
# All parsing / CSV-output / DB-upload logic is identical to the original.
# Run this file independently; the original wifi_stress_log_analyzer.py is
# left completely untouched.

import csv
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import psycopg2
    import log_parser
    DB_UPLOAD_AVAILABLE = True
except ImportError:
    DB_UPLOAD_AVAILABLE = False

APP_VERSION = "2026.04.29"
DB_DUPLICATE_POLICY = "skip"
DB_DSN_TEMPLATE = "postgresql://qc:qcpass@{host}/wifitest"
DB_CONNECT_TIMEOUT_SECONDS = 3
DB_HEARTBEAT_INTERVAL_MS = 30_000

APP_WINDOW_TITLE = "WiFi Stress Log Analyzer - Designed by TechNexion"
APP_HEADER_TITLE = "WiFi Stress Log Analyzer"

APP_DIR = os.path.dirname(os.path.abspath(__file__))
HOME_DIR = os.path.expanduser("~")
_documents_dir = os.path.join(HOME_DIR, "Documents")
DEFAULT_DIR = _documents_dir if os.path.isdir(_documents_dir) else HOME_DIR

# ── Cyber Tech Color Palette ───────────────────────────────────────────────
C_BG      = "#07080a"
C_SURFACE = "#111318"
C_SURF2   = "#1a1c23"
C_SURF3   = "#252830"
C_TEXT    = "#e2e8f0"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#475569"
C_BLUE    = "#00e5ff"
C_GREEN   = "#10b981"
C_RED     = "#f43f5e"
C_ORANGE  = "#f59e0b"
C_PURPLE  = "#8b5cf6"
C_BORDER  = "#334155"

# ══════════════════════════════════════════════════════════════════════════
# Core logic — identical to wifi_stress_log_analyzer.py
# ══════════════════════════════════════════════════════════════════════════

_FILENAME_V1_RE = re.compile(
    r"^(?P<date>\d{8})_(?P<time>\d{6})_(?P<sn>[^_]+)_(?P<mac>[^_]+)_(?P<result>[A-Za-z]+)\.txt$"
)
_FILENAME_V2_RE = re.compile(
    r"^(?P<date>\d{8})_(?P<time>\d{6})_(?P<sn>[^_]+)_(?P<mac1>[^_]+)_(?P<mac2>[^_]+)_(?P<result>[A-Za-z]+)\.txt$"
)


@dataclass(frozen=True)
class LogRecord:
    dt: datetime
    test_date: str
    test_time: str
    mac: str
    serial: str
    result: str
    filename: str


def parse_log_directory_raw(log_dir: str) -> List[LogRecord]:
    records: List[LogRecord] = []
    if not log_dir or not os.path.isdir(log_dir):
        return records
    for name in os.listdir(log_dir):
        if not name.lower().endswith(".txt"):
            continue
        if _is_excluded_by_name(name):
            continue
        rec = _try_parse_record_from_filename(name)
        if rec is None:
            continue
        records.append(rec)
    records.sort(key=lambda r: r.dt)
    return records


def _dedupe_keep_latest_by_sn(records: List[LogRecord]) -> List[LogRecord]:
    latest_by_sn: dict[str, LogRecord] = {}
    for r in records:
        sn_key = r.mac
        prev = latest_by_sn.get(sn_key)
        if prev is None or r.dt > prev.dt:
            latest_by_sn[sn_key] = r
    deduped = list(latest_by_sn.values())
    deduped.sort(key=lambda r: r.dt)
    return deduped


def _is_excluded_by_name(filename: str) -> bool:
    return "DUMMY" in filename.upper()


def _try_parse_record_from_filename(filename: str) -> Optional[LogRecord]:
    m2 = _FILENAME_V2_RE.match(filename)
    m1 = _FILENAME_V1_RE.match(filename) if m2 is None else None
    m = m2 or m1
    if not m:
        return None

    date_raw = m.group("date")
    time_raw = m.group("time")
    sn = m.group("sn")
    result = m.group("result").upper()
    if result == "TERNINATED":
        result = "TERMINATED"

    if m2 is not None:
        mac_field = f"{m.group('mac1')}_{m.group('mac2')}"
    else:
        mac_field = m.group("mac")

    if result not in {"PASS", "FAIL", "TERMINATED"}:
        return None

    try:
        dt = datetime.strptime(f"{date_raw}{time_raw}", "%Y%m%d%H%M%S")
    except ValueError:
        return None

    test_date = f"{date_raw[0:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
    test_time = f"{time_raw[0:2]}:{time_raw[2:4]}:{time_raw[4:6]}"

    return LogRecord(
        dt=dt,
        test_date=test_date,
        test_time=test_time,
        mac=sn,
        serial=mac_field,
        result=result,
        filename=filename,
    )


def parse_log_directory(log_dir: str) -> Tuple[List[LogRecord], int, int, int]:
    raw = parse_log_directory_raw(log_dir)
    deduped = _dedupe_keep_latest_by_sn(raw)
    pass_count = sum(1 for r in deduped if r.result == "PASS")
    fail_count = sum(1 for r in deduped if r.result == "FAIL")
    total = pass_count + fail_count
    return deduped, total, pass_count, fail_count


def _ratio_text(count: int, total: int) -> str:
    if total <= 0:
        return "0.0%"
    return f"{(count / total) * 100.0:.1f}%"


def _safe_name(text: str) -> str:
    text = (text or "").strip() or "UNKNOWN"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)


def _default_browse_dir() -> str:
    return DEFAULT_DIR


_SN_TOKEN_RE = re.compile(r"^(?P<prefix>\D*?)(?P<num>\d+)(?P<suffix>\D*)$")


def _try_parse_sn_token(sn_text: str) -> Optional[Tuple[str, int, int, str]]:
    sn_text = (sn_text or "").strip()
    if not sn_text:
        return None
    m = _SN_TOKEN_RE.match(sn_text)
    if not m:
        return None
    num_text = m.group("num")
    try:
        num = int(num_text)
    except ValueError:
        return None
    return m.group("prefix"), num, len(num_text), m.group("suffix")


def _format_missing_ranges(nums: List[int], prefix: str, width: int, suffix: str) -> List[str]:
    if not nums:
        return []
    nums_sorted = sorted(set(nums))
    ranges: List[Tuple[int, int]] = []
    start = prev = nums_sorted[0]
    for n in nums_sorted[1:]:
        if n == prev + 1:
            prev = n
            continue
        ranges.append((start, prev))
        start = prev = n
    ranges.append((start, prev))

    def fmt(n: int) -> str:
        return f"{prefix}{n:0{width}d}{suffix}" if width > 0 else f"{prefix}{n}{suffix}"

    out: List[str] = []
    for a, b in ranges:
        out.append(fmt(a) if a == b else f"{fmt(a)} ~ {fmt(b)}")
    return out


def _write_sn_sequence_check(
    out_dir: Path,
    pn_product_prefix: str,
    raw_source_dir: str,
    records: List[LogRecord],
) -> Optional[Path]:
    if not records:
        return None

    parsed: List[Tuple[datetime, str, str, int, int, str]] = []
    for r in records:
        tok = _try_parse_sn_token(r.mac)
        if tok is None:
            continue
        prefix, num, width, suffix = tok
        parsed.append((r.dt, r.mac, prefix, num, width, suffix))

    path = out_dir / f"{pn_product_prefix}sn_sequence_check.txt"
    report_time_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if len(parsed) < 2:
        with open(path, "w", encoding="utf-8") as f:
            f.write("SN Sequence Check\n")
            f.write(f"Report Time: {report_time_text}\n")
            f.write(f"Source Folder: {os.path.normpath(raw_source_dir)}\n\n")
            f.write("Not enough numeric SN records to analyze.\n")
        return path

    parsed.sort(key=lambda x: x[0])
    first_dt, first_sn_text, first_prefix, _, _, first_suffix = parsed[0]
    last_dt, last_sn_text = parsed[-1][0], parsed[-1][1]

    common_prefix = first_prefix if all(p[2] == first_prefix for p in parsed) else ""
    common_suffix = first_suffix if all(p[5] == first_suffix for p in parsed) else ""
    common_width = max((p[4] for p in parsed), default=0)

    nums_all = [p[3] for p in parsed]
    start_num = min(nums_all)
    end_num = max(nums_all)

    min_dt = min((p[0] for p in parsed if p[3] == start_num), default=first_dt)
    max_dt = min((p[0] for p in parsed if p[3] == end_num), default=last_dt)

    def _fmt_sn(n: int) -> str:
        return (
            f"{common_prefix}{n:0{common_width}d}{common_suffix}"
            if common_width > 0
            else f"{common_prefix}{n}{common_suffix}"
        )

    present_nums = {p[3] for p in parsed}
    expected_nums = list(range(start_num, end_num + 1))
    missing_nums = [n for n in expected_nums if n not in present_nums]

    with open(path, "w", encoding="utf-8") as f:
        f.write("SN Sequence Check\n")
        f.write(f"Report Time: {report_time_text}\n")
        f.write(f"Source Folder: {os.path.normpath(raw_source_dir)}\n\n")
        f.write(f"First Record: {first_dt.strftime('%Y-%m-%d %H:%M:%S')}  SN={first_sn_text}\n")
        f.write(f"Last Record : {last_dt.strftime('%Y-%m-%d %H:%M:%S')}  SN={last_sn_text}\n")
        f.write(f"Min SN (numeric): {min_dt.strftime('%Y-%m-%d %H:%M:%S')}  SN={_fmt_sn(start_num)}\n")
        f.write(f"Max SN (numeric): {max_dt.strftime('%Y-%m-%d %H:%M:%S')}  SN={_fmt_sn(end_num)}\n")
        f.write(f"Range (min~max numeric): {start_num} ~ {end_num}\n")
        if common_prefix or common_suffix or common_width:
            f.write(f"SN Format: prefix='{common_prefix}', width={common_width}, suffix='{common_suffix}'\n")
        f.write(f"Present SN count (numeric): {len(present_nums)}\n")
        f.write(f"Expected SN count: {len(expected_nums)}\n")
        f.write(f"Missing SN count: {len(missing_nums)}\n\n")

        if not missing_nums:
            f.write("SN為正常連續\n")
        else:
            f.write("Missing SN (by range):\n")
            for line in _format_missing_ranges(missing_nums, common_prefix, common_width, common_suffix):
                f.write(f"- {line}\n")
            f.write("\nMissing SN (all):\n")
            for n in missing_nums:
                f.write(
                    f"{common_prefix}{n:0{common_width}d}{common_suffix}\n"
                    if common_width > 0
                    else f"{common_prefix}{n}{common_suffix}\n"
                )
    return path


def _outfiles_dir_for(base_dir: str) -> Path:
    base = Path(base_dir) if base_dir else Path(DEFAULT_DIR)
    return base / "outfiles"


def _format_generated_files_message(
    out_dir: Path, files: List[Path], summary_text: str = "", max_items: int = 30
) -> str:
    out_dir_text = os.path.normpath(str(out_dir))
    summary_text = (summary_text or "").strip()

    if not files:
        base = f"Outputs generated in outfiles folder.\nOutput: {out_dir_text}"
        return f"{summary_text}\n\n{base}" if summary_text else base

    shown = sorted(files, key=lambda p: (p.suffix.lower(), p.name.lower()))[:max_items]
    lines: List[str] = []
    if summary_text:
        lines.extend([summary_text, ""])
    lines.extend(
        ["Outputs generated in outfiles folder.", f"Output: {out_dir_text}", "", "Generated/Updated files:"]
    )
    lines.extend([os.path.normpath(str(p)) for p in shown])
    if len(files) > max_items:
        lines.append(f"... and {len(files) - max_items} more")
    return "\n".join(lines)


def _write_sn_attempt_summary_csv(
    out_dir: Path,
    records: List[LogRecord],
    production_name: str,
    safe_product_name: str,
    ts: str,
) -> Path:
    path = out_dir / f"wifi_stress_sn_summary_{safe_product_name}_{ts}.csv"

    by_sn: dict[str, List[LogRecord]] = {}
    for r in records:
        by_sn.setdefault(r.mac, []).append(r)

    rows: List[Tuple[str, int, int, int, str, str]] = []
    for sn, items in by_sn.items():
        items_sorted = sorted(items, key=lambda x: x.dt)
        attempts = len(items_sorted)
        retest = max(0, attempts - 1)
        terminated = sum(1 for i in items_sorted if i.result == "TERMINATED")
        final_result = items_sorted[-1].result if items_sorted else "UNKNOWN"
        result_set = {i.result for i in items_sorted}
        if "PASS" in result_set:
            overall_result = "PASS"
        elif "FAIL" in result_set:
            overall_result = "FAIL"
        elif "TERMINATED" in result_set:
            overall_result = "TERMINATED"
        else:
            overall_result = "UNKNOWN"
        rows.append((sn, attempts, retest, terminated, overall_result, final_result))

    rows.sort(key=lambda x: x[0])

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Product", "SN", "Attempts", "Retest", "Terminated", "Result", "Final Result"])
        for sn, attempts, retest, terminated, overall_result, final_result in rows:
            w.writerow([production_name, sn, attempts, retest, terminated, overall_result, final_result])

    return path


# ══════════════════════════════════════════════════════════════════════════
# Advanced Analysis — NEW functions (not in original)
# ══════════════════════════════════════════════════════════════════════════

def compute_advanced_analysis(records: List[LogRecord]) -> dict:
    """
    Compute throughput and production-time analytics.

    Cycle-time definition: wall-clock gap between consecutive test completions,
    sorted by datetime.  Gaps > 7200 s (2 h) are excluded as shift breaks so
    they don't inflate the average.
    """
    if not records:
        return {}

    sorted_recs = sorted(records, key=lambda r: r.dt)
    total = len(sorted_recs)

    batch_start = sorted_recs[0].dt
    batch_end   = sorted_recs[-1].dt
    batch_duration_s = (batch_end - batch_start).total_seconds() if total >= 2 else 0.0

    gaps_s: List[float] = []
    for i in range(1, len(sorted_recs)):
        gap = (sorted_recs[i].dt - sorted_recs[i - 1].dt).total_seconds()
        if 0 < gap <= 7200:
            gaps_s.append(gap)

    avg_cycle_s = sum(gaps_s) / len(gaps_s) if gaps_s else 0.0
    min_cycle_s = min(gaps_s) if gaps_s else 0.0
    max_cycle_s = max(gaps_s) if gaps_s else 0.0

    hourly: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"pass": 0, "fail": 0, "term": 0, "total": 0}
    )
    for r in sorted_recs:
        h = r.dt.strftime("%Y-%m-%d %H:00")
        hourly[h]["total"] += 1
        if r.result == "PASS":
            hourly[h]["pass"] += 1
        elif r.result == "FAIL":
            hourly[h]["fail"] += 1
        else:
            hourly[h]["term"] += 1

    hourly_sorted = dict(sorted(hourly.items()))
    hourly_totals = [v["total"] for v in hourly_sorted.values()]
    avg_hourly  = sum(hourly_totals) / len(hourly_totals) if hourly_totals else 0.0

    peak_entry  = (
        max(hourly_sorted.items(), key=lambda x: x[1]["total"])
        if hourly_sorted else ("", {"total": 0})
    )
    peak_hour, peak_data = peak_entry

    return {
        "total":            total,
        "batch_start":      batch_start,
        "batch_end":        batch_end,
        "batch_duration_s": batch_duration_s,
        "avg_cycle_s":      avg_cycle_s,
        "min_cycle_s":      min_cycle_s,
        "max_cycle_s":      max_cycle_s,
        "hourly":           hourly_sorted,
        "avg_hourly":       avg_hourly,
        "peak_hour":        peak_hour,
        "peak_count":       peak_data["total"],
    }


def _fmt_duration(seconds: float) -> str:
    if seconds <= 0:
        return "N/A"
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}m {sec:02d}s"
    h, mn = divmod(m, 60)
    return f"{h}h {mn:02d}m"


# ══════════════════════════════════════════════════════════════════════════
# GUI
# ══════════════════════════════════════════════════════════════════════════

def run_gui() -> int:
    try:
        from PyQt5.QtCore import Qt, QTimer
        from PyQt5.QtGui import QColor, QFont
        from PyQt5.QtWidgets import (
            QApplication,
            QCheckBox,
            QFileDialog,
            QFrame,
            QGridLayout,
            QGroupBox,
            QHBoxLayout,
            QHeaderView,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMessageBox,
            QProgressBar,
            QPushButton,
            QSizePolicy,
            QTabWidget,
            QTableWidget,
            QTableWidgetItem,
            QVBoxLayout,
            QWidget,
        )
    except ModuleNotFoundError as e:
        print("PyQt5 is not installed in this Python environment.")
        print("Install it first:  pip install PyQt5")
        print(f"Details: {e}")
        return 2

    try:
        from PyQt5.QtSvg import QSvgWidget  # type: ignore
    except ModuleNotFoundError:
        QSvgWidget = None  # type: ignore

    # ── Global QSS ─────────────────────────────────────────────────────────
    APP_QSS = f"""
    QMainWindow, QDialog, QWidget {{
        background-color: {C_BG};
        color: {C_TEXT};
        font-family: "Segoe UI", "SF Pro Display", Arial, sans-serif;
        font-size: 12px;
    }}
    /* ── Tabs ── */
    QTabWidget::pane {{
        border: 1px solid {C_BORDER};
        border-radius: 10px;
        background: {C_SURFACE};
        margin-top: -1px;
    }}
    QTabBar::tab {{
        background: {C_SURFACE};
        color: {C_TEXT2};
        padding: 10px 26px;
        border: 1px solid {C_BORDER};
        border-bottom: none;
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 1.5px;
        min-width: 120px;
    }}
    QTabBar::tab:selected {{
        background: {C_SURF2};
        color: {C_BLUE};
        border-color: {C_BLUE};
        border-bottom: 2px solid {C_BLUE};
    }}
    QTabBar::tab:hover:!selected {{
        background: {C_SURF2};
        color: {C_TEXT};
    }}
    /* ── GroupBox ── */
    QGroupBox {{
        font-weight: 600;
        font-size: 10px;
        letter-spacing: 2px;
        color: {C_TEXT2};
        border: 1px solid {C_BORDER};
        border-radius: 10px;
        margin-top: 18px;
        padding-top: 18px;
        background-color: {C_SURFACE};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 14px;
        padding: 0 8px;
        color: {C_TEXT2};
        background: {C_SURFACE};
    }}
    /* ── Inputs ── */
    QLineEdit {{
        background-color: {C_SURF2};
        color: {C_TEXT};
        border: 1px solid {C_BORDER};
        border-radius: 6px;
        padding: 7px 10px;
        font-size: 12px;
        selection-background-color: {C_BLUE};
        selection-color: #07080a;
    }}
    QLineEdit:focus     {{ border-color: {C_BLUE}; }}
    QLineEdit:read-only {{ background-color: {C_SURF3}; color: {C_TEXT2}; }}
    QLabel    {{ color: {C_TEXT2}; font-size: 12px; }}
    QCheckBox {{ color: {C_TEXT2}; spacing: 8px; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px;
        border: 1px solid {C_BORDER};
        border-radius: 4px;
        background: {C_SURF2};
    }}
    QCheckBox::indicator:checked {{ background: {C_BLUE}; border-color: {C_BLUE}; }}
    /* ── Progress ── */
    QProgressBar {{
        background-color: {C_SURF3};
        border: 1px solid {C_BORDER};
        border-radius: 5px;
        text-align: center;
        color: {C_TEXT};
        font-size: 11px;
        font-weight: 600;
        height: 20px;
    }}
    QProgressBar::chunk {{
        background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
            stop:0 #0097a7, stop:1 {C_BLUE});
        border-radius: 4px;
    }}
    /* ── Scrollbar ── */
    QScrollBar:vertical {{
        background: {C_SURF2}; width: 8px; border-radius: 4px; margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {C_BORDER}; border-radius: 4px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {C_TEXT3}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    /* ── Table ── */
    QTableWidget {{
        background-color: {C_SURFACE};
        color: {C_TEXT};
        border: 1px solid {C_BORDER};
        border-radius: 8px;
        gridline-color: {C_SURF3};
        selection-background-color: {C_SURF2};
        alternate-background-color: {C_SURF2};
    }}
    QTableWidget::item {{ padding: 6px 10px; border-bottom: 1px solid {C_SURF3}; }}
    QTableWidget::item:selected {{ background-color: {C_SURF2}; color: {C_TEXT}; }}
    QHeaderView::section {{
        background-color: {C_SURF2};
        color: {C_TEXT2};
        padding: 8px 10px;
        border: none;
        border-right: 1px solid {C_BORDER};
        border-bottom: 1px solid {C_BORDER};
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 1px;
    }}
    /* ── MessageBox ── */
    QMessageBox {{ background-color: {C_SURFACE}; color: {C_TEXT}; }}
    QMessageBox QLabel {{ color: {C_TEXT}; font-size: 13px; }}
    QMessageBox QPushButton {{ min-width: 80px; }}
    """

    # ── MetricCard ─────────────────────────────────────────────────────────
    class MetricCard(QFrame):
        def __init__(self, label: str, value: str = "—", accent: str = C_TEXT, parent=None):
            super().__init__(parent)
            self.setObjectName("metricCard")
            self._accent = accent
            self.setStyleSheet(f"""
                QFrame#metricCard {{
                    background: {C_SURF2};
                    border: 1px solid {C_BORDER};
                    border-radius: 10px;
                }}
                QFrame#metricCard:hover {{
                    border-color: {C_BLUE};
                    background: {C_SURF3};
                }}
            """)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.setMinimumHeight(84)

            vbox = QVBoxLayout(self)
            vbox.setContentsMargins(14, 12, 14, 12)
            vbox.setSpacing(5)

            self._lbl = QLabel(label.upper())
            self._lbl.setStyleSheet(
                f"color:{C_TEXT3}; font-size:10px; font-weight:600; "
                f"letter-spacing:1.5px; background:transparent; border:none;"
            )
            self._val = QLabel(value)
            self._val.setStyleSheet(
                f"color:{accent}; font-size:20px; font-weight:700; "
                f"background:transparent; border:none;"
            )
            self._val.setFont(QFont("Segoe UI", 16, QFont.Bold))

            vbox.addWidget(self._lbl)
            vbox.addWidget(self._val)

        def set_value(self, value: str, accent: str = None):
            self._val.setText(value)
            color = accent or self._accent
            self._val.setStyleSheet(
                f"color:{color}; font-size:20px; font-weight:700; "
                f"background:transparent; border:none;"
            )

    # ── Main Window ────────────────────────────────────────────────────────
    class WiFiStressLogAnalyzer(QMainWindow):

        def __init__(self):
            super().__init__()
            self.records: List[LogRecord] = []
            self.raw_records: List[LogRecord] = []
            self._csv_sorter_available = self._try_load_filename_sorter()
            self._log_dir_user_selected = False
            self._db_upload_cancel_requested = False
            self._db_heartbeat_timer = None
            self.init_ui()
            if DB_UPLOAD_AVAILABLE:
                QTimer.singleShot(150, self._run_initial_db_connection_check)
                self._start_db_heartbeat_timer()

        # ── helpers ───────────────────────────────────────────────────────

        def _try_load_filename_sorter(self) -> bool:
            try:
                import sort_filenames_to_csv as sn_csv  # type: ignore
            except Exception:
                self._sn_csv = None
                return False
            self._sn_csv = sn_csv
            return True

        def _reset_state_for_new_input(self):
            self.records = []
            self.raw_records = []
            self.total_label.set_value("0", C_TEXT)
            self.pass_label.set_value("0  (0.0%)", C_GREEN)
            self.fail_label.set_value("0  (0.0%)", C_RED)
            self.term_label.set_value("0  (0.0%)", C_ORANGE)
            if hasattr(self, "report_csv_btn"):
                self.report_csv_btn.setEnabled(bool(self._log_dir_user_selected))
            if hasattr(self, "db_upload_btn"):
                self.db_upload_btn.setEnabled(bool(self._log_dir_user_selected))

        def _field_label(self, text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"color:{C_TEXT3}; font-size:10px; font-weight:600; "
                f"letter-spacing:1px; background:transparent; border:none;"
            )
            return lbl

        # ── button styles ─────────────────────────────────────────────────

        def _btn_gray(self) -> str:
            return f"""
                QPushButton {{
                    background:{C_SURF3}; color:{C_TEXT2};
                    border:1px solid {C_BORDER}; padding:7px 16px;
                    border-radius:6px; font-size:12px; font-weight:600;
                }}
                QPushButton:hover {{ background:{C_BORDER}; color:{C_TEXT}; }}
                QPushButton:pressed {{ background:{C_SURF2}; }}
            """

        def _btn_blue(self) -> str:
            return f"""
                QPushButton {{
                    background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                        stop:0 #0097a7, stop:1 {C_BLUE});
                    color:#07080a; border:none; padding:9px 20px;
                    border-radius:7px; font-size:12px; font-weight:700;
                    letter-spacing:0.5px;
                }}
                QPushButton:hover {{
                    background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                        stop:0 #00bcd4, stop:1 #40efff);
                }}
                QPushButton:pressed {{ background:#006979; color:{C_TEXT}; }}
                QPushButton:disabled {{ background:{C_SURF3}; color:{C_TEXT3}; }}
            """

        def _btn_green(self) -> str:
            return f"""
                QPushButton {{
                    background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                        stop:0 #059669, stop:1 {C_GREEN});
                    color:#ffffff; border:none; padding:9px 20px;
                    border-radius:7px; font-size:12px; font-weight:700;
                    letter-spacing:0.5px;
                }}
                QPushButton:hover {{
                    background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                        stop:0 {C_GREEN}, stop:1 #34d399);
                }}
                QPushButton:pressed {{ background:#065f46; }}
                QPushButton:disabled {{ background:{C_SURF3}; color:{C_TEXT3}; }}
            """

        # ── UI init ───────────────────────────────────────────────────────

        def init_ui(self):
            self.setWindowTitle(f"{APP_WINDOW_TITLE} - (v{APP_VERSION})")
            self.setGeometry(100, 100, 1120, 720)
            self.setMinimumSize(900, 600)

            root = QWidget()
            self.setCentralWidget(root)
            root_vbox = QVBoxLayout(root)
            root_vbox.setContentsMargins(0, 0, 0, 0)
            root_vbox.setSpacing(0)

            # Header bar
            header = QFrame()
            header.setFixedHeight(58)
            header.setStyleSheet(f"""
                QFrame {{
                    background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                        stop:0 {C_SURFACE}, stop:0.5 {C_SURF2}, stop:1 {C_SURFACE});
                    border-bottom:1px solid {C_BORDER};
                }}
            """)
            hbox = QHBoxLayout(header)
            hbox.setContentsMargins(20, 0, 20, 0)

            logo_path = os.path.join(APP_DIR, "technexion_logo.svg")
            if QSvgWidget is not None and os.path.exists(logo_path):
                logo = QSvgWidget(logo_path)
                logo.setFixedSize(160, 28)
                hbox.addWidget(logo)
            else:
                brand = QLabel("TechNexion")
                brand.setStyleSheet(
                    f"color:{C_BLUE}; font-size:14px; font-weight:700; "
                    f"letter-spacing:2px; border:none; background:transparent;"
                )
                hbox.addWidget(brand)

            hbox.addStretch(1)
            title_lbl = QLabel(APP_HEADER_TITLE.upper())
            title_lbl.setStyleSheet(
                f"color:{C_TEXT}; font-size:13px; font-weight:700; "
                f"letter-spacing:3px; border:none; background:transparent;"
            )
            hbox.addWidget(title_lbl)
            hbox.addStretch(1)

            ver_lbl = QLabel(f"v{APP_VERSION}")
            ver_lbl.setStyleSheet(
                f"color:{C_TEXT3}; font-size:11px; border:none; background:transparent;"
            )
            hbox.addWidget(ver_lbl)
            root_vbox.addWidget(header)

            # Tabs
            content = QWidget()
            c_vbox = QVBoxLayout(content)
            c_vbox.setContentsMargins(16, 12, 16, 12)
            c_vbox.setSpacing(0)

            self.tabs = QTabWidget()
            self.tabs.setDocumentMode(True)

            # Tab 1 — Analysis
            tab1 = QWidget()
            t1 = QVBoxLayout(tab1)
            t1.setContentsMargins(0, 12, 0, 0)
            t1.setSpacing(10)
            t1.addWidget(self._build_log_analysis_group())
            t1.addWidget(self._build_report_group())
            t1.addStretch(1)
            self.tabs.addTab(tab1, "  Analysis  ")

            # Tab 2 — Advanced
            tab2 = QWidget()
            t2 = QVBoxLayout(tab2)
            t2.setContentsMargins(0, 12, 0, 0)
            t2.setSpacing(10)
            t2.addWidget(self._build_advanced_analysis_group())
            self.tabs.addTab(tab2, "  Advanced  ")

            # Tab 3 — DB Upload (conditional)
            if DB_UPLOAD_AVAILABLE:
                tab3 = QWidget()
                t3 = QVBoxLayout(tab3)
                t3.setContentsMargins(0, 12, 0, 0)
                t3.setSpacing(10)
                t3.addWidget(self._build_db_upload_group())
                t3.addStretch(1)
                self.tabs.addTab(tab3, "  DB Upload  ")

            c_vbox.addWidget(self.tabs)
            root_vbox.addWidget(content)

        # ── Analysis tab ──────────────────────────────────────────────────

        def _build_log_analysis_group(self):
            group = QGroupBox("Log Analysis")
            grid = QGridLayout(group)
            grid.setContentsMargins(16, 22, 16, 16)
            grid.setVerticalSpacing(10)
            grid.setHorizontalSpacing(12)

            grid.addWidget(self._field_label("Work Order"), 0, 0)
            self.work_order_input = QLineEdit()
            self.work_order_input.setPlaceholderText("5101-260108007")
            self.work_order_input.setMinimumHeight(34)
            grid.addWidget(self.work_order_input, 0, 1, 1, 2)

            grid.addWidget(self._field_label("Product Name"), 1, 0)
            self.production_name_input = QLineEdit()
            self.production_name_input.setPlaceholderText("TNXXX-XXXX-XXXXXX")
            self.production_name_input.setMinimumHeight(34)
            grid.addWidget(self.production_name_input, 1, 1, 1, 2)

            grid.addWidget(self._field_label("Log Folder"), 2, 0)
            self.log_dir_display = QLineEdit()
            self.log_dir_display.setReadOnly(True)
            self.log_dir_display.setPlaceholderText("Select folder containing log .txt files")
            self.log_dir_display.setText(DEFAULT_DIR)
            self.log_dir_display.setMinimumHeight(34)
            grid.addWidget(self.log_dir_display, 2, 1)

            browse_btn = QPushButton("Browse")
            browse_btn.clicked.connect(self.on_browse_log_dir)
            browse_btn.setStyleSheet(self._btn_gray())
            browse_btn.setMinimumHeight(34)
            grid.addWidget(browse_btn, 2, 2)

            self.parse_btn = QPushButton("Parse")
            self.parse_btn.clicked.connect(self.on_parse)
            self.parse_btn.setStyleSheet(self._btn_blue())
            self.parse_btn.setMinimumHeight(38)
            grid.addWidget(self.parse_btn, 3, 2)

            # KPI row
            kpi_frame = QFrame()
            kpi_frame.setStyleSheet("background:transparent; border:none;")
            kpi_hbox = QHBoxLayout(kpi_frame)
            kpi_hbox.setContentsMargins(0, 6, 0, 0)
            kpi_hbox.setSpacing(10)

            self.total_label = MetricCard("Total Units",    "0",          C_TEXT)
            self.pass_label  = MetricCard("PASS",           "0  (0.0%)",  C_GREEN)
            self.fail_label  = MetricCard("FAIL + TERM",    "0  (0.0%)",  C_RED)
            self.term_label  = MetricCard("Terminated",     "0  (0.0%)",  C_ORANGE)

            for card in (self.total_label, self.pass_label, self.fail_label, self.term_label):
                kpi_hbox.addWidget(card)

            grid.addWidget(kpi_frame, 3, 0, 1, 2)
            return group

        def _build_report_group(self):
            group = QGroupBox("Report Output")
            layout = QGridLayout(group)
            layout.setContentsMargins(16, 22, 16, 16)
            layout.setSpacing(10)

            layout.addWidget(self._field_label("Output Folder"), 0, 0)
            self.report_dir_display = QLineEdit()
            self.report_dir_display.setReadOnly(True)
            self.report_dir_display.setPlaceholderText("Auto: <Log Folder>/outfiles")
            self.report_dir_display.setText(os.path.join(DEFAULT_DIR, "outfiles"))
            self.report_dir_display.setMinimumHeight(34)
            layout.addWidget(self.report_dir_display, 0, 1, 1, 2)

            self.report_csv_btn = QPushButton("Generate Report + SN Summary")
            self.report_csv_btn.clicked.connect(self.on_report)
            self.report_csv_btn.setStyleSheet(self._btn_green())
            self.report_csv_btn.setMinimumHeight(38)
            self.report_csv_btn.setEnabled(False)
            layout.addWidget(self.report_csv_btn, 1, 1, 1, 2)
            return group

        # ── Advanced Analysis tab ─────────────────────────────────────────

        def _build_advanced_analysis_group(self):
            group = QGroupBox("Production Throughput Analysis")
            outer = QVBoxLayout(group)
            outer.setContentsMargins(16, 22, 16, 16)
            outer.setSpacing(12)

            # Row 1 — cycle-time KPIs
            row1 = QHBoxLayout()
            row1.setSpacing(10)
            self.adv_total     = MetricCard("Total Units",    "—", C_BLUE)
            self.adv_avg_cycle = MetricCard("Avg Cycle Time", "—", C_TEXT)
            self.adv_min_cycle = MetricCard("Fastest Unit",   "—", C_GREEN)
            self.adv_max_cycle = MetricCard("Slowest Unit",   "—", C_ORANGE)
            for c in (self.adv_total, self.adv_avg_cycle, self.adv_min_cycle, self.adv_max_cycle):
                row1.addWidget(c)
            outer.addLayout(row1)

            # Row 2 — throughput KPIs
            row2 = QHBoxLayout()
            row2.setSpacing(10)
            self.adv_avg_hourly = MetricCard("Avg Output / Hour", "—", C_PURPLE)
            self.adv_peak_hour  = MetricCard("Peak Hour Output",  "—", C_ORANGE)
            self.adv_duration   = MetricCard("Batch Duration",    "—", C_TEXT2)
            self.adv_window     = MetricCard("Batch Window",      "—", C_TEXT3)
            for c in (self.adv_avg_hourly, self.adv_peak_hour, self.adv_duration, self.adv_window):
                row2.addWidget(c)
            outer.addLayout(row2)

            note = QLabel(
                "* Cycle time = gap between consecutive test completions  "
                "(gaps > 2 h excluded as shift breaks)"
            )
            note.setStyleSheet(
                f"color:{C_TEXT3}; font-size:10px; font-style:italic; "
                f"background:transparent; border:none;"
            )
            outer.addWidget(note)

            # Divider
            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            sep.setStyleSheet(f"color:{C_BORDER}; background:{C_BORDER};")
            sep.setFixedHeight(1)
            outer.addWidget(sep)

            tbl_hdr = QLabel("Hourly Breakdown")
            tbl_hdr.setStyleSheet(
                f"color:{C_TEXT2}; font-size:11px; font-weight:600; "
                f"letter-spacing:1px; background:transparent; border:none;"
            )
            outer.addWidget(tbl_hdr)

            self.hourly_table = QTableWidget(0, 6)
            self.hourly_table.setHorizontalHeaderLabels(
                ["Hour", "Total", "PASS", "FAIL+TERM", "TERM", "Yield %"]
            )
            self.hourly_table.setEditTriggers(QTableWidget.NoEditTriggers)
            self.hourly_table.setSelectionBehavior(QTableWidget.SelectRows)
            self.hourly_table.setAlternatingRowColors(True)
            self.hourly_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            for col in range(1, 6):
                self.hourly_table.horizontalHeader().setSectionResizeMode(
                    col, QHeaderView.ResizeToContents
                )
            self.hourly_table.verticalHeader().setVisible(False)
            self.hourly_table.setMinimumHeight(220)
            self.hourly_table.setVisible(False)
            outer.addWidget(self.hourly_table)

            self._adv_placeholder = QLabel(
                "Parse or Generate Report on the Analysis tab to populate this view."
            )
            self._adv_placeholder.setAlignment(Qt.AlignCenter)
            self._adv_placeholder.setStyleSheet(
                f"color:{C_TEXT3}; font-size:13px; padding:40px; "
                f"background:transparent; border:none;"
            )
            outer.addWidget(self._adv_placeholder)
            return group

        def _update_advanced_analysis(self):
            if not self.records:
                self._adv_placeholder.setVisible(True)
                self.hourly_table.setVisible(False)
                return

            data = compute_advanced_analysis(self.records)
            self._adv_placeholder.setVisible(False)
            self.hourly_table.setVisible(True)

            self.adv_total.set_value(str(data["total"]), C_BLUE)
            self.adv_avg_cycle.set_value(_fmt_duration(data["avg_cycle_s"]))
            self.adv_min_cycle.set_value(_fmt_duration(data["min_cycle_s"]), C_GREEN)
            self.adv_max_cycle.set_value(_fmt_duration(data["max_cycle_s"]), C_ORANGE)
            self.adv_avg_hourly.set_value(f"{data['avg_hourly']:.1f}", C_PURPLE)

            peak_label = (
                f"{data['peak_count']}  ({data['peak_hour'][-5:]})"
                if data["peak_hour"] else "—"
            )
            self.adv_peak_hour.set_value(peak_label, C_ORANGE)
            self.adv_duration.set_value(_fmt_duration(data["batch_duration_s"]), C_TEXT2)

            window_text = (
                f"{data['batch_start'].strftime('%m/%d %H:%M')} ~ "
                f"{data['batch_end'].strftime('%m/%d %H:%M')}"
                if data["total"] >= 2 else "—"
            )
            self.adv_window.set_value(window_text, C_TEXT3)

            # Hourly table
            hourly = data["hourly"]
            self.hourly_table.setRowCount(len(hourly))
            for row_i, (hour_key, counts) in enumerate(hourly.items()):
                t         = counts["total"]
                p         = counts["pass"]
                fail_term = counts["fail"] + counts["term"]
                term      = counts["term"]
                yld       = (p / t * 100) if t > 0 else 0.0
                yld_color = C_GREEN if yld >= 95 else (C_ORANGE if yld >= 80 else C_RED)

                cells = [
                    (hour_key,       C_TEXT2,  Qt.AlignLeft | Qt.AlignVCenter),
                    (str(t),         C_TEXT,   Qt.AlignCenter),
                    (str(p),         C_GREEN,  Qt.AlignCenter),
                    (str(fail_term), C_RED,    Qt.AlignCenter),
                    (str(term),      C_ORANGE, Qt.AlignCenter),
                    (f"{yld:.1f}%",  yld_color, Qt.AlignCenter),
                ]
                for col_i, (text, color, align) in enumerate(cells):
                    item = QTableWidgetItem(text)
                    item.setForeground(QColor(color))
                    item.setTextAlignment(align)
                    self.hourly_table.setItem(row_i, col_i, item)

        # ── DB Upload tab ─────────────────────────────────────────────────

        def _build_db_upload_group(self):
            group = QGroupBox("Database Upload  (log_parser)")
            layout = QGridLayout(group)
            layout.setContentsMargins(16, 22, 16, 16)
            layout.setSpacing(10)

            layout.addWidget(self._field_label("Server IP"), 0, 0)
            self.db_server_ip_input = QLineEdit()
            self.db_server_ip_input.setText("10.20.31.40")
            self.db_server_ip_input.setMinimumHeight(34)
            self.db_server_ip_input.textChanged.connect(self._on_server_ip_changed)
            layout.addWidget(self.db_server_ip_input, 0, 1, 1, 2)

            layout.addWidget(self._field_label("PostgreSQL DSN"), 1, 0)
            self.dsn_input = QLineEdit()
            self.dsn_input.setText(self._build_dsn_from_server_ip())
            self.dsn_input.setReadOnly(True)
            self.dsn_input.setMinimumHeight(34)
            layout.addWidget(self.dsn_input, 1, 1, 1, 2)

            self.db_conn_status_label = QLabel("DB connection: not checked")
            self.db_conn_status_label.setStyleSheet(f"color:{C_TEXT3};")
            layout.addWidget(self.db_conn_status_label, 2, 1, 1, 2)

            self.db_upload_btn = QPushButton("Upload to DB")
            self.db_upload_btn.clicked.connect(self.on_upload_to_db)
            self.db_upload_btn.setStyleSheet(self._btn_blue())
            self.db_upload_btn.setMinimumHeight(38)
            self.db_upload_btn.setEnabled(False)
            layout.addWidget(self.db_upload_btn, 3, 2)

            self.db_cancel_btn = QPushButton("Cancel Upload")
            self.db_cancel_btn.clicked.connect(self.on_cancel_db_upload)
            self.db_cancel_btn.setStyleSheet(self._btn_gray())
            self.db_cancel_btn.setMinimumHeight(38)
            self.db_cancel_btn.setEnabled(False)
            layout.addWidget(self.db_cancel_btn, 3, 1)

            self.db_resume_checkbox = QCheckBox("Resume from last upload log")
            self.db_resume_checkbox.setChecked(False)
            layout.addWidget(self.db_resume_checkbox, 4, 0, 1, 2)

            self.db_upload_progress = QProgressBar()
            self.db_upload_progress.setMinimum(0)
            self.db_upload_progress.setMaximum(100)
            self.db_upload_progress.setValue(0)
            self.db_upload_progress.setTextVisible(True)
            self.db_upload_progress.setFormat("%p%")
            layout.addWidget(self.db_upload_progress, 5, 0, 1, 3)

            self.db_upload_status = QLabel("Upload status: idle")
            layout.addWidget(self.db_upload_status, 6, 0, 1, 3)

            self.db_upload_sanitize_status = QLabel("NUL sanitize: files=0, bytes=0")
            self.db_upload_sanitize_status.setStyleSheet(f"color:{C_TEXT3}; font-size:11px;")
            layout.addWidget(self.db_upload_sanitize_status, 7, 0, 1, 3)

            self.db_upload_policy = QLabel(
                "Duplicate policy: SKIP (same file_hash will not overwrite existing DB row)"
            )
            self.db_upload_policy.setStyleSheet(f"color:{C_TEXT3}; font-size:11px;")
            layout.addWidget(self.db_upload_policy, 8, 0, 1, 3)
            return group

        # ── DB helpers — logic identical to original ──────────────────────

        def _build_dsn_from_server_ip(self) -> str:
            host = "localhost"
            if hasattr(self, "db_server_ip_input"):
                host = self.db_server_ip_input.text().strip() or "localhost"
            return DB_DSN_TEMPLATE.format(host=host)

        def _set_db_connection_status(self, state: str, detail: str = ""):
            if not hasattr(self, "db_conn_status_label"):
                return
            if state == "ok":
                self.db_conn_status_label.setText("DB connection: connected")
                self.db_conn_status_label.setStyleSheet(f"color:{C_GREEN};")
            elif state == "fail":
                suffix = f" ({detail})" if detail else ""
                self.db_conn_status_label.setText(f"DB connection: disconnected{suffix}")
                self.db_conn_status_label.setStyleSheet(f"color:{C_RED};")
            else:
                self.db_conn_status_label.setText("DB connection: not checked")
                self.db_conn_status_label.setStyleSheet(f"color:{C_TEXT3};")

        def _on_server_ip_changed(self, _value: str):
            if hasattr(self, "dsn_input"):
                self.dsn_input.setText(self._build_dsn_from_server_ip())
            self._set_db_connection_status("unknown")

        def _check_db_connection(self, dsn: str) -> Tuple[bool, str]:
            if not DB_UPLOAD_AVAILABLE:
                return False, "psycopg2 unavailable"
            try:
                import psycopg2
                with psycopg2.connect(dsn, connect_timeout=DB_CONNECT_TIMEOUT_SECONDS) as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1")
                        cur.fetchone()
                return True, ""
            except Exception as exc:
                return False, str(exc)

        def _start_db_heartbeat_timer(self):
            if self._db_heartbeat_timer is None:
                self._db_heartbeat_timer = QTimer(self)
                self._db_heartbeat_timer.timeout.connect(self._run_db_heartbeat_check)
            self._db_heartbeat_timer.start(DB_HEARTBEAT_INTERVAL_MS)

        def _run_db_heartbeat_check(self):
            self._run_db_connection_check(update_upload_status=False)

        def _run_initial_db_connection_check(self):
            self._run_db_connection_check(update_upload_status=True)

        def _run_db_connection_check(self, update_upload_status: bool = False):
            if not hasattr(self, "db_server_ip_input"):
                return
            server_ip = self.db_server_ip_input.text().strip()
            if not server_ip:
                self._set_db_connection_status("fail", "empty server ip")
                if update_upload_status and hasattr(self, "db_upload_status"):
                    self.db_upload_status.setText("Upload status: DB check failed (empty server ip)")
                return
            dsn = self._build_dsn_from_server_ip()
            if hasattr(self, "dsn_input"):
                self.dsn_input.setText(dsn)
            ok, err = self._check_db_connection(dsn)
            if ok:
                self._set_db_connection_status("ok")
                if update_upload_status and hasattr(self, "db_upload_status"):
                    self.db_upload_status.setText("Upload status: idle (DB connected)")
            else:
                self._set_db_connection_status("fail", err)
                if update_upload_status and hasattr(self, "db_upload_status"):
                    self.db_upload_status.setText("Upload status: idle (DB disconnected)")

        def closeEvent(self, event):
            if self._db_heartbeat_timer is not None:
                self._db_heartbeat_timer.stop()
            super().closeEvent(event)

        def on_cancel_db_upload(self):
            self._db_upload_cancel_requested = True
            if hasattr(self, "db_cancel_btn"):
                self.db_cancel_btn.setEnabled(False)
            if hasattr(self, "db_upload_status"):
                self.db_upload_status.setText("Upload status: cancellation requested...")

        def _set_db_upload_status(
            self, processed, total_files, inserted, duplicates, errors,
            sanitized_files, sanitized_nul_bytes, state="processed",
        ):
            self.db_upload_status.setText(
                f"Upload status: {state} ({processed}/{total_files}, "
                f"inserted={inserted}, dup={duplicates}, err={errors})"
            )
            if hasattr(self, "db_upload_sanitize_status"):
                self.db_upload_sanitize_status.setText(
                    f"NUL sanitize: files={sanitized_files}, bytes={sanitized_nul_bytes}"
                )

        def _write_upload_logs(
            self, log_dir: str, entries: List[Tuple[int, str, str, str]], summary: str
        ) -> Tuple[Path, Path]:
            out_dir = _outfiles_dir_for(log_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_path = out_dir / f"db_upload_log_{ts}.csv"
            txt_path = out_dir / f"db_upload_log_{ts}.txt"

            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["Index", "Filename", "Status", "Message"])
                for idx, filename, status, message in entries:
                    w.writerow([idx, filename, status, message])

            with open(txt_path, "w", encoding="utf-8") as f:
                f.write("Database Upload Log\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Summary: {summary}\n\n")
                for idx, filename, status, message in entries:
                    f.write(f"[{idx}] {filename} | {status} | {message}\n")

            return csv_path, txt_path

        def _load_processed_filenames_from_last_upload_log(self, log_dir: str) -> set:
            out_dir = _outfiles_dir_for(log_dir)
            if not out_dir.exists() or not out_dir.is_dir():
                return set()
            latest_csv: Optional[Path] = None
            latest_mtime = -1.0
            for p in out_dir.glob("db_upload_log_*.csv"):
                if not p.is_file():
                    continue
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    continue
                if mtime > latest_mtime:
                    latest_mtime = mtime
                    latest_csv = p
            if latest_csv is None:
                return set()
            processed: set = set()
            try:
                with open(latest_csv, "r", encoding="utf-8-sig", newline="") as f:
                    for row in csv.DictReader(f):
                        filename = (row.get("Filename") or "").strip()
                        status   = (row.get("Status") or "").strip().lower()
                        if filename and status in {"inserted", "duplicate", "error"}:
                            processed.add(filename)
            except Exception:
                return set()
            return processed

        # ── Browse ────────────────────────────────────────────────────────

        def on_browse_log_dir(self):
            initial_dir = self.log_dir_display.text().strip() or _default_browse_dir()
            folder = QFileDialog.getExistingDirectory(self, "Select Log Folder", initial_dir)
            if folder:
                self.log_dir_display.setText(folder)
                self._log_dir_user_selected = True
                self._reset_state_for_new_input()
                if hasattr(self, "report_csv_btn"):
                    self.report_csv_btn.setEnabled(True)
                if hasattr(self, "db_upload_btn"):
                    self.db_upload_btn.setEnabled(True)
                self.report_dir_display.setText(
                    os.path.normpath(str(_outfiles_dir_for(folder)))
                )

        # ── DB Upload — logic identical to original ───────────────────────

        def on_upload_to_db(self):
            if not DB_UPLOAD_AVAILABLE:
                QMessageBox.warning(self, "Database Upload",
                                    "psycopg2 or log_parser module not found.")
                return

            log_dir = self.log_dir_display.text().strip()
            if not log_dir:
                QMessageBox.warning(self, "Database Upload", "Please select Log Folder first.")
                return

            product_name = self.production_name_input.text().strip()
            work_order   = self.work_order_input.text().strip()
            if not product_name or not work_order:
                QMessageBox.warning(self, "Database Upload",
                                    "Product Name and Work Order are required before upload.")
                return

            server_ip = (
                self.db_server_ip_input.text().strip()
                if hasattr(self, "db_server_ip_input") else ""
            )
            if not server_ip:
                QMessageBox.warning(self, "Database Upload", "請輸入 Server IP。")
                self._set_db_connection_status("fail", "empty server ip")
                return

            dsn = self._build_dsn_from_server_ip()
            if hasattr(self, "dsn_input"):
                self.dsn_input.setText(dsn)

            db_ok, db_err = self._check_db_connection(dsn)
            if not db_ok:
                self._set_db_connection_status("fail", db_err)
                QMessageBox.warning(self, "Database Upload", f"DB 無連線。\n{db_err}")
                return

            self._set_db_connection_status("ok")
            self.db_upload_btn.setText("Uploading...")
            self.db_upload_btn.setEnabled(False)
            self.db_cancel_btn.setEnabled(True)
            self._db_upload_cancel_requested = False
            self.db_upload_progress.setValue(0)
            self.db_upload_status.setText("Upload status: preparing... (duplicate policy: skip)")
            self.repaint()

            try:
                import log_parser
                import psycopg2

                files = log_parser.scan_directory(log_dir)
                if not files:
                    self.db_upload_status.setText("Upload status: no matching files")
                    QMessageBox.information(self, "Database Upload",
                                            "No matching log files found.")
                    return

                if hasattr(self, "db_resume_checkbox") and self.db_resume_checkbox.isChecked():
                    processed = self._load_processed_filenames_from_last_upload_log(log_dir)
                    if processed:
                        files = [f for f in files if os.path.basename(str(f)) not in processed]
                        if not files:
                            self.db_upload_status.setText(
                                "Upload status: no pending files (resume mode)"
                            )
                            QMessageBox.information(
                                self, "Database Upload",
                                "Resume mode enabled. No pending files were found to upload.",
                            )
                            return

                ok = skip = err = 0
                sanitized_files = sanitized_nul_bytes_total = 0
                cancelled = False
                upload_log_entries: List[Tuple[int, str, str, str]] = []
                total_files = len(files)
                self.db_upload_progress.setMinimum(0)
                self.db_upload_progress.setMaximum(total_files)
                self.db_upload_progress.setValue(0)
                self._set_db_upload_status(0, total_files, ok, skip, err,
                                            sanitized_files, sanitized_nul_bytes_total)

                with psycopg2.connect(dsn) as conn:
                    for idx, f in enumerate(files, start=1):
                        QApplication.processEvents()
                        if self._db_upload_cancel_requested:
                            cancelled = True
                            upload_log_entries.append(
                                (idx, os.path.basename(str(f)), "cancelled",
                                 "User requested cancellation")
                            )
                            break

                        record = log_parser.ingest_file(f)
                        if not record:
                            err += 1
                            upload_log_entries.append(
                                (idx, os.path.basename(str(f)), "error",
                                 "Parse returned empty record")
                            )
                            self.db_upload_progress.setValue(idx)
                            self._set_db_upload_status(idx, total_files, ok, skip, err,
                                                        sanitized_files, sanitized_nul_bytes_total)
                            QApplication.processEvents()
                            continue

                        record["product_model"] = product_name
                        record["work_order"]     = work_order
                        sanitized_nul_bytes = int(record.get("sanitized_nul_bytes", 0) or 0)
                        if sanitized_nul_bytes > 0:
                            sanitized_files += 1
                            sanitized_nul_bytes_total += sanitized_nul_bytes

                        detail_suffix = (
                            f"; sanitized_nul_bytes={sanitized_nul_bytes}"
                            if sanitized_nul_bytes > 0 else ""
                        )

                        try:
                            if DB_DUPLICATE_POLICY != "skip":
                                raise RuntimeError("Unsupported duplicate policy for GUI uploader")
                            rid = log_parser.write_record(conn, record)
                            if rid:
                                ok += 1
                                upload_log_entries.append(
                                    (idx, os.path.basename(str(f)), "inserted",
                                     f"record_id={rid}{detail_suffix}")
                                )
                            else:
                                skip += 1
                                upload_log_entries.append(
                                    (idx, os.path.basename(str(f)), "duplicate",
                                     f"Duplicate or no-op insert{detail_suffix}")
                                )
                        except Exception as e:
                            print(f"[ERR] DB Insert Failed {f}: {e}")
                            err += 1
                            upload_log_entries.append(
                                (idx, os.path.basename(str(f)), "error",
                                 f"DB insert failed: {e}{detail_suffix}")
                            )

                        self.db_upload_progress.setValue(idx)
                        self._set_db_upload_status(idx, total_files, ok, skip, err,
                                                    sanitized_files, sanitized_nul_bytes_total)
                        QApplication.processEvents()

                processed_count = ok + skip + err
                state = "cancelled" if cancelled else "completed"
                self._set_db_upload_status(
                    processed_count, total_files, ok, skip, err,
                    sanitized_files, sanitized_nul_bytes_total, state=state,
                )

                summary_text = (
                    f"processed={processed_count}/{total_files}, inserted={ok}, "
                    f"duplicates={skip}, errors={err}, "
                    f"sanitized_files={sanitized_files}, "
                    f"sanitized_nul_bytes={sanitized_nul_bytes_total}, cancelled={cancelled}"
                )
                csv_log_path, txt_log_path = self._write_upload_logs(
                    log_dir, upload_log_entries, summary_text
                )

                sanitize_note = (
                    f"NUL bytes sanitized: {sanitized_nul_bytes_total} "
                    f"across {sanitized_files} files\n\n"
                    if sanitized_files > 0 else ""
                )
                status_word = "cancelled by user" if cancelled else "complete"
                QMessageBox.information(
                    self, "Database Upload",
                    f"Upload {status_word}!\n\n"
                    f"Found: {len(files)} files\n"
                    f"Processed: {processed_count}\n"
                    f"Inserted: {ok}\n"
                    f"Duplicates Skipped: {skip}\n"
                    f"Errors: {err}\n"
                    f"{sanitize_note}"
                    f"Upload Log CSV: {os.path.normpath(str(csv_log_path))}\n"
                    f"Upload Log TXT: {os.path.normpath(str(txt_log_path))}",
                )

            except Exception as e:
                self.db_upload_status.setText("Upload status: failed")
                QMessageBox.critical(self, "Database Upload", f"Upload failed:\n{e}")
            finally:
                self.db_upload_btn.setText("Upload to DB")
                self.db_upload_btn.setEnabled(True)
                self.db_cancel_btn.setEnabled(False)

        # ── Parse ─────────────────────────────────────────────────────────

        def on_parse(self):
            log_dir = self.log_dir_display.text().strip()
            if not log_dir:
                QMessageBox.warning(self, "Parse", "Please select Log Folder first.")
                return

            raw     = parse_log_directory_raw(log_dir)
            deduped = _dedupe_keep_latest_by_sn(raw)
            self.raw_records = raw
            self.records     = deduped

            pass_count = sum(1 for r in deduped if r.result == "PASS")
            fail_count = sum(1 for r in deduped if r.result == "FAIL")
            term_count = sum(1 for r in deduped if r.result == "TERMINATED")
            total      = pass_count + fail_count + term_count
            fail_incl  = fail_count + term_count

            self.total_label.set_value(str(total), C_TEXT)
            self.pass_label.set_value(f"{pass_count}  ({_ratio_text(pass_count, total)})", C_GREEN)
            self.fail_label.set_value(f"{fail_incl}  ({_ratio_text(fail_incl, total)})", C_RED)
            self.term_label.set_value(f"{term_count}  ({_ratio_text(term_count, total)})", C_ORANGE)

            self._log_dir_user_selected = True
            if hasattr(self, "report_csv_btn"):
                self.report_csv_btn.setEnabled(True)

            self._update_advanced_analysis()

            QMessageBox.information(
                self, "Parse",
                f"Parse completed.\nTotal: {total}\nPASS: {pass_count}\n"
                f"FAIL: {fail_count}\nTERMINATED: {term_count}",
            )

        # ── Generate outputs — identical logic to original ─────────────────

        def _generate_all_outputs_from_current_state(self) -> Tuple[Path, List[Path]]:
            if not self.records:
                raise ValueError("No parsed records. Please click Parse first.")

            log_dir = self.log_dir_display.text().strip()
            if not log_dir or not os.path.isdir(log_dir):
                raise ValueError("Please select Log Folder first.")

            out_dir = _outfiles_dir_for(log_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            self.report_dir_display.setText(os.path.normpath(str(out_dir)))

            pre_mtime: dict[Path, float] = {}
            for p in out_dir.glob("*"):
                if p.is_file():
                    try:
                        pre_mtime[p] = p.stat().st_mtime
                    except OSError:
                        pass

            production_name = (
                self.production_name_input.text().strip()
                or self.production_name_input.placeholderText().strip()
                or "UNKNOWN"
            )
            safe_name = _safe_name(production_name)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")

            csv_path = out_dir / f"wifi_stress_report_{safe_name}_{ts}.csv"
            txt_path = out_dir / f"wifi_stress_yield_{safe_name}_{ts}.txt"

            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["Product", "Date", "Time", "SN", "MAC", "Result", "Filename"])
                for r in sorted(self.records, key=lambda x: x.dt):
                    w.writerow([production_name, r.test_date, r.test_time,
                                 r.mac, r.serial, r.result, r.filename])

            raw_source = self.raw_records if self.raw_records else self.records
            _write_sn_attempt_summary_csv(out_dir, raw_source, production_name, safe_name, ts)

            total      = len(self.records)
            pass_count = sum(1 for r in self.records if r.result == "PASS")
            fail_count = sum(1 for r in self.records if r.result == "FAIL")
            term_count = sum(1 for r in self.records if r.result == "TERMINATED")

            dates = sorted({r.test_date for r in self.records})
            test_date_text = (
                dates[0] if len(dates) == 1
                else (f"{dates[0]} ~ {dates[-1]}" if dates
                      else datetime.now().strftime("%Y-%m-%d"))
            )
            report_time_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            def _r2(c, t):
                return "0.00%" if t <= 0 else f"{c / t * 100:.2f}%"

            with open(txt_path, "w", encoding="utf-8") as f:
                f.write("WiFi Yield Report\n")
                f.write(f"Product: {production_name}\n")
                f.write(f"Test Date: {test_date_text}\n")
                f.write(f"Report Time: {report_time_text}\n")
                f.write(f"Total Tests: {total}\n")
                f.write(f"PASS Count: {pass_count}, PASS Rate: {_r2(pass_count, total)}\n")
                fail_incl = fail_count + term_count
                f.write(f"FAIL Count: {fail_incl}, FAIL Rate: {_r2(fail_incl, total)}\n")
                f.write(f"(FAIL-only) Count: {fail_count}, Rate: {_r2(fail_count, total)}\n")
                f.write(f"TERMINATED Count: {term_count}, TERMINATED Rate: {_r2(term_count, total)}\n")

                by_sn: dict[str, List[LogRecord]] = {}
                for r in raw_source:
                    by_sn.setdefault(r.mac, []).append(r)
                retest_items = [
                    (sn, sorted(items, key=lambda x: x.dt))
                    for sn, items in by_sn.items() if len(items) > 1
                ]
                retest_items.sort(key=lambda x: x[0])
                total_retests = sum(len(items) - 1 for _, items in retest_items)

                f.write("\nRetest Details (grouped by SN)\n")
                f.write(f"Total Retests (exclude final): {total_retests}\n")
                if not retest_items:
                    f.write("No retest records found.\n")
                else:
                    for sn, items in retest_items:
                        seq = " -> ".join(
                            f"{i.result}@{i.test_date} {i.test_time}" for i in items
                        )
                        f.write(
                            f"SN: {sn}, Attempts: {len(items)}, "
                            f"Retests: {len(items)-1}, Results: {seq}\n"
                        )

                # Advanced analysis appended to the yield TXT report
                adv = compute_advanced_analysis(self.records)
                if adv:
                    f.write("\nAdvanced Production Analysis\n")
                    f.write(f"Batch Start   : {adv['batch_start'].strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write(f"Batch End     : {adv['batch_end'].strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write(f"Batch Duration: {_fmt_duration(adv['batch_duration_s'])}\n")
                    f.write(f"Avg Cycle Time: {_fmt_duration(adv['avg_cycle_s'])}\n")
                    f.write(f"Fastest Unit  : {_fmt_duration(adv['min_cycle_s'])}\n")
                    f.write(f"Slowest Unit  : {_fmt_duration(adv['max_cycle_s'])}\n")
                    f.write(f"Avg / Hour    : {adv['avg_hourly']:.1f}\n")
                    f.write(f"Peak Hour     : {adv['peak_hour']} ({adv['peak_count']} units)\n")
                    f.write("\nHourly Breakdown:\n")
                    for hk, hv in adv["hourly"].items():
                        yld = (hv["pass"] / hv["total"] * 100) if hv["total"] > 0 else 0
                        f.write(
                            f"  {hk}: total={hv['total']}, pass={hv['pass']}, "
                            f"fail={hv['fail']}, term={hv['term']}, yield={yld:.1f}%\n"
                        )

            if self._csv_sorter_available:
                input_dir = Path(log_dir)
                pn_prefix = f"{_safe_name(production_name)}_"
                recs_sn: list = []
                for fp in self._sn_csv.iter_txt_files(input_dir):
                    rec = self._sn_csv.parse_filename(fp)
                    if rec is not None:
                        recs_sn.append(rec)
                recs_sn.sort(key=self._sn_csv.sort_key)
                all_csv     = out_dir / f"{pn_prefix}all_sn_in_order.csv"
                actual_csv  = self._sn_csv.write_csv(recs_sn, all_csv)
                self._sn_csv.generate_reports_from_sn_in_order_csv(
                    actual_csv, out_dir, prefix=pn_prefix
                )

            pn_prefix  = f"{_safe_name(production_name)}_"
            _write_sn_sequence_check(out_dir, pn_prefix, log_dir, raw_source)

            changed: List[Path] = []
            for p in out_dir.glob("*"):
                if not p.is_file():
                    continue
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    continue
                prev = pre_mtime.get(p)
                if prev is None or mtime != prev:
                    changed.append(p)

            return out_dir, changed

        # ── Report (one-click) ────────────────────────────────────────────

        def on_report(self):
            log_dir = self.log_dir_display.text().strip()
            if not log_dir:
                QMessageBox.warning(self, "Report/CSV", "Please select Log Folder first.")
                return

            raw     = parse_log_directory_raw(log_dir)
            deduped = _dedupe_keep_latest_by_sn(raw)
            self.raw_records = raw
            self.records     = deduped

            pass_count = sum(1 for r in deduped if r.result == "PASS")
            fail_count = sum(1 for r in deduped if r.result == "FAIL")
            term_count = sum(1 for r in deduped if r.result == "TERMINATED")
            total      = pass_count + fail_count + term_count
            fail_incl  = fail_count + term_count

            self.total_label.set_value(str(total), C_TEXT)
            self.pass_label.set_value(f"{pass_count}  ({_ratio_text(pass_count, total)})", C_GREEN)
            self.fail_label.set_value(f"{fail_incl}  ({_ratio_text(fail_incl, total)})", C_RED)
            self.term_label.set_value(f"{term_count}  ({_ratio_text(term_count, total)})", C_ORANGE)

            self._log_dir_user_selected = True
            if hasattr(self, "report_csv_btn"):
                self.report_csv_btn.setEnabled(True)

            self._update_advanced_analysis()

            try:
                out_dir, generated_files = self._generate_all_outputs_from_current_state()
            except Exception as e:
                QMessageBox.critical(self, "Report/CSV", f"Failed to generate outputs.\n{e}")
                return

            summary_text = (
                "Summary: "
                f"Total={total} | "
                f"PASS={pass_count} ({_ratio_text(pass_count, total)}) | "
                f"FAIL(+TERM)={fail_incl} ({_ratio_text(fail_incl, total)}) | "
                f"TERMINATED={term_count} ({_ratio_text(term_count, total)})"
            )
            QMessageBox.information(
                self, "Report/CSV",
                _format_generated_files_message(out_dir, generated_files,
                                                 summary_text=summary_text),
            )

    import sys
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_QSS)
    win = WiFiStressLogAnalyzer()
    win.show()
    return app.exec_()


def main() -> int:
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
