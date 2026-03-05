#!/usr/bin/env python3
import argparse
import json
import math
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class RecorderError(RuntimeError):
    pass


@dataclass
class Point:
    t: float
    x_raw: int
    y_raw: int


@dataclass
class Gesture:
    start_t: float
    end_t: float
    points: List[Point]
    explicit_touch: bool


WM_SIZE_RE = re.compile(r"(\d+)x(\d+)")
EVENT_WITH_DEV_RE = re.compile(
    r"\[\s*([0-9]+\.[0-9]+)\]\s+(/dev/input/event\d+):\s+([0-9a-fA-F]{4})\s+([0-9a-fA-F]{4})\s+([0-9a-fA-F]{8})"
)
EVENT_NO_DEV_RE = re.compile(r"\[\s*([0-9]+\.[0-9]+)\]\s+([0-9a-fA-F]{4})\s+([0-9a-fA-F]{4})\s+([0-9a-fA-F]{8})")
EVENT_TEXT_WITH_DEV_RE = re.compile(
    r"\[\s*([0-9]+\.[0-9]+)\]\s+(/dev/input/event\d+):\s+([A-Z_]+)\s+([A-Z0-9_]+)\s+([0-9a-fA-F]{1,8})"
)
EVENT_TEXT_NO_DEV_RE = re.compile(r"\[\s*([0-9]+\.[0-9]+)\]\s+([A-Z_]+)\s+([A-Z0-9_]+)\s+([0-9a-fA-F]{1,8})")
ADD_DEVICE_RE = re.compile(r"add device \d+:\s+(/dev/input/event\d+)")
ABS_X_RE = re.compile(r"ABS_MT_POSITION_X")
ABS_Y_RE = re.compile(r"ABS_MT_POSITION_Y")
ABS_X_FALLBACK_RE = re.compile(r"\bABS_X\b")
ABS_Y_FALLBACK_RE = re.compile(r"\bABS_Y\b")
MAX_RE = re.compile(r"max\s+(\d+)")

ETYPE_MAP = {
    "EV_SYN": "0000",
    "EV_KEY": "0001",
    "EV_ABS": "0003",
}

ECODE_MAP = {
    "SYN_REPORT": "0000",
    "ABS_X": "0000",
    "ABS_Y": "0001",
    "ABS_MT_POSITION_X": "0035",
    "ABS_MT_POSITION_Y": "0036",
    "ABS_MT_TRACKING_ID": "0039",
    "BTN_TOUCH": "014a",
}


