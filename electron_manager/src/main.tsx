import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "./styles.css";

type RendererErrorBoundaryState = { error: Error | null };

class RendererErrorBoundary extends React.Component<React.PropsWithChildren, RendererErrorBoundaryState> {
  state: RendererErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): RendererErrorBoundaryState {
    return { error };
  }

  render() {
    if (this.state.error) {
      return <main className="renderer-error"><h1>界面加载失败</h1><p>{this.state.error.message}</p><button className="button primary" onClick={() => window.location.reload()}>重新加载界面</button></main>;
    }
    return this.props.children;
  }
}

// Browser-only preview bridge. The packaged app always receives the real API from Electron preload.
if (!window.bsManager) {
  const previewPlans = new Map<string, string>([
    ["sample_plan.json", JSON.stringify({ device: "emulator-5554", jitter_px: 2, variables: [{ name: "WAIT_SHORT", value: "0.5", note: "短等待" }], actions: [{ type: "wait", seconds: "${WAIT_SHORT}", remark: "等待" }] }, null, 2)],
    ["choukaka.json", JSON.stringify({ device: "emulator-5554", actions: [] }, null, 2)],
    ["开宝箱截图.json", JSON.stringify({ device: "emulator-5554", actions: [] }, null, 2)],
  ]);
  const listeners = new Set<(event: { id: string; type: "started" | "log" | "exit"; code?: number; text?: string }) => void>();
  const notify = (event: { id: string; type: "started" | "log" | "exit"; code?: number; text?: string }) => listeners.forEach((listener) => listener(event));
  window.bsManager = {
    runtimeState: async () => ({ root: "/preview/runtime", plansDir: "/preview/runtime/plans", templatesDir: "/preview/runtime/image_templates" }),
    settingsGet: async () => ({ adbPath: "adb", hdcPath: "hdc", pythonPath: "python3", language: "zh" }),
    settingsSave: async (settings) => settings,
    updateState: async () => ({ currentVersion: "1.2.14", supported: false, phase: "unsupported", message: "预览环境不检查更新" }),
    updatePolicyState: async () => ({ supported: false, currentVersion: "1.2.14", channel: "stable", sourceUrl: "", loaded: false, checking: false, blocked: false, message: "预览环境不检查更新" }),
    updatePolicyCheck: async () => ({ supported: false, currentVersion: "1.2.14", channel: "stable", sourceUrl: "", loaded: false, checking: false, blocked: false, message: "预览环境不检查更新" }),
    updatePromptState: async () => ({}),
    updatePromptAcknowledge: async () => ({}),
    updateCheck: async () => ({ currentVersion: "1.2.14", supported: false, phase: "unsupported", message: "预览环境不检查更新" }),
    updateDownload: async () => ({ currentVersion: "1.2.14", supported: false, phase: "unsupported", message: "预览环境不检查更新" }),
    updateInstall: async () => {},
    licenseGet: async () => ({ installId: "preview", tier: "pro", valid: true, maxConcurrentRunners: 3, message: "预览授权" }),
    licenseActivate: async () => ({ installId: "preview", tier: "pro", valid: true, maxConcurrentRunners: 3, message: "预览授权" }),
    licenseClear: async () => ({ installId: "preview", tier: "free", valid: true, maxConcurrentRunners: 1, message: "已清除预览授权" }),
    plansList: async () => [...previewPlans.keys()],
    plansRead: async (name) => previewPlans.get(name) ?? "",
    plansSave: async (name, text) => { JSON.parse(text); previewPlans.set(name, text); },
    plansCreate: async (name) => { const filename = `${name.replace(/\.json$/i, "")}.json`; previewPlans.set(filename, "{\n  \"actions\": []\n}\n"); return filename; },
    plansDelete: async (name) => { previewPlans.delete(name); },
    templatesList: async () => ["role_lujuren.png", "role_kakaxi.png", "role_done_min.png"],
    templatesOpenFolder: async () => {},
    templatesImport: async () => "../image_templates/imported_template.png",
    templatesSaveCapture: async (name) => `../image_templates/${name}.png`,
    drawListSessions: async () => [],
    drawEvents: async () => [],
    drawScreenshotPairs: async () => [],
    drawImage: async () => null,
    drawOpenScreenshots: async () => {},
    chestListDays: async () => [{ day: "2026-07-31", count: 1, latestAt: "2026-07-31 15:45:00" }],
    chestScreenshots: async () => [{ before_path: "/preview/chest-items.png", before_saved_at: "2026-07-31 15:45:00" }],
    chestItemEvents: async () => [],
    chestItemSummary: async () => [],
    chestSummaryRange: async () => ({ items: [], boxCount: 0 }),
    chestExportReport: async () => ({ file: "", boxCount: 0 }),
    chestSyncExport: async () => ({ canceled: false, file: "", events: 0, icons: 0 }),
    chestSyncImport: async () => ({ canceled: false, imported: 0, skipped: 0, icons: 0 }),
    chestOpenReportDirectory: async () => {},
    chestSetActiveSource: async (_userId: string, _taskId: string, sourceId: string, sourceName: string) => ({ sourceFile: "", sourceId, sourceName }),
    chestSources: async () => [],
    chestCreateSource: async (_userId: string, sourceName: string) => ({ sourceId: `custom_${sourceName}`, sourceName }),
    chestDeleteSource: async (_userId: string, sourceId: string) => ({ sourceId, sourceName: "" }),
    chestUsers: async () => [{ id: "default", name: "默认用户", createdAt: "" }],
    chestCreateUser: async (name) => ({ id: "preview-user", name, createdAt: "" }),
    chestRenameUser: async (id, name) => ({ id, name, createdAt: "" }),
    chestReanalyze: async (_day?: string, _userId?: string) => ({}),
    chestUnlabeledItems: async () => [],
    chestLabelItem: async () => ({}),
    chestSetItemWeight: async () => ({}),
    chestCorrectEvent: async () => ({}),
    chestDeleteEvent: async () => ({}),
    chestDeleteItem: async () => ({}),
    openChestWindow: async () => {},
    chestImage: async () => null,
    chestOpenScreenshots: async () => {},
    devicesList: async () => ["emulator-5554", "127.0.0.1:5555", "ABC123 [HarmonyOS/HDC]"],
    devicesForceRefresh: async () => ["emulator-5554", "127.0.0.1:5555", "ABC123 [HarmonyOS/HDC]"],
    adbRun: async () => ({ code: 0, text: "Physical size: 1080x1920\n" }),
    screenshot: async () => "",
    startTask: async (request) => {
      notify({ id: request.id, type: "started" });
      notify({ id: request.id, type: "log", text: "Preview task started. Electron will execute Python here.\n" });
    },
    stopTask: async (id) => notify({ id, type: "exit", code: 0 }),
    onTaskEvent: (listener) => { listeners.add(listener); return () => listeners.delete(listener); },
    onDevicesEvent: () => () => {},
    onSettingsEvent: () => () => {},
    onUpdateEvent: () => () => {},
    onUpdatePromptEvent: () => () => {},
  };
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RendererErrorBoundary><App /></RendererErrorBoundary>
  </React.StrictMode>,
);
