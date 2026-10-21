"""FAIL log classifier (5F-1) — pure functions (no database), unit-tested on the host.

parse(log)            → the signals of one test log: station (port, SSID group, target SSIDs), per-band attempts
                        (Mbps, threshold, RSSI, no-data), band results, Bluetooth results, summary lines
classify(log, flags)  → {category, secondary, band, measures, evidence, station…} for a FAIL record

Only FAIL-specific signals count. Lines that also appear in PASS logs ("wlan0 interface not found. Loading
driver", "No lease", kernel driver warnings, X server messages…) are never a cause (Final_Improvement_Plan §2.9).
Codes are the rows of fail_taxonomy; the most severe one is the category, the rest are secondary.
"""
import re
from typing import Any, Optional

CLASSIFIER_VERSION = 1
SHORT_LOG = 5000          # a log shorter than this has no complete test in it

# category order = severity (first = most severe) when one record has several
SEVERITY = ["BT_DEV_NOT_FOUND", "WIFI_ASSOC_FAIL", "WIFI_TP_LOW_24G", "WIFI_TP_LOW_5G", "BT_PING_FAIL",
            "WIFI_NO_TP_DATA", "LOG_INCOMPLETE", "UNKNOWN"]

BAND_START = re.compile(r"Starting (2\.4G|5G) Band Test")
BAND_INFO = re.compile(r"WiFi Connection Information - (2\.4G|5G) Band")
THRESHOLD = re.compile(r"^\s*Throughput Threshold:\s*([\d.]+)\s*MBits/sec", re.I)
TARGET = re.compile(r"^\s*Target SSID:\s*(\S+)")
RESULT = re.compile(r"Result:\s*(PASSED|FAILED)\s*\(([\d.]+)\s*MBits/sec\s*([<>]=?)\s*([\d.]+)\s*MBits/sec\)"
                    r"(?:\s*-\s*RSSI after test:\s*(-?\d+)\s*dBm)?")
NO_DATA = re.compile(r"Result:\s*FAILED\s*\(No valid throughput data[^)]*\)(?:\s*-\s*RSSI after test:\s*(-?\d+)\s*dBm)?")
OVERALL = re.compile(r"OVERALL RESULT:\s*(PASSED|FAILED)")
CONNECT_ERR = re.compile(r"ERROR: Failed to connect to (2\.4G|5G) band WiFi")
IF_ERR = re.compile(r"ERROR: (wlan\d interface is not up|Failed to reconnect to WiFi)")
WIFI_SUMMARY = re.compile(r"^\s*WiFi Test Result:\s*(PASS|FAIL)\s*$")
BT_RESULT = re.compile(r"Bluetooth Test Result:\s*(PASSED|FAILED)(.*)")
BT_NOT_FOUND = re.compile(r"ERROR: BT device \(([^)]*)\) not found")
BT_INIT = re.compile(r"Bluetooth initialization failed after (\d+) seconds")
BT_CANT = re.compile(r"Can't (?:connect|init device \S+|read local name \S+):\s*(.+)")
L2PING = re.compile(r"L2ping result:\s*(\d+) sent,\s*(\d+) received,\s*(\d+)% loss")
BT_SUMMARY = re.compile(r"^\s*BT Test Result:\s*(PASS|FAIL)\s*$")
FINAL = re.compile(r"^\s*Final Result:\s*(PASS|FAIL)\s*$")
PORT = re.compile(r"^\s*Port:\s*(\S+)")
GROUP = re.compile(r"^\s*(?:SSID Group|Using SSID group):\s*(\S+)", re.I)
PRIORITY = re.compile(r"^\s*Band Priority:\s*(\S+)")
REBOOT = re.compile(r"up and running since|login: root \(automatic login\)")

BAND = {"2.4G": "24g", "5G": "5g"}


def _num(text):
    if text is None:
        return None
    v = float(text)
    return int(v) if v.is_integer() else v


