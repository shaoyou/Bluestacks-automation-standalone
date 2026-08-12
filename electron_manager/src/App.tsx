import { useEffect, useRef, useState } from "react";
import {
  Activity,
  Archive,
  Bot,
  Code2,
  Crosshair,
  Play,
  Radio,
  Settings2,
} from "lucide-react";
import type { AppSettings, EnvironmentState, LicenseStatus, TaskEvent, UpdateState } from "./types";
import { appendPlanAction, type Page, type PickedCoordinate, type SharedProps } from "./app/shared";
import { EnvironmentSetup, LicenseActivationDialog } from "./components/layout";
import { ScriptsPage, RunnerPage } from "./pages/ScriptsRunnerPage";
import { CalibrationPage, DiagnosticsPage, RecorderPage, SettingsPage } from "./pages/DevicePages";
import { DrawPage } from "./pages/DrawPage";
import { ChestPage } from "./pages/ChestPage";

const navItems: { id: Page; label: string; icon: typeof Code2 }[] = [
  { id: "runner", label: "运行", icon: Play },
  { id: "draw", label: "抽卡", icon: Activity },
  { id: "chest", label: "开宝箱", icon: Archive },
  { id: "scripts", label: "脚本", icon: Code2 },
  { id: "recorder", label: "录制", icon: Radio },
  { id: "calibration", label: "标定", icon: Crosshair },
  { id: "diagnostics", label: "诊断", icon: Activity },
  { id: "settings", label: "设置", icon: Settings2 },
];

const defaultSettings: AppSettings = { adbPath: "adb", pythonPath: "python3", language: "zh" };

