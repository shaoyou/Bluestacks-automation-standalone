#!/usr/bin/env python3
import argparse
import csv
import io
import json
import os
import random
import re
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

WM_SIZE_RE = re.compile(r"(\d+)x(\d+)")
ENV_REF_RE = re.compile(r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
LAST_IF_IMAGE_MATCH_KEY = "last_if_image_match"


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
    plan_dir: Path
    runtime_values: Dict[str, Any] = field(default_factory=dict)
    motionevent_supported: Optional[bool] = None


@dataclass(frozen=True)
class PreparedSearchImage:
    crop_box: Tuple[int, int, int, int]
    rgb: np.ndarray
    gray: np.ndarray


@dataclass(frozen=True)
class PreparedTemplateVariant:
    scale: float
    width: int
    height: int
    rgb: np.ndarray
    weights: np.ndarray
    centered: np.ndarray
    weights_sum: float
    norm: float


_TEMPLATE_IMAGE_CACHE: Dict[Tuple[str, int, int], Image.Image] = {}
_TEMPLATE_VARIANT_CACHE: Dict[Tuple[str, int, int, int, int, Tuple[float, ...]], List[PreparedTemplateVariant]] = {}
SCREENSHOT_SESSION_KEY = "_save_screenshot_session_id"
SCREENSHOT_COUNTERS_KEY = "_save_screenshot_pair_counters"
SCREENSHOT_ACTIVE_KEY = "_save_screenshot_active_pairs"
DEFAULT_RESULT_SCREENSHOT_DIR = Path(__file__).resolve().parent / "diagnostics" / "draw_result_pairs"
SCREENSHOT_INDEX_CSV = "index.csv"
SCREENSHOT_INDEX_JSONL = "index.jsonl"
SCREENSHOT_INDEX_FIELDS = [
    "session_id",
    "pair_key",
    "pair_index",
    "pair_prefix",
    "before_label",
    "before_path",
    "before_saved_at",
    "after_label",
    "after_path",
    "after_saved_at",
]
DRAW_STATS_COUNTS_KEY = "_draw_stats_counts"
DEFAULT_DRAW_STATS_DIR = Path(__file__).resolve().parent / "diagnostics" / "draw_stats"
DRAW_STATS_EVENTS_JSONL_SUFFIX = "_events.jsonl"
DRAW_STATS_SUMMARY_SUFFIX = "_summary.json"
DRAW_STATS_LATEST_SUMMARY = "latest_summary.json"


def log(message: str) -> None:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def action_remark_text(action: Dict[str, Any]) -> str:
    raw_value = action.get("remark")
    if raw_value is None:
        return ""
    return re.sub(r"\s+", " ", str(raw_value)).strip()


def with_action_remark(message: str, action: Dict[str, Any]) -> str:
    remark = action_remark_text(action)
    if not remark:
        return message
    return f"{message} | remark: {remark}"


def resolve_action_template_paths(
    ctx: RunContext,
    action: Dict[str, Any],
    action_name: str,
) -> List[Path]:
    raw_templates = action.get("templates")
    template_paths: List[Path] = []

    if raw_templates is not None:
        if not isinstance(raw_templates, list) or not raw_templates:
            raise BotError(f"{action_name} 'templates' must be a non-empty array")
        for index, item in enumerate(raw_templates):
            if isinstance(item, str):
                raw_path = item.strip()
            elif isinstance(item, dict):
                raw_path = str(item.get("template") or item.get("image") or item.get("path") or "").strip()
            else:
                raw_path = ""
            if not raw_path:
                raise BotError(f"{action_name} templates[{index}] requires non-empty path")
            template_paths.append(resolve_action_file_path(ctx, raw_path))
        return template_paths

    raw_template = str(action.get("template") or action.get("image") or action.get("path") or "").strip()
    if not raw_template:
        raise BotError(f"{action_name} requires non-empty 'template'")
    template_paths.append(resolve_action_file_path(ctx, raw_template))
    return template_paths


def image_action_target_summary(template_paths: List[Path]) -> str:
    if len(template_paths) == 1:
        return f"template='{template_paths[0]}'"
    joined = ", ".join(f"'{path.name}'" for path in template_paths)
    return f"templates=[{joined}]"


def current_if_image_match(ctx: RunContext) -> Optional[Dict[str, Any]]:
    raw_value = ctx.runtime_values.get(LAST_IF_IMAGE_MATCH_KEY)
    if isinstance(raw_value, dict):
        return raw_value
    return None


def clear_if_image_match(ctx: RunContext) -> None:
    ctx.runtime_values.pop(LAST_IF_IMAGE_MATCH_KEY, None)


def _env_name(match: re.Match[str]) -> str:
    return match.group(1) or match.group(2) or ""


def _parse_env_literal(raw_value: str) -> Any:
    text = raw_value.strip()
    if not text:
        return raw_value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return raw_value


def plan_variable_dictionary(raw_plan: Dict[str, Any]) -> Dict[str, str]:
    def variable_value_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    raw_variables = raw_plan.get("variables", [])
    variables: Dict[str, str] = {}
    if isinstance(raw_variables, dict):
        for name, value in raw_variables.items():
            if not str(name).strip():
                continue
            variables[str(name).strip()] = variable_value_text(value)
        return variables
    if not isinstance(raw_variables, list):
        return variables
    for item in raw_variables:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        variables[name] = variable_value_text(item.get("value", ""))
    return variables


def resolve_plan_env_string(text: str, path: str, variables: Dict[str, str]) -> Any:
    matches = list(ENV_REF_RE.finditer(text))
    if not matches:
        return text

    if len(matches) == 1 and matches[0].span() == (0, len(text)):
        name = _env_name(matches[0])
        raw_value = variables.get(name)
        if raw_value is None:
            raise BotError(f"Missing script variable '{name}' for plan value at {path}")
        return _parse_env_literal(raw_value)

    def replace(match: re.Match[str]) -> str:
        name = _env_name(match)
        raw_value = variables.get(name)
        if raw_value is None:
            raise BotError(f"Missing script variable '{name}' for plan value at {path}")
        return raw_value

    return ENV_REF_RE.sub(replace, text)


def resolve_plan_env_value(value: Any, path: str = "plan", variables: Optional[Dict[str, str]] = None) -> Any:
    if variables is None:
        variables = {}
    if isinstance(value, dict):
        return {key: resolve_plan_env_value(item, f"{path}.{key}", variables) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_plan_env_value(item, f"{path}[{index}]", variables) for index, item in enumerate(value)]
    if isinstance(value, str):
        return resolve_plan_env_string(value, path, variables)
    return value


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


def adb_exec_out_result(ctx: RunContext, extra: List[str]) -> subprocess.CompletedProcess[bytes]:
    if ctx.dry_run:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
    cmd = build_adb_cmd(ctx, ["exec-out"] + extra)
    return subprocess.run(cmd, capture_output=True, check=False)


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
    log(with_action_remark(f"Click ({x}, {y})", action))


def tap_absolute(ctx: RunContext, x: int, y: int) -> Tuple[int, int]:
    if ctx.dst_screen_w > 0 and ctx.dst_screen_h > 0:
        x = max(0, min(ctx.dst_screen_w - 1, x))
        y = max(0, min(ctx.dst_screen_h - 1, y))
    x = apply_jitter(x, ctx.jitter)
    y = apply_jitter(y, ctx.jitter)
    adb_shell(ctx, ["input", "tap", str(x), str(y)])
    return x, y


def do_click_absolute(ctx: RunContext, x: int, y: int) -> None:
    x, y = tap_absolute(ctx, x, y)
    log(f"Click OCR target ({x}, {y})")


def do_click_match(ctx: RunContext, action: Dict[str, Any]) -> None:
    if ctx.dry_run:
        log(with_action_remark("[DRY-RUN] click_match uses the latest if_image target", action))
        return

    match_info = current_if_image_match(ctx)
    if match_info is None:
        raise BotError("click_match requires a previous matched if_image in the current runtime context")

    x = int(match_info.get("center_x", 0)) + int(action.get("offset_x", 0))
    y = int(match_info.get("center_y", 0)) + int(action.get("offset_y", 0))
    x, y = tap_absolute(ctx, x, y)
    template_name = str(match_info.get("template_name") or "unknown")
    log(with_action_remark(f"Click if_image match '{template_name}' at ({x}, {y})", action))


def capture_device_screenshot(ctx: RunContext) -> Image.Image:
    result = adb_exec_out_result(ctx, ["screencap", "-p"])
    if result.returncode != 0:
        text = (result.stderr or b"").decode("utf-8", errors="ignore").strip()
        raise BotError(f"ADB screencap failed:\n{text or 'unknown error'}")
    image_bytes = result.stdout
    if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise BotError("ADB screencap did not return a PNG image.")
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
        return image.convert("RGB")
    except Exception as exc:
        raise BotError(f"Failed to decode screencap PNG: {exc}") from exc


def screenshot_output_dir(ctx: RunContext, action: Dict[str, Any]) -> Path:
    raw_dir = str(action.get("dir", "")).strip()
    if not raw_dir:
        path = DEFAULT_RESULT_SCREENSHOT_DIR
    else:
        candidate = Path(os.path.expanduser(raw_dir))
        path = candidate if candidate.is_absolute() else (ctx.plan_dir / candidate).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def screenshot_session_id(ctx: RunContext) -> str:
    current = ctx.runtime_values.get(SCREENSHOT_SESSION_KEY)
    if isinstance(current, str) and current:
        return current
    value = time.strftime("%Y%m%d-%H%M%S")
    ctx.runtime_values[SCREENSHOT_SESSION_KEY] = value
    return value


def do_save_screenshot(ctx: RunContext, action: Dict[str, Any]) -> None:
    if ctx.dry_run:
        log(with_action_remark("[DRY-RUN] save_screenshot skipped", action))
        return

    output_dir = screenshot_output_dir(ctx, action)
    pair_key = sanitize_file_component(str(action.get("pair_key", "")).strip())
    stage = str(action.get("stage", "single")).strip().lower() or "single"
    label = sanitize_file_component(str(action.get("label", action.get("remark", "capture"))).strip())
    image = capture_device_screenshot(ctx)
    session_id = screenshot_session_id(ctx)

    if pair_key:
        counters = ctx.runtime_values.setdefault(SCREENSHOT_COUNTERS_KEY, {})
        active_pairs = ctx.runtime_values.setdefault(SCREENSHOT_ACTIVE_KEY, {})
        if not isinstance(counters, dict):
            counters = {}
            ctx.runtime_values[SCREENSHOT_COUNTERS_KEY] = counters
        if not isinstance(active_pairs, dict):
            active_pairs = {}
            ctx.runtime_values[SCREENSHOT_ACTIVE_KEY] = active_pairs

        if stage == "before":
            next_index = int(counters.get(pair_key, 0)) + 1
            counters[pair_key] = next_index
            pair_prefix = f"{session_id}_{pair_key}_{next_index:04d}"
            active_pairs[pair_key] = {
                "session_id": session_id,
                "pair_key": pair_key,
                "pair_index": next_index,
                "pair_prefix": pair_prefix,
            }
        elif stage == "after":
            active_pair = active_pairs.get(pair_key)
            if not isinstance(active_pair, dict):
                log(with_action_remark(f"save_screenshot skipped: no active pair for key '{pair_key}'", action))
                return
            pair_prefix = str(active_pair.get("pair_prefix") or "")
            if not pair_prefix:
                log(with_action_remark(f"save_screenshot skipped: invalid active pair for key '{pair_key}'", action))
                return
            active_pairs.pop(pair_key, None)
        else:
            next_index = int(counters.get(pair_key, 0)) + 1
            counters[pair_key] = next_index
            pair_prefix = f"{session_id}_{pair_key}_{next_index:04d}"

        filename = f"{pair_prefix}_{stage}"
        if label and label != "capture":
            filename += f"_{label}"
    else:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        filename = f"{session_id}_{timestamp}_{label or 'capture'}"

    destination = output_dir / f"{filename}.png"
    image.save(destination)
    saved_at = time.strftime("%Y-%m-%d %H:%M:%S")

    if pair_key:
        if stage == "before":
            active_pair = ctx.runtime_values[SCREENSHOT_ACTIVE_KEY][pair_key]
            active_pair["before_label"] = label
            active_pair["before_path"] = str(destination)
            active_pair["before_saved_at"] = saved_at
        elif stage == "after":
            record = dict(active_pair)
            record["after_label"] = label
            record["after_path"] = str(destination)
            record["after_saved_at"] = saved_at
            append_screenshot_index(output_dir, record)
        else:
            append_screenshot_index(
                output_dir,
                {
                    "session_id": session_id,
                    "pair_key": pair_key,
                    "pair_index": next_index,
                    "pair_prefix": pair_prefix,
                    "before_label": label,
                    "before_path": str(destination),
                    "before_saved_at": saved_at,
                    "after_label": "",
                    "after_path": "",
                    "after_saved_at": "",
                },
            )
    log(with_action_remark(f"Saved screenshot: {destination}", action))


def append_screenshot_index(output_dir: Path, record: Dict[str, Any]) -> None:
    normalized = {field: record.get(field, "") for field in SCREENSHOT_INDEX_FIELDS}

    csv_path = output_dir / SCREENSHOT_INDEX_CSV
    write_header = not csv_path.exists()
    with csv_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SCREENSHOT_INDEX_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(normalized)

    jsonl_path = output_dir / SCREENSHOT_INDEX_JSONL
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(normalized, ensure_ascii=False) + "\n")


