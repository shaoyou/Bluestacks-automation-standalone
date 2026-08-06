import type { AppSettings, LicenseStatus, UpdateState } from "../types";

export type Page = "scripts" | "runner" | "draw" | "chest" | "recorder" | "calibration" | "diagnostics" | "settings";
export type PickedCoordinate = { x: number; y: number; device: string; capturedAt: string };
export type ScriptVariable = { name: string; value: string; note: string };
export type RunnerSelection = { plan: string; device: string; profitPerCycle: string; showRealtimeLogs: boolean };

export type SharedProps = {
  settings: AppSettings;
  setSettings: (settings: AppSettings) => void;
  license: LicenseStatus | null;
  activateLicense: (code: string) => Promise<boolean>;
  clearLicense: () => Promise<void>;
  openLicenseActivation: () => void;
  update: UpdateState | null;
  checkForUpdates: () => Promise<void>;
  downloadUpdate: () => Promise<void>;
  installUpdate: () => Promise<void>;
  runtime: { root: string; plansDir: string; templatesDir: string } | null;
  plans: string[];
  activePlan: string | null;
  source: string;
  setSource: (source: string) => void;
  savedSource: string;
  logs: Record<string, string>;
  running: Record<string, boolean>;
  notice: string;
  setNotice: (notice: string) => void;
  loadPlan: (name: string) => Promise<void>;
  savePlan: () => Promise<void>;
  deletePlan: () => Promise<void>;
  createPlan: () => Promise<void>;
  refreshPlans: () => Promise<string[]>;
  startTask: (id: string, args: string[]) => Promise<void>;
  pickedCoordinates: PickedCoordinate[];
  setPickedCoordinates: (coordinates: PickedCoordinate[]) => void;
  insertAction: (action: Record<string, unknown>) => void;
  clearTaskLog: (id: string) => void;
  devices: string[];
  refreshDevices: () => Promise<void>;
  chestTaskId?: string;
  chestUserId?: string;
  chestSourceId?: string;
  chestSourceName?: string;
};

export function updateVariables(source: string, variables: ScriptVariable[]) {
  const plan = JSON.parse(source) as Record<string, unknown>;
  plan.variables = variables;
  return `${JSON.stringify(plan, null, 2)}\n`;
}

export function appendPlanAction(source: string, action: Record<string, unknown>) {
  const plan = JSON.parse(source) as { actions?: unknown };
  const actions = Array.isArray(plan.actions) ? plan.actions : [];
  plan.actions = [...actions, action];
  return `${JSON.stringify(plan, null, 2)}\n`;
}
