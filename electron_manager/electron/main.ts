import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";
import electronUpdater from "electron-updater";
import { ChildProcess, spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { accessSync, constants, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, copyFileSync, renameSync, writeFileSync, unlinkSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { activateLicense, clearLicense, getLicenseStatus } from "./license.js";

const { autoUpdater } = electronUpdater;

type Settings = {
  adbPath: string;
  hdcPath: string;
  pythonPath: string;
  language: "zh" | "en";
};

type TaskRequest = {
  id: string;
  kind: "runner" | "draw" | "chest" | "recorder" | "clickPicker" | "diagnostic";
  args: string[];
  cwd?: string;
};

type UpdateState = {
  currentVersion: string;
  supported: boolean;
  phase: "idle" | "checking" | "available" | "not-available" | "downloading" | "downloaded" | "error" | "unsupported";
  message: string;
  version?: string;
  progress?: number;
  releaseNotes?: string;
};

const thisDir = path.dirname(fileURLToPath(import.meta.url));
const isDevelopment = !app.isPackaged;
const tasks = new Map<string, ChildProcess>();
const taskOwners = new Map<string, number>();
const runnerWindows = new Set<number>();
let mainWindow: BrowserWindow | null = null;
let isQuitting = false;
const supportsAutoUpdatePlatform = process.platform === "win32" || process.platform === "darwin";
let updateState: UpdateState = {
  currentVersion: app.getVersion(),
  supported: app.isPackaged && supportsAutoUpdatePlatform,
  phase: app.isPackaged && supportsAutoUpdatePlatform ? "idle" : "unsupported",
  message: app.isPackaged
    ? supportsAutoUpdatePlatform
      ? "尚未检查更新"
      : "当前平台暂不支持自动更新"
    : "开发环境不检查更新",
};
const HDC_DEVICE_SUFFIX = " [HarmonyOS/HDC]";

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

function copyResources(sourceDir: string, targetDir: string, overwriteExisting = false) {
  if (!existsSync(sourceDir)) return;
  mkdirSync(targetDir, { recursive: true });
  for (const entry of readdirSync(sourceDir, { withFileTypes: true })) {
    const source = path.join(sourceDir, entry.name);
    const target = path.join(targetDir, entry.name);
    if (entry.isDirectory()) {
      copyResources(source, target, overwriteExisting);
    } else if (entry.isFile() && (overwriteExisting || !existsSync(target))) {
      copyFileSync(source, target);
    }
  }
}

function copyFileAtomic(source: string, target: string) {
  const temporary = `${target}.tmp-${process.pid}`;
  copyFileSync(source, temporary);
  renameSync(temporary, target);
}

function runtimeRoot(): string {
  const target = path.join(app.getPath("userData"), "runtime");
  const source = bundledRuntimeRoot();
  mkdirSync(target, { recursive: true });

  for (const file of ["adb_bot.py", "record_touch.py", "chest_analyzer.py", "data_store.py"]) {
    const sourceFile = path.join(source, file);
    if (existsSync(sourceFile)) copyFileAtomic(sourceFile, path.join(target, file));
  }
  for (const dir of ["plans", "image_templates"]) {
    const sourceDir = dir === "plans" ? bundledPlansRoot() : path.join(source, dir);
    const targetDir = path.join(target, dir);
    copyResources(sourceDir, targetDir, dir === "plans");
  }
  for (const dir of ["diagnostics", "recording_profiles"]) {
    mkdirSync(path.join(target, dir), { recursive: true });
  }
  return target;
}

function sendUpdateEvent(state: UpdateState) {
  updateState = state;
  for (const window of BrowserWindow.getAllWindows()) {
    window.webContents.send("update:event", state);
  }
}

function sendSettingsEvent(settings: Settings) {
  for (const window of BrowserWindow.getAllWindows()) {
    window.webContents.send("settings:event", settings);
  }
}

function sendDevicesEvent(devices: string[]) {
  for (const window of BrowserWindow.getAllWindows()) {
    window.webContents.send("devices:event", devices);
  }
}

function updateReleaseNotes(notes: string | Array<{ note?: string | null }> | null | undefined) {
  if (!notes) return undefined;
  if (typeof notes === "string") return notes;
  return notes.map((item) => item.note).filter(Boolean).join("\n");
}

function initializeAutoUpdater() {
  if (!updateState.supported) return;
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.on("checking-for-update", () => {
    sendUpdateEvent({ ...updateState, phase: "checking", message: "正在检查更新" });
  });
  autoUpdater.on("update-available", (info) => {
    sendUpdateEvent({
      ...updateState,
      phase: "available",
      message: `发现新版本 ${info.version}`,
      version: info.version,
      releaseNotes: updateReleaseNotes(info.releaseNotes),
    });
  });
  autoUpdater.on("update-not-available", () => {
    sendUpdateEvent({ ...updateState, phase: "not-available", message: "当前已是最新版本", version: undefined, progress: undefined, releaseNotes: undefined });
  });
  autoUpdater.on("download-progress", (progress) => {
    sendUpdateEvent({
      ...updateState,
      phase: "downloading",
      message: `正在下载更新 ${Math.round(progress.percent)}%`,
      progress: Math.round(progress.percent),
    });
  });
  autoUpdater.on("update-downloaded", (info) => {
    sendUpdateEvent({
      ...updateState,
      phase: "downloaded",
      message: `版本 ${info.version} 已下载，重启后安装`,
      version: info.version,
      progress: 100,
      releaseNotes: updateReleaseNotes(info.releaseNotes),
    });
  });
  autoUpdater.on("error", (error) => {
    sendUpdateEvent({ ...updateState, phase: "error", message: `更新失败: ${error.message}`, progress: undefined });
  });
}

async function checkForUpdates() {
  if (!updateState.supported) return updateState;
  try {
    await autoUpdater.checkForUpdates();
  } catch (error) {
    sendUpdateEvent({ ...updateState, phase: "error", message: `检查更新失败: ${String(error)}`, progress: undefined });
  }
  return updateState;
}

async function downloadUpdate() {
  if (!updateState.supported) return updateState;
  if (updateState.phase !== "available") throw new Error("当前没有可下载的更新");
  await autoUpdater.downloadUpdate();
  return updateState;
}

function getSettings(): Settings {
  const settingsFile = path.join(app.getPath("userData"), "settings.json");
  const defaults: Settings = {
    adbPath: process.platform === "win32" ? "adb.exe" : "adb",
    hdcPath: process.platform === "win32" ? "hdc.exe" : "hdc",
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
  return resolveExecutable((rawPath || "").trim(), process.platform === "win32" ? "adb.exe" : "adb");
}

function resolveHdcExecutable(rawPath: string): string {
  return resolveExecutable((rawPath || "").trim(), process.platform === "win32" ? "hdc.exe" : "hdc");
}

function saveSettings(settings: Settings): Settings {
  const normalized = { ...getSettings(), ...settings };
  writeFileSync(
    path.join(app.getPath("userData"), "settings.json"),
    JSON.stringify(normalized, null, 2),
    "utf8",
  );
  return normalized;
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

function forgetTask(taskId: string) {
  tasks.delete(taskId);
  taskOwners.delete(taskId);
}

function trackTask(taskId: string, task: ChildProcess, ownerWebContentsId?: number, commandLine?: string) {
  tasks.set(taskId, task);
  if (ownerWebContentsId) taskOwners.set(taskId, ownerWebContentsId);
  sendTaskEvent({ id: taskId, type: "started" });
  if (commandLine) sendTaskEvent({ id: taskId, type: "log", text: `$ ${commandLine}\n` });

  const onData = (data: Buffer) => {
    sendTaskEvent({ id: taskId, type: "log", text: data.toString("utf8") });
  };
  task.stdout?.on("data", onData);
  task.stderr?.on("data", onData);
  task.on("error", (error) => {
    forgetTask(taskId);
    sendTaskEvent({ id: taskId, type: "log", text: `${error.message}\n` });
  });
  task.on("exit", (code) => {
    forgetTask(taskId);
    sendTaskEvent({ id: taskId, type: "exit", code });
  });
}

function stopAllTasks() {
  for (const task of tasks.values()) {
    if (process.platform === "win32") task.kill();
    else task.kill("SIGINT");
  }
  tasks.clear();
  taskOwners.clear();
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
  const executable = resolveExecutable(settings.pythonPath, process.platform === "win32" ? "python.exe" : "python3");
  const args = ["-u", ...request.args];
  const task = spawn(executable, args, {
    cwd: request.cwd ?? runtimeRoot(),
    env: {
      ...runtimeEnvironment(),
      PYTHONUNBUFFERED: "1",
    },
    windowsHide: true,
  });
  trackTask(request.id, task, ownerWebContentsId, `${executable} ${args.join(" ")}`);
}

function commandToolPaths(raw: unknown): { adbPath: string; hdcPath: string } {
  const settings = getSettings();
  if (typeof raw === "string") return { adbPath: raw, hdcPath: settings.hdcPath };
  if (raw && typeof raw === "object") {
    const value = raw as Partial<Settings>;
    return {
      adbPath: String(value.adbPath || settings.adbPath),
      hdcPath: String(value.hdcPath || settings.hdcPath),
    };
  }
  return { adbPath: settings.adbPath, hdcPath: settings.hdcPath };
}

async function runCommand(command: string, args: string[], backend: "adb" | "hdc" = "adb") {
  return new Promise<{ code: number; text: string }>((resolve) => {
    let executable: string;
    try {
      executable = backend === "hdc" ? resolveHdcExecutable(command) : resolveAdbExecutable(command);
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

async function runCommandBuffer(command: string, args: string[], backend: "adb" | "hdc" = "adb") {
  return new Promise<{ code: number; stdout: Buffer; stderr: string }>((resolve) => {
    let executable: string;
    try {
      executable = backend === "hdc" ? resolveHdcExecutable(command) : resolveAdbExecutable(command);
    } catch (error) {
      resolve({ code: -1, stdout: Buffer.alloc(0), stderr: String(error) });
      return;
    }
    const proc = spawn(executable, args, { windowsHide: true, env: runtimeEnvironment() });
    const chunks: Buffer[] = [];
    let stderr = "";
    proc.stdout.on("data", (data) => chunks.push(Buffer.from(data)));
    proc.stderr.on("data", (data) => (stderr += data.toString("utf8")));
    proc.on("error", (error) => resolve({ code: -1, stdout: Buffer.alloc(0), stderr: error.message }));
    proc.on("close", (code) => resolve({ code: code ?? -1, stdout: Buffer.concat(chunks), stderr }));
  });
}

function splitTargetArgs(args: string[]): { target: string; rest: string[] } {
  if (args[0] === "-t" && args[1]) return { target: args[1], rest: args.slice(2) };
  if (args[0] === "-s" && args[1]) return { target: args[1], rest: args.slice(2) };
  return { target: "", rest: args };
}

function hdcUiInputArgs(shellArgs: string[]): string[] | null {
  if (shellArgs[0] !== "input") return null;
  let inputArgs = shellArgs.slice(1);
  if (["touchscreen", "touchpad", "mouse", "keyboard"].includes(inputArgs[0])) inputArgs = inputArgs.slice(1);
  if ((inputArgs[0] === "tap" || inputArgs[0] === "click") && inputArgs.length >= 3) {
    return ["uitest", "uiInput", "click", inputArgs[1], inputArgs[2]];
  }
  if (["doubleclick", "double_click", "double-tap", "doubletap"].includes(inputArgs[0]) && inputArgs.length >= 3) {
    return ["uitest", "uiInput", "doubleClick", inputArgs[1], inputArgs[2]];
  }
  if (["longclick", "long_click", "longpress", "long_press"].includes(inputArgs[0]) && inputArgs.length >= 3) {
    return ["uitest", "uiInput", "longClick", inputArgs[1], inputArgs[2]];
  }
  if (inputArgs[0] === "swipe" && inputArgs.length >= 5) {
    return ["uinput", "-T", "-m", inputArgs[1], inputArgs[2], inputArgs[3], inputArgs[4], "-k", "0", inputArgs[5] || "300"];
  }
  if (inputArgs[0] === "drag" && inputArgs.length >= 5) {
    return ["uinput", "-T", "-m", inputArgs[1], inputArgs[2], inputArgs[3], inputArgs[4], "-k", "500", inputArgs[5] || "600"];
  }
  if (inputArgs[0] === "fling" && inputArgs.length >= 5) {
    return ["uitest", "uiInput", "fling", inputArgs[1], inputArgs[2], inputArgs[3], inputArgs[4], hdcSwipeVelocity(inputArgs[1], inputArgs[2], inputArgs[3], inputArgs[4], inputArgs[5])];
  }
  if ((inputArgs[0] === "keyevent" || inputArgs[0] === "key") && inputArgs[1]) {
    return ["uitest", "uiInput", "keyEvent", hdcKeyValue(inputArgs[1])];
  }
  if (inputArgs[0] === "text" && inputArgs[1]) {
    return ["uitest", "uiInput", "text", ...inputArgs.slice(1)];
  }
  if (inputArgs[0] === "back" || inputArgs[0] === "home") {
    return ["uitest", "uiInput", "keyEvent", inputArgs[0] === "back" ? "Back" : "Home"];
  }
  return null;
}

function hdcSwipeVelocity(x1: string, y1: string, x2: string, y2: string, durationMs?: string): string {
  if (!durationMs) return "600";
  const dx = Number(x2) - Number(x1);
  const dy = Number(y2) - Number(y1);
  const duration = Math.max(1, Number(durationMs));
  if (!Number.isFinite(dx) || !Number.isFinite(dy) || !Number.isFinite(duration)) return "600";
  const velocity = Math.round(Math.hypot(dx, dy) * 1000 / duration);
  return String(Math.max(200, Math.min(40000, velocity)));
}

function hdcKeyValue(rawKey: string): string {
  const key = rawKey.trim();
  const lowered = key.toLowerCase();
  if (lowered === "keycode_back" || lowered === "back") return "Back";
  if (lowered === "keycode_home" || lowered === "home") return "Home";
  if (lowered === "keycode_power" || lowered === "power") return "Power";
  return key;
}

function hdcCompatArgs(args: string[]): string[] {
  const { target, rest } = splitTargetArgs(args);
  const prefix = target ? ["-t", target] : [];
  if (rest[0] === "get-state") return [...prefix, "shell", "echo", "device"];
  if (rest[0] === "shell") {
    const shellArgs = rest.slice(1);
    const inputArgs = hdcUiInputArgs(shellArgs);
    if (inputArgs) return [...prefix, "shell", ...inputArgs];
    if (shellArgs[0] === "screencap") return [...prefix, "shell", "uitest", "screenCap", "-p", "/data/local/tmp/bsmanager_shell_screencap.png"];
    if (shellArgs[0] === "uiautomator" && shellArgs[1] === "dump") {
      const savePath = shellArgs.find((item) => item.startsWith("/")) || "/data/local/tmp/window_dump.json";
      return [...prefix, "shell", "uitest", "dumpLayout", "-p", savePath];
    }
  }
  return [...prefix, ...rest];
}

async function hdcScreenshotBuffer(hdcPath: string, target: string): Promise<Buffer> {
  const remotePath = `/data/local/tmp/bsmanager_screen_${process.pid}_${Date.now()}.png`;
  const localPath = path.join(tmpdir(), `bsmanager-hdc-screen-${process.pid}-${Date.now()}.png`);
  const targetArgs = target ? ["-t", target] : [];
  const capture = await runCommand(hdcPath, [...targetArgs, "shell", "uitest", "screenCap", "-p", remotePath], "hdc");
  if (capture.code !== 0) throw new Error(capture.text || "HDC screenCap failed");
  const recv = await runCommand(hdcPath, [...targetArgs, "file", "recv", remotePath, localPath], "hdc");
  await runCommand(hdcPath, [...targetArgs, "shell", "rm", "-f", remotePath], "hdc");
  if (recv.code !== 0) throw new Error(recv.text || "HDC screenshot fetch failed");
  try {
    return readFileSync(localPath);
  } finally {
    try { unlinkSync(localPath); } catch { /* ignore temp cleanup */ }
  }
}

async function hdcScreenSizeText(hdcPath: string, target: string): Promise<string> {
  const image = await hdcScreenshotBuffer(hdcPath, target);
  if (image.length < 24 || image.subarray(0, 8).toString("hex") !== "89504e470d0a1a0a") {
    throw new Error("HDC screenshot did not return a PNG image");
  }
  const width = image.readUInt32BE(16);
  const height = image.readUInt32BE(20);
  return `Physical size: ${width}x${height}\n`;
}

async function listAdbDevices(adbPath: string): Promise<string[]> {
  const result = await runCommand(adbPath || getSettings().adbPath, ["devices"]);
  if (result.code !== 0) throw new Error(result.text);
  return parseAdbDevices(result.text);
}

async function listHdcDevices(hdcPath: string): Promise<string[]> {
  const result = await runCommand(hdcPath || getSettings().hdcPath, ["list", "targets"], "hdc");
  if (result.code !== 0) return [];
  return parseHdcDevices(result.text).map((target) => `${target}${HDC_DEVICE_SUFFIX}`);
}

function splitDeviceBackend(device: string): { backend: "adb" | "hdc"; target: string } {
  const raw = String(device || "").trim();
  if (raw.endsWith(HDC_DEVICE_SUFFIX)) return { backend: "hdc", target: raw.slice(0, -HDC_DEVICE_SUFFIX.length).trim() };
  if (raw.startsWith("hdc:")) return { backend: "hdc", target: raw.slice(4).trim() };
  return { backend: "adb", target: raw };
}

function loadRenderer(window: BrowserWindow, mode: "main" | "runner" | "chest", runnerId?: string, initialPlan?: string, userId?: string, sourceId?: string, sourceName?: string) {
  const query = new URLSearchParams({ mode, ...(runnerId ? { runnerId } : {}), ...(initialPlan ? { plan: initialPlan } : {}), ...(userId ? { userId } : {}), ...(sourceId ? { sourceId } : {}), ...(sourceName ? { sourceName } : {}) });
  if (isDevelopment) {
    void window.loadURL(`${process.env.VITE_DEV_SERVER_URL ?? "http://localhost:5173"}?${query}`);
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
  window.on("close", (event) => {
    if (process.platform !== "darwin" || isQuitting) return;
    event.preventDefault();
    window.hide();
  });
  window.on("closed", () => {
    if (mainWindow === window) mainWindow = null;
  });
  loadRenderer(window, "main");
}

function createRunWindow(initialPlan?: string, mode: "runner" | "chest" = "runner", userId = "default", sourceId = "", sourceName = "") {
  const runnerId = `run-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const window = new BrowserWindow({
    minWidth: 760,
    minHeight: 620,
    width: 940,
    height: 780,
    title: mode === "chest" ? "开宝箱窗口" : "运行窗口",
    backgroundColor: "#101417",
    webPreferences: {
      preload: path.join(thisDir, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  runnerWindows.add(window.id);
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
  window.on("closed", () => {
    runnerWindows.delete(window.id);
  });
  loadRenderer(window, mode, runnerId, initialPlan, userId, sourceId, sourceName);
}

app.whenReady().then(() => {
  runtimeRoot();
  migrateLegacyChestSources();
  createWindow();
  initializeAutoUpdater();
  if (updateState.supported) {
    setTimeout(() => void checkForUpdates(), 5_000);
  }

  ipcMain.handle("runtime:state", () => {
    const root = runtimeRoot();
    return { root, plansDir: path.join(root, "plans"), templatesDir: path.join(root, "image_templates") };
  });
  ipcMain.handle("settings:get", () => getSettings());
  ipcMain.handle("settings:save", (_, settings: Settings) => {
    const saved = saveSettings(settings);
    sendSettingsEvent(saved);
    return saved;
  });
  ipcMain.handle("update:state", () => updateState);
  ipcMain.handle("update:check", () => checkForUpdates());
  ipcMain.handle("update:download", () => downloadUpdate());
  ipcMain.handle("update:install", () => {
    if (updateState.phase !== "downloaded") throw new Error("更新尚未下载完成");
    setImmediate(() => autoUpdater.quitAndInstall());
  });
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
  ipcMain.handle("templates:open-folder", async () => {
    const directory = path.join(runtimeRoot(), "image_templates");
    const error = await shell.openPath(directory);
    if (error) throw new Error(error);
  });
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

  ipcMain.handle("draw:users", () => chestUsers());
  ipcMain.handle("draw:user-create", (_, name: string) => createChestUser(name));
  ipcMain.handle("draw:user-rename", (_, userId: string, name: string) => renameChestUser(userId, name));
  ipcMain.handle("draw:list-sessions", (_, userId = "default") => {
    const directory = path.join(runtimeRoot(), "diagnostics", "draw_stats");
    if (!existsSync(directory)) return [];
    return readdirSync(directory)
      .filter((name) => name !== "latest_summary.json" && name.endsWith("_summary.json"))
      .flatMap((name) => {
        try {
          const summary = JSON.parse(readFileSync(path.join(directory, name), "utf8")) as Record<string, unknown>;
          return String(summary.user_id ?? "default") === userId ? [{ file: name, summary }] : [];
        } catch {
          return [];
        }
      })
      .sort((a, b) => String(b.summary.updated_at ?? "").localeCompare(String(a.summary.updated_at ?? "")));
  });
  ipcMain.handle("draw:events", (_, sessionId: string, userId = "default") => {
    const safeId = path.basename(sessionId).replace(/[^a-zA-Z0-9_.-]/g, "");
    if (!safeId) return [];
    const eventsFile = path.join(runtimeRoot(), "diagnostics", "draw_stats", `${safeId}_events.jsonl`);
    if (!existsSync(eventsFile)) return [];
    return readFileSync(eventsFile, "utf8")
      .split(/\r?\n/)
      .filter(Boolean)
      .flatMap((line) => {
        try {
          const event = JSON.parse(line) as Record<string, unknown>;
          return String(event.user_id ?? "default") === userId ? [event] : [];
        } catch { return []; }
      });
  });
  ipcMain.handle("draw:screenshot-pairs", (_, sessionId: string, userId = "default") => {
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
          return String(pair.session_id ?? "") === safeId && String(pair.user_id ?? "default") === userId ? [pair] : [];
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
  ipcMain.handle("draw:correct-result", (_, pairPrefix: string, roleName: string, userId = "default") => correctDrawResult(pairPrefix, roleName, userId));
  ipcMain.handle("draw:export-report", (_, endDay: string, range: string, userId = "default", startDay?: string) => exportDrawReport(endDay, range, userId, startDay));
  ipcMain.handle("draw:open-report-directory", async () => {
    const directory = path.join(drawResultsRoot(), "reports");
    mkdirSync(directory, { recursive: true });
    const error = await shell.openPath(directory);
    if (error) throw new Error(error);
  });
  ipcMain.handle("history:migrate", () => migrateHistoryToDatabase());
  ipcMain.handle("chest:users", () => chestUsers());
  ipcMain.handle("chest:user-create", (_, name: string) => createChestUser(name));
  ipcMain.handle("chest:user-rename", (_, userId: string, name: string) => renameChestUser(userId, name));
  ipcMain.handle("chest:list-days", (_, device = "", userId = "default") => {
    const records = chestScreenshotRecords(device, userId);
    const daily = new Map<string, { day: string; count: number; latestAt: string }>();
    for (const record of records) {
      const day = String(record.before_saved_at ?? "").slice(0, 10);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) continue;
      const existing = daily.get(day) ?? { day, count: 0, latestAt: "" };
      existing.count += 1;
      const savedAt = String(record.before_saved_at ?? "");
      if (savedAt > existing.latestAt) existing.latestAt = savedAt;
      daily.set(day, existing);
    }
    const screenshotPaths = new Set(records.map((record) => String(record.before_path ?? "")));
    for (const event of chestAllItemEvents(device, userId)) {
      if (screenshotPaths.has(String(event.screenshot_path ?? ""))) continue;
      const day = String(event.captured_at ?? "").slice(0, 10);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) continue;
      const existing = daily.get(day) ?? { day, count: 0, latestAt: "" };
      existing.count += 1;
      const capturedAt = String(event.captured_at ?? "");
      if (capturedAt > existing.latestAt) existing.latestAt = capturedAt;
      daily.set(day, existing);
    }
    return [...daily.values()].sort((a, b) => b.day.localeCompare(a.day));
  });
  ipcMain.handle("chest:screenshots", (_, day: string, device = "", userId = "default") => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(day))) return [];
    return chestScreenshotRecords(device, userId)
      .filter((record) => String(record.before_saved_at ?? "").startsWith(day))
      .sort((a, b) => String(b.before_saved_at ?? "").localeCompare(String(a.before_saved_at ?? "")));
  });
  ipcMain.handle("chest:item-events", (_, day: string, device = "", userId = "default") => chestItemEvents(day, device, userId));
  ipcMain.handle("chest:item-summary", (_, day: string, device = "", userId = "default") => chestItemSummary(day, device, userId));
  ipcMain.handle("chest:summary-range", (_, endDay: string, range: string, device = "", userId = "default", startDay?: string, sourceId = "") => chestSummaryRange(endDay, range, device, userId, startDay, sourceId));
  ipcMain.handle("chest:export-report", (_, endDay: string, range: string, device = "", userId = "default", startDay?: string, sourceId = "") => exportChestReport(endDay, range, device, userId, startDay, sourceId));
  ipcMain.handle("chest:sync-export", (_, userId = "default") => exportChestSyncPackage(userId));
  ipcMain.handle("chest:sync-import", (_, userId = "default") => importChestSyncPackage(userId));
  ipcMain.handle("chest:open-report-directory", async () => {
    const directory = path.join(chestResultsRoot(), "reports");
    mkdirSync(directory, { recursive: true });
    const error = await shell.openPath(directory);
    if (error) throw new Error(error);
  });
  ipcMain.handle("chest:set-active-source", (_, userId: string, taskId: string, sourceId: string, sourceName: string) =>
    setChestActiveSource(userId, taskId, sourceId, sourceName),
  );
  ipcMain.handle("chest:sources", (_, userId = "default") => customChestSources(userId));
  ipcMain.handle("chest:source-create", (_, userId: string, sourceName: string) => addCustomChestSource(userId, sourceName));
  ipcMain.handle("chest:source-delete", (_, userId: string, sourceId: string) => deleteCustomChestSource(userId, sourceId));
  ipcMain.handle("chest:reanalyze", (event, day?: string, userId?: string) =>
    runChestAnalyzer(
      typeof day === "string" ? day : undefined,
      typeof userId === "string" ? userId : undefined,
      event.sender.id,
    ),
  );
  ipcMain.handle("chest:unlabeled-items", () => chestUnlabeledItems());
  ipcMain.handle("chest:label-item", (_, itemId: string, name: string) => labelChestItem(itemId, name));
  ipcMain.handle("chest:item-weight", (_, itemId: string, weight: number | null) => setChestItemWeight(itemId, weight));
  ipcMain.handle("chest:correct-event", (_, screenshotPath: string, corrections: Array<{ slot: number; itemName?: string | null; quantity: number | null }>, metadata?: { userId: string; sourceId: string; sourceName: string }) =>
    correctChestEvent(screenshotPath, corrections, metadata),
  );
  ipcMain.handle("chest:delete-event", (_, screenshotPath: string) => deleteChestEvent(screenshotPath));
  ipcMain.handle("chest:delete-item", (_, itemId: string) => deleteChestCatalogItem(itemId));
  ipcMain.handle("chest:image", (_, rawPath: string) => runtimeDiagnosticImage("chest_results", rawPath));
  ipcMain.handle("chest:open-screenshots", () => {
    const directory = path.join(runtimeRoot(), "diagnostics", "chest_results");
    mkdirSync(directory, { recursive: true });
    void shell.openPath(directory);
  });

  ipcMain.handle("adb:list-devices", async (_, rawToolPaths: unknown) => {
    const { adbPath, hdcPath } = commandToolPaths(rawToolPaths);
    const [adbDevices, hdcDevices] = await Promise.all([
      listAdbDevices(adbPath).catch(() => []),
      listHdcDevices(hdcPath).catch(() => []),
    ]);
    const devices = [...adbDevices, ...hdcDevices];
    sendDevicesEvent(devices);
    return devices;
  });
  ipcMain.handle("adb:force-refresh-devices", async (_, rawToolPaths: unknown) => {
    const { adbPath, hdcPath } = commandToolPaths(rawToolPaths);
    const executable = adbPath || getSettings().adbPath;
    await runCommand(executable, ["kill-server"]);
    await runCommand(executable, ["start-server"]);
    const [adbDevices, hdcDevices] = await Promise.all([
      listAdbDevices(executable).catch(() => []),
      listHdcDevices(hdcPath).catch(() => []),
    ]);
    const devices = [...adbDevices, ...hdcDevices];
    sendDevicesEvent(devices);
    return devices;
  });
  ipcMain.handle("adb:run", (_, rawToolPaths: unknown, args: string[], requestedBackend?: "adb" | "hdc") => {
    const { adbPath, hdcPath } = commandToolPaths(rawToolPaths);
    if (requestedBackend === "hdc") {
      const { target, rest } = splitTargetArgs(args);
      if (rest[0] === "shell" && rest[1] === "wm" && rest[2] === "size") {
        return hdcScreenSizeText(hdcPath, target).then(
          (text) => ({ code: 0, text }),
          (error) => ({ code: -1, text: String(error) }),
        );
      }
      return runCommand(hdcPath, hdcCompatArgs(args), "hdc");
    }
    return runCommand(adbPath, args, "adb");
  });
  ipcMain.handle("adb:screenshot", async (_, rawToolPaths: unknown, device: string) => {
    const { backend, target } = splitDeviceBackend(device);
    const { adbPath, hdcPath } = commandToolPaths(rawToolPaths);
    if (backend === "hdc") {
      const result = await hdcScreenshotBuffer(hdcPath, target);
      return `data:image/png;base64,${result.toString("base64")}`;
    }
    const executable = resolveAdbExecutable(adbPath || getSettings().adbPath);
    const result = await new Promise<Buffer>((resolve, reject) => {
      const proc = spawn(executable, ["-s", target, "exec-out", "screencap", "-p"], { windowsHide: true, env: runtimeEnvironment() });
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
    const license = getLicenseStatus();
    if (license.tier !== "pro") throw new Error("多开运行需要专业版，请在设置中输入激活码");
    const maxAdditionalWindows = Math.max(0, license.maxConcurrentRunners - 1);
    if (runnerWindows.size >= maxAdditionalWindows) {
      throw new Error(`专业版最多可额外打开 ${maxAdditionalWindows} 个运行窗口`);
    }
    createRunWindow(initialPlan);
  });
  ipcMain.handle("chest:open-window", (_, initialPlan?: string, userId = "default", sourceId = "", sourceName = "") => {
    const license = getLicenseStatus();
    if (license.tier !== "pro") throw new Error("开宝箱多开需要专业版，请在设置中输入激活码");
    const maxAdditionalWindows = Math.max(0, license.maxConcurrentRunners - 1);
    if (runnerWindows.size >= maxAdditionalWindows) throw new Error(`专业版当前最多可额外打开 ${maxAdditionalWindows} 个开宝箱窗口`);
    createRunWindow(initialPlan, "chest", userId, sourceId, sourceName);
  });
  ipcMain.handle("task:start", (event, request: TaskRequest) => spawnTask(request, event.sender.id));
  ipcMain.handle("task:stop", (_, id: string) => {
    const task = tasks.get(id);
    if (!task) return;
    if (process.platform === "win32") task.kill();
    else task.kill("SIGINT");
  });
});

type ChestUser = { id: string; name: string; createdAt: string };
type ChestSource = { sourceId: string; sourceName: string };
const CHEST_INDEX_FIELDS = [
  "device",
  "user_id",
  "source_id",
  "source_name",
  "session_id",
  "pair_key",
  "pair_index",
  "pair_prefix",
  "before_label",
  "before_path",
  "before_saved_at",
  "after_label",
  "after_path",
  "after_saved_at",
  "composite_path",
  "composite_saved_at",
];

function chestUsersFile() {
  return path.join(app.getPath("userData"), "chest_users.json");
}

function chestUsers(): ChestUser[] {
  const fallback: ChestUser[] = [{ id: "default", name: "默认用户", createdAt: "" }];
  try {
    const value = JSON.parse(readFileSync(chestUsersFile(), "utf8")) as { users?: ChestUser[] };
    const users = Array.isArray(value.users) ? value.users.filter((user) => user && user.id && user.name) : [];
    return users.length ? users : fallback;
  } catch {
    return fallback;
  }
}

function requireChestPro() {
  if (getLicenseStatus().tier !== "pro") throw new Error("多用户开宝箱需要专业版，请在设置中输入激活码");
}

function writeChestUsers(users: ChestUser[]) {
  writeFileSync(chestUsersFile(), `${JSON.stringify({ version: 1, users }, null, 2)}\n`, "utf8");
}

function createChestUser(rawName: string) {
  requireChestPro();
  const name = String(rawName ?? "").trim();
  if (!name) throw new Error("用户名不能为空");
  const users = chestUsers();
  const id = `user-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const user = { id, name, createdAt: new Date().toISOString() };
  writeChestUsers([...users, user]);
  return user;
}

function renameChestUser(rawUserId: string, rawName: string) {
  requireChestPro();
  const userId = String(rawUserId ?? "");
  const name = String(rawName ?? "").trim();
  if (!userId || !name) throw new Error("用户标识和名称不能为空");
  const users = chestUsers();
  const user = users.find((entry) => entry.id === userId);
  if (!user) throw new Error("未找到用户");
  user.name = name;
  writeChestUsers(users);
  return user;
}

function chestSourcesFile() {
  return path.join(app.getPath("userData"), "chest_sources.json");
}

function readCustomChestSources(): Record<string, ChestSource[]> {
  try {
    const value = JSON.parse(readFileSync(chestSourcesFile(), "utf8")) as { sources?: Record<string, ChestSource[]> };
    return value.sources && typeof value.sources === "object" ? value.sources : {};
  } catch {
    return {};
  }
}

function writeCustomChestSources(sources: Record<string, ChestSource[]>) {
  writeFileSync(chestSourcesFile(), `${JSON.stringify({ version: 1, sources }, null, 2)}\n`, "utf8");
}

function customChestSources(rawUserId = "default"): ChestSource[] {
  const userId = String(rawUserId || "default");
  const sources = readCustomChestSources()[userId] ?? [];
  return sources.filter((source) => source && typeof source.sourceId === "string" && source.sourceId.startsWith("custom_") && typeof source.sourceName === "string" && source.sourceName.trim());
}

function addCustomChestSource(rawUserId: string, rawName: string) {
  const userId = String(rawUserId || "default");
  const sourceName = String(rawName || "").trim();
  if (!sourceName) throw new Error("自定义来源名称不能为空");
  const allSources = readCustomChestSources();
  const sources = allSources[userId] ?? [];
  const existing = sources.find((source) => source.sourceName === sourceName);
  if (existing) return existing;
  const baseId = `custom_${sourceName.replace(/[^a-zA-Z0-9\u4e00-\u9fff]+/g, "_").replace(/^_+|_+$/g, "") || "source"}`;
  let sourceId = baseId;
  let suffix = 2;
  while (sources.some((source) => source.sourceId === sourceId)) sourceId = `${baseId}_${suffix++}`;
  const source = { sourceId, sourceName };
  allSources[userId] = [...sources, source];
  writeCustomChestSources(allSources);
  return source;
}

function deleteCustomChestSource(rawUserId: string, rawSourceId: string) {
  const userId = String(rawUserId || "default");
  const sourceId = String(rawSourceId || "").trim();
  if (!sourceId.startsWith("custom_")) throw new Error("只能删除自定义来源");
  const allSources = readCustomChestSources();
  const sources = allSources[userId] ?? [];
  const removed = sources.find((source) => source.sourceId === sourceId);
  if (!removed) throw new Error("未找到自定义来源");
  allSources[userId] = sources.filter((source) => source.sourceId !== sourceId);
  writeCustomChestSources(allSources);
  return removed;
}

function matchesChestDevice(record: Record<string, unknown>, device: string): boolean {
  if (!device) return true;
  const recordDevice = String(record.device ?? "");
  return !recordDevice || recordDevice === device;
}

function matchesChestUser(record: Record<string, unknown>, userId: string): boolean {
  if (!userId) return true;
  return String(record.user_id ?? "default") === userId;
}

function legacyChestSource(userId: string): ChestSource {
  const userName = chestUsers().find((user) => user.id === userId)?.name ?? "";
  if (userName === "潇然") return { sourceId: "boss_jinjia", sourceName: "金甲" };
  if (userName === "熊大") return { sourceId: "boss_dayan", sourceName: "大眼" };
  return { sourceId: "", sourceName: "" };
}

function chestSourceFromRecord(record: Record<string, unknown>, fallbackUserId?: string): ChestSource {
  const sourceId = String(record.source_id ?? "").trim();
  const sourceName = String(record.source_name ?? "").trim();
  if (sourceId || sourceName) return { sourceId, sourceName: sourceName || sourceId };
  return legacyChestSource(String(record.user_id ?? fallbackUserId ?? "default"));
}

function chestSourceStateFile(rawUserId: string, rawTaskId: string) {
  const safeUser = String(rawUserId || "default").replace(/[^a-zA-Z0-9_-]/g, "_");
  const safeTask = String(rawTaskId || "chest").replace(/[^a-zA-Z0-9_-]/g, "_");
  return path.join(chestResultsRoot(), "active_sources", `${safeUser}_${safeTask}.json`);
}

function setChestActiveSource(rawUserId: string, rawTaskId: string, rawSourceId: string, rawSourceName: string) {
  const userId = String(rawUserId || "default");
  const sourceId = String(rawSourceId || "").trim();
  const sourceName = String(rawSourceName || "").trim();
  if (!sourceId || !sourceName) throw new Error("宝箱来源不能为空");
  const file = chestSourceStateFile(userId, rawTaskId);
  mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(file, `${JSON.stringify({ source_id: sourceId, source_name: sourceName })}\n`, "utf8");
  return { sourceFile: file, sourceId, sourceName };
}

function chestScreenshotSources(): Map<string, ChestSource> {
  const indexFile = path.join(chestResultsRoot(), "index.jsonl");
  if (!existsSync(indexFile)) return new Map();
  const sources = new Map<string, ChestSource>();
  for (const line of readFileSync(indexFile, "utf8").split(/\r?\n/).filter(Boolean)) {
    try {
      const record = JSON.parse(line) as Record<string, unknown>;
      const screenshotPath = String(record.before_path ?? "");
      if (screenshotPath) sources.set(screenshotPath, chestSourceFromRecord(record));
    } catch { /* ignore malformed screenshot records */ }
  }
  return sources;
}

function attachChestEventSource(event: Record<string, unknown>, screenshotSources: Map<string, ChestSource>): Record<string, unknown> {
  const source = screenshotSources.get(String(event.screenshot_path ?? "")) ?? chestSourceFromRecord(event);
  return { ...event, source_id: source.sourceId, source_name: source.sourceName };
}

function migrateLegacyChestSources() {
  const indexFile = path.join(chestResultsRoot(), "index.jsonl");
  if (existsSync(indexFile)) {
    const records = readFileSync(indexFile, "utf8").split(/\r?\n/).filter(Boolean).flatMap((line) => {
      try { return [JSON.parse(line) as Record<string, unknown>]; } catch { return []; }
    });
    let changed = false;
    for (const record of records) {
      if (String(record.source_id ?? "").trim() || String(record.source_name ?? "").trim()) continue;
      const source = legacyChestSource(String(record.user_id ?? "default"));
      if (!source.sourceId) continue;
      record.source_id = source.sourceId;
      record.source_name = source.sourceName;
      changed = true;
    }
    if (changed) writeFileSync(indexFile, `${records.map((record) => JSON.stringify(record)).join("\n")}\n`, "utf8");
  }

  const eventFile = path.join(chestResultsRoot(), "item_events.jsonl");
  if (!existsSync(eventFile)) return;
  const screenshotSources = chestScreenshotSources();
  const events = readFileSync(eventFile, "utf8").split(/\r?\n/).filter(Boolean).flatMap((line) => {
    try { return [JSON.parse(line) as Record<string, unknown>]; } catch { return []; }
  });
  let changed = false;
  for (const event of events) {
    if (String(event.source_id ?? "").trim() || String(event.source_name ?? "").trim()) continue;
    const source = screenshotSources.get(String(event.screenshot_path ?? "")) ?? legacyChestSource(String(event.user_id ?? "default"));
    if (!source.sourceId) continue;
    event.source_id = source.sourceId;
    event.source_name = source.sourceName;
    changed = true;
  }
  if (changed) writeFileSync(eventFile, `${events.map((event) => JSON.stringify(event)).join("\n")}\n`, "utf8");
}

function chestScreenshotUserIds(): Map<string, string> {
  const indexFile = path.join(chestResultsRoot(), "index.jsonl");
  if (!existsSync(indexFile)) return new Map();
  const users = new Map<string, string>();
  for (const line of readFileSync(indexFile, "utf8").split(/\r?\n/).filter(Boolean)) {
    try {
      const record = JSON.parse(line) as Record<string, unknown>;
      const screenshotPath = String(record.before_path ?? "");
      if (screenshotPath) users.set(screenshotPath, String(record.user_id ?? "default"));
    } catch { /* ignore malformed screenshot records */ }
  }
  return users;
}

function matchesChestEventUser(event: Record<string, unknown>, userId: string, screenshotUsers: Map<string, string>): boolean {
  if (!userId) return true;
  const screenshotUser = screenshotUsers.get(String(event.screenshot_path ?? ""));
  return (screenshotUser ?? String(event.user_id ?? "default")) === userId;
}

function chestScreenshotRecords(device = "", userId = "default"): Array<Record<string, unknown>> {
  const indexFile = path.join(runtimeRoot(), "diagnostics", "chest_results", "index.jsonl");
  if (!existsSync(indexFile)) return [];
  return readFileSync(indexFile, "utf8")
    .split(/\r?\n/)
    .filter(Boolean)
    .flatMap((line) => {
      try {
        const record = JSON.parse(line) as Record<string, unknown>;
        if (!matchesChestDevice(record, device) || !matchesChestUser(record, userId)) return [];
        const source = chestSourceFromRecord(record);
        return [{ ...record, source_id: source.sourceId, source_name: source.sourceName }];
      } catch { return []; }
    });
}

function chestItemEvents(day: string, device = "", userId = "default"): Array<Record<string, unknown>> {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return [];
  const eventsFile = path.join(runtimeRoot(), "diagnostics", "chest_results", "item_events.jsonl");
  if (!existsSync(eventsFile)) return [];
  const screenshotUsers = chestScreenshotUserIds();
  const screenshotSources = chestScreenshotSources();
  return readFileSync(eventsFile, "utf8")
    .split(/\r?\n/)
    .filter(Boolean)
    .flatMap((line) => {
      try {
        const event = JSON.parse(line) as Record<string, unknown>;
        return matchesChestDevice(event, device) && matchesChestEventUser(event, userId, screenshotUsers)
          ? [attachChestEventSource(event, screenshotSources)]
          : [];
      } catch { return []; }
    })
    .filter((event) => String(event.captured_at ?? "").startsWith(day))
    .sort((a, b) => String(b.captured_at ?? "").localeCompare(String(a.captured_at ?? "")));
}

function canonicalChestItemName(rawName: unknown): string {
  const name = String(rawName ?? "").trim();
  return name === "蓝色石头" ? "蓝石头" : name;
}

function buildChestItemSummary(events: Array<Record<string, unknown>>, totalEvents = events.length): Array<Record<string, unknown>> {
  const catalogFile = path.join(chestResultsRoot(), "item_catalog.json");
  let catalogItems: Array<Record<string, unknown>> = [];
  try {
    const catalog = JSON.parse(readFileSync(catalogFile, "utf8")) as { items?: Array<Record<string, unknown>> };
    catalogItems = catalog.items ?? [];
  } catch { /* summary can still render without catalog weights */ }
  const weightsById = new Map(catalogItems.map((item) => [String(item.item_id ?? ""), item.weight]));
  const weightsByName = new Map(catalogItems
    .filter((item) => item.name && item.weight !== undefined && item.weight !== null)
    .map((item) => [canonicalChestItemName(item.name), item.weight]));
  const totals = new Map<string, Record<string, unknown>>();
  for (const event of events) {
    const sourceId = String(event.source_id ?? "").trim();
    const sourceName = String(event.source_name ?? "").trim() || sourceId || "未分类";
    const items = Array.isArray(event.items) ? event.items : [];
    for (const rawItem of items) {
      if (!rawItem || typeof rawItem !== "object") continue;
      const item = rawItem as Record<string, unknown>;
      const itemId = String(item.item_id ?? "unknown");
      const itemName = canonicalChestItemName(item.item_name) || "待标注物品";
      // Keep unknown items separate until they have a real catalog name;
      // merge named items across different item IDs for daily totals.
      const summaryKey = itemName === "待标注物品"
        ? `${sourceId}:${itemId}`
        : `${sourceId}:name:${itemName}`;
      const current = totals.get(summaryKey) ?? {
        itemId,
        itemName,
        sourceId,
        sourceName,
        totalQuantity: 0,
        itemCount: 0,
        unreadQuantityCount: 0,
        cropPath: String(item.crop_path ?? ""),
        iconCropPath: String(item.icon_crop_path ?? item.crop_path ?? ""),
      };
      current.itemCount = Number(current.itemCount) + 1;
      const quantity = Number(item.quantity);
      if (Number.isFinite(quantity) && quantity > 0) current.totalQuantity = Number(current.totalQuantity) + quantity;
      else current.unreadQuantityCount = Number(current.unreadQuantityCount) + 1;
      totals.set(summaryKey, current);
    }
  }
  return [...totals.entries()].map(([summaryKey, item]) => {
    const unread = Number(item.unreadQuantityCount ?? 0);
    const weight = weightsByName.get(canonicalChestItemName(item.itemName)) ?? weightsById.get(String(item.itemId ?? ""));
    return {
      ...item,
      weight: Number.isFinite(Number(weight)) ? Number(weight) : null,
      dropProbability: totalEvents ? Math.round((Number(item.itemCount) / totalEvents) * 10000) / 100 : 0,
      expectedQuantity: totalEvents && unread === 0 ? Math.round((Number(item.totalQuantity) / totalEvents) * 100) / 100 : null,
    };
  }).sort((a, b) => {
    const left = a as Record<string, unknown>;
    const right = b as Record<string, unknown>;
    const leftWeight = Number(left.weight);
    const rightWeight = Number(right.weight);
    const leftHasWeight = left.weight !== null && left.weight !== undefined && String(left.weight).trim() !== "" && Number.isFinite(leftWeight);
    const rightHasWeight = right.weight !== null && right.weight !== undefined && String(right.weight).trim() !== "" && Number.isFinite(rightWeight);
    if (leftHasWeight !== rightHasWeight) return leftHasWeight ? -1 : 1;
    if (leftHasWeight && leftWeight !== rightWeight) return leftWeight - rightWeight;
    return Number(right.totalQuantity) - Number(left.totalQuantity);
  });
}

function chestItemSummary(day: string, device = "", userId = "default") {
  return buildChestItemSummary(chestItemEvents(day, device, userId));
}

function chestAllItemEvents(device = "", userId = "default") {
  const eventsFile = path.join(runtimeRoot(), "diagnostics", "chest_results", "item_events.jsonl");
  if (!existsSync(eventsFile)) return [];
  const screenshotUsers = chestScreenshotUserIds();
  const screenshotSources = chestScreenshotSources();
  return readFileSync(eventsFile, "utf8").split(/\r?\n/).filter(Boolean).flatMap((line) => {
    try {
      const event = JSON.parse(line) as Record<string, unknown>;
      return matchesChestDevice(event, device) && matchesChestEventUser(event, userId, screenshotUsers)
        ? [attachChestEventSource(event, screenshotSources)]
        : [];
    } catch { return []; }
  });
}

function chestSummaryRange(endDay: string, range: string, device = "", userId = "default", customStartDay?: string, sourceId = "") {
  const datePattern = /^\d{4}-\d{2}-\d{2}$/;
  if (!datePattern.test(endDay)) return { items: [], boxCount: 0 };
  let startDay = endDay;
  if (range === "custom") {
    if (!datePattern.test(String(customStartDay ?? ""))) return { items: [], boxCount: 0 };
    startDay = String(customStartDay);
    if (startDay > endDay) throw new Error("自定义统计范围的开始日期不能晚于结束日期");
  } else {
    const spanDays = range === "month" ? 30 : range === "7d" ? 7 : 1;
    const endParts = endDay.split("-").map(Number);
    const endOrdinal = Date.UTC(endParts[0], endParts[1] - 1, endParts[2]);
    const start = new Date(endOrdinal - (spanDays - 1) * 24 * 60 * 60 * 1000);
    startDay = [
      start.getUTCFullYear(),
      String(start.getUTCMonth() + 1).padStart(2, "0"),
      String(start.getUTCDate()).padStart(2, "0"),
    ].join("-");
  }
  const inRange = (timestamp: unknown) => {
    const day = String(timestamp ?? "").slice(0, 10);
    return day >= startDay && day <= endDay;
  };
  const selectedSourceId = String(sourceId ?? "").trim();
  const records = chestScreenshotRecords(device, userId).filter((record) => inRange(record.before_saved_at ?? record.saved_at) && (!selectedSourceId || String(record.source_id ?? "").trim() === selectedSourceId));
  const events = chestAllItemEvents(device, userId).filter((event) => {
    const day = String(event.captured_at ?? "").slice(0, 10);
    return day >= startDay && day <= endDay && (!selectedSourceId || String(event.source_id ?? "").trim() === selectedSourceId);
  });
  const recordPaths = new Set(records.map((record) => String(record.before_path ?? "")));
  const boxCount = records.length + events.filter((event) => !recordPaths.has(String(event.screenshot_path ?? ""))).length;
  return { items: buildChestItemSummary(events, boxCount || events.length), boxCount: boxCount || events.length, startDay, endDay, range, sourceId: selectedSourceId };
}

function exportChestReport(endDay: string, range: string, device = "", userId = "default", customStartDay?: string, sourceId = "") {
  const summary = chestSummaryRange(endDay, range, device, userId, customStartDay, sourceId);
  const directory = path.join(chestResultsRoot(), "reports");
  mkdirSync(directory, { recursive: true });
  const safeUser = userId.replace(/[^a-zA-Z0-9_-]/g, "_") || "default";
  const userName = chestUsers().find((user) => user.id === userId)?.name ?? userId;
  const rangeFilePart = range === "custom" ? `${summary.startDay}_${endDay}_custom` : `${endDay}_${range}`;
  const sourceName = sourceId ? String(summary.items.find((item) => String(item.sourceId ?? "") === sourceId)?.sourceName ?? sourceId) : "全部";
  const safeSource = sourceId.replace(/[^a-zA-Z0-9\u4e00-\u9fff_-]/g, "_") || "all";
  const file = path.join(directory, `${rangeFilePart}_${safeUser}_${safeSource}_chest_report.csv`);
  const escape = (value: unknown) => `"${String(value ?? "").replace(/"/g, '""')}"`;
  const lines = [
    ["用户", userName, "统计开始日期", summary.startDay, "统计结束日期", summary.endDay, "来源筛选", sourceName, "宝箱数量", summary.boxCount],
    [],
    ["来源", "物品", "累计数量", "掉落次数", "掉落概率(%)", "期望/次开箱"],
    ...summary.items.map((item) => [item.sourceName ?? "未分类", item.itemName, item.totalQuantity, item.itemCount, item.dropProbability, item.expectedQuantity ?? "待识别"]),
  ];
  writeFileSync(file, `\uFEFF${lines.map((line) => line.map(escape).join(",")).join("\n")}\n`, "utf8");
  return { file, boxCount: summary.boxCount };
}

function chestSyncEventId(event: Record<string, unknown>) {
  if (typeof event.sync_id === "string" && event.sync_id) return event.sync_id;
  const items = Array.isArray(event.items) ? event.items.map((raw) => {
    const item = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
    return [item.item_id, item.item_name, item.quantity, item.slot];
  }) : [];
  return createHash("sha256").update(JSON.stringify([
    event.captured_at,
    event.source_id,
    event.source_name,
    event.reward_kind,
    items,
  ])).digest("hex").slice(0, 32);
}

function exportChestSyncPackage(userId: string) {
  const user = chestUsers().find((entry) => entry.id === userId) ?? { id: userId, name: userId, createdAt: "" };
  const events = chestAllItemEvents("", userId);
  const referencedIds = new Set<string>();
  const icons: Record<string, string> = {};
  const root = chestResultsRoot();
  const safeIcon = (rawPath: unknown) => {
    const target = path.resolve(String(rawPath ?? ""));
    return target.startsWith(`${root}${path.sep}`) && existsSync(target) ? target : "";
  };
  const exportedEvents = events.map((event) => {
    const items = Array.isArray(event.items) ? event.items.map((raw) => {
      const item = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
      const itemId = String(item.item_id ?? "");
      if (itemId) referencedIds.add(itemId);
      const iconPath = safeIcon(item.icon_crop_path ?? item.crop_path);
      if (itemId && iconPath && !icons[itemId]) icons[itemId] = readFileSync(iconPath).toString("base64");
      const { crop_path: _cropPath, icon_crop_path: _iconCropPath, ...portableItem } = item;
      return portableItem;
    }) : [];
    const { screenshot_path: _screenshotPath, user_id: _userId, device: _device, ...portableEvent } = event;
    return { ...portableEvent, sync_id: chestSyncEventId(event), items };
  });
  let catalogItems: Array<Record<string, unknown>> = [];
  try {
    const catalog = JSON.parse(readFileSync(path.join(root, "item_catalog.json"), "utf8")) as { items?: Array<Record<string, unknown>> };
    catalogItems = (catalog.items ?? []).filter((item) => referencedIds.has(String(item.item_id ?? "")));
  } catch { /* event details remain exportable without a catalog */ }
  const packageData = {
    format: "bs-manager-chest-sync",
    version: 1,
    exported_at: new Date().toISOString(),
    user: { id: user.id, name: user.name },
    events: exportedEvents,
    catalog: catalogItems,
    icons,
  };
  const date = new Date().toISOString().slice(0, 10);
  return dialog.showSaveDialog(mainWindow!, {
    title: "导出开箱同步数据",
    defaultPath: `${user.name || user.id}_${date}.chest-sync.json`,
    filters: [{ name: "开箱同步数据", extensions: ["json"] }],
  }).then((result) => {
    if (result.canceled || !result.filePath) return { canceled: true, events: 0, icons: 0 };
    writeFileSync(result.filePath, `${JSON.stringify(packageData)}\n`, "utf8");
    return { canceled: false, file: result.filePath, events: exportedEvents.length, icons: Object.keys(icons).length };
  });
}

function importChestSyncPackage(userId: string) {
  return dialog.showOpenDialog(mainWindow!, {
    title: "导入开箱同步数据",
    properties: ["openFile"],
    filters: [{ name: "开箱同步数据", extensions: ["json"] }],
  }).then((result) => {
    if (result.canceled || !result.filePaths[0]) return { canceled: true, imported: 0, skipped: 0, icons: 0 };
    let data: Record<string, unknown>;
    try { data = JSON.parse(readFileSync(result.filePaths[0], "utf8")) as Record<string, unknown>; }
    catch { throw new Error("同步数据文件不是有效 JSON"); }
    if (data.format !== "bs-manager-chest-sync" || data.version !== 1 || !Array.isArray(data.events)) {
      throw new Error("不是受支持的开箱同步数据文件");
    }
    const root = chestResultsRoot();
    const iconDirectory = path.join(root, "synced_icons");
    mkdirSync(iconDirectory, { recursive: true });
    const icons = data.icons && typeof data.icons === "object" ? data.icons as Record<string, unknown> : {};
    let importedIcons = 0;
    const iconPathFor = (itemId: string) => {
      const encoded = icons[itemId];
      if (typeof encoded !== "string" || !/^[A-Za-z0-9+/=]+$/.test(encoded)) return "";
      const bytes = Buffer.from(encoded, "base64");
      if (!bytes.length || bytes.length > 2 * 1024 * 1024) return "";
      const safeId = itemId.replace(/[^a-zA-Z0-9_-]/g, "_");
      const target = path.join(iconDirectory, `${safeId}.png`);
      if (!existsSync(target)) {
        writeFileSync(target, bytes);
        importedIcons += 1;
      }
      return target;
    };
    const catalogFile = path.join(root, "item_catalog.json");
    let catalog: { version?: number; items?: Array<Record<string, unknown>> } = { version: 1, items: [] };
    try { catalog = JSON.parse(readFileSync(catalogFile, "utf8")) as typeof catalog; } catch { /* create a catalog below */ }
    const catalogById = new Map((catalog.items ?? []).map((item) => [String(item.item_id ?? ""), item]));
    for (const raw of Array.isArray(data.catalog) ? data.catalog : []) {
      if (!raw || typeof raw !== "object") continue;
      const incoming = raw as Record<string, unknown>;
      const itemId = String(incoming.item_id ?? "");
      if (!itemId) continue;
      const existing = catalogById.get(itemId);
      if (!existing) {
        const created = { ...incoming, hashes: Array.isArray(incoming.hashes) ? incoming.hashes : [] };
        catalogById.set(itemId, created);
        continue;
      }
      const hashes = new Set([...(Array.isArray(existing.hashes) ? existing.hashes : []), ...(Array.isArray(incoming.hashes) ? incoming.hashes : [])]);
      existing.hashes = [...hashes];
      if (String(existing.category ?? "unknown") === "unknown" && String(incoming.category ?? "unknown") !== "unknown") {
        existing.name = incoming.name;
        existing.category = incoming.category;
        if (incoming.weight !== undefined) existing.weight = incoming.weight;
      }
    }
    catalog.items = [...catalogById.values()];
    writeFileSync(catalogFile, `${JSON.stringify(catalog, null, 2)}\n`, "utf8");

    const existingEvents = chestItemEventsFromFile();
    const knownSyncIds = new Set(existingEvents.map(chestSyncEventId));
    let imported = 0;
    let skipped = 0;
    for (const raw of data.events) {
      if (!raw || typeof raw !== "object") continue;
      const incoming = raw as Record<string, unknown>;
      const syncId = chestSyncEventId(incoming);
      if (knownSyncIds.has(syncId)) { skipped += 1; continue; }
      const items = Array.isArray(incoming.items) ? incoming.items.flatMap((rawItem, index) => {
        if (!rawItem || typeof rawItem !== "object") return [];
        const item = rawItem as Record<string, unknown>;
        const itemId = String(item.item_id ?? "");
        const iconPath = itemId ? iconPathFor(itemId) : "";
        return [{ ...item, slot: Number(item.slot) || index + 1, crop_path: iconPath, icon_crop_path: iconPath }];
      }) : [];
      const event = {
        event_id: `sync-${syncId.slice(0, 16)}`,
        sync_id: syncId,
        user_id: userId,
        screenshot_path: `sync://${syncId}`,
        captured_at: String(incoming.captured_at ?? ""),
        reward_kind: String(incoming.reward_kind ?? "items"),
        source_id: String(incoming.source_id ?? ""),
        source_name: String(incoming.source_name ?? "") || "未分类",
        items,
        review_required: items.some((item) => {
          const quantity = (item as Record<string, unknown>).quantity;
          return quantity === null || quantity === undefined;
        }),
        imported_at: new Date().toISOString(),
      };
      if (!/^\d{4}-\d{2}-\d{2}/.test(event.captured_at)) { skipped += 1; continue; }
      existingEvents.push(event);
      knownSyncIds.add(syncId);
      imported += 1;
    }
    existingEvents.sort((left, right) => String(right.captured_at ?? "").localeCompare(String(left.captured_at ?? "")));
    writeFileSync(path.join(root, "item_events.jsonl"), existingEvents.map((event) => JSON.stringify(event)).join("\n") + (existingEvents.length ? "\n" : ""), "utf8");
    return { canceled: false, imported, skipped, icons: importedIcons };
  });
}

function runChestAnalyzer(day?: string, userId?: string, ownerWebContentsId?: number) {
  const root = path.join(runtimeRoot(), "diagnostics", "chest_results");
  const script = path.join(runtimeRoot(), "chest_analyzer.py");
  const executable = resolveExecutable(getSettings().pythonPath, process.platform === "win32" ? "python.exe" : "python3");
  if (day && !/^\d{4}-\d{2}-\d{2}$/.test(day)) {
    return Promise.reject(new Error("重新识别日期格式无效"));
  }
  const args = [script, "--input-dir", root, "--force"];
  if (day) args.push("--day", day);
  if (userId) args.push("--user-id", userId);
  return new Promise<Record<string, unknown>>((resolve, reject) => {
    const taskId = `chest-reanalyze-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
    const process = spawn(executable, args, { windowsHide: true, env: runtimeEnvironment() });
    trackTask(taskId, process, ownerWebContentsId, `${executable} ${args.join(" ")}`);
    let output = "";
    process.stdout.on("data", (data) => (output += data.toString("utf8")));
    process.stderr.on("data", (data) => (output += data.toString("utf8")));
    process.on("error", reject);
    process.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(output || `物品识别进程退出码 ${code}`));
        return;
      }
      try {
        resolve(JSON.parse(output.trim()) as Record<string, unknown>);
      } catch {
        resolve({ message: output.trim() });
      }
    });
  });
}

function chestResultsRoot() {
  return path.join(runtimeRoot(), "diagnostics", "chest_results");
}

function chestEventPathKey(rawPath: unknown) {
  const value = String(rawPath || "");
  return /^[a-z][a-z0-9+.-]*:\/\//i.test(value) ? value : path.resolve(value);
}

function writeChestScreenshotIndexCsv(root: string, records: Array<Record<string, unknown>>) {
  const escape = (value: unknown) => `"${String(value ?? "").replace(/"/g, "\"\"")}"`;
  const rows = [
    CHEST_INDEX_FIELDS,
    ...records.map((record) => CHEST_INDEX_FIELDS.map((field) => record[field])),
  ];
  writeFileSync(path.join(root, "index.csv"), `${rows.map((row) => row.map(escape).join(",")).join("\n")}\n`, "utf8");
}

function normalizeChestCatalogItems(items: Array<Record<string, unknown>>): Array<Record<string, unknown>> {
  const merged = new Map<string, Record<string, unknown>>();
  for (const item of items) {
    const itemId = String(item.item_id ?? "");
    if (!itemId) continue;
    const current = merged.get(itemId);
    if (!current) {
      merged.set(itemId, { ...item, hashes: Array.isArray(item.hashes) ? [...item.hashes] : [] });
      continue;
    }
    const hashes = Array.isArray(current.hashes) ? current.hashes : [];
    for (const hash of Array.isArray(item.hashes) ? item.hashes : []) if (!hashes.includes(hash)) hashes.push(hash);
    current.hashes = hashes;
    if (String(current.name ?? "待标注物品") === "待标注物品" && String(item.name ?? "待标注物品") !== "待标注物品") {
      current.name = item.name;
      current.category = item.category;
    }
  }
  return [...merged.values()];
}

function chestUnlabeledItems(): Array<Record<string, unknown>> {
  const result = new Map<string, Record<string, unknown>>();
  const catalogFile = path.join(chestResultsRoot(), "item_catalog.json");
  let catalogItems: Array<Record<string, unknown>> = [];
  try {
    const catalog = JSON.parse(readFileSync(catalogFile, "utf8")) as { items?: Array<Record<string, unknown>> };
    catalogItems = Array.isArray(catalog.items) ? catalog.items : [];
  } catch { /* no catalog yet */ }
  catalogItems = normalizeChestCatalogItems(catalogItems);
  const labels = new Map(catalogItems.map((item) => [String(item.item_id ?? ""), item]));
  const labelsByName = new Map(catalogItems
    .filter((item) => String(item.category ?? "unknown") !== "unknown" && String(item.name ?? "") !== "待标注物品")
    .map((item) => [String(item.name), item]));
  for (const event of chestItemEventsFromFile()) {
    for (const rawItem of Array.isArray(event.items) ? event.items : []) {
      if (!rawItem || typeof rawItem !== "object") continue;
      const item = rawItem as Record<string, unknown>;
      const itemId = String(item.item_id ?? "");
      if (!itemId) continue;
      const existing = result.get(itemId);
      if (existing) {
        existing.occurrences = Number(existing.occurrences ?? 0) + 1;
        continue;
      }
      const catalogItem = labels.get(itemId) ?? labelsByName.get(String(item.item_name ?? ""));
      const name = String(catalogItem?.name ?? item.item_name ?? "待标注物品");
      const labeled = String(catalogItem?.category ?? "unknown") !== "unknown" && name !== "待标注物品";
      result.set(itemId, { itemId, name, labeled, weight: catalogItem?.weight ?? null, cropPath: String(item.icon_crop_path ?? item.crop_path ?? ""), occurrences: 1 });
    }
  }
  return [...result.values()];
}

function setChestItemWeight(rawItemId: string, rawWeight: number | null) {
  const itemId = String(rawItemId ?? "");
  if (!itemId) throw new Error("物品标识无效");
  const weight = rawWeight === null || rawWeight === undefined ? null : Number(rawWeight);
  if (weight !== null && (!Number.isFinite(weight) || weight < 0)) throw new Error("权重必须是非负数字");
  const file = path.join(chestResultsRoot(), "item_catalog.json");
  if (!existsSync(file)) throw new Error("物品图鉴不存在");
  const catalog = JSON.parse(readFileSync(file, "utf8")) as { items?: Array<Record<string, unknown>> };
  const item = (catalog.items ?? []).find((entry) => String(entry.item_id ?? "") === itemId);
  if (!item) throw new Error("未找到物品图鉴");
  if (weight === null) delete item.weight;
  else item.weight = weight;
  writeFileSync(file, `${JSON.stringify(catalog, null, 2)}\n`, "utf8");
  return { itemId, weight };
}

function chestItemEventsFromFile(): Array<Record<string, unknown>> {
  const eventsFile = path.join(chestResultsRoot(), "item_events.jsonl");
  if (!existsSync(eventsFile)) return [];
  return readFileSync(eventsFile, "utf8").split(/\r?\n/).filter(Boolean).flatMap((line) => {
    try { return [JSON.parse(line) as Record<string, unknown>]; } catch { return []; }
  });
}

function labelChestItem(rawItemId: string, rawName: string) {
  const itemId = String(rawItemId ?? "");
  const name = String(rawName ?? "").trim();
  if (!itemId || !name) throw new Error("物品标识或名称无效");
  const root = chestResultsRoot();
  const catalogFile = path.join(root, "item_catalog.json");
  let catalog: { items?: Array<Record<string, unknown>> } = { items: [] };
  try { catalog = JSON.parse(readFileSync(catalogFile, "utf8")) as { items?: Array<Record<string, unknown>> }; } catch { /* created by analyzer later */ }
  const items = Array.isArray(catalog.items) ? catalog.items : [];
  const normalizedItems = normalizeChestCatalogItems(items);
  const eventName = chestItemEventsFromFile()
    .flatMap((event) => Array.isArray(event.items) ? event.items : [])
    .find((rawItem) => rawItem && typeof rawItem === "object" && String((rawItem as Record<string, unknown>).item_id ?? "") === itemId && String((rawItem as Record<string, unknown>).item_name ?? ""));
  const target = normalizedItems.find((item) => String(item.item_id ?? "") === itemId)
    ?? normalizedItems.find((item) => String(item.name ?? "") === String((eventName as Record<string, unknown> | undefined)?.item_name ?? "") && String(item.category ?? "unknown") !== "unknown");
  if (!target) throw new Error("未找到待标注物品");
  const previousId = itemId;
  let nextId = String(target.item_id);
  if (String(target.category ?? "unknown") === "unknown") {
    let nextNumber = 1;
    const usedIds = new Set(normalizedItems.map((item) => String(item.item_id ?? "")));
    nextId = `item_${String(nextNumber).padStart(4, "0")}`;
    while (usedIds.has(nextId)) {
      nextNumber += 1;
      nextId = `item_${String(nextNumber).padStart(4, "0")}`;
    }
  }
  target.item_id = nextId;
  target.name = name;
  target.category = "labeled";
  catalog.items = normalizedItems;
  writeFileSync(catalogFile, `${JSON.stringify(catalog, null, 2)}\n`, "utf8");

  const events = chestItemEventsFromFile();
  for (const event of events) {
    for (const rawItem of Array.isArray(event.items) ? event.items : []) {
      if (rawItem && typeof rawItem === "object" && String((rawItem as Record<string, unknown>).item_id ?? "") === previousId) {
        (rawItem as Record<string, unknown>).item_id = nextId;
        (rawItem as Record<string, unknown>).item_name = name;
      }
    }
  }
  writeFileSync(path.join(root, "item_events.jsonl"), events.map((event) => JSON.stringify(event)).join("\n") + (events.length ? "\n" : ""), "utf8");
  return { itemId: nextId, name };
}

function correctChestEvent(
  rawScreenshotPath: string,
  correctionsInput: Array<{ slot: number; itemName?: string | null; itemId?: string | null; iconCropPath?: string | null; quantity: number | null }>,
  metadata?: { userId: string; sourceId: string; sourceName: string },
) {
  const root = chestResultsRoot();
  const targetPath = chestEventPathKey(rawScreenshotPath);
  const events = chestItemEventsFromFile();
  const event = events.find((candidate) => chestEventPathKey(candidate.screenshot_path) === targetPath);
  if (!event) throw new Error("未找到对应的开箱事件");
  const userId = String(metadata?.userId ?? event.user_id ?? "default");
  const sourceId = String(metadata?.sourceId ?? event.source_id ?? "").trim();
  const sourceName = String(metadata?.sourceName ?? event.source_name ?? "").trim();
  if (!chestUsers().some((user) => user.id === userId)) throw new Error("未找到校准用户");
  if (!sourceId || !sourceName) throw new Error("宝箱来源不能为空");
  const clean = correctionsInput
    .filter((item) => Number.isInteger(item.slot) && item.slot > 0 && (item.quantity === null || (Number.isInteger(item.quantity) && item.quantity >= 0)))
    .map((item) => {
      const itemName = String(item.itemName ?? "").trim();
      return {
        slot: item.slot,
        quantity: item.quantity,
        ...(itemName ? { item_name: itemName } : {}),
        ...(String(item.itemId ?? "").trim() ? { item_id: String(item.itemId).trim() } : {}),
        ...(String(item.iconCropPath ?? "").trim() ? { icon_crop_path: String(item.iconCropPath).trim(), crop_path: String(item.iconCropPath).trim() } : {}),
      };
    });
  if (clean.some((item) => !String(item.item_name ?? "").trim())) throw new Error("校准物品名称不能为空");
  const correctionsPath = path.join(root, "manual_item_corrections.json");
  let corrections: { version: number; events: Record<string, Array<Record<string, unknown>>> } = { version: 1, events: {} };
  try { corrections = JSON.parse(readFileSync(correctionsPath, "utf8")) as typeof corrections; } catch { /* create on first calibration */ }
  const capturedAt = String(event.captured_at ?? "");
  const correctionKey = String(event.event_id ?? targetPath);
  const previous = corrections.events[capturedAt] ?? [];
  const keyedPrevious = corrections.events[correctionKey] ?? previous;
  const previousBySlot = new Map(keyedPrevious.map((item) => [Number(item.slot), item]));
  const correctionRows = clean.map((item) => ({
    ...(previousBySlot.get(item.slot) ?? {}),
    slot: item.slot,
    quantity: item.quantity,
    item_name: item.item_name,
    ...(item.item_id ? { item_id: item.item_id } : {}),
    ...(item.icon_crop_path ? { icon_crop_path: item.icon_crop_path, crop_path: item.icon_crop_path } : {}),
  }));
  corrections.events[correctionKey] = correctionRows;
  corrections.events[targetPath] = correctionRows;
  if (capturedAt) corrections.events[capturedAt] = correctionRows;
  writeFileSync(correctionsPath, `${JSON.stringify(corrections, null, 2)}\n`, "utf8");
  const eventItems = Array.isArray(event.items) ? event.items as Array<unknown> : [];
  event.items = clean.map((item) => {
    const previous = eventItems[item.slot - 1];
    return {
      ...(previous && typeof previous === "object" ? previous as Record<string, unknown> : {}),
      slot: item.slot,
      row: Math.floor((item.slot - 1) / 5) + 1,
      column: ((item.slot - 1) % 5) + 1,
      item_id: item.item_id ?? `calibrated_${String(event.event_id ?? "event")}_${item.slot}`,
      item_name: item.item_name,
      quantity: item.quantity,
      ...(item.icon_crop_path ? { icon_crop_path: item.icon_crop_path, crop_path: item.icon_crop_path } : {}),
      manual_correction: true,
    };
  });
  event.user_id = userId;
  event.source_id = sourceId;
  event.source_name = sourceName;
  writeFileSync(path.join(root, "item_events.jsonl"), events.map((item) => JSON.stringify(item)).join("\n") + "\n", "utf8");
  const indexFile = path.join(root, "index.jsonl");
  if (existsSync(indexFile)) {
    const indexRecords = readFileSync(indexFile, "utf8").split(/\r?\n/).filter(Boolean).flatMap((line) => {
      try { return [JSON.parse(line) as Record<string, unknown>]; } catch { return []; }
    });
    for (const record of indexRecords) {
      if (chestEventPathKey(record.before_path) !== targetPath) continue;
      record.user_id = userId;
      record.source_id = sourceId;
      record.source_name = sourceName;
    }
    writeFileSync(indexFile, indexRecords.map((record) => JSON.stringify(record)).join("\n") + (indexRecords.length ? "\n" : ""), "utf8");
    writeChestScreenshotIndexCsv(root, indexRecords);
  }
  return { screenshotPath: targetPath, capturedAt, corrected: clean.length, userId, sourceId, sourceName };
}

function deleteChestEvent(rawScreenshotPath: string) {
  const root = chestResultsRoot();
  const target = chestEventPathKey(rawScreenshotPath);
  const isFileTarget = !/^[a-z][a-z0-9+.-]*:\/\//i.test(String(rawScreenshotPath || ""));
  if (isFileTarget && !target.startsWith(`${root}${path.sep}`)) throw new Error("截图路径无效");
  const currentEvents = chestItemEventsFromFile();
  const events = currentEvents.filter((event) => chestEventPathKey(event.screenshot_path) !== target);
  // The event id is derived from the absolute screenshot path, so locate its
  // crop directory by matching the event before removing it.
  const removed = currentEvents.find((event) => chestEventPathKey(event.screenshot_path) === target);
  const cropDir = removed ? path.join(root, "item_crops", String(removed.event_id ?? "")) : "";
  let foundIndexRecord = false;
  if ((!isFileTarget || !existsSync(target)) && !removed) {
    const indexFile = path.join(root, "index.jsonl");
    if (existsSync(indexFile)) {
      foundIndexRecord = readFileSync(indexFile, "utf8").split(/\r?\n/).filter(Boolean).some((line) => {
        try {
          const record = JSON.parse(line) as Record<string, unknown>;
          return chestEventPathKey(record.before_path) === target || chestEventPathKey(record.after_path) === target;
        } catch { return false; }
      });
    }
    if (!foundIndexRecord) throw new Error("截图路径无效");
  }
  if (isFileTarget && existsSync(target)) unlinkSync(target);
  writeFileSync(path.join(root, "item_events.jsonl"), events.map((event) => JSON.stringify(event)).join("\n") + (events.length ? "\n" : ""), "utf8");
  const indexFile = path.join(root, "index.jsonl");
  if (existsSync(indexFile)) {
    const indexRecords = readFileSync(indexFile, "utf8").split(/\r?\n/).filter(Boolean).flatMap((line) => {
      try { return [JSON.parse(line) as Record<string, unknown>]; } catch { return []; }
    }).filter((record) => chestEventPathKey(record.before_path) !== target && chestEventPathKey(record.after_path) !== target);
    writeFileSync(indexFile, indexRecords.map((record) => JSON.stringify(record)).join("\n") + (indexRecords.length ? "\n" : ""), "utf8");
    writeChestScreenshotIndexCsv(root, indexRecords);
  }
  if (existsSync(cropDir)) rmSync(cropDir, { recursive: true, force: true });
  return { deleted: target };
}

function deleteChestCatalogItem(itemId: string) {
  const root = chestResultsRoot();
  const file = path.join(root, "item_catalog.json");
  let catalog: { items?: Array<Record<string, unknown>> } = { items: [] };
  try { catalog = JSON.parse(readFileSync(file, "utf8")) as typeof catalog; } catch { /* remove stale event-only unknown item */ }
  const target = (catalog.items ?? []).find((item) => String(item.item_id ?? "") === itemId);
  const removeOccurrences = itemId.startsWith("unknown_") || String(target?.category ?? "unknown") === "unknown";
  catalog.items = (catalog.items ?? []).filter((item) => String(item.item_id ?? "") !== itemId);
  writeFileSync(file, `${JSON.stringify(catalog, null, 2)}\n`, "utf8");

  let removedOccurrences = 0;
  if (removeOccurrences) {
    const events = chestItemEventsFromFile();
    for (const event of events) {
      const items = Array.isArray(event.items) ? event.items : [];
      const kept = items.filter((rawItem) => {
        if (!rawItem || typeof rawItem !== "object" || String((rawItem as Record<string, unknown>).item_id ?? "") !== itemId) return true;
        removedOccurrences += 1;
        const cropPath = path.resolve(String((rawItem as Record<string, unknown>).crop_path ?? ""));
        if (cropPath.startsWith(`${root}${path.sep}`) && existsSync(cropPath)) unlinkSync(cropPath);
        const iconCropPath = path.resolve(String((rawItem as Record<string, unknown>).icon_crop_path ?? ""));
        if (iconCropPath.startsWith(`${root}${path.sep}`) && existsSync(iconCropPath)) unlinkSync(iconCropPath);
        return false;
      });
      event.items = kept;
    }
    writeFileSync(path.join(root, "item_events.jsonl"), events.map((event) => JSON.stringify(event)).join("\n") + (events.length ? "\n" : ""), "utf8");
  }
  return { deleted: itemId, removedOccurrences };
}

function runtimeDiagnosticImage(directory: string, rawPath: string): string | null {
  const root = path.resolve(runtimeRoot(), "diagnostics", directory);
  const target = path.resolve(String(rawPath || ""));
  if (!target.startsWith(`${root}${path.sep}`) || !existsSync(target)) return null;
  const extension = path.extname(target).toLowerCase();
  const mime = extension === ".jpg" || extension === ".jpeg"
    ? "image/jpeg"
    : extension === ".webp"
      ? "image/webp"
      : "image/png";
  return `data:${mime};base64,${readFileSync(target).toString("base64")}`;
}

function parseAdbDevices(output: string): string[] {
  return output
    .split(/\r?\n/)
    .slice(1)
    .map((line) => line.trim().split(/\s+/))
    .filter((parts) => parts[1] === "device")
    .map((parts) => parts[0]);
}

function parseHdcDevices(output: string): string[] {
  return output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => {
      const lowered = line.toLowerCase();
      return line && !lowered.startsWith("[empty]") && !lowered.startsWith("empty") && !lowered.startsWith("list of targets");
    })
    .map((line) => line.split(/\s+/)[0])
    .filter(Boolean);
}

function drawResultsRoot() {
  return path.join(runtimeRoot(), "diagnostics", "draw_result_pairs");
}

function drawStatsRoot() {
  return path.join(runtimeRoot(), "diagnostics", "draw_stats");
}

function historyDatabaseFile() {
  return path.join(app.getPath("userData"), "history.sqlite");
}

function readJsonLines(file: string): Array<Record<string, unknown>> {
  if (!existsSync(file)) return [];
  return readFileSync(file, "utf8").split(/\r?\n/).filter(Boolean).flatMap((line) => {
    try { return [JSON.parse(line) as Record<string, unknown>]; } catch { return []; }
  });
}

function drawDateRange(endDay: string, range: string, customStartDay?: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(endDay)) throw new Error("统计结束日期无效");
  if (range === "custom") {
    const startDay = String(customStartDay ?? "");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(startDay) || startDay > endDay) throw new Error("自定义统计日期无效");
    return { startDay, endDay };
  }
  const span = range === "month" ? 30 : range === "7d" ? 7 : 1;
  const end = new Date(`${endDay}T00:00:00Z`);
  end.setUTCDate(end.getUTCDate() - span + 1);
  return { startDay: end.toISOString().slice(0, 10), endDay };
}

function drawSessionsForUser(userId: string) {
  const root = drawStatsRoot();
  if (!existsSync(root)) return [];
  return readdirSync(root)
    .filter((name) => name !== "latest_summary.json" && name.endsWith("_summary.json"))
    .flatMap((name) => {
      try {
        const summary = JSON.parse(readFileSync(path.join(root, name), "utf8")) as Record<string, unknown>;
        return String(summary.user_id ?? "default") === userId ? [summary] : [];
      } catch { return []; }
    });
}

function correctDrawResult(rawPairPrefix: string, rawRoleName: string, rawUserId = "default") {
  const pairPrefix = String(rawPairPrefix ?? "").trim();
  const roleName = String(rawRoleName ?? "").trim();
  const userId = String(rawUserId ?? "default");
  if (!pairPrefix || !roleName) throw new Error("请选择需要校准的红卡角色");
  const indexFile = path.join(drawResultsRoot(), "index.jsonl");
  const pairs = readJsonLines(indexFile);
  const pair = pairs.find((item) => String(item.pair_prefix ?? "") === pairPrefix && String(item.user_id ?? "default") === userId);
  if (!pair) throw new Error("未找到需要校准的抽卡截图");
  const sessionId = String(pair.session_id ?? "");
  const eventsFile = path.join(drawStatsRoot(), `${path.basename(sessionId)}_events.jsonl`);
  const events = readJsonLines(eventsFile);
  const candidates = events.filter((event) => String(event.pair_prefix ?? "") === pairPrefix && String(event.user_id ?? "default") === userId);
  const target = candidates.find((event) => String(event.matched_template ?? "") === "unknown_red_role") ?? candidates[candidates.length - 1];
  if (!target) throw new Error("该截图没有可校准的抽卡结果");
  target.matched_template = roleName;
  target.matched_role_note = roleName;
  target.calibrated_at = new Date().toISOString();
  target.calibrated = true;
  writeFileSync(eventsFile, `${events.map((event) => JSON.stringify(event)).join("\n")}\n`, "utf8");
  const summaryFile = path.join(drawStatsRoot(), `${path.basename(sessionId)}_summary.json`);
  if (existsSync(summaryFile)) {
    const summary = JSON.parse(readFileSync(summaryFile, "utf8")) as Record<string, unknown>;
    const counts = summary.role_hit_counts && typeof summary.role_hit_counts === "object" ? summary.role_hit_counts as Record<string, number> : {};
    counts.unknown_red_role = Math.max(0, Number(counts.unknown_red_role ?? 0) - 1);
    counts[roleName] = Number(counts[roleName] ?? 0) + 1;
    summary.role_hit_counts = counts;
    summary.updated_at = new Date().toISOString().replace("T", " ").slice(0, 19);
    writeFileSync(summaryFile, `${JSON.stringify(summary, null, 2)}\n`, "utf8");
  }
  return { pairPrefix, roleName, sessionId };
}

function exportDrawReport(endDay: string, range: string, userId = "default", customStartDay?: string) {
  const { startDay, endDay: normalizedEndDay } = drawDateRange(endDay, range, customStartDay);
  const sessions = drawSessionsForUser(userId).filter((session) => {
    const day = String(session.updated_at ?? "").slice(0, 10);
    return day >= startDay && day <= normalizedEndDay;
  });
  const totals = sessions.reduce<{ draws: number; seen: number; hits: number }>((value, session) => ({
    draws: value.draws + Number(session.draw_started_count ?? 0),
    seen: value.seen + Number(session.target_seen_count ?? 0),
    hits: value.hits + Number(session.target_hit_count ?? 0),
  }), { draws: 0, seen: 0, hits: 0 });
  const roles = new Map<string, number>();
  for (const session of sessions) {
    const counts = session.role_hit_counts && typeof session.role_hit_counts === "object" ? session.role_hit_counts as Record<string, unknown> : {};
    for (const [role, count] of Object.entries(counts)) roles.set(role, (roles.get(role) ?? 0) + Number(count ?? 0));
  }
  const directory = path.join(drawResultsRoot(), "reports");
  mkdirSync(directory, { recursive: true });
  const file = path.join(directory, `抽卡报表_${userId}_${startDay}_${normalizedEndDay}.csv`);
  const rows = [
    ["用户", userId],
    ["统计日期", `${startDay} 至 ${normalizedEndDay}`],
    ["抽卡次数", String(totals.draws)],
    ["红卡出现次数", String(totals.seen)],
    ["红卡命中次数", String(totals.hits)],
    ["红卡出现概率", totals.draws ? `${(totals.seen / totals.draws * 100).toFixed(2)}%` : "0.00%"],
    ["红卡命中概率", totals.draws ? `${(totals.hits / totals.draws * 100).toFixed(2)}%` : "0.00%"],
    [],
    ["红卡角色", "命中次数", "在抽卡中的概率"],
    ...[...roles.entries()].sort((a, b) => b[1] - a[1]).map(([role, count]) => [role, String(count), totals.draws ? `${(count / totals.draws * 100).toFixed(2)}%` : "0.00%"]),
  ];
  writeFileSync(file, `\ufeff${rows.map((row) => row.map((value) => `"${String(value).replace(/"/g, "\"\"")}"`).join(",")).join("\n")}\n`, "utf8");
  return { file, startDay, endDay: normalizedEndDay, ...totals };
}

async function migrateHistoryToDatabase() {
  const drawPairs = readJsonLines(path.join(drawResultsRoot(), "index.jsonl"));
  const drawEvents = existsSync(drawStatsRoot())
    ? readdirSync(drawStatsRoot()).filter((name) => name.endsWith("_events.jsonl")).flatMap((name) => readJsonLines(path.join(drawStatsRoot(), name)))
    : [];
  const drawSessions = existsSync(drawStatsRoot())
    ? readdirSync(drawStatsRoot()).filter((name) => name !== "latest_summary.json" && name.endsWith("_summary.json")).flatMap((name) => {
      try { return [JSON.parse(readFileSync(path.join(drawStatsRoot(), name), "utf8")) as Record<string, unknown>]; } catch { return []; }
    }) : [];
  const chestRecords = chestItemEventsFromFile();
  const payload = JSON.stringify({ database: historyDatabaseFile(), operation: "import", data: { users: chestUsers(), chestRecords, drawSessions, drawEvents, drawPairs } });
  const script = path.join(runtimeRoot(), "data_store.py");
  const executable = resolveExecutable(getSettings().pythonPath, process.platform === "win32" ? "python.exe" : "python3");
  const result = await new Promise<string>((resolve, reject) => {
    const process = spawn(executable, [script], { windowsHide: true, env: runtimeEnvironment() });
    let output = "";
    let errors = "";
    process.stdout.on("data", (data) => (output += data.toString("utf8")));
    process.stderr.on("data", (data) => (errors += data.toString("utf8")));
    process.on("error", reject);
    process.on("close", (code) => code === 0 ? resolve(output) : reject(new Error(errors || output || `数据库迁移退出码 ${code}`)));
    process.stdin.end(payload);
  });
  return JSON.parse(result) as Record<string, unknown>;
}

app.on("window-all-closed", () => {
  stopAllTasks();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  isQuitting = true;
  stopAllTasks();
});

process.once("SIGINT", () => {
  stopAllTasks();
});

process.once("SIGTERM", () => {
  stopAllTasks();
});

app.on("activate", () => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.show();
    mainWindow.focus();
    return;
  }
  createWindow();
});
