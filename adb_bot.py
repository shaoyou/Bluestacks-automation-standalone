#!/usr/bin/env python3
import argparse
import json
import random
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

WM_SIZE_RE = re.compile(r"(\d+)x(\d+)")


class BotError(RuntimeError):
    pass


@dataclass
class RunContext:
    adb_path: str
    device: Optional[str]
    dry_run: bool
    jitter: int
    stop_at: Optional[float]
    src_screen_w: int
    src_screen_h: int
    dst_screen_w: int
    dst_screen_h: int
    trace_time_scale: float
    motionevent_supported: Optional[bool] = None


def log(message: str) -> None:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}")


def run_cmd(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def build_adb_cmd(ctx: RunContext, extra: List[str]) -> List[str]:
    cmd = [ctx.adb_path]
    if ctx.device:
        cmd += ["-s", ctx.device]
    cmd.extend(extra)
    return cmd


def adb_shell(ctx: RunContext, shell_args: List[str]) -> None:
    pretty = " ".join(shlex.quote(p) for p in shell_args)
    if ctx.dry_run:
        log(f"[DRY-RUN] adb shell {pretty}")
        return
    log(f"CMD adb shell {pretty}")
    cmd = build_adb_cmd(ctx, ["shell"] + shell_args)
    try:
        run_cmd(cmd)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        text = f"{stdout}\n{stderr}".strip().lower()
        transient = any(
            token in text
            for token in [
                "device offline",
                "device not found",
                "more than one device",
                "closed",
                "cannot connect",
            ]
        )
        if transient and ctx.device:
            log("ADB shell failed, try reconnect once ...")
            run_cmd([ctx.adb_path, "connect", ctx.device], check=False)
            try:
                run_cmd(cmd)
                return
            except subprocess.CalledProcessError as retry_exc:
                retry_stderr = (retry_exc.stderr or "").strip()
                retry_stdout = (retry_exc.stdout or "").strip()
                retry_text = f"{retry_stdout}\n{retry_stderr}".strip().lower()
                retry_transient = any(
                    token in retry_text
                    for token in ["device offline", "device not found", "closed", "cannot connect"]
                )
                if retry_transient:
                    candidates = [d for d in _list_connected_devices(ctx.adb_path) if d != ctx.device]
                    for candidate in candidates:
                        log(f"Try fallback device: {candidate}")
                        fallback_cmd = [ctx.adb_path, "-s", candidate, "shell"] + shell_args
                        fallback_result = run_cmd(fallback_cmd, check=False)
                        if fallback_result.returncode == 0:
                            log(f"Switched active device to: {candidate}")
                            ctx.device = candidate
                            return
                    retry_details = retry_stderr or retry_stdout or str(retry_exc)
                    raise BotError(
                        f"ADB shell failed after reconnect and fallback: adb shell {pretty}\n{retry_details}"
                    ) from retry_exc
                retry_details = retry_stderr or retry_stdout or str(retry_exc)
                raise BotError(f"ADB shell failed after reconnect: adb shell {pretty}\n{retry_details}") from retry_exc
        details = stderr or stdout or str(exc)
        raise BotError(f"ADB shell failed: adb shell {pretty}\n{details}") from exc


def adb_shell_result(ctx: RunContext, shell_args: List[str]) -> subprocess.CompletedProcess[str]:
    if ctx.dry_run:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    cmd = build_adb_cmd(ctx, ["shell"] + shell_args)
    return run_cmd(cmd, check=False)


def map_input_point(ctx: RunContext, x: int, y: int) -> Tuple[int, int]:
    if ctx.src_screen_w <= 0 or ctx.src_screen_h <= 0:
        return x, y
    if ctx.dst_screen_w <= 0 or ctx.dst_screen_h <= 0:
        return x, y
    sx = ctx.dst_screen_w / float(ctx.src_screen_w)
    sy = ctx.dst_screen_h / float(ctx.src_screen_h)
    mapped_x = int(round(x * sx))
    mapped_y = int(round(y * sy))
    mapped_x = max(0, min(ctx.dst_screen_w - 1, mapped_x))
    mapped_y = max(0, min(ctx.dst_screen_h - 1, mapped_y))
    return mapped_x, mapped_y


def supports_motionevent(ctx: RunContext) -> bool:
    if ctx.motionevent_supported is not None:
        return ctx.motionevent_supported
    if ctx.dry_run:
        ctx.motionevent_supported = True
        return True
    probe = adb_shell_result(ctx, ["input", "motionevent", "DOWN", "1", "1"])
    text = f"{probe.stdout}\n{probe.stderr}".lower()
    if probe.returncode == 0:
        adb_shell_result(ctx, ["input", "motionevent", "UP", "1", "1"])
        ctx.motionevent_supported = True
        return True
    unsupported_tokens = [
        "unknown command",
        "invalid arguments",
        "usage:",
        "can't find service",
        "not found",
    ]
    if any(token in text for token in unsupported_tokens):
        ctx.motionevent_supported = False
        log("Device does not support 'input motionevent', fallback to segmented swipe trace.")
        return False
    ctx.motionevent_supported = False
    return False


def apply_jitter(value: int, jitter: int) -> int:
    if jitter <= 0:
        return value
    return value + random.randint(-jitter, jitter)


def check_stop(ctx: RunContext) -> None:
    if ctx.stop_at is None:
        return
    if time.time() >= ctx.stop_at:
        raise KeyboardInterrupt("Reached max runtime")


def do_click(ctx: RunContext, action: Dict[str, Any]) -> None:
    x = int(action["x"])
    y = int(action["y"])
    x, y = map_input_point(ctx, x, y)
    x = apply_jitter(x, ctx.jitter)
    y = apply_jitter(y, ctx.jitter)
    adb_shell(ctx, ["input", "tap", str(x), str(y)])
    log(f"Click ({x}, {y})")


def do_swipe(ctx: RunContext, action: Dict[str, Any]) -> None:
    x1, y1 = map_input_point(ctx, int(action["x1"]), int(action["y1"]))
    x2, y2 = map_input_point(ctx, int(action["x2"]), int(action["y2"]))
    x1 = apply_jitter(x1, ctx.jitter)
    y1 = apply_jitter(y1, ctx.jitter)
    x2 = apply_jitter(x2, ctx.jitter)
    y2 = apply_jitter(y2, ctx.jitter)
    duration = int(action.get("duration_ms", 300))
    adb_shell(ctx, ["input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)])
    log(f"Swipe ({x1}, {y1}) -> ({x2}, {y2}), {duration}ms")


def do_trace(ctx: RunContext, action: Dict[str, Any]) -> None:
    points = action.get("points", [])
    if not isinstance(points, list) or len(points) < 2:
        return

    min_segment_ms = int(action.get("min_segment_ms", 1))
    max_segment_ms = int(action.get("max_segment_ms", 1000))
    trace_mode = str(action.get("mode", "auto")).lower()
    trace_time_scale = float(action.get("time_scale", ctx.trace_time_scale))
    trace_jitter_px = int(action.get("trace_jitter_px", 0))
    offset_x = 0 if trace_jitter_px <= 0 else random.randint(-trace_jitter_px, trace_jitter_px)
    offset_y = 0 if trace_jitter_px <= 0 else random.randint(-trace_jitter_px, trace_jitter_px)

    def map_trace_point(raw_x: int, raw_y: int) -> Tuple[int, int]:
        x, y = map_input_point(ctx, raw_x, raw_y)
        x += offset_x
        y += offset_y
        if ctx.dst_screen_w > 0 and ctx.dst_screen_h > 0:
            x = max(0, min(ctx.dst_screen_w - 1, x))
            y = max(0, min(ctx.dst_screen_h - 1, y))
        return x, y

    if trace_mode == "motion":
        use_motion = supports_motionevent(ctx)
    elif trace_mode == "swipe":
        use_motion = False
    else:
        use_motion = supports_motionevent(ctx)
    if use_motion:
        first = points[0]
        replay_start = time.perf_counter()
        x0, y0 = map_trace_point(int(first["x"]), int(first["y"]))
        adb_shell(ctx, ["input", "motionevent", "DOWN", str(x0), str(y0)])
        first_t = int(first.get("t_ms", 0))
        prev_t = first_t
        for i in range(1, len(points)):
            p = points[i]
            t_now = int(p.get("t_ms", prev_t))
            delta_raw = t_now - prev_t
            _delta = min_segment_ms if delta_raw <= 0 else max(min_segment_ms, min(max_segment_ms, delta_raw))
            # Use absolute scheduling to compensate adb command overhead.
            target_elapsed = max(0.0, (t_now - first_t) / 1000.0) * trace_time_scale
            remain = target_elapsed - (time.perf_counter() - replay_start)
            if remain > 0:
                time.sleep(remain)
            x, y = map_trace_point(int(p["x"]), int(p["y"]))
            adb_shell(ctx, ["input", "motionevent", "MOVE", str(x), str(y)])
            prev_t = t_now
        last_t = int(points[-1].get("t_ms", prev_t))
        final_elapsed = max(0.0, (last_t - first_t) / 1000.0) * trace_time_scale
        final_remain = final_elapsed - (time.perf_counter() - replay_start)
        if final_remain > 0:
            time.sleep(final_remain)
        last = points[-1]
        xl, yl = map_trace_point(int(last["x"]), int(last["y"]))
        adb_shell(ctx, ["input", "motionevent", "UP", str(xl), str(yl)])
        log(f"Trace replayed continuously with {len(points)} points")
        return

    # Fallback for devices without motionevent support, or forced swipe mode.
    for i in range(len(points) - 1):
        p1 = points[i]
        p2 = points[i + 1]
        x1, y1 = map_trace_point(int(p1["x"]), int(p1["y"]))
        x2, y2 = map_trace_point(int(p2["x"]), int(p2["y"]))
        seg_ms = min_segment_ms
        if "t_ms" in p1 and "t_ms" in p2:
            try:
                delta = int(p2["t_ms"]) - int(p1["t_ms"])
                seg_ms = max(min_segment_ms, min(max_segment_ms, delta))
            except (TypeError, ValueError):
                seg_ms = min_segment_ms
        adb_shell(ctx, ["input", "swipe", str(x1), str(y1), str(x2), str(y2), str(seg_ms)])
    log(f"Trace replayed by segmented swipe with {len(points)} points")


def do_wait(action: Dict[str, Any]) -> None:
    seconds = float(action.get("seconds", 1.0))
    jitter = float(action.get("jitter_seconds", 0.0))
    if jitter > 0:
        seconds = max(0.0, seconds + random.uniform(-jitter, jitter))
    log(f"Wait {seconds:.2f}s")
    time.sleep(seconds)


def execute_patrol(ctx: RunContext, action: Dict[str, Any]) -> None:
    frm = action["from"]
    to = action["to"]
    duration_ms = int(action.get("duration_ms", 500))
    rest = float(action.get("leg_wait_sec", 0.4))
    rounds = int(action.get("rounds", 1))
    if rounds == 0:
        return
    count = 0
    while rounds < 0 or count < rounds:
        check_stop(ctx)
        do_swipe(
            ctx,
            {
                "x1": frm["x"],
                "y1": frm["y"],
                "x2": to["x"],
                "y2": to["y"],
                "duration_ms": duration_ms,
            },
        )
        do_wait({"seconds": rest})
        check_stop(ctx)
        do_swipe(
            ctx,
            {
                "x1": to["x"],
                "y1": to["y"],
                "x2": frm["x"],
                "y2": frm["y"],
                "duration_ms": duration_ms,
            },
        )
        do_wait({"seconds": rest})
        count += 1


def execute_action(ctx: RunContext, action: Dict[str, Any]) -> None:
    check_stop(ctx)
    action_type = action.get("type")
    if action_type == "click":
        do_click(ctx, action)
    elif action_type == "swipe":
        do_swipe(ctx, action)
    elif action_type == "trace":
        do_trace(ctx, action)
    elif action_type == "wait":
        do_wait(action)
    elif action_type == "sequence":
        execute_actions(ctx, action.get("actions", []))
    elif action_type == "loop":
        loop_count = int(action.get("count", 1))
        if loop_count == 0:
            return
        i = 0
        while loop_count < 0 or i < loop_count:
            execute_actions(ctx, action.get("actions", []))
            i += 1
    elif action_type == "patrol":
        execute_patrol(ctx, action)
    else:
        raise BotError(f"Unknown action type: {action_type}")


def execute_actions(ctx: RunContext, actions: List[Dict[str, Any]]) -> None:
    for action in actions:
        execute_action(ctx, action)


def _list_connected_devices(adb_path: str) -> List[str]:
    result = run_cmd([adb_path, "devices"], check=True)
    lines = [line.strip() for line in result.stdout.splitlines()[1:] if line.strip()]
    return [line.split()[0] for line in lines if "\tdevice" in line]


def _is_device_shell_healthy(adb_path: str, device: str) -> bool:
    # Some BlueStacks builds are flaky on one-off shell probes; retry a few times.
    for _ in range(3):
        result = run_cmd([adb_path, "-s", device, "shell", "getprop", "ro.build.version.release"], check=False)
        output = f"{result.stdout}\n{result.stderr}".lower()
        if result.returncode == 0 and "error: closed" not in output:
            return True
        time.sleep(0.2)
    return False


def ensure_device_connected(adb_path: str, device: Optional[str]) -> Optional[str]:
    try:
        connected = _list_connected_devices(adb_path)
    except FileNotFoundError as exc:
        raise BotError(f"adb not found: {adb_path}") from exc

    if device is None:
        if not connected:
            raise BotError(
                "No connected Android device/emulator found. Start BlueStacks and run: adb connect 127.0.0.1:5555"
            )
        for serial in connected:
            if _is_device_shell_healthy(adb_path, serial):
                log(f"Use device: {serial}")
                return serial
        raise BotError("All connected devices are unhealthy for adb shell.")

    if device not in connected:
        log(f"Device {device} not listed, trying adb connect ...")
        run_cmd([adb_path, "connect", device], check=False)
        connected = _list_connected_devices(adb_path)
        if device not in connected:
            raise BotError(f"Unable to connect to device {device}. Check ADB setting in BlueStacks.")

    if _is_device_shell_healthy(adb_path, device):
        return device

    for serial in connected:
        if serial == device:
            continue
        if _is_device_shell_healthy(adb_path, serial):
            log(f"Device {device} shell unhealthy, fallback to: {serial}")
            return serial

    log(f"Device {device} health probe failed; continue with requested device and rely on action-level retry.")
    return device


def load_plan(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BotError(f"Plan file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BotError(f"Invalid JSON in plan file {path}: {exc}") from exc


def get_device_screen_size(adb_path: str, device: Optional[str]) -> Tuple[int, int]:
    cmd = [adb_path]
    if device:
        cmd += ["-s", device]
    cmd += ["shell", "wm", "size"]
    result = run_cmd(cmd, check=False)
    text = f"{result.stdout}\n{result.stderr}"
    match = WM_SIZE_RE.search(text)
    if not match:
        raise BotError(f"Cannot parse device screen size from adb output:\n{text.strip()}")
    return int(match.group(1)), int(match.group(2))


def main() -> int:
    parser = argparse.ArgumentParser(description="BlueStacks ADB automation bot")
    parser.add_argument("--plan", required=True, help="Path to plan JSON file")
    parser.add_argument("--device", help="ADB device serial, e.g. 127.0.0.1:5555")
    parser.add_argument("--adb", default="adb", help="ADB binary path")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without executing")
    parser.add_argument("--max-runtime-sec", type=int, default=0, help="Stop after N seconds (0 means unlimited)")
    args = parser.parse_args()

    plan = load_plan(Path(args.plan))
    default_device = plan.get("device")
    cli_device = args.device.strip() if isinstance(args.device, str) else None
    plan_device = default_device.strip() if isinstance(default_device, str) else default_device
    device = cli_device or plan_device or None
    jitter = int(plan.get("jitter_px", 0))
    trace_time_scale = float(plan.get("trace_time_scale", 1.0))
    runtime_limit = args.max_runtime_sec or int(plan.get("max_runtime_sec", 0))
    stop_at = None if runtime_limit <= 0 else time.time() + runtime_limit
    screen_cfg = plan.get("screen_size", {})
    has_screen_cfg = isinstance(screen_cfg, dict) and "width" in screen_cfg and "height" in screen_cfg
    if has_screen_cfg:
        src_screen_w = int(screen_cfg.get("width", 1080))
        src_screen_h = int(screen_cfg.get("height", 1920))
    else:
        # Backward-compatible behavior for old plans: no implicit scaling.
        src_screen_w = -1
        src_screen_h = -1
    dst_screen_w = -1
    dst_screen_h = -1

    if not args.dry_run:
        device = ensure_device_connected(args.adb, device)
        dst_screen_w, dst_screen_h = get_device_screen_size(args.adb, device)
        if not has_screen_cfg:
            src_screen_w, src_screen_h = dst_screen_w, dst_screen_h
    elif not has_screen_cfg:
        src_screen_w, src_screen_h = 1080, 1920
        dst_screen_w, dst_screen_h = 1080, 1920

    ctx = RunContext(
        adb_path=args.adb,
        device=device,
        dry_run=args.dry_run,
        jitter=jitter,
        stop_at=stop_at,
        src_screen_w=src_screen_w,
        src_screen_h=src_screen_h,
        dst_screen_w=dst_screen_w,
        dst_screen_h=dst_screen_h,
        trace_time_scale=trace_time_scale,
    )

    actions = plan.get("actions")
    if not isinstance(actions, list):
        raise BotError("Plan must contain an 'actions' array")

    log("Bot start")
    log(f"Config jitter_px={ctx.jitter}")
    log(f"Config trace_time_scale={ctx.trace_time_scale}")
    log(f"Screen scale {ctx.src_screen_w}x{ctx.src_screen_h} -> {ctx.dst_screen_w}x{ctx.dst_screen_h}")
    try:
        execute_actions(ctx, actions)
    except KeyboardInterrupt:
        log("Bot stopped")
    log("Bot exit")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BotError as exc:
        log(f"ERROR: {exc}")
        raise SystemExit(1)
