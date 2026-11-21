#!/usr/bin/env python3
"""End-to-end self-test for touch recording and timed replay.

What it proves
--------------
1. Offline unit checks: gesture classification (tap / drag / long press),
   wait gaps, click hold duration, and trace generation (no stale first point).
2. On-device record -> plan: a complex known route is injected through the
   touch event device (visible to getevent, exactly like a real finger). It has
   five gestures with four releases: a multi-turn zigzag held in one touch, a
   diagonal route with a ~180 degree reversal, a tap, a separate short swipe
   and a long press. The recorder runs normally, then the produced plan is
   compared against an independent parse of the same raw getevent stream
   (types, endpoints, durations, waits, and every recorded corner).
3. On-device replay: every recorded trace is replayed by adb_bot's replayer.
   The check verifies wall-clock duration against the recorded timeline, and
   that every corner is emitted unconditionally (adaptive sampling may only
   drop straight-line samples). With the pointer-location overlay enabled,
   each replayed route is also saved as a screenshot for visual comparison.

Safety
------
Real touch events are injected. By default the script opens a blank image in
the system gallery first; taps/swipes on a solid image do not change app state.
Pass --no-sandbox to test on the current screen (e.g. inside a game) at your
own risk, or --coordinates to place the synthetic gestures elsewhere.

Usage
-----
    python3 tools/recorder_selftest.py --device emulator-5554
    python3 tools/recorder_selftest.py --device emulator-5554 --offline
    python3 tools/recorder_selftest.py --device emulator-5554 --no-sandbox
"""

from __future__ import annotations

import argparse
import io
import json
import math
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import adb_bot as bot  # noqa: E402
import record_touch as rt  # noqa: E402

EVENT_LINE_RE = re.compile(
    r"\[\s*([0-9]+\.[0-9]+)\]\s+(?:/dev/input/event\d+:\s+)?([0-9a-fA-F]{4})\s+([0-9a-fA-F]{4})\s+([0-9a-fA-F]{8})"
)
SANDBOX_REMOTE = "/sdcard/Download/recorder_selftest_blank.png"
INJECT_REMOTE = "/data/local/tmp/recorder_selftest_inject.sh"
DEFAULT_TOLERANCE_PX = 4.0
DEFAULT_TOLERANCE_SEC = 0.08


class SelfTestError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# small helpers


def sh(cmd: List[str], *, timeout: float = 60.0, check: bool = False, text: bool = True):
    return subprocess.run(cmd, capture_output=True, text=text, timeout=timeout, check=check)


def adb_cmd(adb: str, device: Optional[str], args: List[str]) -> List[str]:
    cmd = [adb]
    if device:
        cmd += ["-s", device]
    return cmd + args


def adb(adb_path: str, device: Optional[str], args: List[str], *, timeout: float = 60.0):
    return sh(adb_cmd(adb_path, device, args), timeout=timeout)


def adb_shell(adb_path: str, device: Optional[str], shell: str, *, timeout: float = 120.0):
    return sh(adb_cmd(adb_path, device, ["shell", shell]), timeout=timeout)


def screen_size(adb_path: str, device: Optional[str]) -> Tuple[int, int]:
    result = adb(adb_path, device, ["shell", "wm", "size"])
    match = rt.WM_SIZE_RE.search(f"{result.stdout}\n{result.stderr}")
    if not match:
        raise SelfTestError(f"Cannot read wm size: {result.stdout!r} {result.stderr!r}")
    return int(match.group(1)), int(match.group(2))


def screenshot_png(adb_path: str, device: Optional[str]) -> bytes:
    result = subprocess.run(
        adb_cmd(adb_path, device, ["exec-out", "screencap", "-p"]),
        capture_output=True,
        timeout=30.0,
    )
    if result.returncode != 0 or not result.stdout:
        raise SelfTestError(f"screencap failed: {result.stderr[:200]!r}")
    return result.stdout


def pointer_location_setting(adb_path: str, device: Optional[str]) -> str:
    result = adb(adb_path, device, ["shell", "settings", "get", "system", "pointer_location"])
    return (result.stdout or "").strip() or "0"


def set_pointer_location(adb_path: str, device: Optional[str], enabled: bool) -> None:
    adb(adb_path, device, ["shell", "settings", "put", "system", "pointer_location", "1" if enabled else "0"])


def current_focus(adb_path: str, device: Optional[str]) -> str:
    result = adb(adb_path, device, ["shell", "dumpsys window"])
    match = re.search(r"mCurrentFocus=Window\{[^}]*?(\S+/[^}\s]+)", result.stdout)
    return match.group(1) if match else ""


def ensure_sandbox(adb_path: str, device: Optional[str], output_dir: Path) -> str:
    """Show a solid image in the gallery so injected gestures are harmless."""
    image = output_dir / "blank_sandbox.png"
    if not image.exists():
        try:
            from PIL import Image, ImageDraw
        except Exception as exc:  # pragma: no cover - PIL ships with the repo deps
            raise SelfTestError(f"Pillow is required for the sandbox image: {exc}") from exc
        width, height = screen_size(adb_path, device)
        canvas = Image.new("RGB", (width, height), (20, 20, 20))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle([8, 8, width - 9, height - 9], outline=(50, 50, 50), width=3)
        canvas.save(image)
    adb(adb_path, device, ["push", str(image), SANDBOX_REMOTE])
    adb(adb_path, device, [
        "shell", "am", "start", "-a", "android.intent.action.VIEW",
        "-d", f"file://{SANDBOX_REMOTE}", "-t", "image/png",
    ])
    time.sleep(2.0)
    focus = current_focus(adb_path, device)
    return focus


