import { execFileSync, spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import { fileURLToPath } from "node:url";
import process from "node:process";

const projectDir = fileURLToPath(new URL("..", import.meta.url));
const distElectronDir = fileURLToPath(new URL("../dist-electron", import.meta.url));
const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
const startPort = 5173;
const endPort = 5199;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function isPortFree(port) {
  const check = (host) => new Promise((resolve) => {
    const server = net.createServer();
    server.unref();
    server.once("error", () => resolve(false));
    server.listen({ port, host, exclusive: true }, () => {
      server.close(() => resolve(true));
    });
  });
  return Promise.all([check("127.0.0.1"), check("::1")]).then(([ipv4Free, ipv6Free]) => ipv4Free && ipv6Free);
}

async function pickPort() {
  for (let port = startPort; port <= endPort; port += 1) {
    if (await isPortFree(port)) return port;
  }
  throw new Error(`无法找到可用端口 ${startPort}-${endPort}`);
}

function spawnProcess(command, args, extraEnv = {}) {
  return spawn(command, args, {
    cwd: projectDir,
    env: { ...process.env, ...extraEnv },
    stdio: "inherit",
  });
}

function listChildPids(pid) {
  try {
    const output = execFileSync("pgrep", ["-P", String(pid)], { encoding: "utf8" });
    return output.split(/\s+/).filter(Boolean).map(Number).filter((value) => Number.isInteger(value) && value > 0);
  } catch {
    return [];
  }
}

function killTree(child) {
  if (!child || typeof child.pid !== "number") return;
  if (process.platform === "win32") {
    try {
      spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
    } catch {
      // Ignore and try the direct kill below.
    }
    try { child.kill(); } catch { /* ignore */ }
    return;
  }
  // npm scripts wrap the real processes (vite / tsc / electron); collect
  // descendants so no child survives after shutdown.
  const descendants = [];
  const queue = [child.pid];
  while (queue.length > 0) {
    const current = queue.shift();
    for (const pid of listChildPids(current)) {
      descendants.push(pid);
      queue.push(pid);
    }
  }
  for (const pid of descendants.reverse()) {
    try { process.kill(pid, "SIGTERM"); } catch { /* already gone */ }
  }
  try { child.kill("SIGTERM"); } catch { /* already gone */ }
}

const port = await pickPort();
const startedAt = Date.now();

// electron 侧（主进程 + preload）以 watch 模式常驻编译，保证加载的产物与源码一致
const electronWatch = spawnProcess(npmCommand, ["run", "build:electron", "--", "--watch", "--preserveWatchOutput"]);
const waitForFreshElectronBuild = async (timeoutMs = 60000) => {
  const outputs = ["main.js", "preload.cjs"].map((file) => `${distElectronDir}/${file}`);
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const fresh = outputs.every((file) => {
      try { return fs.statSync(file).mtimeMs > startedAt; } catch { return false; }
    });
    if (fresh) return true;
    await sleep(200);
  }
  return false;
};
if (!(await waitForFreshElectronBuild())) {
  console.error("[dev] electron 主进程编译失败，请检查上方 tsc 报错后重试");
  killTree(electronWatch);
  process.exit(1);
}

const renderer = spawnProcess(npmCommand, ["run", "dev:renderer", "--", "--port", String(port), "--strictPort"]);
let electron = null;
let shuttingDown = false;
let restartTimer = null;
let restartInFlight = false;

const waitForRenderer = async () => {
  const urls = [`http://127.0.0.1:${port}`, `http://[::1]:${port}`];
  for (let attempt = 0; attempt < 100; attempt += 1) {
    for (const url of urls) {
      try {
        const response = await fetch(url, { cache: "no-store" });
        if (response.ok) return;
      } catch {
        // Try the next address family or next retry.
      }
    }
    await sleep(150);
  }
  throw new Error(`Vite 未能在端口 ${port} 启动`);
};

let outputWatcher = null;

const shutdown = () => {
  if (shuttingDown) return;
  shuttingDown = true;
  if (restartTimer) clearTimeout(restartTimer);
  if (outputWatcher) {
    try { outputWatcher.close(); } catch { /* ignore */ }
    outputWatcher = null;
  }
  killTree(renderer);
  killTree(electronWatch);
  killTree(electron);
  // The dist-electron watcher or a lingering child would otherwise keep the
  // Node event loop alive after the app window is closed.
  setTimeout(() => process.exit(0), 400);
};

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

renderer.on("exit", (code) => {
  if (code !== 0) shutdown();
});
electronWatch.on("exit", (code) => {
  if (code !== 0 && !shuttingDown) shutdown();
});

await waitForRenderer();

const startElectron = () => {
  const child = spawnProcess(npmCommand, ["run", "dev:electron"], {
    VITE_DEV_SERVER_URL: `http://localhost:${port}`,
  });
  electron = child;
  child.on("exit", () => {
    if (shuttingDown || electron !== child) return;
    shutdown();
  });
};

const restartElectron = async () => {
  if (shuttingDown || !electron || restartInFlight) return;
  restartInFlight = true;
  console.log("[dev] electron 代码已重新编译，正在重启 electron...");
  const previous = electron;
  electron = null;
  killTree(previous);
  await Promise.race([new Promise((resolve) => previous.once("exit", resolve)), sleep(1500)]);
  restartInFlight = false;
  if (shuttingDown) return;
  startElectron();
};

startElectron();

// dist-electron 出现新产物时自动重启 electron；watch 的首次编译在启动前已完成，不会误触发
try {
  outputWatcher = fs.watch(distElectronDir, () => {
    if (shuttingDown || !electron) return;
    if (restartTimer) clearTimeout(restartTimer);
    restartTimer = setTimeout(() => void restartElectron(), 500);
  });
  outputWatcher.on("error", () => undefined);
} catch {
  // dist-electron 目录不可监听时跳过自动重启
}
