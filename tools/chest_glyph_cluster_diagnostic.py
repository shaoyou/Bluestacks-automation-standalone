#!/usr/bin/env python3
"""Render quantity-glyph clusters as ASCII art and label them.

The terminal rendering is the reviewable ground truth for designing the digit
classifier: same-rendering digits form tight clusters, and each cluster's
consensus mask printed as text can be identified unambiguously.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image

import chest_analyzer as ca

RESULTS = Path("/Users/admins/Library/Application Support/bs-manager-cross-platform/runtime/diagnostics/chest_results")


def collect_glyphs():
    metadata = ca.screenshot_metadata(RESULTS)
    glyphs = []
    for path in ca.chest_screenshot_paths(RESULTS):
        image, slots = ca.load_capture_for_analysis(path, metadata, None)
        for index, (left, top, size) in enumerate(slots):
            tile = image.crop((left, top, left + size, top + size))
            for glyph in ca.extract_quantity_glyphs(tile):
                glyphs.append({"file": path.name, "slot": index + 1, "vector": glyph})
    return glyphs


def glyph_mask(vector: np.ndarray) -> np.ndarray:
    return (vector[:-3].reshape(40, 24) > 0.5)


def tesseract_vote(mask: np.ndarray) -> str:
    image = Image.fromarray(((~mask) * 255).astype("uint8"))  # black digits on white
    enlarged = image.resize((image.width * 6, image.height * 6), Image.Resampling.LANCZOS)
    with tempfile.NamedTemporaryFile(suffix=".png") as temporary:
        enlarged.save(temporary.name)
        result = subprocess.run(
            ["tesseract", temporary.name, "stdout", "--psm", "10", "-c", "tessedit_char_whitelist=0123456789"],
            capture_output=True, text=True, timeout=8, check=False,
        )
    return "".join(ch for ch in result.stdout if ch.isdigit())


def ascii_art(mask: np.ndarray, width: int = 24) -> str:
    lines = []
    for row in mask[:, :width]:
        lines.append("".join("#" if value else "." for value in row))
    return "\n".join(lines)


def main() -> int:
    glyphs = collect_glyphs()
    print(f"total glyphs: {len(glyphs)}")
    pixels_all = [glyph_mask(g["vector"]).astype(np.float32) for g in glyphs]
    parent = list(range(len(glyphs)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    medoids = []
    for index, pixels in enumerate(pixels_all):
        for medoid_index in medoids:
            if float(np.mean((pixels - pixels_all[medoid_index]) ** 2)) < 0.02:
                union(index, medoid_index)
                break
        else:
            medoids.append(index)
    clusters = defaultdict(list)
    for index in range(len(glyphs)):
        clusters[find(index)].append(index)

    rows = []
    for root, members in clusters.items():
        consensus = (np.mean(np.stack([pixels_all[i] for i in members]), axis=0) > 0.5)
        height = int(consensus.any(axis=1).sum())
        width = int(consensus.any(axis=0).sum())
        ys, xs = np.where(consensus)
        holes = count_holes(consensus)
        rows.append({
            "size": len(members),
            "aspect": round(width / max(1, height), 2),
            "holes": holes,
            "tess": tesseract_vote(consensus),
            "consensus": consensus,
            "members": members,
        })
    rows.sort(key=lambda row: -row["size"])
    print(f"clusters: {len(rows)}; multi-member: {sum(1 for r in rows if r['size'] >= 2)}")
    print(f"top-40 coverage: {sum(r['size'] for r in rows[:40])}/{len(glyphs)}\n")
    for row in rows[:40]:
        print(f"--- cluster size={row['size']} aspect={row['aspect']} holes={row['holes']} tess={row['tess']!r}")
        print(ascii_art(row["consensus"]))
    # hole-count / aspect histogram of multi-member clusters
    print("hole histogram (multi-member):", Counter(r["holes"] for r in rows if r["size"] >= 2))
    print("aspect histogram (multi-member):", Counter(r["aspect"] for r in rows if r["size"] >= 2).most_common(12))
    return 0


def count_holes(binary: np.ndarray) -> int:
    from collections import deque
    padded = np.pad(~binary, 1, constant_values=True)
    height, width = padded.shape
    labels = np.zeros((height, width), dtype=np.int32)
    current = 0
    for sy in range(height):
        for sx in range(width):
            if padded[sy, sx] and labels[sy, sx] == 0:
                current += 1
                labels[sy, sx] = current
                queue = deque([(sy, sx)])
                while queue:
                    cy, cx = queue.popleft()
                    for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                        if 0 <= ny < height and 0 <= nx < width and padded[ny, nx] and labels[ny, nx] == 0:
                            labels[ny, nx] = current
                            queue.append((ny, nx))
    border = set(labels[0]) | set(labels[-1]) | set(labels[:, 0]) | set(labels[:, -1])
    holes = []
    for label in range(1, current + 1):
        if label in border:
            continue
        size = int((labels == label).sum())
        if size >= 3:
            holes.append(size)
    return len(holes)


if __name__ == "__main__":
    raise SystemExit(main())
