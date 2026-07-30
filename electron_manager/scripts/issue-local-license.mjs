import { execFileSync } from "node:child_process";
import { createInterface } from "node:readline/promises";
import { stdin, stdout } from "node:process";
import path from "node:path";
import { fileURLToPath } from "node:url";

function oneMonthFromToday() {
  const now = new Date();
  const year = now.getUTCFullYear();
  const month = now.getUTCMonth() + 1;
  const day = now.getUTCDate();
  const lastDay = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  return new Date(Date.UTC(year, month, Math.min(day, lastDay))).toISOString().slice(0, 10);
}

const installIdFromArgs = process.argv[2]?.trim();
const maxRunners = process.argv[3]?.trim() || "3";
const prompt = createInterface({ input: stdin, output: stdout });

try {
  const installId = installIdFromArgs || (await prompt.question("请输入用户安装 ID: ")).trim();
  if (!installId) throw new Error("未提供用户安装 ID");
  const expiresAt = oneMonthFromToday();
  const scriptDir = path.dirname(fileURLToPath(import.meta.url));
  const activationCode = execFileSync(
    process.execPath,
    [path.join(scriptDir, "license.mjs"), "issue", "--install-id", installId, "--expires", expiresAt, "--max-runners", maxRunners],
    { encoding: "utf8" },
  ).trim();
  console.log(`用户 ID: ${installId}`);
  console.log(`有效至: ${expiresAt}`);
  console.log(`并发运行数: ${maxRunners}`);
  console.log(`激活码:\n${activationCode}`);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
} finally {
  prompt.close();
}
