import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "./styles.css";

// Browser-only preview bridge. The packaged app always receives the real API from Electron preload.
if (!window.bsManager) {
  const previewPlans = new Map<string, string>([
    ["sample_plan.json", JSON.stringify({ device: "emulator-5554", jitter_px: 2, variables: [{ name: "WAIT_SHORT", value: "0.5", note: "短等待" }], actions: [{ type: "wait", seconds: "${WAIT_SHORT}", remark: "等待" }] }, null, 2)],
  ]);
  const listeners = new Set<(event: { id: string; type: "started" | "log" | "exit"; code?: number; text?: string }) => void>();
  const notify = (event: { id: string; type: "started" | "log" | "exit"; code?: number; text?: string }) => listeners.forEach((listener) => listener(event));
  window.bsManager = {
    runtimeState: async () => ({ root: "/preview/runtime", plansDir: "/preview/runtime/plans", templatesDir: "/preview/runtime/image_templates" }),
    settingsGet: async () => ({ adbPath: "adb", pythonPath: "python3", language: "zh" }),
    settingsSave: async (settings) => settings,
    plansList: async () => [...previewPlans.keys()],
    plansRead: async (name) => previewPlans.get(name) ?? "",
    plansSave: async (name, text) => { JSON.parse(text); previewPlans.set(name, text); },
    plansCreate: async (name) => { const filename = `${name.replace(/\.json$/i, "")}.json`; previewPlans.set(filename, "{\n  \"actions\": []\n}\n"); return filename; },
    plansDelete: async (name) => { previewPlans.delete(name); },
    templatesList: async () => ["role_lujuren.png", "role_kakaxi.png", "role_done_min.png"],
    templatesImport: async () => "../image_templates/imported_template.png",
    templatesSaveCapture: async (name) => `../image_templates/${name}.png`,
    drawListSessions: async () => [],
    drawEvents: async () => [],
    devicesList: async () => ["emulator-5554", "127.0.0.1:5555"],
    adbRun: async () => ({ code: 0, text: "Physical size: 1080x1920\n" }),
    screenshot: async () => "",
    startTask: async (request) => {
      notify({ id: request.id, type: "started" });
      notify({ id: request.id, type: "log", text: "Preview task started. Electron will execute Python here.\n" });
    },
    stopTask: async (id) => notify({ id, type: "exit", code: 0 }),
    onTaskEvent: (listener) => { listeners.add(listener); return () => listeners.delete(listener); },
  };
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
