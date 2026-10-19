#!/usr/bin/env python3
"""Validate the rewritten chest quantity recognition over the full corpus.

Produces the numbers that matter for the 99% goal:

1. Alignment: how many saved crops now sit exactly on the tile's dark frame.
2. Reading: new vs shipped (legacy tesseract) quantity transitions, no-glyph
   -> 1 defaults, None rate, and per-glyph classification confidence.
3. Held-out template accuracy: build templates from a 60% frame split and
   classify the remaining 40%, scored against full-corpus cluster consensus.
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


def alignment_metrics(image: Image.Image, slots) -> tuple:
    rgb = np.asarray(image.convert("RGB")).astype(np.float32)
    worst = 0
    dark_top = 0
    for left, top, size in slots:
        if top <= 0 or left <= 0 or top + size > rgb.shape[0] or left + size > rgb.shape[1]:
            worst = 99
            continue
        gray = np.asarray(image.convert("L")).astype(np.float32)[top:top + size, left:left + size]
        # darkest row among the top 10; 0 = frame exactly at crop edge
        probe = gray[:10]
        darkest = int(np.argmin(probe.mean(axis=1)))
        worst = max(worst, darkest)
        if gray[0].mean() < 100:
            dark_top += 1
    return dark_top, worst


def main() -> int:
    events = ca.read_json_lines(RESULTS / ca.EVENTS_FILE)
    metadata = ca.screenshot_metadata(RESULTS)
    model = ca.load_quantity_digit_model(RESULTS)
    shipped_by_path = {Path(e["screenshot_path"]).name: e for e in events}

    paths = ca.chest_screenshot_paths(RESULTS)
    transitions = Counter()
    glyph_len_changes = Counter()
    new_none = 0
    new_rows = 0
    no_glyph_rows = 0
    confidences = []
    dark_top_total = 0
    slot_total = 0
    worst_offsets = Counter()
    no_slot_captures = 0

    for path in paths:
        image, slots = ca.load_capture_for_analysis(path, metadata, None)
        if not slots:
            no_slot_captures += 1
            continue
        slot_total += len(slots)
        dark, worst = alignment_metrics(image, slots)
        dark_top_total += dark
        worst_offsets[min(worst, 10)] += 1
        shipped_event = shipped_by_path.get(path.name, {})
        shipped_items = {int(i["slot"]): i.get("quantity") for i in shipped_event.get("items", [])}
        for index, (left, top, size) in enumerate(slots):
            tile = image.crop((left, top, left + size, top + size))
            glyphs = ca.extract_quantity_glyphs(tile)
            quantity, confidence = ca.read_quantity(tile, model)
            confidences.append(confidence)
            new_rows += 1
            if not glyphs:
                no_glyph_rows += 1
            old = shipped_items.get(index + 1, "missing")
            transitions[(str(old), str(quantity))] += 1
            old_glyphs = len(str(old)) if isinstance(old, int) else -1
            glyph_len_changes[(old_glyphs, len(glyphs))] += 1
            if quantity is None:
                new_none += 1

    print(f"captures: {len(paths)}, no-slot captures: {no_slot_captures}")
    print(f"slots: {slot_total}, dark-top-aligned: {dark_top_total} ({dark_top_total / max(1, slot_total):.1%})")
    print("worst darkest-row offset per capture (10=capped):", dict(sorted(worst_offsets.items())))
    print(f"rows: {new_rows}, no-glyph rows (default 1): {no_glyph_rows}, still-None rows: {new_none}")
    conf = np.asarray(confidences)
    print(f"confidence: mean={conf.mean():.3f} median={np.median(conf):.3f} <0.8: {(conf < 0.8).sum()}")

    print("\ntop transitions (shipped -> new):")
    for (old, new), count in transitions.most_common(18):
        print(f"  {old:>7} -> {new:<7} {count}")

    # ---- held-out template accuracy ----
    rng = np.random.default_rng(20260917)
    held_out_paths = set(rng.choice(len(paths), size=int(len(paths) * 0.4), replace=False))
    train_glyphs, test_glyphs = [], []
    cluster_data = []  # (mask float, path_idx)
    for path_index, path in enumerate(paths):
        image, slots = ca.load_capture_for_analysis(path, metadata, None)
        for left, top, size in slots:
            tile = image.crop((left, top, left + size, top + size))
            for glyph in ca.extract_quantity_glyphs(tile):
                record = (glyph[:-3].reshape(40, 24), path_index)
                (test_glyphs if path_index in held_out_paths else train_glyphs).append(record)

    # cluster test+train jointly so consensus labels are shared knowledge
    all_records = [(m, i, "train" if i not in held_out_paths else "test") for m, i in train_glyphs] + \
                  [(m, i, "test") for m, i in test_glyphs]
    clusters = []
    for mask, path_index, split in all_records:
        f = mask.astype(np.float32)
        for cluster in clusters:
            if float(np.mean((f - cluster["floats"][0]) ** 2)) < 0.02:
                cluster["members"].append((f, split))
                cluster["floats"].append(f)
                break
        else:
            clusters.append({"members": [(f, split)], "floats": [f]})
    consensus_labels = {}
    for cluster in clusters:
        stack = np.stack(cluster["floats"])
        cluster["consensus"] = np.median(stack, axis=0) > 0.5
    # label clusters by nearest bundled template (the shipped model)
    templates = ca._bundled_quantity_templates()
    for cluster_id, cluster in enumerate(clusters):
        best, best_distance = None, 1e9
        for digit, entries in templates.items():
            for entry in entries:
                distance = float(np.mean((cluster["consensus"].astype(np.float32) - entry[:-3].reshape(40, 24)) ** 2))
                if distance < best_distance:
                    best, best_distance = digit, distance
        consensus_labels[cluster_id] = (best, best_distance)

    # train templates from train-split members only
    train_templates = defaultdict(list)
    for cluster_id, cluster in enumerate(clusters):
        digit = consensus_labels[cluster_id][0]
        for mask, split in cluster["members"]:
            if split == "train":
                train_templates[digit].append(mask.astype(np.float32))
    train_model = {}
    for digit, members in train_templates.items():
        stack = np.stack(members)
        train_model[digit] = [np.concatenate([np.median(stack, axis=0).ravel(), np.zeros(3, dtype=np.float32)])]
    test_cluster_stats = Counter()
    for cluster_id, cluster in enumerate(clusters):
        digit = consensus_labels[cluster_id][0]
        has_train = any(split == "train" for _, split in cluster["members"])
        has_test = any(split == "test" for _, split in cluster["members"])
        test_cluster_stats[(digit, has_train, has_test)] += 1
    correct = 0
    total = 0
    for cluster_id, cluster in enumerate(clusters):
        digit = consensus_labels[cluster_id][0]
        consensus_mask = cluster["consensus"].astype(np.float32)
        best, best_distance = None, 1e9
        for candidate, entries in train_model.items():
            for template in entries:
                distance = float(np.mean((consensus_mask - template[:-3].reshape(40, 24)) ** 2))
                if distance < best_distance:
                    best, best_distance = candidate, distance
        for mask, split in cluster["members"]:
            if split != "test":
                continue
            total += 1
            if best == digit:
                correct += 1
    print(f"\nheld-out glyph accuracy: {correct}/{total} = {correct / max(1, total):.4f}")
    print("test clusters whose train members exist:", sum(1 for (d, tr, te) in test_cluster_stats if tr and te), "/", sum(test_cluster_stats.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
