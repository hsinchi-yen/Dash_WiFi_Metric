#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import psycopg2
    import log_parser
    DB_UPLOAD_AVAILABLE = True
except ImportError:
    DB_UPLOAD_AVAILABLE = False

APP_VERSION = "2026.03.20"
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


_FILENAME_V1_RE = re.compile(
    # Legacy format:
    #   YYYYMMDD_HHMMSS_SN_MAC_RESULT.txt
    # Note: historically this project stored SN in the LogRecord.mac field,
    # and MAC in the LogRecord.serial field (to match the requested CSV columns).
    r"^(?P<date>\d{8})_(?P<time>\d{6})_(?P<sn>[^_]+)_(?P<mac>[^_]+)_(?P<result>[A-Za-z]+)\.txt$"
)

_FILENAME_V2_RE = re.compile(
    # New format:
    #   YYYYMMDD_HHMMSS_SN_MAC1_MAC2_RESULT.txt
    r"^(?P<date>\d{8})_(?P<time>\d{6})_(?P<sn>[^_]+)_(?P<mac1>[^_]+)_(?P<mac2>[^_]+)_(?P<result>[A-Za-z]+)\.txt$"
)


@dataclass(frozen=True)
class LogRecord:
    dt: datetime
    test_date: str
    test_time: str
    mac: str
    serial: str
    result: str  # PASS/FAIL
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
    # Counting rule: duplicates by SN keep only the latest time.
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
    name_upper = filename.upper()

    # Requirement:
    # - Exclude dummy_dummy or any filename containing dummy
    if "DUMMY" in name_upper:
        return True

    return False


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

    # Keep existing CSV behavior:
    # - LogRecord.mac maps to the CSV "SN" column
    # - LogRecord.serial maps to the CSV "MAC" column
    if m2 is not None:
        mac1 = m.group("mac1")
        mac2 = m.group("mac2")
        mac_field = f"{mac1}_{mac2}"
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


def _default_browse_dir() -> str:
    return DEFAULT_DIR


def _safe_name(text: str) -> str:
    text = (text or "").strip() or "UNKNOWN"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)


_SN_TOKEN_RE = re.compile(r"^(?P<prefix>\D*?)(?P<num>\d+)(?P<suffix>\D*)$")


def _try_parse_sn_token(sn_text: str) -> Optional[Tuple[str, int, int, str]]:
    """Return (prefix, number, width, suffix) if sn_text contains a numeric token."""
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
        if width > 0:
            return f"{prefix}{n:0{width}d}{suffix}"
        return f"{prefix}{n}{suffix}"

    out: List[str] = []
    for a, b in ranges:
        if a == b:
            out.append(fmt(a))
        else:
            out.append(f"{fmt(a)} ~ {fmt(b)}")
    return out


def _write_sn_sequence_check(
    out_dir: Path,
    pn_product_prefix: str,
    raw_source_dir: str,
    records: List[LogRecord],
) -> Optional[Path]:
    """Write SN sequence continuity report into outfiles.

    Rule: within the range from first record to last record (by datetime),
    list missing SNs; if none missing, write 'SN為正常連續'.
    """
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
            f.write(f"Source Folder: {os.path.normpath(raw_source_dir)}\n")
            f.write("\n")
            f.write("Not enough numeric SN records to analyze.\n")
        return path

    parsed.sort(key=lambda x: x[0])
    first_dt, first_sn_text, first_prefix, _, _, first_suffix = parsed[0]
    last_dt, last_sn_text, last_prefix, _, _, last_suffix = parsed[-1]

    common_prefix = first_prefix if all(p[2] == first_prefix for p in parsed) else ""
    common_suffix = first_suffix if all(p[5] == first_suffix for p in parsed) else ""
    common_width = max((p[4] for p in parsed), default=0)

    nums_all = [p[3] for p in parsed]
    start_num = min(nums_all)
    end_num = max(nums_all)

    # For reference, pick the earliest record time for min/max numeric SN.
    min_dt = min((p[0] for p in parsed if p[3] == start_num), default=first_dt)
    max_dt = min((p[0] for p in parsed if p[3] == end_num), default=last_dt)

    def _fmt_sn(n: int) -> str:
        if common_width > 0:
            return f"{common_prefix}{n:0{common_width}d}{common_suffix}"
        return f"{common_prefix}{n}{common_suffix}"

    present_nums = {p[3] for p in parsed}
    expected_nums = list(range(start_num, end_num + 1))
    missing_nums = [n for n in expected_nums if n not in present_nums]

    with open(path, "w", encoding="utf-8") as f:
        f.write("SN Sequence Check\n")
        f.write(f"Report Time: {report_time_text}\n")
        f.write(f"Source Folder: {os.path.normpath(raw_source_dir)}\n")
        f.write("\n")
        f.write(f"First Record: {first_dt.strftime('%Y-%m-%d %H:%M:%S')}  SN={first_sn_text}\n")
        f.write(f"Last Record : {last_dt.strftime('%Y-%m-%d %H:%M:%S')}  SN={last_sn_text}\n")
        f.write(f"Min SN (numeric): {min_dt.strftime('%Y-%m-%d %H:%M:%S')}  SN={_fmt_sn(start_num)}\n")
        f.write(f"Max SN (numeric): {max_dt.strftime('%Y-%m-%d %H:%M:%S')}  SN={_fmt_sn(end_num)}\n")
        f.write(f"Range (min~max numeric): {start_num} ~ {end_num}\n")
        if common_prefix or common_suffix or common_width:
            f.write(f"SN Format: prefix='{common_prefix}', width={common_width}, suffix='{common_suffix}'\n")
        f.write(f"Present SN count (numeric): {len(present_nums)}\n")
        f.write(f"Expected SN count: {len(expected_nums)}\n")
        f.write(f"Missing SN count: {len(missing_nums)}\n")
        f.write("\n")

        if not missing_nums:
            f.write("SN為正常連續\n")
        else:
            f.write("Missing SN (by range):\n")
            for line in _format_missing_ranges(missing_nums, common_prefix, common_width, common_suffix):
                f.write(f"- {line}\n")

            f.write("\n")
            f.write("Missing SN (all):\n")
            for n in missing_nums:
                if common_width > 0:
                    f.write(f"{common_prefix}{n:0{common_width}d}{common_suffix}\n")
                else:
                    f.write(f"{common_prefix}{n}{common_suffix}\n")

    return path


