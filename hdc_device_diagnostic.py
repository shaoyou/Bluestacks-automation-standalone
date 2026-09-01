#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

HDC_DEVICE_SUFFIX = " [HarmonyOS/HDC]"


def run_cmd(cmd: List[str], timeout: int = 10) -> Dict[str, Any]:
    print(f"[CMD] {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
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


def print_section(title: str, payload: str) -> None:
    print(f"\n== {title} ==")
    print(payload.rstrip() or "(empty)")


def split_targets(text: str) -> List[str]:
    targets: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith("[empty]") or lowered.startswith("empty") or lowered.startswith("list of targets"):
            continue
        targets.append(line.split()[0])
    return targets


def normalize_target(raw: str) -> str:
    value = raw.strip()
    if value.endswith(HDC_DEVICE_SUFFIX):
        return value[: -len(HDC_DEVICE_SUFFIX)].strip()
    if value.startswith("hdc:"):
        return value[4:].strip()
    return value


def hdc_screenshot_result(hdc: str, target: Optional[str]) -> subprocess.CompletedProcess[bytes]:
    device = normalize_target(target or "")
    remote_path = f"/data/local/tmp/bsmanager_hdc_diag_{os.getpid()}.png"
    with tempfile.TemporaryDirectory(prefix="bsmanager-hdc-diagnostic-") as tmp_dir:
        local_path = Path(tmp_dir) / "screen.png"
        capture_cmd = [hdc]
        if device:
            capture_cmd += ["-t", device]
        capture_cmd += ["shell", "uitest", "screenCap", "-p", remote_path]
        capture = run_cmd(capture_cmd)
        if capture["code"] != 0:
            text = f"{capture['stdout']}\n{capture['stderr']}".strip()
            return subprocess.CompletedProcess(args=capture_cmd, returncode=capture["code"], stdout=b"", stderr=f"Cannot capture HDC screen for {device or 'default'}:\n{text}".encode("utf-8"))

        recv_cmd = [hdc]
        if device:
            recv_cmd += ["-t", device]
        recv_cmd += ["file", "recv", remote_path, str(local_path)]
        recv = run_cmd(recv_cmd)
        cleanup_cmd = [hdc]
        if device:
            cleanup_cmd += ["-t", device]
        cleanup_cmd += ["shell", "rm", "-f", remote_path]
        run_cmd(cleanup_cmd)
        if recv["code"] != 0:
            text = f"{recv['stdout']}\n{recv['stderr']}".strip()
            return subprocess.CompletedProcess(args=recv_cmd, returncode=recv["code"], stdout=b"", stderr=f"Cannot fetch HDC screen for {device or 'default'}:\n{text}".encode("utf-8"))

        try:
            return subprocess.CompletedProcess(args=recv_cmd, returncode=0, stdout=local_path.read_bytes(), stderr=b"")
        except OSError as exc:
            return subprocess.CompletedProcess(args=recv_cmd, returncode=1, stdout=b"", stderr=f"Cannot read HDC screenshot for {device or 'default'}: {exc}".encode("utf-8"))


def hdc_screen_size_text(hdc: str, target: Optional[str]) -> str:
    result = hdc_screenshot_result(hdc, target)
    if result.returncode != 0:
        return (result.stderr or b"").decode("utf-8", errors="ignore").strip()
    try:
        image = Image.open(io.BytesIO(result.stdout))
        return f"Physical size: {image.width}x{image.height}\n"
    except Exception as exc:
        return f"Cannot decode HDC screenshot size for {target or 'default'}: {exc}"


def probe_target(hdc: str, target: str) -> Dict[str, Any]:
    normalized = normalize_target(target)
    results: Dict[str, Any] = {}
    probes = {
        "shell echo ok": [hdc, "-t", normalized, "shell", "echo", "ok"],
        "screen size": None,
    }
    for name, cmd in probes.items():
        if cmd is None:
            text = hdc_screen_size_text(hdc, normalized)
            print_section(f"{target} :: {name}", text)
            results[name] = {"code": 0 if text.startswith("Physical size:") else 1, "text": text}
            continue
        result = run_cmd(cmd)
        print_section(f"{target} :: {name}", result["text"] or f"exit {result['code']}")
        results[name] = {"code": result["code"], "text": result["text"]}
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="HarmonyOS/HDC device diagnostic")
    parser.add_argument("--hdc", default="hdc", help="HDC executable path")
    parser.add_argument("--device", help="Optional HDC target, e.g. 12345 or 12345 [HarmonyOS/HDC]")
    parser.add_argument("--report-dir", default="diagnostics/hdc_device_discovery", help="Directory for saved report JSON")
    args = parser.parse_args()

    started_at = dt.datetime.now(dt.timezone.utc)
    report: Dict[str, Any] = {
        "started_at": started_at.isoformat(),
        "host": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "cwd": os.getcwd(),
        },
        "hdc": args.hdc,
        "requested_device": args.device or "",
        "targets_before": [],
        "targets_after": [],
        "probes": {},
    }

    print_section("Host", json.dumps(report["host"], ensure_ascii=False, indent=2))
    print_section("HDC Path", args.hdc)

    version = run_cmd([args.hdc, "version"])
    report["version"] = version
    print_section("hdc version", version["text"])

    before = run_cmd([args.hdc, "list", "targets"])
    report["targets_before"] = split_targets(before["text"])
    print_section("hdc list targets (before)", before["text"])

    targets: List[str] = [normalize_target(args.device)] if args.device else []
    if not targets:
        targets = report["targets_before"]

    if not targets:
        print_section("Summary", "No connected HDC targets found.")
    else:
        for target in targets:
            report["probes"][target] = probe_target(args.hdc, target)

    after = run_cmd([args.hdc, "list", "targets"])
    report["targets_after"] = split_targets(after["text"])
    print_section("hdc list targets (after)", after["text"])

    report_path: Optional[Path] = None
    if args.report_dir:
        report_dir = Path(args.report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = started_at.strftime("%Y%m%d-%H%M%S")
        report_path = report_dir / f"{timestamp}_hdc_device_discovery.json"
        report["report_path"] = str(report_path)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print_section("Report", str(report_path))

    summary = "\n".join([
        f"hdc version exit={version['code']}",
        f"targets before={len(report['targets_before'])} after={len(report['targets_after'])}",
    ])
    print_section("Summary", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
