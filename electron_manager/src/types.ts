export type AppSettings = {
  adbPath: string;
  hdcPath: string;
  pythonPath: string;
  language: "zh" | "en";
};

export type TaskKind = "runner" | "draw" | "chest" | "recorder" | "clickPicker" | "diagnostic";

export type TaskEvent = {
  id: string;
  type: "started" | "log" | "exit";
  code?: number | null;
  text?: string;
};

export type TaskRequest = {
  id: string;
  kind: TaskKind;
  args: string[];
  cwd?: string;
};

export type LicenseStatus = {
  installId: string;
  tier: "free" | "pro";
  valid: boolean;
  maxConcurrentRunners: number;
  expiresAt?: string;
  message: string;
};

export type UpdateState = {
  currentVersion: string;
  supported: boolean;
  phase: "idle" | "checking" | "available" | "not-available" | "downloading" | "downloaded" | "error" | "unsupported";
  message: string;
  version?: string;
  progress?: number;
  releaseNotes?: string;
};

declare global {
  interface Window {
    bsManager: {
      runtimeState(): Promise<{ root: string; plansDir: string; templatesDir: string }>;
      settingsGet(): Promise<AppSettings>;
      settingsSave(settings: AppSettings): Promise<AppSettings>;
      updateState(): Promise<UpdateState>;
      updateCheck(): Promise<UpdateState>;
      updateDownload(): Promise<UpdateState>;
      updateInstall(): Promise<void>;
      licenseGet(): Promise<LicenseStatus>;
      licenseActivate(code: string): Promise<LicenseStatus>;
      licenseClear(): Promise<LicenseStatus>;
      plansList(): Promise<string[]>;
      plansRead(name: string): Promise<string>;
      plansSave(name: string, text: string): Promise<void>;
      plansCreate(name: string): Promise<string>;
      plansDelete(name: string): Promise<void>;
      templatesList(): Promise<string[]>;
      templatesOpenFolder(): Promise<void>;
      templatesImport(): Promise<string | null>;
      templatesSaveCapture(name: string, dataUrl: string): Promise<string>;
      drawUsers(): Promise<Array<{ id: string; name: string; createdAt: string }>>;
      drawCreateUser(name: string): Promise<{ id: string; name: string; createdAt: string }>;
      drawRenameUser(userId: string, name: string): Promise<{ id: string; name: string; createdAt: string }>;
      drawListSessions(userId?: string): Promise<Array<{ file: string; summary: Record<string, unknown> }>>;
      drawEvents(sessionId: string, userId?: string): Promise<Array<Record<string, unknown>>>;
      drawScreenshotPairs(sessionId: string, userId?: string): Promise<Array<Record<string, unknown>>>;
      drawCorrectResult(pairPrefix: string, roleName: string, userId?: string): Promise<Record<string, unknown>>;
      drawExportReport(endDay: string, range: string, userId?: string, startDay?: string): Promise<Record<string, unknown>>;
      drawOpenReportDirectory(): Promise<void>;
      drawImage(filePath: string): Promise<string | null>;
      drawOpenScreenshots(): Promise<void>;
      chestListDays(device?: string, userId?: string): Promise<Array<{ day: string; count: number; latestAt: string }>>;
      chestScreenshots(day: string, device?: string, userId?: string): Promise<Array<Record<string, unknown>>>;
      chestItemEvents(day: string, device?: string, userId?: string): Promise<Array<Record<string, unknown>>>;
      chestItemSummary(day: string, device?: string, userId?: string): Promise<Array<Record<string, unknown>>>;
      chestSummaryRange(endDay: string, range: string, device?: string, userId?: string, startDay?: string, sourceId?: string): Promise<{ items: Array<Record<string, unknown>>; boxCount: number; startDay?: string; endDay?: string; sourceId?: string }>;
      chestExportReport(endDay: string, range: string, device?: string, userId?: string, startDay?: string, sourceId?: string): Promise<{ file: string; boxCount: number }>;
      chestSyncExport(userId?: string): Promise<{ canceled: boolean; file?: string; events: number; icons: number }>;
      chestSyncImport(userId?: string): Promise<{ canceled: boolean; imported: number; skipped: number; icons: number }>;
      chestOpenReportDirectory(): Promise<void>;
      chestSetActiveSource(userId: string, taskId: string, sourceId: string, sourceName: string): Promise<{ sourceFile: string; sourceId: string; sourceName: string }>;
      chestSources(userId?: string): Promise<Array<{ sourceId: string; sourceName: string }>>;
      chestCreateSource(userId: string, sourceName: string): Promise<{ sourceId: string; sourceName: string }>;
      chestDeleteSource(userId: string, sourceId: string): Promise<{ sourceId: string; sourceName: string }>;
      chestUsers(): Promise<Array<{ id: string; name: string; createdAt: string }>>;
      chestCreateUser(name: string): Promise<{ id: string; name: string; createdAt: string }>;
      chestRenameUser(userId: string, name: string): Promise<{ id: string; name: string; createdAt: string }>;
      chestReanalyze(day?: string, userId?: string): Promise<Record<string, unknown>>;
      chestUnlabeledItems(): Promise<Array<{ itemId: string; name: string; labeled: boolean; weight?: number | null; cropPath: string; occurrences: number }>>;
      chestLabelItem(itemId: string, name: string): Promise<Record<string, unknown>>;
      chestSetItemWeight(itemId: string, weight: number | null): Promise<Record<string, unknown>>;
      chestCorrectEvent(screenshotPath: string, corrections: Array<{ slot: number; itemName?: string | null; itemId?: string | null; iconCropPath?: string | null; quantity: number | null }>, metadata?: { userId: string; sourceId: string; sourceName: string }): Promise<Record<string, unknown>>;
      chestDeleteEvent(screenshotPath: string): Promise<Record<string, unknown>>;
      chestDeleteItem(itemId: string): Promise<Record<string, unknown>>;
      chestImage(filePath: string): Promise<string | null>;
      chestOpenScreenshots(): Promise<void>;
      historyMigrate(): Promise<Record<string, unknown>>;
      devicesList(paths: { adbPath: string; hdcPath: string }): Promise<string[]>;
      devicesForceRefresh(paths: { adbPath: string; hdcPath: string }): Promise<string[]>;
      adbRun(paths: { adbPath: string; hdcPath: string }, args: string[], backend?: "adb" | "hdc"): Promise<{ code: number; text: string }>;
      screenshot(paths: { adbPath: string; hdcPath: string }, device: string): Promise<string>;
      openRunWindow(initialPlan?: string): Promise<void>;
      openChestWindow(initialPlan?: string, userId?: string, sourceId?: string, sourceName?: string): Promise<void>;
      startTask(request: TaskRequest): Promise<void>;
      stopTask(id: string): Promise<void>;
      onTaskEvent(listener: (event: TaskEvent) => void): () => void;
      onDevicesEvent(listener: (devices: string[]) => void): () => void;
      onSettingsEvent(listener: (settings: AppSettings) => void): () => void;
      onUpdateEvent(listener: (event: UpdateState) => void): () => void;
    };
  }
}
