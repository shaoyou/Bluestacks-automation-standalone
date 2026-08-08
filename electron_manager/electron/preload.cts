import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("bsManager", {
  runtimeState: () => ipcRenderer.invoke("runtime:state"),
  environmentState: () => ipcRenderer.invoke("environment:state"),
  environmentBootstrap: () => ipcRenderer.invoke("environment:bootstrap"),
  environmentCancel: () => ipcRenderer.invoke("environment:cancel"),
  settingsGet: () => ipcRenderer.invoke("settings:get"),
  settingsSave: (settings: unknown) => ipcRenderer.invoke("settings:save", settings),
  updateState: () => ipcRenderer.invoke("update:state"),
  updateCheck: () => ipcRenderer.invoke("update:check"),
  updateDownload: () => ipcRenderer.invoke("update:download"),
  updateInstall: () => ipcRenderer.invoke("update:install"),
  licenseGet: () => ipcRenderer.invoke("license:get"),
  licenseActivate: (code: string) => ipcRenderer.invoke("license:activate", code),
  licenseClear: () => ipcRenderer.invoke("license:clear"),
  plansList: () => ipcRenderer.invoke("plans:list"),
  plansRead: (name: string) => ipcRenderer.invoke("plans:read", name),
  plansSave: (name: string, text: string) => ipcRenderer.invoke("plans:save", name, text),
  plansCreate: (name: string) => ipcRenderer.invoke("plans:create", name),
  plansDelete: (name: string) => ipcRenderer.invoke("plans:delete", name),
  templatesList: () => ipcRenderer.invoke("templates:list"),
  templatesOpenFolder: () => ipcRenderer.invoke("templates:open-folder"),
  templatesImport: () => ipcRenderer.invoke("templates:import"),
  templatesSaveCapture: (name: string, dataUrl: string) => ipcRenderer.invoke("templates:save-capture", name, dataUrl),
  drawListSessions: () => ipcRenderer.invoke("draw:list-sessions"),
  drawEvents: (sessionId: string) => ipcRenderer.invoke("draw:events", sessionId),
  drawScreenshotPairs: (sessionId: string) => ipcRenderer.invoke("draw:screenshot-pairs", sessionId),
  drawImage: (filePath: string) => ipcRenderer.invoke("draw:image", filePath),
  drawOpenScreenshots: () => ipcRenderer.invoke("draw:open-screenshots"),
  chestListDays: (device?: string, userId?: string) => ipcRenderer.invoke("chest:list-days", device, userId),
  chestScreenshots: (day: string, device?: string, userId?: string) => ipcRenderer.invoke("chest:screenshots", day, device, userId),
  chestItemEvents: (day: string, device?: string, userId?: string) => ipcRenderer.invoke("chest:item-events", day, device, userId),
  chestItemSummary: (day: string, device?: string, userId?: string) => ipcRenderer.invoke("chest:item-summary", day, device, userId),
  chestSummaryRange: (endDay: string, range: string, device?: string, userId?: string, startDay?: string, sourceId?: string) => ipcRenderer.invoke("chest:summary-range", endDay, range, device, userId, startDay, sourceId),
  chestExportReport: (endDay: string, range: string, device?: string, userId?: string, startDay?: string, sourceId?: string) => ipcRenderer.invoke("chest:export-report", endDay, range, device, userId, startDay, sourceId),
  chestSyncExport: (userId?: string) => ipcRenderer.invoke("chest:sync-export", userId),
  chestSyncImport: (userId?: string) => ipcRenderer.invoke("chest:sync-import", userId),
  chestOpenReportDirectory: () => ipcRenderer.invoke("chest:open-report-directory"),
  chestSetActiveSource: (userId: string, taskId: string, sourceId: string, sourceName: string) => ipcRenderer.invoke("chest:set-active-source", userId, taskId, sourceId, sourceName),
  chestSources: (userId?: string) => ipcRenderer.invoke("chest:sources", userId),
  chestCreateSource: (userId: string, sourceName: string) => ipcRenderer.invoke("chest:source-create", userId, sourceName),
  chestDeleteSource: (userId: string, sourceId: string) => ipcRenderer.invoke("chest:source-delete", userId, sourceId),
  chestUsers: () => ipcRenderer.invoke("chest:users"),
  chestCreateUser: (name: string) => ipcRenderer.invoke("chest:user-create", name),
  chestRenameUser: (userId: string, name: string) => ipcRenderer.invoke("chest:user-rename", userId, name),
  chestReanalyze: (day?: string, userId?: string) => ipcRenderer.invoke("chest:reanalyze", day, userId),
  chestUnlabeledItems: () => ipcRenderer.invoke("chest:unlabeled-items"),
  chestLabelItem: (itemId: string, name: string) => ipcRenderer.invoke("chest:label-item", itemId, name),
  chestSetItemWeight: (itemId: string, weight: number | null) => ipcRenderer.invoke("chest:item-weight", itemId, weight),
  chestCorrectEvent: (screenshotPath: string, corrections: Array<{ slot: number; itemName?: string | null; itemId?: string | null; iconCropPath?: string | null; quantity: number | null }>, metadata?: { userId: string; sourceId: string; sourceName: string }) => ipcRenderer.invoke("chest:correct-event", screenshotPath, corrections, metadata),
  chestDeleteEvent: (screenshotPath: string) => ipcRenderer.invoke("chest:delete-event", screenshotPath),
  chestDeleteItem: (itemId: string) => ipcRenderer.invoke("chest:delete-item", itemId),
  chestImage: (filePath: string) => ipcRenderer.invoke("chest:image", filePath),
  chestOpenScreenshots: () => ipcRenderer.invoke("chest:open-screenshots"),
  devicesList: (adbPath: string) => ipcRenderer.invoke("adb:list-devices", adbPath),
  adbRun: (adbPath: string, args: string[]) => ipcRenderer.invoke("adb:run", adbPath, args),
  screenshot: (adbPath: string, device: string) => ipcRenderer.invoke("adb:screenshot", adbPath, device),
  openRunWindow: (initialPlan?: string) => ipcRenderer.invoke("run-window:open", initialPlan),
  openChestWindow: (initialPlan?: string, userId?: string, sourceId?: string, sourceName?: string) => ipcRenderer.invoke("chest:open-window", initialPlan, userId, sourceId, sourceName),
  startTask: (request: unknown) => ipcRenderer.invoke("task:start", request),
  stopTask: (id: string) => ipcRenderer.invoke("task:stop", id),
  onTaskEvent: (listener: (event: unknown) => void) => {
    const callback = (_: Electron.IpcRendererEvent, event: unknown) => listener(event);
    ipcRenderer.on("task:event", callback);
    return () => ipcRenderer.removeListener("task:event", callback);
  },
  onEnvironmentEvent: (listener: (event: unknown) => void) => {
    const callback = (_: Electron.IpcRendererEvent, event: unknown) => listener(event);
    ipcRenderer.on("environment:event", callback);
    return () => ipcRenderer.removeListener("environment:event", callback);
  },
  onUpdateEvent: (listener: (event: unknown) => void) => {
    const callback = (_: Electron.IpcRendererEvent, event: unknown) => listener(event);
    ipcRenderer.on("update:event", callback);
    return () => ipcRenderer.removeListener("update:event", callback);
  },
});
