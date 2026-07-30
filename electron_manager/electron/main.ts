import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";
import { ChildProcess, spawn } from "node:child_process";
import { accessSync, constants, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, copyFileSync, writeFileSync } from "node:fs";
import { copyFile, rename } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { activateLicense, clearLicense, getLicenseStatus } from "./license.js";

type Settings = {
  adbPath: string;
  pythonPath: string;
  language: "zh" | "en";
};

type TaskRequest = {
  id: string;
  kind: "runner" | "draw" | "recorder" | "clickPicker" | "diagnostic";
  args: string[];
  cwd?: string;
};

type EnvironmentState = {
  required: boolean;
  ready: boolean;
  phase: "ready" | "required" | "running" | "cancelled" | "failed";
  progress: number;
  message: string;
  error?: string;
};

const thisDir = path.dirname(fileURLToPath(import.meta.url));
const isDevelopment = !app.isPackaged;
const tasks = new Map<string, ChildProcess>();
const taskOwners = new Map<string, number>();
let mainWindow: BrowserWindow | null = null;
let bootstrapCancelled = false;
let bootstrapRunning = false;
const WINDOWS_RUNTIME_VERSION = "2";
const RUNTIME_RESOURCE_MIGRATION_VERSION = 5;
const RUNTIME_RESOURCE_MIGRATION_FILES = [
  path.join("plans", "choukaka.json"),
  path.join("image_templates", "role_done.png"),
];

function bundledRuntimeRoot(): string {
  return isDevelopment
    ? path.resolve(thisDir, "../../")
    : path.join(process.resourcesPath, "runtime");
}

function bundledPlansRoot(): string {
  return isDevelopment
    ? path.resolve(thisDir, "../default_plans")
    : path.join(bundledRuntimeRoot(), "plans");
}

function bundledResourcePath(relativePath: string): string {
  const parts = relativePath.split(path.sep);
  if (parts[0] === "plans") return path.join(bundledPlansRoot(), ...parts.slice(1));
  return path.join(bundledRuntimeRoot(), relativePath);
}

function copyMissingResources(sourceDir: string, targetDir: string) {
  if (!existsSync(sourceDir)) return;
  mkdirSync(targetDir, { recursive: true });
  for (const entry of readdirSync(sourceDir, { withFileTypes: true })) {
    const source = path.join(sourceDir, entry.name);
    const target = path.join(targetDir, entry.name);
    if (entry.isDirectory()) {
      copyMissingResources(source, target);
    } else if (entry.isFile() && !existsSync(target)) {
      copyFileSync(source, target);
    }
  }
}

function applyRuntimeResourceMigration(targetRoot: string) {
  const marker = path.join(targetRoot, ".bundled-resource-migration-version");
  let appliedVersion = 0;
  try {
    appliedVersion = Number.parseInt(readFileSync(marker, "utf8"), 10) || 0;
  } catch {
    // A missing marker means this installation predates the targeted migration.
  }
  if (appliedVersion >= RUNTIME_RESOURCE_MIGRATION_VERSION) return;

  for (const relativePath of RUNTIME_RESOURCE_MIGRATION_FILES) {
    const source = bundledResourcePath(relativePath);
    const target = path.join(targetRoot, relativePath);
    if (!existsSync(source)) continue;
    mkdirSync(path.dirname(target), { recursive: true });
    copyFileSync(source, target);
  }
  writeFileSync(marker, String(RUNTIME_RESOURCE_MIGRATION_VERSION), "utf8");
}

function runtimeRoot(): string {
  const target = path.join(app.getPath("userData"), "runtime");
  const source = bundledRuntimeRoot();
  mkdirSync(target, { recursive: true });

  for (const file of ["adb_bot.py", "record_touch.py"]) {
    const sourceFile = path.join(source, file);
    if (existsSync(sourceFile)) copyFileSync(sourceFile, path.join(target, file));
  }
  for (const dir of ["plans", "image_templates"]) {
    const sourceDir = dir === "plans" ? bundledPlansRoot() : path.join(source, dir);
    const targetDir = path.join(target, dir);
    copyMissingResources(sourceDir, targetDir);
  }
  applyRuntimeResourceMigration(target);
  for (const dir of ["diagnostics", "recording_profiles"]) {
    mkdirSync(path.join(target, dir), { recursive: true });
  }
  return target;
}

