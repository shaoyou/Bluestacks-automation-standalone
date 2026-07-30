export type AppSettings = {
  adbPath: string;
  pythonPath: string;
  language: "zh" | "en";
};

export type TaskKind = "runner" | "draw" | "recorder" | "clickPicker" | "diagnostic";

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

export type EnvironmentState = {
  required: boolean;
  ready: boolean;
  phase: "ready" | "required" | "running" | "cancelled" | "failed";
  progress: number;
  message: string;
  error?: string;
};

export type LicenseStatus = {
  installId: string;
  tier: "free" | "pro";
  valid: boolean;
  maxConcurrentRunners: number;
  expiresAt?: string;
  message: string;
};

declare global {
  interface Window {
    bsManager: {
      runtimeState(): Promise<{ root: string; plansDir: string; templatesDir: string }>;
      environmentState(): Promise<EnvironmentState>;
      environmentBootstrap(): Promise<EnvironmentState>;
      environmentCancel(): Promise<void>;
      settingsGet(): Promise<AppSettings>;
      settingsSave(settings: AppSettings): Promise<AppSettings>;
      licenseGet(): Promise<LicenseStatus>;
      licenseActivate(code: string): Promise<LicenseStatus>;
      licenseClear(): Promise<LicenseStatus>;
      plansList(): Promise<string[]>;
      plansRead(name: string): Promise<string>;
      plansSave(name: string, text: string): Promise<void>;
      plansCreate(name: string): Promise<string>;
      plansDelete(name: string): Promise<void>;
      templatesList(): Promise<string[]>;
      templatesImport(): Promise<string | null>;
      templatesSaveCapture(name: string, dataUrl: string): Promise<string>;
      drawListSessions(): Promise<Array<{ file: string; summary: Record<string, unknown> }>>;
      drawEvents(sessionId: string): Promise<Array<Record<string, unknown>>>;
      drawScreenshotPairs(sessionId: string): Promise<Array<Record<string, unknown>>>;
      drawImage(filePath: string): Promise<string | null>;
      drawOpenScreenshots(): Promise<void>;
      devicesList(adbPath: string): Promise<string[]>;
      adbRun(adbPath: string, args: string[]): Promise<{ code: number; text: string }>;
      screenshot(adbPath: string, device: string): Promise<string>;
      openRunWindow(initialPlan?: string): Promise<void>;
      startTask(request: TaskRequest): Promise<void>;
      stopTask(id: string): Promise<void>;
      onTaskEvent(listener: (event: TaskEvent) => void): () => void;
      onEnvironmentEvent(listener: (event: EnvironmentState) => void): () => void;
    };
  }
}