# ---------------------------------------------------------------------------
# offline unit checks


def _mapped(raw_x: int, raw_y: int, caps: Tuple[int, int], screen: Tuple[int, int]) -> Tuple[int, int]:
    return rt.map_raw_point_to_screen(raw_x, raw_y, caps[0], caps[1], screen[0], screen[1], False, False, False)


def offline_checks() -> List[str]:
    failures: List[str] = []
    screen = (1080, 1920)
    caps = (32767, 32767)

    def gesture(points: List[Tuple[float, int, int]], explicit: bool = True) -> rt.Gesture:
        return rt.Gesture(
            start_t=points[0][0],
            end_t=points[-1][0],
            points=[rt.Point(t=t, x_raw=x, y_raw=y) for t, x, y in points],
            explicit_touch=explicit,
        )

    tap_far = gesture([(10.0, 9100, 24000), (10.12, 9120, 24010)])
    drag = gesture([
        (11.5, 7000, 20500),
        (11.7, 10000, 20500),
        (11.9, 13000, 20500),
        (12.1, 16000, 20500),
    ])

    actions = rt.build_actions_from_gestures([tap_far, drag], caps[0], caps[1], screen[0], screen[1])

    # click keeps the hold duration and lands on the last sample
    clicks = [a for a in actions if a.get("type") == "click"]
    if len(clicks) != 1:
        failures.append(f"offline: expected 1 click, got {len(clicks)}")
    else:
        click = clicks[0]
        expected_x, expected_y = _mapped(9120, 24010, caps, screen)
        if abs(click["x"] - expected_x) > 1 or abs(click["y"] - expected_y) > 1:
            failures.append(f"offline: click coordinate mismatch {click} vs {(expected_x, expected_y)}")
        if int(click.get("duration_ms", 0)) < 100:
            failures.append(f"offline: click lost hold duration: {click}")

    waits = [a for a in actions if a.get("type") == "wait"]
    if not waits or abs(float(waits[0]["seconds"]) - 1.38) > 0.02:
        failures.append(f"offline: expected 1.38s wait, got {waits}")

    traces = [a for a in actions if a.get("type") == "trace"]
    if len(traces) != 1:
        failures.append(f"offline: expected 1 trace, got {len(traces)}")
    else:
        points = traces[0]["points"]
        start = (points[0]["x"], points[0]["y"])
        end = (points[-1]["x"], points[-1]["y"])
        expected_start = _mapped(7000, 20500, caps, screen)
        expected_end = _mapped(16000, 20500, caps, screen)
        if math.dist(start, expected_start) > 1:
            failures.append(f"offline: trace starts at {start}, expected {expected_start} (stale start point?)")
        if math.dist(end, expected_end) > 1:
            failures.append(f"offline: trace ends at {end}, expected {expected_end}")
        if int(points[0]["t_ms"]) != 0:
            failures.append(f"offline: trace does not start at t=0: {points[0]}")
        span_ms = int(points[-1]["t_ms"])
        if abs(span_ms - 600) > 2:
            failures.append(f"offline: trace span should be 600ms, got {span_ms}ms")

    hold = gesture([(20.0, 16000, 27000), (20.9, 16000, 27000)])
    hold_actions = rt.build_actions_from_gestures([hold], caps[0], caps[1], screen[0], screen[1])
    has_long_press = bool(hold_actions) and hold_actions[0].get("type") == "long_press"
    if not has_long_press:
        failures.append(f"offline: 0.9s stationary hold should be long_press, got {hold_actions}")
    elif abs(int(hold_actions[0]["duration_ms"]) - 900) > 2:
        failures.append(f"offline: long_press duration should be 900ms, got {hold_actions[0]}")

    # Replay script composition: per-step sleeps plus measured command overhead
    # must reconstruct the recorded timeline.
    recorded = [
        {"x": 100, "y": 100, "t_ms": 0},
        {"x": 200, "y": 100, "t_ms": 100},
        {"x": 300, "y": 100, "t_ms": 200},
        {"x": 400, "y": 100, "t_ms": 400},
    ]
    captured: List[str] = []

    def capture_script(ctx, script, timeout=120.0):  # noqa: ANN001
        captured.append(script)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    real_script = bot.adb_shell_script
    bot.adb_shell_script = capture_script
    try:
        ctx = make_ctx(1080, 1920, 1080, 1920)
        ctx.motionevent_supported = True
        bot.do_trace(ctx, {"type": "trace", "points": recorded, "mode": "motion", "min_segment_ms": 1, "max_segment_ms": 10000})
    finally:
        bot.adb_shell_script = real_script
    if not captured:
        failures.append("offline: do_trace did not emit a batch script")
    else:
        script = captured[0]
        if "input motionevent DOWN" not in script or "input motionevent UP" not in script:
            failures.append("offline: batch script missing DOWN/UP")
        if script.count("input motionevent MOVE") != 3:
            failures.append("offline: batch script should contain 3 MOVE commands")
        if "/proc/uptime" not in script or "read -t " not in script:
            failures.append("offline: trace script should schedule against /proc/uptime with read -t")
        if "usleep" in script:
            failures.append("offline: trace script should not use blocking usleep")
        targets = [int(v) for v in re.findall(r"remain=\$\(\( (\d+) -", script)]
        if targets != [100, 200, 400]:
            failures.append(f"offline: trace targets should be cumulative [100, 200, 400], got {targets}")

    # Corner preservation: a right-angle route must mark the turns and emit
    # them unconditionally even though ordinary samples may be skipped.
    corner_route = [
        {"x": 300, "y": 1200, "t_ms": 0},
        {"x": 700, "y": 1200, "t_ms": 100},
        {"x": 700, "y": 900, "t_ms": 200},
        {"x": 400, "y": 900, "t_ms": 300},
    ]
    flags = bot.trace_corner_flags(corner_route)
    if not (flags[1] and flags[2]):
        failures.append(f"offline: corner flags should hit the right-angle turns, got {flags}")
    captured.clear()
    bot.adb_shell_script = capture_script
    try:
        ctx = make_ctx(1080, 1920, 1080, 1920)
        ctx.motionevent_supported = True
        bot.do_trace(
            ctx,
            {"type": "trace", "points": corner_route, "mode": "motion",
             "min_segment_ms": 1, "max_segment_ms": 10000},
        )
    finally:
        bot.adb_shell_script = real_script
    script = captured[0] if captured else ""
    plain_moves = [
        line for line in script.splitlines() if line.startswith("input motionevent MOVE ")
    ]
    if not any(line.endswith("700 1200") for line in plain_moves):
        failures.append("offline: right-angle corner (700,1200) should be unconditional")
    if not any(line.endswith("700 900") for line in plain_moves):
        failures.append("offline: right-angle corner (700,900) should be unconditional")

    return failures