function requiresWindowsBootstrap() {
  return process.platform === "win32" && app.isPackaged;
}

function bundledWindowsToolsRoot() {
  return path.join(bundledRuntimeRoot(), "windows", "x64");
}

function installedWindowsToolsRoot() {
  return path.join(runtimeRoot(), "windows", "x64");
}

function windowsToolFiles() {
  return ["adb_bot.exe", "record_touch.exe", "adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll"];
}

function environmentState(): EnvironmentState {
  if (!requiresWindowsBootstrap()) {
    return { required: false, ready: true, phase: "ready", progress: 100, message: "当前平台使用已配置的运行环境" };
  }
  if (bootstrapRunning) return { required: true, ready: false, phase: "running", progress: 0, message: "正在准备 Windows 运行环境" };
  const root = installedWindowsToolsRoot();
  const marker = path.join(root, ".runtime-version");
  const complete = windowsToolFiles().every((file) => existsSync(path.join(root, file)));
  const version = existsSync(marker) ? readFileSync(marker, "utf8").trim() : "";
  if (complete && version === WINDOWS_RUNTIME_VERSION) {
    return { required: true, ready: true, phase: "ready", progress: 100, message: "内置自动化环境已就绪" };
  }
  return { required: true, ready: false, phase: "required", progress: 0, message: "需要准备内置自动化环境" };
}

function sendEnvironmentEvent(state: EnvironmentState) {
  for (const window of BrowserWindow.getAllWindows()) {
    window.webContents.send("environment:event", state);
  }
}

function allFiles(directory: string, base = directory): string[] {
  if (!existsSync(directory)) return [];
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const source = path.join(directory, entry.name);
    return entry.isDirectory() ? allFiles(source, base) : entry.isFile() ? [path.relative(base, source)] : [];
  });
}

async function bootstrapWindowsEnvironment(): Promise<EnvironmentState> {
  if (!requiresWindowsBootstrap()) return environmentState();
  if (bootstrapRunning) throw new Error("环境初始化正在进行");
  bootstrapRunning = true;
  bootstrapCancelled = false;
  try {
    const sourceRoot = bundledWindowsToolsRoot();
    const files = allFiles(sourceRoot);
    const missing = windowsToolFiles().filter((file) => !files.includes(file));
    if (missing.length) throw new Error(`安装包缺少内置组件: ${missing.join(", ")}`);
    const targetRoot = installedWindowsToolsRoot();
    const stagingRoot = `${targetRoot}.staging`;
    rmSync(stagingRoot, { recursive: true, force: true });
    for (let index = 0; index < files.length; index += 1) {
      if (bootstrapCancelled) {
        rmSync(stagingRoot, { recursive: true, force: true });
        return { required: true, ready: false, phase: "cancelled", progress: 0, message: "已取消环境准备" };
      }
      const relative = files[index];
      const source = path.join(sourceRoot, relative);
      const target = path.join(stagingRoot, relative);
      mkdirSync(path.dirname(target), { recursive: true });
      await copyFile(source, target);
      sendEnvironmentEvent({ required: true, ready: false, phase: "running", progress: Math.round((index + 1) / files.length * 80), message: `正在部署 ${relative}` });
    }
    writeFileSync(path.join(stagingRoot, ".runtime-version"), WINDOWS_RUNTIME_VERSION, "utf8");
    sendEnvironmentEvent({ required: true, ready: false, phase: "running", progress: 90, message: "正在验证 ADB 工具" });
    const check = await runCommand(path.join(stagingRoot, "adb.exe"), ["version"]);
    if (check.code !== 0) throw new Error(check.text || "adb version 执行失败");
    if (bootstrapCancelled) {
      rmSync(stagingRoot, { recursive: true, force: true });
      return { required: true, ready: false, phase: "cancelled", progress: 0, message: "已取消环境准备" };
    }
    rmSync(targetRoot, { recursive: true, force: true });
    await rename(stagingRoot, targetRoot);
    bootstrapRunning = false;
    const ready = environmentState();
    sendEnvironmentEvent(ready);
    return ready;
  } catch (error) {
    rmSync(`${installedWindowsToolsRoot()}.staging`, { recursive: true, force: true });
    const failed: EnvironmentState = { required: true, ready: false, phase: "failed", progress: 0, message: "环境准备失败", error: String(error) };
    sendEnvironmentEvent(failed);
    return failed;
  } finally {
    bootstrapRunning = false;
  }
}

