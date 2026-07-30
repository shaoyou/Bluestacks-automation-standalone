#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple


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

DEFAULT_RECORDING_PROFILE: Dict[str, float] = {
    "tap_distance_px": 24.0,
    "tap_duration_sec": 0.45,
    "min_swipe_duration_sec": 0.18,
    "merge_gap_sec": 0.35,
    "merge_distance_px": 10.0,
    "continuation_gap_sec": 1.2,
    "continuation_distance_px": 12.0,
    "continuation_span_px": 40.0,
    "continuation_duration_ms": 700.0,
}

SPARSE_ABS_IDLE_SPLIT_SEC = 0.55


def run_cmd(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, encoding="utf-8", errors="replace", capture_output=True, check=check)


def normalize_profile(raw: Optional[Dict[str, object]]) -> Dict[str, float]:
    profile = dict(DEFAULT_RECORDING_PROFILE)
    if isinstance(raw, dict):
        for key, default in DEFAULT_RECORDING_PROFILE.items():
            value = raw.get(key)
            if isinstance(value, (int, float)):
                profile[key] = float(value)
    profile["tap_distance_px"] = max(4.0, profile["tap_distance_px"])
    profile["tap_duration_sec"] = max(0.05, profile["tap_duration_sec"])
    profile["min_swipe_duration_sec"] = max(0.05, profile["min_swipe_duration_sec"])
    profile["merge_gap_sec"] = max(0.0, profile["merge_gap_sec"])
    profile["merge_distance_px"] = max(0.0, profile["merge_distance_px"])
    profile["continuation_gap_sec"] = max(profile["merge_gap_sec"], profile["continuation_gap_sec"])
    profile["continuation_distance_px"] = max(profile["merge_distance_px"], profile["continuation_distance_px"])
    profile["continuation_span_px"] = max(1.0, profile["continuation_span_px"])
    profile["continuation_duration_ms"] = max(1.0, profile["continuation_duration_ms"])
    return profile


def load_recording_profile(path: Optional[str]) -> Dict[str, float]:
    if not path:
        return normalize_profile(None)
    profile_path = Path(path)
    if not profile_path.exists():
        return normalize_profile(None)
    try:
        raw = json.loads(profile_path.read_text(encoding="utf-8"))
    except Exception:
        return normalize_profile(None)
    return normalize_profile(raw)


def save_recording_profile(path: str, profile: Dict[str, float]) -> None:
    profile_path = Path(path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(normalize_profile(profile), ensure_ascii=False, indent=2), encoding="utf-8")


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


def adb_output_is_transient(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in lowered
        for token in [
            "device offline",
            "device not found",
            "more than one device",
            "closed",
            "cannot connect",
            "failed to check server version",
            "adb server didn't ack",
        ]
    )


def get_screen_size(adb: str, device: Optional[str]) -> Tuple[int, int]:
    last_text = ""
    recovered = False
    for attempt in range(5):
        result = run_cmd(adb_cmd(adb, device, ["shell", "wm", "size"]), check=False)
        text = f"{result.stdout}\n{result.stderr}"
        last_text = text
        m = WM_SIZE_RE.search(text)
        if m:
            return int(m.group(1)), int(m.group(2))

        if adb_output_is_transient(text) and not recovered:
            restart_adb_server(adb)
            if device:
                run_cmd([adb, "connect", device], check=False)
            recovered = True
        elif attempt < 4:
            time.sleep(0.3)
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


def px_to_raw(px: int, px_max: int, raw_max: int, invert: bool = False) -> int:
    if px_max <= 0 or raw_max <= 0:
        return 0
    ratio = max(0.0, min(1.0, px / float(px_max)))
    if invert:
        ratio = 1.0 - ratio
    return int(round(ratio * raw_max))


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


def map_screen_point_to_raw(
    px: int,
    py: int,
    x_raw_max: int,
    y_raw_max: int,
    screen_w: int,
    screen_h: int,
    invert_x: bool,
    invert_y: bool,
    swap_xy: bool,
) -> Tuple[int, int]:
    px_max_x = max(1, screen_w - 1)
    px_max_y = max(1, screen_h - 1)
    if swap_xy:
        raw_y = px_to_raw(px, px_max_x, y_raw_max, invert=invert_x)
        raw_x = px_to_raw(py, px_max_y, x_raw_max, invert=invert_y)
    else:
        raw_x = px_to_raw(px, px_max_x, x_raw_max, invert=invert_x)
        raw_y = px_to_raw(py, px_max_y, y_raw_max, invert=invert_y)
    return raw_x, raw_y


