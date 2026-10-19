#!/usr/bin/env python3
"""Diagnose chest item-crop alignment and quantity-glyph quality.

Runs the shipped chest_analyzer slot detection over every saved chest capture
and measures how well each detected tile aligns with the game's dark tile
frame, then clusters the extracted quantity glyphs so digit-level problems
(1/4 confusion, false positives, missing numbers) can be quantified without
manual labels.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image

import chest_analyzer as ca

RESULTS = Path("/Users/admins/Library/Application Support/bs-manager-cross-platform/runtime/diagnostics/chest_results")


def slot_alignment_metrics(image: Image.Image, slots) -> list:
    rgb = np.asarray(image.convert("RGB")).astype(np.float32)
    out = []
    for left, top, size in slots:
        if top <= 0 or left <= 0 or top + size > rgb.shape[0] or left + size > rgb.shape[1]:
            out.append({"left": left, "top": top, "size": size, "oob": True})
            continue
        tile = rgb[top:top + size, left:left + size]
        gray = tile.mean(axis=2)
        # A well-aligned tile has a continuous dark frame at every edge.
        border_darkness = [
            float(gray[0].mean()),
            float(gray[-1].mean()),
            float(gray[:, 0].mean()),
            float(gray[:, -1].mean()),
        ]
        # Where is the darkest horizontal line near the top edge?
        probe = gray[: max(10, size // 8)]
        darkest_row = int(np.argmin(probe.mean(axis=1)))
        # Background above the tile (should be brighter than the frame line).
        above = rgb[max(0, top - 4):top, left:left + size].mean()
        interior = tile[size // 4: 3 * size // 4, size // 4: 3 * size // 4]
        color_ratio = float(((interior.max(axis=2) - interior.min(axis=2) > 70) & (interior.max(axis=2) > 130)).mean())
        out.append({
            "left": left, "top": top, "size": size,
            "border": [round(v, 1) for v in border_darkness],
            "darkest_row_in_top10": darkest_row,
            "above_brightness": round(float(above), 1),
            "interior_color_ratio": round(color_ratio, 3),
        })
    return out


def main() -> int:
    metadata = ca.screenshot_metadata(RESULTS)
    paths = [p for p in ca.chest_screenshot_paths(RESULTS)]
    print(f"captures: {len(paths)}")

    slot_counts = Counter()
    no_slots = []
    darkest_rows = Counter()
    misaligned = []
    zero_dark_top = 0
    total_slots = 0
    for path in paths:
        image, slots = ca.load_capture_for_analysis(path, metadata, None)
        slot_counts[len(slots)] += 1
        if not slots:
            no_slots.append(path.name)
            continue
        total_slots += len(slots)
        for m in slot_alignment_metrics(image, slots):
            if m.get("oob"):
                misaligned.append((path.name, m))
                continue
            darkest_rows[m["darkest_row_in_top10"]] += 1
            if m["border"][0] > 100:  # top edge is NOT a dark frame line
                zero_dark_top += 1
                if len(misaligned) < 15:
                    misaligned.append((path.name, m))
    print(f"\nslots per capture: {dict(sorted(slot_counts.items()))}")
    print(f"captures with no slots: {len(no_slots)} -> {no_slots[:5]}")
    print(f"total slots: {total_slots}")
    print("darkest row within top-10 rows of tile:", dict(sorted(darkest_rows.items())))
    print(f"tiles whose top edge row is bright (misaligned): {zero_dark_top} / {total_slots}")
    for name, m in misaligned[:8]:
        print("  sample misaligned:", name, m)

    # ---- quantity glyph clustering ----
    glyph_fingerprints = {}
    clusters = []
    per_frame_glyph_counts = Counter()
    for path in paths:
        image, slots = ca.load_capture_for_analysis(path, metadata, None)
        for index, (left, top, size) in enumerate(slots):
            tile = image.crop((left, top, left + size, top + size))
            glyphs = ca.extract_quantity_glyphs(tile)
            per_frame_glyph_counts[len(glyphs)] += 1
            for glyph in glyphs:
                pixels = glyph[:-3].reshape(24, 40)
                fp_key = (pixels > 0.5)
                key = tuple(fp_key.astype(np.uint8).ravel().tolist())
                entry = glyph_fingerprints.get(key)
                if entry is None:
                    entry = {"count": 0, "members": [], "shape": Counter()}
                    glyph_fingerprints[key] = entry
                    clusters.append((key, entry))
                entry["count"] += 1
                entry["members"].append((path.name, index))
                entry["shape"][tuple(np.round(glyph[-3:], 3))] += 1
    sizes = sorted((entry["count"] for _, entry in clusters), reverse=True)
    print(f"\ndistinct binary glyph patterns: {len(clusters)}; top counts: {sizes[:15]}")
    print(f"singletons: {sum(1 for s in sizes if s == 1)}")
    print("glyphs per slot distribution:", dict(sorted(per_frame_glyph_counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