function getSettings(): Settings {
  const settingsFile = path.join(app.getPath("userData"), "settings.json");
  const defaults: Settings = {
    adbPath: process.platform === "win32" ? "adb.exe" : "adb",
    pythonPath: process.platform === "win32" ? "python.exe" : "python3",
    language: "zh",
  };
  try {
    return { ...defaults, ...JSON.parse(readFileSync(settingsFile, "utf8")) };
  } catch {
    return defaults;
  }
}

function runtimeEnvironment(): NodeJS.ProcessEnv {
  const environment = { ...process.env };
  if (requiresWindowsBootstrap()) {
    environment.PATH = [installedWindowsToolsRoot(), environment.PATH ?? ""].filter(Boolean).join(path.delimiter);
  }
  if (process.platform !== "win32") {
    const extraPaths = [
      "/opt/homebrew/bin",
      "/usr/local/bin",
      "/usr/bin",
      "/bin",
      path.join(homedir(), "Library/Android/sdk/platform-tools"),
    ];
    environment.PATH = [...(environment.PATH ?? "").split(path.delimiter), ...extraPaths]
      .filter(Boolean)
      .filter((entry, index, entries) => entries.indexOf(entry) === index)
      .join(path.delimiter);
  }
  return environment;
}

function resolveExecutable(rawPath: string, fallback: string): string {
  const requested = (rawPath || fallback).trim() || fallback;
  const expanded = requested.startsWith("~") ? path.join(homedir(), requested.slice(1)) : requested;
  if (expanded.includes(path.sep) || (process.platform === "win32" && expanded.includes("/"))) {
    if (!existsSync(expanded)) throw new Error(`未找到可执行文件: ${expanded}`);
    accessSync(expanded, constants.X_OK);
    return expanded;
  }
  const candidates = (runtimeEnvironment().PATH ?? "")
    .split(path.delimiter)
    .filter(Boolean)
    .flatMap((directory) => process.platform === "win32"
      ? [path.join(directory, expanded), path.join(directory, `${expanded}.exe`)]
      : [path.join(directory, expanded)]);
  const resolved = candidates.find((candidate) => {
    try {
      accessSync(candidate, constants.X_OK);
      return true;
    } catch {
      return false;
    }
  });
  if (!resolved) throw new Error(`未找到可执行文件: ${requested}。请在设置中填写完整路径。`);
  return resolved;
}

function resolveAdbExecutable(rawPath: string): string {
  const requested = (rawPath || "").trim();
  if (requiresWindowsBootstrap() && (!requested || requested === "adb" || requested === "adb.exe")) {
    const bundled = path.join(installedWindowsToolsRoot(), "adb.exe");
    if (!existsSync(bundled)) throw new Error(`内置 ADB 不存在: ${bundled}`);
    return bundled;
  }
  return resolveExecutable(requested, process.platform === "win32" ? "adb.exe" : "adb");
}

function saveSettings(settings: Settings): Settings {
  writeFileSync(
    path.join(app.getPath("userData"), "settings.json"),
    JSON.stringify(settings, null, 2),
    "utf8",
  );
  return settings;
}

function safePlanPath(name: string): string {
  const base = path.basename(name);
  if (!base.endsWith(".json")) throw new Error("Plan file must end in .json");
  return path.join(runtimeRoot(), "plans", base);
}

function sendTaskEvent(event: Record<string, unknown>) {
  for (const window of BrowserWindow.getAllWindows()) {
    window.webContents.send("task:event", event);
  }
}

function assertRunnerCapacity() {
  const license = getLicenseStatus();
  const activeRunners = [...tasks.keys()].filter((id) => id.startsWith("runner-")).length;
  if (activeRunners >= license.maxConcurrentRunners) {
    throw new Error(
      license.tier === "pro"
        ? `专业版当前最多可同时运行 ${license.maxConcurrentRunners} 个任务`
        : "免费版仅支持同时运行 1 个任务，请在设置中激活专业版",
    );
  }
}

