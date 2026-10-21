#!/usr/bin/env python3
"""Unit tests for the FAIL log classifier (services/fail_rules.py: pure, no database).

    python3 tests/unit_fail_rules.py

The logs are synthetic but follow the production format line for line (no real SN / MAC).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dockerup-essential" / "api" / "services"))
import fail_rules as R  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail and not cond else ""))


from fail_log_samples import LOW24, NOISE, OK5, OK24, PAD, band, bt, header, log, summary  # noqa: E402

# ── categories ──
c = R.classify(log(LOW24, OK5, bt()))
check("2.4G below threshold → WIFI_TP_LOW_24G", c["category"] == "WIFI_TP_LOW_24G" and c["band"] == "24g" and not c["secondary"], c["category"])
check("headline numbers = the best failed attempt (8 Mbps of 10, RSSI −32, 3 attempts)",
      (c["mbps"], c["threshold"], c["rssi"], c["attempts"]) == (8, 10, -32, 3), (c["mbps"], c["threshold"], c["rssi"], c["attempts"]))
check("evidence = the FAILED result lines and the OVERALL line, with line numbers",
      len(c["evidence"]) == 4 and all("FAILED" in e["text"] for e in c["evidence"]) and all(isinstance(e["n"], int) for e in c["evidence"]))
check("station: port, SSID group and the band's target SSID",
      (c["station_port"], c["ssid_group"], c["target_ssid"]) == ("/dev/ttyUSB2", "grpb", "PD-RF-QC-2-4-STA-B"))
check("noise lines are never evidence", not any("Loading driver" in e["text"] or "UPnP" in e["text"] for e in c["evidence"]))

c = R.classify(log(OK24, band("5G", 50, "PD-RF-QC-5-STA-B", [31, 44, 12], "FAILED (All 3 attempts failed)"), bt()))
check("5G below threshold → WIFI_TP_LOW_5G (best 44 of 50)", c["category"] == "WIFI_TP_LOW_5G" and c["mbps"] == 44 and c["threshold"] == 50)

c = R.classify(log(band("2.4G", 10, "S24", ["nodata", "nodata", "nodata"], "FAILED (All 3 attempts failed)"), OK5, bt()))
check("only 'No valid throughput data' → WIFI_NO_TP_DATA", c["category"] == "WIFI_NO_TP_DATA" and c["band"] == "24g", c["category"])

c = R.classify(log(band("2.4G", 10, "S24", [5, "nodata", 7], "FAILED (All 3 attempts failed)"), OK5, bt()))
check("low + no data → WIFI_TP_LOW_24G with WIFI_NO_TP_DATA secondary", c["category"] == "WIFI_TP_LOW_24G" and c["secondary"] == ["WIFI_NO_TP_DATA"], c)

c = R.classify(log(band("2.4G", 10, "S24", [], None, connect_error=True), OK5, bt()))
check("connect error without a throughput test → WIFI_ASSOC_FAIL", c["category"] == "WIFI_ASSOC_FAIL" and "Failed to connect" in c["evidence"][0]["text"])

c = R.classify(log(OK24, OK5, bt("ping"), wifi="PASS", bt_res="FAIL"))
check("BT l2ping failing every attempt → BT_PING_FAIL with 'Host is down' as evidence",
      c["category"] == "BT_PING_FAIL" and any("Host is down" in e["text"] for e in c["evidence"]), c["category"])

c = R.classify(log(LOW24, OK5, bt("notfound"), bt_res="FAIL"))
check("BT device not found outranks a WiFi failure (most severe first)",
      c["category"] == "BT_DEV_NOT_FOUND" and c["secondary"] == ["WIFI_TP_LOW_24G"], (c["category"], c["secondary"]))

c = R.classify(log(LOW24, OK5, "", bt_res="FAIL"))
check("WiFi failed and the script never ran Bluetooth → no extra code, marked 'BT not tested'",
      c["category"] == "WIFI_TP_LOW_24G" and not c["secondary"] and c["measures"]["bt_not_tested"])

c = R.classify(log(OK24, OK5, "", wifi="PASS", bt_res="FAIL"))
check("WiFi passed but the Bluetooth test is missing → LOG_INCOMPLETE", c["category"] == "LOG_INCOMPLETE" and c["band"] == "bt")

reboot = header() + OK24 + band("5G", 50, "S5", [], None) + "EDM QC Linux up and running since 2013-04-02.\nlogin: root (automatic login)\n" + PAD + "\n" + summary("FAIL", None)
c = R.classify(reboot)
check("the board restarts during the 5G test → LOG_INCOMPLETE, the reboot line as evidence",
      c["category"] == "LOG_INCOMPLETE" and c["band"] == "5g" and "up and running" in c["evidence"][0]["text"], c)
check("… and the parse marks it rebooted", c["measures"]["rebooted"] is not None)

c = R.classify("WiFi Test Result: FAIL\nFinal Result: FAIL\n")
check("a short log → LOG_INCOMPLETE", c["category"] == "LOG_INCOMPLETE")

c = R.classify(header() + PAD + "\n" + NOISE * 3 + summary("FAIL", None).replace("WiFi Test Result: FAIL\n", ""))
check("only noise lines → UNKNOWN (for the LLM)", c["category"] == "UNKNOWN" and c["confidence"] == 0.0, c["category"])

c = R.classify(log(LOW24, OK5, bt()), {"band_result_24g": False, "band_result_5g": True, "bt_result": True})
check("the desktop parser's flags agree", c["category"] == "WIFI_TP_LOW_24G")
c = R.classify(header() + OK5 + summary("FAIL", "PASS"), {"band_result_24g": False})
check("a band the flags say failed but the log never ran → LOG_INCOMPLETE", c["category"] == "LOG_INCOMPLETE" and c["band"] == "24g", c)

# ── retest ──
check("run sequence FFP", R.run_sequence([False, False, True]) == "FFP")
check("FAIL then PASS → pass_on_retest", R.retest_outcome([False, True], 0) == "pass_on_retest")
check("FAIL, FAIL (first) → failed_again", R.retest_outcome([False, False], 0) == "failed_again")
check("last FAIL → not_retested", R.retest_outcome([False, False], 1) == "not_retested")
check("PASS later after two FAILs → both pass_on_retest",
      [R.retest_outcome([False, False, True], i) for i in (0, 1)] == ["pass_on_retest", "pass_on_retest"])

# ── LLM excerpt and evidence check ──
text = log(LOW24, OK5, bt())
ex = R.excerpt(text)
check("the LLM excerpt keeps result lines and drops noise", any("FAILED (4 MBits/sec" in e["text"] for e in ex)
      and not any("Loading driver" in e["text"] or "UPnP" in e["text"] for e in ex))
real = next(e for e in ex if "FAILED (4 MBits" in e["text"])
check("a cited line that exists is kept", R.verify_evidence([{"n": real["n"], "text": real["text"]}], text) == [real])
check("a cited line with the wrong number is dropped", R.verify_evidence([{"n": real["n"] + 1, "text": real["text"]}], text) == [])
check("an invented line is dropped", R.verify_evidence([{"n": 5, "text": "ERROR: antenna cable broken"}], text) == [])
noise_n = next(n for n, line in enumerate(text.splitlines(), 1) if "Loading driver" in line)
check("a noise line is not accepted as evidence", R.verify_evidence([{"n": noise_n, "text": "wlan0 interface not found"}], text) == [])

passed = sum(results)
print(f"\n{passed}/{len(results)} classifier checks passed")
sys.exit(0 if passed == len(results) else 1)
