#!/usr/bin/env python3
"""Migrate historical chest captures to named, cropped, lossless WebP files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adb_bot import chest_result_crop_box
import chest_analyzer as analyzer


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def read_json_lines(path: Path) -> List[Dict[str, Any]]:
    return analyzer.read_json_lines(path)


def write_json_lines(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    path.write_text("".join(f"{json.dumps(record, ensure_ascii=False)}\n" for record in records), encoding="utf-8")


def safe_name(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", value).strip(" ._")
    return cleaned or "未命名用户"


def user_names(users_file: Path) -> Dict[str, str]:
    data = read_json(users_file, {})
    users = data.get("users", []) if isinstance(data, dict) else []
    names = {
        str(user.get("id")): str(user.get("name")).strip()
        for user in users
        if isinstance(user, dict) and str(user.get("id", "")).strip() and str(user.get("name", "")).strip()
    }
    names.setdefault("default", "默认用户")
    return names


def event_id_for(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:16]


def valid_crop_metadata(record: Dict[str, Any], image_size: Tuple[int, int]) -> bool:
    source_size = record.get("source_size")
    crop_box = record.get("crop_box")
    if not (
        isinstance(source_size, list)
        and len(source_size) == 2
        and isinstance(crop_box, list)
        and len(crop_box) == 4
    ):
        return False
    try:
        left, top, right, bottom = (int(value) for value in crop_box)
        return image_size == (right - left, bottom - top)
    except (TypeError, ValueError):
        return False


def infer_source_size(cropped_size: Tuple[int, int]) -> Tuple[int, int]:
    for source_size in ((1080, 1920), (1216, 2688)):
        crop_box = chest_result_crop_box(source_size)
        expected_size = (crop_box[2] - crop_box[0], crop_box[3] - crop_box[1])
        if expected_size == cropped_size:
            return source_size
    raise ValueError(f"Cannot infer original screenshot size from cropped image {cropped_size}")


def verify_crop(path: Path, crop_box: Tuple[int, int, int, int]) -> Tuple[bool, str]:
    with Image.open(path).convert("RGB") as source:
        cropped = source.crop(crop_box)
        temporary = path.with_name(f".{path.stem}.migration-check.webp")
        try:
            cropped.save(temporary, format="WEBP", lossless=True, method=6)
            with Image.open(temporary).convert("RGB") as restored_crop:
                if not np.array_equal(np.asarray(cropped), np.asarray(restored_crop)):
                    return False, "lossless pixel verification failed"
                restored = Image.new("RGB", source.size)
                restored.paste(restored_crop, crop_box[:2])
                if analyzer.find_reward_slots(source) != analyzer.find_reward_slots(restored):
                    return False, "reward slot verification failed"
        finally:
            temporary.unlink(missing_ok=True)
    return True, ""


def replace_values(value: Any, paths: Dict[str, str], event_ids: Dict[str, str]) -> Any:
    if isinstance(value, str):
        if value in event_ids:
            return event_ids[value]
        if value in paths:
            return paths[value]
        for old_path, new_path in sorted(paths.items(), key=lambda item: len(item[0]), reverse=True):
            if value.startswith(f"{old_path}{os.sep}"):
                return f"{new_path}{value[len(old_path):]}"
        return value
    if isinstance(value, list):
        return [replace_values(item, paths, event_ids) for item in value]
    if isinstance(value, dict):
        replaced: Dict[Any, Any] = {}
        for key, item in value.items():
            new_key = key
            if isinstance(key, str):
                new_key = event_ids.get(key, paths.get(key, key))
            replaced[new_key] = replace_values(item, paths, event_ids)
        return replaced
    return value


def backup_files(root: Path, files: Iterable[Path]) -> Path:
    backup_dir = root / "migration_backups" / time.strftime("%Y%m%d-%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)
    for path in files:
        if path.exists():
            shutil.copy2(path, backup_dir / path.name)
    return backup_dir


def repair_existing_references(root: Path, users_file: Path) -> Dict[str, int]:
    index_file = root / "index.jsonl"
    events_file = root / analyzer.EVENTS_FILE
    corrections_file = root / analyzer.CORRECTIONS_FILE
    catalog_file = root / analyzer.CATALOG_FILE
    records = read_json_lines(index_file)
    names = user_names(users_file)
    for empty_file in root.glob("*.webp"):
        if empty_file.stat().st_size == 0:
            empty_file.unlink()
    paths: Dict[str, str] = {}
    event_ids: Dict[str, str] = {}
    backup_dirs = sorted((root / "migration_backups").glob("*"))
    legacy_dir = backup_dirs[0] if backup_dirs else None
    legacy_events = read_json_lines(legacy_dir / analyzer.EVENTS_FILE) if legacy_dir else []
    legacy_corrections = read_json(legacy_dir / analyzer.CORRECTIONS_FILE, {}) if legacy_dir else {}
    for legacy_event in legacy_events:
        old_path = Path(str(legacy_event.get("screenshot_path", "")))
        if not old_path:
            continue
        user_name = names.get(str(legacy_event.get("user_id") or "default"), "默认用户")
        candidates = sorted(
            candidate for candidate in root.glob(f"{safe_name(user_name)}_{old_path.stem}*.webp")
            if candidate.stat().st_size > 0
        )
        if len(candidates) != 1:
            continue
        target = candidates[0]
        paths[str(old_path.resolve())] = str(target.resolve())
        old_event_id = str(legacy_event.get("event_id", "")) or event_id_for(old_path)
        new_event_id = event_id_for(target)
        event_ids[old_event_id] = new_event_id
        paths[str((root / analyzer.CROPS_DIR / old_event_id).resolve())] = str(
            (root / analyzer.CROPS_DIR / new_event_id).resolve()
        )
    for record in records:
        target = Path(str(record.get("before_path", "")))
        if not target.exists():
            continue
        user_name = str(record.get("user_name") or names.get(str(record.get("user_id") or "default"), "默认用户"))
        prefix = f"{safe_name(user_name)}_"
        if not target.stem.startswith(prefix):
            continue
        old_stem = target.stem[len(prefix):]
        old_path = target.with_name(f"{old_stem}.png")
        paths[str(old_path.resolve())] = str(target.resolve())
        event_ids[event_id_for(old_path)] = event_id_for(target)
        record["user_name"] = user_name
    events = read_json_lines(events_file)
    repaired = 0
    for event in events:
        old_path = Path(str(event.get("screenshot_path", "")))
        if old_path.exists():
            continue
        user_name = names.get(str(event.get("user_id") or "default"), "默认用户")
        candidates = sorted(
            candidate for candidate in root.glob(f"{safe_name(user_name)}_{old_path.stem}*.webp")
            if candidate.stat().st_size > 0
        )
        if len(candidates) == 1:
            paths[str(old_path.resolve())] = str(candidates[0].resolve())
            event_ids[event_id_for(old_path)] = event_id_for(candidates[0])
            repaired += 1
    for event in events:
        screenshot_path = Path(str(event.get("screenshot_path", "")))
        if not screenshot_path.exists():
            continue
        expected_event_id = event_id_for(screenshot_path)
        previous_event_id = str(event.get("event_id", ""))
        if previous_event_id and previous_event_id != expected_event_id:
            event_ids[previous_event_id] = expected_event_id
        event["event_id"] = expected_event_id
        target_crop_dir = root / analyzer.CROPS_DIR / expected_event_id
        target_crop_dir.mkdir(parents=True, exist_ok=True)
        for item in event.get("items", []):
            if not isinstance(item, dict):
                continue
            for key in ("crop_path", "icon_crop_path"):
                old_crop = Path(str(item.get(key, "")))
                if old_crop.parent.name == expected_event_id:
                    continue
                if old_crop.parent.parent == root / analyzer.CROPS_DIR:
                    new_crop = target_crop_dir / old_crop.name
                    if old_crop.exists() and not new_crop.exists():
                        old_crop.rename(new_crop)
                    paths[str(old_crop.resolve())] = str(new_crop.resolve())
                    item[key] = str(new_crop.resolve())
    repaired += sum(1 for old, new in event_ids.items() if old != new)
    if not repaired:
        return {"events_repaired": 0, "paths_repaired": 0}
    backup_files(root, (index_file, events_file, corrections_file, catalog_file))
    events = [replace_values(event, paths, event_ids) for event in events]
    corrections_source = legacy_corrections if isinstance(legacy_corrections, dict) else read_json(corrections_file, {"version": 1, "events": {}})
    corrections = replace_values(corrections_source, paths, event_ids)
    catalog = replace_values(read_json(catalog_file, {"version": 1, "items": []}), paths, event_ids)
    write_json_lines(index_file, records)
    write_json_lines(events_file, events)
    corrections_file.write_text(f"{json.dumps(corrections, ensure_ascii=False, indent=2)}\n", encoding="utf-8")
    catalog_file.write_text(f"{json.dumps(catalog, ensure_ascii=False, indent=2)}\n", encoding="utf-8")
    return {"events_repaired": repaired, "paths_repaired": len(paths)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate chest screenshots to cropped lossless WebP")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--users-file", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-verify", action="store_true")
    parser.add_argument("--repair-only", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    root = Path(args.input_dir).expanduser().resolve()
    if args.repair_only:
        print(json.dumps(repair_existing_references(root, Path(args.users_file).expanduser().resolve()), ensure_ascii=False, indent=2))
        return 0
    index_file = root / "index.jsonl"
    events_file = root / analyzer.EVENTS_FILE
    corrections_file = root / analyzer.CORRECTIONS_FILE
    catalog_file = root / analyzer.CATALOG_FILE
    records = read_json_lines(index_file)
    names = user_names(Path(args.users_file).expanduser().resolve())
    path_records = {
        str(Path(str(record.get("before_path", ""))).resolve()): record
        for record in records
        if str(record.get("before_path", "")).strip()
    }

    migrations: List[Dict[str, Any]] = []
    for source_text, record in path_records.items():
        source = Path(source_text)
        user_id = str(record.get("user_id") or "default")
        user_name = names.get(user_id, user_id)
        already_migrated = False
        if not source.exists():
            candidates = sorted(root.glob(f"{safe_name(user_name)}_{source.stem}*.webp"))
            if len(candidates) != 1:
                continue
            source = candidates[0]
            already_migrated = True
        if source.suffix.lower() not in {".png", ".webp"}:
            continue
        with Image.open(source) as image:
            image_size = image.size
        if already_migrated:
            source_size = infer_source_size(image_size)
            crop_box = chest_result_crop_box(source_size)
            needs_crop = False
        elif image_size in {
            (972, 710),
            (1094, 995),
        }:
            source_size = infer_source_size(image_size)
            crop_box = chest_result_crop_box(source_size)
            needs_crop = False
        elif valid_crop_metadata(record, image_size):
            crop_box = tuple(int(value) for value in record["crop_box"])
            source_size = tuple(int(value) for value in record["source_size"])
            needs_crop = False
        else:
            source_size = image_size
            crop_box = chest_result_crop_box(image_size)
            needs_crop = True
        stem = source.stem
        prefix = f"{safe_name(user_name)}_"
        target_name = f"{prefix}{stem}.webp" if not stem.startswith(prefix) else f"{stem}.webp"
        target = root / target_name
        suffix = 2
        while target.exists() and target.resolve() != source.resolve():
            target = root / f"{Path(target_name).stem}_{suffix}.webp"
            suffix += 1
        migrations.append(
            {
                "source": source,
                "target": target,
                "record": record,
                "user_name": user_name,
                "source_size": source_size,
                "crop_box": crop_box,
                "needs_crop": needs_crop,
                "already_migrated": already_migrated,
            }
        )

    verify_jobs = [entry for entry in migrations if entry["needs_crop"] and not entry["already_migrated"]]
    failures: List[str] = []
    if not args.skip_verify:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            for entry, result in zip(verify_jobs, pool.map(lambda item: verify_crop(item["source"], item["crop_box"]), verify_jobs)):
                valid, message = result
                if not valid:
                    failures.append(f"{entry['source'].name}: {message}")
    if failures:
        raise RuntimeError("Migration verification failed:\n" + "\n".join(failures[:20]))

    if args.dry_run:
        print(json.dumps({
            "mode": "dry-run",
            "screenshots": len(migrations),
            "cropped": len(verify_jobs),
            "renamed_only": len(migrations) - len(verify_jobs),
            "users": {name: sum(1 for item in migrations if item["user_name"] == name) for name in sorted({item["user_name"] for item in migrations})},
        }, ensure_ascii=False, indent=2))
        return 0

    backup_dir = backup_files(root, (index_file, events_file, corrections_file, catalog_file))
    path_updates: Dict[str, str] = {}
    event_id_updates: Dict[str, str] = {}
    crop_dir_updates: Dict[str, str] = {}
    for entry in migrations:
        source = entry["source"]
        target = entry["target"]
        if entry["needs_crop"]:
            with Image.open(source).convert("RGB") as image:
                image.crop(entry["crop_box"]).save(target, format="WEBP", lossless=True, method=6)
        elif source.resolve() != target.resolve():
            source.rename(target)
        if source.resolve() != target.resolve() and source.exists():
            source.unlink()
        path_updates[str(source.resolve())] = str(target.resolve())
        old_event_id = event_id_for(source)
        new_event_id = event_id_for(target)
        event_id_updates[old_event_id] = new_event_id
        old_crop_dir = root / analyzer.CROPS_DIR / old_event_id
        new_crop_dir = root / analyzer.CROPS_DIR / new_event_id
        if old_crop_dir.exists() and old_crop_dir.resolve() != new_crop_dir.resolve():
            new_crop_dir.parent.mkdir(parents=True, exist_ok=True)
            old_crop_dir.rename(new_crop_dir)
        crop_dir_updates[str(old_crop_dir.resolve())] = str(new_crop_dir.resolve())

        record = entry["record"]
        record["before_path"] = str(target.resolve())
        record["user_name"] = entry["user_name"]
        record["source_size"] = list(entry["source_size"])
        record["crop_box"] = list(entry["crop_box"])

    all_path_updates = {**path_updates, **crop_dir_updates}
    events = [replace_values(record, all_path_updates, event_id_updates) for record in read_json_lines(events_file)]
    corrections = replace_values(read_json(corrections_file, {"version": 1, "events": {}}), all_path_updates, event_id_updates)
    catalog = replace_values(read_json(catalog_file, {"version": 1, "items": []}), all_path_updates, event_id_updates)
    write_json_lines(index_file, records)
    write_json_lines(events_file, events)
    corrections_file.write_text(f"{json.dumps(corrections, ensure_ascii=False, indent=2)}\n", encoding="utf-8")
    catalog_file.write_text(f"{json.dumps(catalog, ensure_ascii=False, indent=2)}\n", encoding="utf-8")
    print(json.dumps({
        "mode": "migrated",
        "screenshots": len(migrations),
        "cropped": len(verify_jobs),
        "backup": str(backup_dir),
        "users": {name: sum(1 for item in migrations if item["user_name"] == name) for name in sorted({item["user_name"] for item in migrations})},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