def parse(log: str) -> dict[str, Any]:
    out: dict[str, Any] = {"length": len(log or ""), "port": None, "ssid_group": None, "band_priority": None,
                           "bands": {}, "wifi_summary": None, "bt": {"result": None, "detail": None, "line": None,
                           "not_found": None, "init_failed": None, "errors": [], "pings": []},
                           "bt_summary": None, "final": None, "if_errors": [], "reboot": None}
    band = None
    in_bt = False
    for n, raw in enumerate((log or "").splitlines(), 1):
        line = raw.rstrip()
        m = BAND_START.search(line) or BAND_INFO.search(line)
        if m:
            band = BAND[m.group(1)]
            out["bands"].setdefault(band, {"start": n, "threshold": None, "target_ssid": None, "attempts": [],
                                           "overall": None, "overall_line": None, "connect_error": None})
            continue
        if out["port"] is None and (m := PORT.match(line)):
            out["port"] = m.group(1)
        if out["ssid_group"] is None and (m := GROUP.match(line)):
            out["ssid_group"] = m.group(1)
        if out["band_priority"] is None and (m := PRIORITY.match(line)):
            out["band_priority"] = m.group(1)
        if (m := CONNECT_ERR.search(line)):
            b = out["bands"].setdefault(BAND[m.group(1)], {"start": n, "threshold": None, "target_ssid": None, "attempts": [],
                                                           "overall": None, "overall_line": None, "connect_error": None})
            b["connect_error"] = {"n": n, "text": line.strip()}
            continue
        if out["bands"] and out["reboot"] is None and REBOOT.search(line):
            out["reboot"] = {"n": n, "text": line.strip()}    # the board restarted after the WiFi test began
            continue
        if (m := IF_ERR.search(line)):
            out["if_errors"].append({"n": n, "text": line.strip()})
            continue
        if "bt_ping" in line or "Bluetooth l2ping test" in line or "BT interface" in line:
            in_bt = True
        b = out["bands"].get(band) if band and not in_bt else None
        if b is not None:
            if (m := THRESHOLD.match(line)):
                b["threshold"] = _num(m.group(1))
                continue
            if (m := TARGET.match(line)):
                b["target_ssid"] = m.group(1)
                continue
            if (m := RESULT.search(line)):
                b["attempts"].append({"n": n, "result": m.group(1), "mbps": _num(m.group(2)), "threshold": _num(m.group(4)),
                                      "rssi": _num(m.group(5)), "no_data": False, "text": line.strip()})
                continue
            if (m := NO_DATA.search(line)):
                b["attempts"].append({"n": n, "result": "FAILED", "mbps": None, "threshold": b["threshold"],
                                      "rssi": _num(m.group(1)), "no_data": True, "text": line.strip()})
                continue
            if (m := OVERALL.search(line)):
                b["overall"], b["overall_line"] = m.group(1), {"n": n, "text": line.strip()}
                continue
        if (m := BT_RESULT.search(line)):
            out["bt"].update(result=m.group(1), detail=m.group(2).strip() or None, line={"n": n, "text": line.strip()})
            continue
        if (m := BT_NOT_FOUND.search(line)):
            out["bt"]["not_found"] = {"n": n, "text": line.strip()}
            continue
        if (m := BT_INIT.search(line)):
            out["bt"]["init_failed"] = {"n": n, "text": line.strip()}
            continue
        if in_bt and (m := BT_CANT.search(line)):
            if len(out["bt"]["errors"]) < 6:
                out["bt"]["errors"].append({"n": n, "text": line.strip()})
            continue
        if (m := L2PING.search(line)):
            out["bt"]["pings"].append({"sent": int(m.group(1)), "received": int(m.group(2)), "loss": int(m.group(3))})
            continue
        if (m := WIFI_SUMMARY.match(line)):
            out["wifi_summary"] = m.group(1)
        elif (m := BT_SUMMARY.match(line)):
            out["bt_summary"] = m.group(1)
        elif (m := FINAL.match(line)):
            out["final"] = {"result": m.group(1), "n": n, "text": line.strip()}
    return out


