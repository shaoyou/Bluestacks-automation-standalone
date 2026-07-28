import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  Bot,
  ChevronRight,
  CircleStop,
  Code2,
  CopyPlus,
  Crosshair,
  FileCode2,
  FolderOpen,
  MonitorSmartphone,
  Play,
  Plus,
  Radio,
  RefreshCw,
  Save,
  Settings2,
  SlidersHorizontal,
  SquareDashedMousePointer,
  TerminalSquare,
  Trash2,
  Upload,
} from "lucide-react";
import type { AppSettings, EnvironmentState, TaskEvent } from "./types";

type Page = "scripts" | "runner" | "draw" | "recorder" | "calibration" | "diagnostics" | "settings";
type RunnerSlot = "runner-a" | "runner-b";
type PickedCoordinate = { x: number; y: number; device: string; capturedAt: string };

const navItems: { id: Page; label: string; icon: typeof Code2 }[] = [
  { id: "scripts", label: "脚本", icon: Code2 },
  { id: "runner", label: "运行", icon: Play },
  { id: "draw", label: "抽卡", icon: Activity },
  { id: "recorder", label: "录制", icon: Radio },
  { id: "calibration", label: "标定", icon: Crosshair },
  { id: "diagnostics", label: "诊断", icon: Activity },
  { id: "settings", label: "设置", icon: Settings2 },
];

const defaultSettings: AppSettings = { adbPath: "adb", pythonPath: "python3", language: "zh" };
const drawDeviceStorageKey = "draw-selected-device";
const defaultRedRoles = [
  ["role_bosiwangzi.png", "波斯王子"],
  ["role_kakaxi.png", "卡卡西"],
  ["role_libai.png", "李白"],
  ["role_longsan.png", "龙三"],
  ["role_lujuren.png", "绿巨人"],
  ["role_shengqishi.png", "圣骑士"],
  ["role_woailuo.png", "我爱罗"],
  ["role_zhizhu.png", "蜘蛛"],
] as const;

function logTime() {
  return new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

function updateVariables(source: string, variables: { name: string; value: string; note: string }[]) {
  const plan = JSON.parse(source) as Record<string, unknown>;
  plan.variables = variables;
  return `${JSON.stringify(plan, null, 2)}\n`;
}

function appendPlanAction(source: string, action: Record<string, unknown>) {
  const plan = JSON.parse(source) as { actions?: unknown };
  const actions = Array.isArray(plan.actions) ? plan.actions : [];
  plan.actions = [...actions, action];
  return `${JSON.stringify(plan, null, 2)}\n`;
}

export function App() {
  const [page, setPage] = useState<Page>("draw");
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
        const [state, savedSettings, names, environmentState] = await Promise.all([
          window.bsManager.runtimeState(),
          window.bsManager.settingsGet(),
          window.bsManager.plansList(),
          window.bsManager.environmentState(),
        ]);
        setRuntime(state);
        setSettings(savedSettings);
        setPlans(names);
        setEnvironment(environmentState);
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
        if (event.id === "draw") window.dispatchEvent(new Event("draw-task-finished"));
      }
    });
    const removeEnvironmentListener = window.bsManager.onEnvironmentEvent((event: EnvironmentState) => setEnvironment(event));
    return () => { removeTaskListener(); removeEnvironmentListener(); };
  }, []);

  useEffect(() => {
    if (runtime) void refreshDevices();
  }, [runtime, settings.adbPath]);

  const selectPage = (nextPage: Page) => {
    if (nextPage !== "draw") {
      window.alert("嘿嘿，你暂时没有权限");
      return;
    }
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
      await window.bsManager.startTask({ id, kind: id.includes("recorder") ? "recorder" : id.includes("diagnostic") ? "diagnostic" : "runner", args });
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

  const context = {
    settings,
    setSettings,
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
  };

  if (!environment || (environment.required && !environment.ready)) {
    return <EnvironmentSetup state={environment} onRetry={() => void bootstrapEnvironment()} onCancel={() => void window.bsManager.environmentCancel()} />;
  }

  return (
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
        {page === "recorder" && <RecorderPage {...context} />}
        {page === "calibration" && <CalibrationPage {...context} />}
        {page === "diagnostics" && <DiagnosticsPage {...context} />}
        {page === "settings" && <SettingsPage {...context} />}
      </section>
    </main>
  );
}

function EnvironmentSetup({ state, onRetry, onCancel }: { state: EnvironmentState | null; onRetry: () => void; onCancel: () => void }) {
  const running = state?.phase === "running" || !state;
  const canRetry = state?.phase === "cancelled" || state?.phase === "failed";
  return <main className="environment-shell"><section className="environment-panel"><div className="brand"><Bot size={24} /><span>熊熊乐园小助手</span></div><h1>正在准备自动化环境</h1><p>{state?.message ?? "正在读取环境状态..."}</p><div className="progress-track"><i style={{ width: `${state?.progress ?? 0}%` }} /></div><div className="environment-steps"><span>内置自动化后端</span><span>Android Platform Tools</span><span>ADB 通信验证</span></div>{state?.error ? <pre className="environment-error">{state.error}</pre> : null}<div className="environment-actions">{running ? <button className="button secondary" onClick={onCancel}>取消</button> : null}{canRetry ? <button className="button primary" onClick={onRetry}><RefreshCw size={16} />重新准备</button> : null}</div><small>完成环境检测后，自动化、录制和设备标定功能才会启用。</small></section></main>;
}

