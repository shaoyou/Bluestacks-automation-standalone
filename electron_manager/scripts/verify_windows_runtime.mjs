import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptsDir = path.dirname(fileURLToPath(import.meta.url));
const runtimeDir = path.resolve(scriptsDir, "../", process.argv[2] ?? "vendor/windows/x64");
const required = ["adb_bot.exe", "record_touch.exe", "adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll"];
const missing = required.filter((name) => !existsSync(path.join(runtimeDir, name)));

if (missing.length) {
  console.error(`Missing bundled Windows runtime: ${missing.join(", ")}`);
  console.error(`Run scripts/build_windows_runtime.ps1 on Windows before packaging (${runtimeDir}).`);
  process.exit(1);
}
