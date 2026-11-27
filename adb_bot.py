#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
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
import traceback
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def configure_console_encoding() -> None:
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


configure_console_encoding()

WM_SIZE_RE = re.compile(r"(\d+)x(\d+)")
ENV_REF_RE = re.compile(r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
LAST_IF_IMAGE_MATCH_KEY = "last_if_image_match"
LAST_IF_IMAGE_SCREENSHOT_KEY = "last_if_image_screenshot"
LAST_RED_LIGHT_SCREENSHOT_KEY = "last_red_light_screenshot"
LAST_SCREENSHOT_PAIR_KEY = "_latest_screenshot_pair"
BACKGROUND_TASKS_KEY = "_background_tasks"
CHEST_ANALYSIS_TASK_KEY = "_chest_analysis_task"
CHEST_ANALYSIS_QUEUE_KEY = "_chest_analysis_queue"
BG_CHECK_THREAD_KEY = "_bg_check_thread"
BG_CHECK_HIT_KEY = "_bg_check_hit"
BG_CHECK_STOP_KEY = "_bg_check_stop"


class BotError(RuntimeError):
    pass


class DeviceError(BotError):
    """Device/ADB/HDC-level failure; the bot may recover once the link is back."""


@dataclass
class RunContext:
    adb_path: str
    hdc_path: str
    device_backend: str
    device: Optional[str]
    user_id: str
    source_id: str
    source_name: str
    source_file: Optional[Path]
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
    image_match_debug: bool = False


@dataclass(frozen=True)
class PreparedSearchImage:
    crop_box: Tuple[int, int, int, int]
    rgb: np.ndarray
    gray: np.ndarray
    # Lazily filled correlation buffers shared by every template variant:
    # one search-side FFT pair plus float64 summed-area tables, all computed
    # at most once per screenshot regardless of how many variants run.
    gray_fft: Optional[np.ndarray] = None
    gray_fft_shape: Optional[Tuple[int, int]] = None
    gray_squared_fft: Optional[np.ndarray] = None
    integral_gray: Optional[np.ndarray] = None
    integral_gray_squared: Optional[np.ndarray] = None


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
    is_opaque: bool
    fft_shape: Optional[Tuple[int, int]] = None
    weights_fft: Optional[np.ndarray] = None
    centered_fft: Optional[np.ndarray] = None


_TEMPLATE_IMAGE_CACHE: Dict[Tuple[str, int, int], Image.Image] = {}
_TEMPLATE_VARIANT_CACHE: Dict[
    Tuple[str, int, int, int, int, Tuple[float, ...], float, float, bool],
    List[PreparedTemplateVariant],
] = {}
_TEMPLATE_RESOLUTION_VARIANT_CACHE: Dict[Tuple[str, int, int], List[Path]] = {}
FFT_CACHE_TOTAL_BUDGET_BYTES = 512 * 1024 * 1024
_FFT_CACHE_TOTAL_BYTES = 0
SCREENSHOT_SESSION_KEY = "_save_screenshot_session_id"
SCREENSHOT_COUNTERS_KEY = "_save_screenshot_pair_counters"
SCREENSHOT_ACTIVE_KEY = "_save_screenshot_active_pairs"


def runtime_data_root() -> Path:
    return Path(__file__).parent


RUNTIME_DATA_ROOT = runtime_data_root()
DEFAULT_RESULT_SCREENSHOT_DIR = RUNTIME_DATA_ROOT / "diagnostics" / "draw_result_pairs"
SCREENSHOT_INDEX_CSV = "index.csv"
SCREENSHOT_INDEX_JSONL = "index.jsonl"
DEFAULT_SCREEN_WIDTH = 1080
DEFAULT_SCREEN_HEIGHT = 1920
SCREENSHOT_INDEX_FIELDS = [
    "device",
    "user_id",
    "source_id",
    "source_name",
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


def persistent_runtime_root(ctx: RunContext) -> Path:
    """Return the durable runtime root when running as a one-file executable."""
    if getattr(sys, "frozen", False):
        return ctx.plan_dir.parent
    return RUNTIME_DATA_ROOT
HDC_DEVICE_SUFFIX = " [HarmonyOS/HDC]"
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
    if not bool_value(action.get("disable_contextual_cancel_template")) and any(path.name == "role_cancel.png" for path in template_paths):
        contextual_path = resolve_action_file_path(ctx, "../image_templates/role_cancel_phone.png")
        if contextual_path.exists() and contextual_path not in template_paths:
            template_paths.insert(0, contextual_path)
    if bool_value(action.get("disable_resolution_template_variants")):
        return template_paths
    return expand_resolution_template_paths(ctx, template_paths)


def strip_resolution_suffix(stem: str) -> str:
    match = re.match(r"^(.*)_\d+x\d+$", stem)
    if match:
        return match.group(1)
    return stem


def expand_resolution_template_paths(ctx: RunContext, template_paths: List[Path]) -> List[Path]:
    if ctx.dst_screen_w <= 0 or ctx.dst_screen_h <= 0:
        return template_paths

    cache_key = tuple(sorted(str(path) for path in template_paths)), ctx.dst_screen_w, ctx.dst_screen_h
    cached = _TEMPLATE_RESOLUTION_VARIANT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    expanded: List[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = str(path.expanduser().resolve())
        if key in seen:
            return
        seen.add(key)
        expanded.append(Path(key))

    for path in template_paths:
        resolved = path.expanduser().resolve()
        base_stem = strip_resolution_suffix(resolved.stem)
        exact_variant = resolved.with_name(f"{base_stem}_{ctx.dst_screen_w}x{ctx.dst_screen_h}{resolved.suffix}")
        if exact_variant.exists():
            add(exact_variant)
        else:
            wildcard = sorted(
                candidate
                for candidate in resolved.parent.glob(f"{base_stem}*{ctx.dst_screen_w}x{ctx.dst_screen_h}{resolved.suffix}")
                if candidate.is_file() and candidate != resolved
            )
            for candidate in wildcard:
                add(candidate)
        add(resolved)

    _TEMPLATE_RESOLUTION_VARIANT_CACHE[cache_key] = expanded
    return expanded


def image_search_action(action: Dict[str, Any], template_paths: List[Path]) -> Dict[str, Any]:
    """Add a wider default region for the contextual cancel-button lookup."""
    if bool_value(action.get("disable_contextual_cancel_template")) or not any(path.name == "role_cancel.png" for path in template_paths):
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


def current_if_text_match(ctx: RunContext) -> Optional[Dict[str, Any]]:
    raw_value = ctx.runtime_values.get("current_if_text_match")
    if isinstance(raw_value, dict):
        return raw_value
    return None


def clear_if_text_match(ctx: RunContext) -> None:
    ctx.runtime_values.pop("current_if_text_match", None)


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


def skip_magnifier_actions(actions: List[Any]) -> List[Any]:
    """Extract the confirmation flow after the magnifier gate for one-shot execution."""
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_type = str(action.get("type", "")).strip().lower()
        templates: List[str] = []
        raw_template = action.get("template")
        if isinstance(raw_template, str) and raw_template.strip():
            templates.append(raw_template)
        raw_templates = action.get("templates")
        if isinstance(raw_templates, list):
            templates.extend(item for item in raw_templates if isinstance(item, str) and item.strip())
        normalized_templates = {
            str(template).replace("\\", "/").rsplit("/", 1)[-1]
            for template in templates
        }
        if action_type == "if_image" and (
            "bl_baoxiaoxing.png" in normalized_templates
            or "bl_baoxiaoxing_1080x1920.png" in normalized_templates
        ):
            then_actions = action.get("then_actions")
            if isinstance(then_actions, list):
                confirmation = next(
                    (
                        nested
                        for nested in then_actions
                        if isinstance(nested, dict) and str(nested.get("type", "")).strip().lower() == "if_image"
                    ),
                    None,
                )
                if confirmation is not None:
                    return [dict(confirmation)]
        for key in ("actions", "then_actions", "else_actions"):
            nested = action.get(key)
            if isinstance(nested, list):
                extracted = skip_magnifier_actions(nested)
                if extracted:
                    return extracted
    return []


def current_chest_capture_actions(actions: List[Any]) -> List[Dict[str, Any]]:
    """Extract the standard chest result capture action for a one-off save."""
    for action in actions:
        if not isinstance(action, dict):
            continue
        if (
            str(action.get("type", "")).strip().lower() == "save_screenshot"
            and str(action.get("pair_key", "")).strip() == "chest_result"
        ):
            return [dict(action)]
        for key in ("actions", "then_actions", "else_actions"):
            nested = action.get(key)
            if isinstance(nested, list):
                capture_actions = current_chest_capture_actions(nested)
                if capture_actions:
                    return capture_actions
    return []


SPAWN_RETRY_ATTEMPTS = 3


def run_process_with_spawn_retry(cmd: List[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """subprocess.run that tolerates transient OS-level spawn failures.

    Windows antiviruses briefly lock freshly unpacked binaries while the plan
    is spawning adb.exe several times per second (WinError 32 "another program
    is using this file"); such locks usually clear within a few hundred ms.
    """
    last_error: Optional[OSError] = None
    for attempt in range(1, SPAWN_RETRY_ATTEMPTS + 1):
        try:
            return subprocess.run(cmd, **kwargs)
        except OSError as exc:
            last_error = exc
            if attempt < SPAWN_RETRY_ATTEMPTS:
                log(f"Command could not start: {exc}; retrying ({attempt}/{SPAWN_RETRY_ATTEMPTS - 1}) ...")
                time.sleep(0.3 * attempt)
    assert last_error is not None
    raise last_error


def run_cmd(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return run_process_with_spawn_retry(cmd, text=True, encoding="utf-8", errors="replace", capture_output=True, check=check)


def split_device_backend(raw_device: Optional[str], preferred_backend: str = "auto") -> Tuple[str, Optional[str]]:
    backend = preferred_backend if preferred_backend in {"adb", "hdc"} else "adb"
    device = raw_device.strip() if isinstance(raw_device, str) and raw_device.strip() else None
    if device and device.endswith(HDC_DEVICE_SUFFIX):
        return "hdc", device[: -len(HDC_DEVICE_SUFFIX)].strip() or None
    if device and device.startswith("hdc:"):
        return "hdc", device[4:].strip() or None
    return backend, device


def build_backend_cmd(ctx: RunContext, extra: List[str]) -> List[str]:
    if ctx.device_backend == "hdc":
        cmd = [ctx.hdc_path]
        if ctx.device:
            cmd += ["-t", ctx.device]
        cmd.extend(extra)
        return cmd
    return build_adb_cmd(ctx, extra)


def hdc_swipe_velocity(x1: str, y1: str, x2: str, y2: str, duration_ms: Optional[str]) -> str:
    if not duration_ms:
        return "600"
    try:
        dx = float(x2) - float(x1)
        dy = float(y2) - float(y1)
        duration = max(1.0, float(duration_ms))
        velocity = int(round(((dx * dx + dy * dy) ** 0.5) * 1000.0 / duration))
        return str(max(200, min(40000, velocity)))
    except ValueError:
        return "600"


def hdc_key_value(raw_key: str) -> str:
    key = raw_key.strip()
    lowered = key.lower()
    if lowered in {"keycode_back", "back"}:
        return "Back"
    if lowered in {"keycode_home", "home"}:
        return "Home"
    if lowered in {"keycode_power", "power"}:
        return "Power"
    return key


def hdc_ui_input_args(shell_args: List[str]) -> Optional[List[str]]:
    if not shell_args or shell_args[0] != "input":
        return None
    input_args = shell_args[1:]
    if input_args and input_args[0] in {"touchscreen", "touchpad", "mouse", "keyboard"}:
        input_args = input_args[1:]
    if len(input_args) >= 3 and input_args[0] in {"tap", "click"}:
        return ["uitest", "uiInput", "click", input_args[1], input_args[2]]
    if len(input_args) >= 3 and input_args[0] in {"doubleclick", "double_click", "double-tap", "doubletap"}:
        return ["uitest", "uiInput", "doubleClick", input_args[1], input_args[2]]
    if len(input_args) >= 3 and input_args[0] in {"longclick", "long_click", "longpress", "long_press"}:
        return ["uitest", "uiInput", "longClick", input_args[1], input_args[2]]
    if len(input_args) >= 5 and input_args[0] == "swipe":
        duration = input_args[5] if len(input_args) >= 6 else "300"
        return ["uinput", "-T", "-m", input_args[1], input_args[2], input_args[3], input_args[4], "-k", "0", duration]
    if len(input_args) >= 5 and input_args[0] == "drag":
        duration = input_args[5] if len(input_args) >= 6 else "600"
        return ["uinput", "-T", "-m", input_args[1], input_args[2], input_args[3], input_args[4], "-k", "500", duration]
    if len(input_args) >= 5 and input_args[0] == "fling":
        velocity = hdc_swipe_velocity(input_args[1], input_args[2], input_args[3], input_args[4], input_args[5] if len(input_args) >= 6 else None)
        return ["uitest", "uiInput", input_args[0], input_args[1], input_args[2], input_args[3], input_args[4], velocity]
    if len(input_args) >= 2 and input_args[0] in {"keyevent", "key"}:
        return ["uitest", "uiInput", "keyEvent", hdc_key_value(input_args[1])]
    if len(input_args) >= 2 and input_args[0] == "text":
        return ["uitest", "uiInput", "text", *input_args[1:]]
    if input_args and input_args[0] in {"back", "home"}:
        key = "Back" if input_args[0] == "back" else "Home"
        return ["uitest", "uiInput", "keyEvent", key]
    return None


def hdc_touch_move(
    ctx: RunContext,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    keep_ms: int = 0,
    smooth_ms: int = 1000,
) -> None:
    if ctx.device_backend != "hdc":
        raise BotError("hdc_touch_move requires the HDC backend")
    keep_ms = max(0, min(60000, int(keep_ms)))
    smooth_ms = max(0, min(15000, int(smooth_ms)))
    args = [
        "uinput",
        "-T",
        "-m",
        str(x1),
        str(y1),
        str(x2),
        str(y2),
        "-k",
        str(keep_ms),
        str(smooth_ms),
    ]
    pretty = " ".join(shlex.quote(p) for p in args)
    if ctx.dry_run:
        log(f"[DRY-RUN] hdc shell {pretty}")
        return
    log(f"CMD hdc shell {pretty}")
    result = run_cmd(build_backend_cmd(ctx, ["shell"] + args), check=False)
    if result.returncode != 0:
        text = (result.stderr or result.stdout or "").strip()
        raise DeviceError(f"HDC touch move failed: hdc shell {pretty}\n{text}")


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
    if ctx.device_backend == "hdc":
        hdc_args = hdc_ui_input_args(shell_args)
        if hdc_args is None:
            raise BotError(f"HDC backend only supports simple input actions for now: {pretty}")
        hdc_pretty = " ".join(shlex.quote(p) for p in hdc_args)
        if ctx.dry_run:
            log(f"[DRY-RUN] hdc shell {hdc_pretty}")
            return
        log(f"CMD hdc shell {hdc_pretty}")
        result = run_cmd(build_backend_cmd(ctx, ["shell"] + hdc_args), check=False)
        if result.returncode != 0:
            text = (result.stderr or result.stdout or "").strip()
            raise DeviceError(f"HDC shell failed: hdc shell {hdc_pretty}\n{text}")
        return
    if ctx.dry_run:
        log(f"[DRY-RUN] {ctx.device_backend} shell {pretty}")
        return
    log(f"CMD {ctx.device_backend} shell {pretty}")
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
                    raise DeviceError(
                        f"ADB shell failed after reconnect and fallback: adb shell {pretty}\n{retry_details}"
                    ) from retry_exc
                retry_details = retry_stderr or retry_stdout or str(retry_exc)
                raise DeviceError(f"ADB shell failed after reconnect: adb shell {pretty}\n{retry_details}") from retry_exc
        details = stderr or stdout or str(exc)
        raise DeviceError(f"ADB shell failed: adb shell {pretty}\n{details}") from exc


def adb_shell_script(ctx: RunContext, script: str, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    """Run a multi-line shell script on the device through a single adb session.

    Feeding the script on stdin avoids one adb round-trip per command, which is
    what makes timed trace replay feasible: `input motionevent` commands and
    shell-builtin waits then run back-to-back inside the guest shell.
    """
    if ctx.device_backend == "hdc":
        raise BotError("Shell scripts are only supported on the ADB backend.")
    line_count = len(script.splitlines())
    if ctx.dry_run:
        log(f"[DRY-RUN] {ctx.device_backend} shell sh -s ({line_count} lines)")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    log(f"CMD {ctx.device_backend} shell sh -s ({line_count} lines)")
    cmd = build_adb_cmd(ctx, ["shell", "sh", "-s"])
    try:
        result = subprocess.run(
            cmd,
            input=script,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise DeviceError(f"ADB shell script timed out after {timeout:.0f}s") from exc
    if result.returncode != 0:
        text = f"{result.stdout}\n{result.stderr}".strip()
        raise DeviceError(f"ADB shell script failed (exit {result.returncode}):\n{text}")
    return result


def adb_shell_result(ctx: RunContext, shell_args: List[str]) -> subprocess.CompletedProcess[str]:
    if ctx.dry_run:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    if ctx.device_backend == "hdc":
        hdc_args = hdc_ui_input_args(shell_args)
        if hdc_args is None:
            return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="unsupported hdc shell command")
        return run_cmd(build_backend_cmd(ctx, ["shell"] + hdc_args), check=False)
    cmd = build_adb_cmd(ctx, ["shell"] + shell_args)
    return run_cmd(cmd, check=False)


def adb_exec_out_result(ctx: RunContext, extra: List[str]) -> subprocess.CompletedProcess[bytes]:
    if ctx.dry_run:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
    if ctx.device_backend == "hdc":
        return hdc_screenshot_result(ctx.hdc_path, ctx.device)
    cmd = build_adb_cmd(ctx, ["exec-out"] + extra)
    return run_process_with_spawn_retry(cmd, capture_output=True, check=False)


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


def resolve_resolution_override(
    ctx: RunContext, action: Dict[str, Any]
) -> Optional[Tuple[int, int, str]]:
    """Return a device-pixel point for the current screen resolution, if configured.

    `resolution_overrides` lets a click carry coordinates for specific devices whose
    layout does not scale linearly from the plan's reference screen (for example a
    1080x1920 emulator plan used on a 1216x2688 phone). Entries are matched against
    the real device resolution and used as device pixels, without `screen_size` scaling.
    """
    raw_overrides = action.get("resolution_overrides")
    if raw_overrides is None:
        return None
    if not isinstance(raw_overrides, list):
        raise BotError("resolution_overrides must be an array")
    if ctx.dst_screen_w <= 0 or ctx.dst_screen_h <= 0:
        return None
    for index, entry in enumerate(raw_overrides):
        if not isinstance(entry, dict):
            raise BotError(f"resolution_overrides[{index}] must be an object")
        try:
            width = int(entry.get("width"))
            height = int(entry.get("height"))
            x = int(entry.get("x"))
            y = int(entry.get("y"))
        except (TypeError, ValueError):
            raise BotError(
                f"resolution_overrides[{index}] requires integer width, height, x and y"
            )
        if width <= 0 or height <= 0:
            raise BotError(f"resolution_overrides[{index}] width and height must be positive")
        if width == ctx.dst_screen_w and height == ctx.dst_screen_h:
            return (
                max(0, min(ctx.dst_screen_w - 1, x)),
                max(0, min(ctx.dst_screen_h - 1, y)),
                f"{width}x{height}",
            )
    return None


def clamp_input_point(ctx: RunContext, x: int, y: int) -> Tuple[int, int]:
    if ctx.dst_screen_w > 0 and ctx.dst_screen_h > 0:
        x = max(0, min(ctx.dst_screen_w - 1, x))
        y = max(0, min(ctx.dst_screen_h - 1, y))
    return x, y


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
    if ctx.device_backend == "hdc":
        ctx.motionevent_supported = False
        return False
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


def resolve_click_point(ctx: RunContext, action: Dict[str, Any]) -> Tuple[int, int, str]:
    override = resolve_resolution_override(ctx, action)
    if override is not None:
        x, y, label = override
        return x, y, f" [resolution {label}]"
    x = int(action["x"])
    y = int(action["y"])
    x, y = map_input_point(ctx, x, y)
    return x, y, ""


def do_click(ctx: RunContext, action: Dict[str, Any]) -> None:
    x, y, resolution_note = resolve_click_point(ctx, action)
    x = apply_jitter(x, ctx.jitter)
    y = apply_jitter(y, ctx.jitter)
    duration_ms = int(action.get("duration_ms", 0) or 0)
    if duration_ms >= 150 and ctx.device_backend != "hdc":
        # Preserve a recorded slow tap: a plain `input tap` releases almost
        # immediately, which some game buttons distinguish from a short hold.
        adb_shell(ctx, ["input", "swipe", str(x), str(y), str(x), str(y), str(duration_ms)])
        log(with_action_remark(f"Click ({x}, {y}){resolution_note}, hold {duration_ms}ms", action))
        return
    adb_shell(ctx, ["input", "tap", str(x), str(y)])
    log(with_action_remark(f"Click ({x}, {y}){resolution_note}", action))


def do_long_press(ctx: RunContext, action: Dict[str, Any]) -> None:
    x, y, resolution_note = resolve_click_point(ctx, action)
    duration_ms = max(1, int(action.get("duration_ms", 700) or 700))
    x = apply_jitter(x, ctx.jitter)
    y = apply_jitter(y, ctx.jitter)
    if ctx.device_backend == "hdc":
        adb_shell(ctx, ["input", "longclick", str(x), str(y)])
    else:
        # Zero-length swipe = press, hold, release at the same coordinate.
        adb_shell(ctx, ["input", "swipe", str(x), str(y), str(x), str(y), str(duration_ms)])
    log(with_action_remark(f"Long press ({x}, {y}){resolution_note}, {duration_ms}ms", action))


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


def do_input(ctx: RunContext, action: Dict[str, Any]) -> None:
    command = str(action.get("command") or action.get("name") or "").strip().lower()
    raw_args = action.get("args", [])
    if isinstance(raw_args, list):
        input_args = [str(item) for item in raw_args]
    elif raw_args in (None, ""):
        input_args = []
    else:
        input_args = [str(raw_args)]

    if command in {"tap", "click"}:
        x = int(action.get("x", input_args[0] if len(input_args) > 0 else 0))
        y = int(action.get("y", input_args[1] if len(input_args) > 1 else 0))
        x, y = map_input_point(ctx, x, y)
        x = apply_jitter(x, ctx.jitter)
        y = apply_jitter(y, ctx.jitter)
        adb_shell(ctx, ["input", "tap", str(x), str(y)])
        log(with_action_remark(f"Input tap ({x}, {y})", action))
        return

    if command in {"doubleclick", "double_click", "double-tap", "doubletap"}:
        x = int(action.get("x", input_args[0] if len(input_args) > 0 else 0))
        y = int(action.get("y", input_args[1] if len(input_args) > 1 else 0))
        x, y = map_input_point(ctx, x, y)
        x = apply_jitter(x, ctx.jitter)
        y = apply_jitter(y, ctx.jitter)
        if ctx.device_backend == "hdc":
            adb_shell(ctx, ["input", "doubleclick", str(x), str(y)])
        else:
            adb_shell(ctx, ["input", "tap", str(x), str(y)])
            time.sleep(0.08)
            adb_shell(ctx, ["input", "tap", str(x), str(y)])
        log(with_action_remark(f"Input double click ({x}, {y})", action))
        return

    if command in {"longclick", "long_click", "longpress", "long_press"}:
        x = int(action.get("x", input_args[0] if len(input_args) > 0 else 0))
        y = int(action.get("y", input_args[1] if len(input_args) > 1 else 0))
        duration = int(action.get("duration_ms", input_args[2] if len(input_args) > 2 else 800))
        x, y = map_input_point(ctx, x, y)
        x = apply_jitter(x, ctx.jitter)
        y = apply_jitter(y, ctx.jitter)
        if ctx.device_backend == "hdc":
            adb_shell(ctx, ["input", "longclick", str(x), str(y)])
        else:
            adb_shell(ctx, ["input", "swipe", str(x), str(y), str(x), str(y), str(duration)])
        log(with_action_remark(f"Input long click ({x}, {y})", action))
        return

    if command == "swipe":
        do_swipe(ctx, action)
        return

    if command in {"drag", "fling"}:
        x1, y1 = map_input_point(ctx, int(action["x1"]), int(action["y1"]))
        x2, y2 = map_input_point(ctx, int(action["x2"]), int(action["y2"]))
        duration = int(action.get("duration_ms", input_args[4] if len(input_args) > 4 else 450))
        if ctx.device_backend != "hdc":
            adb_shell(ctx, ["input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)])
        elif command == "drag":
            press_ms = int(action.get("press_ms", action.get("press_time_ms", 500)))
            total_ms = max(duration, press_ms + 500)
            smooth_ms = max(1, total_ms - press_ms)
            hdc_touch_move(ctx, x1, y1, x2, y2, keep_ms=press_ms, smooth_ms=smooth_ms)
        else:
            adb_shell(ctx, ["input", command, str(x1), str(y1), str(x2), str(y2), str(duration)])
        log(with_action_remark(f"Input {command} ({x1}, {y1}) -> ({x2}, {y2})", action))
        return

    if command in {"keyevent", "key"}:
        key = str(action.get("key") or (input_args[0] if input_args else "")).strip()
        if not key:
            raise BotError("input keyevent requires 'key' or args[0]")
        adb_shell(ctx, ["input", "keyevent", key])
        log(with_action_remark(f"Input keyevent {key}", action))
        return

    if command in {"back", "home"}:
        adb_shell(ctx, ["input", command])
        log(with_action_remark(f"Input {command}", action))
        return

    if command == "text":
        text = str(action.get("text") or (input_args[0] if input_args else ""))
        adb_shell(ctx, ["input", "text", text])
        log(with_action_remark("Input text", action))
        return

    raise BotError("input action requires command: tap/click/doubleclick/longclick/swipe/drag/fling/keyevent/back/home/text")


def do_click_match(ctx: RunContext, action: Dict[str, Any]) -> None:
    if ctx.dry_run:
        log(with_action_remark("[DRY-RUN] click_match uses the latest if_image/if_text target", action))
        return

    match_info = current_if_image_match(ctx) or current_if_text_match(ctx)
    if match_info is None:
        raise BotError("click_match requires a previous matched if_image or if_text in the current runtime context")

    x = int(match_info.get("center_x", 0)) + int(action.get("offset_x", 0))
    y = int(match_info.get("center_y", 0)) + int(action.get("offset_y", 0))
    x, y = tap_absolute(ctx, x, y)
    template_name = str(match_info.get("template_name") or match_info.get("text") or "unknown")
    log(with_action_remark(f"Click match '{template_name}' at ({x}, {y})", action))


RAW_SCREENCAP_ENV = "BS_BOT_RAW_SCREENCAP"
# android.graphics.PixelFormat values emitted by `screencap` raw output.
_RAW_FORMAT_RGBA_8888 = 1
_RAW_FORMAT_RGBX_8888 = 2
_RAW_FORMAT_BGRA_8888 = 5


def raw_screencap_enabled() -> bool:
    return os.environ.get(RAW_SCREENCAP_ENV, "1").strip().lower() not in {"0", "false", "off", "no"}


def parse_raw_screencap(data: bytes) -> Optional[Image.Image]:
    """Decode `adb exec-out screencap` raw RGBA frames, or None if malformed."""
    if len(data) < 16:
        return None
    width = int.from_bytes(data[0:4], "little")
    height = int.from_bytes(data[4:8], "little")
    pixel_format = int.from_bytes(data[8:12], "little")
    if width <= 0 or height <= 0 or width > 8192 or height > 16384:
        return None
    pixel_count = width * height
    raw_size = pixel_count * 4
    # Newer Android builds append a colorspace word to the 12-byte header.
    if len(data) == raw_size + 16:
        header_size = 16
    elif len(data) == raw_size + 12:
        header_size = 12
    else:
        return None
    if pixel_format not in (_RAW_FORMAT_RGBA_8888, _RAW_FORMAT_RGBX_8888, _RAW_FORMAT_BGRA_8888):
        return None
    if pixel_format != _RAW_FORMAT_RGBX_8888:
        # screencap alpha is always opaque; a violation means the payload is
        # not the raw frame we expect (e.g. interleaved shell output).
        alpha = np.frombuffer(data, dtype=np.uint8, count=pixel_count, offset=header_size + 3)[::4]
        if not bool((alpha == 255).all()):
            return None
    # PIL's raw decoder does the 4->3 byte channel pick in one C pass.
    # frombytes (not frombuffer) so the result is a real RGB-mode image.
    rawmode = {_RAW_FORMAT_RGBA_8888: "RGBX", _RAW_FORMAT_RGBX_8888: "RGBX", _RAW_FORMAT_BGRA_8888: "BGRX"}[pixel_format]
    try:
        return Image.frombytes("RGB", (width, height), data[header_size:], "raw", rawmode, 0, 4)
    except Exception:
        return None


SCREENSHOT_RETRY_ATTEMPTS = 3


def capture_device_screenshot(ctx: RunContext) -> Image.Image:
    """Capture one screenshot, retrying transient ADB/HDC failures.

    BlueStacks periodically fails `screencap` with an empty error for a few
    seconds while the emulator is busy; a single failure used to kill the plan.
    """
    last_error: Optional[BotError] = None
    for attempt in range(1, SCREENSHOT_RETRY_ATTEMPTS + 1):
        if ctx.device_backend == "adb" and not ctx.dry_run and raw_screencap_enabled():
            result = adb_exec_out_result(ctx, ["screencap"])
            if result.returncode == 0:
                image = parse_raw_screencap(result.stdout)
                if image is not None:
                    return image
            # Raw framing differs across Android versions; fall back to PNG.
        result = adb_exec_out_result(ctx, ["screencap", "-p"])
        if result.returncode != 0:
            text = (result.stderr or b"").decode("utf-8", errors="ignore").strip()
            last_error = DeviceError(f"{ctx.device_backend.upper()} screenshot failed:\n{text or 'unknown error'}")
        elif not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
            last_error = DeviceError(f"{ctx.device_backend.upper()} screenshot did not return a PNG image.")
        else:
            try:
                image = Image.open(io.BytesIO(result.stdout))
                image.load()
                return image.convert("RGB")
            except Exception as exc:
                last_error = DeviceError(f"Failed to decode screencap PNG: {exc}")
        if attempt >= SCREENSHOT_RETRY_ATTEMPTS or ctx.dry_run:
            break
        # Screenshots fail in bursts when the device/ADB link hiccups; wait a
        # moment and reconnect before spending the next attempt.
        if attempt == 1 and ctx.device_backend == "adb" and ctx.device:
            run_cmd([ctx.adb_path, "connect", ctx.device], check=False)
        elif attempt == 2 and ctx.device_backend == "adb":
            log("Screenshot still failing; restarting ADB server before last attempt ...")
            recover_adb_server(ctx.adb_path, ctx.device)
        time.sleep(0.5 * attempt)
    assert last_error is not None
    raise last_error


def screenshot_output_dir(ctx: RunContext, action: Dict[str, Any]) -> Path:
    raw_dir = str(action.get("dir", "")).strip()
    if not raw_dir:
        path = persistent_runtime_root(ctx) / "diagnostics" / "draw_result_pairs"
    else:
        candidate = Path(os.path.expanduser(raw_dir))
        path = candidate if candidate.is_absolute() else (ctx.plan_dir / candidate).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def screenshot_session_id(ctx: RunContext) -> str:
    current = ctx.runtime_values.get(SCREENSHOT_SESSION_KEY)
    if isinstance(current, str) and current:
        return current
    # Include microseconds and the worker PID so simultaneous chest windows
    # cannot overwrite each other's screenshot files.
    value = f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-{os.getpid()}"
    ctx.runtime_values[SCREENSHOT_SESSION_KEY] = value
    return value


def active_chest_source(ctx: RunContext) -> Dict[str, str]:
    source = {
        "source_id": ctx.source_id,
        "source_name": ctx.source_name,
    }
    if not ctx.source_file or not ctx.source_file.exists():
        return source
    try:
        value = json.loads(ctx.source_file.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            source_id = str(value.get("source_id", "")).strip()
            source_name = str(value.get("source_name", "")).strip()
            if source_id:
                source["source_id"] = source_id
            if source_name:
                source["source_name"] = source_name
    except (OSError, json.JSONDecodeError):
        pass
    return source


def chest_result_crop_box(image_size: Tuple[int, int]) -> Tuple[int, int, int, int]:
    """Keep the reward dialog while dropping unrelated game UI."""
    width, height = image_size
    return (
        round(width * 0.05),
        round(height * 0.33),
        round(width * 0.95),
        round(height * 0.70),
    )


def do_save_screenshot(ctx: RunContext, action: Dict[str, Any]) -> None:
    if ctx.dry_run:
        log(with_action_remark("[DRY-RUN] save_screenshot skipped", action))
        return

    output_dir = screenshot_output_dir(ctx, action)
    pair_key = sanitize_file_component(str(action.get("pair_key", "")).strip())
    stage = str(action.get("stage", "single")).strip().lower() or "single"
    label = sanitize_file_component(str(action.get("label", action.get("remark", "capture"))).strip())
    session_id = screenshot_session_id(ctx)
    source = active_chest_source(ctx)

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
                "device": ctx.device or "",
                "user_id": ctx.user_id,
                **source,
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

    crop_metadata: Dict[str, Any] = {}
    saved_image = image
    if bool_value(action.get("crop_chest_result"), default=False):
        crop_box = chest_result_crop_box(image.size)
        saved_image = image.crop(crop_box)
        crop_metadata = {"source_size": list(image.size), "crop_box": list(crop_box)}

    storage_format = str(action.get("storage_format", "png")).strip().lower()
    if storage_format in {"webp", "webp_lossless"}:
        destination = output_dir / f"{filename}.webp"
        saved_image.save(destination, format="WEBP", lossless=True, method=6)
    else:
        destination = output_dir / f"{filename}.png"
        saved_image.save(destination, format="PNG", optimize=True)
    saved_at = time.strftime("%Y-%m-%d %H:%M:%S")

    if pair_key:
        if stage == "before":
            active_pair = ctx.runtime_values[SCREENSHOT_ACTIVE_KEY][pair_key]
            active_pair["before_label"] = label
            active_pair["before_path"] = str(destination)
            active_pair["before_saved_at"] = saved_at
            active_pair.update(crop_metadata)
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
                    "device": ctx.device or "",
                    "user_id": ctx.user_id,
                    **source,
                    "pair_key": pair_key,
                    "pair_index": next_index,
                    "pair_prefix": pair_prefix,
                    "before_label": label,
                    "before_path": str(destination),
                    "before_saved_at": saved_at,
                    **crop_metadata,
                    "after_label": "",
                    "after_path": "",
                    "after_saved_at": "",
                    "composite_path": "",
                    "composite_saved_at": "",
                },
            )
    log(with_action_remark(f"Saved screenshot: {destination} ({image_source})", action))
    if bool_value(action.get("analyze_chest_items"), default=False):
        schedule_chest_analysis(ctx, action, output_dir)


def chest_analysis_worker(ctx: RunContext, action: Dict[str, Any], output_dir: Path) -> None:
    log(with_action_remark(f"Chest item analysis worker started for {output_dir}", action))
    while True:
        try:
            from chest_analyzer import analyze_results
            log(with_action_remark(f"Chest item analysis running for {output_dir}", action))
            summary = analyze_results(output_dir)
            log(
                with_action_remark(
                    f"Chest item analysis: {summary['analyzed']} new, "
                    f"{summary['events']} total, {summary['unknown_items']} pending labels",
                    action,
                )
            )
        except Exception as error:
            log(with_action_remark(f"Chest item analysis failed for {output_dir}: {error}", action))
            log(with_action_remark(traceback.format_exc().rstrip(), action))

        with ctx.stats_lock:
            queue = ctx.runtime_values.get(CHEST_ANALYSIS_QUEUE_KEY)
            if not isinstance(queue, list) or not queue:
                log(with_action_remark(f"Chest item analysis queue drained for {output_dir}", action))
                ctx.runtime_values.pop(CHEST_ANALYSIS_TASK_KEY, None)
                return
            next_output_dir = Path(str(queue.pop(0)))
            log(with_action_remark(
                f"Chest item analysis dequeued next target {next_output_dir} (remaining {len(queue)})",
                action,
            ))
            output_dir = next_output_dir


def schedule_chest_analysis(ctx: RunContext, action: Dict[str, Any], output_dir: Path) -> None:
    with ctx.stats_lock:
        queue = ctx.runtime_values.get(CHEST_ANALYSIS_QUEUE_KEY)
        if not isinstance(queue, list):
            queue = []
            ctx.runtime_values[CHEST_ANALYSIS_QUEUE_KEY] = queue
        log(with_action_remark(
            f"Queue chest analysis for {output_dir} (before={len(queue)})",
            action,
        ))
        queue.append(output_dir)
        previous = ctx.runtime_values.get(CHEST_ANALYSIS_TASK_KEY)
        if isinstance(previous, threading.Thread) and previous.is_alive():
            log(with_action_remark(
                f"Chest item analysis queued in background (pending={len(queue)})",
                action,
            ))
            return
        thread = threading.Thread(
            target=chest_analysis_worker,
            args=(ctx, dict(action), output_dir),
            name="chest-item-analysis",
            daemon=False,
        )
        tasks = ctx.runtime_values.setdefault(BACKGROUND_TASKS_KEY, [])
        if isinstance(tasks, list):
            tasks.append(thread)
        ctx.runtime_values[CHEST_ANALYSIS_TASK_KEY] = thread
        log(with_action_remark(
            f"Starting chest analysis worker thread for {output_dir} (pending={len(queue)})",
            action,
        ))
        thread.start()
    log(with_action_remark(f"Chest item analysis scheduled in background for {output_dir}", action))


def do_analyze_chest_results(ctx: RunContext, action: Dict[str, Any]) -> None:
    if ctx.dry_run:
        log(with_action_remark("[DRY-RUN] analyze_chest_results skipped", action))
        return
    output_dir = screenshot_output_dir(ctx, action)
    log(with_action_remark(f"Manual chest reanalyze requested for {output_dir}", action))
    schedule_chest_analysis(ctx, action, output_dir)


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
        # PyInstaller one-file builds unpack beside __file__ in a temporary
        # directory. Resolve the default stats directory from the plan instead.
        path = persistent_runtime_root(ctx) / "diagnostics" / "draw_stats"
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
            "user_id": ctx.user_id,
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
            "user_id": ctx.user_id,
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


def debug_output_dir(ctx: RunContext, action: Dict[str, Any], default_subdir: str) -> Path:
    """Resolve a debug output directory without letting a bad custom path
    (e.g. a plan authored on another machine) abort the automation run."""
    fallback = persistent_runtime_root(ctx) / "diagnostics" / default_subdir
    raw_dir = str(action.get("debug_dir", "")).strip()
    if raw_dir:
        path = resolve_action_file_path(ctx, raw_dir)
        try:
            path.mkdir(parents=True, exist_ok=True)
            if os.access(path, os.W_OK):
                return path
            log(f"Debug directory {path} is not writable; using {fallback}")
        except OSError as exc:
            log(f"Debug directory {path} is unavailable ({exc}); using {fallback}")
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def image_match_debug_dir(ctx: RunContext, action: Dict[str, Any]) -> Path:
    return debug_output_dir(ctx, action, "image_match_debug")


def red_light_debug_dir(ctx: RunContext, action: Dict[str, Any]) -> Path:
    return debug_output_dir(ctx, action, "red_light_debug")


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
    if bool_value(action.get("template_is_device_pixels")):
        scale_x, scale_y = 1.0, 1.0
    else:
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


def fft_correlate2d_valid_from_product(
    product: np.ndarray,
    image_shape: Tuple[int, int],
    kernel_shape: Tuple[int, int],
    fft_shape: Tuple[int, int],
) -> np.ndarray:
    """Finish a valid correlation when the forward FFTs are already cached."""
    image_h, image_w = image_shape
    kernel_h, kernel_w = kernel_shape
    full = np.fft.irfftn(product, s=fft_shape, axes=(0, 1))
    valid = full[kernel_h - 1 : image_h, kernel_w - 1 : image_w]
    return valid.astype(np.float32, copy=False)


def fft_cache_entry_bytes(fft_shape: Tuple[int, int], array_count: int = 2) -> int:
    complex_columns = fft_shape[1] // 2 + 1
    return fft_shape[0] * complex_columns * np.dtype(np.complex64).itemsize * array_count


def shared_search_fft_shape(search: PreparedSearchImage, template_variants: List[PreparedTemplateVariant]) -> Tuple[int, int]:
    """Padded FFT shape covering every variant so the search-side FFT pair is
    computed once per screenshot instead of once per template size."""
    image_h, image_w = search.gray.shape
    max_h = max(variant.height for variant in template_variants)
    max_w = max(variant.width for variant in template_variants)
    return (image_h + max_h - 1, image_w + max_w - 1)


def ensure_search_gray_fft(search: PreparedSearchImage, fft_shape: Tuple[int, int]) -> np.ndarray:
    if search.gray_fft is not None and search.gray_fft_shape == fft_shape:
        return search.gray_fft
    fft = np.fft.rfftn(search.gray, s=fft_shape, axes=(0, 1))
    object.__setattr__(search, "gray_fft", fft)
    object.__setattr__(search, "gray_fft_shape", fft_shape)
    return fft


def ensure_search_squared_fft(search: PreparedSearchImage, fft_shape: Tuple[int, int]) -> np.ndarray:
    if search.gray_squared_fft is not None and search.gray_fft_shape == fft_shape:
        return search.gray_squared_fft
    fft = np.fft.rfftn(search.gray * search.gray, s=fft_shape, axes=(0, 1))
    object.__setattr__(search, "gray_squared_fft", fft)
    return fft


def ensure_search_integrals(search: PreparedSearchImage) -> Tuple[np.ndarray, np.ndarray]:
    """Summed-area tables of gray and gray^2 for O(1) box sums in float64."""
    if search.integral_gray is not None:
        return search.integral_gray, search.integral_gray_squared
    gray64 = search.gray.astype(np.float64)
    integral = np.zeros((gray64.shape[0] + 1, gray64.shape[1] + 1), dtype=np.float64)
    integral[1:, 1:] = gray64.cumsum(axis=0).cumsum(axis=1)
    integral_sq = np.zeros_like(integral)
    integral_sq[1:, 1:] = (gray64 * gray64).cumsum(axis=0).cumsum(axis=1)
    object.__setattr__(search, "integral_gray", integral)
    object.__setattr__(search, "integral_gray_squared", integral_sq)
    return integral, integral_sq


def box_sums_valid(integral: np.ndarray, kernel_h: int, kernel_w: int) -> np.ndarray:
    """Box-filter sums over every valid kernel-sized window."""
    return (
        integral[kernel_h:, kernel_w:]
        - integral[:-kernel_h, kernel_w:]
        - integral[kernel_h:, :-kernel_w]
        + integral[:-kernel_h, :-kernel_w]
    )


def ensure_template_ffts(variant: PreparedTemplateVariant, fft_shape: Tuple[int, int]) -> Tuple[np.ndarray, np.ndarray]:
    """Per-variant FFTs at the shared padded shape, cached across attempts."""
    global _FFT_CACHE_TOTAL_BYTES
    if variant.fft_shape == fft_shape and variant.weights_fft is not None:
        return variant.weights_fft, variant.centered_fft
    weights_fft = np.fft.rfftn(np.flipud(np.fliplr(variant.weights)), s=fft_shape, axes=(0, 1)).astype(np.complex64)
    centered_fft = np.fft.rfftn(np.flipud(np.fliplr(variant.centered)), s=fft_shape, axes=(0, 1)).astype(np.complex64)
    object.__setattr__(variant, "fft_shape", fft_shape)
    object.__setattr__(variant, "weights_fft", weights_fft)
    object.__setattr__(variant, "centered_fft", centered_fft)
    _FFT_CACHE_TOTAL_BYTES += fft_cache_entry_bytes(fft_shape)
    if _FFT_CACHE_TOTAL_BYTES > FFT_CACHE_TOTAL_BUDGET_BYTES:
        for prepared in _TEMPLATE_VARIANT_CACHE.values():
            for cached_variant in prepared:
                object.__setattr__(cached_variant, "fft_shape", None)
                object.__setattr__(cached_variant, "weights_fft", None)
                object.__setattr__(cached_variant, "centered_fft", None)
        _FFT_CACHE_TOTAL_BYTES = fft_cache_entry_bytes(fft_shape)
    return weights_fft, centered_fft


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
        is_opaque=bool(np.all(weights == 1.0)),
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
    coordinate_variants = [(1.0, 1.0)] if bool_value(action.get("template_is_device_pixels")) else [(scale_x, scale_y)]
    if not bool_value(action.get("template_is_device_pixels")) and not (abs(scale_x - 1.0) < 1e-6 and abs(scale_y - 1.0) < 1e-6):
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
        bool_value(action.get("template_is_device_pixels")),
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
    squared = np.einsum("ijc,ijc->ij", diff, diff)
    mse = float((squared * weights).sum() / max(float(weights.sum()), 1.0) / 3.0)
    return max(0.0, 1.0 - (mse ** 0.5) / 255.0)


def extract_peak_candidates(score_map: np.ndarray, count: int, suppress_x: int, suppress_y: int) -> List[Tuple[int, int, float]]:
    """Greedy non-max-suppressed peaks, selection-identical to the classic
    repeated-argmax scan: candidates come from one argpartition pool extended
    with every position tied at the pool boundary (so first-occurrence tie
    ordering is preserved), and degenerate maps with huge tie plateaus fall
    back to the exhaustive masked scan."""
    if score_map.size == 0 or count <= 0:
        return []
    map_h, map_w = score_map.shape
    flat = score_map.reshape(-1)
    total = flat.size
    pool_size = min(total, max(count * 8, count + 64))
    if pool_size < total:
        pool = np.argpartition(flat, total - pool_size)[total - pool_size:]
        boundary = float(flat[pool].min())
        tied = np.flatnonzero(flat == boundary)
        if tied.size > pool_size + 4096:
            pool = None
        else:
            pool = np.union1d(pool, tied)
    else:
        pool = np.arange(total)

    candidates: List[Tuple[int, int, float]] = []
    picked: List[Tuple[int, int]] = []
    if pool is not None:
        order = pool[np.argsort(-flat[pool], kind="stable")]
        for flat_index in order:
            best_score = float(flat[flat_index])
            if not math.isfinite(best_score):
                break
            y, x = divmod(int(flat_index), map_w)
            if any(abs(py - y) <= suppress_y and abs(px - x) <= suppress_x for px, py in picked):
                continue
            candidates.append((x, y, best_score))
            picked.append((x, y))
            if len(candidates) >= count:
                return candidates

    # Tie plateau too large or pool exhausted before `count` peaks:
    # finish with the exhaustive masked scan.
    work = score_map.copy()
    for x, y in picked:
        y0 = max(0, y - suppress_y)
        y1 = min(map_h, y + suppress_y + 1)
        x0 = max(0, x - suppress_x)
        x1 = min(map_w, x + suppress_x + 1)
        work[y0:y1, x0:x1] = -np.inf
    while len(candidates) < count:
        flat_index = int(np.argmax(work))
        best_score = float(work.flat[flat_index])
        if not np.isfinite(best_score):
            break
        y, x = divmod(flat_index, map_w)
        candidates.append((x, y, best_score))
        y0 = max(0, y - suppress_y)
        y1 = min(map_h, y + suppress_y + 1)
        x0 = max(0, x - suppress_x)
        x1 = min(map_w, x + suppress_x + 1)
        work[y0:y1, x0:x1] = -np.inf
    return candidates


def image_match_score(shape_similarity: float, color_similarity: float, action: Dict[str, Any]) -> float:
    """Combine independent image signals without letting one hide a weak signal."""
    mode = str(action.get("score_mode", "weighted")).strip().lower()
    if mode in {"harmonic", "accurate", "strict"}:
        total = shape_similarity + color_similarity
        if total <= 1e-9:
            return 0.0
        return max(0.0, min(1.0, 2.0 * shape_similarity * color_similarity / total))
    return max(0.0, min(1.0, 0.7 * shape_similarity + 0.3 * color_similarity))


def image_match_center_distance(
    left: Dict[str, float],
    right: Dict[str, float],
) -> float:
    left_x = float(left.get("x", 0.0)) + float(left.get("width", 0.0)) / 2.0
    left_y = float(left.get("y", 0.0)) + float(left.get("height", 0.0)) / 2.0
    right_x = float(right.get("x", 0.0)) + float(right.get("width", 0.0)) / 2.0
    right_y = float(right.get("y", 0.0)) + float(right.get("height", 0.0)) / 2.0
    return float(math.hypot(left_x - right_x, left_y - right_y))


def image_match_is_stable(
    previous: Optional[Dict[str, float]],
    current: Optional[Dict[str, float]],
) -> bool:
    if previous is None or current is None:
        return False
    previous_width = max(1.0, float(previous.get("width", 1.0)))
    previous_height = max(1.0, float(previous.get("height", 1.0)))
    current_width = max(1.0, float(current.get("width", 1.0)))
    current_height = max(1.0, float(current.get("height", 1.0)))
    width_ratio = max(previous_width, current_width) / min(previous_width, current_width)
    height_ratio = max(previous_height, current_height) / min(previous_height, current_height)
    if width_ratio > 2.5 or height_ratio > 2.5:
        return False
    max_shift = max(12.0, min(previous_width, previous_height, current_width, current_height) * 0.55)
    return image_match_center_distance(previous, current) <= max_shift


def find_image_match(
    search: PreparedSearchImage,
    template_variants: List[PreparedTemplateVariant],
    threshold: float,
    index: int,
    action: Dict[str, Any],
    fft_shape: Optional[Tuple[int, int]] = None,
) -> Tuple[Optional[Dict[str, float]], Optional[Dict[str, float]]]:
    crop_box = search.crop_box
    search_rgb = search.rgb
    search_gray = search.gray
    if not template_variants:
        return None, None
    evaluated: List[Dict[str, float]] = []
    wanted = max(index + 1, int(action.get("max_candidates", 8)))
    # Allow plans to widen the candidate pool for small templates that have
    # many high-shape false positives before color scoring distinguishes them.
    raw_peak_count = action.get("candidate_peaks")
    try:
        per_scale_peaks = int(raw_peak_count) if raw_peak_count is not None else max(4, wanted * 2)
    except (TypeError, ValueError):
        raise BotError("candidate_peaks must be an integer when provided")
    per_scale_peaks = max(1, min(128, per_scale_peaks))
    if fft_shape is None:
        fft_shape = shared_search_fft_shape(search, template_variants)
    gray_fft = ensure_search_gray_fft(search, fft_shape)
    for variant in template_variants:
        kernel_shape = (variant.height, variant.width)
        if variant.is_opaque:
            # Fully opaque templates make the weighted sums plain box filters,
            # so two of the three full-size FFT correlations per variant
            # collapse into summed-area-table lookups. The sums are still
            # reduced to float32 afterwards so the variance/score arithmetic
            # keeps the exact same rounding behavior as the FFT path.
            integral_gray, integral_gray_squared = ensure_search_integrals(search)
            sum_i = box_sums_valid(integral_gray, variant.height, variant.width).astype(np.float32)
            sum_i2 = box_sums_valid(integral_gray_squared, variant.height, variant.width).astype(np.float32)
            _, centered_fft = ensure_template_ffts(variant, fft_shape)
            numerator = fft_correlate2d_valid_from_product(
                gray_fft * centered_fft,
                search_gray.shape,
                kernel_shape,
                fft_shape,
            )
        else:
            weights_fft, centered_fft = ensure_template_ffts(variant, fft_shape)
            squared_fft = ensure_search_squared_fft(search, fft_shape)
            sum_i = fft_correlate2d_valid_from_product(
                gray_fft * weights_fft,
                search_gray.shape,
                kernel_shape,
                fft_shape,
            )
            sum_i2 = fft_correlate2d_valid_from_product(
                squared_fft * weights_fft,
                search_gray.shape,
                kernel_shape,
                fft_shape,
            )
            numerator = fft_correlate2d_valid_from_product(
                gray_fft * centered_fft,
                search_gray.shape,
                kernel_shape,
                fft_shape,
            )

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
            similarity = image_match_score(shape_similarity, color_similarity, action)
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
    min_color_similarity = action.get("min_color_similarity")
    try:
        min_color_similarity = (
            float(min_color_similarity) if min_color_similarity is not None else None
        )
    except (TypeError, ValueError):
        raise BotError("min_color_similarity must be a number when provided")
    min_shape_similarity = action.get("min_shape_similarity")
    try:
        min_shape_similarity = (
            float(min_shape_similarity) if min_shape_similarity is not None else None
        )
    except (TypeError, ValueError):
        raise BotError("min_shape_similarity must be a number when provided")
    qualified = [
        item
        for item in evaluated
        if item["similarity"] >= threshold
        and (
            min_color_similarity is None
            or item["color_similarity"] >= min_color_similarity
        )
        and (
            min_shape_similarity is None
            or item["shape_similarity"] >= min_shape_similarity
        )
    ]
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


def verify_template_once(
    ctx: RunContext,
    templates: List[str],
    region: Dict[str, Any],
    threshold: float,
) -> Dict[str, Any]:
    screenshot = capture_device_screenshot(ctx)
    action = {
        "templates": templates,
        "region": region,
        "threshold": threshold,
        "scales": [1.0],
        "max_candidates": 1,
        "search_padding_ratio": 0.0,
        "disable_contextual_cancel_template": True,
        "disable_resolution_template_variants": True,
        "template_is_device_pixels": True,
    }
    template_paths = resolve_action_template_paths(ctx, action, "template verification")
    template_errors: Dict[Path, str] = {}
    valid_template_paths: List[Path] = []
    for template_path in template_paths:
        try:
            load_template_image(template_path)
            valid_template_paths.append(template_path)
        except BotError as exc:
            template_errors[template_path] = str(exc)
    minimum_size = required_template_search_size(ctx, valid_template_paths, action) if valid_template_paths else (1, 1)
    search = prepare_search_image(ctx, screenshot, action, minimum_size=minimum_size)
    matches: List[Tuple[Path, Optional[Dict[str, float]], Optional[Dict[str, float]], Optional[str]]] = []
    prepared_by_template: Dict[Path, List[PreparedTemplateVariant]] = {}
    for template_path in valid_template_paths:
        try:
            prepared_by_template[template_path] = get_prepared_template_variants(
                ctx, template_path, action, (search.gray.shape[1], search.gray.shape[0])
            )
        except BotError as exc:
            template_errors[template_path] = str(exc)
    shared_fft_shape = shared_search_fft_shape(
        search,
        [variant for variants in prepared_by_template.values() for variant in variants],
    ) if prepared_by_template else None
    for template_path in template_paths:
        error = template_errors.get(template_path)
        if error is not None:
            matches.append((template_path, None, None, error))
            continue
        try:
            variants = prepared_by_template.get(template_path, [])
            match, best = find_image_match(search, variants, threshold, 0, action, fft_shape=shared_fft_shape)
            matches.append((template_path, match, best, None))
        except BotError as exc:
            matches.append((template_path, None, None, str(exc)))
    qualified_index = next((index for index, item in enumerate(matches) if item[1] is not None), None)
    best_index = max(range(len(matches)), key=lambda index: float((matches[index][1] or matches[index][2] or {}).get("similarity", 0.0)))
    selected_index = qualified_index if qualified_index is not None else best_index
    selected_path, selected_match, selected_best, selected_error = matches[selected_index]
    def result_for(path: Path, candidate: Optional[Dict[str, float]], best_candidate: Optional[Dict[str, float]], error: Optional[str]) -> Dict[str, Any]:
        selected = candidate or best_candidate
        template_width: Optional[int] = None
        template_height: Optional[int] = None
        if error is None:
            try:
                template_width, template_height = load_template_image(path).size
            except BotError as exc:
                error = str(exc)
        box = None
        overlay = screenshot.copy()
        if selected is not None:
            box = (
                int(round(selected["x"])),
                int(round(selected["y"])),
                int(round(selected["x"] + selected["width"])),
                int(round(selected["y"] + selected["height"])),
            )
            draw = ImageDraw.Draw(overlay, "RGBA")
            color = (56, 211, 159, 90) if candidate is not None else (225, 141, 135, 90)
            border = (56, 211, 159, 230) if candidate is not None else (225, 141, 135, 230)
            draw.rectangle(box, fill=color, outline=border, width=max(3, round(min(screenshot.size) / 360)))
        output = io.BytesIO()
        overlay.save(output, format="PNG")
        result: Dict[str, Any] = {
            "template": path.name,
            "matched": candidate is not None,
            "similarity": float(best_candidate["similarity"] if best_candidate is not None else 0.0),
            "scale": float(selected["scale"] if selected is not None else 0.0),
            "threshold": threshold,
            "box": {"x": box[0], "y": box[1], "width": box[2] - box[0], "height": box[3] - box[1]} if box else None,
            "image": "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii"),
        }
        if template_width is not None and template_height is not None:
            result["template_size"] = {"width": template_width, "height": template_height}
        if error is not None:
            result["error"] = error
        return result
    results = [result_for(path, candidate, best_candidate, error) for path, candidate, best_candidate, error in matches]
    aggregate = screenshot.copy()
    aggregate_draw = ImageDraw.Draw(aggregate, "RGBA")
    for item in results:
        item_box = item.get("box")
        if not isinstance(item_box, dict):
            continue
        left = int(item_box["x"])
        top = int(item_box["y"])
        right = left + int(item_box["width"])
        bottom = top + int(item_box["height"])
        color = (56, 211, 159, 90) if item["matched"] else (225, 141, 135, 90)
        border = (56, 211, 159, 230) if item["matched"] else (225, 141, 135, 230)
        aggregate_draw.rectangle((left, top, right, bottom), fill=color, outline=border, width=max(3, round(min(screenshot.size) / 360)))
    aggregate_output = io.BytesIO()
    aggregate.save(aggregate_output, format="PNG")
    result = {
        "matched": selected_match is not None,
        "template": selected_path.name,
        "similarity": float(selected_best["similarity"] if selected_best is not None else 0.0),
        "threshold": threshold,
        "region": region,
        "box": results[selected_index]["box"],
        "image": "data:image/png;base64," + base64.b64encode(aggregate_output.getvalue()).decode("ascii"),
        "results": results,
    }
    if selected_error is not None:
        result["error"] = selected_error
    return result


def remember_if_text_match(ctx: RunContext, action: Dict[str, Any], result: Dict[str, Any]) -> None:
    clear_if_text_match(ctx)
    if result.get("dry_run") or not result.get("matched"):
        return
    match = result.get("match")
    if not isinstance(match, dict):
        return
    screenshot = result.get("screenshot")
    if isinstance(screenshot, Image.Image):
        ctx.runtime_values["current_if_text_screenshot"] = screenshot.copy()

    raw_center_x = int(round(float(match.get("x", 0)) + float(match.get("width", 0)) / 2.0))
    raw_center_y = int(round(float(match.get("y", 0)) + float(match.get("height", 0)) / 2.0))
    center_x = raw_center_x + int(action.get("offset_x", 0))
    center_y = raw_center_y + int(action.get("offset_y", 0))
    ctx.runtime_values["current_if_text_match"] = {
        "text": str(match.get("text", "")),
        "target_text": str(action.get("text") or ""),
        "center_x": center_x,
        "center_y": center_y,
        "match_center_x": raw_center_x,
        "match_center_y": raw_center_y,
        "x": int(round(float(match.get("x", 0)))),
        "y": int(round(float(match.get("y", 0)))),
        "width": int(round(float(match.get("width", 0)))),
        "height": int(round(float(match.get("height", 0)))),
    }


def execute_if_text(ctx: RunContext, action: Dict[str, Any]) -> None:
    clear_if_text_match(ctx)
    raw_texts = action.get("texts")
    if isinstance(raw_texts, list):
        texts = [str(item).strip() for item in raw_texts if str(item).strip()]
    else:
        text = str(action.get("text", "")).strip()
        texts = [text] if text else []
    if not texts:
        raise BotError("if_text requires 'text' or non-empty 'texts'")
    then_actions = action.get("then_actions", [])
    else_actions = action.get("else_actions", [])
    if not isinstance(then_actions, list):
        raise BotError("if_text 'then_actions' must be an array")
    if not isinstance(else_actions, list):
        raise BotError("if_text 'else_actions' must be an array")

    timeout_sec = float(action.get("timeout_sec", 2.0))
    interval_sec = float(action.get("interval_sec", 0.2))
    max_attempts = int(action.get("max_attempts", 1))
    match_mode = str(action.get("match", "contains")).strip().lower()
    if match_mode not in {"contains", "exact"}:
        raise BotError("if_text 'match' must be 'contains' or 'exact'")
    lang = str(action.get("lang", "chi_sim+eng")).strip() or "chi_sim+eng"
    psm = int(action.get("psm", 6))
    index = int(action.get("index", 0))

    if ctx.dry_run:
        log(with_action_remark(f"[DRY-RUN] if_text texts={texts} match={match_mode}", action))
        return

    deadline = time.time() + max(0.0, timeout_sec)
    attempt = 0
    while True:
        check_stop(ctx)
        attempt += 1
        screenshot = capture_device_screenshot(ctx)
        left, top, width, height = resolve_search_region(ctx, action, screenshot.size)
        cropped = screenshot.crop((left, top, left + width, top + height))
        rows = run_tesseract_tsv(cropped, lang=lang, psm=psm)
        found: Optional[Tuple[int, int, int, int, str]] = None
        for target_text in texts:
            found = find_text_region(rows, target_text=target_text, match_mode=match_mode, index=index)
            if found is not None:
                break
        if found is not None:
            box_left, box_top, box_right, box_bottom, matched_text = found
            match = {
                "x": box_left + left,
                "y": box_top + top,
                "width": box_right - box_left,
                "height": box_bottom - box_top,
                "text": matched_text,
            }
            remember_if_text_match(ctx, action, {"matched": True, "match": match, "screenshot": screenshot})
            log(with_action_remark(f"if_text matched '{matched_text}' on attempt {attempt}", action))
            execute_actions(ctx, then_actions)
            return

        if max_attempts > 0 and attempt >= max_attempts:
            log(with_action_remark(f"if_text did not match after {attempt} attempt(s)", action))
            execute_actions(ctx, else_actions)
            return
        if time.time() >= deadline:
            log(with_action_remark(f"if_text timed out after {timeout_sec:.1f}s", action))
            execute_actions(ctx, else_actions)
            return
        time.sleep(max(0.1, interval_sec))


def prepare_image_match_profile(
    action: Dict[str, Any],
    action_name: str,
    threshold: float,
    interval_sec: float,
) -> Dict[str, Any]:
    profile_raw = str(
        action.get("match_profile")
        or ("accurate" if action_name == "if_image" else "balanced")
    ).strip().lower()
    if profile_raw in {"accuracy", "strict"}:
        profile = "accurate"
    elif profile_raw in {"fast", "legacy"}:
        profile = "balanced"
    elif profile_raw in {"accurate", "balanced"}:
        profile = profile_raw
    else:
        raise BotError("match_profile must be 'accurate' or 'balanced'")

    match_action = dict(action)
    match_action["match_profile"] = profile
    if profile != "accurate":
        match_action.setdefault("score_mode", "weighted")
        return match_action

    # Keep the existing default scale range for normal accuracy mode. Fine
    # sampling is available explicitly for difficult scale-drift cases.
    if (
        bool_value(action.get("fine_scales"))
        and "scales" not in action
        and "scale_min" not in action
        and "scale_max" not in action
        and "scale_step" not in action
    ):
        match_action["scale_min"] = 0.75
        match_action["scale_max"] = 1.25
        match_action["scale_step"] = 0.05

    match_action.setdefault("score_mode", "harmonic")
    requested_candidates = max(1, int(action.get("max_candidates", 8)))
    requested_index = max(0, int(action.get("index", 0)))
    match_action.setdefault(
        "candidate_peaks",
        max(16, requested_candidates * 2, requested_index + 1),
    )
    if action.get("min_shape_similarity") is None:
        match_action["min_shape_similarity"] = max(
            0.0,
            min(1.0, threshold - 0.12),
        )
    if action.get("min_color_similarity") is None:
        match_action["min_color_similarity"] = max(
            0.0,
            min(1.0, threshold - 0.18),
        )
    if action_name == "if_image":
        match_action.setdefault("confirm_attempts", 2)
        match_action.setdefault(
            "confirm_interval_sec",
            max(0.1, min(0.3, interval_sec / 2.0)),
        )
        match_action.setdefault("evaluate_all_templates", True)
    return match_action


def resolve_find_image_action(
    ctx: RunContext,
    action: Dict[str, Any],
    action_name: str,
) -> Dict[str, Any]:
    threshold = float(action.get("threshold", 0.92))
    timeout_sec = float(action.get("timeout_sec", 8.0))
    interval_sec = float(action.get("interval_sec", 0.6))
    max_attempts = max(0, int(action.get("max_attempts", 0)))
    index = int(action.get("index", 0))
    offset_x = int(action.get("offset_x", 0))
    offset_y = int(action.get("offset_y", 0))
    preview_only = bool_value(action.get("preview_only")) or bool_value(action.get("debug_preview"))
    save_debug = bool_value(action.get("save_debug"), default=ctx.image_match_debug)
    match_action = prepare_image_match_profile(action, action_name, threshold, interval_sec)
    template_paths = resolve_action_template_paths(ctx, match_action, action_name)
    confirm_attempts = max(1, int(match_action.get("confirm_attempts", 1)))
    if max_attempts > 0:
        confirm_attempts = min(confirm_attempts, max_attempts)
    confirm_interval_sec = max(
        0.0,
        float(match_action.get("confirm_interval_sec", interval_sec)),
    )
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
        "match_action": match_action,
        "match_profile": str(match_action.get("match_profile", "balanced")),
        "confirm_attempts": confirm_attempts,
        "confirm_interval_sec": confirm_interval_sec,
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
    match_profile = settings["match_profile"]
    confirm_attempts = settings["confirm_attempts"]
    confirm_interval_sec = settings["confirm_interval_sec"]
    log_template_progress = action.get("templates") is not None
    search_action = image_search_action(settings["match_action"], template_paths)
    minimum_template_size = required_template_search_size(ctx, template_paths, search_action)
    if ctx.dry_run:
        attempt_text = f" max_attempts={max_attempts}" if max_attempts > 0 else ""
        log(
            with_action_remark(
                f"[DRY-RUN] {action_name} {image_action_target_summary(template_paths)} "
                f"threshold={threshold:.3f} timeout={timeout_sec:.1f}s{attempt_text} "
                f"profile={match_profile} confirm={confirm_attempts} preview_only={preview_only}",
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
    pending_match: Optional[Dict[str, float]] = None
    pending_count = 0
    while True:
        check_stop(ctx)
        attempt += 1
        log(
            with_action_remark(
                f"{action_name} attempt {attempt} started; matching {len(template_paths)} template(s)",
                action,
            )
        )
        screenshot = capture_device_screenshot(ctx)
        search = prepare_search_image(ctx, screenshot, search_action, minimum_size=minimum_template_size)
        search_size = (search.gray.shape[1], search.gray.shape[0])
        matched_template_path: Optional[Path] = None
        matched_template_candidate: Optional[Dict[str, float]] = None

        template_total = len(template_paths)
        evaluate_all_templates = bool_value(
            search_action.get("evaluate_all_templates"),
            default=False,
        )
        prepared_variants_by_template = [
            get_prepared_template_variants(ctx, template_path, search_action, search_size)
            for template_path in template_paths
        ]
        shared_fft_shape = shared_search_fft_shape(
            search,
            [variant for variants in prepared_variants_by_template for variant in variants],
        )
        for template_index, (template_path, prepared_variants) in enumerate(zip(template_paths, prepared_variants_by_template), start=1):
            match, candidate_best = find_image_match(
                search=search,
                template_variants=prepared_variants,
                threshold=threshold,
                index=index,
                action=search_action,
                fft_shape=shared_fft_shape,
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
                if (
                    matched_template_candidate is None
                    or match["similarity"] > matched_template_candidate["similarity"]
                ):
                    matched_template_path = template_path
                    matched_template_candidate = match
                if not evaluate_all_templates:
                    break

        if matched_template_path is not None and matched_template_candidate is not None:
            if confirm_attempts <= 1:
                pending_count = 1
            elif image_match_is_stable(pending_match, matched_template_candidate):
                pending_count += 1
            else:
                pending_count = 1
            pending_match = dict(matched_template_candidate)

            if pending_count >= confirm_attempts:
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
                    "screenshot": screenshot,
                }
        else:
            pending_match = None
            pending_count = 0

        confirmation_in_progress = 0 < pending_count < confirm_attempts
        attempts_exhausted = (
            max_attempts > 0
            and attempt >= max_attempts
            and not confirmation_in_progress
        )
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
        sleep_interval = confirm_interval_sec if pending_count > 0 else interval_sec
        time.sleep(max(0.1, sleep_interval))


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


def background_check_match(
    ctx: RunContext,
    screenshot: Image.Image,
    template_paths: List[Path],
    search_action: Dict[str, Any],
    minimum_template_size: Tuple[int, int],
    threshold: float,
    index: int,
) -> Optional[Dict[str, Any]]:
    search = prepare_search_image(ctx, screenshot, search_action, minimum_size=minimum_template_size)
    search_size = (search.gray.shape[1], search.gray.shape[0])
    for template_path in template_paths:
        prepared_variants = get_prepared_template_variants(ctx, template_path, search_action, search_size)
        match, _candidate_best = find_image_match(
            search=search,
            template_variants=prepared_variants,
            threshold=threshold,
            index=index,
            action=search_action,
        )
        if match is None:
            continue
        return {
            "template": template_path.name,
            "template_path": str(template_path),
            "similarity": float(match["similarity"]),
            "x": int(round(match["x"])),
            "y": int(round(match["y"])),
            "width": int(round(match["width"])),
            "height": int(round(match["height"])),
            "center_x": int(round(match["x"] + match["width"] / 2.0)),
            "center_y": int(round(match["y"] + match["height"] / 2.0)),
        }
    return None


def background_check_until_profile(
    ctx: RunContext,
    action: Dict[str, Any],
    until_paths: List[Path],
    interval_sec: float,
) -> Tuple[float, Dict[str, Any], Tuple[int, int]]:
    threshold = float(action.get("until_threshold", 0.8))
    profile_action = dict(action)
    profile_action["threshold"] = threshold
    until_region = action.get("until_region")
    if isinstance(until_region, dict):
        profile_action["region"] = until_region
    if "until_scales" in action:
        profile_action["scales"] = action.get("until_scales")
    else:
        for key in ("scales", "scale_min", "scale_max", "scale_step", "fine_scales"):
            profile_action.pop(key, None)
    profile_action["match_profile"] = str(action.get("until_match_profile") or "balanced")
    profile_action["index"] = 0
    profile_action = prepare_image_match_profile(profile_action, "background_check", threshold, interval_sec)
    search_action = image_search_action(profile_action, until_paths)
    minimum_size = required_template_search_size(ctx, until_paths, search_action)
    return threshold, search_action, minimum_size


def background_check_worker(
    ctx: RunContext,
    action: Dict[str, Any],
    template_paths: List[Path],
    stop_event: threading.Event,
    until_paths: Optional[List[Path]] = None,
) -> None:
    """Continuously screenshot+match in a daemon thread so the main plan can
    keep tapping while a template (e.g. the chest magnifier) is being watched.

    Default single-shot semantics: exits after the first hit (result stored in
    BG_CHECK_HIT_KEY) or when stop_event/ctx.stop_at fires. Optional mode for
    deep-linked workflows:
    - "click": tap every primary hit and keep watching instead of exiting, so
      the icon can be retried while a battle blocks the popup.
    - "until_templates": exit only when one of these appears (e.g. the claim or
      watch-video button), which proves the popup really opened.
    Never raises: transient failures are logged and retried."""
    until_paths = list(until_paths or [])
    threshold = float(action.get("threshold", 0.88))
    interval_sec = max(0.2, float(action.get("interval_sec", 0.5)))
    index = int(action.get("index", 0))
    click_on_hit = bool_value(action.get("click"), default=False)
    click_interval_sec = max(0.0, float(action.get("click_interval_sec", interval_sec)))
    save_debug = bool_value(action.get("save_debug"), default=False)
    debug_dir = debug_output_dir(ctx, action, "background_check") if save_debug and not ctx.dry_run else None
    match_action = prepare_image_match_profile(dict(action), "background_check", threshold, interval_sec)
    search_action = image_search_action(match_action, template_paths)
    minimum_template_size = required_template_search_size(ctx, template_paths, search_action)
    until_threshold = 0.0
    until_search_action: Optional[Dict[str, Any]] = None
    until_minimum_template_size: Optional[Tuple[int, int]] = None
    if until_paths:
        until_threshold, until_search_action, until_minimum_template_size = background_check_until_profile(
            ctx,
            action,
            until_paths,
            interval_sec,
        )
    remark = action_remark_text(action)
    remark_suffix = f" | remark: {remark}" if remark else ""
    attempt = 0
    last_click_at = 0.0
    previous_primary_found = False
    last_debug_save_at = 0.0
    while not stop_event.is_set():
        if ctx.stop_at is not None and time.time() >= ctx.stop_at:
            return
        attempt += 1
        try:
            screenshot = capture_device_screenshot(ctx)
        except Exception as exc:  # noqa: BLE001 - worker must survive transient device errors
            log(f"background_check screenshot failed (attempt {attempt}): {exc}")
            if stop_event.wait(1.5):
                return
            continue
        try:
            if until_search_action is not None and until_minimum_template_size is not None:
                until_hit = background_check_match(
                    ctx,
                    screenshot,
                    until_paths,
                    until_search_action,
                    until_minimum_template_size,
                    until_threshold,
                    0,
                )
                if until_hit is not None:
                    until_hit["kind"] = "until"
                    ctx.runtime_values[BG_CHECK_HIT_KEY] = until_hit
                    log(
                        f"background_check UNTIL '{until_hit['template']}' "
                        f"box=({until_hit['x']},{until_hit['y']})-"
                        f"({until_hit['x'] + until_hit['width']},{until_hit['y'] + until_hit['height']}) "
                        f"similarity={until_hit['similarity']:.3f} -> popup opened "
                        f"(attempt {attempt}){remark_suffix}"
                    )
                    return
            hit = background_check_match(
                ctx,
                screenshot,
                template_paths,
                search_action,
                minimum_template_size,
                threshold,
                index,
            )
            if hit is not None:
                previous_primary_found = True
                if not click_on_hit and not until_paths:
                    ctx.runtime_values[BG_CHECK_HIT_KEY] = hit
                    log(
                        f"background_check HIT '{hit['template']}' "
                        f"box=({hit['x']},{hit['y']})-({hit['x'] + hit['width']},{hit['y'] + hit['height']}) "
                        f"similarity={hit['similarity']:.3f} (attempt {attempt}){remark_suffix}"
                    )
                    return
                if click_on_hit:
                    now = time.time()
                    if now - last_click_at >= click_interval_sec:
                        tap_x, tap_y = tap_absolute(ctx, hit["center_x"], hit["center_y"])
                        last_click_at = time.time()
                        log(
                            f"background_check CLICK '{hit['template']}' "
                            f"box=({hit['x']},{hit['y']})-({hit['x'] + hit['width']},{hit['y'] + hit['height']}) "
                            f"similarity={hit['similarity']:.3f} -> tap ({tap_x}, {tap_y}) "
                            f"(attempt {attempt}){remark_suffix}"
                        )
            else:
                if (
                    debug_dir is not None
                    and previous_primary_found
                    and time.time() - last_debug_save_at >= 5.0
                ):
                    try:
                        debug_path = debug_dir / (
                            f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
                            "_magnifier_lost_until_miss.png"
                        )
                        screenshot.save(debug_path)
                        last_debug_save_at = time.time()
                        log(
                            f"background_check magnifier lost and until missed; "
                            f"debug screenshot saved to {debug_path}{remark_suffix}"
                        )
                    except Exception as exc:  # noqa: BLE001 - diagnostics must not kill the worker
                        log(f"background_check debug screenshot failed: {exc}")
                previous_primary_found = False
        except Exception as exc:  # noqa: BLE001 - matching/clicking must never kill the worker
            log(f"background_check cycle failed (attempt {attempt}): {exc}")
        if stop_event.wait(interval_sec):
            return


def resolve_background_check_until_paths(ctx: RunContext, action: Dict[str, Any]) -> List[Path]:
    raw_templates = action.get("until_templates")
    if raw_templates is None:
        return []
    if not isinstance(raw_templates, list) or not raw_templates:
        raise BotError("background_check 'until_templates' must be a non-empty array")
    until_action = dict(action)
    until_action["templates"] = raw_templates
    return resolve_action_template_paths(ctx, until_action, "background_check")


def start_background_check(ctx: RunContext, action: Dict[str, Any]) -> None:
    if ctx.dry_run:
        log(with_action_remark("[DRY-RUN] background_check start skipped (no device in preview)", action))
        return
    thread = ctx.runtime_values.get(BG_CHECK_THREAD_KEY)
    if isinstance(thread, threading.Thread) and thread.is_alive():
        return  # idempotent: the watcher keeps running across plan cycles
    template_paths = resolve_action_template_paths(ctx, action, "background_check")
    if not template_paths:
        raise BotError("background_check requires at least one template")
    until_paths = resolve_background_check_until_paths(ctx, action)
    stop_event = threading.Event()
    ctx.runtime_values[BG_CHECK_STOP_KEY] = stop_event
    worker = threading.Thread(
        target=background_check_worker,
        args=(ctx, dict(action), template_paths, stop_event, until_paths),
        name="background-check",
        daemon=True,
    )
    ctx.runtime_values[BG_CHECK_THREAD_KEY] = worker
    worker.start()
    mode_parts = [
        f"{len(template_paths)} template(s)",
        f"threshold={float(action.get('threshold', 0.88)):.3f}",
        f"interval={max(0.2, float(action.get('interval_sec', 0.5))):.1f}s",
    ]
    if bool_value(action.get("click")):
        click_interval_sec = max(0.2, float(action.get("click_interval_sec", action.get("interval_sec", 0.5))))
        mode_parts.append(f"click=True cooldown={click_interval_sec:.1f}s")
    if until_paths:
        mode_parts.append(f"until={len(until_paths)} template(s)")
    log(with_action_remark(f"background_check worker started ({', '.join(mode_parts)})", action))


def execute_background_check(ctx: RunContext, action: Dict[str, Any]) -> None:
    command = str(action.get("command", "start")).strip().lower()
    if command == "stop":
        stop_event = ctx.runtime_values.get(BG_CHECK_STOP_KEY)
        if isinstance(stop_event, threading.Event):
            stop_event.set()
        log(with_action_remark("background_check worker stop requested", action))
    else:
        start_background_check(ctx, action)


def execute_grab_loop(ctx: RunContext, action: Dict[str, Any]) -> None:
    loop_count = int(action.get("count", 1))
    if loop_count == 0:
        return
    watch_hit = bool_value(action.get("watch_hit"), default=False)
    nested_actions = action.get("actions", [])
    if not isinstance(nested_actions, list):
        raise BotError("grab_loop 'actions' must be an array")
    log(with_action_remark(
        f"Grab loop start count={'infinite' if loop_count < 0 else str(loop_count)} watch_hit={watch_hit}",
        action,
    ))
    rounds = 0
    while loop_count < 0 or rounds < loop_count:
        check_stop(ctx)
        if watch_hit and ctx.runtime_values.get(BG_CHECK_HIT_KEY) is not None:
            log(with_action_remark("background_check hit detected; abort grab loop early", action))
            return
        execute_actions(ctx, nested_actions)
        rounds += 1


def execute_if_background_hit(ctx: RunContext, action: Dict[str, Any]) -> None:
    hit = ctx.runtime_values.pop(BG_CHECK_HIT_KEY, None)
    then_actions = action.get("then_actions", [])
    else_actions = action.get("else_actions", [])
    if not isinstance(then_actions, list):
        raise BotError("if_background_hit 'then_actions' must be an array")
    if not isinstance(else_actions, list):
        raise BotError("if_background_hit 'else_actions' must be an array")
    if hit is not None:
        log(with_action_remark(
            f"background_check hit consumed: '{hit.get('template')}' "
            f"similarity={float(hit.get('similarity', 0.0)):.3f} -> then_actions",
            action,
        ))
        execute_actions(ctx, then_actions)
    else:
        log(with_action_remark("no background_check hit -> else_actions", action))
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
    prepared_by_template: List[Tuple[Path, List[PreparedTemplateVariant]]] = []
    for template_path in template_paths:
        try:
            variants = get_prepared_template_variants(ctx, template_path, search_action, search_size)
        except BotError:
            continue
        prepared_by_template.append((template_path, variants))
    shared_fft_shape = shared_search_fft_shape(
        search,
        [variant for _, variants in prepared_by_template for variant in variants],
    ) if prepared_by_template else None
    for template_path, variants in prepared_by_template:
        _, candidate = find_image_match(search, variants, 0.0, 0, search_action, fft_shape=shared_fft_shape)
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


def resolve_draw_state_text_target(
    ctx: RunContext,
    screenshot: Image.Image,
    parent_action: Dict[str, Any],
    target_config: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    target_action = dict(parent_action)
    target_action.update(target_config)
    raw_texts = target_action.get("texts")
    if isinstance(raw_texts, list):
        texts = [str(item).strip() for item in raw_texts if str(item).strip()]
    else:
        text = str(target_action.get("text", "")).strip()
        texts = [text] if text else []
    if not texts:
        return None

    match_mode = str(target_action.get("match", "contains")).strip().lower()
    if match_mode not in {"contains", "exact"}:
        raise BotError("resolve_draw_state text target 'match' must be 'contains' or 'exact'")
    lang = str(target_action.get("lang", "chi_sim+eng")).strip() or "chi_sim+eng"
    psm = int(target_action.get("psm", 6))
    index = int(target_action.get("index", 0))
    left, top, width, height = resolve_search_region(ctx, target_action, screenshot.size)
    cropped = screenshot.crop((left, top, left + width, top + height))

    try:
        rows = run_tesseract_tsv(cropped, lang=lang, psm=psm)
    except Exception:
        return None

    for text in texts:
        found = find_text_region(rows, target_text=text, match_mode=match_mode, index=index)
        if found is None:
            continue
        box_left, box_top, box_right, box_bottom, matched_text = found
        center_x = int(round((box_left + box_right) / 2.0)) + left
        center_y = int(round((box_top + box_bottom) / 2.0)) + top
        return {
            "text": matched_text,
            "target_text": text,
            "x": center_x,
            "y": center_y,
            "left": box_left + left,
            "top": box_top + top,
            "right": box_right + left,
            "bottom": box_bottom + top,
        }
    return None


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

        cancel_text_match = resolve_draw_state_text_target(ctx, screenshot, action, cancel_config)
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

        if cancel_text_match is not None:
            next_score = float(next_best.get("similarity", 0.0)) if next_best is not None else 0.0
            log(
                with_action_remark(
                    f"resolve_draw_state detected cancel text '{cancel_text_match['text']}' "
                    f"on attempt {attempt} (next best {next_score:.3f}), click text center",
                    action,
                )
            )
            x, y = tap_absolute(ctx, int(cancel_text_match["x"]), int(cancel_text_match["y"]))
            log(with_action_remark(f"Click draw-state cancel text at ({x}, {y})", action))
            do_wait({"seconds": cancel_settle_wait_sec, "remark": "本次抽卡结束，等待动画结束后开启下一轮"})
            return

        if cancel_match is not None:
            next_score = float(next_best.get("similarity", 0.0)) if next_best is not None else 0.0
            center_x = int(round(cancel_match["x"] + cancel_match["width"] / 2.0))
            center_y = int(round(cancel_match["y"] + cancel_match["height"] / 2.0))
            offset_x = int(cancel_config.get("offset_x", 0))
            offset_y = int(cancel_config.get("offset_y", 0))
            log(
                with_action_remark(
                    f"resolve_draw_state detected cancel '{cancel_template.name}' on attempt {attempt} "
                    f"(cancel {cancel_match['similarity']:.3f}, next best {next_score:.3f}), click cancel",
                    action,
                )
            )
            x, y = tap_absolute(ctx, center_x + offset_x, center_y + offset_y)
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


TRACE_EMIT_GAP_MS = 35
TRACE_CORNER_MIN_GAP_MS = 30


def trace_corner_flags(points: List[Dict[str, Any]], min_turn_deg: float = 25.0) -> List[bool]:
    """Mark points that define the shape of a trace (turns / reversals).

    During replay the adaptive scheduler may drop intermediate samples when the
    guest cannot keep up. Dropping a corner would cut the route short, so those
    points (and their neighbours) are kept unconditionally.
    """
    flags = [False] * len(points)
    if len(points) < 3:
        return flags

    def coords(index: int) -> Tuple[float, float]:
        return float(points[index].get("x", 0.0)), float(points[index].get("y", 0.0))

    for i in range(1, len(points) - 1):
        prev_index = i - 1
        while prev_index > 0:
            x0, y0 = coords(prev_index)
            xi, yi = coords(i)
            if math.hypot(xi - x0, yi - y0) >= 1.0:
                break
            prev_index -= 1
        next_index = i + 1
        while next_index < len(points) - 1:
            x1, y1 = coords(next_index)
            xi, yi = coords(i)
            if math.hypot(x1 - xi, y1 - yi) >= 1.0:
                break
            next_index += 1

        x0, y0 = coords(prev_index)
        xi, yi = coords(i)
        x1, y1 = coords(next_index)
        v1x, v1y = xi - x0, yi - y0
        v2x, v2y = x1 - xi, y1 - yi
        n1 = math.hypot(v1x, v1y)
        n2 = math.hypot(v2x, v2y)
        if n1 < 1.0 or n2 < 1.0:
            continue
        cosine = max(-1.0, min(1.0, (v1x * v2x + v1y * v2y) / (n1 * n2)))
        if math.degrees(math.acos(cosine)) >= min_turn_deg:
            flags[i] = True
            if i - 1 > 0:
                flags[i - 1] = True
            if i + 1 < len(points) - 1:
                flags[i + 1] = True
    return flags


def do_trace(ctx: RunContext, action: Dict[str, Any]) -> None:
    points = action.get("points", [])
    if not isinstance(points, list) or len(points) < 2:
        return

    min_segment_ms = max(1, int(action.get("min_segment_ms", 1)))
    max_segment_ms = max(min_segment_ms, int(action.get("max_segment_ms", 1000)))
    trace_mode = str(action.get("mode", "auto")).lower()
    trace_time_scale = float(action.get("time_scale", ctx.trace_time_scale))
    trace_jitter_px = int(action.get("trace_jitter_px", 0))
    offset_x = 0 if trace_jitter_px <= 0 else random.randint(-trace_jitter_px, trace_jitter_px)
    offset_y = 0 if trace_jitter_px <= 0 else random.randint(-trace_jitter_px, trace_jitter_px)

    def map_trace_point(raw_x: int, raw_y: int) -> Tuple[int, int]:
        x, y = map_input_point(ctx, raw_x, raw_y)
        x += offset_x
        y += offset_y
        return clamp_input_point(ctx, x, y)

    def map_trace_vector_point(origin_raw: Dict[str, Any], target_raw: Dict[str, Any]) -> Tuple[int, int]:
        if ctx.src_screen_w <= 0 or ctx.src_screen_h <= 0:
            return map_trace_point(int(target_raw["x"]), int(target_raw["y"]))
        if ctx.dst_screen_w <= 0 or ctx.dst_screen_h <= 0:
            return map_trace_point(int(target_raw["x"]), int(target_raw["y"]))

        # Trace/joystick vectors must keep their screen angle when the device
        # aspect ratio differs from the plan's source screen. Scaling x and y
        # independently would tilt the path (e.g. 50° becomes 56°).
        origin_x = int(origin_raw["x"])
        origin_y = int(origin_raw["y"])
        target_x = int(target_raw["x"])
        target_y = int(target_raw["y"])
        mapped_origin_x, mapped_origin_y = map_trace_point(origin_x, origin_y)
        uniform_scale = ctx.dst_screen_w / float(ctx.src_screen_w)
        x = mapped_origin_x + int(round((target_x - origin_x) * uniform_scale))
        y = mapped_origin_y + int(round((target_y - origin_y) * uniform_scale))
        return clamp_input_point(ctx, x, y)

    if ctx.device_backend == "hdc":
        first = points[0]
        first_t = int(first.get("t_ms", 0))
        last_t = first_t
        try:
            last_t = int(points[-1].get("t_ms", first_t))
        except (TypeError, ValueError):
            last_t = first_t
        x0, y0 = map_trace_point(int(first["x"]), int(first["y"]))

        target = points[-1]
        target_t = last_t
        for p in points[1:]:
            x, y = map_trace_vector_point(first, p)
            if (x, y) != (x0, y0):
                target = p
                try:
                    target_t = int(p.get("t_ms", first_t))
                except (TypeError, ValueError):
                    target_t = first_t
                break

        x2, y2 = map_trace_vector_point(first, target)
        total_ms = max(min_segment_ms, int(round((last_t - first_t) * trace_time_scale)))
        smooth_ms = max(min_segment_ms, int(round((target_t - first_t) * trace_time_scale)))
        smooth_ms = min(15000, smooth_ms)
        keep_ms = max(0, total_ms - smooth_ms)
        angle = math.degrees(math.atan2(-(y2 - y0), x2 - x0)) if (x2, y2) != (x0, y0) else 0.0

        hdc_touch_move(ctx, x0, y0, x2, y2, keep_ms=keep_ms, smooth_ms=smooth_ms)
        log(with_action_remark(
            f"Trace replayed by HDC uinput touch move with {len(points)} points "
            f"(angle={angle:.1f}deg, smooth={smooth_ms}ms, keep={keep_ms}ms)",
            action,
        ))
        return

    if trace_mode == "motion":
        use_motion = supports_motionevent(ctx)
    elif trace_mode == "swipe":
        use_motion = False
    else:
        use_motion = supports_motionevent(ctx)
    if use_motion:
        # One adb round-trip for the whole gesture. Every `input motionevent`
        # is a synchronous process launch inside the guest and can cost tens
        # of milliseconds depending on app load, so blind sleeps overshoot.
        # The script below keeps reading /proc/uptime (shell builtins only)
        # and waits for each point's target time: fast when the device keeps
        # up, and compressed instead of stretched when it cannot.
        first = points[0]
        x0, y0 = map_trace_point(int(first["x"]), int(first["y"]))
        script_lines = [f"input motionevent DOWN {x0} {y0}"]
        # Time base starts once DOWN has been delivered, matching the
        # recorded gesture whose first sample is the touch-down event.
        # Uptime in ms fits a 32-bit signed int for any realistic uptime
        # (< 2.1M seconds), so no modulo dance is needed here. The decimal
        # part of /proc/uptime is centiseconds, hence the x10; 10# forces
        # decimal parsing for values like 08/09.
        script_lines += [
            "read up _ < /proc/uptime",
            "t0=$(( ${up%.*} * 1000 + 10#${up#*.} * 10 ))",
        ]
        critical_flags = trace_corner_flags(points)
        # The guest spends roughly 50ms of wall time per emitted point (one
        # `input motionevent` spawn plus /proc/uptime reads), while recorded
        # traces sample every 15-25ms. Emitting every sample therefore
        # stretches the whole gesture 1.5-2x; joystick-style games walk at a
        # constant speed while the finger is down, so the character overshoots
        # the recorded route and gets stuck on obstacles. Emit on a
        # TRACE_EMIT_GAP_MS grid and keep corners only when they fit within
        # TRACE_CORNER_MIN_GAP_MS, so the schedule keeps up with the recorded
        # timing.
        emit_points = [first]
        for i in range(1, len(points)):
            gap_ms = int(points[i].get("t_ms", 0)) - int(emit_points[-1].get("t_ms", 0))
            if i == len(points) - 1:
                emit_points.append(points[i])
            elif gap_ms >= TRACE_EMIT_GAP_MS or (critical_flags[i] and gap_ms >= TRACE_CORNER_MIN_GAP_MS):
                emit_points.append(points[i])
        sampled_count = len(emit_points)
        # max_segment_ms is a per-chunk cap, not a total cap: split long gaps
        # into interpolated points so the gesture keeps its recorded duration.
        split_points: List[Dict[str, Any]] = [emit_points[0]]
        for i in range(1, len(emit_points)):
            p_prev = emit_points[i - 1]
            p_next = emit_points[i]
            t_prev = int(p_prev.get("t_ms", 0))
            t_next = int(p_next.get("t_ms", t_prev))
            delta = t_next - t_prev
            if delta > max_segment_ms:
                chunks = max(1, min(1000, math.ceil(delta / max_segment_ms)))
                x_prev, y_prev = int(p_prev["x"]), int(p_prev["y"])
                x_next, y_next = int(p_next["x"]), int(p_next["y"])
                for chunk in range(1, chunks):
                    ratio = chunk / chunks
                    split_points.append({
                        "x": int(round(x_prev + (x_next - x_prev) * ratio)),
                        "y": int(round(y_prev + (y_next - y_prev) * ratio)),
                        "t_ms": t_prev + int(round(delta * ratio)),
                    })
            split_points.append(p_next)
        emit_points = split_points
        prev_t = int(first.get("t_ms", 0))
        target_ms = 0.0
        for i in range(1, len(emit_points)):
            p = emit_points[i]
            t_now = int(p.get("t_ms", prev_t))
            delta_raw = t_now - prev_t
            delta_ms = min_segment_ms if delta_raw <= 0 else max(min_segment_ms, min(max_segment_ms, delta_raw))
            target_ms += delta_ms * trace_time_scale
            x, y = map_trace_vector_point(first, p)
            script_lines += [
                "read up _ < /proc/uptime",
                "now=$(( ${up%.*} * 1000 + 10#${up#*.} * 10 ))",
                f"remain=$(( {int(round(target_ms))} - (now - t0) ))",
                # Do not sleep unless ahead of schedule.
                "if [ $remain -gt 0 ]; then",
                "  sec=$((remain/1000)); msec=$((remain%1000))",
                "  if [ $msec -lt 10 ]; then ms=00$msec; elif [ $msec -lt 100 ]; then ms=0$msec; else ms=$msec; fi",
                "  read -t $sec.$ms _ </dev/zero",
                "fi",
                f"input motionevent MOVE {x} {y}",
            ]
            prev_t = t_now
        last = points[-1]
        xl, yl = map_trace_vector_point(first, last)
        script_lines.append(f"input motionevent UP {xl} {yl}")
        adb_shell_script(ctx, "\n".join(script_lines) + "\n", timeout=180.0)
        log(with_action_remark(f"Trace replayed continuously with {len(emit_points)} points ({sampled_count} sampled from {len(points)})", action))
        return

    # Fallback for devices without motionevent support, or forced swipe mode.
    first = points[0]
    for i in range(len(points) - 1):
        p1 = points[i]
        p2 = points[i + 1]
        x1, y1 = map_trace_vector_point(first, p1)
        x2, y2 = map_trace_vector_point(first, p2)
        seg_ms = min_segment_ms
        if "t_ms" in p1 and "t_ms" in p2:
            try:
                delta = int(p2["t_ms"]) - int(p1["t_ms"])
                # Splitting a swipe would lift the finger between chunks, so
                # honor the recorded segment duration instead of clamping it.
                seg_ms = max(min_segment_ms, delta)
            except (TypeError, ValueError):
                seg_ms = min_segment_ms
        adb_shell(ctx, ["input", "swipe", str(x1), str(y1), str(x2), str(y2), str(seg_ms)])
    log(with_action_remark(f"Trace replayed by segmented swipe with {len(points)} points", action))


def do_joystick(ctx: RunContext, action: Dict[str, Any]) -> None:
    """Drag from a start point in a direction and hold for a duration.

    Editable shorthand for the common joystick trace: reach the edge in
    `flick_ms`, then keep the finger there until `duration_ms`.
    Angle uses screen coordinates: 0=right, 90=up, -90=down, 180=left.
    """
    if "x" not in action or "y" not in action:
        raise BotError("joystick action requires x and y")
    try:
        x = int(action["x"])
        y = int(action["y"])
        angle = float(action.get("angle", 0))
        distance = max(1, int(action.get("distance", 180)))
        if action.get("duration_ms") is not None:
            duration_ms = max(1, int(round(float(action["duration_ms"]))))
        else:
            duration_ms = max(1, int(round(float(action.get("seconds", 1.0)) * 1000)))
        flick_ms = max(1, min(duration_ms, int(round(float(action.get("flick_ms", 100))))))
    except (TypeError, ValueError) as exc:
        raise BotError(f"Invalid joystick action: {exc}") from exc

    radians = math.radians(angle)
    end_x = int(round(x + math.cos(radians) * distance))
    end_y = int(round(y - math.sin(radians) * distance))
    trace_action: Dict[str, Any] = {
        "type": "trace",
        "remark": action.get("remark") or f"{angle:g}度{duration_ms / 1000:g}秒",
        "points": [
            {"x": x, "y": y, "t_ms": 0},
            {"x": end_x, "y": end_y, "t_ms": flick_ms},
            {"x": end_x, "y": end_y, "t_ms": duration_ms},
        ],
        "mode": action.get("mode", "motion"),
        "min_segment_ms": max(1, int(action.get("min_segment_ms", 1))),
        "max_segment_ms": max(1, duration_ms),
    }
    for key in ("trace_jitter_px", "time_scale"):
        if key in action:
            trace_action[key] = action[key]
    log(with_action_remark(
        f"Joystick ({x}, {y}) -> ({end_x}, {end_y}) angle={angle:g} duration={duration_ms}ms",
        action,
    ))
    do_trace(ctx, trace_action)


def do_wait(action: Dict[str, Any]) -> None:
    seconds = float(action.get("seconds", 1.0))
    jitter = float(action.get("jitter_seconds", 0.0))
    if jitter > 0:
        seconds = max(0.0, seconds + random.uniform(-jitter, jitter))
    log(with_action_remark(f"Wait {seconds:.2f}s", action))
    time.sleep(seconds)


def do_log_action(action: Dict[str, Any]) -> None:
    message = str(action.get("message") or action.get("remark") or "").strip()
    if message:
        log(f"[Log] {message}")


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


# ---------------------------------------------------------------------------
# bag_appraise: classify bag equipment by cell background color, tap each
# unidentified item, and stop once only red items and gray empty cells remain.
# ---------------------------------------------------------------------------

BAG_APPRAISE_DEFAULT_TARGETS = ("orange", "purple", "blue")
BAG_APPRAISE_COLOR_RULES: Dict[str, Dict[str, Any]] = {
    "red": {"hue_ranges": ((338.0, 360.0), (0.0, 16.0)), "min_saturation": 0.45, "min_value": 0.25},
    "orange": {"hue_ranges": ((17.0, 50.0),), "min_saturation": 0.55, "min_value": 0.40},
    "purple": {"hue_ranges": ((280.0, 330.0),), "min_saturation": 0.45, "min_value": 0.30},
    "blue": {"hue_ranges": ((180.0, 235.0),), "min_saturation": 0.45, "min_value": 0.30},
}
BAG_APPRAISE_STALL_DIFF = 1.0


def bag_rgb_to_hsv(rgb: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """RGB(0-255) -> HSV with hue in degrees and saturation/value in 0..1."""
    values = rgb.astype(np.float32) / 255.0
    max_channel = values.max(axis=2)
    min_channel = values.min(axis=2)
    delta = max_channel - min_channel
    saturation = np.where(max_channel > 1e-6, delta / np.maximum(max_channel, 1e-6), 0.0)
    hue = np.zeros_like(max_channel)
    nonzero = delta > 1e-6
    red_channel = values[..., 0]
    green_channel = values[..., 1]
    blue_channel = values[..., 2]
    red_max = nonzero & (max_channel == red_channel)
    green_max = nonzero & ~red_max & (max_channel == green_channel)
    blue_max = nonzero & ~red_max & ~green_max
    hue[red_max] = ((green_channel[red_max] - blue_channel[red_max]) / delta[red_max]) % 6.0
    hue[green_max] = (blue_channel[green_max] - red_channel[green_max]) / delta[green_max] + 2.0
    hue[blue_max] = (red_channel[blue_max] - green_channel[blue_max]) / delta[blue_max] + 4.0
    return hue * 60.0, saturation, max_channel


def bag_projection_runs(
    projection: np.ndarray,
    threshold: float,
    min_length: int,
    max_gap: int,
) -> List[Tuple[int, int]]:
    """Merge contiguous above-threshold spans of a 1-D projection into runs."""
    indexes = np.flatnonzero(projection >= threshold)
    if indexes.size == 0:
        return []
    runs: List[Tuple[int, int]] = []
    start = previous = int(indexes[0])
    for raw_index in indexes[1:]:
        index = int(raw_index)
        if index - previous > max_gap:
            if previous - start + 1 >= min_length:
                runs.append((start, previous))
            start = index
        previous = index
    if previous - start + 1 >= min_length:
        runs.append((start, previous))
    return runs


def bag_cell_pixel_mask(rgb: np.ndarray, action: Dict[str, Any]) -> np.ndarray:
    """Mask quality-colored card pixels and gray empty cells to locate the grid."""
    _, saturation, value = bag_rgb_to_hsv(rgb)
    colored = (
        (saturation > float(action.get("cell_min_saturation", 0.45)))
        & (value > float(action.get("cell_min_value", 0.30)))
    )
    empty = (
        (saturation < float(action.get("empty_max_saturation", 0.32)))
        & (value > float(action.get("empty_min_value", 0.42)))
        & (value < float(action.get("empty_max_value", 0.82)))
    )
    return colored | empty


def bag_classify_patch(patch: np.ndarray, action: Dict[str, Any]) -> Tuple[str, float]:
    """Classify a cell from its median top-strip color; returns (color, std)."""
    if patch.size == 0:
        return "unknown", 0.0
    flat_pixels = patch.reshape(-1, 3)
    median = np.median(flat_pixels, axis=0).astype(np.float32).reshape(1, 1, 3)
    hue, saturation, value = bag_rgb_to_hsv(median)
    hue_value = float(hue[0, 0])
    saturation_value = float(saturation[0, 0])
    value_value = float(value[0, 0])
    flatness = float(flat_pixels.std(axis=0).mean())
    empty_max_saturation = float(action.get("empty_max_saturation", 0.32))
    empty_min_value = float(action.get("empty_min_value", 0.42))
    empty_max_value = float(action.get("empty_max_value", 0.82))
    if (
        saturation_value <= empty_max_saturation
        and empty_min_value <= value_value <= empty_max_value
    ):
        return "gray", flatness
    for name, rule in BAG_APPRAISE_COLOR_RULES.items():
        if saturation_value < float(rule["min_saturation"]) or value_value < float(rule["min_value"]):
            continue
        if any(low <= hue_value <= high for low, high in rule["hue_ranges"]):
            return name, flatness
    return "unknown", flatness


def bag_search_box(
    ctx: RunContext,
    action: Dict[str, Any],
    image_size: Tuple[int, int],
) -> Optional[Dict[str, int]]:
    raw_region = action.get("region")
    if not isinstance(raw_region, dict):
        return None
    return map_logical_region(ctx, raw_region, image_size, name="bag_appraise region")


def bag_analyze_screen(
    ctx: RunContext,
    screenshot: Image.Image,
    action: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Locate the 5-column bag grid and classify every cell background.

    Columns come from a horizontal projection over the middle screen band;
    rows come from a vertical projection across the detected columns with a
    row-height filter, so any scroll offset (snapped or not) is supported.
    """
    rgb = np.asarray(screenshot.convert("RGB"), dtype=np.float32)
    height, width = rgb.shape[:2]
    if width < 160 or height < 320:
        return None
    mask = bag_cell_pixel_mask(rgb, action)

    region = bag_search_box(ctx, action, (width, height))
    if region is not None:
        band_left = region["x"]
        band_top = region["y"]
        band_right = region["x"] + region["width"]
        band_bottom = region["y"] + region["height"]
        row_top, row_bottom = band_top, band_bottom
    else:
        band_left = int(width * 0.03)
        band_top = int(height * 0.25)
        band_right = int(width * 0.97)
        band_bottom = int(height * 0.65)
        row_top, row_bottom = 0, height
    band_left = max(0, min(width - 1, band_left))
    band_right = max(band_left + 1, min(width, band_right))
    band_top = max(0, min(height - 1, band_top))
    band_bottom = max(band_top + 1, min(height, band_bottom))

    column_projection = mask[band_top:band_bottom, band_left:band_right].sum(axis=0)
    column_runs = bag_projection_runs(
        column_projection,
        threshold=max(1.0, (band_bottom - band_top) * 0.20),
        min_length=max(8, int(width * 0.05)),
        max_gap=max(2, int(round(width * 0.005))),
    )
    expected_columns = max(1, int(action.get("columns", 5)))
    if expected_columns < 2:
        return None
    width_min = width * 0.07
    width_max = width * 0.25
    candidates = [
        (band_left + start, band_left + end)
        for start, end in column_runs
        if width_min <= end - start + 1 <= width_max
    ]
    if len(candidates) < expected_columns:
        return None

    best_group: Optional[Tuple[float, List[Tuple[int, int]], float]] = None
    for index in range(0, len(candidates) - expected_columns + 1):
        group = candidates[index : index + expected_columns]
        centers = [(left + right) / 2.0 for left, right in group]
        gaps = [centers[i + 1] - centers[i] for i in range(expected_columns - 1)]
        if any(gap <= 0 for gap in gaps):
            continue
        mean_gap = sum(gaps) / len(gaps)
        if not (width * 0.07 <= mean_gap <= width * 0.30):
            continue
        spacing_error = sum(abs(gap - mean_gap) for gap in gaps)
        coverage = min(
            float(mask[band_top:band_bottom, left:right].sum(axis=0).max()) / max(1, band_bottom - band_top)
            for left, right in group
        )
        score = spacing_error + max(0.0, 0.60 - coverage) * 80.0
        if best_group is None or score < best_group[0]:
            best_group = (score, group, coverage)
    if best_group is None:
        return None

    group = best_group[1]
    centers_array = np.array([(left + right) / 2.0 for left, right in group], dtype=np.float64)
    index_array = np.arange(expected_columns, dtype=np.float64)
    slope, intercept = np.polyfit(index_array, centers_array, 1)
    if not np.isfinite(slope) or slope <= 0:
        return None
    pitch_x = float(slope)
    column_centers = [float(intercept + slope * i) for i in range(expected_columns)]

    row_left = int(group[0][0])
    row_right = int(group[-1][1])
    row_projection = mask[row_top:row_bottom, row_left:row_right].sum(axis=1)
    row_runs = bag_projection_runs(
        row_projection,
        threshold=max(1.0, (row_right - row_left) * 0.45),
        min_length=20,
        max_gap=max(2, int(round(width * 0.005))),
    )
    if len(row_runs) < 2:
        return None
    row_heights = {row_top + start: end - start + 1 for start, end in row_runs}
    starts = sorted(row_heights)

    diffs = [
        later - earlier
        for earlier, later in zip(starts, starts[1:])
        if 0 < later - earlier <= max(400, int(height * 0.20))
    ]
    if not diffs:
        return None
    best_pitch: Optional[Tuple[int, int]] = None
    for candidate_pitch in sorted(set(diffs)):
        if candidate_pitch < 30:
            continue
        cluster_size = len([value for value in diffs if abs(value - candidate_pitch) <= 12])
        if cluster_size < 2:
            continue
        key = (cluster_size, candidate_pitch)
        if best_pitch is None or key > best_pitch:
            best_pitch = key
    if best_pitch is None:
        return None
    pitch_y = float(np.median([value for value in diffs if abs(value - best_pitch[1]) <= 12]))
    tolerance = max(5.0, pitch_y * 0.06)

    best_chain: List[int] = []
    for seed in starts:
        chain = [seed]
        for next_start in starts:
            if next_start <= chain[-1]:
                continue
            steps = (next_start - chain[-1]) / pitch_y
            whole_steps = int(round(steps))
            if 1 <= whole_steps <= 3 and abs(steps - whole_steps) * pitch_y <= tolerance:
                chain.append(next_start)
        if len(chain) > len(best_chain):
            best_chain = chain

    card_height = pitch_y * float(action.get("card_height_ratio", 0.89))
    row_starts = [
        start
        for start in best_chain
        if row_heights.get(start, 0) >= pitch_y * 0.55
    ]
    if len(row_starts) < 2:
        return None

    patch_half_width = max(5, int(round(pitch_x * 0.16)))
    patch_top = max(0, int(round(card_height * 0.08)))
    patch_bottom = max(patch_top + 2, int(round(card_height * 0.18)))
    click_offset = max(2, int(round(card_height * 0.10)))
    rows: List[Dict[str, Any]] = []
    flatness_values: List[float] = []
    for start in row_starts:
        cells: List[Dict[str, Any]] = []
        for column_index, center in enumerate(column_centers):
            center_x = int(round(center))
            left = max(0, min(width - 1, center_x - patch_half_width))
            right = max(left + 1, min(width, center_x + patch_half_width))
            top = max(0, min(height - 1, start + patch_top))
            bottom = max(top + 1, min(height, start + patch_bottom))
            color, flatness = bag_classify_patch(rgb[top:bottom, left:right], action)
            flatness_values.append(flatness)
            cells.append(
                {
                    "column": column_index,
                    "color": color,
                    "x": center_x,
                    "y": min(height - 1, start + click_offset),
                }
            )
        rows.append({"start": start, "cells": cells})

    return {
        "rows": rows,
        "columns": column_centers,
        "pitch_x": pitch_x,
        "pitch_y": pitch_y,
        "card_height": card_height,
        "median_flatness": float(np.median(flatness_values)) if flatness_values else 0.0,
        "coverage": float(best_group[2]),
    }


def bag_save_debug_image(
    ctx: RunContext,
    screenshot: Image.Image,
    layout: Dict[str, Any],
    action: Dict[str, Any],
    tag: str,
) -> None:
    directory = persistent_runtime_root(ctx) / "diagnostics" / "bag_appraise"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        image = screenshot.copy()
        draw = ImageDraw.Draw(image)
        palette = {
            "red": (255, 80, 80),
            "orange": (255, 170, 40),
            "purple": (230, 90, 230),
            "blue": (80, 160, 255),
            "gray": (200, 200, 200),
            "unknown": (255, 255, 0),
        }
        half_width = layout["pitch_x"] * 0.45
        cell_height = layout["card_height"] * 0.55
        for row in layout["rows"]:
            for cell in row["cells"]:
                box = (
                    int(round(cell["x"] - half_width)),
                    int(row["start"]),
                    int(round(cell["x"] + half_width)),
                    int(round(row["start"] + cell_height)),
                )
                draw.rectangle(box, outline=palette.get(cell["color"], (255, 255, 255)), width=3)
                draw.text((box[0] + 5, box[1] + 3), cell["color"][:1].upper(), fill=(0, 0, 0))
        path = directory / f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')}_{sanitize_file_component(tag)}.png"
        image.save(path)
        log(with_action_remark(f"bag_appraise debug image saved: {path}", action))
    except OSError as exc:
        log(f"bag_appraise could not save debug image: {exc}")


def bag_view_signature(
    screenshot: Image.Image,
    layout: Optional[Dict[str, Any]],
) -> Optional[np.ndarray]:
    if not layout or not layout["rows"]:
        return None
    rows = layout["rows"]
    columns = layout["columns"]
    half = max(1, int(round(layout["pitch_x"] * 0.5)))
    left = max(0, int(round(columns[0] - half)))
    right = min(screenshot.size[0], int(round(columns[-1] + half)))
    top = max(0, int(rows[0]["start"]))
    bottom = min(screenshot.size[1], int(round(rows[-1]["start"] + layout["card_height"])))
    if right - left < 8 or bottom - top < 8:
        return None
    crop = screenshot.crop((left, top, right, bottom)).convert("L").resize((24, 32), Image.Resampling.BILINEAR)
    return np.asarray(crop, dtype=np.float32)


def bag_signature_distance(left: Optional[np.ndarray], right: Optional[np.ndarray]) -> float:
    if left is None or right is None or left.shape != right.shape:
        return float("inf")
    return float(np.mean(np.abs(left - right)))


def bag_color_signature(layout: Optional[Dict[str, Any]]) -> tuple:
    if not layout or not layout["rows"]:
        return ()
    return tuple(
        (int(row["start"]) // 8, "".join(cell["color"] for cell in row["cells"]))
        for row in layout["rows"]
    )


def bag_view_changed(
    before_layout: Optional[Dict[str, Any]],
    before_image: Optional[np.ndarray],
    after_layout: Optional[Dict[str, Any]],
    after_image: Optional[np.ndarray],
    threshold: float,
) -> bool:
    """True when the bag visibly changed (color grid first, then pixels)."""
    if before_layout is None or after_layout is None:
        return True
    if bag_color_signature(before_layout) != bag_color_signature(after_layout):
        return True
    return bag_signature_distance(before_image, after_image) >= threshold


def bag_cell_key(cell: Dict[str, Any]) -> Tuple[int, int]:
    return int(cell["column"]), int(cell["y"]) // 8


def bag_pick_target(
    layout: Dict[str, Any],
    target_colors: set,
    failed_attempts: Dict[Tuple[int, int], int],
    max_attempts: int,
) -> Optional[Dict[str, Any]]:
    """Return the top-most, left-most unidentified cell, skipping dead ones."""
    for row in layout["rows"]:
        for cell in row["cells"]:
            if cell["color"] not in target_colors:
                continue
            if failed_attempts.get(bag_cell_key(cell), 0) >= max_attempts:
                continue
            return cell
    return None


def bag_swipe_device(
    ctx: RunContext,
    x: int,
    y1: int,
    y2: int,
    duration_ms: int,
    action: Dict[str, Any],
    note: str,
) -> None:
    if ctx.dst_screen_w > 0:
        x = max(0, min(ctx.dst_screen_w - 1, x))
    if ctx.dst_screen_h > 0:
        y1 = max(0, min(ctx.dst_screen_h - 1, y1))
        y2 = max(0, min(ctx.dst_screen_h - 1, y2))
    x = apply_jitter(x, ctx.jitter)
    y1 = apply_jitter(y1, ctx.jitter)
    y2 = apply_jitter(y2, ctx.jitter)
    adb_shell(ctx, ["input", "swipe", str(x), str(y1), str(x), str(y2), str(duration_ms)])
    log(with_action_remark(f"{note} ({x}, {y1}) -> ({x}, {y2}), {duration_ms}ms", action))


def bag_scroll_once(
    ctx: RunContext,
    layout: Dict[str, Any],
    action: Dict[str, Any],
    *,
    toward_top: bool,
) -> bool:
    rows = layout["rows"]
    if len(rows) < 2:
        return False
    pitch = layout["pitch_y"]
    card_height = layout["card_height"]
    distance = int(round(pitch * max(1, int(action.get("scroll_rows", 4)))))
    columns = layout["columns"]
    x = int(round(columns[len(columns) // 2]))
    if toward_top:
        y1 = int(round(rows[0]["start"] + card_height * 0.45))
        y2 = min(int(round(rows[-1]["start"] + card_height * 0.55)), y1 + distance)
        note = "Swipe bag list to top"
    else:
        y1 = int(round(rows[-2]["start"] + card_height * 0.55))
        y2 = max(int(round(rows[0]["start"] + card_height * 0.45)), y1 - distance)
        note = "Swipe bag list down"
    if abs(y2 - y1) < pitch * 0.5:
        return False
    bag_swipe_device(
        ctx,
        x,
        y1,
        y2,
        max(100, int(action.get("scroll_duration_ms", 700))),
        action,
        note,
    )
    return True


def execute_bag_appraise(ctx: RunContext, action: Dict[str, Any]) -> None:
    raw_targets = action.get("target_colors", list(BAG_APPRAISE_DEFAULT_TARGETS))
    if not isinstance(raw_targets, list) or not raw_targets:
        raise BotError("bag_appraise 'target_colors' must be a non-empty array")
    target_colors = {str(item).strip().lower() for item in raw_targets}
    unknown_targets = target_colors - set(BAG_APPRAISE_COLOR_RULES)
    if unknown_targets:
        raise BotError(f"bag_appraise unknown target color(s): {', '.join(sorted(unknown_targets))}")

    click_wait_sec = max(0.1, float(action.get("click_wait_sec", 2.5)))
    scroll_wait_sec = max(0.1, float(action.get("scroll_wait_sec", 1.0)))
    max_steps = max(0, int(action.get("max_steps", 5000)))
    max_clicks = max(0, int(action.get("max_clicks", 0)))
    scroll_to_top = bool_value(action.get("scroll_to_top"), default=False)
    debug = bool_value(action.get("debug"), default=False)
    max_patch_std = float(action.get("max_patch_std", 10.0))
    stall_diff = max(0.1, float(action.get("stall_diff", BAG_APPRAISE_STALL_DIFF)))
    recover_with_back = bool_value(action.get("recover_with_back"), default=True)

    if ctx.dry_run:
        log(with_action_remark(
            f"[DRY-RUN] bag_appraise target_colors={sorted(target_colors)} click_wait={click_wait_sec:.1f}s",
            action,
        ))
        return

    screenshot = capture_device_screenshot(ctx)
    layout = bag_analyze_screen(ctx, screenshot, action)
    if layout is None:
        raise BotError(
            "bag_appraise could not locate the 5-column bag grid; open the equipment bag page first "
            "(or set 'region' to the grid area)"
        )
    if layout["median_flatness"] > max_patch_std:
        if debug:
            bag_save_debug_image(ctx, screenshot, layout, action, "wrong_tab")
        raise BotError(
            "bag_appraise cell backgrounds are not uniform; this does not look like the equipment bag page "
            f"(median_patch_std={layout['median_flatness']:.1f}, limit={max_patch_std:g})"
        )
    log(with_action_remark(
        f"bag_appraise start: columns={len(layout['columns'])} rows={len(layout['rows'])} "
        f"pitch={layout['pitch_y']:.0f}px patch_std={layout['median_flatness']:.1f} "
        f"targets={sorted(target_colors)}",
        action,
    ))
    if debug:
        bag_save_debug_image(ctx, screenshot, layout, action, "start")

    if scroll_to_top:
        for _ in range(max(1, int(action.get("scroll_to_top_swipes", 8)))):
            check_stop(ctx)
            before_layout = layout
            before_signature = bag_view_signature(screenshot, layout)
            if not bag_scroll_once(ctx, layout, action, toward_top=True):
                break
            time.sleep(scroll_wait_sec)
            new_screenshot = capture_device_screenshot(ctx)
            new_layout = bag_analyze_screen(ctx, new_screenshot, action)
            if new_layout is None:
                break
            after_signature = bag_view_signature(new_screenshot, new_layout)
            screenshot, layout = new_screenshot, new_layout
            if not bag_view_changed(before_layout, before_signature, new_layout, after_signature, stall_diff):
                break

    clicks = 0
    scrolls = 0
    steps = 0
    misses = 0
    scroll_stalls = 0
    max_attempts = max(1, int(action.get("max_click_attempts", 2)))
    failed_attempts: Dict[Tuple[int, int], int] = {}
    while True:
        check_stop(ctx)
        steps += 1
        if max_steps and steps > max_steps:
            log(with_action_remark(f"bag_appraise reached max_steps={max_steps}, stopping", action))
            break
        if layout is None:
            misses += 1
            if misses >= 3:
                raise BotError(
                    "bag_appraise lost the bag grid (a detail dialog may be open); check the game screen"
                )
            log(f"bag_appraise: bag grid not found (attempt {misses}), retrying ...")
            if misses >= 2 and recover_with_back:
                log("bag_appraise: sending BACK to dismiss a possible dialog")
                adb_shell(ctx, ["input", "keyevent", "4"])
                time.sleep(0.6)
            time.sleep(0.9)
            screenshot = capture_device_screenshot(ctx)
            layout = bag_analyze_screen(ctx, screenshot, action)
            continue
        misses = 0

        cell = bag_pick_target(layout, target_colors, failed_attempts, max_attempts)
        if cell is not None:
            scroll_stalls = 0
            before_signature = bag_view_signature(screenshot, layout)
            x, y = tap_absolute(ctx, int(cell["x"]), int(cell["y"]))
            clicks += 1
            log(with_action_remark(
                f"bag_appraise click {cell['color']} col={int(cell['column']) + 1} ({x}, {y}) "
                f"[{clicks}]",
                action,
            ))
            if max_clicks and clicks >= max_clicks:
                log(with_action_remark(f"bag_appraise reached max_clicks={max_clicks}, stopping", action))
                break
            time.sleep(click_wait_sec)
            check_stop(ctx)
            screenshot = capture_device_screenshot(ctx)
            new_layout = bag_analyze_screen(ctx, screenshot, action)
            if new_layout is not None:
                after_signature = bag_view_signature(screenshot, new_layout)
                if not bag_view_changed(layout, before_signature, new_layout, after_signature, stall_diff):
                    key = bag_cell_key(cell)
                    failed_attempts[key] = failed_attempts.get(key, 0) + 1
                    if failed_attempts[key] >= max_attempts:
                        log(f"bag_appraise: click had no effect after {max_attempts} attempt(s); skipping this cell")
                    else:
                        log("bag_appraise: click had no effect yet; retrying this cell")
                else:
                    failed_attempts.clear()
            layout = new_layout
            continue

        before_layout = layout
        before_signature = bag_view_signature(screenshot, layout)
        if not bag_scroll_once(ctx, layout, action, toward_top=False):
            log(with_action_remark("bag_appraise: grid is too small to scroll, finish", action))
            break
        scrolls += 1
        time.sleep(scroll_wait_sec)
        check_stop(ctx)
        screenshot = capture_device_screenshot(ctx)
        new_layout = bag_analyze_screen(ctx, screenshot, action)
        if new_layout is None:
            layout = new_layout
            continue
        after_signature = bag_view_signature(screenshot, new_layout)
        if not bag_view_changed(before_layout, before_signature, new_layout, after_signature, stall_diff):
            scroll_stalls += 1
            layout = new_layout
            if scroll_stalls < 2:
                log("bag_appraise: scroll had no visible effect; retrying once")
                continue
            log(with_action_remark(
                "bag_appraise: reached the list bottom, no orange/purple/blue left",
                action,
            ))
            break
        scroll_stalls = 0
        failed_attempts.clear()
        layout = new_layout

    counts: Dict[str, int] = {}
    if layout is not None:
        for row in layout["rows"]:
            for cell in row["cells"]:
                counts[cell["color"]] = counts.get(cell["color"], 0) + 1
    log(with_action_remark(
        f"bag_appraise finished: clicks={clicks} scrolls={scrolls} last_view={counts or 'n/a'}",
        action,
    ))


def execute_action(ctx: RunContext, action: Dict[str, Any]) -> None:
    check_stop(ctx)
    action_type = action.get("type")
    if action_type in {"find_image", "find_image_click"} and (action.get("then_actions") or action.get("else_actions")):
        log(with_action_remark(
            f"{action_type} ignores then_actions/else_actions; use if_image with click_match for branches",
            action,
        ))
    if action_type == "click":
        do_click(ctx, action)
    elif action_type == "long_press":
        do_long_press(ctx, action)
    elif action_type == "click_match":
        do_click_match(ctx, action)
    elif action_type == "record_draw_event":
        do_record_draw_event(ctx, action)
    elif action_type == "save_screenshot":
        do_save_screenshot(ctx, action)
    elif action_type == "analyze_chest_results":
        do_analyze_chest_results(ctx, action)
    elif action_type == "find_image":
        do_find_image(ctx, action)
    elif action_type == "find_image_click":
        do_find_image_click(ctx, action)
    elif action_type == "find_text_click":
        do_find_text_click(ctx, action)
    elif action_type == "if_text":
        execute_if_text(ctx, action)
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
    elif action_type == "joystick":
        do_joystick(ctx, action)
    elif action_type == "input":
        do_input(ctx, action)
    elif action_type in {"back", "home"}:
        do_input(ctx, {"command": action_type, **action})
    elif action_type in {"keyevent", "key"}:
        do_input(ctx, {"command": "keyevent", **action})
    elif action_type == "log":
        do_log_action(action)
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
    elif action_type == "background_check":
        execute_background_check(ctx, action)
    elif action_type == "grab_loop":
        execute_grab_loop(ctx, action)
    elif action_type == "if_background_hit":
        execute_if_background_hit(ctx, action)
    elif action_type == "patrol":
        execute_patrol(ctx, action)
    elif action_type == "bag_appraise":
        execute_bag_appraise(ctx, action)
    else:
        raise BotError(f"Unknown action type: {action_type}")


def execute_actions(ctx: RunContext, actions: List[Dict[str, Any]]) -> None:
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise BotError(
                f"Invalid action at index {index}: expected an object, got {type(action).__name__}. "
                "Remove any extra nested [] around actions."
            )
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


def _list_hdc_devices(hdc_path: str) -> List[str]:
    result = run_cmd([hdc_path, "list", "targets"], check=True)
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    skipped = ("[empty]", "empty", "list of targets")
    return [line.split()[0] for line in lines if not line.lower().startswith(skipped)]


def _is_device_shell_healthy(adb_path: str, device: str) -> bool:
    # Some BlueStacks builds are flaky on one-off shell probes; retry a few times.
    for _ in range(3):
        result = run_cmd([adb_path, "-s", device, "shell", "getprop", "ro.build.version.release"], check=False)
        output = f"{result.stdout}\n{result.stderr}".lower()
        if result.returncode == 0 and "error: closed" not in output:
            return True
        time.sleep(0.2)
    return False


def _is_hdc_shell_healthy(hdc_path: str, device: str) -> bool:
    result = run_cmd([hdc_path, "-t", device, "shell", "echo", "ok"], check=False)
    output = f"{result.stdout}\n{result.stderr}".lower()
    return result.returncode == 0 and "ok" in output


def ensure_hdc_device_connected(hdc_path: str, device: Optional[str]) -> Optional[str]:
    try:
        connected = _list_hdc_devices(hdc_path)
    except FileNotFoundError as exc:
        raise BotError(f"hdc not found: {hdc_path}") from exc

    if device is None:
        if not connected:
            raise BotError("No connected HarmonyOS/HDC device found. Check hdc list targets.")
        for target in connected:
            if _is_hdc_shell_healthy(hdc_path, target):
                log(f"Use HarmonyOS/HDC device: {target}")
                return target
        raise BotError("All connected HDC devices are unhealthy for hdc shell.")

    if device not in connected:
        raise BotError(f"Unable to find HarmonyOS/HDC device {device}. Check hdc list targets.")
    if _is_hdc_shell_healthy(hdc_path, device):
        return device
    raise BotError(f"HarmonyOS/HDC device shell unhealthy: {device}")


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


DEFAULT_RECOVERY_GRACE_SEC = 300.0
RECOVERY_PROBE_INTERVAL_SEC = 5.0
RECOVERY_NO_PROGRESS_WINDOW_SEC = 20.0
RECOVERY_MAX_NO_PROGRESS_RESUMES = 3


def device_ready_for_plan(ctx: RunContext) -> bool:
    """True once the backend answers shell and screenshot probes again."""
    if ctx.dry_run:
        return True
    try:
        if ctx.device_backend == "hdc":
            if ctx.device:
                return _is_hdc_shell_healthy(ctx.hdc_path, ctx.device)
            return bool(_list_hdc_devices(ctx.hdc_path))
        if ctx.device:
            if not _is_device_shell_healthy(ctx.adb_path, ctx.device):
                return False
            # Screenshots are what failed; only report recovery once screencap works.
            result = adb_exec_out_result(ctx, ["screencap", "-p"])
            return result.returncode == 0 and result.stdout.startswith(b"\x89PNG\r\n\x1a\n")
        return any(_is_device_shell_healthy(ctx.adb_path, serial) for serial in _list_connected_devices(ctx.adb_path))
    except Exception as error:
        log(f"Recovery probe failed: {error}")
        return False


def recover_device_connection(ctx: RunContext, grace_sec: float) -> bool:
    """Probe the device until it answers again or the grace window expires."""
    deadline = time.time() + max(0.0, grace_sec)
    if ctx.stop_at is not None:
        deadline = min(deadline, ctx.stop_at)
    attempt = 0
    while True:
        attempt += 1
        if ctx.device_backend == "adb" and ctx.device and not ctx.dry_run:
            run_cmd([ctx.adb_path, "connect", ctx.device], check=False)
        if device_ready_for_plan(ctx):
            if attempt > 1:
                log(f"Device answered again after {attempt} probe(s).")
            return True
        remaining = deadline - time.time()
        if remaining <= 0:
            return False
        if attempt == 2 and ctx.device_backend == "adb" and not ctx.dry_run:
            log("Reconnect did not help; restarting ADB server ...")
            recover_adb_server(ctx.adb_path, ctx.device)
        time.sleep(min(RECOVERY_PROBE_INTERVAL_SEC, max(0.5, remaining)))


def run_actions_with_recovery(ctx: RunContext, actions: List[Dict[str, Any]], grace_sec: float) -> None:
    """Run the plan; on device errors recover the connection instead of exiting.

    After a successful recovery the plan restarts from its first action — plans
    begin with state checks (red light / button detection), so re-entry is safe.
    The bot only stops when the grace window is exhausted, or when the plan
    keeps failing right after a resume (a real config/template problem).
    OSError (e.g. Windows locking adb.exe mid-spawn) is treated the same way.
    """
    if grace_sec <= 0 or ctx.dry_run:
        execute_actions(ctx, actions)
        return
    no_progress_resumes = 0
    resumed_at: Optional[float] = None
    while True:
        try:
            execute_actions(ctx, actions)
            return
        except (DeviceError, OSError) as exc:
            log(f"ERROR: {exc}")
            if resumed_at is not None and time.monotonic() - resumed_at <= RECOVERY_NO_PROGRESS_WINDOW_SEC:
                no_progress_resumes += 1
                if no_progress_resumes >= RECOVERY_MAX_NO_PROGRESS_RESUMES:
                    log("Plan keeps failing right after recovery; giving up.")
                    raise
            else:
                no_progress_resumes = 0
            log(f"Entering recovery mode: retrying the device connection for up to {int(grace_sec)}s ...")
            if not recover_device_connection(ctx, grace_sec):
                log("Device did not recover within the grace period; stopping.")
                raise
            resumed_at = time.monotonic()
            log("Device recovered; restarting the plan.")


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


SCREEN_PROBE_TRANSIENT_SEC = 120.0
SCREEN_PROBE_INTERVAL_SEC = 2.0
SCREEN_PROBE_SERVER_RESTART_SEC = 15.0


def get_device_screen_size(adb_path: str, device: Optional[str]) -> Tuple[int, int]:
    # Transient ADB states ("error: closed", offline, ...) at bot startup usually
    # mean the emulator's adbd is still settling (BlueStacks booting/restarting).
    # Retry patiently like the mid-run recovery instead of exiting after ~4s.
    # Non-transient output (wm size answered but unparseable) still fails fast.
    active_device = device
    last_text = ""
    attempt = 0
    fast_attempts = 5
    transient_deadline: Optional[float] = None
    last_server_restart = 0.0
    logged_failure = ""

    while True:
        attempt += 1
        cmd = [adb_path]
        if active_device:
            cmd += ["-s", active_device]
        cmd += ["shell", "wm", "size"]
        result = run_cmd(cmd, check=False)
        text = f"{result.stdout}\n{result.stderr}"
        last_text = text
        match = WM_SIZE_RE.search(text)
        if match:
            if attempt > 1:
                log(f"Screen size probe succeeded after {attempt} attempt(s).")
            return int(match.group(1)), int(match.group(2))

        now = time.monotonic()
        if transient_deadline is None:
            if not adb_output_is_transient(text):
                if attempt >= fast_attempts:
                    break
                time.sleep(0.3)
                continue
            transient_deadline = now + SCREEN_PROBE_TRANSIENT_SEC

        if now >= transient_deadline:
            break

        if now - last_server_restart >= SCREEN_PROBE_SERVER_RESTART_SEC:
            log("ADB screen size probe failed, restart adb server and retry ...")
            recover_adb_server(adb_path, active_device)
            last_server_restart = now
            try:
                active_device = ensure_device_connected(adb_path, active_device)
            except BotError as exc:
                log(f"Device reconnect during screen probe failed: {exc}")
        else:
            brief = " ".join(text.split())[:80]
            if brief != logged_failure:
                log(f"Screen size probe failed ({brief}); retrying for up to {int(transient_deadline - now)}s ...")
                logged_failure = brief
        time.sleep(SCREEN_PROBE_INTERVAL_SEC)

    raise BotError(f"Cannot parse device screen size from adb output:\n{last_text.strip()}")


def hdc_screenshot_result(hdc_path: str, device: Optional[str]) -> subprocess.CompletedProcess[bytes]:
    target = device or "default"
    remote_path = f"/data/local/tmp/bsmanager_screen_{os.getpid()}.png"
    with tempfile.TemporaryDirectory(prefix="bsmanager-hdc-screen-") as tmp_dir:
        local_path = Path(tmp_dir) / "screen.png"
        capture_cmd = [hdc_path]
        if device:
            capture_cmd += ["-t", device]
        capture_cmd += ["shell", "uitest", "screenCap", "-p", remote_path]
        capture = run_cmd(capture_cmd, check=False)
        if capture.returncode != 0:
            text = f"{capture.stdout}\n{capture.stderr}".strip()
            return subprocess.CompletedProcess(args=capture_cmd, returncode=capture.returncode, stdout=b"", stderr=f"Cannot capture HDC screen for {target}:\n{text}".encode("utf-8"))

        recv_cmd = [hdc_path]
        if device:
            recv_cmd += ["-t", device]
        recv_cmd += ["file", "recv", remote_path, str(local_path)]
        recv = run_cmd(recv_cmd, check=False)
        cleanup_cmd = [hdc_path]
        if device:
            cleanup_cmd += ["-t", device]
        cleanup_cmd += ["shell", "rm", "-f", remote_path]
        run_cmd(cleanup_cmd, check=False)
        if recv.returncode != 0:
            text = f"{recv.stdout}\n{recv.stderr}".strip()
            return subprocess.CompletedProcess(args=recv_cmd, returncode=recv.returncode, stdout=b"", stderr=f"Cannot fetch HDC screen for {target}:\n{text}".encode("utf-8"))

        try:
            return subprocess.CompletedProcess(args=recv_cmd, returncode=0, stdout=local_path.read_bytes(), stderr=b"")
        except OSError as exc:
            return subprocess.CompletedProcess(args=recv_cmd, returncode=1, stdout=b"", stderr=f"Cannot read HDC screenshot for {target}: {exc}".encode("utf-8"))


def get_hdc_device_screen_size(hdc_path: str, device: Optional[str]) -> Tuple[int, int]:
    result = hdc_screenshot_result(hdc_path, device)
    if result.returncode != 0:
        text = (result.stderr or b"").decode("utf-8", errors="ignore").strip()
        raise BotError(text or f"Cannot capture HDC screen for {device or 'default'}")
    try:
        image = Image.open(io.BytesIO(result.stdout))
        return int(image.width), int(image.height)
    except Exception as exc:
        raise BotError(f"Cannot decode HDC screenshot size for {device or 'default'}: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="BlueStacks/HarmonyOS automation bot")
    parser.add_argument("--plan", required=True, help="Path to plan JSON file")
    parser.add_argument("--device", help="Device serial/target, e.g. 127.0.0.1:5555 or HarmonyOS target with HDC suffix")
    parser.add_argument("--user-id", default="default", help="Chest user identifier")
    parser.add_argument("--source-id", default="", help="Chest source identifier")
    parser.add_argument("--source-name", default="", help="Chest source display name")
    parser.add_argument("--source-file", help="Optional JSON file used to switch the active chest source")
    temporary_mode = parser.add_mutually_exclusive_group()
    temporary_mode.add_argument(
        "--skip-magnifier",
        action="store_true",
        help="Temporarily skip the magnifier button and enter the confirmation flow directly",
    )
    temporary_mode.add_argument(
        "--capture-current-chest",
        action="store_true",
        help="Immediately save the current chest item list without image matching or taps",
    )
    parser.add_argument("--adb", default="adb", help="ADB binary path")
    parser.add_argument("--hdc", default="hdc", help="HDC binary path for HarmonyOS devices")
    parser.add_argument("--device-backend", choices=["auto", "adb", "hdc"], default="auto", help="Device backend")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without executing")
    parser.add_argument(
        "--image-match-debug",
        action="store_true",
        help="Save image match debug overlays by default (per-action save_debug still overrides)",
    )
    parser.add_argument("--max-runtime-sec", type=int, default=0, help="Stop after N seconds (0 means unlimited)")
    parser.add_argument(
        "--recovery-grace-sec",
        type=float,
        default=None,
        help="On device/ADB failure keep retrying recovery for N seconds before stopping (default 300, 0 disables)",
    )
    parser.add_argument("--verify-template", nargs="+", help="Capture one screen and verify one or more image templates")
    parser.add_argument("--verify-region", nargs=4, type=int, metavar=("X", "Y", "WIDTH", "HEIGHT"))
    parser.add_argument("--verify-threshold", type=float, default=0.88)
    args = parser.parse_args()

    plan_path = Path(args.plan).expanduser().resolve()
    plan = load_plan(plan_path)
    default_device = plan.get("device")
    cli_device = args.device.strip() if isinstance(args.device, str) else None
    plan_device = default_device.strip() if isinstance(default_device, str) else default_device
    raw_device = cli_device or plan_device or None
    device_backend, device = split_device_backend(raw_device, args.device_backend)
    jitter = int(plan.get("jitter_px", 0))
    trace_time_scale = float(plan.get("trace_time_scale", 1.0))
    runtime_limit = args.max_runtime_sec or int(plan.get("max_runtime_sec", 0))
    stop_at = None if runtime_limit <= 0 else time.time() + runtime_limit
    raw_recovery_grace = args.recovery_grace_sec if args.recovery_grace_sec is not None else plan.get("recovery_grace_sec", DEFAULT_RECOVERY_GRACE_SEC)
    try:
        recovery_grace_sec = max(0.0, float(raw_recovery_grace))
    except (TypeError, ValueError):
        raise BotError(f"recovery_grace_sec must be a number, got {raw_recovery_grace!r}")
    screen_cfg = plan.get("screen_size", {})
    has_screen_cfg = isinstance(screen_cfg, dict) and "width" in screen_cfg and "height" in screen_cfg
    if has_screen_cfg:
        src_screen_w = int(screen_cfg.get("width", 1080))
        src_screen_h = int(screen_cfg.get("height", 1920))
    else:
        src_screen_w = DEFAULT_SCREEN_WIDTH
        src_screen_h = DEFAULT_SCREEN_HEIGHT
    dst_screen_w = -1
    dst_screen_h = -1

    if not args.dry_run:
        if device_backend == "hdc":
            device = ensure_hdc_device_connected(args.hdc, device)
            dst_screen_w, dst_screen_h = get_hdc_device_screen_size(args.hdc, device)
            log("HarmonyOS/HDC backend enabled; screenshots use uitest screenCap and input uses uiInput.")
        else:
            device = ensure_device_connected(args.adb, device)
            dst_screen_w, dst_screen_h = get_device_screen_size(args.adb, device)
            if not has_screen_cfg:
                src_screen_w, src_screen_h = DEFAULT_SCREEN_WIDTH, DEFAULT_SCREEN_HEIGHT
    elif not has_screen_cfg:
        dst_screen_w, dst_screen_h = DEFAULT_SCREEN_WIDTH, DEFAULT_SCREEN_HEIGHT

    ctx = RunContext(
        adb_path=args.adb,
        hdc_path=args.hdc,
        device_backend=device_backend,
        device=device,
        user_id=str(args.user_id or "default"),
        source_id=str(args.source_id or ""),
        source_name=str(args.source_name or ""),
        source_file=Path(args.source_file).expanduser().resolve() if args.source_file else None,
        dry_run=args.dry_run,
        jitter=jitter,
        stop_at=stop_at,
        src_screen_w=src_screen_w,
        src_screen_h=src_screen_h,
        dst_screen_w=dst_screen_w,
        dst_screen_h=dst_screen_h,
        trace_time_scale=trace_time_scale,
        plan_dir=plan_path.parent,
        image_match_debug=args.image_match_debug,
    )

    if args.verify_template:
        if not args.verify_region or args.verify_region[2] <= 0 or args.verify_region[3] <= 0:
            raise BotError("--verify-region requires positive X Y WIDTH HEIGHT")
        result = verify_template_once(
            ctx,
            args.verify_template,
            {"x": args.verify_region[0], "y": args.verify_region[1], "width": args.verify_region[2], "height": args.verify_region[3]},
            max(0.1, min(1.0, args.verify_threshold)),
        )
        print("__DRAW_TEMPLATE_VERIFY__" + json.dumps(result, ensure_ascii=False), flush=True)
        return 0

    actions = plan.get("actions")
    if not isinstance(actions, list):
        raise BotError("Plan must contain an 'actions' array")
    if args.skip_magnifier:
        actions = skip_magnifier_actions(actions)
        if not actions:
            raise BotError("Plan does not contain a magnifier confirmation flow")
        log("Temporary override enabled: skip magnifier detection")
    elif args.capture_current_chest:
        actions = current_chest_capture_actions(actions)
        if not actions:
            raise BotError("Plan does not contain a chest_result save_screenshot action")
        log("Temporary override enabled: save current chest items immediately")
    if not actions:
        raise BotError(f"Plan contains no actions: {plan_path}")

    log("Bot start")
    log(f"Device backend={ctx.device_backend} device={ctx.device or 'default'}")
    log(f"Config jitter_px={ctx.jitter}")
    log(f"Config trace_time_scale={ctx.trace_time_scale}")
    log(f"Screen scale {ctx.src_screen_w}x{ctx.src_screen_h} -> {ctx.dst_screen_w}x{ctx.dst_screen_h}")
    log(f"Config recovery_grace_sec={recovery_grace_sec:g}")
    try:
        run_actions_with_recovery(ctx, actions, recovery_grace_sec)
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
