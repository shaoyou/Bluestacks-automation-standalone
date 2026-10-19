#!/usr/bin/env python3
"""Benchmark adb_bot image matching with real screenshots and templates.

Measures find_image_match under the two plan workloads that dominate runtime:
full-screen multi-template if_image (刷金币) and region-based multi-scale
find_image_click (开宝箱), plus a per-stage breakdown of the current pipeline.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image

import adb_bot as bot

ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT = ROOT / "diagnostics" / "choukaka_current_harmony2.png"  # 1260x2720


def make_ctx(screen_w: int, screen_h: int) -> bot.RunContext:
    return bot.RunContext(
        adb_path="adb",
        hdc_path="hdc",
        device_backend="adb",
        device="emulator",
        user_id="bench",
        source_id="bench",
        source_name="bench",
        source_file=None,
        dry_run=True,
        jitter=0,
        stop_at=None,
        src_screen_w=screen_w,
        src_screen_h=screen_h,
        dst_screen_w=screen_w,
        dst_screen_h=screen_h,
        trace_time_scale=1.0,
        plan_dir=ROOT,
    )


def stage_timings(search: bot.PreparedSearchImage, variants, action, fft_shape):
    timers = {
        "search_fft": 0.0,
        "integrals": 0.0,
        "template_fft": 0.0,
        "box_sums+numerator": 0.0,
        "score_map": 0.0,
        "peaks": 0.0,
        "color": 0.0,
    }
    per_scale_peaks = int(action.get("candidate_peaks", 16))
    search_gray = search.gray
    image_h, image_w = search_gray.shape

    t0 = time.perf_counter()
    gray_fft = bot.ensure_search_gray_fft(search, fft_shape)
    timers["search_fft"] += time.perf_counter() - t0
    t0 = time.perf_counter()
    integral_gray, integral_sq = bot.ensure_search_integrals(search)
    timers["integrals"] += time.perf_counter() - t0

    for variant in variants:
        kernel_shape = (variant.height, variant.width)
        t0 = time.perf_counter()
        _, centered_fft = bot.ensure_template_ffts(variant, fft_shape)
        timers["template_fft"] += time.perf_counter() - t0

        t0 = time.perf_counter()
        if variant.is_opaque:
            sum_i = bot.box_sums_valid(integral_gray, variant.height, variant.width)
            sum_i2 = bot.box_sums_valid(integral_sq, variant.height, variant.width)
            numerator = bot.fft_correlate2d_valid_from_product(gray_fft * centered_fft, search_gray.shape, kernel_shape, fft_shape)
        else:
            weights_fft, centered_fft = bot.ensure_template_ffts(variant, fft_shape)
            squared_fft = bot.ensure_search_squared_fft(search, fft_shape)
            sum_i = bot.fft_correlate2d_valid_from_product(gray_fft * weights_fft, search_gray.shape, kernel_shape, fft_shape)
            sum_i2 = bot.fft_correlate2d_valid_from_product(squared_fft * weights_fft, search_gray.shape, kernel_shape, fft_shape)
            numerator = bot.fft_correlate2d_valid_from_product(gray_fft * centered_fft, search_gray.shape, kernel_shape, fft_shape)
        timers["box_sums+numerator"] += time.perf_counter() - t0

        t0 = time.perf_counter()
        variance = np.maximum(sum_i2.astype(np.float64) - (sum_i.astype(np.float64) * sum_i.astype(np.float64)) / variant.weights_sum, 1e-6)
        scores = numerator.astype(np.float64) / (np.sqrt(variance) * variant.norm)
        scores = np.clip(np.nan_to_num(scores, nan=-1.0, posinf=-1.0, neginf=-1.0), -1.0, 1.0).astype(np.float32)
        timers["score_map"] += time.perf_counter() - t0

        t0 = time.perf_counter()
        peaks = bot.extract_peak_candidates(
            scores,
            count=per_scale_peaks,
            suppress_x=max(4, variant.width // 3),
            suppress_y=max(4, variant.height // 3),
        )
        timers["peaks"] += time.perf_counter() - t0

        t0 = time.perf_counter()
        for peak_x, peak_y, _ in peaks:
            bot.weighted_color_similarity(search.rgb, variant.rgb, variant.weights, peak_x, peak_y)
        timers["color"] += time.perf_counter() - t0
    return timers


def run_scenario(name, screen, region, template_names, action_base, repeats, show_stages):
    ctx = make_ctx(screen.size[0], screen.size[1])
    for template_name in template_names:
        bot.load_template_image(ROOT / "image_templates" / template_name)
    action = dict(action_base)
    if region:
        action["region"] = region
        action["region_is_device_coordinates"] = True
    template_paths = [ROOT / "image_templates" / n for n in template_names]
    search_action = bot.image_search_action(action, template_paths)
    minimum = bot.required_template_search_size(ctx, template_paths, search_action)
    print(f"\n=== {name} (screen {screen.size[0]}x{screen.size[1]}, {len(template_names)} template(s))")
    match_times = []
    best = None
    for i in range(repeats):
        t0 = time.perf_counter()
        search = bot.prepare_search_image(ctx, screen, search_action, minimum_size=minimum)
        search_size = (search.gray.shape[1], search.gray.shape[0])
        variants_flat = []
        for tp in template_paths:
            variants_flat.extend(bot.get_prepared_template_variants(ctx, tp, search_action, search_size))
        t1 = time.perf_counter()
        _, best = bot.find_image_match(search, variants_flat, action_base.get("threshold", 0.88), 0, search_action)
        t2 = time.perf_counter()
        match_times.append(t2 - t1)
        if i == 0:
            print(f"  search region: {search_size[0]}x{search_size[1]}, variants: {len(variants_flat)}")
            if show_stages:
                fft_shape = bot.shared_search_fft_shape(search, variants_flat)
                for key, value in stage_timings(search, variants_flat, search_action, fft_shape).items():
                    print(f"    {key:20s} {value * 1000:8.1f} ms")
    warm = match_times[1:] or match_times
    print(f"  find_image_match: first={match_times[0] * 1000:.1f} ms, warm avg={sum(warm) / len(warm) * 1000:.1f} ms")
    if best:
        print(f"  best similarity={float(best['similarity']):.4f} at ({int(best['x'])},{int(best['y'])})")
    return match_times


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=4)
    parser.add_argument("--stages", action="store_true", help="print per-stage timings of the current pipeline")
    args = parser.parse_args()

    screen = Image.open(SCREENSHOT).convert("RGB")
    print(f"source screenshot: {SCREENSHOT.name} {screen.size}")

    # 刷金币: full-screen if_image, 3 role templates, single scale, accurate profile.
    run_scenario(
        "刷金币 if_image (accurate)",
        screen,
        None,
        ["role_kakaxi.png", "role_libai.png", "role_shengqishi.png"],
        {"threshold": 0.8, "scales": [1.0], "match_profile": "accurate", "max_candidates": 8},
        args.repeats,
        args.stages,
    )

    # 开宝箱: region search, cancel template, default 5 scales, balanced profile.
    run_scenario(
        "开宝箱 find_image_click (balanced, 5 scales)",
        screen,
        {"x": 216, "y": 764, "width": 828, "height": 1191},
        ["role_cancel_harmony_1260x2720.png", "role_count_max_harmony_1260x2720.png", "bl_baoxiaoxing_harmony_1260x2720.png"],
        {"threshold": 0.88, "match_profile": "balanced", "max_candidates": 8},
        args.repeats,
        args.stages,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