def build_actions_from_gestures(
    gestures: List[Gesture],
    x_raw_max: int,
    y_raw_max: int,
    screen_w: int,
    screen_h: int,
    invert_x: bool = False,
    invert_y: bool = False,
    swap_xy: bool = False,
    profile: Optional[Dict[str, float]] = None,
) -> List[Dict]:
    actions: List[Dict] = []
    prev_end: Optional[float] = None
    resolved_profile = normalize_profile(profile)

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
        click = gesture_to_click(x1s, y1s, x2s, y2s, g, resolved_profile)
        x2, y2 = x2s, y2s
        duration = max(0.0, g.end_t - g.start_t)

        if click is not None:
            actions.append(click)
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
                    if math.hypot(px - last_px[0], py - last_px[1]) < 2.0:
                        continue
                last_px = (px, py)
                px_points.append({"x": px, "y": py, "t_ms": int((p.t - start_t) * 1000)})

            # Preserve hold-at-end: keep last move time, then append a same-position end point.
            end_t_ms = int(duration * 1000)
            if not px_points:
                px_points.append({"x": x2, "y": y2, "t_ms": end_t_ms})
            elif px_points[-1]["x"] != x2 or px_points[-1]["y"] != y2:
                px_points.append({"x": x2, "y": y2, "t_ms": end_t_ms})
            else:
                last_t_ms = int(px_points[-1].get("t_ms", 0))
                if end_t_ms > last_t_ms:
                    px_points.append({"x": x2, "y": y2, "t_ms": end_t_ms})
            if len(px_points) > 180:
                step = max(1, len(px_points) // 180)
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
                        "mode": "motion",
                        "min_segment_ms": 1,
                        "max_segment_ms": 1000,
                    }
                )
    return actions


def gesture_to_click(
    x1: int, y1: int, x2: int, y2: int, gesture: Gesture, profile: Optional[Dict[str, float]] = None
) -> Optional[Dict]:
    resolved_profile = normalize_profile(profile)
    tap_distance_px = resolved_profile["tap_distance_px"]
    tap_duration_sec = resolved_profile["tap_duration_sec"]
    min_swipe_duration_sec = resolved_profile["min_swipe_duration_sec"]
    dist = math.hypot(x2 - x1, y2 - y1)
    duration = max(0.0, gesture.end_t - gesture.start_t)
    point_count = len(gesture.points)
    # BlueStacks virtual touch can emit sparse ABS samples without BTN_TOUCH/TRACKING_ID.
    # In that mode, a multi-point window is more likely a drag than a tap.
    if not gesture.explicit_touch and point_count >= 2 and (duration >= 0.20 or dist >= tap_distance_px * 0.5):
        return None
    if (
        point_count <= 12
        and dist <= tap_distance_px
        and duration <= tap_duration_sec
        and not (point_count >= 2 and duration >= min_swipe_duration_sec)
    ):
        return {"type": "click", "x": x2, "y": y2}
    return None


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


def merge_short_gap_traces(actions: List[Dict], profile: Optional[Dict[str, float]] = None) -> List[Dict]:
    # Merge trace-wait-trace caused by transient touch split on some devices/emulators.
    resolved_profile = normalize_profile(profile)
    def is_trace(act: Dict) -> bool:
        return act.get("type") == "trace" and isinstance(act.get("points"), list) and len(act.get("points")) >= 2

    def is_wait(act: Dict) -> bool:
        return act.get("type") == "wait"

    merged: List[Dict] = []
    i = 0
    merge_count = 0
    def trace_span(points: List[Dict]) -> float:
        if len(points) < 2:
            return 0.0
        p0 = points[0]
        p1 = points[-1]
        return math.hypot(float(p1["x"]) - float(p0["x"]), float(p1["y"]) - float(p0["y"]))

    while i < len(actions):
        if i + 2 < len(actions) and is_trace(actions[i]) and is_wait(actions[i + 1]) and is_trace(actions[i + 2]):
            left = actions[i]
            wait = float(actions[i + 1].get("seconds", 0.0))
            right = actions[i + 2]
            left_pts = left["points"]
            right_pts = right["points"]
            lx, ly = int(left_pts[-1]["x"]), int(left_pts[-1]["y"])
            rx, ry = int(right_pts[0]["x"]), int(right_pts[0]["y"])
            dist = math.hypot(lx - rx, ly - ry)
            right_span = trace_span(right_pts)
            right_duration = int(right_pts[-1].get("t_ms", 0)) - int(right_pts[0].get("t_ms", 0))

            # Standard merge: tiny gap and near-continuous endpoint.
            standard_merge = wait <= resolved_profile["merge_gap_sec"] and dist <= resolved_profile["merge_distance_px"]
            # Joystick-friendly merge: short continuation trace after longer event drop.
            # Typical split pattern: endpoint close, then a tiny continuation trace.
            continuation_merge = (
                wait <= resolved_profile["continuation_gap_sec"]
                and dist <= resolved_profile["continuation_distance_px"]
                and right_span <= resolved_profile["continuation_span_px"]
                and right_duration <= resolved_profile["continuation_duration_ms"]
            )

            if standard_merge or continuation_merge:
                base_t = int(left_pts[-1].get("t_ms", 0))
                offset_t = base_t + int(round(wait * 1000))
                new_points = [dict(p) for p in left_pts]
                for j, p in enumerate(right_pts):
                    if j == 0 and int(p.get("x", 0)) == lx and int(p.get("y", 0)) == ly:
                        continue
                    np = dict(p)
                    np["t_ms"] = int(p.get("t_ms", 0)) + offset_t
                    new_points.append(np)

                merged_trace = dict(left)
                merged_trace["points"] = new_points
                merged.append(merged_trace)
                i += 3
                merge_count += 1
                continue

        merged.append(actions[i])
        i += 1

    print(f"[Recorder] Short-gap trace merges: {merge_count}")
    return merged