def draw_stats_output_dir(ctx: RunContext, action: Dict[str, Any]) -> Path:
    raw_dir = str(action.get("dir", "")).strip()
    if not raw_dir:
        path = DEFAULT_DRAW_STATS_DIR
    else:
        candidate = Path(os.path.expanduser(raw_dir))
        path = candidate if candidate.is_absolute() else (ctx.plan_dir / candidate).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def draw_stats_counts(ctx: RunContext) -> Dict[str, int]:
    counts = ctx.runtime_values.get(DRAW_STATS_COUNTS_KEY)
    if not isinstance(counts, dict):
        counts = {"draw_started": 0, "target_hit": 0}
        ctx.runtime_values[DRAW_STATS_COUNTS_KEY] = counts
    counts.setdefault("draw_started", 0)
    counts.setdefault("target_hit", 0)
    return counts


def write_draw_stats_summary(output_dir: Path, summary: Dict[str, Any]) -> None:
    session_id = str(summary["session_id"])
    session_path = output_dir / f"{session_id}{DRAW_STATS_SUMMARY_SUFFIX}"
    latest_path = output_dir / DRAW_STATS_LATEST_SUMMARY
    payload = json.dumps(summary, ensure_ascii=False, indent=2)
    session_path.write_text(payload, encoding="utf-8")
    latest_path.write_text(payload, encoding="utf-8")


