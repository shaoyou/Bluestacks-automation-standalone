#!/usr/bin/env python3
"""Benchmark chest quantity OCR against local manual calibration samples."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chest_analyzer as analyzer


def load_jobs(root: Path) -> List[Tuple[str, List[Dict[str, Any]], int, Dict[str, Any]]]:
    corrections_path = root / analyzer.CORRECTIONS_FILE
    index_path = root / "index.jsonl"
    events_path = root / analyzer.EVENTS_FILE
    corrections = json.loads(corrections_path.read_text(encoding="utf-8")).get("events", {})
    index_records = analyzer.read_json_lines(index_path)
    events = analyzer.read_json_lines(events_path)
    metadata = analyzer.screenshot_metadata(root)
    by_id = {str(event.get("event_id")): event for event in events}
    by_time = {str(event.get("captured_at")): event for event in events}
    jobs: List[Tuple[str, List[Dict[str, Any]], int, Dict[str, Any]]] = []
    for key, raw_rows in corrections.items():
        event = by_id.get(str(key)) or by_time.get(str(key))
        if not event:
            continue
        screenshot = Path(str(event.get("screenshot_path", "")))
        if not screenshot.exists():
            continue
        rows = [
            row for row in raw_rows
            if isinstance(row, dict) and isinstance(row.get("quantity"), int)
        ]
        if rows:
            jobs.append((
                str(screenshot),
                rows,
                max(int(row["slot"]) for row in rows),
                metadata.get(str(screenshot.resolve()), {}),
            ))
    return jobs


def evaluate_job(job: Tuple[str, List[Dict[str, Any]], int, Dict[str, Any]]) -> List[Tuple[int, int | None, float, int, str]]:
    screenshot_path, rows, expected_count, metadata = job
    path = Path(screenshot_path)
    image, slots = analyzer.load_capture_for_analysis(
        path,
        {str(path.resolve()): metadata},
        expected_count,
    )
    results = []
    for row in rows:
        slot = int(row["slot"])
        if slot > len(slots):
            continue
        left, top, size = slots[slot - 1]
        quantity, confidence = analyzer.read_quantity(
            image.crop((left, top, left + size, top + size))
        )
        results.append((int(row["quantity"]), quantity, confidence, slot, screenshot_path))
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    root = Path(args.input_dir).expanduser().resolve()
    jobs = load_jobs(root)
    digit_model = analyzer.load_quantity_digit_model(root)
    samples = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for result in pool.map(lambda job: evaluate_job_with_model(job, digit_model), jobs):
            samples.extend(result)
    detected = [sample for sample in samples if sample[1] is not None]
    correct = [sample for sample in detected if sample[0] == sample[1]]
    confusion = Counter((truth, predicted) for truth, predicted, *_ in detected if truth != predicted)
    summary = {
        "calibration_groups": len(jobs),
        "samples": len(samples),
        "detected": len(detected),
        "correct": len(correct),
        "accuracy_detected": round(len(correct) / len(detected), 4) if detected else None,
        "coverage": round(len(detected) / len(samples), 4) if samples else None,
        "accuracy_all": round(len(correct) / len(samples), 4) if samples else None,
        "top_errors": [
            {"truth": truth, "predicted": predicted, "count": count}
            for (truth, predicted), count in confusion.most_common(25)
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def evaluate_job_with_model(job: Tuple[str, List[Dict[str, Any]], int, Dict[str, Any]], digit_model: Dict[str, Any]) -> List[Tuple[int, int | None, float, int, str]]:
    screenshot_path, rows, expected_count, metadata = job
    path = Path(screenshot_path)
    image, slots = analyzer.load_capture_for_analysis(
        path,
        {str(path.resolve()): metadata},
        expected_count,
    )
    results = []
    for row in rows:
        slot = int(row["slot"])
        if slot > len(slots):
            continue
        left, top, size = slots[slot - 1]
        quantity, confidence = analyzer.read_quantity(
            image.crop((left, top, left + size, top + size)),
            digit_model,
        )
        results.append((int(row["quantity"]), quantity, confidence, slot, screenshot_path))
    return results


if __name__ == "__main__":
    raise SystemExit(main())