def build_raw_excerpt(lines: List[str], limit: int = 40) -> List[str]:
    if len(lines) <= limit:
        return lines
    head = max(1, limit // 2)
    tail = max(1, limit - head - 1)
    return lines[:head] + ["..."] + lines[-tail:]


def record_gestures(
    adb: str,
    device: Optional[str],
    touch_caps: Dict[str, Tuple[int, int]],
    min_points: int,
    forced_event_dev: Optional[str],
    stop_after_gestures: Optional[int] = None,
    on_gesture_captured: Optional[Callable[[Gesture, str], None]] = None,
    on_gesture_raw_captured: Optional[Callable[[Gesture, str, List[str]], None]] = None,
) -> Tuple[List[Gesture], str]:
    cmd = adb_cmd(adb, device, ["shell", "getevent", "-lt"])
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)
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
    using_sparse_abs_mode = False
    last_point_t: Optional[float] = None
    idle_split_sec = 0.25
    seen_event_lines = 0
    unmatched_samples: List[str] = []
    cur_raw_lines: List[str] = []
    last_gesture_signature: Optional[Tuple[float, float, int]] = None

    if selected_dev:
        print(f"[Recorder] Listening on forced device {selected_dev}. Press Ctrl+C to stop and save.")
    else:
        print("[Recorder] Listening on all input events. Touch BlueStacks to auto-detect touch device.")
        print("[Recorder] Press Ctrl+C to stop and save.")

    def append_point_if_possible(ts: float) -> None:
        nonlocal cur_points, x_raw, y_raw
        if x_raw is None or y_raw is None:
            return
        if cur_points and cur_points[-1].x_raw == x_raw and cur_points[-1].y_raw == y_raw:
            return
        cur_points.append(Point(t=ts, x_raw=x_raw, y_raw=y_raw))

    def mark_latest_gesture() -> None:
        nonlocal last_gesture_signature
        if not gestures:
            return
        latest = gestures[-1]
        last_gesture_signature = (round(latest.start_t, 6), round(latest.end_t, 6), len(latest.points))

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

            if len(cur_raw_lines) < 200:
                cur_raw_lines.append(line_s)

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
                        append_point_if_possible(t)
                        if touching and cur_points:
                            gestures.append(
                                Gesture(
                                    start_t=cur_start_t or t,
                                    end_t=t,
                                    points=cur_points[:],
                                    explicit_touch=current_explicit_touch,
                                )
                            )
                            if on_gesture_captured is not None and selected_dev is not None:
                                on_gesture_captured(gestures[-1], selected_dev)
                            if on_gesture_raw_captured is not None and selected_dev is not None:
                                on_gesture_raw_captured(gestures[-1], selected_dev, cur_raw_lines[:])
                            mark_latest_gesture()
                            print(f"[Recorder] Gesture captured: {len(cur_points)} points")
                            if stop_after_gestures and len(gestures) >= stop_after_gestures:
                                touching = False
                                cur_points = []
                                cur_raw_lines = []
                                cur_start_t = None
                                current_explicit_touch = False
                                break
                        touching = False
                        cur_points = []
                        cur_raw_lines = []
                        cur_start_t = None
                        current_explicit_touch = False
                    else:
                        touching = True
                        cur_start_t = t
                        cur_points = []
                        cur_raw_lines = [line_s]
                        current_explicit_touch = True
                        # Some devices emit coordinates before TRACKING_ID down.
                        append_point_if_possible(t)
            # EV_KEY BTN_TOUCH
            elif etype == "0001" and ecode == "014a":
                saw_touch_flag = True
                if evalue == 1:
                    touching = True
                    cur_start_t = t
                    cur_points = []
                    current_explicit_touch = True
                    append_point_if_possible(t)
                elif evalue == 0:
                    append_point_if_possible(t)
                    if touching and cur_points:
                        gestures.append(
                            Gesture(
                                start_t=cur_start_t or t,
                                end_t=t,
                                points=cur_points[:],
                                explicit_touch=current_explicit_touch,
                            )
                        )
                        if on_gesture_captured is not None and selected_dev is not None:
                            on_gesture_captured(gestures[-1], selected_dev)
                        if on_gesture_raw_captured is not None and selected_dev is not None:
                            on_gesture_raw_captured(gestures[-1], selected_dev, cur_raw_lines[:])
                        mark_latest_gesture()
                        print(f"[Recorder] Gesture captured: {len(cur_points)} points")
                        if stop_after_gestures and len(gestures) >= stop_after_gestures:
                            touching = False
                            cur_points = []
                            cur_raw_lines = []
                            cur_start_t = None
                            current_explicit_touch = False
                            break
                    touching = False
                    cur_points = []
                    cur_raw_lines = []
                    cur_start_t = None
                    current_explicit_touch = False
            # EV_SYN SYN_REPORT
            elif etype == "0000" and ecode == "0000":
                # Fallback split: some devices don't emit BTN_TOUCH/TRACKING_ID.
                effective_idle_split = SPARSE_ABS_IDLE_SPLIT_SEC if using_sparse_abs_mode else idle_split_sec
                if not saw_touch_flag and last_point_t is not None and touching and (t - last_point_t) > effective_idle_split:
                    if cur_points:
                        gestures.append(
                            Gesture(
                                start_t=cur_start_t or last_point_t,
                                end_t=last_point_t,
                                points=cur_points[:],
                                explicit_touch=False,
                            )
                        )
                        if on_gesture_captured is not None and selected_dev is not None:
                            on_gesture_captured(gestures[-1], selected_dev)
                        if on_gesture_raw_captured is not None and selected_dev is not None:
                            on_gesture_raw_captured(gestures[-1], selected_dev, cur_raw_lines[:])
                        mark_latest_gesture()
                        print(f"[Recorder] Gesture captured (idle split): {len(cur_points)} points")
                        if stop_after_gestures and len(gestures) >= stop_after_gestures:
                            touching = False
                            cur_points = []
                            cur_raw_lines = []
                            cur_start_t = None
                            current_explicit_touch = False
                            break
                    touching = False
                    cur_points = []
                    cur_raw_lines = []
                    cur_start_t = None
                    current_explicit_touch = False
                if touching and x_raw is not None and y_raw is not None:
                    cur_points.append(Point(t=t, x_raw=x_raw, y_raw=y_raw))
                    last_t = t
                    last_point_t = t
                elif not saw_touch_flag and x_raw is not None and y_raw is not None:
                    # Start pseudo-touch if only ABS coordinates are reported.
                    if not using_sparse_abs_mode:
                        using_sparse_abs_mode = True
                        print(f"[Recorder] Sparse ABS mode enabled for {selected_dev}")
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
        trailing = Gesture(
            start_t=cur_start_t or last_t,
            end_t=last_t,
            points=cur_points[:],
            explicit_touch=current_explicit_touch,
        )
        trailing_signature = (round(trailing.start_t, 6), round(trailing.end_t, 6), len(trailing.points))
        if trailing_signature != last_gesture_signature:
            gestures.append(trailing)
            if on_gesture_captured is not None and selected_dev is not None:
                on_gesture_captured(trailing, selected_dev)
            if on_gesture_raw_captured is not None and selected_dev is not None:
                on_gesture_raw_captured(trailing, selected_dev, cur_raw_lines[:])

    filtered = [g for g in gestures if len(g.points) >= min_points]
    print(f"[Recorder] Kept gestures: {len(filtered)} / raw {len(gestures)}")
    if seen_event_lines == 0 and unmatched_samples:
        print("[Recorder] Debug sample (unparsed getevent lines):")
        for sample in unmatched_samples:
            print(f"  {sample}")
    if selected_dev is None:
        raise RecorderError("No active touch device detected during recording.")
    return filtered, selected_dev