function spawnTask(request: TaskRequest, ownerWebContentsId?: number) {
  if (tasks.has(request.id)) throw new Error("Task is already running");
  if (request.kind === "runner") assertRunnerCapacity();
  const settings = getSettings();
  const windowsEnvironment = environmentState();
  if (windowsEnvironment.required && !windowsEnvironment.ready) throw new Error("Windows 运行环境尚未准备完成");
  const scriptName = path.basename(request.args[0] ?? "");
  const bundledExecutable = requiresWindowsBootstrap() && scriptName === "adb_bot.py"
    ? path.join(installedWindowsToolsRoot(), "adb_bot.exe")
    : requiresWindowsBootstrap() && scriptName === "record_touch.py"
      ? path.join(installedWindowsToolsRoot(), "record_touch.exe")
      : "";
  const executable = bundledExecutable || resolveExecutable(settings.pythonPath, process.platform === "win32" ? "python.exe" : "python3");
  const args = bundledExecutable ? request.args.slice(1) : ["-u", ...request.args];
  const task = spawn(executable, args, {
    cwd: request.cwd ?? runtimeRoot(),
    env: {
      ...runtimeEnvironment(),
      PYTHONUNBUFFERED: "1",
    },
    windowsHide: true,
  });
  tasks.set(request.id, task);
  if (ownerWebContentsId) taskOwners.set(request.id, ownerWebContentsId);
  sendTaskEvent({ id: request.id, type: "started" });
  sendTaskEvent({ id: request.id, type: "log", text: `$ ${executable} ${args.join(" ")}\n` });

  const onData = (data: Buffer) => {
    sendTaskEvent({ id: request.id, type: "log", text: data.toString("utf8") });
  };
  task.stdout?.on("data", onData);
  task.stderr?.on("data", onData);
  task.on("error", (error) => {
    tasks.delete(request.id);
    taskOwners.delete(request.id);
    sendTaskEvent({ id: request.id, type: "log", text: `${error.message}\n` });
  });
  task.on("exit", (code) => {
    tasks.delete(request.id);
    taskOwners.delete(request.id);
    sendTaskEvent({ id: request.id, type: "exit", code });
  });
}

async function runCommand(command: string, args: string[]) {
  return new Promise<{ code: number; text: string }>((resolve) => {
    let executable: string;
    try {
      executable = resolveAdbExecutable(command);
    } catch (error) {
      resolve({ code: -1, text: String(error) });
      return;
    }
    const proc = spawn(executable, args, { windowsHide: true, env: runtimeEnvironment() });
    let text = "";
    proc.stdout.on("data", (data) => (text += data.toString("utf8")));
    proc.stderr.on("data", (data) => (text += data.toString("utf8")));
    proc.on("error", (error) => resolve({ code: -1, text: error.message }));
    proc.on("close", (code) => resolve({ code: code ?? -1, text }));
  });
}

function loadRenderer(window: BrowserWindow, mode: "main" | "runner", runnerId?: string, initialPlan?: string) {
  const query = new URLSearchParams({ mode, ...(runnerId ? { runnerId } : {}), ...(initialPlan ? { plan: initialPlan } : {}) });
  if (isDevelopment) {
    void window.loadURL(`${process.env.VITE_DEV_SERVER_URL ?? "http://127.0.0.1:5173"}?${query}`);
  } else {
    void window.loadFile(path.join(thisDir, "../dist/index.html"), { query: Object.fromEntries(query) });
  }
}

