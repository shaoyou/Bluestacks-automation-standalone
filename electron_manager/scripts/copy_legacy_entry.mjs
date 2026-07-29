import { copyFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptsDir = path.dirname(fileURLToPath(import.meta.url));
const projectDir = path.resolve(scriptsDir, "..");
const outputDir = path.join(projectDir, "dist-electron");

mkdirSync(outputDir, { recursive: true });
copyFileSync(
  path.join(projectDir, "electron", "legacy-main.cjs"),
  path.join(outputDir, "legacy-main.cjs"),
);
