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
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

WM_SIZE_RE = re.compile(r"(\d+)x(\d+)")
ENV_REF_RE = re.compile(r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
LAST_IF_IMAGE_MATCH_KEY = "last_if_image_match"
LAST_IF_IMAGE_SCREENSHOT_KEY = "last_if_image_screenshot"
LAST_RED_LIGHT_SCREENSHOT_KEY = "last_red_light_screenshot"
LAST_SCREENSHOT_PAIR_KEY = "_latest_screenshot_pair"
BACKGROUND_TASKS_KEY = "_background_tasks"


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
    stats_lock: threading.RLock = field(default_factory=threading.RLock)


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


def runtime_data_root() -> Path:
    # PyInstaller one-file apps run from a temporary _MEI directory. On Windows 7,
    # resolving that virtual path can fail, and diagnostics must outlive the process.
    if getattr(sys, "frozen", False):
        return Path.cwd()
    return Path(__file__).parent


RUNTIME_DATA_ROOT = runtime_data_root()
DEFAULT_RESULT_SCREENSHOT_DIR = RUNTIME_DATA_ROOT / "diagnostics" / "draw_result_pairs"
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
    "composite_path",
    "composite_saved_at",
]
DRAW_STATS_COUNTS_KEY = "_draw_stats_counts"
DEFAULT_DRAW_STATS_DIR = RUNTIME_DATA_ROOT / "diagnostics" / "draw_stats"
DRAW_STATS_EVENTS_JSONL_SUFFIX = "_events.jsonl"
DRAW_STATS_SUMMARY_SUFFIX = "_summary.json"
DRAW_STATS_LATEST_SUMMARY = "latest_summary.json"
DEFAULT_RED_LIGHT_DEBUG_DIR = RUNTIME_DATA_ROOT / "diagnostics" / "red_light_debug"
DEFAULT_RED_LIGHT_REGIONS = [
    {"name": "left", "x": 110, "y": 300, "width": 250, "height": 760},
    {"name": "center", "x": 390, "y": 220, "width": 300, "height": 860},
    {"name": "right", "x": 720, "y": 300, "width": 280, "height": 760},
]
DEFAULT_RED_LIGHT_SLOTS = [
    {
        "name": "left",
        "x": 105,
        "y": 420,
        "width": 250,
        "height": 655,
        "card": {"x": 118, "y": 590, "width": 95, "height": 115},
        "base": {"x": 105, "y": 930, "width": 250, "height": 145},
        "beam": {"x": 120, "y": 420, "width": 210, "height": 520},
    },
    {
        "name": "center",
        "x": 405,
        "y": 360,
        "width": 270,
        "height": 755,
        "card": {"x": 420, "y": 660, "width": 95, "height": 115},
        "base": {"x": 405, "y": 970, "width": 270, "height": 145},
        "beam": {"x": 425, "y": 360, "width": 230, "height": 620},
    },
    {
        "name": "right",
        "x": 720,
        "y": 420,
        "width": 270,
        "height": 655,
        "card": {"x": 735, "y": 590, "width": 95, "height": 115},
        "base": {"x": 720, "y": 930, "width": 270, "height": 145},
        "beam": {"x": 740, "y": 420, "width": 220, "height": 520},
    },
]
DEFAULT_RED_ROLE_TEMPLATES = [
    "../image_templates/role_bosiwangzi.png",
    "../image_templates/role_kakaxi.png",
    "../image_templates/role_libai.png",
    "../image_templates/role_longsan.png",
    "../image_templates/role_lujuren.png",
    "../image_templates/role_shengqishi.png",
    "../image_templates/role_woailuo.png",
    "../image_templates/role_zhizhu.png",
]
DEFAULT_RED_ROLE_NOTES = {
    "role_bosiwangzi.png": "波斯王子",
    "role_kakaxi.png": "卡卡西",
    "role_libai.png": "李白",
    "role_longsan.png": "龙三",
    "role_lujuren.png": "绿巨人",
    "role_shengqishi.png": "圣骑士",
    "role_woailuo.png": "我爱罗",
    "role_zhizhu.png": "蜘蛛",
}
DEFAULT_RED_ROLE_SEARCH_REGIONS = {
    "left": {"x": 65, "y": 780, "width": 315, "height": 365},
    "center": {"x": 385, "y": 780, "width": 310, "height": 365},
    "right": {"x": 705, "y": 780, "width": 315, "height": 365},
}
DEFAULT_GREEN_CHECK_REGIONS = {
    "left": {"x": 80, "y": 760, "width": 300, "height": 380},
    "center": {"x": 390, "y": 780, "width": 320, "height": 380},
    "right": {"x": 700, "y": 760, "width": 330, "height": 380},
}


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
    else:
        raw_template = str(action.get("template") or action.get("image") or action.get("path") or "").strip()
        if not raw_template:
            raise BotError(f"{action_name} requires non-empty 'template'")
        template_paths.append(resolve_action_file_path(ctx, raw_template))

    # The original cancel template is text-only and may match other labels. When
    # available, also try the contextual native screenshot template.
    if any(path.name == "role_cancel.png" for path in template_paths):
        contextual_path = resolve_action_file_path(ctx, "../image_templates/role_cancel_phone.png")
        if contextual_path.exists() and contextual_path not in template_paths:
            template_paths.insert(0, contextual_path)
    return template_paths


def image_search_action(action: Dict[str, Any], template_paths: List[Path]) -> Dict[str, Any]:
    """Add a wider default region for the contextual cancel-button lookup."""
    if not any(path.name == "role_cancel.png" for path in template_paths):
        return action
    search_action = dict(action)
    search_action.setdefault("search_padding_ratio", 1.3)
    return search_action


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
    ctx.runtime_values.pop(LAST_IF_IMAGE_SCREENSHOT_KEY, None)


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


def recover_adb_server(adb_path: str, device: Optional[str] = None) -> None:
    if device:
        run_cmd([adb_path, "connect", device], check=False)
    run_cmd([adb_path, "kill-server"], check=False)
    run_cmd([adb_path, "start-server"], check=False)
    if device:
        run_cmd([adb_path, "connect", device], check=False)
    time.sleep(0.6)


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
        transient = adb_output_is_transient(text)
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
                retry_transient = adb_output_is_transient(retry_text)
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


def image_scale_factors(ctx: RunContext, image_size: Tuple[int, int]) -> Tuple[float, float]:
    width, height = image_size
    if ctx.src_screen_w <= 0 or ctx.src_screen_h <= 0:
        return 1.0, 1.0
    return width / float(ctx.src_screen_w), height / float(ctx.src_screen_h)


