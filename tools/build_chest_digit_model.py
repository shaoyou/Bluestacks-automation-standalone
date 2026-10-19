#!/usr/bin/env python3
"""Build the bundled chest quantity digit model from the local corpus.

Digit identities were confirmed by reviewing consensus renders of every major
glyph cluster:

- '1' renders as a heavy top flag + fat right stem: NO hole, NO crossbar, and
  the lower 40% of the glyph has no ink left of the stem. Tesseract reads this
  shape as 4, 7 or even a duplicated "44" depending on context, so shipped
  labels are meaningless for it.
- '0' one large centered hole; '6' lower hole; '9' upper hole; '8' two holes.
- '2','3','5','7' are hole-free classics whose clusters have ~100% shipped
  agreement, which seeds the iterative hole-free labeling.

The output is a compact uint8 template set (up to two rendering variants per
digit) embedded into chest_analyzer.py as the bundled default model.
"""

from __future__ import annotations

import base64
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import chest_analyzer as ca

RESULTS = Path("/Users/admins/Library/Application Support/bs-manager-cross-platform/runtime/diagnostics/chest_results")
CLUSTER_MSE = 0.02


def holes_of(mask: np.ndarray) -> list:
    """Enclosed background regions: (size, cy, h) normalized to glyph size."""
    padded = np.pad(~mask, 1, constant_values=True)
    height, width = padded.shape
    labels = np.zeros((height, width), dtype=np.int32)
    current = 0
    for sy in range(height):
        for sx in range(width):
            if padded[sy, sx] and labels[sy, sx] == 0:
                current += 1
                labels[sy, sx] = current
                queue = [(sy, sx)]
                while queue:
                    cy, cx = queue.pop()
                    for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                        if 0 <= ny < height and 0 <= nx < width and padded[ny, nx] and labels[ny, nx] == 0:
                            labels[ny, nx] = current
                            queue.append((ny, nx))
    border = set(labels[0]) | set(labels[-1]) | set(labels[:, 0]) | set(labels[:, -1])
    holes = []
    for label in range(1, current + 1):
        if label in border:
            continue
        ys, xs = np.where(labels == label)
        if len(ys) < 3:
            continue
        holes.append({
            "size": int(len(ys)),
            "cy": float((ys.min() + ys.max()) / 2 - 1) / mask.shape[0],
            "h": float(ys.max() - ys.min() + 1) / mask.shape[0],
        })
    holes.sort(key=lambda item: -item["size"])
    return holes


def collect_glyphs():
    events = ca.read_json_lines(RESULTS / ca.EVENTS_FILE)
    metadata = ca.screenshot_metadata(RESULTS)
    glyphs = []
    for event in events:
        path = Path(event.get("screenshot_path", ""))
        if not path.exists():
            continue
        image, slots = ca.load_capture_for_analysis(path, metadata, None)
        for item in event.get("items", []):
            slot = int(item.get("slot", 1)) - 1
            if slot >= len(slots):
                continue
            left, top, size = slots[slot]
            tile = image.crop((left, top, left + size, top + size))
            for index, glyph in enumerate(ca.extract_quantity_glyphs(tile)):
                shipped = item.get("quantity")
                label = str(shipped)[index] if isinstance(shipped, int) and index < len(str(shipped)) else None
                glyphs.append({"mask": glyph[:-3].reshape(40, 24) > 0.5, "vector": glyph, "shipped": label})
    return glyphs


def cluster_glyphs(glyphs):
    clusters = []
    for glyph in glyphs:
        for cluster in clusters:
            if float(np.mean((glyph["mask"].astype(np.float32) - cluster["floats"][0]) ** 2)) < CLUSTER_MSE:
                cluster["members"].append(glyph)
                cluster["floats"].append(glyph["mask"].astype(np.float32))
                break
        else:
            clusters.append({"members": [glyph], "floats": [glyph["mask"].astype(np.float32)]})
    for cluster in clusters:
        cluster["consensus_mask"] = np.median(np.stack(cluster["floats"]), axis=0)
        cluster["consensus"] = np.median(np.stack([m["vector"] for m in cluster["members"]]), axis=0)
        cluster["mask"] = cluster["consensus_mask"] > 0.5
        cluster["holes"] = holes_of(cluster["mask"])
        shipped = Counter(m["shipped"] for m in cluster["members"] if m["shipped"])
        cluster["shipped"] = shipped
    return clusters