def listen_click_coordinates(
    adb: str,
    device: Optional[str],
    touch_caps: Dict[str, Tuple[int, int]],
    screen_w: int,
    screen_h: int,
    forced_event_dev: Optional[str],
    invert_x: bool,
    invert_y: bool,
    swap_xy: bool,
    profile: Optional[Dict[str, float]] = None,
) -> str:
    selected_dev_holder: List[str] = []

    def report_click(gesture: Gesture, selected_dev: str) -> None:
        if not selected_dev_holder:
            selected_dev_holder.append(selected_dev)
        x_max, y_max = touch_caps[selected_dev]
        first = gesture.points[0]
        last = gesture.points[-1]
        x1, y1 = map_raw_point_to_screen(
            first.x_raw, first.y_raw, x_max, y_max, screen_w, screen_h, invert_x, invert_y, swap_xy
        )
        x2, y2 = map_raw_point_to_screen(
            last.x_raw, last.y_raw, x_max, y_max, screen_w, screen_h, invert_x, invert_y, swap_xy
        )
        click = gesture_to_click(x1, y1, x2, y2, gesture, profile)
        if click is not None:
            print(f"[Click] x={click['x']} y={click['y']}", flush=True)

    _, selected_dev = record_gestures(
        adb,
        device,
        touch_caps,
        min_points=1,
        forced_event_dev=forced_event_dev,
        stop_after_gestures=None,
        on_gesture_captured=report_click,
    )
    return selected_dev