type SharedProps = {
  settings: AppSettings;
  setSettings: (settings: AppSettings) => void;
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
};

function PageHeading({ title, detail, children }: { title: string; detail: string; children?: React.ReactNode }) {
  return <header className="page-heading"><div><h1>{title}</h1><p>{detail}</p></div><div className="heading-actions">{children}</div></header>;
}

function ScriptsPage(props: SharedProps) {
  const dirty = props.source !== props.savedSource;
  const [templates, setTemplates] = useState<string[]>([]);
  const [variables, setVariables] = useState<{ name: string; value: string; note: string }[]>([]);
  const [quickX, setQuickX] = useState("540");
  const [quickY, setQuickY] = useState("960");
  const [quickSeconds, setQuickSeconds] = useState("1");
  const [quickTemplate, setQuickTemplate] = useState("");
  const [quickThreshold, setQuickThreshold] = useState("0.9");

  useEffect(() => {
    void window.bsManager.templatesList().then(setTemplates);
  }, []);
  useEffect(() => {
    try {
      const plan = JSON.parse(props.source) as { variables?: unknown };
      const raw = plan.variables;
      if (Array.isArray(raw)) setVariables(raw.map((item) => ({
        name: String((item as Record<string, unknown>).name ?? ""),
        value: String((item as Record<string, unknown>).value ?? ""),
        note: String((item as Record<string, unknown>).note ?? ""),
      })));
      else if (raw && typeof raw === "object") setVariables(Object.entries(raw as Record<string, unknown>).map(([name, value]) => ({ name, value: String(value), note: "" })));
      else setVariables([]);
    } catch { setVariables([]); }
  }, [props.activePlan, props.source]);

  const applyVariables = () => {
    try {
      const next = updateVariables(props.source, variables.filter((item) => item.name.trim()));
      props.setSource(next);
      props.setNotice("变量已写入编辑器，请保存计划");
    } catch {
      props.setNotice("变量未写入：当前 JSON 无法解析");
    }
  };

  const refresh = async () => {
    try {
      const refreshed = await props.refreshPlans();
      if (props.activePlan && refreshed.includes(props.activePlan)) {
        await props.loadPlan(props.activePlan);
      }
      props.setNotice("计划列表已刷新");
    } catch (error) {
      props.setNotice(`刷新失败: ${String(error)}`);
    }
  };

  const numeric = (value: string, fallback: number) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const insertClick = () => props.insertAction({ type: "click", x: Math.round(numeric(quickX, 540)), y: Math.round(numeric(quickY, 960)), remark: "快捷点击" });
  const insertWait = () => props.insertAction({ type: "wait", seconds: Math.max(0, numeric(quickSeconds, 1)), remark: "快捷等待" });
  const insertImage = (click: boolean) => {
    if (!quickTemplate) {
      props.setNotice("请先选择图像模板");
      return;
    }
    props.insertAction({ type: click ? "find_image_click" : "find_image", template: `../image_templates/${quickTemplate}`, threshold: Math.min(1, Math.max(0, numeric(quickThreshold, 0.9))), timeout_sec: 8, remark: click ? "查找图像后点击" : "查找图像" });
  };

  return <div className="page scripts-page">
    <PageHeading title="脚本工作区" detail="编辑 JSON 计划、管理运行变量与图像模板。">
      <button className="button secondary" onClick={props.createPlan}><Plus size={16} />新建</button>
      <button className="button primary" disabled={!props.activePlan || !dirty} onClick={() => void props.savePlan()}><Save size={16} />保存</button>
    </PageHeading>
    <div className="script-layout">
      <section className="plan-list panel">
        <div className="panel-title"><span>计划文件</span><button className="icon-button" title="刷新" onClick={() => void refresh()}><RefreshCw size={15} /></button></div>
        <div className="plan-scroll">
          {props.plans.map((name) => <button key={name} className={`plan-item ${name === props.activePlan ? "selected" : ""}`} onClick={() => void props.loadPlan(name)}><FileCode2 size={16} /><span>{name}</span></button>)}
        </div>
        <div className="list-footer"><button className="button quiet danger" disabled={!props.activePlan} onClick={() => void props.deletePlan()}><Trash2 size={15} />删除</button></div>
      </section>
      <section className="editor-column">
        <div className="editor-toolbar">
          <div><span className="eyebrow">JSON 计划</span><strong>{props.activePlan ?? "未选择计划"}</strong>{dirty && <span className="dirty-mark">未保存</span>}</div>
          <button className="button quiet" onClick={() => void window.bsManager.templatesImport().then(async (value) => { if (value) { setTemplates(await window.bsManager.templatesList()); props.setNotice(`模板已导入: ${value}`); } })}><Upload size={15} />导入模板</button>
        </div>
        <textarea className="code-editor" aria-label="脚本 JSON 编辑器" spellCheck={false} value={props.source} onChange={(event) => {
          props.setSource(event.target.value);
          props.setNotice("正在编辑");
        }} />
      </section>
      <aside className="inspector">
        <section className="panel">
          <div className="panel-title"><span>运行变量</span><button className="icon-button" title="添加变量" onClick={() => setVariables([...variables, { name: "", value: "", note: "" }])}><Plus size={15} /></button></div>
          <div className="variables">
            {variables.length === 0 && <p className="empty-note">此计划暂无变量。</p>}
            {variables.map((item, index) => <div className="variable-row" key={`${item.name}-${index}`}>
              <input aria-label="变量名" value={item.name} placeholder="NAME" onChange={(event) => setVariables(variables.map((v, i) => i === index ? { ...v, name: event.target.value } : v))} />
              <input aria-label="变量值" value={item.value} placeholder="value" onChange={(event) => setVariables(variables.map((v, i) => i === index ? { ...v, value: event.target.value } : v))} />
              <input aria-label="变量备注" value={item.note} placeholder="备注" onChange={(event) => setVariables(variables.map((v, i) => i === index ? { ...v, note: event.target.value } : v))} />
              <button className="icon-button" title="删除变量" onClick={() => setVariables(variables.filter((_, i) => i !== index))}><Trash2 size={14} /></button>
            </div>)}
          </div>
          <button className="button secondary full" onClick={applyVariables}><Save size={15} />应用变量</button>
        </section>
        <section className="panel template-panel">
          <div className="panel-title"><span>图像模板</span><span className="counter">{templates.length}</span></div>
          <div className="template-list">{templates.slice(0, 12).map((name) => <div key={name}><SquareDashedMousePointer size={14} />{name}</div>)}</div>
        </section>
        <section className="panel quick-actions-panel">
          <div className="panel-title"><span>快捷插入</span></div>
          <div className="quick-grid"><input aria-label="快捷 X" value={quickX} onChange={(event) => setQuickX(event.target.value)} /><input aria-label="快捷 Y" value={quickY} onChange={(event) => setQuickY(event.target.value)} /></div>
          <button className="button secondary full" onClick={insertClick}>插入 click</button>
          <div className="quick-grid"><input aria-label="等待秒数" value={quickSeconds} onChange={(event) => setQuickSeconds(event.target.value)} /><button className="button secondary" onClick={insertWait}>插入 wait</button></div>
          <select aria-label="快捷模板" value={quickTemplate} onChange={(event) => setQuickTemplate(event.target.value)}><option value="">选择图像模板</option>{templates.map((name) => <option key={name}>{name}</option>)}</select>
          <div className="quick-grid"><input aria-label="图像阈值" value={quickThreshold} onChange={(event) => setQuickThreshold(event.target.value)} /><button className="button secondary" onClick={() => insertImage(false)}>查找</button></div>
          <button className="button secondary full" onClick={() => insertImage(true)}>查找后点击</button>
        </section>
      </aside>
    </div>
  </div>;
}

