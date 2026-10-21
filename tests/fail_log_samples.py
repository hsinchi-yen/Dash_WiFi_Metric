"""Synthetic test logs in the production format (no real SN / MAC), shared by the 5F tests."""

PAD = "\n".join(f"[SUM] {i}.00-{i + 1}.00 sec  1.20 MBytes  10.1 Mbits/sec" for i in range(120))   # iperf noise, makes logs realistic in size
NOISE = "wlan0 interface not found. Loading driver: wlan\nNo IGD UPnP Device found on the network !\nCan't deallocate console 1 Device or resource busy\n"


def header(group="grpb", port="/dev/ttyUSB2"):
    return f"""============================================================
WiFi & Bluetooth Stress Test
Date: 2026-10-21 09:00:00
Port: {port}
SN: 999999999999
============================================================
bash wifi_test.sh -d l2 -c bgn -s {group}
Using SSID group: {group} (STA group B)
Test Configuration:
  Band Priority: bgn (2.4G first)
  SSID Group: {group}
{NOISE}Max attempts per band: 3
Iperf duration: 120 seconds
"""


def band(name, threshold, ssid, attempts, overall=None, connect_error=False):
    out = [f"==========================================\nStarting {name} Band Test\n==========================================",
           f"WiFi Connection Information - {name} Band", f"Throughput Threshold: {threshold} MBits/sec", f"Target SSID: {ssid}"]
    if connect_error:
        out.append(f"ERROR: Failed to connect to {name} band WiFi")
    for i, a in enumerate(attempts, 1):
        out.append(f"  Attempt {i}: Preparing iperf test (120 seconds)...\n{PAD}")
        if a == "nodata":
            out.append("    Result: FAILED (No valid throughput data after multiple attempts) - RSSI after test: -40 dBm")
        elif a >= threshold:
            out.append(f"    Result: PASSED ({a} MBits/sec >= {threshold} MBits/sec) - RSSI after test: -35 dBm")
        else:
            out.append(f"    Result: FAILED ({a} MBits/sec < {threshold} MBits/sec) - RSSI after test: -{30 + i} dBm")
    if overall:
        out.append(f"  OVERALL RESULT: {overall}")
    return "\n".join(out) + "\n"


def bt(result="pass"):
    if result == "notfound":
        return "root@qc:~# bash bt_ping.sh 28:92:00:00:00:00\nERROR: BT device (hci0/hciX) not found!\nBluetooth initialization failed after 20 seconds\nBluetooth Test Result: FAILED\n"
    lines = ["root@qc:~# bash bt_ping.sh 28:92:00:00:00:00", "BT interface detected: hci0", "Starting Bluetooth l2ping test (max 6 attempts)..."]
    if result == "pass":
        lines += ["L2ping result: 10 sent, 10 received, 0% loss", "Bluetooth Test Result: PASSED (At least one attempt succeeded)"]
    else:
        lines += ["Can't connect: Host is down", "Attempt 1: FAILED", "Can't connect: Host is down", "Bluetooth Test Result: FAILED (All 6 attempts failed)"]
    return "\n".join(lines) + "\n"


def summary(wifi, bt_res):
    return f"""============================================================
Test Results Summary
============================================================
WiFi Test Result: {wifi}
{f'BT Test Result: {bt_res}' + chr(10) if bt_res else ''}Final Result: FAIL
============================================================
"""


def log(b24, b5, bt_part, wifi="FAIL", bt_res="PASS", **h):
    return header(**h) + b24 + b5 + ("WiFi Test Result: FAILED\nOne or more WiFi validation tests failed.\n" if wifi == "FAIL" else "") + bt_part + summary(wifi, bt_res)


OK5 = band("5G", 50, "PD-RF-QC-5-STA-B", [108], "PASSED (At least one test met threshold)")
OK24 = band("2.4G", 10, "PD-RF-QC-2-4-STA-B", [22], "PASSED (At least one test met threshold)")
LOW24 = band("2.4G", 10, "PD-RF-QC-2-4-STA-B", [4, 8, 6], "FAILED (All 3 attempts failed)")