def map_logical_region(
    ctx: RunContext,
    raw_region: Dict[str, Any],
    image_size: Tuple[int, int],
    *,
    name: str,
    default_width: Optional[int] = None,
    default_height: Optional[int] = None,
) -> Dict[str, int]:
    image_w, image_h = image_size
    raw_x = int(raw_region.get("x", 0))
    raw_y = int(raw_region.get("y", 0))
    raw_w = int(raw_region.get("width", default_width if default_width is not None else 0))
    raw_h = int(raw_region.get("height", default_height if default_height is not None else 0))
    if raw_w <= 0 or raw_h <= 0:
        raise BotError(f"{name} requires positive width and height")

    scale_x, scale_y = image_scale_factors(ctx, image_size)
    epsilon = 1e-6
    left = max(0, min(image_w - 1, int(np.floor(raw_x * scale_x + epsilon))))
    top = max(0, min(image_h - 1, int(np.floor(raw_y * scale_y + epsilon))))
    right = max(left + 1, min(image_w, int(np.ceil((raw_x + raw_w) * scale_x - epsilon))))
    bottom = max(top + 1, min(image_h, int(np.ceil((raw_y + raw_h) * scale_y - epsilon))))
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def clamp_device_region(raw_region: Dict[str, Any], image_size: Tuple[int, int], *, name: str) -> Dict[str, int]:
    image_w, image_h = image_size
    x = max(0, min(image_w - 1, int(raw_region.get("x", 0))))
    y = max(0, min(image_h - 1, int(raw_region.get("y", 0))))
    width = int(raw_region.get("width", 0))
    height = int(raw_region.get("height", 0))
    if width <= 0 or height <= 0:
        raise BotError(f"{name} requires positive width and height")
    width = max(1, min(width, image_w - x))
    height = max(1, min(height, image_h - y))
    return {"x": x, "y": y, "width": width, "height": height}


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

    image_source = "adb screencap"
    image = None
    if stage == "before" and bool_value(action.get("reuse_last_red_light_screenshot"), default=False):
        cached = ctx.runtime_values.get(LAST_RED_LIGHT_SCREENSHOT_KEY)
        if isinstance(cached, Image.Image):
            image = cached.copy()
            image_source = "confirmed red light screenshot"
    if image is None and stage == "before" and bool_value(action.get("reuse_last_if_image_screenshot", True), default=True):
        cached = ctx.runtime_values.get(LAST_IF_IMAGE_SCREENSHOT_KEY)
        if isinstance(cached, Image.Image):
            image = cached.copy()
            image_source = "latest if_image screenshot"
    if image is None:
        image = capture_device_screenshot(ctx)

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
            if bool_value(action.get("save_composite", True), default=True):
                composite = save_screenshot_pair_composite(output_dir, record)
                if composite is not None:
                    record["composite_path"] = str(composite)
                    record["composite_saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    log(with_action_remark(f"Saved screenshot pair composite: {composite}", action))
            append_screenshot_index(output_dir, record)
            ctx.runtime_values[LAST_SCREENSHOT_PAIR_KEY] = dict(record)
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
                    "composite_path": "",
                    "composite_saved_at": "",
                },
            )
    log(with_action_remark(f"Saved screenshot: {destination} ({image_source})", action))


def save_screenshot_pair_composite(output_dir: Path, record: Dict[str, Any]) -> Optional[Path]:
    before_path = Path(str(record.get("before_path", "")))
    after_path = Path(str(record.get("after_path", "")))
    if not before_path.exists() or not after_path.exists():
        return None

    try:
        before = Image.open(before_path).convert("RGB")
        after = Image.open(after_path).convert("RGB")
    except Exception as exc:
        log(f"Skipped screenshot pair composite: failed to load pair images: {exc}")
        return None

    target_h = min(before.height, after.height)
    if before.height != target_h:
        before = before.resize((int(before.width * target_h / before.height), target_h), Image.Resampling.LANCZOS)
    if after.height != target_h:
        after = after.resize((int(after.width * target_h / after.height), target_h), Image.Resampling.LANCZOS)

    header_h = 86
    gutter = 10
    width = before.width + after.width + gutter
    height = header_h + target_h
    canvas = Image.new("RGB", (width, height), (245, 247, 250))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    pair_prefix = str(record.get("pair_prefix") or "pair")
    title = f"{pair_prefix}  before -> after"
    subtitle = (
        f"before: {record.get('before_saved_at', '')}    "
        f"after: {record.get('after_saved_at', '')}"
    )
    draw.text((12, 12), title, fill=(20, 24, 31), font=font)
    draw.text((12, 34), subtitle, fill=(72, 79, 90), font=font)
    draw.text((12, 62), "before", fill=(20, 24, 31), font=font)
    draw.text((before.width + gutter + 12, 62), "after", fill=(20, 24, 31), font=font)
    canvas.paste(before, (0, header_h))
    canvas.paste(after, (before.width + gutter, header_h))

    composite_dir = output_dir / "comparisons"
    composite_dir.mkdir(parents=True, exist_ok=True)
    composite_path = composite_dir / f"{sanitize_file_component(pair_prefix)}_comparison.png"
    try:
        canvas.save(composite_path)
    except Exception as exc:
        log(f"Skipped screenshot pair composite: failed to save image: {exc}")
        return None
    return composite_path


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


def draw_stats_counts(ctx: RunContext) -> Dict[str, Any]:
    counts = ctx.runtime_values.get(DRAW_STATS_COUNTS_KEY)
    if not isinstance(counts, dict):
        counts = {"draw_started": 0, "target_seen": 0, "target_hit": 0, "role_hit_counts": {}}
        ctx.runtime_values[DRAW_STATS_COUNTS_KEY] = counts
    counts.setdefault("draw_started", 0)
    counts.setdefault("target_seen", 0)
    counts.setdefault("target_hit", 0)
    if not isinstance(counts.get("role_hit_counts"), dict):
        counts["role_hit_counts"] = {}
    return counts


def red_role_notes(action: Dict[str, Any]) -> Dict[str, str]:
    notes = dict(DEFAULT_RED_ROLE_NOTES)
    raw_notes = action.get("role_notes")
    if isinstance(raw_notes, dict):
        for key, value in raw_notes.items():
            name = Path(str(key)).name
            note = str(value).strip()
            if name and note:
                notes[name] = note
    return notes


def role_note_for_template(template_name: str, action: Dict[str, Any]) -> str:
    name = Path(str(template_name)).name
    return red_role_notes(action).get(name, "")


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