def make_ctx(src_w: int, src_h: int, dst_w: int, dst_h: int, device: Optional[str] = None) -> bot.RunContext:
    return bot.RunContext(
        adb_path="adb",
        hdc_path="hdc",
        device_backend="adb",
        device=device,
        user_id="selftest",
        source_id="",
        source_name="",
        source_file=None,
        dry_run=False,
        jitter=0,
        stop_at=None,
        src_screen_w=src_w,
        src_screen_h=src_h,
        dst_screen_w=dst_w,
        dst_screen_h=dst_h,
        trace_time_scale=1.0,
        plan_dir=Path("/tmp"),
        image_match_debug=False,
    )


# ---------------------------------------------------------------------------
# raw getevent ground truth


@dataclass
class RawGesture:
    start_t: float
    end_t: float
    points: List[Tuple[float, int, int]] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return int(round((self.end_t - self.start_t) * 1000.0))


def parse_raw_gestures(lines: List[str]) -> List[RawGesture]:
    """Independent parse of `getevent -lt` output for the primary touch slot."""
    gestures: List[RawGesture] = []
    gesture: Optional[RawGesture] = None
    x: Optional[int] = None
    y: Optional[int] = None
    slot = 0
    explicit_started = False
    saw_explicit = False

    def finish(end_t: float) -> None:
        nonlocal gesture, explicit_started, x, y
        if gesture is not None and gesture.points:
            gesture.end_t = end_t
            gestures.append(gesture)
        gesture = None
        explicit_started = False
        # Do not let the release frame seed a new sparse-ABS gesture.
        x = y = None

    def maybe_start(t: float) -> None:
        nonlocal gesture, explicit_started
        if gesture is None:
            gesture = RawGesture(start_t=t, end_t=t)
            explicit_started = True

    for line in lines:
        match = EVENT_LINE_RE.search(line)
        if not match:
            continue
        t = float(match.group(1))
        etype, ecode = match.group(2).lower(), match.group(3).lower()
        value_hex = match.group(4).lower()
        value = int(value_hex, 16)

        if etype == "0003" and ecode == "002f":  # ABS_MT_SLOT
            slot = value
            continue
        if slot != 0:
            continue
        if etype == "0003" and ecode in {"0035", "0000"}:  # ABS_MT_POSITION_X / ABS_X
            x = value
            continue
        if etype == "0003" and ecode in {"0036", "0001"}:  # ABS_MT_POSITION_Y / ABS_Y
            y = value
            continue
        if etype == "0003" and ecode == "0039":  # ABS_MT_TRACKING_ID
            saw_explicit = True
            if value_hex == "ffffffff":
                if gesture is not None and x is not None and y is not None:
                    gesture.points.append((t, x, y))
                finish(t)
            else:
                if gesture is not None:
                    finish(gesture.end_t)
                gesture = RawGesture(start_t=t, end_t=t)
                explicit_started = True
                x = y = None
            continue
        if etype == "0001" and ecode == "014a":  # BTN_TOUCH
            saw_explicit = True
            if value == 1:
                if gesture is None:
                    x = y = None
                maybe_start(t)
            else:
                if gesture is not None and x is not None and y is not None:
                    gesture.points.append((t, x, y))
                finish(t)
            continue
        if etype == "0000" and ecode == "0000":  # SYN_REPORT
            if gesture is None and not explicit_started and not saw_explicit and x is not None and y is not None:
                # sparse ABS input without BTN_TOUCH / TRACKING_ID
                gesture = RawGesture(start_t=t, end_t=t)
            if gesture is not None and x is not None and y is not None:
                gesture.points.append((t, x, y))
            continue
    if gesture is not None and gesture.points:
        gesture.end_t = gesture.points[-1][0]
        gestures.append(gesture)
    return gestures


# ---------------------------------------------------------------------------
# injection