function RunnerPage(props: SharedProps) {
  const [selection, setSelection] = useState<Record<RunnerSlot, { plan: string; device: string }>>({
    "runner-a": { plan: props.activePlan ?? "", device: "" },
    "runner-b": { plan: props.plans[1] ?? props.activePlan ?? "", device: "" },
  });

  useEffect(() => {
    if (props.plans.length === 0) return;
    setSelection((current) => {
      const defaultPlan = props.activePlan || props.plans[0];
      const defaultSecondPlan = props.plans[1] || defaultPlan;
      const next = {
        "runner-a": { ...current["runner-a"], plan: props.plans.includes(current["runner-a"].plan) ? current["runner-a"].plan : defaultPlan },
        "runner-b": { ...current["runner-b"], plan: props.plans.includes(current["runner-b"].plan) ? current["runner-b"].plan : defaultSecondPlan },
      };
      return next["runner-a"].plan === current["runner-a"].plan && next["runner-b"].plan === current["runner-b"].plan ? current : next;
    });
  }, [props.activePlan, props.plans]);

  const start = (slot: RunnerSlot) => {
    const selected = selection[slot];
    if (!props.runtime) {
      props.setNotice("运行环境尚未加载完成");
      return;
    }
    if (!selected.plan) {
      props.setNotice("请先选择一个计划文件");
      return;
    }
    const args = [ `${props.runtime.root}/adb_bot.py`, "--plan", `${props.runtime.plansDir}/${selected.plan}`, "--adb", props.settings.adbPath ];
    if (selected.device) args.push("--device", selected.device);
    props.setNotice(`${slot === "runner-a" ? "Runner A" : "Runner B"} 正在启动`);
    void props.startTask(slot, args);
  };

  return <div className="page">
    <PageHeading title="运行中心" detail="两个独立 Runner 可同时运行不同计划和设备。">
      <button className="button secondary" onClick={() => void props.refreshDevices()}><RefreshCw size={16} />刷新设备</button>
    </PageHeading>
    <div className="device-strip panel"><MonitorSmartphone size={18} /><strong>已发现 {props.devices.length} 个设备</strong><span>{props.devices.join("  ·  ") || "请检查 ADB 路径、模拟器或 USB 调试连接"}</span></div>
    <div className="runner-grid">
      {(["runner-a", "runner-b"] as RunnerSlot[]).map((slot, index) => <RunnerCard key={slot} label={index === 0 ? "Runner A" : "Runner B"} slot={slot} plans={props.plans} devices={props.devices} selected={selection[slot]} running={!!props.running[slot]} log={props.logs[slot] ?? ""} onChange={(next) => setSelection({ ...selection, [slot]: next })} onStart={() => start(slot)} onStop={() => void window.bsManager.stopTask(slot)} onClear={() => props.clearTaskLog(slot)} />)}
    </div>
  </div>;
}

