#!/usr/bin/env python3
"""Measure item-group separation for the chest icon matcher.

Groups come from a previous catalog run (assignments JSON); this tool extracts
the icons again with the current pipeline and reports within-group vs
between-group template distances plus rarity classification coverage.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict, Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np

import chest_analyzer as ca

RESULTS = Path("/Users/admins/Library/Application Support/bs-manager-cross-platform/runtime/diagnostics/chest_results")


def main() -> int:
    assignments_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/assignments.json"
    assignments = json.load(open(assignments_path))
    by_item = defaultdict(list)
    for name, slot, item_id, _conf in assignments:
        by_item[item_id].append((name, slot))
    items = sorted(by_item, key=lambda k: -len(by_item[k]))[:8]

    metadata = ca.screenshot_metadata(RESULTS)
    paths = {p.name: p for p in ca.chest_screenshot_paths(RESULTS)}
    paths_by_name = paths

    feats = defaultdict(list)
    rarities = defaultdict(Counter)
    for index, item_id in enumerate(items):
        for name, slot in by_item[item_id][:15]:
            path = paths_by_name.get(name)
            if path is None:
                continue
            image, slots = ca.load_capture_for_analysis(path, metadata, None)
            if slot > len(slots):
                continue
            left, top, size = slots[slot - 1]
            tile = image.crop((left, top, left + size, top + size))
            crop = ca.extract_icon(ca.remove_quantity_overlay(tile))
            feats[item_id].append(ca.icon_template(crop))
            rarities[item_id][ca.tile_rarity(tile)] += 1

    for index, item_id in enumerate(items):
        fs = feats.get(item_id, [])
        if len(fs) < 3:
            continue
        within = [ca.icon_template_distance(fs[a], fs[b]) for a in range(len(fs)) for b in range(a + 1, len(fs))]
        rarity = dict(rarities.get(item_id, {}))
        print(f"item{index} ({item_id[:14]}) n={len(fs)}: within mean={np.mean(within):.4f} max={max(within):.4f} rarity={rarity}")

    ids = [item_id for item_id in items if len(feats.get(item_id, [])) >= 3]
    for i in range(min(4, len(ids))):
        for j in range(i + 1, min(5, len(ids))):
            vals = [ca.icon_template_distance(a, b) for a in feats[ids[i]] for b in feats[ids[j]]]
            print(f"between item{i}-item{j}: mean={np.mean(vals):.4f} min={min(vals):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