def looks_like_one(mask: np.ndarray) -> bool:
    """Flag+stem '1': solid top block, fat right stem, empty lower-left."""
    height, width = mask.shape
    upper = mask[: int(height * 0.4)]
    lower = mask[int(height * 0.6):]
    lower_left = lower[:, : int(width * 0.38)]
    stem = lower[:, int(width * 0.4):]
    return (
        float(upper.mean()) > 0.72
        and float(lower_left.mean()) < 0.06
        and float(stem.mean()) > 0.55
    )


# (file-name suffix, slot index 1-based, glyph index) of renders reviewed by
# hand while designing the classifier.
_REFERENCE_GLYPHS = {
    "1": ("esult_0007_single_items.webp", 5, 0),
    "7": ("esult_0008_single_items.webp", 5, 0),
    "2": ("esult_0012_single_items.webp", 3, 0),
    "3": ("esult_0015_single_items.webp", 2, 0),
    "5": ("esult_0015_single_items.webp", 1, 0),
    "6": ("esult_0012_single_items.webp", 5, 0),
    "8": ("esult_0014_single_items.webp", 4, 0),
    "9": ("esult_0015_single_items.webp", 5, 0),
}


def reference_seeds(clusters: List[dict], results_dir: Path) -> Dict[str, dict]:
    metadata = ca.screenshot_metadata(results_dir)
    seeds: Dict[str, dict] = {}
    for digit, (suffix, slot, glyph_index) in _REFERENCE_GLYPHS.items():
        match = next((e for e in ca.read_json_lines(results_dir / ca.EVENTS_FILE)
                      if Path(e.get("screenshot_path", "")).name.endswith(suffix)), None)
        if match is None:
            raise SystemExit(f"reference capture for digit {digit} not found")
        image, slots = ca.load_capture_for_analysis(Path(match["screenshot_path"]), metadata, None)
        left, top, size = slots[slot - 1]
        tile = image.crop((left, top, left + size, top + size))
        glyphs = ca.extract_quantity_glyphs(tile)
        if glyph_index >= len(glyphs):
            raise SystemExit(f"reference glyph missing for digit {digit}")
        mask = glyphs[glyph_index][:-3].reshape(40, 24) > 0.5
        best, best_distance = None, 1e9
        for cluster in clusters:
            distance = float(np.mean((mask.astype(np.float32) - cluster["consensus_mask"]) ** 2))
            if distance < best_distance:
                best, best_distance = cluster, distance
        seeds[digit] = best
    return seeds


def assign_hole_free(cluster: dict, templates: Dict[str, np.ndarray]) -> Optional[str]:
    best, best_distance = None, 1e9
    for digit, template in templates.items():
        distance = float(np.mean((cluster["consensus"] - template) ** 2))
        if distance < best_distance:
            best, best_distance = digit, distance
    if best is None:
        return "1" if looks_like_one(cluster["mask"]) else None
    return best


