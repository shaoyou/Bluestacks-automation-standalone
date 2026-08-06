#!/usr/bin/env python3
"""Analyze chest reward screenshots into item events and an editable item catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageOps


EVENTS_FILE = "item_events.jsonl"
CATALOG_FILE = "item_catalog.json"
CORRECTIONS_FILE = "manual_item_corrections.json"
CROPS_DIR = "item_crops"
NON_STACKABLE_ITEM_NAMES = {"装备", "护符", "符文", "元素符文"}
ITEM_NAME_ALIASES = {"蓝色石头": "蓝石头"}


def canonical_item_name(raw_name: Any) -> str:
    name = str(raw_name or "").strip()
    return ITEM_NAME_ALIASES.get(name, name)


def read_json_lines(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                records.append(value)
        except json.JSONDecodeError:
            continue
    return records


def write_json_lines(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    path.write_text("".join(f"{json.dumps(record, ensure_ascii=False)}\n" for record in records), encoding="utf-8")


def load_catalog(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": 1, "items": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and isinstance(value.get("items"), list):
            return normalize_catalog(value)
    except json.JSONDecodeError:
        pass
    return {"version": 1, "items": []}


def normalize_catalog(catalog: Dict[str, Any]) -> Dict[str, Any]:
    """Merge repeated perceptual IDs and labeled entries with the same name."""
    merged: Dict[str, Dict[str, Any]] = {}
    for raw_item in catalog.get("items", []):
        if not isinstance(raw_item, dict):
            continue
        item_id = str(raw_item.get("item_id", ""))
        if not item_id:
            continue
        name = str(raw_item.get("name", "待标注物品")).strip()
        merge_key = f"name:{name}" if name and name != "待标注物品" and raw_item.get("category") == "labeled" else item_id
        current = merged.get(merge_key)
        if current is None:
            current = dict(raw_item)
            current["hashes"] = list(raw_item.get("hashes", []))
            merged[merge_key] = current
            continue
        hashes = list(current.get("hashes", []))
        for value in raw_item.get("hashes", []):
            if value not in hashes:
                hashes.append(value)
        current["hashes"] = hashes
        if current.get("name") in (None, "", "待标注物品") and raw_item.get("name") not in (None, "", "待标注物品"):
            current["name"] = raw_item["name"]
            current["category"] = raw_item.get("category", current.get("category", "unknown"))
    catalog["items"] = list(merged.values())
    return catalog


def save_catalog(path: Path, catalog: Dict[str, Any]) -> None:
    path.write_text(f"{json.dumps(catalog, ensure_ascii=False, indent=2)}\n", encoding="utf-8")


def integral(mask: np.ndarray) -> np.ndarray:
    return np.pad(mask.astype(np.int32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))


def box_sum(table: np.ndarray, left: int, top: int, right: int, bottom: int) -> int:
    return int(table[bottom, right] - table[top, right] - table[bottom, left] + table[top, left])


def find_reward_slots(image: Image.Image) -> List[Tuple[int, int, int]]:
    """Find reward tiles on the game's five-column reward grid.

    The number of rewards changes the vertical placement, so rows are found
    dynamically. Horizontal slots are stable relative to the screen width.
    """
    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]

    # The emulator uses a 1080x1920 game layout whose reward tiles are
    # smaller and start farther from the left edge than the phone layout.
    # Its rows are left-aligned and the second row is one compact pitch below.
    if width <= 1100 and height <= 2000:
        size = max(96, round(height * 0.065))
        lefts = [round(width * fraction) for fraction in (0.163, 0.305, 0.448, 0.591, 0.733)]
        top_start = round(height * 0.42)
        top_end = min(height - size, round(height * 0.58))
        edge = max(2, round(size * 0.018))
        middle = size // 2

        def tile_score(left: int, top: int) -> float:
            outline = np.array([
                rgb[top + middle, left + edge],
                rgb[top + middle, left + size - edge - 1],
                rgb[top + edge, left + middle],
                rgb[top + size - edge - 1, left + middle],
            ])
            darkness = 1.0 - float(outline.max(axis=1).mean()) / 255.0
            inside = rgb[top + middle, left + middle]
            brightness = float(inside.max()) / 255.0
            return darkness + brightness * 0.15

        def tile_color_ratio(left: int, top: int) -> float:
            tile = rgb[top:top + size, left:left + size]
            maximum = tile.max(axis=2)
            minimum = tile.min(axis=2)
            return float(((maximum - minimum > 70) & (maximum > 130)).mean())

        def tile_border_ratio(left: int, top: int) -> float:
            tile = rgb[top:top + size, left:left + size].mean(axis=2)
            border = np.concatenate([
                tile[:6].flat,
                tile[-6:].flat,
                tile[:, :6].flat,
                tile[:, -6:].flat,
            ])
            return float((border < 80).mean())

        def row_candidate(top: int) -> Tuple[int, float, List[float]]:
            ratios = [tile_color_ratio(left, top) for left in lefts]
            borders = [tile_border_ratio(left, top) for left in lefts]
            count = 0
            for ratio, border in zip(ratios, borders):
                if ratio < 0.35 or border < 0.08:
                    break
                count += 1
            scores = [tile_score(left, top) for left in lefts[:count]]
            return count, sum(scores) * 0.20 + sum(ratios[:count]) * 0.70 + sum(borders[:count]) * 5.0, ratios

        def refine_row_top(rough_top: int, count: int) -> int:
            """Lock the crop to the tile's dark top border, not the background above it."""
            inset = max(8, round(size * 0.12))

            def top_border_score(top: int) -> float:
                score = 0.0
                for left in lefts[:count]:
                    border = rgb[top, left + inset:left + size - inset].astype(np.float32)
                    inside = rgb[top + 5:top + 12, left + inset:left + size - inset].astype(np.float32)
                    darkness = 1.0 - float(border.mean()) / 255.0
                    saturation = float((inside.max(axis=2) - inside.min(axis=2) > 55).mean())
                    brightness = float((inside.max(axis=2) > 120).mean())
                    score += darkness * 2.0 + saturation * 0.45 + brightness * 0.20
                return score

            search_start = max(top_start, rough_top - round(size * 0.10))
            search_end = min(top_end, rough_top + round(size * 0.38))
            return max(range(search_start, search_end + 1), key=top_border_score)

        def has_tile_top_border(left: int, top: int) -> bool:
            inset = max(8, round(size * 0.12))
            border = rgb[top, left + inset:left + size - inset].astype(np.float32)
            darkness = 1.0 - float(border.mean()) / 255.0
            return darkness >= 0.78

        def fallback_rows_from_top_borders() -> List[Tuple[int, int]]:
            candidates: List[Tuple[int, float, int]] = []
            for top in range(round(height * 0.43), min(top_end, round(height * 0.49)) + 1):
                count = 0
                darkness = 0.0
                for left in lefts:
                    inset = max(8, round(size * 0.12))
                    border = rgb[top, left + inset:left + size - inset].astype(np.float32)
                    score = 1.0 - float(border.mean()) / 255.0
                    if score < 0.78:
                        break
                    count += 1
                    darkness += score
                if count:
                    candidates.append((count, darkness, top))
            rows_from_borders: List[Tuple[int, int]] = []
            for count, _, top in sorted(candidates, key=lambda item: (item[0], item[1]), reverse=True):
                if any(abs(top - selected_top) < size // 2 for selected_top, _ in rows_from_borders):
                    continue
                rows_from_borders.append((top, count))
                break
            return rows_from_borders

        candidates = [
            (row_candidate(top)[1], top, row_candidate(top))
            for top in range(round(height * 0.43), min(top_end, round(height * 0.49)) + 1)
        ]
        candidates.sort(reverse=True)
        first: Optional[Tuple[int, int, List[float]]] = None
        for _, top, candidate in candidates:
            if candidate[0] > 0:
                first = (candidate[0], top, candidate[2])
                break
        if first is None:
            return []

        rows = [(first[1], first[0])]
        expected_second_top = first[1] + round(height * 0.063)
        second_candidates: List[Tuple[float, int, int]] = []
        for top in range(
            max(top_start, expected_second_top - round(size * 0.20)),
            min(top_end, expected_second_top + round(size * 0.30)) + 1,
        ):
            count = 0
            score = 0.0
            for left in lefts:
                color_ratio = tile_color_ratio(left, top)
                border_ratio = tile_border_ratio(left, top)
                if color_ratio < 0.35 or border_ratio < 0.40:
                    break
                count += 1
                score += color_ratio + border_ratio
            if count >= 1:
                second_candidates.append((score, top, count))
        second_candidates.sort(reverse=True)
        if second_candidates:
            _, second_top, second_count = second_candidates[0]
            rows.append((second_top, second_count))

        slots: List[Tuple[int, int, int]] = []
        for top, count in rows:
            refined_top = refine_row_top(top, count)
            for column in range(count):
                if not has_tile_top_border(lefts[column], refined_top):
                    break
                slots.append((lefts[column], refined_top, size))
        if not slots:
            for top, count in fallback_rows_from_top_borders():
                for column in range(count):
                    slots.append((lefts[column], top, size))
        return sorted(slots, key=lambda box: (box[1], box[0]))

    size = max(82, round(width * 0.136))
    top_start = round(height * 0.40)
    top_end = min(height - size, round(height * 0.59))
    lefts = [round(width * fraction) for fraction in (0.1085, 0.2737, 0.4388, 0.6041, 0.7690)]
    edge = max(2, round(size * 0.018))
    middle = size // 2

    def tile_score(left: int, top: int) -> float:
        outline = np.array([
            rgb[top + middle, left + edge],
            rgb[top + middle, left + size - edge - 1],
            rgb[top + edge, left + middle],
            rgb[top + size - edge - 1, left + middle],
        ])
        darkness = 1.0 - float(outline.max(axis=1).mean()) / 255.0
        inside = rgb[top + middle, left + middle]
        brightness = float(inside.max()) / 255.0
        return darkness + brightness * 0.15

    row_candidates: List[Tuple[float, int, List[float]]] = []
    for top in range(top_start, top_end + 1):
        scores = [tile_score(left, top) for left in lefts]
        strong = sum(score >= 0.76 for score in scores)
        # Reward rows fill from the left. A single reward is valid, so a
        # sufficiently strong first tile is enough to establish a row.
        if strong >= 3 or scores[0] >= 0.90:
            row_candidates.append((sum(scores), top, scores))

    row_candidates.sort(reverse=True)
    rows: List[Tuple[int, List[float]]] = []
    for _, top, scores in row_candidates:
        if any(abs(top - selected_top) < size // 2 for selected_top, _ in rows):
            continue
        rows.append((top, scores))
        if len(rows) >= 2:
            break

    # When the reward dialog has a second line, it is one full tile pitch
    # below the first line. Scanning it independently can lock on to a dark
    # outline in the background between the two real rows.
    if len(rows) >= 2:
        first_top = min(top for top, _ in rows)
        expected_second_top = first_top + round(height * 0.0744)
        search_radius = max(6, round(size * 0.06))
        if expected_second_top <= top_end:
            second_top = max(
                range(
                    max(top_start, expected_second_top - search_radius),
                    min(top_end, expected_second_top + search_radius) + 1,
                ),
                key=lambda top: (
                    tile_score(lefts[0], top),
                    tile_score(lefts[0], top) + tile_score(lefts[1], top),
                ),
            )
            second_scores = [tile_score(left, second_top) for left in lefts]
            if second_scores[0] >= 0.60:
                rows = [(first_top, next(scores for top, scores in rows if top == first_top)), (second_top, second_scores)]

    slots: List[Tuple[int, int, int]] = []
    for row_index, (top, scores) in enumerate(sorted(rows)):
        threshold = 0.55 if row_index == 1 else 0.70
        for left, score in zip(lefts, scores):
            # Rows are filled left to right. Stop at the first missing tile so
            # a background edge on the right cannot become a reward slot.
            if score < threshold:
                break
            if row_index == 0 and score < 0.70:
                continue
            # Empty positions in the second row can still have dark outlines
            # from the game map. A real reward tile has a bright colored frame
            # or icon surface; require that evidence before accepting it.
            if row_index > 0:
                tile = rgb[top:top + size, left:left + size]
                bright_ratio = float((tile.max(axis=2) > 150).mean())
                if bright_ratio < 0.12:
                    break
            slots.append((left, top, size))
    # With only one or two rewards the game centers the row instead of using
    # the five-column left alignment. Handle the two-item layout explicitly.
    if not slots:
        centered_lefts = [round(width * fraction) for fraction in (0.186, 0.351)]
        best_centered: Optional[Tuple[float, int]] = None
        for top in range(top_start, top_end + 1):
            total = 0.0
            valid = True
            for left in centered_lefts:
                score = tile_score(left, top)
                tile = rgb[top:top + size, left:left + size]
                bright_ratio = float((tile.max(axis=2) > 150).mean())
                if score < 0.50 or bright_ratio < 0.25:
                    valid = False
                    break
                total += score
            if valid and (best_centered is None or total > best_centered[0]):
                best_centered = (total, top)
        if best_centered is not None:
            slots = [(left, best_centered[1], size) for left in centered_lefts]
    return sorted(slots, key=lambda box: (box[1], box[0]))


def perceptual_hash(image: Image.Image) -> str:
    gray = ImageOps.grayscale(image).resize((16, 16), Image.Resampling.LANCZOS)
    pixels = np.asarray(gray, dtype=np.float32)
    threshold = float(np.median(pixels))
    bits = "".join("1" if value >= threshold else "0" for value in pixels.flat)
    return f"{int(bits, 2):064x}"


def hamming(left: str, right: str) -> int:
    return bin(int(left, 16) ^ int(right, 16)).count("1")


def normalize_item_crop(tile: Image.Image) -> Image.Image:
    width, height = tile.size
    quantity_margin = max(24, round(width * 0.30))
    return tile.crop((8, 8, max(9, width - quantity_margin), max(9, height - quantity_margin)))


def expand_mask(mask: np.ndarray, radius: int) -> np.ndarray:
    expanded = mask.copy()
    height, width = mask.shape
    for offset_y in range(-radius, radius + 1):
        for offset_x in range(-radius, radius + 1):
            if offset_x == 0 and offset_y == 0:
                continue
            source_top = max(0, -offset_y)
            source_bottom = min(height, height - offset_y)
            source_left = max(0, -offset_x)
            source_right = min(width, width - offset_x)
            target_top = max(0, offset_y)
            target_bottom = min(height, height + offset_y)
            target_left = max(0, offset_x)
            target_right = min(width, width + offset_x)
            expanded[target_top:target_bottom, target_left:target_right] |= mask[source_top:source_bottom, source_left:source_right]
    return expanded


def remove_quantity_overlay(tile: Image.Image) -> Image.Image:
    """Remove the bottom-right quantity glyph while retaining the full item tile."""
    pixels = np.asarray(tile.convert("RGB")).copy()
    height, width = pixels.shape[:2]
    maximum = pixels.max(axis=2)
    minimum = pixels.min(axis=2)
    quantity_area = np.zeros((height, width), dtype=bool)
    quantity_area[round(height * 0.57):, round(width * 0.50):] = True
    white_glyph = (maximum - minimum < 70) & (maximum > 155)
    glyph_with_outline = expand_mask(white_glyph & quantity_area, 6)
    mask = quantity_area & glyph_with_outline
    if int(mask.sum()) < 6:
        return tile.copy()

    # The source pixels beneath the number are unavailable in a screenshot.
    # Diffusing the surrounding colors removes the outlined glyph without
    # shrinking the image or leaving a hard rectangular replacement patch.
    repaired = pixels.astype(np.float32)
    for _ in range(96):
        north = np.vstack([repaired[:1], repaired[:-1]])
        south = np.vstack([repaired[1:], repaired[-1:]])
        west = np.hstack([repaired[:, :1], repaired[:, :-1]])
        east = np.hstack([repaired[:, 1:], repaired[:, -1:]])
        repaired[mask] = ((north + south + west + east) / 4.0)[mask]
    return Image.fromarray(np.clip(repaired, 0, 255).astype(np.uint8), "RGB")


def read_quantity(tile: Image.Image) -> Tuple[Optional[int], float]:
    width, height = tile.size
    # Quantity glyphs are white with a black outline. Color-isolating them
    # removes the colored item art that previously made Tesseract confuse 1/4
    # and drop digits from 9/10/102.
    crop = tile.crop((round(width * 0.52), round(height * 0.60), width - 3, height - 3)).convert("RGB")
    pixels = np.asarray(crop)
    maximum = pixels.max(axis=2)
    minimum = pixels.min(axis=2)
    masks = [
        ((maximum - minimum < 65) & (maximum > 135)),
        ((maximum - minimum < 85) & (maximum > 120)),
    ]
    try:
        # PSM13 on the strict mask remains the fast primary path. The wider
        # mask and PSM7 are only fallbacks for empty results, so day-to-day
        # batch analysis does not become several times slower.
        for index, (mask, psm) in enumerate(((masks[0], "13"), (masks[1], "13"), (masks[0], "7"))):
            candidate = Image.fromarray(mask.astype("uint8") * 255)
            enlarged = candidate.resize((candidate.width * 6, candidate.height * 6), Image.Resampling.NEAREST)
            with tempfile.NamedTemporaryFile(suffix=".png") as temporary:
                enlarged.save(temporary.name)
                result = subprocess.run(
                    ["tesseract", temporary.name, "stdout", "--psm", psm, "-c", "tessedit_char_whitelist=0123456789"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    check=False,
                )
            digits = "".join(char for char in result.stdout if char.isdigit())
            if digits:
                return int(digits), 0.95 if index == 0 else 0.72
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None, 0.0


def classify_item(icon_hash: str, catalog: Dict[str, Any]) -> Tuple[Dict[str, Any], float]:
    items = catalog.setdefault("items", [])
    best: Optional[Dict[str, Any]] = None
    best_distance = math.inf
    for item in items:
        for candidate in item.get("hashes", []):
            distance = hamming(icon_hash, str(candidate))
            if distance < best_distance:
                best, best_distance = item, distance
    if best is not None and best_distance <= 38:
        return best, max(0.0, 1.0 - best_distance / 64.0)

    # Keep the complete fingerprint while the item is unlabeled. A short
    # prefix can collide between different icons and create duplicate catalog
    # records.
    item_id = f"unknown_{icon_hash}"
    created = {
        "item_id": item_id,
        "name": "待标注物品",
        "category": "unknown",
        "hashes": [icon_hash],
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    items.append(created)
    return created, 0.0


def screenshot_metadata(results_dir: Path) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for record in read_json_lines(results_dir / "index.jsonl"):
        path = str(record.get("before_path", ""))
        if path:
            result[str(Path(path).resolve())] = record
    return result


def load_manual_corrections(results_dir: Path) -> Dict[str, List[Dict[str, Any]]]:
    path = results_dir / CORRECTIONS_FILE
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        events = value.get("events", {}) if isinstance(value, dict) else {}
        return events if isinstance(events, dict) else {}
    except json.JSONDecodeError:
        return {}


def analyze_screenshot(path: Path, results_dir: Path, catalog: Optional[Dict[str, Any]] = None, metadata: Optional[Dict[str, Dict[str, Any]]] = None, corrections: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    catalog = catalog if catalog is not None else load_catalog(results_dir / CATALOG_FILE)
    metadata = metadata if metadata is not None else screenshot_metadata(results_dir)
    corrections = corrections if corrections is not None else load_manual_corrections(results_dir)
    image = Image.open(path).convert("RGB")
    event_id = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
    slots = find_reward_slots(image)
    crops_dir = results_dir / CROPS_DIR / event_id
    if slots:
        crops_dir.mkdir(parents=True, exist_ok=True)
    item_rows: List[Dict[str, Any]] = []
    for index, (left, top, size) in enumerate(slots):
        tile = image.crop((left, top, left + size, top + size))
        crop_path = crops_dir / f"slot_{index + 1:02d}.png"
        icon_crop_path = crops_dir / f"icon_slot_{index + 1:02d}.png"
        icon_crop = normalize_item_crop(tile)
        tile.save(crop_path)
        remove_quantity_overlay(tile).save(icon_crop_path)
        icon_hash = perceptual_hash(icon_crop)
        catalog_item, item_confidence = classify_item(icon_hash, catalog)
        quantity, quantity_confidence = read_quantity(tile)
        if quantity is None and str(catalog_item.get("name", "")).strip() in NON_STACKABLE_ITEM_NAMES:
            quantity, quantity_confidence = 1, 0.80
        item_rows.append({
            "slot": index + 1,
            "row": index // 5 + 1,
            "column": index % 5 + 1,
            "item_id": catalog_item["item_id"],
            "item_name": catalog_item["name"],
            "quantity": quantity,
            "item_confidence": round(item_confidence, 3),
            "quantity_confidence": round(quantity_confidence, 3),
            "crop_path": str(crop_path.resolve()),
            "icon_crop_path": str(icon_crop_path.resolve()),
        })
    index_record = metadata.get(str(path.resolve()), {})
    saved_at = str(index_record.get("before_saved_at") or datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"))
    correction_rows = (
        corrections.get(event_id, [])
        or corrections.get(str(path.resolve()), [])
        or corrections.get(saved_at, [])
    )
    for correction in correction_rows:
        slot = int(correction.get("slot", 0))
        if 1 <= slot <= len(item_rows):
            if "item_name" in correction:
                item_rows[slot - 1]["item_name"] = str(correction["item_name"])
            if "quantity" in correction:
                item_rows[slot - 1]["quantity"] = correction["quantity"]
            item_rows[slot - 1]["item_name"] = canonical_item_name(item_rows[slot - 1].get("item_name"))
            item_rows[slot - 1]["manual_correction"] = True
    for item in item_rows:
        item["item_name"] = canonical_item_name(item.get("item_name"))
    return {
        "event_id": event_id,
        "device": str(index_record.get("device") or ""),
        "user_id": str(index_record.get("user_id") or "default"),
        "source_id": str(index_record.get("source_id") or ""),
        "source_name": str(index_record.get("source_name") or ""),
        "screenshot_path": str(path.resolve()),
        "captured_at": saved_at,
        "reward_kind": str(index_record.get("before_label") or "items"),
        "items": item_rows,
        "review_required": any(item["item_id"].startswith("unknown_") or item["quantity"] is None for item in item_rows),
        "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def analyze_results(
    results_dir: Path,
    force: bool = False,
    day: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Dict[str, int]:
    results_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = results_dir / CATALOG_FILE
    catalog = load_catalog(catalog_path)
    metadata = screenshot_metadata(results_dir)
    corrections = load_manual_corrections(results_dir)
    existing = {str(record.get("screenshot_path", "")): record for record in read_json_lines(results_dir / EVENTS_FILE)}
    analyzed = 0
    for image_path in sorted(results_dir.glob("*.png")):
        resolved = str(image_path.resolve())
        screenshot_record = metadata.get(resolved, {})
        captured_at = str(
            screenshot_record.get("before_saved_at")
            or screenshot_record.get("created_at")
            or ""
        )
        if day and not captured_at.startswith(day):
            continue
        if user_id and str(screenshot_record.get("user_id") or "default") != user_id:
            continue
        if not force and resolved in existing:
            continue
        existing[resolved] = analyze_screenshot(image_path, results_dir, catalog, metadata, corrections)
        analyzed += 1
    records = sorted(existing.values(), key=lambda record: str(record.get("captured_at", "")), reverse=True)
    write_json_lines(results_dir / EVENTS_FILE, records)
    referenced_ids = {
        str(item.get("item_id", ""))
        for record in records
        for item in (record.get("items", []) if isinstance(record.get("items"), list) else [])
        if isinstance(item, dict)
    }
    catalog["items"] = [
        item for item in catalog.get("items", [])
        if item.get("category") != "unknown" or str(item.get("item_id", "")) in referenced_ids
    ]
    save_catalog(catalog_path, catalog)
    unknown = sum(1 for item in catalog.get("items", []) if item.get("category") == "unknown")
    return {"analyzed": analyzed, "events": len(records), "unknown_items": unknown}


def evaluate_quantity_accuracy(results_dir: Path) -> Dict[str, Any]:
    corrections = load_manual_corrections(results_dir)
    if not corrections:
        return {"samples": 0, "correct": 0, "accuracy": None}
    catalog = load_catalog(results_dir / CATALOG_FILE)
    metadata = screenshot_metadata(results_dir)
    total = 0
    correct = 0
    for image_path in sorted(results_dir.glob("*.png")):
        captured_at = str(metadata.get(str(image_path.resolve()), {}).get("before_saved_at", ""))
        expected = corrections.get(captured_at, [])
        if not expected:
            continue
        result = analyze_screenshot(image_path, results_dir, catalog, metadata, {})
        actual = {int(item["slot"]): item.get("quantity") for item in result["items"]}
        for row in expected:
            total += 1
            if actual.get(int(row.get("slot", 0))) == row.get("quantity"):
                correct += 1
    return {"samples": total, "correct": correct, "accuracy": round(correct / total, 4) if total else None}


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze chest reward screenshots")
    parser.add_argument("--input-dir", required=True, help="Chest result screenshot directory")
    parser.add_argument("--force", action="store_true", help="Reanalyze screenshots already in item_events.jsonl")
    parser.add_argument("--evaluate", action="store_true", help="Evaluate quantity OCR against manual corrections")
    parser.add_argument("--day", help="Only analyze screenshots captured on YYYY-MM-DD")
    parser.add_argument("--user-id", help="Only analyze screenshots for this user")
    args = parser.parse_args()
    if args.day:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.day):
            parser.error("--day must use YYYY-MM-DD format")
        try:
            datetime.strptime(args.day, "%Y-%m-%d")
        except ValueError:
            parser.error("--day must use YYYY-MM-DD format")
    summary = analyze_results(
        Path(args.input_dir).expanduser().resolve(),
        force=args.force,
        day=args.day,
        user_id=args.user_id,
    )
    if args.evaluate:
        summary["quantity_evaluation"] = evaluate_quantity_accuracy(Path(args.input_dir).expanduser().resolve())
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