def _outfiles_dir_for(base_dir: str) -> Path:
    base = Path(base_dir) if base_dir else Path(DEFAULT_DIR)
    return base / "outfiles"


def _format_generated_files_message(
    out_dir: Path,
    files: List[Path],
    summary_text: str = "",
    max_items: int = 30,
) -> str:
    out_dir_text = os.path.normpath(str(out_dir))

    summary_text = (summary_text or "").strip()

    if not files:
        if summary_text:
            return f"{summary_text}\n\nOutputs generated in outfiles folder.\nOutput: {out_dir_text}"
        return f"Outputs generated in outfiles folder.\nOutput: {out_dir_text}"

    files_sorted = sorted(files, key=lambda p: (p.suffix.lower(), p.name.lower()))
    shown = files_sorted[:max_items]
    lines: List[str] = []
    if summary_text:
        lines.extend([summary_text, ""])

    lines.extend([f"Outputs generated in outfiles folder.", f"Output: {out_dir_text}", "", "Generated/Updated files:"])
    lines.extend([os.path.normpath(str(p)) for p in shown])
    if len(files_sorted) > max_items:
        lines.append(f"... and {len(files_sorted) - max_items} more")
    return "\n".join(lines)


def _write_sn_attempt_summary_csv(
    out_dir: Path,
    records: List[LogRecord],
    production_name: str,
    safe_product_name: str,
    ts: str,
) -> Path:
    """Write per-SN attempt statistics CSV.

    Output columns:
            SN, Attempts, Retest, Terminated, Result, Final Result

    Notes:
      - SN is stored in LogRecord.mac.
            - Result: if any attempt PASS => PASS; else if any FAIL => FAIL; else TERMINATED.
            - Final result is the last attempt by datetime.
      - Retest = Attempts - 1.
    """

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

