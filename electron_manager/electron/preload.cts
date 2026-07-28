import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("bsManager", {
  runtimeState: () => ipcRenderer.invoke("runtime:state"),
  environmentState: () => ipcRenderer.invoke("environment:state"),
  environmentBootstrap: () => ipcRenderer.invoke("environment:bootstrap"),
  environmentCancel: () => ipcRenderer.invoke("environment:cancel"),
  settingsGet: () => ipcRenderer.invoke("settings:get"),
  settingsSave: (settings: unknown) => ipcRenderer.invoke("settings:save", settings),
  plansList: () => ipcRenderer.invoke("plans:list"),
  plansRead: (name: string) => ipcRenderer.invoke("plans:read", name),
  plansSave: (name: string, text: string) => ipcRenderer.invoke("plans:save", name, text),
  plansCreate: (name: string) => ipcRenderer.invoke("plans:create", name),
  plansDelete: (name: string) => ipcRenderer.invoke("plans:delete", name),
  templatesList: () => ipcRenderer.invoke("templates:list"),
  templatesImport: () => ipcRenderer.invoke("templates:import"),
  templatesSaveCapture: (name: string, dataUrl: string) => ipcRenderer.invoke("templates:save-capture", name, dataUrl),
  drawListSessions: () => ipcRenderer.invoke("draw:list-sessions"),
  drawEvents: (sessionId: string) => ipcRenderer.invoke("draw:events", sessionId),
  drawScreenshotPairs: (sessionId: string) => ipcRenderer.invoke("draw:screenshot-pairs", sessionId),
  drawImage: (filePath: string) => ipcRenderer.invoke("draw:image", filePath),
  drawOpenScreenshots: () => ipcRenderer.invoke("draw:open-screenshots"),
  devicesList: (adbPath: string) => ipcRenderer.invoke("adb:list-devices", adbPath),
  adbRun: (adbPath: string, args: string[]) => ipcRenderer.invoke("adb:run", adbPath, args),
  screenshot: (adbPath: string, device: string) => ipcRenderer.invoke("adb:screenshot", adbPath, device),
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
});