def record_draw_stats_event(
    ctx: RunContext,
    action: Dict[str, Any],
    event: str,
    draw_type: str,
    matched_template: str = "",
    matched_center: Optional[Dict[str, Optional[int]]] = None,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> None:
    session_id = screenshot_session_id(ctx)
    output_dir = draw_stats_output_dir(ctx, action)
    counts = draw_stats_counts(ctx)
    with ctx.stats_lock:
        if event == "draw_started":
            counts["draw_started"] += 1
        elif event == "target_seen":
            counts["target_seen"] += 1
        elif event == "target_hit":
            counts["target_hit"] += 1
            if matched_template:
                role_hit_counts = counts.setdefault("role_hit_counts", {})
                if isinstance(role_hit_counts, dict):
                    role_hit_counts[matched_template] = int(role_hit_counts.get(matched_template, 0)) + 1

        now = time.strftime("%Y-%m-%d %H:%M:%S")
        matched_role_note = role_note_for_template(matched_template, action) if matched_template else ""
        event_record: Dict[str, Any] = {
            "timestamp": now,
            "session_id": session_id,
            "event": event,
            "draw_type": draw_type,
            "matched_template": matched_template,
            "matched_role_note": matched_role_note,
            "matched_center": matched_center or {"x": None, "y": None},
            "draw_started_count": int(counts["draw_started"]),
            "target_seen_count": int(counts["target_seen"]),
            "target_hit_count": int(counts["target_hit"]),
        }
        if extra_fields:
            event_record.update(extra_fields)
        append_draw_stats_event(output_dir, session_id, event_record)

        summary = {
            "session_id": session_id,
            "updated_at": now,
            "draw_started_count": int(counts["draw_started"]),
            "target_seen_count": int(counts["target_seen"]),
            "target_hit_count": int(counts["target_hit"]),
            "latest_event": event,
            "latest_draw_type": draw_type,
            "latest_matched_template": matched_template,
            "latest_matched_role_note": matched_role_note,
            "role_hit_counts": counts.get("role_hit_counts", {}),
            "role_notes": red_role_notes(action),
            "events_path": str(output_dir / f"{session_id}{DRAW_STATS_EVENTS_JSONL_SUFFIX}"),
        }
        if extra_fields:
            summary.update({f"latest_{key}": value for key, value in extra_fields.items()})
        write_draw_stats_summary(output_dir, summary)


def do_record_draw_event(ctx: RunContext, action: Dict[str, Any]) -> None:
    event = str(action.get("event", "")).strip().lower()
    if event not in {"draw_started", "target_hit"}:
        raise BotError("record_draw_event 'event' must be 'draw_started' or 'target_hit'")

    match_info = current_if_image_match(ctx) or {}
    draw_type = str(action.get("draw_type", "")).strip().lower()
    matched_template = str(match_info.get("template_name", ""))
    matched_center = {
        "x": int(match_info.get("center_x", 0)) if "center_x" in match_info else None,
        "y": int(match_info.get("center_y", 0)) if "center_y" in match_info else None,
    }
    record_draw_stats_event(ctx, action, event, draw_type, matched_template, matched_center)
    counts = draw_stats_counts(ctx)
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
        path = RUNTIME_DATA_ROOT / "diagnostics" / "image_match_debug"
    path.mkdir(parents=True, exist_ok=True)
    return path


def red_light_debug_dir(ctx: RunContext, action: Dict[str, Any]) -> Path:
    raw_dir = str(action.get("debug_dir", "")).strip()
    if raw_dir:
        path = resolve_action_file_path(ctx, raw_dir)
    else:
        path = DEFAULT_RED_LIGHT_DEBUG_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_red_light_regions(ctx: RunContext, action: Dict[str, Any], image_size: Tuple[int, int]) -> List[Dict[str, int]]:
    raw_regions = action.get("regions", DEFAULT_RED_LIGHT_REGIONS)
    if not isinstance(raw_regions, list) or not raw_regions:
        raise BotError("if_red_light 'regions' must be a non-empty array")

    regions: List[Dict[str, int]] = []
    for index, raw_region in enumerate(raw_regions):
        if not isinstance(raw_region, dict):
            raise BotError("if_red_light each region must be an object")
        resolved = map_logical_region(
            ctx,
            raw_region,
            image_size,
            name="if_red_light region",
        )
        regions.append({
            "name": str(raw_region.get("name") or f"region_{index + 1}"),
            **resolved,
        })
    return regions


def resolve_red_light_part(
    ctx: RunContext,
    raw_part: Dict[str, Any],
    image_size: Tuple[int, int],
    name: str,
) -> Dict[str, int]:
    return map_logical_region(ctx, raw_part, image_size, name=f"if_red_light slot part '{name}'")


def resolve_red_light_slots(ctx: RunContext, action: Dict[str, Any], image_size: Tuple[int, int]) -> List[Dict[str, Any]]:
    raw_slots = action.get("slots", DEFAULT_RED_LIGHT_SLOTS)
    if not isinstance(raw_slots, list) or not raw_slots:
        raise BotError("if_red_light 'slots' must be a non-empty array")

    slots: List[Dict[str, Any]] = []
    for index, raw_slot in enumerate(raw_slots):
        if not isinstance(raw_slot, dict):
            raise BotError("if_red_light each slot must be an object")
        name = str(raw_slot.get("name") or f"slot_{index + 1}")
        slot = resolve_red_light_part(ctx, raw_slot, image_size, name)
        for part_name in ["card", "base", "beam"]:
            raw_part = raw_slot.get(part_name)
            if not isinstance(raw_part, dict):
                raise BotError(f"if_red_light slot '{name}' requires '{part_name}' object")
            slot[part_name] = resolve_red_light_part(ctx, raw_part, image_size, f"{name}.{part_name}")
        slot["name"] = name
        slots.append(slot)
    return slots


def red_pixel_mask(crop: np.ndarray, action: Dict[str, Any]) -> np.ndarray:
    r = crop[:, :, 0]
    g = crop[:, :, 1]
    b = crop[:, :, 2]
    max_channel = crop.max(axis=2)
    min_channel = crop.min(axis=2)
    saturation = (max_channel - min_channel) / np.maximum(max_channel, 1.0)
    min_red = float(action.get("min_red", 130))
    dominance_rg = float(action.get("dominance_rg", 1.7))
    dominance_rb = float(action.get("dominance_rb", 1.35))
    min_saturation = float(action.get("min_saturation", 0.45))
    return (
        (r >= min_red)
        & (r >= g * dominance_rg)
        & (r >= b * dominance_rb)
        & (saturation >= min_saturation)
    )


def red_part_ratio(rgb: np.ndarray, part: Dict[str, int], action: Dict[str, Any]) -> Tuple[float, int, int]:
    x = int(part["x"])
    y = int(part["y"])
    w = int(part["width"])
    h = int(part["height"])
    crop = rgb[y : y + h, x : x + w]
    mask = red_pixel_mask(crop, action)
    red_pixels = int(mask.sum())
    area = int(mask.size)
    return float(red_pixels / max(area, 1)), red_pixels, area


def analyze_red_light_regions(
    ctx: RunContext,
    screenshot: Image.Image,
    action: Dict[str, Any],
) -> Tuple[bool, List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    rgb = np.asarray(screenshot.convert("RGB"), dtype=np.float32)
    slots = resolve_red_light_slots(ctx, action, screenshot.size)
    base_threshold = float(action.get("base_ratio_threshold", 0.22))
    beam_threshold = float(action.get("beam_ratio_threshold", 0.20))
    card_threshold = float(action.get("card_ratio_threshold", 0.18))
    require_card = bool_value(action.get("require_card"), default=True)

    results: List[Dict[str, Any]] = []
    best: Optional[Dict[str, Any]] = None
    for slot in slots:
        card_ratio, card_pixels, card_area = red_part_ratio(rgb, slot["card"], action)
        base_ratio, base_pixels, base_area = red_part_ratio(rgb, slot["base"], action)
        beam_ratio, beam_pixels, beam_area = red_part_ratio(rgb, slot["beam"], action)
        matched = (
            base_ratio >= base_threshold
            and beam_ratio >= beam_threshold
            and (not require_card or card_ratio >= card_threshold)
        )
        ratio = min(base_ratio / max(base_threshold, 1e-6), beam_ratio / max(beam_threshold, 1e-6))
        item = {
            **{key: slot[key] for key in ["name", "x", "y", "width", "height"]},
            "ratio": ratio,
            "card_ratio": card_ratio,
            "card_red_pixels": card_pixels,
            "card_area": card_area,
            "base_ratio": base_ratio,
            "base_red_pixels": base_pixels,
            "base_area": base_area,
            "beam_ratio": beam_ratio,
            "beam_red_pixels": beam_pixels,
            "beam_area": beam_area,
            "matched": matched,
        }
        results.append(item)
        if matched and (best is None or not bool(best["matched"]) or ratio > float(best["ratio"])):
            best = item
        elif best is None or (not bool(best["matched"]) and ratio > float(best["ratio"])):
            best = item

    return any(bool(item["matched"]) for item in results), results, best


def red_light_matched_slot_names(results: List[Dict[str, Any]]) -> set:
    return {
        str(item.get("name", ""))
        for item in results
        if bool(item.get("matched")) and str(item.get("name", ""))
    }


def best_red_light_result(
    results: List[Dict[str, Any]],
    allowed_slots: Optional[set] = None,
) -> Optional[Dict[str, Any]]:
    candidates = [
        item
        for item in results
        if allowed_slots is None or str(item.get("name", "")) in allowed_slots
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: float(item.get("ratio", 0.0)))


def save_red_light_debug_assets(
    ctx: RunContext,
    screenshot: Image.Image,
    action: Dict[str, Any],
    matched: bool,
    results: List[Dict[str, Any]],
    best: Optional[Dict[str, Any]],
) -> Dict[str, Path]:
    debug_dir = red_light_debug_dir(ctx, action)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int((time.time() % 1) * 1000):03d}"
    status = "matched" if matched else "not_matched"
    overlay_path = debug_dir / f"{stamp}_{status}_overlay.png"
    meta_path = debug_dir / f"{stamp}_{status}_meta.json"

    overlay = screenshot.convert("RGB").copy()
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default()
    for item in results:
        x = int(item["x"])
        y = int(item["y"])
        right = x + int(item["width"])
        bottom = y + int(item["height"])
        color = (255, 64, 64) if item["matched"] else (80, 160, 255)
        draw.rectangle((x, y, right, bottom), outline=color, width=4)
        label = f"{item['name']} red={item['ratio']:.3f}"
        draw.rectangle((x, max(0, y - 22), x + 132, y), fill=(0, 0, 0))
        draw.text((x + 4, max(0, y - 18)), label, fill=(255, 255, 255), font=font)

    overlay.save(overlay_path)
    metadata = {
        "matched": matched,
        "threshold": {
            "base_ratio_threshold": float(action.get("base_ratio_threshold", 0.22)),
            "beam_ratio_threshold": float(action.get("beam_ratio_threshold", 0.20)),
            "card_ratio_threshold": float(action.get("card_ratio_threshold", 0.18)),
            "require_card": bool_value(action.get("require_card"), default=True),
            "min_red": float(action.get("min_red", 130)),
            "dominance_rg": float(action.get("dominance_rg", 1.7)),
            "dominance_rb": float(action.get("dominance_rb", 1.35)),
            "min_saturation": float(action.get("min_saturation", 0.45)),
        },
        "best": best,
        "regions": results,
        "overlay_path": str(overlay_path),
    }
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"overlay": overlay_path, "meta": meta_path}


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
        try:
            resolved = (
                clamp_device_region(region, screenshot.size, name="image search region")
                if bool_value(action.get("region_is_device_coordinates"))
                else map_logical_region(ctx, region, screenshot.size, name="image search region")
            )
            x = resolved["x"]
            y = resolved["y"]
            draw.rectangle(
                (x, y, x + resolved["width"], y + resolved["height"]),
                outline=(80, 140, 255),
                width=3,
            )
        except BotError:
            pass

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


