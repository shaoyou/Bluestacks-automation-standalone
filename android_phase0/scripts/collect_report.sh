#!/usr/bin/env bash
# Pull the latest Phase 0 report from a connected Android device.
# Usage: ./scripts/collect_report.sh [serial]
set -euo pipefail

SERIAL="${1:-}"
ADB=(adb)
if [[ -n "${SERIAL}" ]]; then
  ADB+=(-s "${SERIAL}")
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${ROOT}/reports/phase0_report_$(date +%Y%m%d-%H%M%S).json"

"${ADB[@]}" shell am start -n com.bsauto.phase0/.MainActivity -e probe_action export_report >/dev/null
sleep 2
"${ADB[@]}" pull /sdcard/Android/data/com.bsauto.phase0/files/phase0_report.json "${OUT}" >/dev/null
echo "${OUT}"