def append_draw_stats_event(output_dir: Path, session_id: str, record: Dict[str, Any]) -> None:
    jsonl_path = output_dir / f"{session_id}{DRAW_STATS_EVENTS_JSONL_SUFFIX}"
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def do_record_draw_event(ctx: RunContext, action: Dict[str, Any]) -> None:
    event = str(action.get("event", "")).strip().lower()
    if event not in {"draw_started", "target_hit"}:
        raise BotError("record_draw_event 'event' must be 'draw_started' or 'target_hit'")

    session_id = screenshot_session_id(ctx)
    output_dir = draw_stats_output_dir(ctx, action)
    counts = draw_stats_counts(ctx)
    counts[event] += 1

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    match_info = current_if_image_match(ctx) or {}
    draw_type = str(action.get("draw_type", "")).strip().lower()
    matched_template = str(match_info.get("template_name", ""))
    matched_center = {
        "x": int(match_info.get("center_x", 0)) if "center_x" in match_info else None,
        "y": int(match_info.get("center_y", 0)) if "center_y" in match_info else None,
    }

    event_record = {
        "timestamp": now,
        "session_id": session_id,
        "event": event,
        "draw_type": draw_type,
        "matched_template": matched_template,
        "matched_center": matched_center,
        "draw_started_count": int(counts["draw_started"]),
        "target_hit_count": int(counts["target_hit"]),
    }
    append_draw_stats_event(output_dir, session_id, event_record)

    summary = {
        "session_id": session_id,
        "updated_at": now,
        "draw_started_count": int(counts["draw_started"]),
        "target_hit_count": int(counts["target_hit"]),
        "latest_event": event,
        "latest_draw_type": draw_type,
        "latest_matched_template": matched_template,
        "events_path": str(output_dir / f"{session_id}{DRAW_STATS_EVENTS_JSONL_SUFFIX}"),
    }
    write_draw_stats_summary(output_dir, summary)
    extra = f" draw_type={draw_type}" if draw_type else ""
    target = f" matched_template={matched_template}" if matched_template else ""
    log(with_action_remark(
        f"Recorded draw event: {event}{extra}{target} "
        f"(draws={counts['draw_started']}, target_hits={counts['target_hit']})",
        action,
    ))


def bool_value(raw: Any, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "y", "on"}
    return default


def sanitize_file_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned or "image"


def image_match_debug_dir(ctx: RunContext, action: Dict[str, Any]) -> Path:
    raw_dir = str(action.get("debug_dir", "")).strip()
    if raw_dir:
        path = resolve_action_file_path(ctx, raw_dir)
    else:
        path = Path(__file__).resolve().parent / "diagnostics" / "image_match_debug"
    path.mkdir(parents=True, exist_ok=True)
    return path


def draw_crosshair(draw: ImageDraw.ImageDraw, x: int, y: int, color: Tuple[int, int, int], radius: int = 14) -> None:
    draw.line((x - radius, y, x + radius, y), fill=color, width=3)
    draw.line((x, y - radius, x, y + radius), fill=color, width=3)


def save_image_match_debug_assets(
    ctx: RunContext,
    screenshot: Image.Image,
    template_path: Path,
    action: Dict[str, Any],
    status: str,
    threshold: float,
    attempt: int,
    match: Optional[Dict[str, float]],
    note: str,
) -> Dict[str, Path]:
    debug_dir = image_match_debug_dir(ctx, action)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int((time.time() % 1) * 1000):03d}"
    base_name = f"{stamp}_{sanitize_file_component(template_path.stem)}_{sanitize_file_component(status)}"
    overlay_path = debug_dir / f"{base_name}_overlay.png"
    crop_path = debug_dir / f"{base_name}_crop.png"
    meta_path = debug_dir / f"{base_name}_meta.json"

    overlay = screenshot.convert("RGB").copy()
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default()

    region = action.get("region")
    if isinstance(region, dict):
        x = int(region.get("x", 0))
        y = int(region.get("y", 0))
        w = int(region.get("width", 0))
        h = int(region.get("height", 0))
        if w > 0 and h > 0:
            draw.rectangle((x, y, x + w, y + h), outline=(80, 140, 255), width=3)

    crop_saved = False
    if match is not None:
        left = int(round(match["x"]))
        top = int(round(match["y"]))
        right = int(round(match["x"] + match["width"]))
        bottom = int(round(match["y"] + match["height"]))
        center_x = int(round(match["x"] + match["width"] / 2.0))
        center_y = int(round(match["y"] + match["height"] / 2.0))
        draw.rectangle((left, top, right, bottom), outline=(255, 72, 72), width=4)
        draw_crosshair(draw, center_x, center_y, (255, 230, 64))
        try:
            screenshot.crop((left, top, right, bottom)).save(crop_path)
            crop_saved = True
        except Exception:
            crop_saved = False

    info_lines = [
        f"template: {template_path.name}",
        f"status: {status}",
        f"threshold: {threshold:.3f}",
        f"attempt: {attempt}",
    ]
    if match is not None:
        info_lines.append(
            "match: "
            f"sim={match['similarity']:.3f} "
            f"shape={match['shape_similarity']:.3f} "
            f"color={match['color_similarity']:.3f} "
            f"scale={match['scale']:.3f}"
        )
        info_lines.append(
            "box: "
            f"({int(round(match['x']))},{int(round(match['y']))})-"
            f"({int(round(match['x'] + match['width']))},{int(round(match['y'] + match['height']))})"
        )
    info_lines.append(f"note: {note}")

    max_line_width = max(draw.textbbox((0, 0), line, font=font)[2] for line in info_lines) if info_lines else 0
    line_height = max(draw.textbbox((0, 0), line, font=font)[3] for line in info_lines) if info_lines else 12
    box_height = 10 + len(info_lines) * (line_height + 4)
    draw.rectangle((8, 8, 24 + max_line_width, 16 + box_height), fill=(0, 0, 0))
    y = 14
    for line in info_lines:
        draw.text((14, y), line, fill=(255, 255, 255), font=font)
        y += line_height + 4

    overlay.save(overlay_path)

    metadata = {
        "template": str(template_path),
        "status": status,
        "threshold": threshold,
        "attempt": attempt,
        "note": note,
        "overlay_path": str(overlay_path),
        "crop_path": str(crop_path) if crop_saved else None,
        "region": region if isinstance(region, dict) else None,
        "match": match,
    }
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    result = {"overlay": overlay_path, "meta": meta_path}
    if crop_saved:
        result["crop"] = crop_path
    return result