def _band_label(band):
    return {"24g": "2.4G", "5g": "5G"}.get(band, band)


def classify(log: str, flags: Optional[dict] = None) -> dict[str, Any]:
    """Classify one FAIL log. `flags` = the record's band_result_24g / band_result_5g / bt_result (from the
    desktop parser) fill in when the log itself says nothing about a band."""
    flags = flags or {}
    p = parse(log)
    found: list[tuple[str, Optional[str], list]] = []      # (code, band, evidence)

    for band in ("24g", "5g"):
        b = p["bands"].get(band)
        db_failed = flags.get(f"band_result_{band}") is False
        if not b:
            if db_failed:
                found.append(("LOG_INCOMPLETE", band, []))
            continue
        failed = b["overall"] == "FAILED" or (b["overall"] is None and (db_failed or b["connect_error"] or p["wifi_summary"] == "FAIL"))
        if not failed:
            continue
        measured = [a for a in b["attempts"] if a["mbps"] is not None and a["result"] == "FAILED"]
        nodata = [a for a in b["attempts"] if a["no_data"]]
        ev = [{"n": a["n"], "text": a["text"]} for a in b["attempts"] if a["result"] == "FAILED"][-3:]
        if b["overall_line"]:
            ev.append(b["overall_line"])
        if b["connect_error"] and not b["attempts"]:
            found.append(("WIFI_ASSOC_FAIL", band, [b["connect_error"]] + ev))
        elif measured:
            found.append((f"WIFI_TP_LOW_{band.upper()}", band, ev))
            if nodata:
                found.append(("WIFI_NO_TP_DATA", band, [{"n": a["n"], "text": a["text"]} for a in nodata][-2:]))
        elif nodata:
            found.append(("WIFI_NO_TP_DATA", band, ev))
        elif b["connect_error"]:
            found.append(("WIFI_ASSOC_FAIL", band, [b["connect_error"]] + ev))
        else:   # the band test began but never reported (iperf hung, the board restarted, the log stops)
            found.append(("LOG_INCOMPLETE", band, ev or [p["reboot"] or {"n": b["start"], "text": f"{_band_label(band)} band test has no result"}]))

    bt = p["bt"]
    if bt["not_found"] or bt["init_failed"]:
        found.append(("BT_DEV_NOT_FOUND", "bt", [x for x in (bt["not_found"], bt["init_failed"], bt["line"]) if x]))
    elif bt["result"] == "FAILED":
        found.append(("BT_PING_FAIL", "bt", bt["errors"][:2] + ([bt["line"]] if bt["line"] else [])))
    elif bt["result"] is None and (p["bt_summary"] == "FAIL" or flags.get("bt_result") is False):
        # No Bluetooth test in the log. After a WiFi failure the script stops: Bluetooth was simply not tested.
        if not any(c.startswith("WIFI_") or c == "LOG_INCOMPLETE" for c, _, _ in found):
            found.append(("LOG_INCOMPLETE", "bt", []))

    if p["length"] < SHORT_LOG or p["final"] is None:
        found.append(("LOG_INCOMPLETE", None, [p["final"]] if p["final"] else []))
    if not found:
        found.append(("UNKNOWN", None, [p["final"]] if p["final"] else []))

    found.sort(key=lambda x: SEVERITY.index(x[0]))
    code, band, _ = found[0]
    codes = []
    for c, _, _ in found:
        if c not in codes:
            codes.append(c)
    evidence, seen = [], set()
    for _, _, ev in found:
        for e in ev:
            if e and e["n"] not in seen:
                seen.add(e["n"])
                evidence.append({"n": e["n"], "text": e["text"][:300]})
    evidence = sorted(evidence, key=lambda e: e["n"])[:10]

    measures = {"bands": {}, "bt": None, "bt_not_tested": bt["result"] is None and p["bt_summary"] == "FAIL",
                "rebooted": p["reboot"]}
    for b_name, b in p["bands"].items():
        measures["bands"][b_name] = {
            "threshold": b["threshold"], "target_ssid": b["target_ssid"], "overall": b["overall"],
            "attempts": [{k: a[k] for k in ("result", "mbps", "threshold", "rssi", "no_data")} for a in b["attempts"]],
        }
    if bt["result"] or bt["pings"] or bt["not_found"] or bt["init_failed"]:
        measures["bt"] = {"result": bt["result"], "detail": bt["detail"], "pings": bt["pings"][-6:],
                          "attempts": len(bt["pings"]) or None, "not_found": bool(bt["not_found"] or bt["init_failed"])}

    # headline numbers of the category's band: the best failed attempt (closest to passing)
    mbps = threshold = rssi = attempts = None
    if band in ("24g", "5g") and band in p["bands"]:
        b = p["bands"][band]
        failed = [a for a in b["attempts"] if a["result"] == "FAILED"]
        attempts = len(b["attempts"]) or None
        measured = [a for a in failed if a["mbps"] is not None]
        best = max(measured, key=lambda a: a["mbps"]) if measured else (failed[-1] if failed else None)
        if best:
            mbps, threshold, rssi = best["mbps"], best["threshold"] or b["threshold"], best["rssi"]
        else:
            threshold = b["threshold"]
    elif band == "bt" and measures["bt"]:
        attempts = measures["bt"]["attempts"]

    target = p["bands"].get(band, {}).get("target_ssid") if band in ("24g", "5g") else None
    return {
        "category": code, "secondary": codes[1:], "band": band,
        "mbps": mbps, "threshold": threshold, "rssi": rssi, "attempts": attempts,
        "measures": measures, "evidence": evidence,
        "station_port": p["port"], "ssid_group": p["ssid_group"], "target_ssid": target,
        "target_ssids": {k: v["target_ssid"] for k, v in p["bands"].items()},
        "band_priority": p["band_priority"],
        "classifier": "rule", "classifier_version": CLASSIFIER_VERSION, "confidence": 1.0 if code != "UNKNOWN" else 0.0,
    }