function RunnerCard({ label, plans, devices, selected, running, log, onChange, onStart, onStop, onClear }: { label: string; slot: RunnerSlot; plans: string[]; devices: string[]; selected: { plan: string; device: string }; running: boolean; log: string; onChange: (next: { plan: string; device: string }) => void; onStart: () => void; onStop: () => void; onClear: () => void; }) {
  const cycles = (log.match(/Loop start|循环开始/g) ?? []).length;
  const clicks = (log.match(/\bClick \(|点击\s*\(/g) ?? []).length;
  const errors = (log.match(/\bERROR\b|Traceback|错误/g) ?? []).length;
  return <section className="runner-card panel">
    <div className="runner-header"><div><span className="eyebrow">自动化进程</span><h2>{label}</h2></div><span className={`run-state ${running ? "live" : ""}`}>{running ? "运行中" : "待命"}</span></div>
    <label>计划<select value={selected.plan} onChange={(event) => onChange({ ...selected, plan: event.target.value })}>{plans.map((name) => <option key={name}>{name}</option>)}</select></label>
    <label>设备<select value={selected.device} onChange={(event) => onChange({ ...selected, device: event.target.value })}><option value="">使用计划默认设备</option>{devices.map((name) => <option key={name}>{name}</option>)}</select></label>
    <div className="runner-actions">{!running ? <button className="button primary" onClick={onStart}><Play size={16} />启动</button> : <button className="button danger" onClick={onStop}><CircleStop size={16} />停止</button>}<button className="button quiet" onClick={onClear}><Trash2 size={15} />清日志</button></div>
    <div className="runner-metrics"><span>循环 {cycles}</span><span>点击 {clicks}</span><span className={errors ? "metric-error" : ""}>错误 {errors}</span></div>
    <pre className="log-output">{log || "等待运行日志..."}</pre>
  </section>;
}

function DrawPage(props: SharedProps) {
  const [device, setDevice] = useState(() => window.localStorage.getItem(drawDeviceStorageKey) ?? "");
  const [sessions, setSessions] = useState<Array<{ file: string; summary: Record<string, unknown> }>>([]);
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([]);
  const [pairs, setPairs] = useState<Array<Record<string, unknown>>>([]);
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const selectedSessionIdRef = useRef("");
  const [selectedPairId, setSelectedPairId] = useState("");
  const [beforeImage, setBeforeImage] = useState<string | null>(null);
  const [afterImage, setAfterImage] = useState<string | null>(null);
  const running = !!props.running.draw;
  const plan = props.plans.includes("choukaka.json") ? "choukaka.json" : "";
  const selectedSession = sessions.find((item) => String(item.summary.session_id ?? "") === selectedSessionId) ?? sessions[0];
  const selectedPair = pairs.find((pair) => pairKey(pair) === selectedPairId) ?? pairs[0];
  const drawTypes = drawTypeSummary(events);

  useEffect(() => {
    setDevice((current) => {
      const remembered = current || window.localStorage.getItem(drawDeviceStorageKey) || "";
      const nextDevice = props.devices.includes(remembered) ? remembered : (props.devices[0] ?? "");
      if (nextDevice) window.localStorage.setItem(drawDeviceStorageKey, nextDevice);
      else window.localStorage.removeItem(drawDeviceStorageKey);
      return nextDevice;
    });
  }, [props.devices]);

  const selectDevice = (nextDevice: string) => {
    setDevice(nextDevice);
    if (nextDevice) window.localStorage.setItem(drawDeviceStorageKey, nextDevice);
    else window.localStorage.removeItem(drawDeviceStorageKey);
  };

  const loadSession = async (sessionId: string) => {
    if (!sessionId) {
      setEvents([]);
      setPairs([]);
      setSelectedPairId("");
      return;
    }
    const [nextEvents, nextPairs] = await Promise.all([
      window.bsManager.drawEvents(sessionId),
      window.bsManager.drawScreenshotPairs(sessionId),
    ]);
    setEvents(nextEvents);
    setPairs(nextPairs);
    setSelectedPairId((current) => nextPairs.some((pair) => pairKey(pair) === current) ? current : pairKey(nextPairs[0]));
  };

  const refreshHistory = async () => {
    try {
      const items = await window.bsManager.drawListSessions();
      setSessions(items);
      const preferredSessionId = selectedSessionIdRef.current;
      const preserved = items.some((item) => String(item.summary.session_id ?? "") === preferredSessionId)
        ? preferredSessionId
        : String(items[0]?.summary.session_id ?? "");
      selectedSessionIdRef.current = preserved;
      setSelectedSessionId(preserved);
      await loadSession(preserved);
    } catch (error) {
      props.setNotice(`读取抽卡记录失败: ${String(error)}`);
    }
  };
  useEffect(() => { void refreshHistory(); }, []);
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void refreshHistory(), 2000);
    return () => window.clearInterval(timer);
  }, [running]);
  useEffect(() => {
    const refreshAfterExit = () => void refreshHistory();
    window.addEventListener("draw-task-finished", refreshAfterExit);
    return () => window.removeEventListener("draw-task-finished", refreshAfterExit);
  }, []);
  useEffect(() => {
    const beforePath = String(selectedPair?.before_path ?? "");
    const afterPath = String(selectedPair?.after_path ?? "");
    void Promise.all([
      beforePath ? window.bsManager.drawImage(beforePath) : Promise.resolve(null),
      afterPath ? window.bsManager.drawImage(afterPath) : Promise.resolve(null),
    ]).then(([before, after]) => {
      setBeforeImage(before);
      setAfterImage(after);
    });
  }, [selectedPair?.before_path, selectedPair?.after_path]);
  const start = () => {
    if (!props.runtime || !plan) {
      props.setNotice("找不到 choukaka.json 计划");
      return;
    }
    const args = [`${props.runtime.root}/adb_bot.py`, "--plan", `${props.runtime.plansDir}/${plan}`, "--adb", props.settings.adbPath];
    if (device) args.push("--device", device);
    void refreshHistory();
    void props.startTask("draw", args);
  };
  return <div className="page">
    <PageHeading title="抽卡控制台" detail="实时读取抽卡统计、角色结果与红卡前后截图。"><button className="button secondary" onClick={() => void props.refreshDevices()}><RefreshCw size={16} />刷新设备</button><button className="button secondary" onClick={() => void window.bsManager.drawOpenScreenshots()}><FolderOpen size={16} />打开截图</button><button className="button secondary" onClick={() => void refreshHistory()}><RefreshCw size={16} />刷新记录</button></PageHeading>
    <div className="draw-console-layout">
      <section className="panel form-panel">
        <label>计划<input readOnly value={plan || "未找到 choukaka.json"} /></label>
        <label>设备<select value={device} onChange={(event) => selectDevice(event.target.value)}>{props.devices.map((name) => <option key={name}>{name}</option>)}</select></label>
        {!running ? <button className="button primary full" disabled={!plan} onClick={start}><Play size={16} />开始抽卡</button> : <button className="button danger full" onClick={() => void window.bsManager.stopTask("draw")}><CircleStop size={16} />停止抽卡</button>}
        <LogPanel title="抽卡日志" text={props.logs.draw ?? ""} />
      </section>
      <section className="draw-workspace">
        {selectedSession ? <>
          <section className="panel draw-current-status">
            <div className="panel-title"><span>当前抽卡状态</span><span className={`run-state ${running ? "live" : ""}`}>{running ? "运行中，自动刷新" : "已停止"}</span></div>
            <div className="metric-grid"><Metric label="抽卡次数" value={selectedSession.summary.draw_started_count} detail={String(selectedSession.summary.updated_at ?? "")} /><Metric label="目标出现" value={selectedSession.summary.target_seen_count} detail={rate(selectedSession.summary.target_seen_count, selectedSession.summary.draw_started_count)} /><Metric label="实际命中" value={selectedSession.summary.target_hit_count} detail={rate(selectedSession.summary.target_hit_count, selectedSession.summary.draw_started_count)} /><Metric label="抽卡类型" value={`${drawTypes.max} / ${drawTypes.min}`} detail="大抽 / 小抽" /></div>
            <div className="draw-result-summary"><span>抽卡结果</span><strong>{drawResultSummary(selectedSession.summary)}</strong></div>
          </section>
          <div className="draw-history-layout">
            <section className="panel draw-session-list"><div className="panel-title"><span>抽卡记录</span><span className="counter">{sessions.length}</span></div><div className="draw-list-scroll">{sessions.map((item) => { const summary = item.summary; const id = String(summary.session_id ?? ""); return <button key={item.file} className={`draw-list-item ${id === String(selectedSession.summary.session_id ?? "") ? "selected" : ""}`} onClick={() => { selectedSessionIdRef.current = id; setSelectedSessionId(id); void loadSession(id); }}><strong>{id}</strong><span>{String(summary.updated_at ?? "")}</span><span>抽卡 {String(summary.draw_started_count ?? 0)} · 命中 {String(summary.target_hit_count ?? 0)}</span><small>{drawResultSummary(summary)}</small></button>; })}</div></section>
            <section className="draw-details">
              <section className="panel"><div className="panel-title"><span>结果截图</span><span className="counter">{pairs.length}</span></div>{pairs.length ? <div className="draw-pairs"><div className="pair-list">{pairs.map((pair) => <button key={pairKey(pair)} className={`pair-list-item ${pairKey(pair) === pairKey(selectedPair) ? "selected" : ""}`} onClick={() => setSelectedPairId(pairKey(pair))}><strong>{pairTitle(pair)}</strong><span>{String(pair.after_saved_at ?? pair.before_saved_at ?? "")}</span></button>)}</div><div className="pair-preview"><div className="pair-preview-heading"><strong>{pairTitle(selectedPair)}</strong><span>{String(selectedPair?.after_saved_at ?? selectedPair?.before_saved_at ?? "")}</span></div><DrawImage label="抽卡前" image={beforeImage} placeholder="暂无抽卡前截图" /><DrawImage label="抽卡后" image={afterImage} placeholder="暂无抽卡后截图" /></div></div> : <p className="empty-note padded-note">当前会话尚未保存红卡前后截图。</p>}</section>
              <section className="panel"><div className="panel-title"><span>事件时间线</span><span className="counter">{events.length}</span></div><div className="draw-event-list">{events.length ? [...events].reverse().map((event, index) => <div className="draw-event" key={`${String(event.timestamp ?? "")}-${index}`}><div><strong>{eventTitle(String(event.event ?? ""))}</strong>{event.draw_type ? <span className="event-type">{String(event.draw_type).toUpperCase()}</span> : null}</div><time>{String(event.timestamp ?? "")}</time><p>抽卡 {String(event.draw_started_count ?? 0)} · 出现 {String(event.target_seen_count ?? 0)} · 命中 {String(event.target_hit_count ?? 0)}{event.matched_role_note || event.matched_template ? ` · ${String(event.matched_role_note || event.matched_template)}` : ""}</p></div>) : <p className="empty-note padded-note">当前会话还没有事件记录。</p>}</div></section>
            </section>
          </div>
        </> : <section className="panel empty-draw-state"><strong>暂无抽卡记录</strong><p>开始抽卡后，这里会自动显示会话统计、角色结果和红卡前后截图。</p></section>}
      </section>
    </div>
  </div>;
}