def log_image_match_debug_assets(paths: Dict[str, Path]) -> None:
    log(f"Image match debug overlay: {paths['overlay']}")
    if "crop" in paths:
        log(f"Image match debug crop: {paths['crop']}")
    log(f"Image match debug meta: {paths['meta']}")


def template_cache_identity(path: Path) -> Tuple[str, int, int]:
    resolved = path.expanduser().resolve()
    try:
        stat = resolved.stat()
    except FileNotFoundError as exc:
        raise BotError(f"Template image not found: {resolved}") from exc
    return (str(resolved), stat.st_mtime_ns, stat.st_size)


def load_template_image(path: Path) -> Image.Image:
    cache_key = template_cache_identity(path)
    cached = _TEMPLATE_IMAGE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    resolved = Path(cache_key[0])
    try:
        with Image.open(resolved) as image:
            image.load()
            converted = image.convert("RGBA")
            _TEMPLATE_IMAGE_CACHE[cache_key] = converted
            return converted
    except Exception as exc:
        raise BotError(f"Failed to load template image {resolved}: {exc}") from exc


def resolve_action_file_path(ctx: RunContext, raw_path: str) -> Path:
    candidate = Path(os.path.expanduser(raw_path))
    if candidate.is_absolute():
        return candidate
    for base in [ctx.plan_dir, Path.cwd()]:
        resolved = (base / candidate).resolve()
        if resolved.exists():
            return resolved
    return (ctx.plan_dir / candidate).resolve()


def resolve_search_region(action: Dict[str, Any], image_size: Tuple[int, int]) -> Tuple[int, int, int, int]:
    width, height = image_size
    region = action.get("region")
    if not isinstance(region, dict):
        return (0, 0, width, height)
    x = max(0, int(region.get("x", 0)))
    y = max(0, int(region.get("y", 0)))
    w = int(region.get("width", width - x))
    h = int(region.get("height", height - y))
    w = max(1, min(w, width - x))
    h = max(1, min(h, height - y))
    return (x, y, x + w, y + h)


def prepare_search_image(screenshot: Image.Image, action: Dict[str, Any]) -> PreparedSearchImage:
    crop_box = resolve_search_region(action, screenshot.size)
    cropped = screenshot.crop(crop_box)
    return PreparedSearchImage(
        crop_box=crop_box,
        rgb=np.asarray(cropped.convert("RGB"), dtype=np.float32),
        gray=np.asarray(cropped.convert("L"), dtype=np.float32),
    )