def resolve_search_region(ctx: RunContext, action: Dict[str, Any], image_size: Tuple[int, int]) -> Tuple[int, int, int, int]:
    width, height = image_size
    region = action.get("region")
    if not isinstance(region, dict):
        return (0, 0, width, height)
    if bool_value(action.get("region_is_device_coordinates")):
        resolved = clamp_device_region(region, image_size, name="image search region")
    else:
        resolved = map_logical_region(
            ctx,
            region,
            image_size,
            name="image search region",
            default_width=ctx.src_screen_w,
            default_height=ctx.src_screen_h,
        )
    x = resolved["x"]
    y = resolved["y"]
    return (x, y, x + resolved["width"], y + resolved["height"])


def required_template_search_size(
    ctx: RunContext,
    template_paths: List[Path],
    action: Dict[str, Any],
) -> Tuple[int, int]:
    scale_x, scale_y = image_scale_factors(ctx, (ctx.dst_screen_w, ctx.dst_screen_h))
    maximum_scale = max(build_template_scale_factors(action), default=1.0)
    required_w = 1
    required_h = 1
    for template_path in template_paths:
        template_w, template_h = load_template_image(template_path).size
        required_w = max(required_w, int(np.ceil(template_w * scale_x * maximum_scale)))
        required_h = max(required_h, int(np.ceil(template_h * scale_y * maximum_scale)))
    return required_w, required_h


def expand_search_crop(
    crop_box: Tuple[int, int, int, int],
    minimum_size: Tuple[int, int],
    image_size: Tuple[int, int],
) -> Tuple[int, int, int, int]:
    left, top, right, bottom = crop_box
    image_w, image_h = image_size
    required_w, required_h = minimum_size
    target_w = min(image_w, max(right - left, required_w))
    target_h = min(image_h, max(bottom - top, required_h))
    center_x = (left + right) / 2.0
    center_y = (top + bottom) / 2.0
    new_left = max(0, min(image_w - target_w, int(round(center_x - target_w / 2.0))))
    new_top = max(0, min(image_h - target_h, int(round(center_y - target_h / 2.0))))
    return (new_left, new_top, new_left + target_w, new_top + target_h)