function pairKey(pair: Record<string, unknown> | undefined) {
  return String(pair?.pair_prefix ?? "");
}

function pairTitle(pair: Record<string, unknown> | undefined) {
  const label = String(pair?.before_label ?? pair?.after_label ?? "记录").toUpperCase();
  return `${label} #${String(pair?.pair_index ?? "")}`;
}

function rate(numerator: unknown, denominator: unknown) {
  const total = Number(denominator ?? 0);
  return total > 0 ? `${(Number(numerator ?? 0) / total * 100).toFixed(1)}%` : "0%";
}

function drawResultSummary(summary: Record<string, unknown>) {
  const counts = summary.role_hit_counts && typeof summary.role_hit_counts === "object" ? summary.role_hit_counts as Record<string, unknown> : {};
  const notes = summary.role_notes && typeof summary.role_notes === "object" ? summary.role_notes as Record<string, unknown> : {};
  const defaultOrder = new Map(defaultRedRoles.map(([template], index) => [template, index]));
  const defaultNotes = Object.fromEntries(defaultRedRoles);
  const templates = [...new Set([...defaultRedRoles.map(([template]) => template), ...Object.keys(counts)])];
  return templates
    .sort((left, right) => Number(counts[right] ?? 0) - Number(counts[left] ?? 0) || (defaultOrder.get(left) ?? Number.MAX_SAFE_INTEGER) - (defaultOrder.get(right) ?? Number.MAX_SAFE_INTEGER) || left.localeCompare(right, "zh-CN"))
    .map((template) => `${String(notes[template] ?? defaultNotes[template] ?? template.replace(/\.[^.]+$/, ""))}*${Number(counts[template] ?? 0)}`)
    .join(" · ");
}