def build_diagnostic_cases(screen_w: int, screen_h: int) -> List[Dict[str, object]]:
    center_y = int(round(screen_h * 0.72))
    joy_x = int(round(screen_w * 0.50))
    joy_y = int(round(screen_h * 0.80))
    return [
        {
            "name": "tap_center",
            "kind": "tap",
            "x": int(round(screen_w * 0.50)),
            "y": int(round(screen_h * 0.50)),
            "cooldown_sec": 0.9,
        },
        {
            "name": "swipe_right",
            "kind": "swipe",
            "x1": int(round(screen_w * 0.32)),
            "y1": center_y,
            "x2": int(round(screen_w * 0.68)),
            "y2": center_y,
            "duration_ms": 450,
            "cooldown_sec": 1.1,
        },
        {
            "name": "swipe_up",
            "kind": "swipe",
            "x1": int(round(screen_w * 0.50)),
            "y1": int(round(screen_h * 0.76)),
            "x2": int(round(screen_w * 0.50)),
            "y2": int(round(screen_h * 0.34)),
            "duration_ms": 520,
            "cooldown_sec": 1.1,
        },
        {
            "name": "angle_120",
            "kind": "swipe",
            "x1": joy_x,
            "y1": joy_y,
            "x2": joy_x - 180,
            "y2": joy_y - 312,
            "duration_ms": 600,
            "cooldown_sec": 1.1,
        },
        {
            "name": "angle_-120",
            "kind": "swipe",
            "x1": joy_x,
            "y1": joy_y,
            "x2": joy_x - 180,
            "y2": joy_y + 312,
            "duration_ms": 600,
            "cooldown_sec": 1.1,
        },
        {
            "name": "joystick_hold_2s",
            "kind": "swipe",
            "x1": joy_x,
            "y1": joy_y,
            "x2": joy_x,
            "y2": joy_y - 220,
            "duration_ms": 2000,
            "cooldown_sec": 1.2,
        },
    ]


def diagnostic_event_device(forced_event_dev: Optional[str], touch_caps: Dict[str, Tuple[int, int]]) -> str:
    if forced_event_dev:
        return forced_event_dev
    if len(touch_caps) == 1:
        return next(iter(touch_caps.keys()))
    return sorted(touch_caps.keys())[0]


def send_shell(adb: str, device: Optional[str], shell_args: List[str]) -> subprocess.CompletedProcess[str]:
    return run_cmd(adb_cmd(adb, device, ["shell"] + shell_args), check=False)


def sendevent(adb: str, device: Optional[str], event_dev: str, etype: int, ecode: int, value: int) -> None:
    send_shell(adb, device, ["sendevent", event_dev, str(etype), str(ecode), str(value)])


def inject_low_level_tap(
    adb: str,
    device: Optional[str],
    event_dev: str,
    raw_x: int,
    raw_y: int,
    hold_sec: float = 0.05,
    tracking_id: int = 1001,
) -> None:
    sendevent(adb, device, event_dev, 3, 47, 0)  # ABS_MT_SLOT
    sendevent(adb, device, event_dev, 3, 57, tracking_id)
    sendevent(adb, device, event_dev, 3, 0, raw_x)  # ABS_X
    sendevent(adb, device, event_dev, 3, 1, raw_y)  # ABS_Y
    sendevent(adb, device, event_dev, 3, 53, raw_x)
    sendevent(adb, device, event_dev, 3, 54, raw_y)
    sendevent(adb, device, event_dev, 1, 325, 1)  # BTN_TOOL_FINGER
    sendevent(adb, device, event_dev, 1, 330, 1)
    sendevent(adb, device, event_dev, 0, 0, 0)
    time.sleep(max(0.02, hold_sec))
    sendevent(adb, device, event_dev, 3, 57, -1)
    sendevent(adb, device, event_dev, 1, 325, 0)
    sendevent(adb, device, event_dev, 1, 330, 0)
    sendevent(adb, device, event_dev, 0, 0, 0)