def _raw_point(px: int, py: int, caps: Tuple[int, int], screen: Tuple[int, int]) -> Tuple[int, int]:
    return rt.map_screen_point_to_raw(px, py, caps[0], caps[1], screen[0], screen[1], False, False, False)


def complex_route_spec(screen: Tuple[int, int]) -> Dict[str, object]:
    """Scenario with turns inside one touch plus several touch-down/up cycles.

    - zigzag: one continuous touch, 4 direction changes (90/90/~135 degrees)
    - diagonal_reversal: diagonal motion then a ~180 degree reversal
    - tap: a separate touch
    - short_swipe: another separate touch with a different direction
    - hold: long press
    """
    width, height = screen

    def P(fx: float, fy: float) -> Tuple[int, int]:
        return int(round(width * fx)), int(round(height * fy))

    return {
        "step_ms": 120,
        "gestures": [
            {
                "name": "zigzag",
                "kind": "path",
                "points": [P(0.28, 0.63), P(0.66, 0.63), P(0.66, 0.47), P(0.37, 0.47), P(0.28, 0.57)],
                "steps_per_segment": 6,
            },
            {
                "name": "diagonal_reversal",
                "kind": "path",
                "points": [P(0.76, 0.73), P(0.55, 0.52), P(0.76, 0.37), P(0.76, 0.48)],
                "steps_per_segment": 6,
            },
            {
                "name": "tap",
                "kind": "tap",
                "point": P(0.50, 0.82),
                "hold_ms": 140,
            },
            {
                "name": "short_swipe",
                "kind": "path",
                "points": [P(0.24, 0.80), P(0.43, 0.80)],
                "steps_per_segment": 4,
            },
            {
                "name": "hold",
                "kind": "long_press",
                "point": P(0.20, 0.32),
                "hold_ms": 900,
            },
        ],
        "waits_ms": [1400, 1200, 900, 1100],
    }


def build_injection_script(
    screen: Tuple[int, int],
    caps: Tuple[int, int],
    event_dev: str,
    spec: Optional[Dict[str, object]] = None,
) -> Tuple[str, Dict[str, object]]:
    resolved_spec = spec or complex_route_spec(screen)
    step_ms = int(resolved_spec.get("step_ms", 120))
    gestures = resolved_spec["gestures"]  # type: ignore[index]
    waits = resolved_spec.get("waits_ms", [1200] * len(gestures))  # type: ignore[arg-type]

    lines = ["#!/system/bin/sh", f"D={event_dev}", "tap_down() {",
             "  sendevent $D 3 47 0", "  sendevent $D 3 57 $1",
             "  sendevent $D 3 53 $2", "  sendevent $D 3 54 $3",
             "  sendevent $D 1 330 1", "  sendevent $D 0 0 0", "}",
             "tap_up() {", "  sendevent $D 3 57 -1",
             "  sendevent $D 1 330 0", "  sendevent $D 0 0 0", "}"]

    tracking = 5000
    for gesture_index, gesture in enumerate(gestures):
        tracking += 1
        kind = str(gesture.get("kind"))
        if kind in {"tap", "long_press"}:
            x, y = gesture["point"]  # type: ignore[misc]
            raw_x, raw_y = _raw_point(int(x), int(y), caps, screen)
            hold_ms = int(gesture.get("hold_ms", 140))
            lines += [f"tap_down {tracking} {raw_x} {raw_y}", f"usleep {hold_ms * 1000}", "tap_up"]
        elif kind == "path":
            points = gesture["points"]  # type: ignore[index]
            first = points[0]
            raw_x, raw_y = _raw_point(int(first[0]), int(first[1]), caps, screen)
            lines.append(f"tap_down {tracking} {raw_x} {raw_y}")
            steps_per_segment = int(gesture.get("steps_per_segment", 5))
            for segment in range(len(points) - 1):
                x1, y1 = points[segment]
                x2, y2 = points[segment + 1]
                for step in range(1, steps_per_segment + 1):
                    ratio = step / float(steps_per_segment)
                    px = int(round(x1 + (x2 - x1) * ratio))
                    py = int(round(y1 + (y2 - y1) * ratio))
                    raw_x, raw_y = _raw_point(px, py, caps, screen)
                    lines += [
                        f"sendevent $D 3 53 {raw_x}",
                        f"sendevent $D 3 54 {raw_y}",
                        "sendevent $D 0 0 0",
                        f"usleep {step_ms * 1000}",
                    ]
            lines.append("tap_up")
        else:
            raise SelfTestError(f"unknown gesture kind: {kind}")

        if gesture_index < len(gestures) - 1:
            lines.append(f"usleep {int(waits[gesture_index]) * 1000}")

    return "\n".join(lines) + "\n", resolved_spec


# ---------------------------------------------------------------------------
# record -> plan


class RawCapture:
    def __init__(self, adb_path: str, device: Optional[str], event_dev: str) -> None:
        self.lines: List[str] = []
        self._proc = subprocess.Popen(
            adb_cmd(adb_path, device, ["shell", "getevent", "-t", event_dev]),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", bufsize=1,
        )
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.rstrip("\n")
            if EVENT_LINE_RE.search(line):
                self.lines.append(line)

    def stop(self) -> List[str]:
        try:
            self._proc.terminate()
            self._proc.wait(timeout=2.0)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._thread.join(timeout=2.0)
        return self.lines