function logTime() {
  return new Date().toLocaleTimeString("zh-CN", { hour12: false });
}
export function App() {
  const query = new URLSearchParams(window.location.search);
  const windowMode = query.get("mode") === "runner" ? "runner" : query.get("mode") === "chest" ? "chest" : "main";
  const runnerId = query.get("runnerId") || "main";
  const chestUserId = query.get("userId") || "default";
  const chestSourceId = query.get("sourceId") || "";
  const chestSourceName = query.get("sourceName") || "";
  const initialRunnerPlan = query.get("plan") || "";
  const [page, setPage] = useState<Page>("runner");
  const [settings, setSettings] = useState<AppSettings>(defaultSettings);
  const [runtime, setRuntime] = useState<{ root: string; plansDir: string; templatesDir: string } | null>(null);
  const [plans, setPlans] = useState<string[]>([]);
  const [activePlan, setActivePlan] = useState<string | null>(null);
  const [source, setSource] = useState("");
  const [savedSource, setSavedSource] = useState("");
  const [notice, setNotice] = useState("正在加载运行环境...");
  const [logs, setLogs] = useState<Record<string, string>>({});
  const [running, setRunning] = useState<Record<string, boolean>>({});
  const [pickedCoordinates, setPickedCoordinates] = useState<PickedCoordinate[]>([]);
  const [devices, setDevices] = useState<string[]>([]);
  const [environment, setEnvironment] = useState<EnvironmentState | null>(null);
  const [license, setLicense] = useState<LicenseStatus | null>(null);
  const [activationOpen, setActivationOpen] = useState(false);
  const [activationError, setActivationError] = useState("");
  const [update, setUpdate] = useState<UpdateState | null>(null);

  const bootstrapEnvironment = async () => {
    setEnvironment((current) => current ? { ...current, phase: "running", progress: 0, message: "正在准备运行环境" } : current);
    const result = await window.bsManager.environmentBootstrap();
    setEnvironment(result);
  };

  const refreshDevices = async () => {
    try {
      const found = await window.bsManager.devicesList(settings.adbPath);
      setDevices(found);
      setNotice(found.length ? `已发现 ${found.length} 个 ADB 设备` : "未发现可用 ADB 设备");
    } catch (error) {
      setDevices([]);
      setNotice(`ADB 连接失败: ${String(error)}`);
    }
  };
  const forceRefreshDevices = async () => {
    try {
      const found = await window.bsManager.devicesForceRefresh(settings.adbPath);
      setDevices(found);
      setNotice(found.length ? `ADB 已强制重启，发现 ${found.length} 个设备` : "ADB 已强制重启，未发现可用设备");
    } catch (error) {
      setDevices([]);
      setNotice(`ADB 强制刷新失败: ${String(error)}`);
    }
  };

  const refreshPlans = async () => {
    const names = await window.bsManager.plansList();
    setPlans(names);
    return names;
  };

  const loadPlan = async (name: string) => {
    const text = await window.bsManager.plansRead(name);
    setActivePlan(name);
    setSource(text);
    setSavedSource(text);
  };

  useEffect(() => {
    void (async () => {
      try {
        const [state, savedSettings, names, environmentState, licenseState, updateState] = await Promise.all([
          window.bsManager.runtimeState(),
          window.bsManager.settingsGet(),
          window.bsManager.plansList(),
          window.bsManager.environmentState(),
          window.bsManager.licenseGet(),
          window.bsManager.updateState(),
        ]);
        setRuntime(state);
        setSettings(savedSettings);
        setPlans(names);
        setEnvironment(environmentState);
        setLicense(licenseState);
        setUpdate(updateState);
        if (names[0]) await loadPlan(names[0]);
        setNotice("运行环境已就绪");
        if (environmentState.required && !environmentState.ready) void bootstrapEnvironment();
      } catch (error) {
        setNotice(`启动失败: ${String(error)}`);
      }
    })();
    const removeTaskListener = window.bsManager.onTaskEvent((event: TaskEvent) => {
      if (event.type === "started") setRunning((current) => ({ ...current, [event.id]: true }));
      if (event.type === "log" && event.text) {
        setLogs((current) => ({
          ...current,
          [event.id]: `${current[event.id] ?? ""}[${logTime()}] ${event.text}`.slice(-40000),
        }));
        if (event.id === "click-picker") {
          const matches = [...event.text.matchAll(/\[Click\]\s+x=(\d+)\s+y=(\d+)/g)];
          if (matches.length > 0) {
            setPickedCoordinates((current) => [
              ...matches.map((match) => ({ x: Number(match[1]), y: Number(match[2]), device: "", capturedAt: new Date().toISOString() })),
              ...current,
            ].slice(0, 10));
          }
        }
      }
      if (event.type === "exit") {
        setRunning((current) => ({ ...current, [event.id]: false }));
        setLogs((current) => ({
          ...current,
          [event.id]: `${current[event.id] ?? ""}[${logTime()}] 进程结束，退出码 ${event.code ?? "未知"}\n`,
        }));
        if (event.id === "draw") {
          void window.bsManager.historyMigrate().catch(() => undefined);
          window.dispatchEvent(new Event("draw-task-finished"));
        }
        if (event.id === "chest" || event.id.startsWith("chest-")) {
          void window.bsManager.historyMigrate().catch(() => undefined);
          window.dispatchEvent(new Event("chest-task-finished"));
        }
      }
    });
    const removeEnvironmentListener = window.bsManager.onEnvironmentEvent((event: EnvironmentState) => setEnvironment(event));
    const removeUpdateListener = window.bsManager.onUpdateEvent((event: UpdateState) => setUpdate(event));
    return () => { removeTaskListener(); removeEnvironmentListener(); removeUpdateListener(); };
  }, []);

  useEffect(() => {
    if (runtime) void refreshDevices();
  }, [runtime, settings.adbPath]);

  const selectPage = (nextPage: Page) => {
    setPage(nextPage);
  };

  const createPlan = async () => {
    const name = window.prompt("脚本名称", "new_plan");
    if (!name) return;
    try {
      const created = await window.bsManager.plansCreate(name);
      await refreshPlans();
      await loadPlan(created);
      setNotice(`已创建 ${created}`);
    } catch (error) {
      setNotice(`创建失败: ${String(error)}`);
    }
  };

  const savePlan = async () => {
    if (!activePlan) return;
    try {
      await window.bsManager.plansSave(activePlan, source);
      setSavedSource(source);
      setNotice(`已保存 ${activePlan}`);
    } catch (error) {
      setNotice(`保存失败: ${String(error)}`);
    }
  };

  const deletePlan = async () => {
    if (!activePlan || !window.confirm(`删除 ${activePlan}？`)) return;
    await window.bsManager.plansDelete(activePlan);
    const names = await refreshPlans();
    if (names[0]) await loadPlan(names[0]);
    else {
      setActivePlan(null);
      setSource("");
    }
  };

  const startTask = async (id: string, args: string[]) => {
    try {
      setLogs((current) => ({ ...current, [id]: "" }));
      const kind = id === "draw" ? "draw" : (id === "chest" || id.startsWith("chest-")) ? "chest" : id.includes("recorder") ? "recorder" : id.includes("diagnostic") ? "diagnostic" : "runner";
      await window.bsManager.startTask({ id, kind, args });
    } catch (error) {
      setNotice(`无法启动: ${String(error)}`);
    }
  };

  const insertAction = (action: Record<string, unknown>) => {
    if (!activePlan) {
      setNotice("请先选择计划文件");
      return;
    }
    try {
      setSource(appendPlanAction(source, action));
      setNotice("动作已插入当前计划，请保存");
    } catch {
      setNotice("当前 JSON 无法解析，不能插入动作");
    }
  };

  const clearTaskLog = (id: string) => setLogs((current) => ({ ...current, [id]: "" }));
  const activateLicense = async (code: string) => {
    setActivationError("");
    try {
      const status = await window.bsManager.licenseActivate(code);
      setLicense(status);
      setNotice(status.message);
      return true;
    } catch (error) {
      const message = `激活失败: ${String(error)}`;
      setNotice(message);
      setActivationError(message);
      return false;
    }
  };
  const clearLicense = async () => {
    const status = await window.bsManager.licenseClear();
    setLicense(status);
    setNotice("已移除本机专业版授权");
  };
  const openLicenseActivation = () => {
    setActivationError("");
    setActivationOpen(true);
  };
  const checkForUpdates = async () => {
    try {
      setUpdate(await window.bsManager.updateCheck());
    } catch (error) {
      setNotice(`检查更新失败: ${String(error)}`);
    }
  };
  const downloadUpdate = async () => {
    try {
      setUpdate(await window.bsManager.updateDownload());
    } catch (error) {
      setNotice(`下载更新失败: ${String(error)}`);
    }
  };
  const installUpdate = async () => {
    try {
      await window.bsManager.updateInstall();
    } catch (error) {
      setNotice(`安装更新失败: ${String(error)}`);
    }
  };

  const context = {
    settings,
    setSettings,
    license,
    activateLicense,
    clearLicense,
    openLicenseActivation,
    update,
    checkForUpdates,
    downloadUpdate,
    installUpdate,
    runtime,
    plans,
    activePlan,
    source,
    setSource,
    savedSource,
    logs,
    running,
    notice,
    setNotice,
    loadPlan,
    savePlan,
    deletePlan,
    createPlan,
    refreshPlans,
    startTask,
    pickedCoordinates,
    setPickedCoordinates,
    insertAction,
    clearTaskLog,
    devices,
    refreshDevices,
    forceRefreshDevices,
  };

  if (!environment || (environment.required && !environment.ready)) {
    return <EnvironmentSetup state={environment} onRetry={() => void bootstrapEnvironment()} onCancel={() => void window.bsManager.environmentCancel()} />;
  }

  if (windowMode === "runner") {
    return <main className="runner-window-shell"><RunnerPage {...context} runnerId={runnerId} initialPlan={initialRunnerPlan} standalone /></main>;
  }
  if (windowMode === "chest") {
    return <main className="runner-window-shell"><ChestPage {...context} chestTaskId={`chest-${runnerId}`} chestUserId={chestUserId} chestSourceId={chestSourceId} chestSourceName={chestSourceName} /></main>;
  }

  return (
    <>
      <main className="app-shell">
      <aside className="sidebar">
        <div className="brand"><Bot size={23} /> <span>熊熊乐园小助手</span></div>
        <nav>
          {navItems.map(({ id, label, icon: Icon }) => (
            <button key={id} className={`nav-item ${page === id ? "active" : ""}`} onClick={() => selectPage(id)}>
              <Icon size={18} /><span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-status"><span className="status-dot" />{notice}</div>
      </aside>
      <section className="workbench">
        {page === "scripts" && <ScriptsPage {...context} />}
        {page === "runner" && <RunnerPage {...context} />}
        {page === "draw" && <DrawPage {...context} />}
        {page === "chest" && <ChestPage {...context} />}
        {page === "recorder" && <RecorderPage {...context} />}
        {page === "calibration" && <CalibrationPage {...context} />}
        {page === "diagnostics" && <DiagnosticsPage {...context} />}
        {page === "settings" && <SettingsPage {...context} />}
      </section>
      </main>
      {activationOpen && <LicenseActivationDialog onActivate={activateLicense} error={activationError} onClose={() => setActivationOpen(false)} />}
    </>
  );
}