def inject_low_level_swipe(
    adb: str,
    device: Optional[str],
    event_dev: str,
    start_raw: Tuple[int, int],
    end_raw: Tuple[int, int],
    duration_ms: int,
    steps: int = 12,
    tracking_id: int = 1001,
) -> None:
    x1, y1 = start_raw
    x2, y2 = end_raw
    total_steps = max(2, steps)
    sendevent(adb, device, event_dev, 3, 47, 0)  # ABS_MT_SLOT
    sendevent(adb, device, event_dev, 3, 57, tracking_id)
    sendevent(adb, device, event_dev, 3, 0, x1)  # ABS_X
    sendevent(adb, device, event_dev, 3, 1, y1)  # ABS_Y
    sendevent(adb, device, event_dev, 3, 53, x1)
    sendevent(adb, device, event_dev, 3, 54, y1)
    sendevent(adb, device, event_dev, 1, 325, 1)  # BTN_TOOL_FINGER
    sendevent(adb, device, event_dev, 1, 330, 1)
    sendevent(adb, device, event_dev, 0, 0, 0)
    step_sleep = max(0.005, duration_ms / 1000.0 / total_steps)
    for idx in range(1, total_steps + 1):
        ratio = idx / float(total_steps)
        x = int(round(x1 + (x2 - x1) * ratio))
        y = int(round(y1 + (y2 - y1) * ratio))
        sendevent(adb, device, event_dev, 3, 0, x)
        sendevent(adb, device, event_dev, 3, 1, y)
        sendevent(adb, device, event_dev, 3, 53, x)
        sendevent(adb, device, event_dev, 3, 54, y)
        sendevent(adb, device, event_dev, 0, 0, 0)
        time.sleep(step_sleep)
    sendevent(adb, device, event_dev, 3, 57, -1)
    sendevent(adb, device, event_dev, 1, 325, 0)
    sendevent(adb, device, event_dev, 1, 330, 0)
    sendevent(adb, device, event_dev, 0, 0, 0)


def dispatch_diagnostic_inputs(
    adb: str,
    device: Optional[str],
    event_dev: str,
    touch_caps: Dict[str, Tuple[int, int]],
    screen_w: int,
    screen_h: int,
    invert_x: bool,
    invert_y: bool,
    swap_xy: bool,
    cases: List[Dict[str, object]],
    start_delay_sec: float = 1.0,
) -> None:
    time.sleep(start_delay_sec)
    x_max, y_max = touch_caps[event_dev]
    for case in cases:
        kind = str(case.get("kind", ""))
        if kind == "tap":
            raw_x, raw_y = map_screen_point_to_raw(
                int(case["x"]), int(case["y"]), x_max, y_max, screen_w, screen_h, invert_x, invert_y, swap_xy
            )
            inject_low_level_tap(adb, device, event_dev, raw_x, raw_y)
        else:
            start_raw = map_screen_point_to_raw(
                int(case["x1"]), int(case["y1"]), x_max, y_max, screen_w, screen_h, invert_x, invert_y, swap_xy
            )
            end_raw = map_screen_point_to_raw(
                int(case["x2"]), int(case["y2"]), x_max, y_max, screen_w, screen_h, invert_x, invert_y, swap_xy
            )
            inject_low_level_swipe(
                adb, device, event_dev, start_raw, end_raw, int(case.get("duration_ms", 400))
            )
        time.sleep(float(case.get("cooldown_sec", 0.9)))


def action_end_point(action: Dict) -> Optional[Tuple[int, int]]:
    if action.get("type") == "click":
        return int(action.get("x", 0)), int(action.get("y", 0))
    if action.get("type") == "trace":
        points = action.get("points")
        if isinstance(points, list) and points:
            last = points[-1]
            return int(last.get("x", 0)), int(last.get("y", 0))
    return None


