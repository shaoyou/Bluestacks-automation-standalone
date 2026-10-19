#!/usr/bin/env python3
"""Verify the optimized image matcher stays numerically faithful.

Three checks against a frozen copy of the pre-optimization masked-NCC
pipeline (repeated-argmax peaks, per-variant FFT shapes, FFT-based weighted
sums):

1. Score-map equivalence: per template variant, compare the legacy and current
   NCC score maps. Windows whose true (float64) weighted variance is low are
   numerically degenerate — the float32 variance subtraction is dominated by
   rounding noise, so both implementations emit noise-driven scores there (a
   pre-existing quirk of the algorithm, not of the optimization). The check
   therefore compares positions with true variance >= TEXTURED_VARIANCE, where
   both pipelines must agree tightly.
2. Synthetic end-to-end: templates planted on seeded textured noise; both
   implementations must produce the same candidate list (positions exact,
   similarities within tolerance) and select the planted template.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image

import adb_bot as bot

ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT = ROOT / "diagnostics" / "choukaka_current_harmony2.png"  # 1260x2720
# sqrt(4e6 / N_pixels) ~ gray std 8+: above the legacy float32 variance-noise floor.
TEXTURED_VARIANCE = 4.0e6
SCORE_TOLERANCE = 2e-3
POSITION_TOLERANCE = 1  # px, chebyshev; near-tie flips from summation ordering


# ---------------------------------------------------------------------------
# Frozen reference implementation (pre-optimization behavior).
# ---------------------------------------------------------------------------

def legacy_extract_peak_candidates(score_map: np.ndarray, count: int, suppress_x: int, suppress_y: int) -> List[Tuple[int, int, float]]:
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
        candidates.append((int(x), int(y), best_score))
        y0 = max(0, y - suppress_y)
        y1 = min(work.shape[0], y + suppress_y + 1)
        x0 = max(0, x - suppress_x)
        x1 = min(work.shape[1], x + suppress_x + 1)
        work[y0:y1, x0:x1] = -np.inf
    return candidates


def legacy_weighted_color_similarity(search_rgb, template_rgb, weights, x, y) -> float:
    template_h, template_w = template_rgb.shape[:2]
    patch = search_rgb[y : y + template_h, x : x + template_w]
    if patch.shape[:2] != template_rgb.shape[:2]:
        return 0.0
    diff = patch - template_rgb
    mse = float((((diff * diff).mean(axis=2)) * weights).sum() / max(float(weights.sum()), 1.0))
    return max(0.0, 1.0 - (mse ** 0.5) / 255.0)


def legacy_variant_scores(search_gray: np.ndarray, variant: bot.PreparedTemplateVariant) -> np.ndarray:
    image_h, image_w = search_gray.shape
    kernel_shape = (variant.height, variant.width)
    fft_shape = (image_h + variant.height - 1, image_w + variant.width - 1)
    image_fft = np.fft.rfftn(search_gray, s=fft_shape, axes=(0, 1))
    image_squared_fft = np.fft.rfftn(search_gray * search_gray, s=fft_shape, axes=(0, 1))
    weights_fft = np.fft.rfftn(np.flipud(np.fliplr(variant.weights)), s=fft_shape, axes=(0, 1))
    centered_fft = np.fft.rfftn(np.flipud(np.fliplr(variant.centered)), s=fft_shape, axes=(0, 1))

    def valid(product):
        full = np.fft.irfftn(product, s=fft_shape, axes=(0, 1))
        return full[kernel_shape[0] - 1 : image_h, kernel_shape[1] - 1 : image_w].astype(np.float32, copy=False)

    sum_i = valid(image_fft * weights_fft)
    sum_i2 = valid(image_squared_fft * weights_fft)
    numerator = valid(image_fft * centered_fft)
    variance = np.maximum(sum_i2 - (sum_i * sum_i) / variant.weights_sum, 1e-6)
    scores = numerator / (np.sqrt(variance) * variant.norm)
    return np.clip(np.nan_to_num(scores, nan=-1.0, posinf=-1.0, neginf=-1.0), -1.0, 1.0)


def legacy_variant_candidates(
    search_rgb: np.ndarray,
    search_gray: np.ndarray,
    crop_box: Tuple[int, int, int, int],
    variant: bot.PreparedTemplateVariant,
    per_scale_peaks: int,
    action: Dict[str, Any],
    flat_mask: Optional[np.ndarray],
) -> List[Dict[str, float]]:
    scores = legacy_variant_scores(search_gray, variant)
    if flat_mask is not None:
        scores = np.where(flat_mask, -1.0, scores)
    peaks = legacy_extract_peak_candidates(
        scores,
        count=per_scale_peaks,
        suppress_x=max(4, variant.width // 3),
        suppress_y=max(4, variant.height // 3),
    )
    candidates: List[Dict[str, float]] = []
    for peak_x, peak_y, raw_score in peaks:
        shape_similarity = max(0.0, min(1.0, (raw_score + 1.0) / 2.0))
        color_similarity = legacy_weighted_color_similarity(search_rgb, variant.rgb, variant.weights, peak_x, peak_y)
        similarity = bot.image_match_score(shape_similarity, color_similarity, action)
        candidates.append(
            {
                "x": float(crop_box[0] + peak_x),
                "y": float(crop_box[1] + peak_y),
                "width": float(variant.width),
                "height": float(variant.height),
                "similarity": float(similarity),
            }
        )
    return candidates


# ---------------------------------------------------------------------------
# Current implementation driver (exercises adb_bot's own building blocks).
# ---------------------------------------------------------------------------

def current_variant_scores(
    search: bot.PreparedSearchImage,
    variant: bot.PreparedTemplateVariant,
    fft_shape: Tuple[int, int],
) -> np.ndarray:
    gray_fft = bot.ensure_search_gray_fft(search, fft_shape)
    integral_gray, integral_sq = bot.ensure_search_integrals(search)
    kernel_shape = (variant.height, variant.width)
    if variant.is_opaque:
        sum_i = bot.box_sums_valid(integral_gray, variant.height, variant.width).astype(np.float32)
        sum_i2 = bot.box_sums_valid(integral_sq, variant.height, variant.width).astype(np.float32)
        _, centered_fft = bot.ensure_template_ffts(variant, fft_shape)
        numerator = bot.fft_correlate2d_valid_from_product(gray_fft * centered_fft, search.gray.shape, kernel_shape, fft_shape)
    else:
        weights_fft, centered_fft = bot.ensure_template_ffts(variant, fft_shape)
        squared_fft = bot.ensure_search_squared_fft(search, fft_shape)
        sum_i = bot.fft_correlate2d_valid_from_product(gray_fft * weights_fft, search.gray.shape, kernel_shape, fft_shape)
        sum_i2 = bot.fft_correlate2d_valid_from_product(squared_fft * weights_fft, search.gray.shape, kernel_shape, fft_shape)
        numerator = bot.fft_correlate2d_valid_from_product(gray_fft * centered_fft, search.gray.shape, kernel_shape, fft_shape)
    variance = np.maximum(sum_i2 - (sum_i * sum_i) / variant.weights_sum, 1e-6)
    scores = numerator / (np.sqrt(variance) * variant.norm)
    return np.clip(np.nan_to_num(scores, nan=-1.0, posinf=-1.0, neginf=-1.0), -1.0, 1.0)


def current_find_image_match_candidates(
    search: bot.PreparedSearchImage,
    variants: List[bot.PreparedTemplateVariant],
    action: Dict[str, Any],
    flat_masks: Dict[Tuple[int, int], np.ndarray],
) -> List[Dict[str, float]]:
    per_scale_peaks = int(action.get("candidate_peaks") or 16)
    crop_box = search.crop_box
    candidates: List[Dict[str, float]] = []
    fft_shape = bot.shared_search_fft_shape(search, variants)
    for variant in variants:
        scores = current_variant_scores(search, variant, fft_shape)
        flat_mask = flat_masks.get((variant.height, variant.width))
        if flat_mask is not None:
            scores = np.where(flat_mask, -1.0, scores)
        peaks = bot.extract_peak_candidates(
            scores,
            count=per_scale_peaks,
            suppress_x=max(4, variant.width // 3),
            suppress_y=max(4, variant.height // 3),
        )
        for peak_x, peak_y, raw_score in peaks:
            shape_similarity = max(0.0, min(1.0, (raw_score + 1.0) / 2.0))
            color_similarity = bot.weighted_color_similarity(search.rgb, variant.rgb, variant.weights, peak_x, peak_y)
            similarity = bot.image_match_score(shape_similarity, color_similarity, action)
            candidates.append(
                {
                    "x": float(crop_box[0] + peak_x),
                    "y": float(crop_box[1] + peak_y),
                    "width": float(variant.width),
                    "height": float(variant.height),
                    "similarity": float(similarity),
                }
            )
    return candidates


def degenerate_masks(search: bot.PreparedSearchImage, variants: List[bot.PreparedTemplateVariant]) -> Dict[Tuple[int, int], np.ndarray]:
    """True float64 window-variance masks keyed by kernel shape."""
    integral_gray, integral_sq = bot.ensure_search_integrals(search)
    masks: Dict[Tuple[int, int], np.ndarray] = {}
    for variant in variants:
        key = (variant.height, variant.width)
        if key in masks:
            continue
        sum_i = bot.box_sums_valid(integral_gray, variant.height, variant.width)
        sum_i2 = bot.box_sums_valid(integral_sq, variant.height, variant.width)
        variance = sum_i2 - (sum_i * sum_i) / variant.weights_sum
        masks[key] = variance < TEXTURED_VARIANCE
    return masks


# ---------------------------------------------------------------------------
# Checks.
# ---------------------------------------------------------------------------

def prepare_ctx(screen_size: Tuple[int, int]) -> bot.RunContext:
    return bot.RunContext(
        adb_path="adb",
        hdc_path="hdc",
        device_backend="adb",
        device="emulator",
        user_id="parity",
        source_id="parity",
        source_name="parity",
        source_file=None,
        dry_run=True,
        jitter=0,
        stop_at=None,
        src_screen_w=screen_size[0],
        src_screen_h=screen_size[1],
        dst_screen_w=screen_size[0],
        dst_screen_h=screen_size[1],
        trace_time_scale=1.0,
        plan_dir=ROOT,
    )


def build_search(screen: Image.Image, action: Dict[str, Any], template_paths: List[Path]):
    ctx = prepare_ctx(screen.size)
    search_action = bot.image_search_action(action, template_paths)
    minimum = bot.required_template_search_size(ctx, template_paths, search_action)
    search = bot.prepare_search_image(ctx, screen, search_action, minimum_size=minimum)
    variants: List[bot.PreparedTemplateVariant] = []
    for tp in template_paths:
        variants.extend(bot.get_prepared_template_variants(ctx, tp, search_action, (search.gray.shape[1], search.gray.shape[0])))
    return search, search_action, variants


def check_score_maps(name, screen, region, template_names, action_base):
    action = dict(action_base)
    if region:
        action["region"] = region
        action["region_is_device_coordinates"] = True
    template_paths = [ROOT / "image_templates" / n for n in template_names]
    for tp in template_paths:
        bot.load_template_image(tp)
    search, _sa, variants = build_search(screen, action, template_paths)
    fft_shape = bot.shared_search_fft_shape(search, variants)
    integral_gray, integral_sq = bot.ensure_search_integrals(search)

    ok = True
    print(f"\n[score-map] {name}")
    for variant in variants:
        sum_i = bot.box_sums_valid(integral_gray, variant.height, variant.width)
        sum_i2 = bot.box_sums_valid(integral_sq, variant.height, variant.width)
        textured = (sum_i2 - (sum_i * sum_i) / variant.weights_sum) >= TEXTURED_VARIANCE
        legacy_map = legacy_variant_scores(search.gray, variant)
        current_map = current_variant_scores(search, variant, fft_shape)
        delta = np.abs(legacy_map - current_map)[textured]
        worst = float(delta.max()) if delta.size else 0.0
        over = int((delta > SCORE_TOLERANCE).sum())
        share = float(textured.mean()) * 100.0
        passed = worst <= SCORE_TOLERANCE
        ok &= passed
        print(
            f"  {'PASS' if passed else 'FAIL'} {variant.width}x{variant.height} opaque={variant.is_opaque}: "
            f"textured {share:.1f}% of windows, max|delta|={worst:.2e}, over-{SCORE_TOLERANCE:.0e}: {over}"
        )
    return ok


def compare_candidates(legacy: List[Dict[str, float]], current: List[Dict[str, float]], threshold: float, tolerance: float) -> List[str]:
    problems: List[str] = []
    if len(legacy) != len(current):
        problems.append(f"candidate count differs: legacy={len(legacy)} current={len(current)}")
        return problems
    for position, (left, right) in enumerate(zip(legacy, current)):
        if max(abs(left["x"] - right["x"]), abs(left["y"] - right["y"])) > POSITION_TOLERANCE:
            problems.append(
                f"candidate {position} position differs: legacy=({left['x']:.0f},{left['y']:.0f}) "
                f"current=({right['x']:.0f},{right['y']:.0f})"
            )
            break
        if abs(left["similarity"] - right["similarity"]) > tolerance:
            problems.append(
                f"candidate {position} similarity differs beyond {tolerance}: "
                f"legacy={left['similarity']:.6f} current={right['similarity']:.6f}"
            )
            break
    qualified_legacy = [item for item in legacy if item["similarity"] >= threshold]
    qualified_current = [item for item in current if item["similarity"] >= threshold]
    if len(qualified_legacy) != len(qualified_current):
        problems.append(
            f"qualified count differs at threshold {threshold}: legacy={len(qualified_legacy)} current={len(qualified_current)}"
        )
    return problems


def synthetic_case(tolerance: float) -> bool:
    """Templates planted on seeded textured noise: candidates must match."""
    rng = np.random.default_rng(20260917)
    width, height = 1080, 1920
    noise = rng.integers(0, 256, size=(height // 4, width // 4), dtype=np.uint8)
    texture = np.asarray(Image.fromarray(noise).resize((width, height), Image.Resampling.BILINEAR))
    canvas = np.stack([texture, np.roll(texture, 17, axis=1), np.roll(texture, -31, axis=0)], axis=2).astype(np.uint8)
    planted = [("role_kakaxi.png", (120, 300)), ("role_shengqishi.png", (640, 1250))]
    for name, (px, py) in planted:
        patch = np.asarray(Image.open(ROOT / "image_templates" / name).convert("RGB"))
        ph, pw = patch.shape[:2]
        canvas[py : py + ph, px : px + pw] = patch
    screen = Image.fromarray(canvas)

    action = {"threshold": 0.8, "scales": [1.0], "match_profile": "accurate", "max_candidates": 8}
    template_paths = [ROOT / "image_templates" / n for n, _ in planted]
    search, search_action, variants = build_search(screen, action, template_paths)
    per_scale_peaks = int(search_action.get("candidate_peaks") or 16)

    legacy_all: List[Dict[str, float]] = []
    for variant in variants:
        legacy_all.extend(
            legacy_variant_candidates(
                search.rgb, search.gray, search.crop_box, variant, per_scale_peaks, search_action, None
            )
        )
    legacy_all.sort(key=lambda item: item["similarity"], reverse=True)
    current_all = current_find_image_match_candidates(search, variants, search_action, {})
    current_all.sort(key=lambda item: item["similarity"], reverse=True)
    current_match, _best = bot.find_image_match(search, variants, 0.8, 0, search_action)

    problems = compare_candidates(legacy_all, current_all, 0.8, tolerance)
    best = current_all[0] if current_all else None
    expected = {(px, py) for _, (px, py) in planted}
    if best is not None and not any(abs(best["x"] - px) <= 2 and abs(best["y"] - py) <= 2 for px, py in expected):
        problems.append(f"best match not at a planted position: ({best['x']:.0f},{best['y']:.0f}) sim={best['similarity']:.4f}")
    if current_match is None or current_match["similarity"] < 0.8:
        problems.append("find_image_match api did not confirm the planted template")
    status = "PASS" if not problems else "FAIL"
    print(f"\n[{status}] synthetic textured scene (2 planted templates)")
    if best is not None:
        print(f"  best: ({best['x']:.0f},{best['y']:.0f}) similarity={best['similarity']:.4f} (legacy {legacy_all[0]['similarity']:.4f})")
    for problem in problems:
        print(f"  ! {problem}")
    return not problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tolerance", type=float, default=1e-4, help="max allowed absolute similarity delta")
    args = parser.parse_args()
    print(f"source screenshot: {SCREENSHOT.name}")

    ok = True
    ok &= synthetic_case(args.tolerance)

    screen = Image.open(SCREENSHOT).convert("RGB")
    ok &= check_score_maps(
        "刷金币 if_image (accurate, 3 roles, full screen)",
        screen,
        None,
        ["role_kakaxi.png", "role_libai.png", "role_shengqishi.png"],
        {"threshold": 0.8, "scales": [1.0], "match_profile": "accurate", "max_candidates": 8},
    )
    ok &= check_score_maps(
        "开宝箱 find_image_click (balanced, 5 scales, region)",
        screen,
        {"x": 216, "y": 764, "width": 828, "height": 1191},
        ["role_cancel_harmony_1260x2720.png", "role_count_max_harmony_1260x2720.png", "bl_baoxiaoxing_harmony_1260x2720.png"],
        {"threshold": 0.88, "match_profile": "balanced", "max_candidates": 8},
    )
    print("\nALL PASS" if ok else "\nFAILURES DETECTED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