def run_sequence(results: list[bool]) -> str:
    """PASS / FAIL history of one SN in test order → 'FFP'."""
    return "".join("P" if r else "F" for r in results)


def retest_outcome(results: list[bool], index: int) -> str:
    """What happened after the run at `index`: pass_on_retest / failed_again / not_retested."""
    later = results[index + 1:]
    if not later:
        return "not_retested"
    return "pass_on_retest" if any(later) else "failed_again"


# Excerpt for the LLM (rule misses only): result / error lines, without the lines that also occur in PASS logs.
NOISE = re.compile(r"wlan\d interface not found|No lease|serverip|Network is unreachable|Can't deallocate console|"
                   r"_XSERVTrans|Fatal server error|Cannot get device settings|No IGD UPnP|^\[\s*\d+\.\d+\]")
SIGNAL = re.compile(r"Result|ERROR|FAILED|[Ff]ail|not found|OVERALL|Can't|timed out|Timeout|loss|Threshold|Target SSID")


def excerpt(log: str, limit: int = 60) -> list[dict]:
    rows = []
    for n, line in enumerate((log or "").splitlines(), 1):
        text = line.strip()
        if text and SIGNAL.search(text) and not NOISE.search(text):
            rows.append({"n": n, "text": text[:240]})
    return rows[-limit:]


def verify_evidence(cited: list, log_text: str) -> list:
    """Keep only cited lines that exist in the log with that text, and are not known noise (the model may not
    invent evidence or lean on lines that also appear in passing logs)."""
    lines = (log_text or "").splitlines()
    ok = []
    for e in cited or []:
        n, text = e.get("n"), (e.get("text") or "").strip()
        if isinstance(n, int) and 1 <= n <= len(lines) and text and text[:80] in lines[n - 1] and not NOISE.search(lines[n - 1].strip()):
            ok.append({"n": n, "text": lines[n - 1].strip()[:300]})
    return ok