function createWindow() {
  const window = new BrowserWindow({
    minWidth: 1120,
    minHeight: 720,
    width: 1440,
    height: 920,
    backgroundColor: "#101417",
    webPreferences: {
      // Electron executes preload scripts in CommonJS mode even when the app's
      // main process is ESM. Keep this file as .cjs so contextBridge is loaded.
      preload: path.join(thisDir, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow = window;
  window.on("closed", () => {
    if (mainWindow === window) mainWindow = null;
  });
  loadRenderer(window, "main");
}

function createRunWindow(initialPlan?: string) {
  const runnerId = `run-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const window = new BrowserWindow({
    minWidth: 760,
    minHeight: 620,
    width: 940,
    height: 780,
    title: "运行窗口",
    backgroundColor: "#101417",
    webPreferences: {
      preload: path.join(thisDir, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  window.on("close", (event) => {
    const ownedTaskIds = [...taskOwners.entries()]
      .filter(([, ownerId]) => ownerId === window.webContents.id)
      .map(([taskId]) => taskId);
    if (ownedTaskIds.length === 0) return;
    const choice = dialog.showMessageBoxSync(window, {
      type: "warning",
      buttons: ["停止并关闭", "取消"],
      defaultId: 1,
      cancelId: 1,
      message: "脚本正在运行",
      detail: "关闭窗口会停止该窗口正在运行的脚本。",
    });
    if (choice !== 0) {
      event.preventDefault();
      return;
    }
    for (const taskId of ownedTaskIds) {
      const task = tasks.get(taskId);
      if (task) process.platform === "win32" ? task.kill() : task.kill("SIGINT");
    }
  });
  loadRenderer(window, "runner", runnerId, initialPlan);
}

app.whenReady().then(() => {
  runtimeRoot();
  createWindow();

  ipcMain.handle("runtime:state", () => {
    const root = runtimeRoot();
    return { root, plansDir: path.join(root, "plans"), templatesDir: path.join(root, "image_templates") };
  });
  ipcMain.handle("environment:state", () => environmentState());
  ipcMain.handle("environment:bootstrap", () => bootstrapWindowsEnvironment());
  ipcMain.handle("environment:cancel", () => {
    bootstrapCancelled = true;
  });
  ipcMain.handle("settings:get", () => getSettings());
  ipcMain.handle("settings:save", (_, settings: Settings) => saveSettings(settings));
  ipcMain.handle("license:get", () => getLicenseStatus());
  ipcMain.handle("license:activate", (_, code: string) => activateLicense(String(code || "")));
  ipcMain.handle("license:clear", () => clearLicense());

  ipcMain.handle("plans:list", () =>
    readdirSync(path.join(runtimeRoot(), "plans"))
      .filter((name) => name.endsWith(".json"))
      .sort((a, b) => a.localeCompare(b, "zh-CN")),
  );
  ipcMain.handle("plans:read", (_, name: string) => readFileSync(safePlanPath(name), "utf8"));
  ipcMain.handle("plans:save", (_, name: string, text: string) => {
    JSON.parse(text);
    writeFileSync(safePlanPath(name), `${text.trim()}\n`, "utf8");
  });
  ipcMain.handle("plans:create", (_, rawName: string) => {
    const name = `${path.basename(rawName).replace(/\.json$/i, "")}.json`;
    const file = safePlanPath(name);
    if (existsSync(file)) throw new Error("A plan with that name already exists");
    writeFileSync(file, `${JSON.stringify({ device: "", jitter_px: 0, max_runtime_sec: 0, variables: [], actions: [] }, null, 2)}\n`);
    return name;
  });
  ipcMain.handle("plans:delete", (_, name: string) => rmSync(safePlanPath(name)));

  ipcMain.handle("templates:list", () =>
    readdirSync(path.join(runtimeRoot(), "image_templates"))
      .filter((name) => /\.(png|jpe?g|webp)$/i.test(name))
      .sort(),
  );
  ipcMain.handle("templates:import", async () => {
    const result = await dialog.showOpenDialog(mainWindow!, {
      properties: ["openFile"],
      filters: [{ name: "Images", extensions: ["png", "jpg", "jpeg", "webp"] }],
    });
    if (result.canceled || !result.filePaths[0]) return null;
    const input = result.filePaths[0];
    const output = path.join(runtimeRoot(), "image_templates", path.basename(input));
    copyFileSync(input, output);
    return `../image_templates/${path.basename(output)}`;
  });
  ipcMain.handle("templates:save-capture", (_, rawName: string, dataUrl: string) => {
    const match = /^data:image\/png;base64,([A-Za-z0-9+/=]+)$/.exec(dataUrl);
    if (!match) throw new Error("截图数据不是有效 PNG");
    const name = `${path.basename(rawName).replace(/\.(png|jpe?g|webp)$/i, "") || "capture"}.png`;
    const output = path.join(runtimeRoot(), "image_templates", name);
    writeFileSync(output, Buffer.from(match[1], "base64"));
    return `../image_templates/${name}`;
  });

  ipcMain.handle("draw:list-sessions", () => {
    const directory = path.join(runtimeRoot(), "diagnostics", "draw_stats");
    if (!existsSync(directory)) return [];
    return readdirSync(directory)
      .filter((name) => name !== "latest_summary.json" && name.endsWith("_summary.json"))
      .flatMap((name) => {
        try {
          return [{ file: name, summary: JSON.parse(readFileSync(path.join(directory, name), "utf8")) }];
        } catch {
          return [];
        }
      })
      .sort((a, b) => String(b.summary.updated_at ?? "").localeCompare(String(a.summary.updated_at ?? "")));
  });
  ipcMain.handle("draw:events", (_, sessionId: string) => {
    const safeId = path.basename(sessionId).replace(/[^a-zA-Z0-9_.-]/g, "");
    if (!safeId) return [];
    const eventsFile = path.join(runtimeRoot(), "diagnostics", "draw_stats", `${safeId}_events.jsonl`);
    if (!existsSync(eventsFile)) return [];
    return readFileSync(eventsFile, "utf8")
      .split(/\r?\n/)
      .filter(Boolean)
      .flatMap((line) => {
        try { return [JSON.parse(line)]; } catch { return []; }
      });
  });
  ipcMain.handle("draw:screenshot-pairs", (_, sessionId: string) => {
    const safeId = path.basename(sessionId).replace(/[^a-zA-Z0-9_.-]/g, "");
    if (!safeId) return [];
    const indexFile = path.join(runtimeRoot(), "diagnostics", "draw_result_pairs", "index.jsonl");
    if (!existsSync(indexFile)) return [];
    return readFileSync(indexFile, "utf8")
      .split(/\r?\n/)
      .filter(Boolean)
      .flatMap((line) => {
        try {
          const pair = JSON.parse(line) as Record<string, unknown>;
          return String(pair.session_id ?? "") === safeId ? [pair] : [];
        } catch {
          return [];
        }
      })
      .sort((a, b) => Number(b.pair_index ?? 0) - Number(a.pair_index ?? 0));
  });
  ipcMain.handle("draw:image", (_, rawPath: string) => {
    const root = path.resolve(runtimeRoot(), "diagnostics", "draw_result_pairs");
    const target = path.resolve(String(rawPath || ""));
    if (!target.startsWith(`${root}${path.sep}`) || !existsSync(target)) return null;
    const extension = path.extname(target).toLowerCase();
    const mime = extension === ".jpg" || extension === ".jpeg" ? "image/jpeg" : "image/png";
    return `data:${mime};base64,${readFileSync(target).toString("base64")}`;
  });
  ipcMain.handle("draw:open-screenshots", () => {
    const directory = path.join(runtimeRoot(), "diagnostics", "draw_result_pairs");
    mkdirSync(directory, { recursive: true });
    void shell.openPath(directory);
  });

  ipcMain.handle("adb:list-devices", async (_, adbPath: string) => {
    const result = await runCommand(adbPath || getSettings().adbPath, ["devices"]);
    if (result.code !== 0) throw new Error(result.text);
    return result.text
      .split(/\r?\n/)
      .slice(1)
      .map((line) => line.trim().split(/\s+/))
      .filter((parts) => parts[1] === "device")
      .map((parts) => parts[0]);
  });
  ipcMain.handle("adb:run", (_, adbPath: string, args: string[]) => runCommand(adbPath || getSettings().adbPath, args));
  ipcMain.handle("adb:screenshot", async (_, adbPath: string, device: string) => {
    const executable = resolveAdbExecutable(adbPath || getSettings().adbPath);
    const result = await new Promise<Buffer>((resolve, reject) => {
      const proc = spawn(executable, ["-s", device, "exec-out", "screencap", "-p"], { windowsHide: true, env: runtimeEnvironment() });
      const chunks: Buffer[] = [];
      let stderr = "";
      proc.stdout.on("data", (data) => chunks.push(data));
      proc.stderr.on("data", (data) => (stderr += data.toString("utf8")));
      proc.on("error", reject);
      proc.on("close", (code) => (code === 0 ? resolve(Buffer.concat(chunks)) : reject(new Error(stderr || `adb exited ${code}`))));
    });
    return `data:image/png;base64,${result.toString("base64")}`;
  });

  ipcMain.handle("run-window:open", (_, initialPlan?: string) => {
    if (getLicenseStatus().tier !== "pro") throw new Error("多开运行需要专业版，请在设置中输入激活码");
    createRunWindow(initialPlan);
  });
  ipcMain.handle("task:start", (event, request: TaskRequest) => spawnTask(request, event.sender.id));
  ipcMain.handle("task:stop", (_, id: string) => {
    const task = tasks.get(id);
    if (!task) return;
    if (process.platform === "win32") task.kill();
    else task.kill("SIGINT");
  });
});

app.on("window-all-closed", () => {
  for (const task of tasks.values()) task.kill();
  taskOwners.clear();
  if (process.platform !== "darwin") app.quit();
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