def fft_correlate2d_valid(image: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    image_h, image_w = image.shape
    kernel_h, kernel_w = kernel.shape
    shape = (image_h + kernel_h - 1, image_w + kernel_w - 1)
    axes = (0, 1)
    flipped_kernel = np.flipud(np.fliplr(kernel))
    full = np.fft.irfftn(
        np.fft.rfftn(image, s=shape, axes=axes) * np.fft.rfftn(flipped_kernel, s=shape, axes=axes),
        s=shape,
        axes=axes,
    )
    valid = full[kernel_h - 1 : image_h, kernel_w - 1 : image_w]
    return valid.astype(np.float32, copy=False)


def prepare_template_arrays(image: Image.Image) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    rgba = image.convert("RGBA")
    rgb = np.asarray(rgba.convert("RGB"), dtype=np.float32)
    gray = np.asarray(rgba.convert("L"), dtype=np.float32)
    alpha = np.asarray(rgba.getchannel("A"), dtype=np.float32) / 255.0
    weights = np.where(alpha >= 0.05, alpha, 0.0).astype(np.float32)
    if float(weights.sum()) < max(9.0, weights.size * 0.05):
        weights = np.ones_like(gray, dtype=np.float32)
    return rgb, gray, weights


def build_template_scale_factors(action: Dict[str, Any]) -> List[float]:
    raw_scales = action.get("scales")
    parsed: List[float] = []
    if isinstance(raw_scales, list):
        for item in raw_scales:
            try:
                scale = float(item)
            except (TypeError, ValueError):
                continue
            if scale > 0:
                parsed.append(scale)
    if parsed:
        factors = parsed
    else:
        scale_min = max(0.1, float(action.get("scale_min", 0.75)))
        scale_max = max(scale_min, float(action.get("scale_max", 1.25)))
        scale_step = max(0.01, float(action.get("scale_step", 0.125)))
        factors = []
        current = scale_min
        while current <= scale_max + 1e-9:
            factors.append(round(current, 4))
            current += scale_step
        if not any(abs(value - 1.0) < 1e-6 for value in factors):
            factors.append(1.0)
    return sorted(set(round(value, 4) for value in factors if value > 0))


def prepare_template_variant(variant: Image.Image, scale: float) -> PreparedTemplateVariant:
    template_rgb, template_gray, weights = prepare_template_arrays(variant)
    weights_sum = max(float(weights.sum()), 1.0)
    template_mean = float((template_gray * weights).sum() / weights_sum)
    template_centered = (template_gray - template_mean) * weights
    template_norm = max(float(np.sqrt((template_centered * template_centered).sum())), 1e-6)
    return PreparedTemplateVariant(
        scale=scale,
        width=template_gray.shape[1],
        height=template_gray.shape[0],
        rgb=template_rgb,
        weights=weights,
        centered=template_centered,
        weights_sum=weights_sum,
        norm=template_norm,
    )


def build_template_variants(
    template: Image.Image,
    action: Dict[str, Any],
    search_size: Tuple[int, int],
) -> List[Tuple[float, Image.Image]]:
    search_w, search_h = search_size
    base_w, base_h = template.size
    resample = Image.Resampling.LANCZOS
    variants: List[Tuple[float, Image.Image]] = []
    seen_sizes: set[Tuple[int, int]] = set()
    for scale in build_template_scale_factors(action):
        target_w = max(1, int(round(base_w * scale)))
        target_h = max(1, int(round(base_h * scale)))
        if target_w > search_w or target_h > search_h:
            continue
        size = (target_w, target_h)
        if size in seen_sizes:
            continue
        seen_sizes.add(size)
        if size == template.size:
            variants.append((scale, template))
        else:
            variants.append((scale, template.resize(size, resample=resample)))
    if not variants:
        raise BotError(
            f"Template image is larger than search region across all scales: template={base_w}x{base_h}, "
            f"region={search_w}x{search_h}"
        )
    variants.sort(key=lambda item: abs(item[0] - 1.0))
    return variants


def get_prepared_template_variants(
    template_path: Path,
    action: Dict[str, Any],
    search_size: Tuple[int, int],
) -> List[PreparedTemplateVariant]:
    cache_identity = template_cache_identity(template_path)
    scale_key = tuple(build_template_scale_factors(action))
    cache_key = (
        cache_identity[0],
        cache_identity[1],
        cache_identity[2],
        search_size[0],
        search_size[1],
        scale_key,
    )
    cached = _TEMPLATE_VARIANT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    template = load_template_image(template_path)
    variants = build_template_variants(template, action, search_size)
    prepared = [prepare_template_variant(variant, scale) for scale, variant in variants]
    _TEMPLATE_VARIANT_CACHE[cache_key] = prepared
    return prepared


def weighted_color_similarity(
    search_rgb: np.ndarray,
    template_rgb: np.ndarray,
    weights: np.ndarray,
    x: int,
    y: int,
) -> float:
    template_h, template_w = template_rgb.shape[:2]
    patch = search_rgb[y : y + template_h, x : x + template_w]
    if patch.shape[:2] != template_rgb.shape[:2]:
        return 0.0
    diff = patch - template_rgb
    mse = float((((diff * diff).mean(axis=2)) * weights).sum() / max(float(weights.sum()), 1.0))
    return max(0.0, 1.0 - (mse ** 0.5) / 255.0)


def extract_peak_candidates(score_map: np.ndarray, count: int, suppress_x: int, suppress_y: int) -> List[Tuple[int, int, float]]:
    if score_map.size == 0 or count <= 0:
        return []
    work = score_map.copy()
    candidates: List[Tuple[int, int, float]] = []
    for _ in range(count):
        flat_index = int(np.argmax(work))
        best_score = float(work.flat[flat_index])
        if not np.isfinite(best_score):
            break
        y, x = np.unravel_index(flat_index, work.shape)
        candidates.append((x, y, best_score))
        y0 = max(0, y - suppress_y)
        y1 = min(work.shape[0], y + suppress_y + 1)
        x0 = max(0, x - suppress_x)
        x1 = min(work.shape[1], x + suppress_x + 1)
        work[y0:y1, x0:x1] = -np.inf
    return candidates


def find_image_match(
    search: PreparedSearchImage,
    template_variants: List[PreparedTemplateVariant],
    threshold: float,
    index: int,
    action: Dict[str, Any],
) -> Tuple[Optional[Dict[str, float]], Optional[Dict[str, float]]]:
    crop_box = search.crop_box
    search_rgb = search.rgb
    search_gray = search.gray
    evaluated: List[Dict[str, float]] = []
    wanted = max(index + 1, int(action.get("max_candidates", 8)))
    per_scale_peaks = max(4, min(16, wanted * 2))
    for variant in template_variants:
        sum_i = fft_correlate2d_valid(search_gray, variant.weights)
        sum_i2 = fft_correlate2d_valid(search_gray * search_gray, variant.weights)
        numerator = fft_correlate2d_valid(search_gray, variant.centered)

        variance = np.maximum(sum_i2 - (sum_i * sum_i) / variant.weights_sum, 1e-6)
        scores = numerator / (np.sqrt(variance) * variant.norm)
        scores = np.clip(np.nan_to_num(scores, nan=-1.0, posinf=-1.0, neginf=-1.0), -1.0, 1.0)

        peaks = extract_peak_candidates(
            scores,
            count=per_scale_peaks,
            suppress_x=max(4, variant.width // 3),
            suppress_y=max(4, variant.height // 3),
        )

        for peak_x, peak_y, raw_score in peaks:
            shape_similarity = max(0.0, min(1.0, (raw_score + 1.0) / 2.0))
            color_similarity = weighted_color_similarity(search_rgb, variant.rgb, variant.weights, peak_x, peak_y)
            similarity = 0.7 * shape_similarity + 0.3 * color_similarity
            evaluated.append(
                {
                    "x": float(crop_box[0] + peak_x),
                    "y": float(crop_box[1] + peak_y),
                    "width": float(variant.width),
                    "height": float(variant.height),
                    "similarity": float(similarity),
                    "shape_similarity": float(shape_similarity),
                    "color_similarity": float(color_similarity),
                    "scale": float(variant.scale),
                }
            )

    evaluated.sort(key=lambda item: item["similarity"], reverse=True)
    best_match = evaluated[0] if evaluated else None
    qualified = [item for item in evaluated if item["similarity"] >= threshold]
    if 0 <= index < len(qualified):
        return qualified[index], best_match
    return None, best_match


def normalize_ocr_text(text: str) -> str:
    return "".join(text.split()).lower()


def run_tesseract_tsv(image: Image.Image, lang: str, psm: int) -> List[Dict[str, str]]:
    with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
        image.save(tmp.name, format="PNG")
        cmd = [
            "tesseract",
            tmp.name,
            "stdout",
            "-l",
            lang,
            "--psm",
            str(psm),
            "tsv",
        ]
        result = run_cmd(cmd, check=False)
        if result.returncode != 0:
            details = (result.stderr or result.stdout or "").strip()
            raise BotError(f"Tesseract OCR failed:\n{details}")
        reader = csv.DictReader(io.StringIO(result.stdout), delimiter="\t")
        return [row for row in reader]


def find_text_region(
    rows: List[Dict[str, str]],
    target_text: str,
    match_mode: str,
    index: int,
) -> Optional[Tuple[int, int, int, int, str]]:
    normalized_target = normalize_ocr_text(target_text)
    if not normalized_target:
        return None

    line_groups: Dict[Tuple[str, str, str, str], List[Dict[str, str]]] = {}
    for row in rows:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        key = (
            row.get("block_num", ""),
            row.get("par_num", ""),
            row.get("line_num", ""),
            row.get("page_num", ""),
        )
        line_groups.setdefault(key, []).append(row)

    matches: List[Tuple[int, int, int, int, str]] = []
    for key in sorted(line_groups.keys()):
        group = line_groups[key]
        parts = [(row.get("text") or "").strip() for row in group]
        combined = normalize_ocr_text("".join(parts))
        if not combined:
            continue
        matched = combined == normalized_target if match_mode == "exact" else normalized_target in combined
        if not matched:
            continue
        xs = [int(row["left"]) for row in group]
        ys = [int(row["top"]) for row in group]
        rights = [int(row["left"]) + int(row["width"]) for row in group]
        bottoms = [int(row["top"]) + int(row["height"]) for row in group]
        matches.append((min(xs), min(ys), max(rights), max(bottoms), "".join(parts)))

    if matches:
        return matches[index] if 0 <= index < len(matches) else None

    for row in rows:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        normalized = normalize_ocr_text(text)
        matched = normalized == normalized_target if match_mode == "exact" else normalized_target in normalized
        if not matched:
            continue
        left = int(row["left"])
        top = int(row["top"])
        right = left + int(row["width"])
        bottom = top + int(row["height"])
        matches.append((left, top, right, bottom, text))
    return matches[index] if 0 <= index < len(matches) else None


def do_find_text_click(ctx: RunContext, action: Dict[str, Any]) -> None:
    target_text = str(action.get("text", "")).strip()
    if not target_text:
        raise BotError("find_text_click requires non-empty 'text'")
    timeout_sec = float(action.get("timeout_sec", 8.0))
    interval_sec = float(action.get("interval_sec", 0.8))
    match_mode = str(action.get("match", "contains")).strip().lower()
    if match_mode not in {"contains", "exact"}:
        raise BotError("find_text_click 'match' must be 'contains' or 'exact'")
    lang = str(action.get("lang", "eng")).strip() or "eng"
    psm = int(action.get("psm", 6))
    index = int(action.get("index", 0))
    offset_x = int(action.get("offset_x", 0))
    offset_y = int(action.get("offset_y", 0))
    if ctx.dry_run:
        log(
            with_action_remark(
                f"[DRY-RUN] find_text_click text='{target_text}' "
                f"lang={lang} match={match_mode} timeout={timeout_sec}s",
                action,
            )
        )
        return

    deadline = time.time() + max(0.0, timeout_sec)
    attempt = 0
    while True:
        check_stop(ctx)
        attempt += 1
        image = capture_device_screenshot(ctx)
        rows = run_tesseract_tsv(image, lang=lang, psm=psm)
        region = find_text_region(rows, target_text=target_text, match_mode=match_mode, index=index)
        if region is not None:
            left, top, right, bottom, matched_text = region
            center_x = int(round((left + right) / 2.0)) + offset_x
            center_y = int(round((top + bottom) / 2.0)) + offset_y
            log(
                with_action_remark(
                    f"OCR matched text '{matched_text}' for target '{target_text}' "
                    f"at box=({left},{top})-({right},{bottom}) on attempt {attempt}",
                    action,
                )
            )
            do_click_absolute(ctx, center_x, center_y)
            return
        if time.time() >= deadline:
            raise BotError(
                f"find_text_click timed out after {timeout_sec:.1f}s: "
                f"text '{target_text}' not found (lang={lang}, match={match_mode})"
            )
        time.sleep(max(0.1, interval_sec))


def resolve_find_image_action(
    ctx: RunContext,
    action: Dict[str, Any],
    action_name: str,
) -> Dict[str, Any]:
    template_paths = resolve_action_template_paths(ctx, action, action_name)
    threshold = float(action.get("threshold", 0.92))
    timeout_sec = float(action.get("timeout_sec", 8.0))
    interval_sec = float(action.get("interval_sec", 0.6))
    max_attempts = max(0, int(action.get("max_attempts", 0)))
    index = int(action.get("index", 0))
    offset_x = int(action.get("offset_x", 0))
    offset_y = int(action.get("offset_y", 0))
    preview_only = bool_value(action.get("preview_only")) or bool_value(action.get("debug_preview"))
    save_debug = bool_value(action.get("save_debug", True), default=True)
    return {
        "template_paths": template_paths,
        "threshold": threshold,
        "timeout_sec": timeout_sec,
        "interval_sec": interval_sec,
        "max_attempts": max_attempts,
        "index": index,
        "offset_x": offset_x,
        "offset_y": offset_y,
        "preview_only": preview_only,
        "save_debug": save_debug,
    }


def perform_find_image(
    ctx: RunContext,
    action: Dict[str, Any],
    action_name: str,
    *,
    matched_status: str,
    matched_note: str,
    timeout_status: str,
    timeout_note: str,
    timeout_log_suffix: str = "",
    raise_on_timeout: bool,
) -> Dict[str, Any]:
    settings = resolve_find_image_action(ctx, action, action_name)
    template_paths = settings["template_paths"]
    threshold = settings["threshold"]
    timeout_sec = settings["timeout_sec"]
    interval_sec = settings["interval_sec"]
    max_attempts = settings["max_attempts"]
    index = settings["index"]
    save_debug = settings["save_debug"]
    preview_only = settings["preview_only"]
    log_template_progress = action.get("templates") is not None
    if ctx.dry_run:
        attempt_text = f" max_attempts={max_attempts}" if max_attempts > 0 else ""
        log(
            with_action_remark(
                f"[DRY-RUN] {action_name} {image_action_target_summary(template_paths)} "
                f"threshold={threshold:.3f} timeout={timeout_sec:.1f}s{attempt_text} preview_only={preview_only}",
                action,
            )
        )
        return {
            "matched": False,
            "match": None,
            "best_similarity": 0.0,
            "template_path": template_paths[0],
            "threshold": threshold,
            "preview_only": preview_only,
            "dry_run": True,
            "debug_paths": None,
        }

    deadline = time.time() + max(0.0, timeout_sec)
    attempt = 0
    best_similarity = 0.0
    best_match: Optional[Dict[str, float]] = None
    best_template_path: Optional[Path] = None
    best_screenshot: Optional[Image.Image] = None
    best_attempt = 0
    while True:
        check_stop(ctx)
        attempt += 1
        screenshot = capture_device_screenshot(ctx)
        search = prepare_search_image(screenshot, action)
        search_size = (search.gray.shape[1], search.gray.shape[0])
        matched_template_path: Optional[Path] = None
        matched_template_candidate: Optional[Dict[str, float]] = None

        template_total = len(template_paths)
        for template_index, template_path in enumerate(template_paths, start=1):
            prepared_variants = get_prepared_template_variants(template_path, action, search_size)
            match, candidate_best = find_image_match(
                search=search,
                template_variants=prepared_variants,
                threshold=threshold,
                index=index,
                action=action,
            )
            if candidate_best is not None and candidate_best["similarity"] >= best_similarity:
                best_similarity = candidate_best["similarity"]
                best_match = dict(candidate_best)
                best_template_path = template_path
                best_screenshot = screenshot.copy()
                best_attempt = attempt
            if log_template_progress:
                progress_label = f"[{template_index}/{template_total}]"
                if match is not None:
                    log(
                        with_action_remark(
                            f"{action_name} {progress_label} template '{template_path.name}' matched on attempt {attempt} "
                            f"similarity={match['similarity']:.3f}",
                            action,
                        )
                    )
                else:
                    template_best_similarity = float(candidate_best["similarity"]) if candidate_best is not None else 0.0
                    log(
                        with_action_remark(
                            f"{action_name} {progress_label} template '{template_path.name}' not matched on attempt {attempt} "
                            f"(best similarity {template_best_similarity:.3f})",
                            action,
                        )
                    )
            if match is not None:
                matched_template_path = template_path
                matched_template_candidate = match
                break

        if matched_template_path is not None and matched_template_candidate is not None:
            debug_paths: Optional[Dict[str, Path]] = None
            if save_debug:
                debug_paths = save_image_match_debug_assets(
                    ctx=ctx,
                    screenshot=screenshot,
                    template_path=matched_template_path,
                    action=action,
                    status=matched_status,
                    threshold=threshold,
                    attempt=attempt,
                    match=matched_template_candidate,
                    note=matched_note,
                )
                log_image_match_debug_assets(debug_paths)
            return {
                "matched": True,
                "match": matched_template_candidate,
                "best_similarity": best_similarity,
                "template_path": matched_template_path,
                "threshold": threshold,
                "preview_only": preview_only,
                "dry_run": False,
                "debug_paths": debug_paths,
                "attempt": attempt,
            }
        attempts_exhausted = max_attempts > 0 and attempt >= max_attempts
        timed_out = time.time() >= deadline
        if attempts_exhausted or timed_out:
            debug_suffix = ""
            debug_paths: Optional[Dict[str, Path]] = None
            timeout_template_path = best_template_path or template_paths[0]
            if save_debug and best_screenshot is not None and best_template_path is not None:
                debug_paths = save_image_match_debug_assets(
                    ctx=ctx,
                    screenshot=best_screenshot,
                    template_path=timeout_template_path,
                    action=action,
                    status=timeout_status,
                    threshold=threshold,
                    attempt=best_attempt,
                    match=best_match,
                    note=timeout_note,
                )
                log_image_match_debug_assets(debug_paths)
                debug_suffix = f"; debug overlay saved to {debug_paths['overlay']}"
            result = {
                "matched": False,
                "match": best_match,
                "best_similarity": best_similarity,
                "template_path": timeout_template_path,
                "threshold": threshold,
                "preview_only": preview_only,
                "dry_run": False,
                "debug_paths": debug_paths,
                "attempt": best_attempt,
            }
            if attempts_exhausted and not timed_out:
                stop_reason = f"after {attempt} attempt(s)"
                error_reason = f"stopped after {attempt} attempt(s)"
            else:
                stop_reason = f"within {timeout_sec:.1f}s"
                error_reason = f"timed out after {timeout_sec:.1f}s"
            if raise_on_timeout:
                raise BotError(
                    f"{action_name} {error_reason}: "
                    f"{image_action_target_summary(template_paths)} not found with threshold {threshold:.3f} "
                    f"(best similarity {best_similarity:.3f}){debug_suffix}"
                )
            log(
                with_action_remark(
                    f"{action_name} did not find any target {stop_reason} "
                    f"(best similarity {best_similarity:.3f}){timeout_log_suffix}",
                    action,
                )
            )
            return result
        time.sleep(max(0.1, interval_sec))


def do_find_image_click(ctx: RunContext, action: Dict[str, Any]) -> None:
    result = perform_find_image(
        ctx,
        action,
        "find_image_click",
        matched_status="preview" if (bool_value(action.get("preview_only")) or bool_value(action.get("debug_preview"))) else "matched",
        matched_note="preview only" if (bool_value(action.get("preview_only")) or bool_value(action.get("debug_preview"))) else "matched and clicked",
        timeout_status="timeout",
        timeout_note="best candidate before timeout",
        timeout_log_suffix=", skip current action and continue",
        raise_on_timeout=False,
    )
    match = result["match"]
    if result.get("dry_run"):
        return
    if not result["matched"] or match is None:
        return
    center_x = int(round(match["x"] + match["width"] / 2.0)) + int(action.get("offset_x", 0))
    center_y = int(round(match["y"] + match["height"] / 2.0)) + int(action.get("offset_y", 0))
    log(
        with_action_remark(
            f"Image matched '{result['template_path'].name}' at box="
            f"({int(match['x'])},{int(match['y'])})-"
            f"({int(match['x'] + match['width'])},{int(match['y'] + match['height'])}) "
            f"similarity={match['similarity']:.3f} "
            f"(shape={match['shape_similarity']:.3f}, color={match['color_similarity']:.3f}, scale={match['scale']:.3f}) "
            f"on attempt {result['attempt']}",
            action,
        )
    )
    if result["preview_only"]:
        log(f"Preview only mode: highlighted match at ({center_x}, {center_y}), no click executed")
        return
    do_click_absolute(ctx, center_x, center_y)


def do_find_image(ctx: RunContext, action: Dict[str, Any]) -> None:
    result = perform_find_image(
        ctx,
        action,
        "find_image",
        matched_status="found",
        matched_note="image detected without click",
        timeout_status="not_found",
        timeout_note="best candidate before detect timeout",
        timeout_log_suffix="",
        raise_on_timeout=False,
    )
    template_path = result["template_path"]
    match = result["match"]
    if result["matched"] and match is not None:
        center_x = int(round(match["x"] + match["width"] / 2.0)) + int(action.get("offset_x", 0))
        center_y = int(round(match["y"] + match["height"] / 2.0)) + int(action.get("offset_y", 0))
        log(
            with_action_remark(
                f"Image detected '{template_path.name}' at box="
                f"({int(match['x'])},{int(match['y'])})-"
                f"({int(match['x'] + match['width'])},{int(match['y'] + match['height'])}) "
                f"center=({center_x}, {center_y}) "
                f"similarity={match['similarity']:.3f}",
                action,
            )
        )
    else:
        log(
            with_action_remark(
                f"Image not detected: '{template_path.name}' "
                f"(best similarity {result['best_similarity']:.3f})",
                action,
            )
        )


def remember_if_image_match(ctx: RunContext, action: Dict[str, Any], result: Dict[str, Any]) -> None:
    clear_if_image_match(ctx)
    if result.get("dry_run") or not result.get("matched"):
        return

    match = result.get("match")
    if not isinstance(match, dict):
        return

    raw_center_x = int(round(float(match.get("x", 0)) + float(match.get("width", 0)) / 2.0))
    raw_center_y = int(round(float(match.get("y", 0)) + float(match.get("height", 0)) / 2.0))
    center_x = raw_center_x + int(action.get("offset_x", 0))
    center_y = raw_center_y + int(action.get("offset_y", 0))
    template_path = result.get("template_path")
    template_name = template_path.name if isinstance(template_path, Path) else str(template_path or "")
    ctx.runtime_values[LAST_IF_IMAGE_MATCH_KEY] = {
        "template_name": template_name,
        "template_path": str(template_path) if template_path is not None else "",
        "center_x": center_x,
        "center_y": center_y,
        "match_center_x": raw_center_x,
        "match_center_y": raw_center_y,
        "x": int(round(float(match.get("x", 0)))),
        "y": int(round(float(match.get("y", 0)))),
        "width": int(round(float(match.get("width", 0)))),
        "height": int(round(float(match.get("height", 0)))),
        "similarity": float(match.get("similarity", 0.0)),
    }


def execute_if_image(ctx: RunContext, action: Dict[str, Any]) -> None:
    clear_if_image_match(ctx)
    result = perform_find_image(
        ctx,
        action,
        "if_image",
        matched_status="if_true",
        matched_note="if_image condition matched",
        timeout_status="if_false",
        timeout_note="if_image best candidate before false branch",
        timeout_log_suffix="",
        raise_on_timeout=False,
    )
    remember_if_image_match(ctx, action, result)
    then_actions = action.get("then_actions", [])
    else_actions = action.get("else_actions", [])
    if not isinstance(then_actions, list):
        raise BotError("if_image 'then_actions' must be an array")
    if not isinstance(else_actions, list):
        raise BotError("if_image 'else_actions' must be an array")
    if result.get("dry_run"):
        log(with_action_remark("if_image dry-run: condition evaluated in preview only, branches skipped", action))
        return
    template_label = getattr(result["template_path"], "name", str(result["template_path"]))
    if result["matched"]:
        log(with_action_remark(f"if_image matched '{template_label}', execute then_actions", action))
        execute_actions(ctx, then_actions)
    else:
        log(with_action_remark(f"if_image did not match '{template_label}', execute else_actions", action))
        execute_actions(ctx, else_actions)


def do_swipe(ctx: RunContext, action: Dict[str, Any]) -> None:
    x1, y1 = map_input_point(ctx, int(action["x1"]), int(action["y1"]))
    x2, y2 = map_input_point(ctx, int(action["x2"]), int(action["y2"]))
    x1 = apply_jitter(x1, ctx.jitter)
    y1 = apply_jitter(y1, ctx.jitter)
    x2 = apply_jitter(x2, ctx.jitter)
    y2 = apply_jitter(y2, ctx.jitter)
    duration = int(action.get("duration_ms", 300))
    adb_shell(ctx, ["input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)])
    log(with_action_remark(f"Swipe ({x1}, {y1}) -> ({x2}, {y2}), {duration}ms", action))


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
        log(with_action_remark(f"Trace replayed continuously with {len(points)} points", action))
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
    log(with_action_remark(f"Trace replayed by segmented swipe with {len(points)} points", action))


def do_wait(action: Dict[str, Any]) -> None:
    seconds = float(action.get("seconds", 1.0))
    jitter = float(action.get("jitter_seconds", 0.0))
    if jitter > 0:
        seconds = max(0.0, seconds + random.uniform(-jitter, jitter))
    log(with_action_remark(f"Wait {seconds:.2f}s", action))
    time.sleep(seconds)


def execute_patrol(ctx: RunContext, action: Dict[str, Any]) -> None:
    frm = action["from"]
    to = action["to"]
    duration_ms = int(action.get("duration_ms", 500))
    rest = float(action.get("leg_wait_sec", 0.4))
    rounds = int(action.get("rounds", 1))
    if rounds == 0:
        return
    log(
        with_action_remark(
            f"Patrol start rounds={rounds} duration_ms={duration_ms} leg_wait_sec={rest:.2f}",
            action,
        )
    )
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
    elif action_type == "click_match":
        do_click_match(ctx, action)
    elif action_type == "record_draw_event":
        do_record_draw_event(ctx, action)
    elif action_type == "save_screenshot":
        do_save_screenshot(ctx, action)
    elif action_type == "find_image":
        do_find_image(ctx, action)
    elif action_type == "find_image_click":
        do_find_image_click(ctx, action)
    elif action_type == "find_text_click":
        do_find_text_click(ctx, action)
    elif action_type == "if_image":
        execute_if_image(ctx, action)
    elif action_type == "swipe":
        do_swipe(ctx, action)
    elif action_type == "trace":
        do_trace(ctx, action)
    elif action_type == "wait":
        do_wait(action)
    elif action_type == "sequence":
        nested_actions = action.get("actions", [])
        nested_count = len(nested_actions) if isinstance(nested_actions, list) else 0
        log(with_action_remark(f"Sequence start ({nested_count} actions)", action))
        execute_actions(ctx, nested_actions)
    elif action_type == "loop":
        loop_count = int(action.get("count", 1))
        if loop_count == 0:
            return
        loop_label = "infinite" if loop_count < 0 else str(loop_count)
        log(with_action_remark(f"Loop start count={loop_label}", action))
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
        raw_plan = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BotError(f"Plan file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BotError(f"Invalid JSON in plan file {path}: {exc}") from exc
    resolved = resolve_plan_env_value(raw_plan, path="plan", variables=plan_variable_dictionary(raw_plan))
    if not isinstance(resolved, dict):
        raise BotError(f"Plan root must be a JSON object: {path}")
    return resolved


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

    plan_path = Path(args.plan).expanduser().resolve()
    plan = load_plan(plan_path)
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
        plan_dir=plan_path.parent,
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
