#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def configure_console_encoding() -> None:
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


configure_console_encoding()

def run_cmd(cmd: List[str], timeout: int = 10) -> Dict[str, Any]:
    print(f"[CMD] {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {"code": -1, "stdout": "", "stderr": "file not found", "text": "file not found"}
    except subprocess.TimeoutExpired:
        message = f"command timed out after {timeout}s"
        return {"code": 124, "stdout": "", "stderr": message, "text": message}
    text = (result.stdout or "") + (result.stderr or "")
    return {
        "code": result.returncode,
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
        "text": text.strip(),
    }


def split_adb_output(text: str) -> List[str]:
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line:
            lines.append(line)
    return lines


def parse_devices(text: str) -> List[Dict[str, str]]:
    devices: List[Dict[str, str]] = []
    for line in split_adb_output(text):
        if line.startswith("List of devices attached"):
            continue
        if line.startswith("* daemon "):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial = parts[0]
        state = parts[1]
        extras = " ".join(parts[2:])
        devices.append({"serial": serial, "state": state, "extras": extras})
    return devices


def print_section(title: str, payload: str) -> None:
    print(f"\n== {title} ==")
    print(payload.rstrip() or "(empty)")


def adb_shell_probe(adb: str, serial: str) -> Dict[str, Any]:
    probes = {
        "get-state": [adb, "-s", serial, "get-state"],
        "ro.build.version.release": [adb, "-s", serial, "shell", "getprop", "ro.build.version.release"],
        "wm size": [adb, "-s", serial, "shell", "wm", "size"],
    }
    results: Dict[str, Any] = {}
    for name, cmd in probes.items():
        result = run_cmd(cmd)
        results[name] = {
            "code": result["code"],
            "text": result["text"],
        }
        print_section(f"{serial} :: {name}", result["text"] or f"exit {result['code']}")
    return results


def capture_devices(adb: str, phase: str, report: Dict[str, Any], label: str) -> List[Dict[str, str]]:
    result = run_cmd([adb, "devices", "-l"])
    report["commands"].append({"name": "adb devices -l", "phase": phase, **result})
    devices = parse_devices(result["text"])
    report["device_list"].append({"phase": phase, "devices": devices})
    print_section(label, result["text"])
    return devices


def main() -> int:
    parser = argparse.ArgumentParser(description="ADB device discovery diagnostic")
    parser.add_argument("--adb", default="adb", help="ADB executable path")
    parser.add_argument("--device", help="Optional target serial to probe")
    parser.add_argument("--connect-target", default="127.0.0.1:5555", help="Target to run adb connect against")
    parser.add_argument("--report-dir", default="diagnostics/device_discovery", help="Directory for saved report JSON")
    args = parser.parse_args()

    started_at = dt.datetime.now(dt.timezone.utc)
    report: Dict[str, Any] = {
        "started_at": started_at.isoformat(),
        "host": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "cwd": os.getcwd(),
        },
        "adb": args.adb,
        "connect_target": args.connect_target,
        "requested_device": args.device or "",
        "commands": [],
        "device_list": [],
        "probes": {},
    }

    print_section("Host", json.dumps(report["host"], ensure_ascii=False, indent=2))
    print_section("ADB Path", args.adb)

    version = run_cmd([args.adb, "version"])
    report["commands"].append({"name": "adb version", **version})
    print_section("adb version", version["text"])

    before_devices = capture_devices(args.adb, "before", report, "adb devices -l (before)")

    kill_server = run_cmd([args.adb, "kill-server"])
    report["commands"].append({"name": "adb kill-server", **kill_server})
    print_section("adb kill-server", kill_server["text"])

    start_server = run_cmd([args.adb, "start-server"])
    report["commands"].append({"name": "adb start-server", **start_server})
    print_section("adb start-server", start_server["text"])

    restarted_devices = capture_devices(args.adb, "after_restart", report, "adb devices -l (after restart)")

    if args.connect_target:
        connect = run_cmd([args.adb, "connect", args.connect_target])
        report["commands"].append({"name": f"adb connect {args.connect_target}", **connect})
        print_section(f"adb connect {args.connect_target}", connect["text"])

    after_devices = capture_devices(args.adb, "after_connect", report, "adb devices -l (after connect)")

    targets = [args.device] if args.device else []
    if not targets:
        targets = [entry["serial"] for entry in after_devices if entry["state"] == "device"]

    if not targets:
        print_section("Summary", "No online adb devices found.")
    else:
        for serial in targets:
            probes = adb_shell_probe(args.adb, serial)
            report["probes"][serial] = probes

    report_path: Optional[Path] = None
    if args.report_dir:
        report_dir = Path(args.report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = started_at.strftime("%Y%m%d-%H%M%S")
        report_path = report_dir / f"{timestamp}_device_discovery.json"
        report["report_path"] = str(report_path)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print_section("Report", str(report_path))

    summary_lines = [
        f"adb version exit={version['code']}",
        f"devices before={len(before_devices)} after={len(after_devices)}",
        f"devices after restart={len(restarted_devices)}",
        f"online after={sum(1 for item in after_devices if item['state'] == 'device')}",
    ]
    print_section("Summary", "\n".join(summary_lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