def main() -> int:
    glyphs = collect_glyphs()
    clusters = [c for c in cluster_glyphs(glyphs) if len(c["members"]) >= 4]
    covered = sum(len(c["members"]) for c in clusters)
    print(f"glyphs: {len(glyphs)}, clusters >=4: {len(clusters)} covering {covered}")

    # Round 0: hole classes (verified) + reference-glyph seeds for the
    # hole-free digits. Shipped labels are NOT used for seeding: tesseract
    # reads the game's flag+stem '1' as 4/7, so its votes cannot be trusted
    # for exactly the digits that need seeds.
    for cluster in clusters:
        holes = cluster["holes"]
        if len(holes) >= 2:
            cluster["label"] = "8"
        elif len(holes) == 1:
            hole = holes[0]
            if hole["h"] >= 0.30 and 0.36 <= hole["cy"] <= 0.62:
                cluster["label"] = "0"
            elif hole["cy"] < 0.42:
                cluster["label"] = "9"
            else:
                cluster["label"] = "6"
        else:
            cluster["label"] = None

    seeds = reference_seeds(clusters, RESULTS)
    templates = {digit: seed["consensus"].copy() for digit, seed in seeds.items()}
    print("reference seeds:", {d: len(c["members"]) for d, c in seeds.items()})

    for _ in range(3):
        for cluster in clusters:
            if cluster["label"] is None:
                cluster["label"] = assign_hole_free(cluster, templates)
        grouped = defaultdict(list)
        for cluster in clusters:
            if cluster["label"] is not None:
                grouped[cluster["label"]].append(cluster)
        for digit, group in grouped.items():
            stack = np.stack([c["consensus"] for c in group])
            weights = np.stack([np.full_like(c["consensus"], float(len(c["members"]))) for c in group])
            templates[digit] = (stack * weights).sum(axis=0) / weights.sum()

    # report labeling vs shipped majority
    agreement = Counter()
    for cluster in clusters:
        shipped = cluster["shipped"]
        if shipped:
            majority, count = shipped.most_common(1)[0]
            pure = count / sum(shipped.values()) >= 0.8
            agreement[(cluster["label"], majority if pure else "mixed")] += 1
    print("label vs shipped-majority:", dict(sorted(agreement.items())))
    for cluster in clusters:
        if cluster["shipped"] and cluster["label"] == "1":
            continue

    # per-digit templates: up to 2 largest cluster variants + weighted mean
    grouped = defaultdict(list)
    for cluster in clusters:
        grouped[cluster["label"]].append(cluster)
    model = {}
    for digit in "0123456789":
        entries = []
        if digit == "4":
            entries.append(synthesized_four())
        group = sorted(grouped.get(digit, []), key=lambda c: -len(c["members"]))
        for cluster in group[:3]:
            pixels = np.clip(cluster["consensus"][:-3] * 255.0, 0, 255).round().astype(np.uint8)
            entries.append((pixels.ravel(), np.asarray(cluster["consensus"][-3:], dtype=np.float32)))
        if not entries:
            raise SystemExit(f"no template for digit {digit}")
        model[digit] = entries

    payload = {}
    for digit, entries in model.items():
        payload[digit] = [
            {
                "pixels_b64": base64.b64encode(pixels.tobytes()).decode("ascii"),
                "shape": [round(float(v), 4) for v in shape],
                "samples": sum(len(c["members"]) for c in grouped.get(digit, [])),
            }
            for pixels, shape in entries
        ]
    Path("/tmp/chest_digit_model.json").write_text(json.dumps(payload, indent=1))
    print("templates per digit:", {d: len(v) for d, v in model.items()})
    print("samples per digit:", {d: v[0].get("samples") if isinstance(v[0], dict) else "synthetic" for d, v in model.items()})
    print("written /tmp/chest_digit_model.json")
    return 0


def digit_shape_features(mask: np.ndarray) -> np.ndarray:
    """Shape features on the normalized 24x40 scale, matching the extractor."""
    ys, xs = np.where(mask)
    height, width = mask.shape
    box_w = max(1, int(xs.max() - xs.min() + 1))
    box_h = max(1, int(ys.max() - ys.min() + 1))
    return np.asarray([
        box_w / 24.0,
        box_h / 40.0,
        int(mask.sum()) / max(1, box_w * box_h),
    ], dtype=np.float32)


def synthesized_four() -> tuple:
    """The corpus contains no real '4'; provide a bold template so future
    quantities containing 4 still classify plausibly."""
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    font_path = next((path for path in candidates if Path(path).exists()), None)
    if font_path:
        font = ImageFont.truetype(font_path, 46)
        image = Image.new("L", (24, 40), 0)
        ImageDraw.Draw(image).text((-2, -7), "4", font=font, fill=255)
        mask = np.asarray(image, dtype=np.float32) / 255.0 > 0.45
    else:
        mask = np.zeros((40, 24), dtype=bool)
        for i in range(20):
            row = i // 2
            col = 20 - i
            mask[row, max(0, col - 5):col + 1] = True
        mask[19:23, 0:22] = True
        mask[10:40, 16:20] = True
    ys, xs = np.where(mask)
    mask = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    resized = np.asarray(
        Image.fromarray((mask * 255).astype("uint8")).resize((24, 40), Image.Resampling.LANCZOS),
        dtype=np.float32,
    ) / 255.0
    pixels = np.clip(resized * 255.0, 0, 255).round().astype(np.uint8)
    return pixels.ravel(), digit_shape_features(resized > 0.5)


if __name__ == "__main__":
    raise SystemExit(main())