def evaluate_diagnostic_actions(
    actions: List[Dict], cases: List[Dict[str, object]], raw_snippets: Optional[List[Dict[str, object]]] = None
) -> Dict[str, object]:
    comparable = [act for act in actions if act.get("type") in {"click", "trace"}]
    score = 0.0
    swipe_as_click = 0
    tap_as_trace = 0
    details: List[Dict[str, object]] = []
    snippet_list = raw_snippets or []

    for index, case in enumerate(cases):
        actual = comparable[index] if index < len(comparable) else None
        kind = str(case.get("kind"))
        expected_type = "click" if kind == "tap" else "trace"
        item: Dict[str, object] = {"case": case.get("name", f"case_{index}"), "expected": expected_type}
        if index < len(snippet_list):
            item["raw_excerpt"] = snippet_list[index]

        if actual is None:
            item["actual"] = "missing"
            item["penalty"] = 120.0
            score += 120.0
            details.append(item)
            continue

        actual_type = str(actual.get("type"))
        item["actual"] = actual_type
        penalty = 0.0
        if actual_type != expected_type:
            penalty += 80.0
            if kind == "swipe" and actual_type == "click":
                swipe_as_click += 1
            if kind == "tap" and actual_type == "trace":
                tap_as_trace += 1

        end_point = action_end_point(actual)
        if kind == "tap":
            target = (int(case["x"]), int(case["y"]))
        else:
            target = (int(case["x2"]), int(case["y2"]))
        if end_point is not None:
            end_error = math.hypot(end_point[0] - target[0], end_point[1] - target[1])
            penalty += min(60.0, end_error / 2.0)
            item["end_error_px"] = round(end_error, 2)
        item["penalty"] = round(penalty, 2)
        score += penalty
        details.append(item)

    extra_actions = max(0, len(comparable) - len(cases))
    if extra_actions:
        score += extra_actions * 45.0

    return {
        "score": round(score, 2),
        "expected_case_count": len(cases),
        "recorded_action_count": len(comparable),
        "extra_actions": extra_actions,
        "swipe_as_click": swipe_as_click,
        "tap_as_trace": tap_as_trace,
        "details": details,
    }


def propose_profile_adjustment(profile: Dict[str, float], evaluation: Dict[str, object]) -> Tuple[Dict[str, float], List[str]]:
    updated = dict(profile)
    reasons: List[str] = []

    swipe_as_click = int(evaluation.get("swipe_as_click", 0))
    tap_as_trace = int(evaluation.get("tap_as_trace", 0))
    extra_actions = int(evaluation.get("extra_actions", 0))

    if swipe_as_click > 0:
        updated["tap_distance_px"] = max(8.0, updated["tap_distance_px"] - 4.0)
        updated["tap_duration_sec"] = max(0.20, updated["tap_duration_sec"] - 0.05)
        reasons.append("拖动被识别成点击，收紧点击阈值")
    if tap_as_trace > 0:
        updated["tap_distance_px"] = min(36.0, updated["tap_distance_px"] + 4.0)
        updated["tap_duration_sec"] = min(0.70, updated["tap_duration_sec"] + 0.05)
        reasons.append("点击被识别成拖动，放宽点击阈值")
    if extra_actions > 0:
        updated["merge_gap_sec"] = min(0.90, updated["merge_gap_sec"] + 0.15)
        updated["merge_distance_px"] = min(24.0, updated["merge_distance_px"] + 4.0)
        updated["continuation_gap_sec"] = min(1.80, updated["continuation_gap_sec"] + 0.25)
        updated["continuation_distance_px"] = min(28.0, updated["continuation_distance_px"] + 4.0)
        reasons.append("单次拖动被拆分，放宽 trace 合并阈值")

    return normalize_profile(updated), reasons


def run_diagnostic_capture(
    adb: str,
    effective_device: str,
    touch_caps: Dict[str, Tuple[int, int]],
    screen_w: int,
    screen_h: int,
    forced_event_dev: Optional[str],
    invert_x: bool,
    invert_y: bool,
    swap_xy: bool,
    profile: Dict[str, float],
    cases: List[Dict[str, object]],
) -> Dict[str, object]:
    event_dev = diagnostic_event_device(forced_event_dev, touch_caps)
    print(f"[Diag] Injection event device: {event_dev}")
    raw_snippets: List[Dict[str, object]] = []

    def capture_raw_snippet(gesture: Gesture, selected_dev: str, raw_lines: List[str]) -> None:
        raw_snippets.append(
            {
                "device": selected_dev,
                "start_t": round(gesture.start_t, 6),
                "end_t": round(gesture.end_t, 6),
                "lines": build_raw_excerpt(raw_lines),
            }
        )

    sender = threading.Thread(
        target=dispatch_diagnostic_inputs,
        args=(adb, effective_device, event_dev, touch_caps, screen_w, screen_h, invert_x, invert_y, swap_xy, cases),
        daemon=True,
    )
    sender.start()
    gestures, selected_dev = record_gestures(
        adb,
        effective_device,
        touch_caps,
        min_points=1,
        forced_event_dev=event_dev,
        stop_after_gestures=len(cases),
        on_gesture_raw_captured=capture_raw_snippet,
    )
    sender.join(timeout=5.0)
    x_max, y_max = touch_caps[selected_dev]
    actions = build_actions_from_gestures(
        gestures,
        x_max,
        y_max,
        screen_w,
        screen_h,
        invert_x=invert_x,
        invert_y=invert_y,
        swap_xy=swap_xy,
        profile=profile,
    )
    actions = clean_actions_noise(actions)
    actions = merge_short_gap_traces(actions, profile=profile)
    evaluation = evaluate_diagnostic_actions(actions, cases, raw_snippets=raw_snippets)
    return {
        "selected_dev": selected_dev,
        "injection_event_dev": event_dev,
        "profile": normalize_profile(profile),
        "actions": actions,
        "raw_gesture_snippets": raw_snippets,
        "evaluation": evaluation,
    }