def run_cmd(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def adb_cmd(adb: str, device: Optional[str], extra: List[str]) -> List[str]:
    cmd = [adb]
    if device:
        cmd += ["-s", device]
    cmd += extra
    return cmd


def list_connected_devices(adb: str) -> List[str]:
    result = run_cmd([adb, "devices"], check=False)
    lines = [line.strip() for line in result.stdout.splitlines()[1:] if line.strip()]
    return [line.split()[0] for line in lines if "\tdevice" in line]


def is_shell_healthy(adb: str, device: str) -> bool:
    for _ in range(3):
        result = run_cmd([adb, "-s", device, "shell", "getprop", "ro.build.version.release"], check=False)
        text = f"{result.stdout}\n{result.stderr}".lower()
        if result.returncode == 0 and "error: closed" not in text:
            return True
        time.sleep(0.2)
    return False


def resolve_record_device(adb: str, preferred: Optional[str]) -> Optional[str]:
    devices = list_connected_devices(adb)
    pref = preferred.strip() if isinstance(preferred, str) else None
    if pref:
        if pref not in devices:
            run_cmd([adb, "connect", pref], check=False)
            devices = list_connected_devices(adb)
        if pref in devices and is_shell_healthy(adb, pref):
            return pref
        for d in devices:
            if d != pref and is_shell_healthy(adb, d):
                print(f"[Recorder] Preferred device {pref} unhealthy, fallback to {d}")
                return d
        return pref

    for d in devices:
        if is_shell_healthy(adb, d):
            print(f"[Recorder] Auto-selected healthy device: {d}")
            return d
    return pref


def restart_adb_server(adb: str) -> None:
    print("[Recorder] Device discovery failed, restart adb server and retry once ...")
    run_cmd([adb, "kill-server"], check=False)
    run_cmd([adb, "start-server"], check=False)
    time.sleep(0.6)


def get_screen_size(adb: str, device: Optional[str]) -> Tuple[int, int]:
    last_text = ""
    for _ in range(3):
        result = run_cmd(adb_cmd(adb, device, ["shell", "wm", "size"]), check=False)
        text = f"{result.stdout}\n{result.stderr}"
        last_text = text
        m = WM_SIZE_RE.search(text)
        if m:
            return int(m.group(1)), int(m.group(2))
        time.sleep(0.2)
    raise RecorderError(f"Cannot parse wm size output:\n{last_text}")


def get_touch_devices_and_max(adb: str, device: Optional[str]) -> Dict[str, Tuple[int, int]]:
    result = run_cmd(adb_cmd(adb, device, ["shell", "getevent", "-lp"]), check=False)
    text = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0:
        raise RecorderError(f"getevent -lp failed:\n{text}")

    lines = text.splitlines()
    current_dev: Optional[str] = None
    device_info: Dict[str, Dict[str, Optional[int]]] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        add_match = ADD_DEVICE_RE.search(line)
        if add_match:
            current_dev = add_match.group(1)
            device_info[current_dev] = {"x_max": None, "y_max": None, "has_x": False, "has_y": False}
            i += 1
            continue
        if current_dev is None:
            i += 1
            continue
        info = device_info[current_dev]
        if ABS_X_RE.search(line) or ABS_X_FALLBACK_RE.search(line):
            info["has_x"] = True
            m = MAX_RE.search(line)
            if m:
                info["x_max"] = int(m.group(1))
        if ABS_Y_RE.search(line) or ABS_Y_FALLBACK_RE.search(line):
            info["has_y"] = True
            m = MAX_RE.search(line)
            if m:
                info["y_max"] = int(m.group(1))
        i += 1

    caps: Dict[str, Tuple[int, int]] = {}
    for dev, info in device_info.items():
        if info["has_x"] and info["has_y"] and info["x_max"] and info["y_max"]:
            caps[dev] = (int(info["x_max"]), int(info["y_max"]))
    if not caps:
        raise RecorderError("No touch device with ABS_MT_POSITION_X/Y found in getevent -lp output.")
    return caps


def raw_to_px(raw: int, raw_max: int, px_max: int, invert: bool = False) -> int:
    if raw_max <= 0:
        return 0
    ratio = max(0.0, min(1.0, raw / raw_max))
    if invert:
        ratio = 1.0 - ratio
    return int(round(ratio * px_max))


def map_raw_point_to_screen(
    raw_x: int,
    raw_y: int,
    x_raw_max: int,
    y_raw_max: int,
    screen_w: int,
    screen_h: int,
    invert_x: bool,
    invert_y: bool,
    swap_xy: bool,
) -> Tuple[int, int]:
    if swap_xy:
        mapped_x = raw_to_px(raw_y, y_raw_max, screen_w - 1, invert=invert_x)
        mapped_y = raw_to_px(raw_x, x_raw_max, screen_h - 1, invert=invert_y)
    else:
        mapped_x = raw_to_px(raw_x, x_raw_max, screen_w - 1, invert=invert_x)
        mapped_y = raw_to_px(raw_y, y_raw_max, screen_h - 1, invert=invert_y)
    return mapped_x, mapped_y


def build_actions_from_gestures(
    gestures: List[Gesture],
    x_raw_max: int,
    y_raw_max: int,
    screen_w: int,
    screen_h: int,
    invert_x: bool = False,
    invert_y: bool = False,
    swap_xy: bool = False,
) -> List[Dict]:
    actions: List[Dict] = []
    prev_end: Optional[float] = None
    tap_distance_px = 24.0
    tap_duration_sec = 0.45

    for g in gestures:
        if not g.points:
            continue
        if prev_end is not None:
            gap = max(0.0, g.start_t - prev_end)
            if gap >= 0.02:
                actions.append({"type": "wait", "seconds": round(gap, 3)})
        prev_end = g.end_t

        first = g.points[0]
        last = g.points[-1]
        x1s, y1s = map_raw_point_to_screen(
            first.x_raw, first.y_raw, x_raw_max, y_raw_max, screen_w, screen_h, invert_x, invert_y, swap_xy
        )
        x2s, y2s = map_raw_point_to_screen(
            last.x_raw, last.y_raw, x_raw_max, y_raw_max, screen_w, screen_h, invert_x, invert_y, swap_xy
        )
        x1, y1 = x1s, y1s
        x2, y2 = x2s, y2s
        dist = math.hypot(x2 - x1, y2 - y1)
        duration = max(0.0, g.end_t - g.start_t)

        point_count = len(g.points)
        if g.explicit_touch and point_count <= 12 and dist <= tap_distance_px and duration <= tap_duration_sec:
            actions.append({"type": "click", "x": x2, "y": y2})
        else:
            px_points: List[Dict] = []
            last_px: Optional[Tuple[int, int]] = None
            start_t = g.start_t
            # Keep significant points only; this preserves trajectory and controls JSON size.
            for p in g.points:
                pxs, pys = map_raw_point_to_screen(
                    p.x_raw, p.y_raw, x_raw_max, y_raw_max, screen_w, screen_h, invert_x, invert_y, swap_xy
                )
                px, py = pxs, pys
                if last_px is not None:
                    if math.hypot(px - last_px[0], py - last_px[1]) < 4.0:
                        continue
                last_px = (px, py)
                px_points.append({"x": px, "y": py, "t_ms": int((p.t - start_t) * 1000)})

            # Ensure last point exists, and cap count to avoid oversized plans.
            if not px_points or (px_points[-1]["x"] != x2 or px_points[-1]["y"] != y2):
                px_points.append({"x": x2, "y": y2, "t_ms": int(duration * 1000)})
            if len(px_points) > 80:
                step = max(1, len(px_points) // 80)
                sampled = px_points[::step]
                if sampled[-1] != px_points[-1]:
                    sampled.append(px_points[-1])
                px_points = sampled

            if len(px_points) < 2:
                actions.append({"type": "click", "x": x2, "y": y2})
            else:
                actions.append(
                    {
                        "type": "trace",
                        "points": px_points,
                        "min_segment_ms": 16,
                        "max_segment_ms": 80,
                    }
                )
    return actions


def _point_distance(a: Dict, b: Dict) -> float:
    return math.hypot(float(a["x"]) - float(b["x"]), float(a["y"]) - float(b["y"]))


def _trace_start_end(action: Dict) -> Tuple[Optional[Dict], Optional[Dict]]:
    points = action.get("points")
    if not isinstance(points, list) or len(points) < 2:
        return None, None
    return points[0], points[-1]


def clean_actions_noise(actions: List[Dict]) -> List[Dict]:
    # Mild cleanup: remove accidental click noise near adjacent trace boundaries.
    def is_wait(act: Dict) -> bool:
        return act.get("type") == "wait"

    def is_click(act: Dict) -> bool:
        return act.get("type") == "click"

    def is_trace(act: Dict) -> bool:
        return act.get("type") == "trace"

    def nearest_non_wait_left(i: int) -> Optional[int]:
        j = i - 1
        while j >= 0 and is_wait(actions[j]):
            j -= 1
        return j if j >= 0 else None

    def nearest_non_wait_right(i: int) -> Optional[int]:
        j = i + 1
        while j < len(actions) and is_wait(actions[j]):
            j += 1
        return j if j < len(actions) else None

    def total_wait_between(a: int, b: int) -> float:
        start, end = sorted((a, b))
        total = 0.0
        for k in range(start + 1, end):
            if is_wait(actions[k]):
                total += float(actions[k].get("seconds", 0.0))
        return total

    cleaned: List[Dict] = []
    removed = 0
    for i, act in enumerate(actions):
        if not is_click(act):
            cleaned.append(act)
            continue

        left = nearest_non_wait_left(i)
        right = nearest_non_wait_right(i)
        click_pt = {"x": act.get("x", 0), "y": act.get("y", 0)}

        should_remove = False
        near_px = 14.0
        short_pause = 0.35

        if left is not None and is_trace(actions[left]):
            _, left_end = _trace_start_end(actions[left])
            if left_end is not None:
                d_left = _point_distance(click_pt, left_end)
                w_left = total_wait_between(left, i)
                if d_left <= near_px and w_left <= short_pause:
                    # Tail tap after trace end is likely noise.
                    should_remove = True

        if right is not None and is_trace(actions[right]):
            right_start, _ = _trace_start_end(actions[right])
            if right_start is not None:
                d_right = _point_distance(click_pt, right_start)
                w_right = total_wait_between(i, right)
                if d_right <= near_px and w_right <= short_pause:
                    # If click bridges trace->trace boundary, it's likely accidental.
                    if left is not None and is_trace(actions[left]):
                        should_remove = True

        if should_remove:
            removed += 1
        else:
            cleaned.append(act)

    print(f"[Recorder] Noise clean removed click count: {removed}")
    return cleaned


def record_gestures(
    adb: str,
    device: Optional[str],
    touch_caps: Dict[str, Tuple[int, int]],
    min_points: int,
    forced_event_dev: Optional[str],
    stop_after_gestures: Optional[int] = None,
) -> Tuple[List[Gesture], str]:
    cmd = adb_cmd(adb, device, ["shell", "getevent", "-lt"])
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    assert proc.stdout is not None

    selected_dev: Optional[str] = forced_event_dev if forced_event_dev else None
    fallback_only_dev: Optional[str] = None
    if len(touch_caps) == 1:
        fallback_only_dev = next(iter(touch_caps.keys()))
    if selected_dev and selected_dev not in touch_caps:
        raise RecorderError(f"Forced event device {selected_dev} not in touch-capable list.")

    gestures: List[Gesture] = []
    touching = False
    current_explicit_touch = False
    cur_points: List[Point] = []
    cur_start_t: Optional[float] = None
    last_t = 0.0
    x_raw: Optional[int] = None
    y_raw: Optional[int] = None
    saw_touch_flag = False
    last_point_t: Optional[float] = None
    idle_split_sec = 0.25
    seen_event_lines = 0
    unmatched_samples: List[str] = []

    if selected_dev:
        print(f"[Recorder] Listening on forced device {selected_dev}. Press Ctrl+C to stop and save.")
    else:
        print("[Recorder] Listening on all input events. Touch BlueStacks to auto-detect touch device.")
        print("[Recorder] Press Ctrl+C to stop and save.")
    try:
        for line in proc.stdout:
            line_s = line.strip()
            m = EVENT_WITH_DEV_RE.search(line_s)
            dev: Optional[str] = None
            if m:
                seen_event_lines += 1
                t = float(m.group(1))
                dev = m.group(2)
                etype = m.group(3).lower()
                ecode = m.group(4).lower()
                evalue_hex = m.group(5).lower()
            else:
                mt = EVENT_TEXT_WITH_DEV_RE.search(line_s)
                if mt:
                    seen_event_lines += 1
                    t = float(mt.group(1))
                    dev = mt.group(2)
                    etype = ETYPE_MAP.get(mt.group(3), "").lower()
                    ecode = ECODE_MAP.get(mt.group(4), "").lower()
                    evalue_hex = mt.group(5).lower()
                else:
                    m2 = EVENT_NO_DEV_RE.search(line_s)
                    if m2:
                        seen_event_lines += 1
                        t = float(m2.group(1))
                        etype = m2.group(2).lower()
                        ecode = m2.group(3).lower()
                        evalue_hex = m2.group(4).lower()
                        # Some Android builds omit /dev/input/eventX in -lt output.
                        dev = fallback_only_dev
                    else:
                        mt2 = EVENT_TEXT_NO_DEV_RE.search(line_s)
                        if not mt2:
                            if line_s and len(unmatched_samples) < 8:
                                unmatched_samples.append(line_s)
                            continue
                        seen_event_lines += 1
                        t = float(mt2.group(1))
                        etype = ETYPE_MAP.get(mt2.group(2), "").lower()
                        ecode = ECODE_MAP.get(mt2.group(3), "").lower()
                        evalue_hex = mt2.group(4).lower()
                        dev = fallback_only_dev

            if dev is None:
                continue
            if not etype or not ecode:
                if line_s and len(unmatched_samples) < 8:
                    unmatched_samples.append(line_s)
                continue
            evalue = int(evalue_hex, 16)

            if selected_dev is None:
                is_touch_signal = (
                    (etype == "0003" and ecode in {"0035", "0036", "0039", "0000", "0001"})
                    or (etype == "0001" and ecode == "014a")
                )
                if is_touch_signal and dev in touch_caps:
                    selected_dev = dev
                    print(f"[Recorder] Auto-selected touch device: {selected_dev}")

            if selected_dev is None or dev != selected_dev:
                continue

            # EV_ABS
            if etype == "0003":
                if ecode in {"0035", "0000"}:
                    x_raw = evalue
                elif ecode in {"0036", "0001"}:
                    y_raw = evalue
                elif ecode == "0039":
                    saw_touch_flag = True
                    # ABS_MT_TRACKING_ID, 0xffffffff means up
                    if evalue_hex == "ffffffff":
                        if touching and cur_points:
                            gestures.append(
                                Gesture(
                                    start_t=cur_start_t or t,
                                    end_t=t,
                                    points=cur_points[:],
                                    explicit_touch=current_explicit_touch,
                                )
                            )
                            print(f"[Recorder] Gesture captured: {len(cur_points)} points")
                            if stop_after_gestures and len(gestures) >= stop_after_gestures:
                                break
                        touching = False
                        cur_points = []
                        cur_start_t = None
                        current_explicit_touch = False
                    else:
                        touching = True
                        cur_start_t = t
                        cur_points = []
                        current_explicit_touch = True
            # EV_KEY BTN_TOUCH
            elif etype == "0001" and ecode == "014a":
                saw_touch_flag = True
                if evalue == 1:
                    touching = True
                    cur_start_t = t
                    cur_points = []
                    current_explicit_touch = True
                elif evalue == 0:
                    if touching and cur_points:
                        gestures.append(
                            Gesture(
                                start_t=cur_start_t or t,
                                end_t=t,
                                points=cur_points[:],
                                explicit_touch=current_explicit_touch,
                            )
                        )
                        print(f"[Recorder] Gesture captured: {len(cur_points)} points")
                        if stop_after_gestures and len(gestures) >= stop_after_gestures:
                            break
                    touching = False
                    cur_points = []
                    cur_start_t = None
                    current_explicit_touch = False
            # EV_SYN SYN_REPORT
            elif etype == "0000" and ecode == "0000":
                # Fallback split: some devices don't emit BTN_TOUCH/TRACKING_ID.
                if not saw_touch_flag and last_point_t is not None and touching and (t - last_point_t) > idle_split_sec:
                    if cur_points:
                        gestures.append(
                            Gesture(
                                start_t=cur_start_t or last_point_t,
                                end_t=last_point_t,
                                points=cur_points[:],
                                explicit_touch=False,
                            )
                        )
                        print(f"[Recorder] Gesture captured (idle split): {len(cur_points)} points")
                        if stop_after_gestures and len(gestures) >= stop_after_gestures:
                            break
                    touching = False
                    cur_points = []
                    cur_start_t = None
                    current_explicit_touch = False
                if touching and x_raw is not None and y_raw is not None:
                    cur_points.append(Point(t=t, x_raw=x_raw, y_raw=y_raw))
                    last_t = t
                    last_point_t = t
                elif not saw_touch_flag and x_raw is not None and y_raw is not None:
                    # Start pseudo-touch if only ABS coordinates are reported.
                    touching = True
                    if cur_start_t is None:
                        cur_start_t = t
                    cur_points.append(Point(t=t, x_raw=x_raw, y_raw=y_raw))
                    last_t = t
                    last_point_t = t
                    current_explicit_touch = False
    except KeyboardInterrupt:
        print("\n[Recorder] Stopping...")
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            proc.kill()

    # Capture trailing gesture if not closed properly.
    if touching and cur_points:
        gestures.append(
            Gesture(
                start_t=cur_start_t or last_t,
                end_t=last_t,
                points=cur_points[:],
                explicit_touch=current_explicit_touch,
            )
        )

    filtered = [g for g in gestures if len(g.points) >= min_points]
    print(f"[Recorder] Kept gestures: {len(filtered)} / raw {len(gestures)}")
    if seen_event_lines == 0 and unmatched_samples:
        print("[Recorder] Debug sample (unparsed getevent lines):")
        for sample in unmatched_samples:
            print(f"  {sample}")
    if selected_dev is None:
        raise RecorderError("No active touch device detected during recording.")
    return filtered, selected_dev


def recommend_mapping(
    gesture: Gesture,
    x_raw_max: int,
    y_raw_max: int,
    screen_w: int,
    screen_h: int,
) -> Dict[str, object]:
    first = gesture.points[0]
    last = gesture.points[-1]
    combos = []
    for invert_x in (False, True):
        for invert_y in (False, True):
            for swap_xy in (False, True):
                x1s, y1s = map_raw_point_to_screen(
                    first.x_raw, first.y_raw, x_raw_max, y_raw_max, screen_w, screen_h, invert_x, invert_y, swap_xy
                )
                x2s, y2s = map_raw_point_to_screen(
                    last.x_raw, last.y_raw, x_raw_max, y_raw_max, screen_w, screen_h, invert_x, invert_y, swap_xy
                )
                x1, y1 = x1s, y1s
                x2, y2 = x2s, y2s
                dx = x2 - x1
                dy = y2 - y1
                # Expected calibration stroke: down-left => dx<0 and dy>0.
                score = 0
                if dx < 0:
                    score += 1
                if dy > 0:
                    score += 1
                score += min(1.0, abs(dx) / 200.0) + min(1.0, abs(dy) / 200.0)
                combos.append(
                    {
                        "invert_x": invert_x,
                        "invert_y": invert_y,
                        "swap_xy": swap_xy,
                        "dx": dx,
                        "dy": dy,
                        "score": score,
                    }
                )
    combos.sort(key=lambda c: float(c["score"]), reverse=True)
    return {"best": combos[0], "candidates": combos[:4]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Record BlueStacks touch events and export plan JSON")
    parser.add_argument("--output", required=True, help="Output plan JSON path")
    parser.add_argument("--device", help="ADB serial, e.g. 127.0.0.1:5555")
    parser.add_argument("--adb", default="adb", help="ADB binary path")
    parser.add_argument("--event-dev", help="Force event device, e.g. /dev/input/event2")
    parser.add_argument("--loop-count", type=int, default=1, help="Wrap actions in loop. -1 means infinite.")
    parser.add_argument("--min-points", type=int, default=1, help="Filter gestures with fewer points")
    parser.add_argument("--jitter-px", type=int, default=0, help="jitter_px in exported plan")
    parser.add_argument("--no-clean-noise", action="store_true", help="Disable mild post-record cleanup")
    parser.add_argument("--invert-x", action="store_true", help="Invert X axis mapping")
    parser.add_argument("--invert-y", action="store_true", help="Invert Y axis mapping")
    parser.add_argument("--swap-xy", action="store_true", help="Swap raw X/Y mapping before inversion")
    parser.add_argument("--mapping-lock", action="store_true", help="Write mapping_locked=true in output plan")
    parser.add_argument("--calibrate-mapping", action="store_true", help="Record one stroke and print best mapping")
    args = parser.parse_args()

    retried = False
    while True:
        effective_device = resolve_record_device(args.adb, args.device)
        try:
            if not effective_device:
                raise RecorderError("No healthy adb device found.")
            print(f"[Recorder] Using device: {effective_device}")
            screen_w, screen_h = get_screen_size(args.adb, effective_device)
            touch_caps = get_touch_devices_and_max(args.adb, effective_device)
            break
        except RecorderError:
            if retried:
                raise
            retried = True
            restart_adb_server(args.adb)

    if args.event_dev and args.event_dev not in touch_caps:
        raise RecorderError(f"event-dev not touch-capable: {args.event_dev}")
    print(f"[Recorder] Screen: {screen_w}x{screen_h}")
    print("[Recorder] Touch candidates:")
    for dev, (x_max, y_max) in sorted(touch_caps.items()):
        print(f"  - {dev} (x_max={x_max}, y_max={y_max})")

    gestures, selected_dev = record_gestures(
        args.adb,
        effective_device,
        touch_caps,
        min_points=max(1, args.min_points),
        forced_event_dev=args.event_dev,
        stop_after_gestures=1 if args.calibrate_mapping else None,
    )
    x_max, y_max = touch_caps[selected_dev]
    print(f"[Recorder] Using touch device: {selected_dev}, raw max: x={x_max}, y={y_max}")

    if args.calibrate_mapping:
        if not gestures:
            raise RecorderError("No gesture captured for calibration.")
        longest = max(gestures, key=lambda g: len(g.points))
        rec = recommend_mapping(
            longest,
            x_max,
            y_max,
            screen_w,
            screen_h,
        )
        print(f"[Recorder] RECOMMENDED: {json.dumps(rec, ensure_ascii=False)}")
        return 0

    actions = build_actions_from_gestures(
        gestures,
        x_max,
        y_max,
        screen_w,
        screen_h,
        invert_x=bool(args.invert_x),
        invert_y=bool(args.invert_y),
        swap_xy=bool(args.swap_xy),
    )
    if not args.no_clean_noise:
        actions = clean_actions_noise(actions)
    if not actions:
        raise RecorderError("No actions captured. Please record again.")

    if args.loop_count == 1:
        final_actions = actions
    else:
        final_actions = [{"type": "loop", "count": int(args.loop_count), "actions": actions}]

    plan = {
        "jitter_px": int(args.jitter_px),
        "max_runtime_sec": 0,
        "actions": final_actions,
        "mapping_profile": {
            "invert_x": bool(args.invert_x),
            "invert_y": bool(args.invert_y),
            "swap_xy": bool(args.swap_xy),
        },
    }
    if args.mapping_lock:
        plan["mapping_locked"] = True
    if effective_device and effective_device.strip():
        plan["device"] = effective_device.strip()
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[Recorder] Saved: {out_path}")
    print(f"[Recorder] Actions: {len(actions)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RecorderError as exc:
        print(f"[Recorder] ERROR: {exc}")
        raise SystemExit(1)