def record_plan(
    adb_path: str,
    device: Optional[str],
    event_dev: str,
    output: Path,
    screen: Tuple[int, int],
    caps: Tuple[int, int],
) -> Tuple[Dict, List[str], str, Dict[str, object]]:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    capture = RawCapture(adb_path, device, event_dev)
    time.sleep(0.5)

    cmd = [sys.executable, "-u", str(Path(__file__).resolve().parents[1] / "record_touch.py"),
           "--output", str(output), "--adb", adb_path, "--device", device or "",
           "--event-dev", event_dev, "--stdin-stop"]
    if not device:
        cmd = [part for part in cmd if part]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", bufsize=1)
    logs: List[str] = []
    ready = threading.Event()

    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            logs.append(line.rstrip())
            if "Waiting for 'stop'" in line or "Listening on forced device" in line:
                ready.set()

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    if not ready.wait(20.0):
        proc.kill()
        capture.stop()
        raise SelfTestError("recorder did not become ready:\n" + "\n".join(logs))

    time.sleep(0.3)
    script, meta = build_injection_script(screen, caps, event_dev)
    script_path = output.parent / "recorder_selftest_inject.sh"
    script_path.write_text(script, encoding="utf-8")
    (output.parent / "selftest_route_spec.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    adb(adb_path, device, ["push", str(script_path), INJECT_REMOTE])
    adb_shell(adb_path, device, f"sh {INJECT_REMOTE}", timeout=120.0)
    time.sleep(0.4)

    assert proc.stdin is not None
    proc.stdin.write("stop\n")
    proc.stdin.flush()
    proc.stdin.close()
    try:
        proc.wait(timeout=30.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise SelfTestError("recorder did not stop in time")
    thread.join(timeout=2.0)
    raw_lines = capture.stop()

    if proc.returncode != 0 or not output.exists():
        raise SelfTestError(f"recorder failed (exit {proc.returncode}):\n" + "\n".join(logs[-40:]))
    plan = json.loads(output.read_text(encoding="utf-8"))
    return plan, raw_lines, "\n".join(logs), meta


# ---------------------------------------------------------------------------
# validation


def action_models(actions: List[Dict]) -> List[Dict]:
    return [a for a in actions if a.get("type") in {"click", "trace", "long_press"}]


def measure_route_fidelity(
    png: bytes,
    plan_points: List[Tuple[int, int]],
    margin: int = 30,
) -> Dict[str, float]:
    """Compare the pointer-location trail in a screenshot with a planned route.

    - coverage_*: for samples along the planned polyline, distance to the
      nearest trail pixel. Large values mean a corner or segment was skipped.
    - precision_*: for trail pixels, distance to the nearest planned sample.
      Large values mean the replay went somewhere the plan did not ask for.
    """
    image = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(np.int16)
    mask = (
        (image[:, :, 2] > 110)
        & ((image[:, :, 2] - image[:, :, 0]) > 35)
        & ((image[:, :, 2] - image[:, :, 1]) > 15)
    )
    xs = [point[0] for point in plan_points]
    ys = [point[1] for point in plan_points]
    x0 = max(0, min(xs) - margin)
    x1 = min(image.shape[1], max(xs) + margin)
    y0 = max(0, min(ys) - margin)
    y1 = min(image.shape[0], max(ys) + margin)
    yy, xx = np.nonzero(mask[y0:y1, x0:x1])
    trail = np.column_stack([xx + x0, yy + y0]).astype(float)

    samples: List[Tuple[float, float]] = []
    for start, end in zip(plan_points[:-1], plan_points[1:]):
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        count = max(1, int(length // 2.0))
        for step in range(count + 1):
            ratio = step / float(count)
            samples.append(
                (
                    start[0] + (end[0] - start[0]) * ratio,
                    start[1] + (end[1] - start[1]) * ratio,
                )
            )
    planned = np.array(samples, dtype=float)

    if not len(trail):
        return {
            "trail_pixels": 0,
            "coverage_mean_px": float("inf"),
            "coverage_p95_px": float("inf"),
            "coverage_max_px": float("inf"),
            "precision_mean_px": float("inf"),
        }

    # Cap the comparison size for very long routes; the selftest route is small.
    if len(planned) > 4000:
        planned = planned[:: int(math.ceil(len(planned) / 4000.0))]
    if len(trail) > 6000:
        trail = trail[:: int(math.ceil(len(trail) / 6000.0))]

    distances = np.sqrt(((planned[:, None, :] - trail[None, :, :]) ** 2).sum(axis=2))
    coverage = distances.min(axis=1)
    precision = distances.min(axis=0)
    return {
        "trail_pixels": int(len(trail)),
        "coverage_mean_px": round(float(coverage.mean()), 2),
        "coverage_p95_px": round(float(np.percentile(coverage, 95)), 2),
        "coverage_max_px": round(float(coverage.max()), 2),
        "precision_mean_px": round(float(precision.mean()), 2),
        "precision_max_px": round(float(precision.max()), 2),
    }


def _nearest_distance(point: Tuple[int, int], points: List[Tuple[int, int]]) -> float:
    if not points:
        return float("inf")
    return min(math.dist(point, other) for other in points)


def validate_plan(
    plan: Dict,
    raw_lines: List[str],
    screen: Tuple[int, int],
    caps: Tuple[int, int],
    spec: Optional[Dict[str, object]] = None,
    tolerance_px: float = DEFAULT_TOLERANCE_PX,
    tolerance_sec: float = DEFAULT_TOLERANCE_SEC,
) -> List[str]:
    failures: List[str] = []
    raw_gestures = [g for g in parse_raw_gestures(raw_lines) if g.points]
    actions = action_models(plan.get("actions", []))
    waits = [a for a in plan.get("actions", []) if a.get("type") == "wait"]
    expected = list((spec or {}).get("gestures", []))  # type: ignore[arg-type]

    if expected and len(raw_gestures) != len(expected):
        failures.append(f"plan: expected {len(expected)} raw gestures, parsed {len(raw_gestures)}")
    if len(actions) != len(raw_gestures):
        failures.append(
            f"plan: expected {len(raw_gestures)} gesture actions, got {[a.get('type') for a in actions]}"
        )
        if not actions:
            return failures

    def kind_for(index: int, action: Dict) -> str:
        if index < len(expected):
            return str(expected[index].get("kind", ""))
        return {"click": "tap", "trace": "path", "long_press": "long_press"}.get(
            str(action.get("type")), ""
        )

    def spec_corners(gesture: Dict) -> List[Tuple[int, int]]:
        if str(gesture.get("kind")) != "path":
            return []
        points = gesture.get("points", [])  # type: ignore[assignment]
        return [(int(x), int(y)) for x, y in points]

    corner_checks = 0
    for index in range(min(len(actions), len(raw_gestures))):
        act = actions[index]
        gt = raw_gestures[index]
        kind = kind_for(index, act)
        raw_px = [_mapped(raw_x, raw_y, caps, screen) for _, raw_x, raw_y in gt.points]

        if kind == "tap":
            if act.get("type") != "click":
                failures.append(f"plan: gesture[{index}] should be click, got {act.get('type')}")
                continue
            expected_point = raw_px[-1]
            if math.dist((act["x"], act["y"]), expected_point) > tolerance_px:
                failures.append(f"plan: click[{index}] {(act['x'], act['y'])}, raw {expected_point}")
            if abs(int(act.get("duration_ms", 0)) - gt.duration_ms) > 80:
                failures.append(
                    f"plan: click[{index}] duration {act.get('duration_ms')}ms, raw {gt.duration_ms}ms"
                )
            continue

        if kind == "long_press":
            if act.get("type") != "long_press":
                failures.append(f"plan: gesture[{index}] should be long_press, got {act.get('type')}")
                continue
            expected_point = raw_px[0]
            if math.dist((act["x"], act["y"]), expected_point) > tolerance_px:
                failures.append(f"plan: long_press[{index}] {(act['x'], act['y'])}, raw {expected_point}")
            if abs(int(act.get("duration_ms", 0)) - gt.duration_ms) > 80:
                failures.append(
                    f"plan: long_press[{index}] {act.get('duration_ms')}ms, raw {gt.duration_ms}ms"
                )
            continue

        if act.get("type") != "trace":
            failures.append(f"plan: gesture[{index}] ({kind or 'path'}) should be trace, got {act.get('type')}")
            continue

        points = act["points"]
        plan_px = [(int(p["x"]), int(p["y"])) for p in points]
        start = plan_px[0]
        end = plan_px[-1]
        if math.dist(start, raw_px[0]) > tolerance_px:
            failures.append(f"plan: trace[{index}] starts at {start}, raw {raw_px[0]}")
        if math.dist(end, raw_px[-1]) > tolerance_px:
            failures.append(f"plan: trace[{index}] ends at {end}, raw {raw_px[-1]}")
        if int(points[0]["t_ms"]) != 0:
            failures.append(f"plan: trace[{index}] first t_ms={points[0]['t_ms']} (expected 0)")
        if abs(int(points[-1]["t_ms"]) - gt.duration_ms) > 80:
            failures.append(
                f"plan: trace[{index}] span {points[-1]['t_ms']}ms, raw {gt.duration_ms}ms"
            )

        # Every corner of the recorded route (and every intended segment
        # boundary) must survive in the plan; otherwise replay cuts corners.
        flags = bot.trace_corner_flags([{"x": x, "y": y} for x, y in raw_px])
        corners = [raw_px[i] for i, flag in enumerate(flags) if flag]
        corners += spec_corners(expected[index]) if index < len(expected) else []
        for corner in corners:
            corner_checks += 1
            distance = _nearest_distance(corner, plan_px)
            if distance > max(tolerance_px, 10.0):
                failures.append(f"plan: trace[{index}] lost corner {corner} (nearest {distance:.1f}px)")

    raw_gaps = [
        raw_gestures[i + 1].start_t - raw_gestures[i].end_t
        for i in range(len(raw_gestures) - 1)
    ]
    if len(waits) != len(raw_gaps):
        failures.append(f"plan: expected {len(raw_gaps)} waits, got {len(waits)}")
    else:
        for index, (wait, gap) in enumerate(zip(waits, raw_gaps)):
            seconds = float(wait.get("seconds", 0.0))
            if abs(seconds - gap) > tolerance_sec:
                failures.append(f"plan: wait[{index}]={seconds:.3f}s, raw gap {gap:.3f}s")

    print(f"[selftest] corners preserved in plan: {corner_checks} checked")
    return failures


def replay_plan_check(
    plan: Dict,
    adb_path: str,
    device: Optional[str],
    dst_screen: Tuple[int, int],
    output_dir: Optional[Path] = None,
    capture_routes: bool = False,
) -> Tuple[List[str], Dict[str, Dict[str, float]]]:
    failures: List[str] = []
    route_metrics: Dict[str, Dict[str, float]] = {}
    actions = list(plan.get("actions", []))
    trace_actions = [a for a in actions if a.get("type") == "trace"]
    if not trace_actions:
        return ["replay: plan has no trace to replay"], route_metrics

    screen_cfg = plan.get("screen_size", {})
    src_w = int(screen_cfg.get("width", dst_screen[0]))
    src_h = int(screen_cfg.get("height", dst_screen[1]))
    ctx = make_ctx(src_w, src_h, dst_screen[0], dst_screen[1], device=device)
    ctx.adb_path = adb_path
    ctx.motionevent_supported = True
    time_scale = float(plan.get("trace_time_scale", 1.0))

    route_dir: Optional[Path] = None
    if capture_routes and output_dir is not None:
        route_dir = output_dir / "complex_route"
        route_dir.mkdir(parents=True, exist_ok=True)
        (route_dir / "route_baseline.png").write_bytes(screenshot_png(adb_path, device))

    real_script = bot.adb_shell_script
    trace_index = 0
    for action_index, act in enumerate(actions):
        action_type = act.get("type")
        if action_type == "trace":
            captured: List[str] = []

            def capture_script(ctx_arg, script, timeout=120.0, _captured=captured):  # noqa: ANN001
                _captured.append(script)
                return real_script(ctx_arg, script, timeout=timeout)

            span_ms = int(act["points"][-1].get("t_ms", 0)) * time_scale
            started = time.perf_counter()
            bot.adb_shell_script = capture_script
            try:
                bot.do_trace(ctx, act)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"replay: trace[{trace_index}] failed: {exc}")
                trace_index += 1
                continue
            finally:
                bot.adb_shell_script = real_script
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            lower = max(120.0, span_ms * 0.85 - 100.0)
            upper = span_ms * 1.15 + 200.0
            if not (lower <= elapsed_ms <= upper):
                failures.append(
                    f"replay: trace[{trace_index}] took {elapsed_ms:.0f}ms, recorded timeline "
                    f"{span_ms:.0f}ms (allowed {lower:.0f}-{upper:.0f}ms)"
                )
            print(
                f"[selftest] replay trace[{trace_index}]: {elapsed_ms:.0f}ms wall, "
                f"{span_ms:.0f}ms recorded, {len(act['points'])} points"
            )

            # --- verify the emitted command stream keeps the route ---
            script = captured[0] if captured else ""
            if not script:
                failures.append(f"replay: trace[{trace_index}] emitted no command script")
            else:
                if "/proc/uptime" not in script or "read -t " not in script:
                    failures.append(f"replay: trace[{trace_index}] missing closed-loop timing")
                down = re.search(r"^input motionevent DOWN (\d+) (\d+)$", script, re.M)
                up = re.search(r"^input motionevent UP (\d+) (\d+)$", script, re.M)
                if not down or not up:
                    failures.append(f"replay: trace[{trace_index}] script missing DOWN/UP")
                # Unconditional MOVEs (critical samples) are not indented.
                unconditional = {
                    (int(x), int(y))
                    for x, y in re.findall(r"^input motionevent MOVE (\d+) (\d+)$", script, re.M)
                }
                conditional_count = len(re.findall(r"^\s+input motionevent MOVE ", script, re.M))

                def mapped(point: Dict) -> Tuple[int, int]:
                    x, y = bot.map_input_point(ctx, int(point["x"]), int(point["y"]))
                    return bot.clamp_input_point(ctx, x, y)

                jitter_px = int(act.get("trace_jitter_px", 0) or 0)
                if jitter_px == 0:
                    if down:
                        first = mapped(act["points"][0])
                        if (int(down.group(1)), int(down.group(2))) != first:
                            failures.append(
                                f"replay: trace[{trace_index}] DOWN {down.groups()} != start {first}"
                            )
                    if up:
                        last = mapped(act["points"][-1])
                        if (int(up.group(1)), int(up.group(2))) != last:
                            failures.append(
                                f"replay: trace[{trace_index}] UP {up.groups()} != end {last}"
                            )

                    corner_flags = bot.trace_corner_flags(act["points"])
                    corners = [
                        mapped(point) for point, flag in zip(act["points"], corner_flags) if flag
                    ]
                    for corner in corners:
                        if corner not in unconditional:
                            failures.append(
                                f"replay: trace[{trace_index}] corner {corner} is skippable"
                            )
                    print(
                        f"[selftest] trace[{trace_index}] corners emitted unconditionally: "
                        f"{sum(1 for c in corners if c in unconditional)}/{len(corners)}, "
                        f"skippable samples: {conditional_count}"
                    )

            trace_index += 1
            if route_dir is not None:
                time.sleep(0.45)
                route_path = route_dir / f"route_trace_{trace_index}.png"
                png = screenshot_png(adb_path, device)
                route_path.write_bytes(png)
                if int(act.get("trace_jitter_px", 0) or 0) == 0:
                    plan_points = [
                        bot.clamp_input_point(
                            ctx,
                            *bot.map_input_point(ctx, int(point["x"]), int(point["y"])),
                        )
                        for point in act["points"]
                    ]
                    metrics = measure_route_fidelity(png, plan_points)
                    route_metrics[f"trace_{trace_index}"] = metrics
                    print(
                        f"[selftest] route screenshot: {route_path} "
                        f"(coverage mean {metrics['coverage_mean_px']}px, "
                        f"max {metrics['coverage_max_px']}px, "
                        f"trail pixels {metrics['trail_pixels']})"
                    )
                    if metrics["trail_pixels"] <= 0:
                        failures.append(f"replay: trace[{trace_index - 1}] produced no visible trail")
                    elif (
                        metrics["coverage_mean_px"] > 4.0
                        or metrics["coverage_max_px"] > 12.0
                        or metrics["precision_mean_px"] > 4.0
                    ):
                        failures.append(
                            f"replay: trace[{trace_index - 1}] trail deviates from plan "
                            f"(coverage mean {metrics['coverage_mean_px']}px / "
                            f"max {metrics['coverage_max_px']}px, "
                            f"precision mean {metrics['precision_mean_px']}px)"
                        )
                else:
                    print(f"[selftest] route screenshot: {route_path} (jitter enabled, metrics skipped)")
        elif action_type == "click":
            try:
                bot.do_click(ctx, act)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"replay: action[{action_index}] click failed: {exc}")
        elif action_type == "long_press":
            try:
                bot.do_long_press(ctx, act)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"replay: action[{action_index}] long_press failed: {exc}")

    return failures, route_metrics


# ---------------------------------------------------------------------------
# main


def main() -> int:
    parser = argparse.ArgumentParser(description="Record + replay self-test for record_touch.py")
    parser.add_argument("--device", help="ADB serial, e.g. emulator-5554")
    parser.add_argument("--adb", default="adb", help="ADB binary path")
    parser.add_argument("--event-dev", help="Touch event device, auto-detected when omitted")
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parent.parent / "diagnostics" / "recorder_selftest"))
    parser.add_argument("--offline", action="store_true", help="Run only the offline unit checks")
    parser.add_argument("--no-sandbox", action="store_true", help="Do not open the gallery sandbox image")
    parser.add_argument("--skip-replay", action="store_true", help="Skip the on-device replay timing check")
    parser.add_argument(
        "--no-visual-routes",
        action="store_true",
        help="Do not enable the pointer-location overlay / capture route screenshots",
    )
    parser.add_argument("--tolerance-px", type=float, default=DEFAULT_TOLERANCE_PX)
    parser.add_argument("--tolerance-sec", type=float, default=DEFAULT_TOLERANCE_SEC)
    args = parser.parse_args()

    failures: List[str] = offline_checks()
    print(f"[selftest] offline checks: {'PASS' if not failures else 'FAIL'} "
          f"({len(failures)} issue(s))")
    for item in failures:
        print(f"  - {item}")

    if args.offline:
        print(json.dumps({"offline_failures": failures}, ensure_ascii=False, indent=2))
        return 1 if failures else 0

    if not args.device:
        devices = rt.list_connected_devices(args.adb)
        if not devices:
            raise SelfTestError("No adb device connected; pass --device or use --offline")
        args.device = devices[0]

    screen = screen_size(args.adb, args.device)
    caps_map = rt.get_touch_devices_and_max(args.adb, args.device)
    event_dev = args.event_dev or sorted(caps_map)[0]
    if event_dev not in caps_map:
        raise SelfTestError(f"{event_dev} is not a touch device ({sorted(caps_map)})")
    caps = caps_map[event_dev]
    print(f"[selftest] device={args.device} screen={screen[0]}x{screen[1]} event_dev={event_dev} raw_max={caps}")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not args.no_sandbox:
        focus = ensure_sandbox(args.adb, args.device, output_dir)
        print(f"[selftest] sandbox focus: {focus or 'unknown'}")
        if "gallery" not in focus.lower():
            print("[selftest] WARNING: gallery sandbox did not take focus; gestures may hit another app")

    plan_path = output_dir / "selftest_plan.json"
    plan, raw_lines, recorder_log, route_spec = record_plan(
        args.adb, args.device, event_dev, plan_path, screen, caps
    )
    print(f"[selftest] recorded {len(action_models(plan.get('actions', [])))} gesture action(s), "
          f"raw lines={len(raw_lines)}")

    failures += validate_plan(
        plan, raw_lines, screen, caps, route_spec, args.tolerance_px, args.tolerance_sec
    )

    visual_routes = not args.skip_replay and not args.no_visual_routes
    previous_pointer_location = pointer_location_setting(args.adb, args.device)
    if visual_routes:
        set_pointer_location(args.adb, args.device, True)
        print("[selftest] pointer-location overlay enabled for route screenshots")
    route_metrics: Dict[str, Dict[str, float]] = {}
    try:
        if not args.skip_replay:
            replay_failures, route_metrics = replay_plan_check(
                plan,
                args.adb,
                args.device,
                screen,
                output_dir=output_dir,
                capture_routes=visual_routes,
            )
            failures += replay_failures
    finally:
        if visual_routes:
            set_pointer_location(args.adb, args.device, previous_pointer_location == "1")
            print("[selftest] pointer-location overlay restored")

    report = {
        "device": args.device,
        "screen": {"width": screen[0], "height": screen[1]},
        "event_dev": event_dev,
        "raw_max": {"x": caps[0], "y": caps[1]},
        "plan_path": str(plan_path),
        "route_spec": route_spec,
        "actions": plan.get("actions", []),
        "route_metrics": route_metrics,
        "failures": failures,
    }
    report_path = output_dir / "selftest_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[selftest] report: {report_path}")

    if failures:
        print(f"[selftest] RESULT: FAIL ({len(failures)} issue(s))")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("[selftest] RESULT: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SelfTestError as exc:
        print(f"[selftest] ERROR: {exc}")
        raise SystemExit(2)