def run_self_heal_diagnostic(
    adb: str,
    effective_device: str,
    touch_caps: Dict[str, Tuple[int, int]],
    screen_w: int,
    screen_h: int,
    forced_event_dev: Optional[str],
    invert_x: bool,
    invert_y: bool,
    swap_xy: bool,
    profile_path: Optional[str],
    output_path: str,
) -> int:
    base_profile = load_recording_profile(profile_path)
    cases = build_diagnostic_cases(screen_w, screen_h)
    print(f"[Diag] Cases: {len(cases)}")
    print(f"[Diag] Baseline profile: {json.dumps(base_profile, ensure_ascii=False)}")
    baseline = run_diagnostic_capture(
        adb, effective_device, touch_caps, screen_w, screen_h, forced_event_dev, invert_x, invert_y, swap_xy, base_profile, cases
    )
    print(f"[Diag] Baseline score: {baseline['evaluation']['score']}")

    candidate_profile, reasons = propose_profile_adjustment(base_profile, baseline["evaluation"])
    result: Dict[str, object] = {
        "mode": "diagnose_self_heal",
        "cases": cases,
        "baseline": baseline,
        "candidate": None,
        "applied": False,
        "applied_profile": base_profile,
        "reasons": reasons,
    }

    if reasons and candidate_profile != base_profile:
        print(f"[Diag] Proposed repair: {'; '.join(reasons)}")
        print(f"[Diag] Candidate profile: {json.dumps(candidate_profile, ensure_ascii=False)}")
        candidate = run_diagnostic_capture(
            adb,
            effective_device,
            touch_caps,
            screen_w,
            screen_h,
            forced_event_dev,
            invert_x,
            invert_y,
            swap_xy,
            candidate_profile,
            cases,
        )
        result["candidate"] = candidate
        print(f"[Diag] Candidate score: {candidate['evaluation']['score']}")
        if float(candidate["evaluation"]["score"]) < float(baseline["evaluation"]["score"]):
            result["applied"] = True
            result["applied_profile"] = candidate_profile
            if profile_path:
                save_recording_profile(profile_path, candidate_profile)
                print(f"[Diag] Applied profile saved: {profile_path}")
        else:
            print("[Diag] Candidate profile did not improve score; keep baseline profile")
    else:
        print("[Diag] No repair action proposed; keep baseline profile")

    Path(output_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[Diag] Report saved: {output_path}")
    return 0


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
    parser.add_argument("--print-clicks-only", action="store_true", help="Listen to touches and print click coordinates only")
    parser.add_argument("--profile", help="Recording profile JSON path")
    parser.add_argument("--diagnose-self-heal", action="store_true", help="Run diagnostic capture, propose repair, and save better profile")
    args = parser.parse_args()
    profile = load_recording_profile(args.profile)

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

    if args.print_clicks_only:
        selected_dev = listen_click_coordinates(
            args.adb,
            effective_device,
            touch_caps,
            screen_w,
            screen_h,
            args.event_dev,
            bool(args.invert_x),
            bool(args.invert_y),
            bool(args.swap_xy),
            profile=profile,
        )
        x_max, y_max = touch_caps[selected_dev]
        print(f"[Recorder] Using touch device: {selected_dev}, raw max: x={x_max}, y={y_max}")
        return 0

    if args.diagnose_self_heal:
        return run_self_heal_diagnostic(
            args.adb,
            effective_device,
            touch_caps,
            screen_w,
            screen_h,
            args.event_dev,
            bool(args.invert_x),
            bool(args.invert_y),
            bool(args.swap_xy),
            args.profile,
            args.output,
        )

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
        profile=profile,
    )
    if not args.no_clean_noise:
        actions = clean_actions_noise(actions)
    actions = merge_short_gap_traces(actions, profile=profile)
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
        "screen_size": {
            "width": int(screen_w),
            "height": int(screen_h),
        },
        "mapping_profile": {
            "invert_x": bool(args.invert_x),
            "invert_y": bool(args.invert_y),
            "swap_xy": bool(args.swap_xy),
        },
        "recording_profile": profile,
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