function drawTypeSummary(events: Array<Record<string, unknown>>) {
  const starts = events.filter((event) => event.event === "draw_started");
  return {
    min: starts.filter((event) => event.draw_type === "min").length,
    max: starts.filter((event) => event.draw_type === "max").length,
  };
}

function eventTitle(event: string) {
  return ({ draw_started: "开启抽卡", target_seen: "目标出现", target_hit: "命中目标卡", target_miss: "未命中目标卡" } as Record<string, string>)[event] ?? (event || "事件");
}

function DrawImage({ label, image, placeholder }: { label: string; image: string | null; placeholder: string }) {
  return <div className="draw-image"><span>{label}</span>{image ? <img src={image} alt={label} /> : <div className="draw-image-placeholder">{placeholder}</div>}</div>;
}

function Metric({ label, value, detail }: { label: string; value: unknown; detail?: string }) {
  return <div><span>{label}</span><strong>{String(value ?? 0)}</strong>{detail ? <small>{detail}</small> : null}</div>;
}

function RecorderPage(props: SharedProps) {
  const [device, setDevice] = useState("");
  const [name, setName] = useState("recorded.json");
  const [loop, setLoop] = useState("-1");
  const [invertX, setInvertX] = useState(false);
  const [invertY, setInvertY] = useState(false);
  const [swapXY, setSwapXY] = useState(false);
  const [cleanNoise, setCleanNoise] = useState(true);
  const running = !!props.running.recorder;
  const start = () => {
    if (!props.runtime) return;
    const args = [`${props.runtime.root}/record_touch.py`, "--output", `${props.runtime.plansDir}/${name.replace(/\.json$/i, "")}.json`, "--adb", props.settings.adbPath, "--loop-count", loop];
    if (device) args.push("--device", device, "--profile", `${props.runtime.root}/recording_profiles/${device.replace(/[^a-zA-Z0-9._-]/g, "_")}.json`);
    if (invertX) args.push("--invert-x");
    if (invertY) args.push("--invert-y");
    if (swapXY) args.push("--swap-xy");
    if (!cleanNoise) args.push("--no-clean-noise");
    void props.startTask("recorder", args);
  };
  return <div className="page">
    <PageHeading title="触摸录制" detail="从设备原始触摸事件生成可编辑、可循环的 JSON 计划。"><button className="button secondary" onClick={() => void props.refreshDevices()}><RefreshCw size={16} />刷新设备</button></PageHeading>
    <div className="two-column">
      <section className="panel form-panel">
        <label>输出文件<input value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label>设备序列号<select value={device} onChange={(event) => setDevice(event.target.value)}><option value="">自动选择健康设备</option>{props.devices.map((name) => <option key={name}>{name}</option>)}</select></label>
        <label>循环次数<input value={loop} onChange={(event) => setLoop(event.target.value)} /></label>
        <Toggle label="清理明显误触点击" checked={cleanNoise} onChange={setCleanNoise} />
        <Toggle label="反转 X 轴" checked={invertX} onChange={setInvertX} />
        <Toggle label="反转 Y 轴" checked={invertY} onChange={setInvertY} />
        <Toggle label="交换 X/Y 轴" checked={swapXY} onChange={setSwapXY} />
        {!running ? <button className="button primary full" onClick={start}><Radio size={16} />开始录制</button> : <button className="button danger full" onClick={() => void window.bsManager.stopTask("recorder")}><CircleStop size={16} />停止并保存</button>}
      </section>
      <LogPanel title="录制日志" text={props.logs.recorder ?? ""} />
    </div>
  </div>;
}