def run_gui() -> int:
    try:
        from PyQt5.QtCore import Qt, QTimer
        from PyQt5.QtGui import QFont
        from PyQt5.QtWidgets import (
            QApplication,
            QCheckBox,
            QGridLayout,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMessageBox,
            QProgressBar,
            QPushButton,
            QFileDialog,
            QVBoxLayout,
            QWidget,
        )
    except ModuleNotFoundError as e:
        print("PyQt5 is not installed in this Python environment.")
        print("Install it first. On Ubuntu 22.04 you can use either:")
        print("  sudo apt update")
        print("  sudo apt install -y python3-pyqt5 python3-pyqt5.qtsvg")
        print("Or via pip:")
        print("  python3 -m pip install PyQt5")
        print(f"Details: {e}")
        return 2

    # QtSvg is optional (logo only). Keep the app running even if it's missing.
    try:
        from PyQt5.QtSvg import QSvgWidget  # type: ignore
    except ModuleNotFoundError:
        QSvgWidget = None  # type: ignore

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
                # Run after UI is constructed so status label is ready.
                QTimer.singleShot(150, self._run_initial_db_connection_check)
                self._start_db_heartbeat_timer()

        def _reset_state_for_new_input(self):
            self.records = []
            self.raw_records = []

            self.total_label.setText("Total: 0")
            self.pass_label.setText("PASS: 0 (0.0%)")
            self.fail_label.setText("FAIL(+TERM): 0 (0.0%)")
            self.term_label.setText("TERMINATED: 0 (0.0%)")

            if hasattr(self, "report_csv_btn"):
                # One-click flow: allow Report/CSV immediately after selecting Log Folder.
                self.report_csv_btn.setEnabled(bool(self._log_dir_user_selected))
            if hasattr(self, "db_upload_btn"):
                self.db_upload_btn.setEnabled(bool(self._log_dir_user_selected))

        def _try_load_filename_sorter(self) -> bool:
            # Import lazily so the original UI can still run even if the helper file is missing.
            try:
                import sort_filenames_to_csv as sn_csv  # type: ignore
            except Exception:
                self._sn_csv = None
                return False

            self._sn_csv = sn_csv
            return True

        def _apply_result_styles(self):
            # Larger typography + result highlighting.
            self.total_label.setStyleSheet("font-weight: bold; font-size: 16px; color: #2c3e50;")
            self.pass_label.setStyleSheet("font-weight: bold; font-size: 16px; color: #27ae60;")
            self.fail_label.setStyleSheet("font-weight: bold; font-size: 16px; color: #e74c3c;")
            self.term_label.setStyleSheet("font-weight: bold; font-size: 16px; color: #8e44ad;")

        def init_ui(self):
            self.setWindowTitle(f"{APP_WINDOW_TITLE} - (v{APP_VERSION})")
            self.setGeometry(120, 120, 1000, 560)
            self.setStyleSheet("QMainWindow { background-color: #f0f0f0; }")

            main_widget = QWidget()
            self.setCentralWidget(main_widget)
            main_layout = QVBoxLayout()
            main_widget.setLayout(main_layout)

            title_layout = QHBoxLayout()

            logo_path = os.path.join(APP_DIR, "technexion_logo.svg")
            if QSvgWidget is not None and os.path.exists(logo_path):
                logo_widget = QSvgWidget(logo_path)
                logo_widget.setFixedSize(200, 30)
                title_layout.addWidget(logo_widget)
            else:
                logo_placeholder = QLabel("")
                logo_placeholder.setFixedWidth(200)
                title_layout.addWidget(logo_placeholder)

            title_label = QLabel(APP_HEADER_TITLE)
            title_font = QFont("Arial", 18, QFont.Bold)
            title_label.setFont(title_font)
            title_label.setAlignment(Qt.AlignCenter)
            title_label.setStyleSheet("color: #2c3e50; padding: 10px;")
            title_layout.addWidget(title_label, 1)

            right_spacer = QLabel("")
            right_spacer.setFixedWidth(200)
            title_layout.addWidget(right_spacer)

            main_layout.addLayout(title_layout)

            main_layout.addWidget(self._build_log_analysis_group())
            main_layout.addWidget(self._build_report_group())
            if DB_UPLOAD_AVAILABLE:
                main_layout.addWidget(self._build_db_upload_group())
            main_layout.addStretch(1)

        def _build_db_upload_group(self):
            try:
                from PyQt5.QtWidgets import QCheckBox, QGroupBox, QGridLayout, QLabel, QLineEdit, QPushButton, QProgressBar
            except ImportError:
                pass
            group = QGroupBox("Database Upload (log_parser)")
            group.setStyleSheet(self._groupbox_style())
            layout = QGridLayout()
            group.setLayout(layout)

            layout.addWidget(QLabel("Server IP:"), 0, 0)
            self.db_server_ip_input = QLineEdit()
            self.db_server_ip_input.setText("10.20.31.40")
            self.db_server_ip_input.setMinimumHeight(32)
            self.db_server_ip_input.textChanged.connect(self._on_server_ip_changed)
            layout.addWidget(self.db_server_ip_input, 0, 1, 1, 2)

            layout.addWidget(QLabel("PostgreSQL DSN:"), 1, 0)
            self.dsn_input = QLineEdit()
            self.dsn_input.setText(self._build_dsn_from_server_ip())
            self.dsn_input.setMinimumHeight(32)
            self.dsn_input.setReadOnly(True)
            self.dsn_input.setStyleSheet(
                "QLineEdit { background-color: #ecf0f1; color: #7f8c8d; }"
            )
            layout.addWidget(self.dsn_input, 1, 1, 1, 2)

            self.db_conn_status_label = QLabel("DB connection: not checked")
            self.db_conn_status_label.setStyleSheet("color: #7f8c8d;")
            layout.addWidget(self.db_conn_status_label, 2, 1, 1, 2)

            self.db_upload_btn = QPushButton("Upload to DB")
            self.db_upload_btn.clicked.connect(self.on_upload_to_db)
            self.db_upload_btn.setMinimumHeight(38)
            self.db_upload_btn.setStyleSheet(self._btn_style_blue())
            
            # Start disabled, enabled when log folder is chosen
            self.db_upload_btn.setEnabled(False)
            layout.addWidget(self.db_upload_btn, 3, 2)

            self.db_cancel_btn = QPushButton("Cancel Upload")
            self.db_cancel_btn.clicked.connect(self.on_cancel_db_upload)
            self.db_cancel_btn.setMinimumHeight(38)
            self.db_cancel_btn.setStyleSheet(self._btn_style_gray())
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
            self.db_upload_status.setStyleSheet("color: #2c3e50;")
            layout.addWidget(self.db_upload_status, 6, 0, 1, 3)

            self.db_upload_sanitize_status = QLabel("NUL sanitize: files=0, bytes=0")
            self.db_upload_sanitize_status.setStyleSheet("color: #7f8c8d;")
            layout.addWidget(self.db_upload_sanitize_status, 7, 0, 1, 3)

            self.db_upload_policy = QLabel("Duplicate policy: SKIP (same file_hash will not overwrite existing DB row)")
            self.db_upload_policy.setStyleSheet("color: #7f8c8d;")
            layout.addWidget(self.db_upload_policy, 8, 0, 1, 3)
            
            return group

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
                self.db_conn_status_label.setStyleSheet("color: #27ae60;")
                return

            if state == "fail":
                suffix = f" ({detail})" if detail else ""
                self.db_conn_status_label.setText(f"DB connection: disconnected{suffix}")
                self.db_conn_status_label.setStyleSheet("color: #e74c3c;")
                return

            self.db_conn_status_label.setText("DB connection: not checked")
            self.db_conn_status_label.setStyleSheet("color: #7f8c8d;")

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
            # Heartbeat only updates connection indicator to avoid overriding upload progress text.
            self._run_db_connection_check(update_upload_status=False)

        def _run_initial_db_connection_check(self):
            # Initial check updates both the connection indicator and upload status text.
            self._run_db_connection_check(update_upload_status=True)

        def _run_db_connection_check(self, update_upload_status: bool = False):
            if not hasattr(self, "db_server_ip_input"):
                return

            server_ip = self.db_server_ip_input.text().strip()
            if not server_ip:
                self._set_db_connection_status("fail", "empty server ip")
                if update_upload_status and hasattr(self, "db_upload_status"):
                    self.db_upload_status.setText("Upload status: DB connection check failed (empty server ip)")
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
            if hasattr(self, "db_upload_sanitize_status"):
                # Keep latest counters visible while the request winds down.
                self.db_upload_sanitize_status.setText(self.db_upload_sanitize_status.text())

        def _set_db_upload_status(
            self,
            processed: int,
            total_files: int,
            inserted: int,
            duplicates: int,
            errors: int,
            sanitized_files: int,
            sanitized_nul_bytes: int,
            state: str = "processed",
        ):
            status = (
                f"Upload status: {state} ({processed}/{total_files}, inserted={inserted}, "
                f"dup={duplicates}, err={errors})"
            )
            self.db_upload_status.setText(status)
            if hasattr(self, "db_upload_sanitize_status"):
                self.db_upload_sanitize_status.setText(
                    f"NUL sanitize: files={sanitized_files}, bytes={sanitized_nul_bytes}"
                )

        def _write_upload_logs(
            self,
            log_dir: str,
            entries: List[Tuple[int, str, str, str]],
            summary: str,
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

        def _load_processed_filenames_from_last_upload_log(self, log_dir: str) -> set[str]:
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

            processed: set[str] = set()
            try:
                with open(latest_csv, "r", encoding="utf-8-sig", newline="") as f:
                    r = csv.DictReader(f)
                    for row in r:
                        filename = (row.get("Filename") or "").strip()
                        status = (row.get("Status") or "").strip().lower()
                        if not filename:
                            continue
                        # Resume should skip only files that were fully attempted.
                        if status in {"inserted", "duplicate", "error"}:
                            processed.add(filename)
            except Exception:
                return set()

            return processed

        def _groupbox_style(self) -> str:
            return (
                """
                QGroupBox {
                    font-weight: bold;
                    border: 2px solid #3498db;
                    border-radius: 5px;
                    margin-top: 10px;
                    padding-top: 10px;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    left: 10px;
                    padding: 0 5px;
                }
                """
            )

        def _btn_style_gray(self) -> str:
            return (
                """
                QPushButton {
                    background-color: #95a5a6;
                    color: white;
                    border: none;
                    padding: 6px 16px;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background-color: #7f8c8d;
                }
                """
            )

        def _btn_style_blue(self) -> str:
            return (
                """
                QPushButton {
                    background-color: #3498db;
                    color: white;
                    font-size: 12px;
                    font-weight: bold;
                    border: none;
                    border-radius: 5px;
                }
                QPushButton:hover {
                    background-color: #2980b9;
                }
                QPushButton:pressed {
                    background-color: #1f5f8b;
                }
                """
            )

        def _btn_style_green(self) -> str:
            return (
                """
                QPushButton {
                    background-color: #27ae60;
                    color: white;
                    font-size: 12px;
                    font-weight: bold;
                    border: none;
                    border-radius: 5px;
                }
                QPushButton:hover {
                    background-color: #1f8f4f;
                }
                QPushButton:pressed {
                    background-color: #18703d;
                }
                """
            )

        def _btn_style_purple(self) -> str:
            return (
                """
                QPushButton {
                    background-color: #8e44ad;
                    color: white;
                    font-size: 12px;
                    font-weight: bold;
                    border: none;
                    border-radius: 5px;
                }
                QPushButton:hover {
                    background-color: #7d3c98;
                }
                QPushButton:pressed {
                    background-color: #5b2c6f;
                }
                """
            )

        def _build_log_analysis_group(self):
            input_group = QGroupBox("Log Analysis")
            input_group.setStyleSheet(self._groupbox_style())

            grid = QGridLayout()
            input_group.setLayout(grid)

            grid.addWidget(QLabel("Work Order:"), 0, 0)
            self.work_order_input = QLineEdit()
            self.work_order_input.setPlaceholderText("5101-260108007")
            self.work_order_input.setMinimumHeight(34)
            self.work_order_input.setStyleSheet(
                """
                QLineEdit {
                    padding: 6px;
                    border: 1px solid #cccccc;
                    border-radius: 4px;
                    font-size: 12pt;
                }
                QLineEdit::placeholder {
                    color: #999999;
                }
                """
            )
            grid.addWidget(self.work_order_input, 0, 1, 1, 2)

            grid.addWidget(QLabel("Product Name:"), 1, 0)
            self.production_name_input = QLineEdit()
            self.production_name_input.setPlaceholderText("TNXXX-XXXX-XXXXXX")
            self.production_name_input.setMinimumHeight(34)
            self.production_name_input.setStyleSheet(
                """
                QLineEdit {
                    padding: 6px;
                    border: 1px solid #cccccc;
                    border-radius: 4px;
                    font-size: 12pt;
                }
                QLineEdit::placeholder {
                    color: #999999;
                }
                """
            )
            grid.addWidget(self.production_name_input, 1, 1, 1, 2)

            grid.addWidget(QLabel("Log Folder:"), 2, 0)
            self.log_dir_display = QLineEdit()
            self.log_dir_display.setReadOnly(True)
            self.log_dir_display.setPlaceholderText("Select folder that contains log .txt files")
            self.log_dir_display.setText(DEFAULT_DIR)
            self.log_dir_display.setMinimumHeight(32)
            grid.addWidget(self.log_dir_display, 2, 1)

            browse_log_btn = QPushButton("Browse")
            browse_log_btn.clicked.connect(self.on_browse_log_dir)
            browse_log_btn.setStyleSheet(self._btn_style_gray())
            grid.addWidget(browse_log_btn, 2, 2)

            self.parse_btn = QPushButton("Parse")
            self.parse_btn.clicked.connect(self.on_parse)
            self.parse_btn.setMinimumHeight(38)
            self.parse_btn.setStyleSheet(self._btn_style_blue())
            grid.addWidget(self.parse_btn, 3, 2)

            self.total_label = QLabel("Total: 0")
            self.pass_label = QLabel("PASS: 0 (0.0%)")
            self.fail_label = QLabel("FAIL: 0 (0.0%)")
            self.term_label = QLabel("TERMINATED: 0 (0.0%)")

            self._apply_result_styles()

            grid.addWidget(self.total_label, 3, 0)
            grid.addWidget(self.pass_label, 3, 1)
            grid.addWidget(self.fail_label, 4, 1)
            grid.addWidget(self.term_label, 4, 0)

            return input_group

        def _build_report_group(self):
            report_group = QGroupBox("Report")
            report_group.setStyleSheet(self._groupbox_style())

            report_layout = QGridLayout()
            report_group.setLayout(report_layout)

            report_layout.addWidget(QLabel("Report Output Folder:"), 0, 0)
            self.report_dir_display = QLineEdit()
            self.report_dir_display.setReadOnly(True)
            self.report_dir_display.setPlaceholderText("Auto: <Log Folder>/outfiles")
            self.report_dir_display.setText(os.path.join(DEFAULT_DIR, "outfiles"))
            self.report_dir_display.setMinimumHeight(32)
            report_layout.addWidget(self.report_dir_display, 0, 1, 1, 2)

            self.report_csv_btn = QPushButton("Generate Report + SN Summary")
            self.report_csv_btn.clicked.connect(self.on_report)
            self.report_csv_btn.setMinimumHeight(38)
            self.report_csv_btn.setStyleSheet(self._btn_style_green())
            self.report_csv_btn.setEnabled(False)
            report_layout.addWidget(self.report_csv_btn, 1, 1, 1, 2)

            return report_group

        def _build_sn_sort_group(self):
            group = QGroupBox("Filename Sort to CSV (SN in order)")
            group.setStyleSheet(self._groupbox_style())

            layout = QGridLayout()
            group.setLayout(layout)

            layout.addWidget(QLabel("Source Folder (.txt):"), 0, 0)
            self.sn_sort_input_dir = QLineEdit()
            self.sn_sort_input_dir.setReadOnly(True)
            self.sn_sort_input_dir.setPlaceholderText("Select folder containing log .txt files")
            self.sn_sort_input_dir.setText(DEFAULT_DIR)
            self.sn_sort_input_dir.setMinimumHeight(32)
            layout.addWidget(self.sn_sort_input_dir, 0, 1)

            browse_in_btn = QPushButton("Browse")
            browse_in_btn.clicked.connect(self.on_browse_sn_sort_input_dir)
            browse_in_btn.setStyleSheet(self._btn_style_gray())
            layout.addWidget(browse_in_btn, 0, 2)

            layout.addWidget(QLabel("Or Input CSV:"), 1, 0)
            self.sn_sort_input_csv = QLineEdit()
            self.sn_sort_input_csv.setReadOnly(True)
            self.sn_sort_input_csv.setPlaceholderText("Optional: existing all_sn_in_order.csv")
            self.sn_sort_input_csv.setMinimumHeight(32)
            layout.addWidget(self.sn_sort_input_csv, 1, 1)

            browse_csv_btn = QPushButton("Browse")
            browse_csv_btn.clicked.connect(self.on_browse_sn_sort_input_csv)
            browse_csv_btn.setStyleSheet(self._btn_style_gray())
            layout.addWidget(browse_csv_btn, 1, 2)

            layout.addWidget(QLabel("Output Folder:"), 2, 0)
            self.sn_sort_output_dir = QLineEdit()
            self.sn_sort_output_dir.setReadOnly(True)
            self.sn_sort_output_dir.setPlaceholderText("Auto: <Source Folder>/outfiles")
            self.sn_sort_output_dir.setText(os.path.join(DEFAULT_DIR, "outfiles"))
            self.sn_sort_output_dir.setMinimumHeight(32)
            layout.addWidget(self.sn_sort_output_dir, 2, 1)

            self.sn_sort_btn = QPushButton("Generate CSVs")
            self.sn_sort_btn.clicked.connect(self.on_generate_sn_csvs)
            self.sn_sort_btn.setMinimumHeight(38)
            self.sn_sort_btn.setStyleSheet(self._btn_style_purple())
            layout.addWidget(self.sn_sort_btn, 3, 2)

            if not self._csv_sorter_available:
                hint = QLabel("NOTE: sort_filenames_to_csv.py not available. This feature is disabled.")
                hint.setStyleSheet("color: #b03a2e;")
                layout.addWidget(hint, 3, 0, 1, 2)
                self.sn_sort_btn.setEnabled(False)

            return group

        def on_browse_log_dir(self):
            try:
                from PyQt5.QtWidgets import QFileDialog
            except ImportError:
                pass
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
                out_dir = _outfiles_dir_for(folder)
                self.report_dir_display.setText(os.path.normpath(str(out_dir)))

        def on_browse_sn_sort_input_dir(self):
            initial_dir = self.sn_sort_input_dir.text().strip() or _default_browse_dir()
            folder = QFileDialog.getExistingDirectory(self, "Select Source Folder", initial_dir)
            if folder:
                self.sn_sort_input_dir.setText(folder)
                out_dir = _outfiles_dir_for(folder)
                self.sn_sort_output_dir.setText(os.path.normpath(str(out_dir)))

        def on_browse_sn_sort_input_csv(self):
            initial_dir = self.sn_sort_input_dir.text().strip() or _default_browse_dir()
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Select Input CSV",
                initial_dir,
                "CSV Files (*.csv);;All Files (*.*)",
            )
            if path:
                self.sn_sort_input_csv.setText(path)

        def on_generate_sn_csvs(self):
            if not self._csv_sorter_available:
                QMessageBox.warning(self, "SN CSV", "sort_filenames_to_csv.py is not available.")
                return

            base_dir = self.sn_sort_input_dir.text().strip() or self.log_dir_display.text().strip()
            out_dir_path = _outfiles_dir_for(base_dir)
            out_dir_path.mkdir(parents=True, exist_ok=True)
            self.sn_sort_output_dir.setText(os.path.normpath(str(out_dir_path)))

            input_csv_text = self.sn_sort_input_csv.text().strip()
            input_dir_text = self.sn_sort_input_dir.text().strip()

            production_name = (
                self.production_name_input.text().strip()
                or self.production_name_input.placeholderText().strip()
                or "UNKNOWN"
            )
            pn_product_prefix = f"{_safe_name(production_name)}_"

            try:
                output_dir = out_dir_path
                if input_csv_text:
                    input_csv = Path(input_csv_text)
                    if not input_csv.exists() or not input_csv.is_file():
                        QMessageBox.warning(self, "SN CSV", f"Input CSV not found: {input_csv}")
                        return

                    self._sn_csv.generate_reports_from_sn_in_order_csv(input_csv, output_dir, prefix=pn_product_prefix)
                    QMessageBox.information(
                        self,
                        "SN CSV",
                        "Summary CSVs generated.\n"
                        f"Input CSV: {os.path.normpath(str(input_csv))}\n"
                        f"Output: {os.path.normpath(str(output_dir))}",
                    )
                    return

                if not input_dir_text or not os.path.isdir(input_dir_text):
                    QMessageBox.warning(self, "SN CSV", "Please select Source Folder or Input CSV.")
                    return

                input_dir = Path(input_dir_text)

                records: list = []
                skipped: list[str] = []
                for file_path in self._sn_csv.iter_txt_files(input_dir):
                    rec = self._sn_csv.parse_filename(file_path)
                    if rec is None:
                        skipped.append(file_path.name)
                        continue
                    records.append(rec)

                records.sort(key=self._sn_csv.sort_key)

                all_csv_path = output_dir / f"{pn_product_prefix}all_sn_in_order.csv"
                actual_all_csv = self._sn_csv.write_csv(records, all_csv_path)

                # Generate additional report CSVs next to the full list.
                self._sn_csv.generate_reports_from_sn_in_order_csv(actual_all_csv, output_dir, prefix=pn_product_prefix)

                msg = (
                    "Generated SN CSVs.\n"
                    f"{pn_product_prefix}all_sn_in_order.csv: {os.path.normpath(str(actual_all_csv))}\n"
                    f"Output folder: {os.path.normpath(str(output_dir))}"
                )
                if skipped:
                    msg += f"\nSkipped: {len(skipped)} (unexpected filename format)"

                QMessageBox.information(self, "SN CSV", msg)

            except Exception as e:
                QMessageBox.critical(self, "SN CSV", f"Failed to generate SN CSVs.\n{e}")

        def on_upload_to_db(self):
            try:
                from PyQt5.QtWidgets import QMessageBox
            except ImportError:
                pass
            if not DB_UPLOAD_AVAILABLE:
                QMessageBox.warning(self, "Database Upload", "psycopg2 or log_parser module not found. Upload functionality disabled.")
                return

            log_dir = self.log_dir_display.text().strip()
            if not log_dir:
                QMessageBox.warning(self, "Database Upload", "Please select Log Folder first.")
                return

            product_name = self.production_name_input.text().strip()
            work_order = self.work_order_input.text().strip()
            if not product_name or not work_order:
                QMessageBox.warning(
                    self,
                    "Database Upload",
                    "Product Name and Work Order are required before upload.",
                )
                return

            server_ip = self.db_server_ip_input.text().strip() if hasattr(self, "db_server_ip_input") else ""
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
            self.repaint() # Force UI update

            try:
                # Local imports
                import log_parser
                import psycopg2

                files = log_parser.scan_directory(log_dir)
                if not files:
                    self.db_upload_status.setText("Upload status: no matching files")
                    QMessageBox.information(self, "Database Upload", "No matching log files found.")
                    return

                if hasattr(self, "db_resume_checkbox") and self.db_resume_checkbox.isChecked():
                    processed = self._load_processed_filenames_from_last_upload_log(log_dir)
                    if processed:
                        files = [f for f in files if os.path.basename(str(f)) not in processed]
                        if not files:
                            self.db_upload_status.setText("Upload status: no pending files (resume mode)")
                            QMessageBox.information(
                                self,
                                "Database Upload",
                                "Resume mode enabled. No pending files were found to upload.",
                            )
                            return

                ok = skip = err = 0
                sanitized_files = 0
                sanitized_nul_bytes_total = 0
                cancelled = False
                upload_log_entries: List[Tuple[int, str, str, str]] = []
                total_files = len(files)
                self.db_upload_progress.setMinimum(0)
                self.db_upload_progress.setMaximum(total_files)
                self.db_upload_progress.setValue(0)
                self._set_db_upload_status(0, total_files, ok, skip, err, sanitized_files, sanitized_nul_bytes_total)

                with psycopg2.connect(dsn) as conn:
                    for idx, f in enumerate(files, start=1):
                        QApplication.processEvents()
                        if self._db_upload_cancel_requested:
                            cancelled = True
                            upload_log_entries.append((idx, os.path.basename(str(f)), "cancelled", "User requested cancellation"))
                            break

                        record = log_parser.ingest_file(f)
                        if not record:
                            err += 1
                            upload_log_entries.append((idx, os.path.basename(str(f)), "error", "Parse returned empty record"))
                            self.db_upload_progress.setValue(idx)
                            self._set_db_upload_status(
                                idx,
                                total_files,
                                ok,
                                skip,
                                err,
                                sanitized_files,
                                sanitized_nul_bytes_total,
                            )
                            QApplication.processEvents()
                            continue

                        # GUI fields are the source of truth for production metadata.
                        record["product_model"] = product_name
                        record["work_order"] = work_order
                        sanitized_nul_bytes = int(record.get("sanitized_nul_bytes", 0) or 0)
                        if sanitized_nul_bytes > 0:
                            sanitized_files += 1
                            sanitized_nul_bytes_total += sanitized_nul_bytes

                        detail_suffix = ""
                        if sanitized_nul_bytes > 0:
                            detail_suffix = f"; sanitized_nul_bytes={sanitized_nul_bytes}"

                        try:
                            # Tool behavior is fixed by requirement: duplicate rows are skipped, never overwritten.
                            if DB_DUPLICATE_POLICY != "skip":
                                raise RuntimeError("Unsupported duplicate policy for GUI uploader")
                            rid = log_parser.write_record(conn, record)
                            if rid:
                                ok += 1
                                upload_log_entries.append((idx, os.path.basename(str(f)), "inserted", f"record_id={rid}{detail_suffix}"))
                            else:
                                skip += 1
                                upload_log_entries.append((idx, os.path.basename(str(f)), "duplicate", f"Duplicate or no-op insert{detail_suffix}"))
                        except Exception as e:
                            print(f"[ERR] DB Insert Failed {f}: {e}")
                            err += 1
                            upload_log_entries.append((idx, os.path.basename(str(f)), "error", f"DB insert failed: {e}{detail_suffix}"))

                        self.db_upload_progress.setValue(idx)
                        self._set_db_upload_status(
                            idx,
                            total_files,
                            ok,
                            skip,
                            err,
                            sanitized_files,
                            sanitized_nul_bytes_total,
                        )
                        QApplication.processEvents()

                processed_count = ok + skip + err
                if cancelled:
                    self._set_db_upload_status(
                        processed_count,
                        total_files,
                        ok,
                        skip,
                        err,
                        sanitized_files,
                        sanitized_nul_bytes_total,
                        state="cancelled",
                    )
                else:
                    self._set_db_upload_status(
                        processed_count,
                        total_files,
                        ok,
                        skip,
                        err,
                        sanitized_files,
                        sanitized_nul_bytes_total,
                        state="completed",
                    )

                summary_text = (
                    f"processed={processed_count}/{total_files}, inserted={ok}, duplicates={skip}, errors={err}, "
                    f"sanitized_files={sanitized_files}, sanitized_nul_bytes={sanitized_nul_bytes_total}, cancelled={cancelled}"
                )
                csv_log_path, txt_log_path = self._write_upload_logs(log_dir, upload_log_entries, summary_text)

                sanitize_note = ""
                if sanitized_files > 0:
                    sanitize_note = (
                        f"NUL bytes sanitized: {sanitized_nul_bytes_total} across {sanitized_files} files\n\n"
                    )

                if cancelled:
                    QMessageBox.information(
                        self,
                        "Database Upload",
                        "Upload cancelled by user.\n\n"
                        f"Found: {len(files)} files\n"
                        f"Processed: {processed_count}\n"
                        f"Inserted: {ok}\n"
                        f"Duplicates Skipped: {skip}\n"
                        f"Errors: {err}\n"
                        f"{sanitize_note}"
                        f"Upload Log CSV: {os.path.normpath(str(csv_log_path))}\n"
                        f"Upload Log TXT: {os.path.normpath(str(txt_log_path))}"
                    )
                else:
                    QMessageBox.information(
                        self,
                        "Database Upload",
                        "Upload complete!\n\n"
                        f"Found: {len(files)} files\n"
                        f"Processed: {processed_count}\n"
                        f"Inserted: {ok}\n"
                        f"Duplicates Skipped: {skip}\n"
                        f"Errors: {err}\n"
                        f"{sanitize_note}"
                        f"Upload Log CSV: {os.path.normpath(str(csv_log_path))}\n"
                        f"Upload Log TXT: {os.path.normpath(str(txt_log_path))}"
                    )

            except Exception as e:
                self.db_upload_status.setText("Upload status: failed")
                QMessageBox.critical(self, "Database Upload", f"Upload failed:\n{e}")
            finally:
                self.db_upload_btn.setText("Upload to DB")
                self.db_upload_btn.setEnabled(True)
                self.db_cancel_btn.setEnabled(False)

        def on_parse(self):
            log_dir = self.log_dir_display.text().strip()
            if not log_dir:
                QMessageBox.warning(self, "Parse", "Please select Log Folder first.")
                return

            raw = parse_log_directory_raw(log_dir)
            deduped = _dedupe_keep_latest_by_sn(raw)
            self.raw_records = raw
            self.records = deduped

            pass_count = sum(1 for r in deduped if r.result == "PASS")
            fail_count = sum(1 for r in deduped if r.result == "FAIL")
            term_count = sum(1 for r in deduped if r.result == "TERMINATED")
            total = pass_count + fail_count + term_count

            fail_including_terminated = fail_count + term_count

            self.total_label.setText(f"Total: {total}")
            self.pass_label.setText(f"PASS: {pass_count} ({_ratio_text(pass_count, total)})")
            self.fail_label.setText(
                f"FAIL(+TERM): {fail_including_terminated} ({_ratio_text(fail_including_terminated, total)})"
            )
            self.term_label.setText(f"TERMINATED: {term_count} ({_ratio_text(term_count, total)})")

            # Successful parse means the log folder is confirmed.
            self._log_dir_user_selected = True
            if hasattr(self, "report_csv_btn"):
                self.report_csv_btn.setEnabled(True)

            QMessageBox.information(
                self,
                "Parse",
                f"Parse completed.\nTotal: {total}\nPASS: {pass_count}\nFAIL: {fail_count}\nTERMINATED: {term_count}",
            )

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
                    w.writerow([production_name, r.test_date, r.test_time, r.mac, r.serial, r.result, r.filename])

            # Per-SN attempt statistics CSV.
            raw_source_for_stats = self.raw_records if self.raw_records else self.records
            _write_sn_attempt_summary_csv(out_dir, raw_source_for_stats, production_name, safe_name, ts)

            total = len(self.records)
            pass_count = sum(1 for r in self.records if r.result == "PASS")
            fail_count = sum(1 for r in self.records if r.result == "FAIL")
            term_count = sum(1 for r in self.records if r.result == "TERMINATED")

            dates = sorted({r.test_date for r in self.records})
            if not dates:
                test_date_text = datetime.now().strftime("%Y-%m-%d")
            elif len(dates) == 1:
                test_date_text = dates[0]
            else:
                test_date_text = f"{dates[0]} ~ {dates[-1]}"

            report_time_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            def _ratio_text_2(count: int, total_count: int) -> str:
                if total_count <= 0:
                    return "0.00%"
                return f"{(count / total_count) * 100.0:.2f}%"

            with open(txt_path, "w", encoding="utf-8") as f:
                f.write("WiFi Yield Report\n")
                f.write(f"Product: {production_name}\n")
                f.write(f"Test Date: {test_date_text}\n")
                f.write(f"Report Time: {report_time_text}\n")
                f.write(f"Total Tests: {total}\n")
                f.write(f"PASS Count: {pass_count}, PASS Rate: {_ratio_text_2(pass_count, total)}\n")
                fail_including_terminated = fail_count + term_count
                f.write(
                    f"FAIL Count: {fail_including_terminated}, FAIL Rate: {_ratio_text_2(fail_including_terminated, total)}\n"
                )
                f.write(f"(FAIL-only) Count: {fail_count}, Rate: {_ratio_text_2(fail_count, total)}\n")
                f.write(f"TERMINATED Count: {term_count}, TERMINATED Rate: {_ratio_text_2(term_count, total)}\n")

                raw_source = self.raw_records if self.raw_records else self.records
                by_sn: dict[str, List[LogRecord]] = {}
                for r in raw_source:
                    by_sn.setdefault(r.mac, []).append(r)

                retest_items = [(sn, sorted(items, key=lambda x: x.dt)) for sn, items in by_sn.items() if len(items) > 1]
                retest_items.sort(key=lambda x: x[0])

                total_retests = sum(len(items) - 1 for _, items in retest_items)

                f.write("\n")
                f.write("Retest Details (grouped by SN)\n")
                f.write(f"Total Retests (exclude final): {total_retests}\n")
                if not retest_items:
                    f.write("No retest records found.\n")
                else:
                    for sn, items in retest_items:
                        results_seq = " -> ".join(
                            f"{i.result}@{i.test_date} {i.test_time}" for i in items
                        )
                        f.write(
                            f"SN: {sn}, Attempts: {len(items)}, Retests: {len(items) - 1}, Results: {results_seq}\n"
                        )

            # Also generate SN-order CSVs into outfiles (if helper exists and input folder looks valid).
            if self._csv_sorter_available:
                input_dir = Path(log_dir)
                production_name = (
                    self.production_name_input.text().strip()
                    or self.production_name_input.placeholderText().strip()
                    or "UNKNOWN"
                )
                pn_product_prefix = f"{_safe_name(production_name)}_"

                records: list = []
                for file_path in self._sn_csv.iter_txt_files(input_dir):
                    rec = self._sn_csv.parse_filename(file_path)
                    if rec is None:
                        continue
                    records.append(rec)

                records.sort(key=self._sn_csv.sort_key)
                all_csv_path = out_dir / f"{pn_product_prefix}all_sn_in_order.csv"
                actual_all_csv = self._sn_csv.write_csv(records, all_csv_path)
                self._sn_csv.generate_reports_from_sn_in_order_csv(actual_all_csv, out_dir, prefix=pn_product_prefix)

            # SN sequence continuity check (always attempt; based on parsed log records).
            production_name = (
                self.production_name_input.text().strip()
                or self.production_name_input.placeholderText().strip()
                or "UNKNOWN"
            )
            pn_product_prefix = f"{_safe_name(production_name)}_"
            raw_source = self.raw_records if self.raw_records else self.records
            _write_sn_sequence_check(out_dir, pn_product_prefix, log_dir, raw_source)

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

        def on_report(self):
            log_dir = self.log_dir_display.text().strip()
            if not log_dir:
                QMessageBox.warning(self, "Report/CSV", "Please select Log Folder first.")
                return

            # One-click: auto-parse before generating outputs.
            raw = parse_log_directory_raw(log_dir)
            deduped = _dedupe_keep_latest_by_sn(raw)
            self.raw_records = raw
            self.records = deduped

            pass_count = sum(1 for r in deduped if r.result == "PASS")
            fail_count = sum(1 for r in deduped if r.result == "FAIL")
            term_count = sum(1 for r in deduped if r.result == "TERMINATED")
            total = pass_count + fail_count + term_count

            fail_including_terminated = fail_count + term_count
            self.total_label.setText(f"Total: {total}")
            self.pass_label.setText(f"PASS: {pass_count} ({_ratio_text(pass_count, total)})")
            self.fail_label.setText(
                f"FAIL(+TERM): {fail_including_terminated} ({_ratio_text(fail_including_terminated, total)})"
            )
            self.term_label.setText(f"TERMINATED: {term_count} ({_ratio_text(term_count, total)})")

            # Confirm log folder state.
            self._log_dir_user_selected = True
            if hasattr(self, "report_csv_btn"):
                self.report_csv_btn.setEnabled(True)

            try:
                out_dir, generated_files = self._generate_all_outputs_from_current_state()
            except Exception as e:
                QMessageBox.critical(self, "Report/CSV", f"Failed to generate outputs.\n{e}")
                return

            summary_text = (
                "Summary: "
                f"Total={total} | "
                f"PASS={pass_count} ({_ratio_text(pass_count, total)}) | "
                f"FAIL(+TERM)={fail_including_terminated} ({_ratio_text(fail_including_terminated, total)}) | "
                f"TERMINATED={term_count} ({_ratio_text(term_count, total)})"
            )

            QMessageBox.information(
                self,
                "Report/CSV",
                _format_generated_files_message(out_dir, generated_files, summary_text=summary_text),
            )

    import sys

    app = QApplication(sys.argv)
    win = WiFiStressLogAnalyzer()
    win.show()
    return app.exec_()


def main() -> int:
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