def prepare_search_image(
    ctx: RunContext,
    screenshot: Image.Image,
    action: Dict[str, Any],
    minimum_size: Optional[Tuple[int, int]] = None,
) -> PreparedSearchImage:
    crop_box = resolve_search_region(ctx, action, screenshot.size)
    if minimum_size is not None:
        crop_box = expand_search_crop(crop_box, minimum_size, screenshot.size)
    screen_size_changed = (ctx.src_screen_w, ctx.src_screen_h) != (ctx.dst_screen_w, ctx.dst_screen_h)
    default_padding_ratio = 0.08 if screen_size_changed else 0.0
    padding_ratio = max(0.0, float(action.get("search_padding_ratio", default_padding_ratio)))
    if padding_ratio > 0:
        crop_w = crop_box[2] - crop_box[0]
        crop_h = crop_box[3] - crop_box[1]
        padded_size = (
            int(np.ceil(crop_w * (1.0 + padding_ratio * 2.0))),
            int(np.ceil(crop_h * (1.0 + padding_ratio * 2.0))),
        )
        crop_box = expand_search_crop(crop_box, padded_size, screenshot.size)
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
    ctx: RunContext,
    template: Image.Image,
    action: Dict[str, Any],
    search_size: Tuple[int, int],
) -> List[Tuple[float, Image.Image]]:
    search_w, search_h = search_size
    base_w, base_h = template.size
    scale_x, scale_y = image_scale_factors(ctx, (ctx.dst_screen_w, ctx.dst_screen_h))
    resample = Image.Resampling.LANCZOS
    variants: List[Tuple[float, Image.Image]] = []
    seen_sizes: set[Tuple[int, int]] = set()
    coordinate_variants = [(scale_x, scale_y)]
    if not (abs(scale_x - 1.0) < 1e-6 and abs(scale_y - 1.0) < 1e-6):
        # Some user-added templates were captured on the current device rather
        # than the plan's logical reference screen. Keep that native-size option.
        coordinate_variants.append((1.0, 1.0))
    for scale in build_template_scale_factors(action):
        for coordinate_scale_x, coordinate_scale_y in coordinate_variants:
            target_w = max(1, int(round(base_w * coordinate_scale_x * scale)))
            target_h = max(1, int(round(base_h * coordinate_scale_y * scale)))
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
    ctx: RunContext,
    template_path: Path,
    action: Dict[str, Any],
    search_size: Tuple[int, int],
) -> List[PreparedTemplateVariant]:
    cache_identity = template_cache_identity(template_path)
    scale_key = tuple(build_template_scale_factors(action))
    template_scale_x, template_scale_y = image_scale_factors(ctx, (ctx.dst_screen_w, ctx.dst_screen_h))
    cache_key = (
        cache_identity[0],
        cache_identity[1],
        cache_identity[2],
        search_size[0],
        search_size[1],
        scale_key,
        round(template_scale_x, 6),
        round(template_scale_y, 6),
    )
    cached = _TEMPLATE_VARIANT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    template = load_template_image(template_path)
    variants = build_template_variants(ctx, template, action, search_size)
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
    search_action = image_search_action(action, template_paths)
    minimum_template_size = required_template_search_size(ctx, template_paths, search_action)
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
        search = prepare_search_image(ctx, screenshot, search_action, minimum_size=minimum_template_size)
        search_size = (search.gray.shape[1], search.gray.shape[0])
        matched_template_path: Optional[Path] = None
        matched_template_candidate: Optional[Dict[str, float]] = None

        template_total = len(template_paths)
        for template_index, template_path in enumerate(template_paths, start=1):
            prepared_variants = get_prepared_template_variants(ctx, template_path, search_action, search_size)
            match, candidate_best = find_image_match(
                search=search,
                template_variants=prepared_variants,
                threshold=threshold,
                index=index,
                action=search_action,
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
                "screenshot": screenshot.copy(),
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
    screenshot = result.get("screenshot")
    if isinstance(screenshot, Image.Image):
        ctx.runtime_values[LAST_IF_IMAGE_SCREENSHOT_KEY] = screenshot.copy()

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


def remember_if_red_light_match(
    ctx: RunContext,
    screenshot: Image.Image,
    result: Dict[str, Any],
) -> None:
    clear_if_image_match(ctx)
    if not result.get("matched"):
        return
    best = result.get("best")
    if not isinstance(best, dict):
        return
    ctx.runtime_values[LAST_IF_IMAGE_SCREENSHOT_KEY] = screenshot.copy()
    center_x = int(round(float(best.get("x", 0)) + float(best.get("width", 0)) / 2.0))
    center_y = int(round(float(best.get("y", 0)) + float(best.get("height", 0)) / 2.0))
    ctx.runtime_values[LAST_IF_IMAGE_MATCH_KEY] = {
        "template_name": f"red_light:{best.get('name', 'unknown')}",
        "template_path": "",
        "center_x": center_x,
        "center_y": center_y,
        "match_center_x": center_x,
        "match_center_y": center_y,
        "x": int(round(float(best.get("x", 0)))),
        "y": int(round(float(best.get("y", 0)))),
        "width": int(round(float(best.get("width", 0)))),
        "height": int(round(float(best.get("height", 0)))),
        "similarity": float(best.get("ratio", 0.0)),
    }


def execute_if_red_light(ctx: RunContext, action: Dict[str, Any]) -> None:
    clear_if_image_match(ctx)
    ctx.runtime_values.pop(LAST_RED_LIGHT_SCREENSHOT_KEY, None)
    then_actions = action.get("then_actions", [])
    else_actions = action.get("else_actions", [])
    if not isinstance(then_actions, list):
        raise BotError("if_red_light 'then_actions' must be an array")
    if not isinstance(else_actions, list):
        raise BotError("if_red_light 'else_actions' must be an array")

    if ctx.dry_run:
        log(
            with_action_remark(
                "[DRY-RUN] if_red_light checks red spotlight regions "
                f"base_threshold={float(action.get('base_ratio_threshold', 0.22)):.3f} "
                f"beam_threshold={float(action.get('beam_ratio_threshold', 0.20)):.3f}",
                action,
            )
        )
        return

    screenshot = capture_device_screenshot(ctx)
    matched, results, best = analyze_red_light_regions(ctx, screenshot, action)
    confirm_attempts = max(1, int(action.get("confirm_attempts", 1)))
    confirm_interval_sec = max(0.0, float(action.get("confirm_interval_sec", 0.15)))
    confirmed_slots = red_light_matched_slot_names(results)
    confirmation = {
        "attempts_required": confirm_attempts,
        "attempts_completed": 1,
        "interval_sec": confirm_interval_sec,
        "initial_slots": sorted(confirmed_slots),
        "confirmed_slots": sorted(confirmed_slots),
    }

    if matched and confirm_attempts > 1:
        for _ in range(1, confirm_attempts):
            if confirm_interval_sec > 0:
                time.sleep(confirm_interval_sec)
            check_stop(ctx)
            confirmation_screenshot = capture_device_screenshot(ctx)
            confirmation_matched, confirmation_results, _ = analyze_red_light_regions(
                ctx, confirmation_screenshot, action
            )
            confirmation["attempts_completed"] += 1
            current_slots = red_light_matched_slot_names(confirmation_results) if confirmation_matched else set()
            confirmed_slots &= current_slots
            confirmation["confirmed_slots"] = sorted(confirmed_slots)
            screenshot = confirmation_screenshot
            results = confirmation_results
            if not confirmed_slots:
                matched = False
                best = best_red_light_result(results)
                break

        if confirmed_slots:
            matched = True
            best = best_red_light_result(results, confirmed_slots)

    if matched:
        ctx.runtime_values[LAST_RED_LIGHT_SCREENSHOT_KEY] = screenshot.copy()

    save_debug = bool_value(action.get("save_debug", True), default=True)
    if save_debug:
        debug_paths = save_red_light_debug_assets(ctx, screenshot, action, matched, results, best)
        log(f"Red light debug overlay: {debug_paths['overlay']}")
        log(f"Red light debug meta: {debug_paths['meta']}")

    best_label = "none"
    if best is not None:
        best_label = (
            f"{best['name']} score={float(best['ratio']):.3f} "
            f"base={float(best['base_ratio']):.3f} beam={float(best['beam_ratio']):.3f} "
            f"card={float(best['card_ratio']):.3f}"
        )
    summary = ", ".join(
        f"{item['name']}[base={float(item['base_ratio']):.3f},beam={float(item['beam_ratio']):.3f},card={float(item['card_ratio']):.3f}]"
        for item in results
    )
    result = {
        "matched": matched,
        "regions": results,
        "best": best,
        "confirmation": confirmation,
    }
    remember_if_red_light_match(ctx, screenshot, result)
    if matched:
        record_draw_stats_event(
            ctx,
            action,
            "target_seen",
            "target_red_card",
            matched_template=f"red_light:{best.get('name', 'unknown')}" if isinstance(best, dict) else "red_light",
            matched_center={
                "x": int(round(float(best.get("x", 0)) + float(best.get("width", 0)) / 2.0)) if isinstance(best, dict) else None,
                "y": int(round(float(best.get("y", 0)) + float(best.get("height", 0)) / 2.0)) if isinstance(best, dict) else None,
            },
            extra_fields={
                "red_light_regions": results,
                "red_light_confirmation": confirmation,
            },
        )
        log(
            with_action_remark(
                f"if_red_light matched ({best_label}; confirmed_slots={confirmation['confirmed_slots']}; {summary}), "
                "execute then_actions",
                action,
            )
        )
        execute_actions(ctx, then_actions)
    else:
        log(
            with_action_remark(
                f"if_red_light did not match ({best_label}; confirmed_slots={confirmation['confirmed_slots']}; {summary}), "
                "execute else_actions",
                action,
            )
        )
        execute_actions(ctx, else_actions)


def red_role_template_paths(ctx: RunContext, action: Dict[str, Any]) -> List[Path]:
    raw_templates = action.get("role_templates", DEFAULT_RED_ROLE_TEMPLATES)
    if not isinstance(raw_templates, list) or not raw_templates:
        raise BotError("resolve_red_draw_result 'role_templates' must be a non-empty array")
    return [resolve_action_file_path(ctx, str(item)) for item in raw_templates]


def resolve_named_region(
    ctx: RunContext,
    raw_regions: Dict[str, Dict[str, int]],
    slot_name: str,
    image_size: Tuple[int, int],
) -> Dict[str, int]:
    raw = raw_regions.get(slot_name)
    if not isinstance(raw, dict):
        raise BotError(f"Missing region for slot '{slot_name}'")
    return resolve_red_light_part(ctx, raw, image_size, slot_name)


def match_red_role_in_slot(
    ctx: RunContext,
    before: Image.Image,
    action: Dict[str, Any],
    slot_name: str,
) -> Dict[str, Any]:
    region = resolve_named_region(ctx, DEFAULT_RED_ROLE_SEARCH_REGIONS, slot_name, before.size)
    template_paths = red_role_template_paths(ctx, action)
    search_action = {
        "region": region,
        "region_is_device_coordinates": True,
        "scales": action.get("role_scales", [0.9, 1.0]),
        "max_candidates": 1,
    }
    minimum_template_size = required_template_search_size(ctx, template_paths, search_action)
    search = prepare_search_image(ctx, before, search_action, minimum_size=minimum_template_size)
    search_size = (search.gray.shape[1], search.gray.shape[0])
    best_template: Optional[Path] = None
    best_match: Optional[Dict[str, float]] = None
    second_best_similarity = 0.0
    for template_path in template_paths:
        try:
            variants = get_prepared_template_variants(ctx, template_path, search_action, search_size)
        except BotError:
            continue
        _, candidate = find_image_match(search, variants, 0.0, 0, search_action)
        if candidate is None:
            continue
        candidate_similarity = float(candidate.get("similarity", 0.0))
        if best_match is None or candidate_similarity > float(best_match.get("similarity", 0.0)):
            if best_match is not None:
                second_best_similarity = float(best_match.get("similarity", 0.0))
            best_template = template_path
            best_match = candidate
        elif candidate_similarity > second_best_similarity:
            second_best_similarity = candidate_similarity

    similarity = float(best_match.get("similarity", 0.0)) if best_match is not None else 0.0
    role_thresholds = action.get("role_thresholds", {})
    if not isinstance(role_thresholds, dict):
        raise BotError("resolve_red_draw_result 'role_thresholds' must be an object")
    threshold = float(role_thresholds.get(best_template.name, action.get("role_threshold", 0.82))) if best_template else float(
        action.get("role_threshold", 0.82)
    )
    min_margin = float(action.get("role_min_margin", 0.0))
    confidence_margin = similarity - second_best_similarity
    role_name = (
        best_template.name
        if best_template is not None and similarity >= threshold and confidence_margin >= min_margin
        else "unknown_red_role"
    )
    center = {"x": None, "y": None}
    if best_match is not None:
        center = {
            "x": int(round(float(best_match.get("x", 0)) + float(best_match.get("width", 0)) / 2.0)),
            "y": int(round(float(best_match.get("y", 0)) + float(best_match.get("height", 0)) / 2.0)),
        }
    return {
        "slot": slot_name,
        "role": role_name,
        "similarity": similarity,
        "threshold": threshold,
        "second_best_similarity": second_best_similarity,
        "confidence_margin": confidence_margin,
        "center": center,
    }


def green_mask_components(mask: np.ndarray, min_pixels: int) -> List[Dict[str, int]]:
    height, width = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    components: List[Dict[str, int]] = []
    for y, x in zip(*np.where(mask)):
        if visited[y, x]:
            continue
        stack = [(int(y), int(x))]
        visited[y, x] = True
        pixels = 0
        left = right = int(x)
        top = bottom = int(y)
        while stack:
            current_y, current_x = stack.pop()
            pixels += 1
            left = min(left, current_x)
            right = max(right, current_x)
            top = min(top, current_y)
            bottom = max(bottom, current_y)
            for next_y, next_x in (
                (current_y - 1, current_x),
                (current_y + 1, current_x),
                (current_y, current_x - 1),
                (current_y, current_x + 1),
            ):
                if (
                    0 <= next_y < height
                    and 0 <= next_x < width
                    and mask[next_y, next_x]
                    and not visited[next_y, next_x]
                ):
                    visited[next_y, next_x] = True
                    stack.append((next_y, next_x))
        if pixels >= min_pixels:
            components.append(
                {
                    "pixels": pixels,
                    "x": left,
                    "y": top,
                    "width": right - left + 1,
                    "height": bottom - top + 1,
                }
            )
    return components


def green_check_score(ctx: RunContext, after: Image.Image, action: Dict[str, Any], slot_name: str) -> Dict[str, Any]:
    region = resolve_named_region(ctx, DEFAULT_GREEN_CHECK_REGIONS, slot_name, after.size)
    rgb = np.asarray(after.convert("RGB"), dtype=np.float32)
    x = int(region["x"])
    y = int(region["y"])
    w = int(region["width"])
    h = int(region["height"])
    crop = rgb[y : y + h, x : x + w]
    r = crop[:, :, 0]
    g = crop[:, :, 1]
    b = crop[:, :, 2]
    mask = (g > 120) & (g > r * 1.25) & (g > b * 1.2) & ((g - r) > 35) & ((g - b) > 30)
    scale_x, scale_y = image_scale_factors(ctx, after.size)
    min_scale = min(scale_x, scale_y)
    min_component_pixels = int(action.get("green_check_component_min_pixels", 800) * min_scale * min_scale)
    min_width = int(action.get("green_check_min_width", 50) * scale_x)
    max_width = int(action.get("green_check_max_width", 85) * scale_x)
    min_height = int(action.get("green_check_min_height", 30) * scale_y)
    max_height = int(action.get("green_check_max_height", 62) * scale_y)
    components = green_mask_components(mask, max(1, min_component_pixels))
    tick_components = [
        component
        for component in components
        if min_width <= component["width"] <= max_width
        and min_height <= component["height"] <= max_height
    ]
    best = max(tick_components, key=lambda component: component["pixels"]) if tick_components else None
    pixels = int(best["pixels"]) if best is not None else 0
    ratio = float(pixels / max(int(mask.size), 1))
    return {
        "slot": slot_name,
        "green_pixels": pixels,
        "green_ratio": ratio,
        "component": best,
    }


def detect_green_check_slot(ctx: RunContext, after: Image.Image, action: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    scores = [green_check_score(ctx, after, action, slot) for slot in ["left", "center", "right"]]
    scores.sort(key=lambda item: int(item["green_pixels"]), reverse=True)
    best = scores[0] if scores else None
    if best is None:
        return None
    min_pixels = int(action.get("green_check_min_pixels", 800))
    min_ratio = float(action.get("green_check_min_ratio", 0.006))
    if int(best["green_pixels"]) >= min_pixels and float(best["green_ratio"]) >= min_ratio:
        return best
    return None


def detect_cancel_in_after(ctx: RunContext, after: Image.Image, action: Dict[str, Any]) -> bool:
    raw_template = str(action.get("cancel_template", "../image_templates/role_cancel.png"))
    template_path = resolve_action_file_path(ctx, raw_template)
    search_action = {
        "region": action.get("cancel_region", {"x": 465, "y": 1542, "width": 148, "height": 72}),
        "scales": action.get("cancel_scales", [1.0]),
        "max_candidates": 1,
    }
    minimum_template_size = required_template_search_size(ctx, [template_path], search_action)
    search = prepare_search_image(ctx, after, search_action, minimum_size=minimum_template_size)
    search_size = (search.gray.shape[1], search.gray.shape[0])
    variants = get_prepared_template_variants(ctx, template_path, search_action, search_size)
    match, _ = find_image_match(
        search,
        variants,
        float(action.get("cancel_threshold", 0.85)),
        0,
        search_action,
    )
    return match is not None


def analyze_red_draw_result(ctx: RunContext, action: Dict[str, Any], pair_record: Dict[str, Any]) -> Dict[str, Any]:
    before_path = Path(str(pair_record.get("before_path", "")))
    after_path = Path(str(pair_record.get("after_path", "")))
    before = Image.open(before_path).convert("RGB")
    after = Image.open(after_path).convert("RGB")

    matched, red_results, _ = analyze_red_light_regions(ctx, before, action)
    candidates: List[Dict[str, Any]] = []
    for item in red_results:
        if bool(item.get("matched")):
            candidates.append(match_red_role_in_slot(ctx, before, action, str(item.get("name", ""))))

    green_slot = detect_green_check_slot(ctx, after, action)
    cancel_present = detect_cancel_in_after(ctx, after, action)
    pool_state = "stayed" if green_slot is not None or cancel_present else "refreshed"
    result = "miss"
    drawn_role = ""
    drawn_slot = str(green_slot.get("slot")) if green_slot is not None else ""
    hit_candidate: Optional[Dict[str, Any]] = None

    if pool_state == "stayed":
        if drawn_slot:
            for candidate in candidates:
                if candidate.get("slot") == drawn_slot:
                    hit_candidate = candidate
                    break
        if hit_candidate is not None:
            result = "hit"
            drawn_role = str(hit_candidate.get("role", "unknown_red_role"))
        else:
            result = "miss"
    elif candidates:
        if len(candidates) == 1:
            hit_candidate = candidates[0]
            result = "hit"
            drawn_role = str(hit_candidate.get("role", "unknown_red_role"))
            drawn_slot = str(hit_candidate.get("slot", ""))
        else:
            result = "hit_unknown"
            drawn_role = "unknown_red_role"

    return {
        "pair_prefix": str(pair_record.get("pair_prefix", "")),
        "before_path": str(before_path),
        "after_path": str(after_path),
        "red_light_matched": matched,
        "red_candidates": candidates,
        "pool_state": pool_state,
        "green_check": green_slot,
        "cancel_present": cancel_present,
        "drawn_slot": drawn_slot,
        "drawn_role": drawn_role,
        "result": result,
    }


def red_draw_result_worker(ctx: RunContext, action: Dict[str, Any], pair_record: Dict[str, Any]) -> None:
    try:
        result = analyze_red_draw_result(ctx, action, pair_record)
        event = "target_hit" if result["result"] in {"hit", "hit_unknown"} else "target_miss"
        matched_template = str(result.get("drawn_role") or "")
        matched_center = {"x": None, "y": None}
        for candidate in result.get("red_candidates", []):
            if candidate.get("slot") == result.get("drawn_slot"):
                center = candidate.get("center")
                if isinstance(center, dict):
                    matched_center = {
                        "x": center.get("x") if isinstance(center.get("x"), int) else None,
                        "y": center.get("y") if isinstance(center.get("y"), int) else None,
                    }
                break
        record_draw_stats_event(
            ctx,
            action,
            event,
            "red_draw_result",
            matched_template=matched_template,
            matched_center=matched_center,
            extra_fields={
                "red_draw_result": result["result"],
                "red_candidates": result["red_candidates"],
                "pool_state": result["pool_state"],
                "green_check": result["green_check"],
                "cancel_present": result["cancel_present"],
                "pair_prefix": result["pair_prefix"],
            },
        )
        log(
            with_action_remark(
                f"Resolved red draw result in background: result={result['result']} "
                f"drawn_role={matched_template or 'none'} pool_state={result['pool_state']} "
                f"candidates={[item.get('role') for item in result['red_candidates']]}",
                action,
            )
        )
    except Exception as exc:
        log(with_action_remark(f"resolve_red_draw_result background failed: {exc}", action))


def do_resolve_red_draw_result(ctx: RunContext, action: Dict[str, Any]) -> None:
    if ctx.dry_run:
        log(with_action_remark("[DRY-RUN] resolve_red_draw_result skipped", action))
        return
    pair_record = ctx.runtime_values.get(LAST_SCREENSHOT_PAIR_KEY)
    if not isinstance(pair_record, dict):
        log(with_action_remark("resolve_red_draw_result skipped: no latest screenshot pair", action))
        return
    thread = threading.Thread(
        target=red_draw_result_worker,
        args=(ctx, dict(action), dict(pair_record)),
        name=f"red-draw-result-{pair_record.get('pair_prefix', 'latest')}",
        daemon=True,
    )
    tasks = ctx.runtime_values.setdefault(BACKGROUND_TASKS_KEY, [])
    if isinstance(tasks, list):
        tasks.append(thread)
    thread.start()
    log(with_action_remark(f"resolve_red_draw_result scheduled for {pair_record.get('pair_prefix', 'latest')}", action))


def resolve_draw_state_target(
    ctx: RunContext,
    screenshot: Image.Image,
    parent_action: Dict[str, Any],
    target_config: Dict[str, Any],
    default_threshold: float,
) -> Tuple[Optional[Dict[str, float]], Optional[Dict[str, float]], Path]:
    target_action = dict(parent_action)
    target_action.update(target_config)
    template_paths = resolve_action_template_paths(ctx, target_action, "resolve_draw_state")
    search_action = image_search_action(target_action, template_paths)
    minimum_template_size = required_template_search_size(ctx, template_paths, search_action)
    search = prepare_search_image(ctx, screenshot, search_action, minimum_size=minimum_template_size)
    search_size = (search.gray.shape[1], search.gray.shape[0])
    threshold = float(target_action.get("threshold", default_threshold))
    best_match: Optional[Dict[str, float]] = None
    best_template_path = template_paths[0]
    for template_path in template_paths:
        prepared_variants = get_prepared_template_variants(ctx, template_path, search_action, search_size)
        match, candidate_best = find_image_match(
            search=search,
            template_variants=prepared_variants,
            threshold=threshold,
            index=int(target_action.get("index", 0)),
            action=search_action,
        )
        if candidate_best is not None and (
            best_match is None or candidate_best["similarity"] > best_match["similarity"]
        ):
            best_match = candidate_best
            best_template_path = template_path
        if match is not None:
            return match, candidate_best, template_path
    return None, best_match, best_template_path


def do_resolve_draw_state(ctx: RunContext, action: Dict[str, Any]) -> None:
    next_config = action.get("next")
    cancel_config = action.get("cancel")
    if not isinstance(next_config, dict) or not isinstance(cancel_config, dict):
        raise BotError("resolve_draw_state requires 'next' and 'cancel' objects")

    threshold = float(action.get("threshold", 0.88))
    timeout_sec = float(action.get("timeout_sec", 2.5))
    interval_sec = float(action.get("interval_sec", 0.2))
    settle_wait_sec = float(action.get("settle_wait_sec", 2.0))
    cancel_settle_wait_sec = float(action.get("cancel_settle_wait_sec", settle_wait_sec))
    fallback_actions = action.get("fallback_actions", [])
    if not isinstance(fallback_actions, list):
        raise BotError("resolve_draw_state 'fallback_actions' must be an array")

    if ctx.dry_run:
        log(
            with_action_remark(
                f"[DRY-RUN] resolve_draw_state timeout={timeout_sec:.1f}s interval={interval_sec:.1f}s",
                action,
            )
        )
        return

    deadline = time.time() + max(0.0, timeout_sec)
    attempt = 0
    best_next = 0.0
    best_cancel = 0.0
    while True:
        check_stop(ctx)
        attempt += 1
        screenshot = capture_device_screenshot(ctx)

        next_match, next_best, next_template = resolve_draw_state_target(
            ctx, screenshot, action, next_config, threshold
        )
        if next_best is not None:
            best_next = max(best_next, float(next_best.get("similarity", 0.0)))

        cancel_match, cancel_best, cancel_template = resolve_draw_state_target(
            ctx, screenshot, action, cancel_config, threshold
        )
        if cancel_best is not None:
            best_cancel = max(best_cancel, float(cancel_best.get("similarity", 0.0)))

        if next_match is not None:
            cancel_score = float(cancel_best.get("similarity", 0.0)) if cancel_best is not None else 0.0
            log(
                with_action_remark(
                    f"resolve_draw_state detected next round '{next_template.name}' on attempt {attempt} "
                    f"(next {next_match['similarity']:.3f}, cancel best {cancel_score:.3f})",
                    action,
                )
            )
            do_wait({"seconds": settle_wait_sec, "remark": "本次抽卡结束，等待动画结束后开启下一轮"})
            return

        if cancel_match is not None:
            next_score = float(next_best.get("similarity", 0.0)) if next_best is not None else 0.0
            center_x = int(round(cancel_match["x"] + cancel_match["width"] / 2.0))
            center_y = int(round(cancel_match["y"] + cancel_match["height"] / 2.0))
            log(
                with_action_remark(
                    f"resolve_draw_state detected cancel '{cancel_template.name}' on attempt {attempt} "
                    f"(cancel {cancel_match['similarity']:.3f}, next best {next_score:.3f}), click cancel",
                    action,
                )
            )
            x, y = tap_absolute(ctx, center_x, center_y)
            log(with_action_remark(f"Click draw-state cancel '{cancel_template.name}' at ({x}, {y})", action))
            do_wait({"seconds": cancel_settle_wait_sec, "remark": "本次抽卡结束，等待动画结束后开启下一轮"})
            return

        if time.time() >= deadline:
            log(
                with_action_remark(
                    f"resolve_draw_state timed out after {timeout_sec:.1f}s "
                    f"(best next {best_next:.3f}, best cancel {best_cancel:.3f}), execute fallback_actions",
                    action,
                )
            )
            execute_actions(ctx, fallback_actions)
            return
        time.sleep(max(0.1, interval_sec))


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
    elif action_type == "if_red_light":
        execute_if_red_light(ctx, action)
    elif action_type == "resolve_red_draw_result":
        do_resolve_red_draw_result(ctx, action)
    elif action_type == "resolve_draw_state":
        do_resolve_draw_state(ctx, action)
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


def wait_background_tasks(ctx: RunContext, timeout_sec: float = 2.0) -> None:
    tasks = ctx.runtime_values.get(BACKGROUND_TASKS_KEY)
    if not isinstance(tasks, list) or not tasks:
        return
    deadline = time.time() + max(0.0, timeout_sec)
    for thread in list(tasks):
        if not isinstance(thread, threading.Thread) or not thread.is_alive():
            continue
        remain = deadline - time.time()
        if remain <= 0:
            break
        thread.join(remain)
    alive = [thread.name for thread in tasks if isinstance(thread, threading.Thread) and thread.is_alive()]
    if alive:
        log(f"Background task(s) still running: {', '.join(alive)}")


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
    active_device = device
    last_text = ""
    recovered = False
    for attempt in range(5):
        cmd = [adb_path]
        if active_device:
            cmd += ["-s", active_device]
        cmd += ["shell", "wm", "size"]
        result = run_cmd(cmd, check=False)
        text = f"{result.stdout}\n{result.stderr}"
        last_text = text
        match = WM_SIZE_RE.search(text)
        if match:
            return int(match.group(1)), int(match.group(2))

        if adb_output_is_transient(text) and not recovered:
            log("ADB screen size probe failed, restart adb server and retry ...")
            recover_adb_server(adb_path, active_device)
            active_device = ensure_device_connected(adb_path, active_device)
            recovered = True
        elif attempt < 4:
            time.sleep(0.3)

    raise BotError(f"Cannot parse device screen size from adb output:\n{last_text.strip()}")


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
    wait_background_tasks(ctx)
    log("Bot exit")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BotError as exc:
        log(f"ERROR: {exc}")
        raise SystemExit(1)