function CalibrationPage(props: SharedProps) {
  const [device, setDevice] = useState("");
  const [screen, setScreen] = useState<string>("");
  const [image, setImage] = useState<string>("");
  const [cropName, setCropName] = useState("captured_template");
  const [cropX, setCropX] = useState("0");
  const [cropY, setCropY] = useState("0");
  const [cropWidth, setCropWidth] = useState("1080");
  const [cropHeight, setCropHeight] = useState("1920");
  useEffect(() => {
    if (!device && props.devices.length === 1) setDevice(props.devices[0]);
  }, [device, props.devices]);
  const picking = !!props.running["click-picker"];
  const command = async (args: string[]) => {
    try {
      const output = await window.bsManager.adbRun(props.settings.adbPath, device ? ["-s", device, ...args] : args);
      setScreen(output.text || `exit code ${output.code}`);
      if (output.code !== 0) props.setNotice(`ADB 命令失败，退出码 ${output.code}`);
    } catch (error) {
      setScreen(String(error));
      props.setNotice(`ADB 命令失败: ${String(error)}`);
    }
  };
  const capture = async () => {
    if (!device) return props.setNotice("请填写目标设备序列号");
    try { setImage(await window.bsManager.screenshot(props.settings.adbPath, device)); } catch (error) { setScreen(String(error)); }
  };
  const saveCrop = async () => {
    if (!image) return;
    const source = new Image();
    source.src = image;
    await new Promise<void>((resolve, reject) => { source.onload = () => resolve(); source.onerror = () => reject(new Error("截图加载失败")); });
    const x = Math.max(0, Math.min(source.naturalWidth - 1, Math.round(Number(cropX) || 0)));
    const y = Math.max(0, Math.min(source.naturalHeight - 1, Math.round(Number(cropY) || 0)));
    const width = Math.max(1, Math.min(source.naturalWidth - x, Math.round(Number(cropWidth) || source.naturalWidth)));
    const height = Math.max(1, Math.min(source.naturalHeight - y, Math.round(Number(cropHeight) || source.naturalHeight)));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("无法创建图片画布");
    context.drawImage(source, x, y, width, height, 0, 0, width, height);
    const output = await window.bsManager.templatesSaveCapture(cropName, canvas.toDataURL("image/png"));
    props.setNotice(`已保存模板: ${output}`);
  };
  const startPicking = () => {
    if (!props.runtime || !device) {
      props.setNotice("请先刷新并选择设备");
      return;
    }
    props.setPickedCoordinates([]);
    props.startTask("click-picker", [
      `${props.runtime.root}/record_touch.py`,
      "--output", `${props.runtime.root}/diagnostics/click_picker.json`,
      "--adb", props.settings.adbPath,
      "--device", device,
      "--print-clicks-only",
    ]);
  };
  const pickFromScreenshot = (event: React.MouseEvent<HTMLImageElement>) => {
    const target = event.currentTarget;
    if (!target.naturalWidth || !target.naturalHeight) return;
    const bounds = target.getBoundingClientRect();
    const x = Math.max(0, Math.min(target.naturalWidth - 1, Math.round((event.clientX - bounds.left) * target.naturalWidth / bounds.width)));
    const y = Math.max(0, Math.min(target.naturalHeight - 1, Math.round((event.clientY - bounds.top) * target.naturalHeight / bounds.height)));
    props.setPickedCoordinates([{ x, y, device, capturedAt: new Date().toISOString() }, ...props.pickedCoordinates].slice(0, 10));
    props.setNotice(`已从截图取点: (${x}, ${y})`);
  };
  return <div className="page">
    <PageHeading title="设备标定" detail="检查连接、屏幕尺寸，发送点击/滑动并抓取当前画面。"><button className="button secondary" onClick={() => void props.refreshDevices()}><RefreshCw size={16} />刷新设备</button></PageHeading>
    <div className="calibration-grid">
      <section className="panel form-panel">
        <label>设备序列号<select value={device} onChange={(event) => setDevice(event.target.value)}><option value="">使用默认设备</option>{props.devices.map((name) => <option key={name}>{name}</option>)}</select></label>
        <div className="button-grid">
          <button className="button secondary" onClick={() => void command(["get-state"])}>Ping</button>
          <button className="button secondary" onClick={() => void command(["shell", "wm", "size"])}>屏幕尺寸</button>
          <button className="button secondary" onClick={() => void command(["shell", "input", "tap", "500", "500"])}>测试点击</button>
          <button className="button secondary" onClick={() => void command(["shell", "input", "swipe", "300", "600", "800", "600", "450"])}>测试滑动</button>
        </div>
        <div className="picker-actions">
          {!picking ? <button className="button secondary" onClick={startPicking}><Crosshair size={16} />开始取点</button> : <button className="button danger" onClick={() => void window.bsManager.stopTask("click-picker")}><CircleStop size={16} />停止取点</button>}
          <button className="button quiet" disabled={props.pickedCoordinates.length === 0} onClick={() => props.setPickedCoordinates([])}><Trash2 size={15} />清空坐标</button>
        </div>
        {props.pickedCoordinates.length > 0 && <div className="coordinate-list">{props.pickedCoordinates.map((point, index) => <div key={`${point.capturedAt}-${index}`}><code>x={point.x}, y={point.y}</code><button className="button quiet" onClick={() => props.insertAction({ type: "click", x: point.x, y: point.y, remark: `取点 (${point.x}, ${point.y})` })}>插入 click</button></div>)}</div>}
        <button className="button primary full" onClick={capture}><MonitorSmartphone size={16} />抓取屏幕</button>
        {image && <div className="crop-tools"><label>模板名<input value={cropName} onChange={(event) => setCropName(event.target.value)} /></label><div className="crop-grid"><input aria-label="裁剪 X" value={cropX} onChange={(event) => setCropX(event.target.value)} /><input aria-label="裁剪 Y" value={cropY} onChange={(event) => setCropY(event.target.value)} /><input aria-label="裁剪宽度" value={cropWidth} onChange={(event) => setCropWidth(event.target.value)} /><input aria-label="裁剪高度" value={cropHeight} onChange={(event) => setCropHeight(event.target.value)} /></div><button className="button secondary full" onClick={() => void saveCrop().catch((error) => props.setNotice(`保存模板失败: ${String(error)}`))}><SquareDashedMousePointer size={16} />裁剪并保存模板</button></div>}
        <pre className="command-result">{screen || "命令输出会显示在这里。"}</pre>
        {(picking || props.logs["click-picker"]) && <pre className="command-result picker-log">{props.logs["click-picker"] || "正在等待触摸事件..."}</pre>}
      </section>
      <section className="panel screenshot-panel">{image ? <img src={image} alt="设备当前屏幕" title="点击截图取点" onClick={pickFromScreenshot} /> : <div className="screen-placeholder"><MonitorSmartphone size={42} /><p>抓取的设备屏幕会显示在这里</p></div>}</section>
    </div>
  </div>;
}

function DiagnosticsPage(props: SharedProps) {
  const [device, setDevice] = useState("");
  const running = !!props.running.diagnostic;
  const start = () => {
    if (!props.runtime) return;
    const normalized = (device || "default").replace(/[^a-zA-Z0-9._-]/g, "_");
    const args = [`${props.runtime.root}/record_touch.py`, "--output", `${props.runtime.root}/diagnostics/${normalized}_recording_diagnostic.json`, "--adb", props.settings.adbPath, "--diagnose-self-heal"];
    if (device) args.push("--device", device, "--profile", `${props.runtime.root}/recording_profiles/${normalized}.json`);
    void props.startTask("diagnostic", args);
  };
  return <div className="page">
    <PageHeading title="录制诊断" detail="采集触摸映射样本，生成诊断报告，并在可靠时保存改进后的 profile。"><button className="button secondary" onClick={() => void props.refreshDevices()}><RefreshCw size={16} />刷新设备</button></PageHeading>
    <div className="two-column">
      <section className="panel form-panel">
        <label>设备序列号<select value={device} onChange={(event) => setDevice(event.target.value)}><option value="">自动选择健康设备</option>{props.devices.map((name) => <option key={name}>{name}</option>)}</select></label>
        <div className="diagnostic-note"><SlidersHorizontal size={18} /><p>诊断过程会执行映射校验；请确保设备屏幕可见且不要在过程中切换模拟器窗口。</p></div>
        {!running ? <button className="button primary full" onClick={start}><Activity size={16} />开始诊断</button> : <button className="button danger full" onClick={() => void window.bsManager.stopTask("diagnostic")}><CircleStop size={16} />停止诊断</button>}
      </section>
      <LogPanel title="诊断输出" text={props.logs.diagnostic ?? ""} />
    </div>
  </div>;
}

function SettingsPage(props: SharedProps) {
  const save = async () => {
    const saved = await window.bsManager.settingsSave(props.settings);
    props.setSettings(saved);
    props.setNotice("设置已保存");
  };
  return <div className="page">
    <PageHeading title="环境设置" detail="配置跨平台运行时使用的 ADB 与 Python 可执行文件。" />
    <div className="settings-stack">
      <section className="panel form-panel">
        <label>ADB 可执行文件<input value={props.settings.adbPath} onChange={(event) => props.setSettings({ ...props.settings, adbPath: event.target.value })} /></label>
        <label>Python 可执行文件<input value={props.settings.pythonPath} onChange={(event) => props.setSettings({ ...props.settings, pythonPath: event.target.value })} /></label>
        <p className="field-note">Windows 可以填 `adb.exe` / `python.exe` 或完整路径；macOS 可填 `adb` / `python3`。</p>
        <button className="button primary" onClick={() => void save()}><Save size={16} />保存设置</button>
      </section>
      <section className="panel path-panel">
        <span className="eyebrow">用户运行目录</span>
        <code>{props.runtime?.root ?? "加载中..."}</code>
        <p>脚本、模板、录制配置和诊断数据都存放在这里；应用升级不会覆盖用户创建的计划文件。</p>
        <button className="button secondary" onClick={() => void window.bsManager.templatesList().then(() => props.setNotice("模板目录可通过系统文件管理器查看"))}><FolderOpen size={16} />检查模板</button>
      </section>
    </div>
  </div>;
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (next: boolean) => void }) {
  return <label className="toggle"><span>{label}</span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><i /></label>;
}

function LogPanel({ title, text }: { title: string; text: string }) {
  return <section className="panel log-panel"><div className="panel-title"><span>{title}</span><TerminalSquare size={16} /></div><pre className="log-output">{text || "等待任务输出..."}</pre></section>;
}
