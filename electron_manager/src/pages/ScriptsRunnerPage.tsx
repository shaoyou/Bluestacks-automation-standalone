import { useEffect, useState } from "react";
import { CircleStop, Copy, FileCode2, MonitorSmartphone, PanelsTopLeft, Play, Plus, RefreshCw, Save, SquareDashedMousePointer, Trash2, Upload } from "lucide-react";
import { PageHeading, Toggle } from "../components/layout";
import { updateVariables, type RunnerSelection, type ScriptVariable, type SharedProps } from "../app/shared";

export function ScriptsPage(props: SharedProps) {
  const dirty = props.source !== props.savedSource;
  const [templates, setTemplates] = useState<string[]>([]);
  const [variables, setVariables] = useState<ScriptVariable[]>([]);
  const [quickX, setQuickX] = useState("540");
  const [quickY, setQuickY] = useState("960");
  const [quickSeconds, setQuickSeconds] = useState("1");
  const [quickTemplate, setQuickTemplate] = useState("");
  const [quickThreshold, setQuickThreshold] = useState("0.9");
  useEffect(() => { void window.bsManager.templatesList().then(setTemplates); }, []);
  useEffect(() => {
    try {
      const raw = (JSON.parse(props.source) as { variables?: unknown }).variables;
      if (Array.isArray(raw)) setVariables(raw.map((item) => ({ name: String((item as Record<string, unknown>).name ?? ""), value: String((item as Record<string, unknown>).value ?? ""), note: String((item as Record<string, unknown>).note ?? "") })));
      else if (raw && typeof raw === "object") setVariables(Object.entries(raw as Record<string, unknown>).map(([name, value]) => ({ name, value: String(value), note: "" })));
      else setVariables([]);
    } catch { setVariables([]); }
  }, [props.activePlan, props.source]);
  const applyVariables = () => {
    try { props.setSource(updateVariables(props.source, variables.filter((item) => item.name.trim()))); props.setNotice("变量已写入编辑器，请保存计划"); }
    catch { props.setNotice("变量未写入：当前 JSON 无法解析"); }
  };
  const refresh = async () => {
    try { const refreshed = await props.refreshPlans(); if (props.activePlan && refreshed.includes(props.activePlan)) await props.loadPlan(props.activePlan); props.setNotice("计划列表已刷新"); }
    catch (error) { props.setNotice(`刷新失败: ${String(error)}`); }
  };
  const numeric = (value: string, fallback: number) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const insertImage = (click: boolean) => {
    if (!quickTemplate) return props.setNotice("请先选择图像模板");
    props.insertAction({ type: click ? "find_image_click" : "find_image", template: `../image_templates/${quickTemplate}`, threshold: Math.min(1, Math.max(0, numeric(quickThreshold, 0.9))), timeout_sec: 8, remark: click ? "查找图像后点击" : "查找图像" });
  };
  return <div className="page scripts-page">
    <PageHeading title="脚本工作区" detail="编辑 JSON 计划、管理运行变量与图像模板。"><button className="button secondary" onClick={props.createPlan}><Plus size={16} />新建</button><button className="button primary" disabled={!props.activePlan || !dirty} onClick={() => void props.savePlan()}><Save size={16} />保存</button></PageHeading>
    <div className="script-layout">
      <section className="plan-list panel"><div className="panel-title"><span>计划文件</span><button className="icon-button" title="刷新" onClick={() => void refresh()}><RefreshCw size={15} /></button></div><div className="plan-scroll">{props.plans.map((name) => <button key={name} className={`plan-item ${name === props.activePlan ? "selected" : ""}`} onClick={() => void props.loadPlan(name)}><FileCode2 size={16} /><span>{name}</span></button>)}</div><div className="list-footer"><button className="button quiet danger" disabled={!props.activePlan} onClick={() => void props.deletePlan()}><Trash2 size={15} />删除</button></div></section>
      <section className="editor-column"><div className="editor-toolbar"><div><span className="eyebrow">JSON 计划</span><strong>{props.activePlan ?? "未选择计划"}</strong>{dirty && <span className="dirty-mark">未保存</span>}</div><button className="button quiet" onClick={() => void window.bsManager.templatesImport().then(async (value) => { if (value) { setTemplates(await window.bsManager.templatesList()); props.setNotice(`模板已导入: ${value}`); } })}><Upload size={15} />导入模板</button></div><textarea className="code-editor" aria-label="脚本 JSON 编辑器" spellCheck={false} value={props.source} onChange={(event) => { props.setSource(event.target.value); props.setNotice("正在编辑"); }} /></section>
      <aside className="inspector">
        <section className="panel"><div className="panel-title"><span>运行变量</span><button className="icon-button" title="添加变量" onClick={() => setVariables([...variables, { name: "", value: "", note: "" }])}><Plus size={15} /></button></div><div className="variables">{variables.length === 0 && <p className="empty-note">此计划暂无变量。</p>}{variables.map((item, index) => <div className="variable-row" key={`${item.name}-${index}`}><input aria-label="变量名" value={item.name} placeholder="NAME" onChange={(event) => setVariables(variables.map((v, i) => i === index ? { ...v, name: event.target.value } : v))} /><input aria-label="变量值" value={item.value} placeholder="value" onChange={(event) => setVariables(variables.map((v, i) => i === index ? { ...v, value: event.target.value } : v))} /><input aria-label="变量备注" value={item.note} placeholder="备注" onChange={(event) => setVariables(variables.map((v, i) => i === index ? { ...v, note: event.target.value } : v))} /><button className="icon-button" title="删除变量" onClick={() => setVariables(variables.filter((_, i) => i !== index))}><Trash2 size={14} /></button></div>)}</div><button className="button secondary full" onClick={applyVariables}><Save size={15} />应用变量</button></section>
        <section className="panel template-panel"><div className="panel-title"><span>图像模板</span><span className="counter">{templates.length}</span></div><div className="template-list">{templates.slice(0, 12).map((name) => <div key={name}><SquareDashedMousePointer size={14} />{name}</div>)}</div></section>
        <section className="panel quick-actions-panel"><div className="panel-title"><span>快捷插入</span></div><div className="quick-grid"><input aria-label="快捷 X" value={quickX} onChange={(event) => setQuickX(event.target.value)} /><input aria-label="快捷 Y" value={quickY} onChange={(event) => setQuickY(event.target.value)} /></div><button className="button secondary full" onClick={() => props.insertAction({ type: "click", x: Math.round(numeric(quickX, 540)), y: Math.round(numeric(quickY, 960)), remark: "快捷点击" })}>插入 click</button><div className="quick-grid"><input aria-label="等待秒数" value={quickSeconds} onChange={(event) => setQuickSeconds(event.target.value)} /><button className="button secondary" onClick={() => props.insertAction({ type: "wait", seconds: Math.max(0, numeric(quickSeconds, 1)), remark: "快捷等待" })}>插入 wait</button></div><select aria-label="快捷模板" value={quickTemplate} onChange={(event) => setQuickTemplate(event.target.value)}><option value="">选择图像模板</option>{templates.map((name) => <option key={name}>{name}</option>)}</select><div className="quick-grid"><input aria-label="图像阈值" value={quickThreshold} onChange={(event) => setQuickThreshold(event.target.value)} /><button className="button secondary" onClick={() => insertImage(false)}>查找</button></div><button className="button secondary full" onClick={() => insertImage(true)}>查找后点击</button></section>
      </aside>
    </div>
  </div>;
}

export function RunnerPage(props: SharedProps & { runnerId?: string; initialPlan?: string; standalone?: boolean }) {
  const runnerId = props.runnerId ?? "main";
  const taskId = `runner-${runnerId}`;
  const selectionKey = `runner-selection-${runnerId}`;
  const [selection, setSelection] = useState<RunnerSelection>(() => {
    try { const saved = JSON.parse(window.localStorage.getItem(selectionKey) ?? "") as Partial<RunnerSelection>; return { plan: saved.plan ?? props.initialPlan ?? props.activePlan ?? "", device: saved.device ?? "", profitPerCycle: saved.profitPerCycle ?? "0", showRealtimeLogs: saved.showRealtimeLogs ?? false }; }
    catch { return { plan: props.initialPlan ?? props.activePlan ?? "", device: "", profitPerCycle: "0", showRealtimeLogs: false }; }
  });
  const [variables, setVariables] = useState<ScriptVariable[]>([]);
  const running = !!props.running[taskId];
  const rawLog = props.logs[taskId] ?? "";
  const log = selection.showRealtimeLogs ? rawLog : rawLog.split(/\r?\n/).filter((line) => !/if_image \[\d+\/\d+\] template .+ not matched|CMD adb shell input tap/.test(line)).join("\n");
  const cycles = (rawLog.match(/Loop start|循环开始|\[loop/gi) ?? []).length;
  const clicks = (rawLog.match(/\bClick \(|点击\s*\(|adb shell input tap/g) ?? []).length;
  const errors = (rawLog.match(/\bERROR\b|Traceback|错误/g) ?? []).length;
  const expectedProfit = Number.isFinite(Number(selection.profitPerCycle)) ? new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(Number(selection.profitPerCycle) * cycles) : "0";
  useEffect(() => { window.localStorage.setItem(selectionKey, JSON.stringify(selection)); }, [selection, selectionKey]);
  useEffect(() => { if (props.plans.length) setSelection((current) => props.plans.includes(current.plan) ? current : { ...current, plan: props.initialPlan && props.plans.includes(props.initialPlan) ? props.initialPlan : props.activePlan && props.plans.includes(props.activePlan) ? props.activePlan : props.plans[0] }); }, [props.activePlan, props.initialPlan, props.plans]);
  useEffect(() => {
    if (!selection.plan) return setVariables([]);
    void window.bsManager.plansRead(selection.plan).then((text) => {
      try { const raw = (JSON.parse(text) as { variables?: unknown }).variables; if (Array.isArray(raw)) setVariables(raw.map((item) => ({ name: String((item as Record<string, unknown>).name ?? ""), value: String((item as Record<string, unknown>).value ?? ""), note: String((item as Record<string, unknown>).note ?? "") }))); else if (raw && typeof raw === "object") setVariables(Object.entries(raw as Record<string, unknown>).map(([name, value]) => ({ name, value: String(value), note: "" }))); else setVariables([]); } catch { setVariables([]); }
    }).catch((error) => props.setNotice(`读取变量失败: ${String(error)}`));
  }, [selection.plan]);
  const saveVariables = async (nextVariables = variables) => { if (!selection.plan) return; try { const text = await window.bsManager.plansRead(selection.plan); await window.bsManager.plansSave(selection.plan, updateVariables(text, nextVariables.filter((item) => item.name.trim()))); props.setNotice(`变量已保存到 ${selection.plan}`); } catch (error) { props.setNotice(`变量保存失败: ${String(error)}`); } };
  const start = () => { if (!props.runtime) return props.setNotice("运行环境尚未加载完成"); if (!selection.plan) return props.setNotice("请先选择一个计划文件"); const args = [`${props.runtime.root}/adb_bot.py`, "--plan", `${props.runtime.plansDir}/${selection.plan}`, "--adb", props.settings.adbPath]; if (selection.device.trim()) args.push("--device", selection.device.trim()); props.setNotice(`${props.standalone ? "运行窗口" : "内置运行器"}正在启动`); void props.startTask(taskId, args); };
  const updateSelection = (next: Partial<RunnerSelection>) => setSelection((current) => ({ ...current, ...next }));
  const updateVariable = (index: number, key: "value" | "note", value: string) => setVariables(variables.map((variable, variableIndex) => variableIndex === index ? { ...variable, [key]: value } : variable));
  return <div className={`page runner-page ${props.standalone ? "runner-page-standalone" : ""}`}>
    <PageHeading title={props.standalone ? "运行窗口" : "运行中心"} detail={props.standalone ? "此窗口的计划、设备、变量和日志均独立运行。" : "当前页面内置一个运行器；可继续打开多个独立运行窗口并行执行。"}><button className="button secondary" onClick={() => void props.refreshDevices()}><RefreshCw size={16} />刷新设备</button>{!props.standalone && <button className="button primary" onClick={() => void window.bsManager.openRunWindow(selection.plan)}><PanelsTopLeft size={16} />多开运行</button>}</PageHeading>
    <div className="device-strip panel"><MonitorSmartphone size={18} /><strong>已发现 {props.devices.length} 个设备</strong><span>{props.devices.join("  ·  ") || "请检查 ADB 路径、模拟器或 USB 调试连接"}</span></div>
    <section className="runner-card panel"><div className="runner-header"><div><span className="eyebrow">自动化进程</span><h2>{props.standalone ? `运行窗口 ${runnerId.slice(-6)}` : "内置运行器"}</h2></div><span className={`run-state ${running ? "live" : ""}`}>{running ? "运行中" : "待命"}</span></div><div className="runner-fields"><label>计划<select value={selection.plan} onChange={(event) => updateSelection({ plan: event.target.value })}>{props.plans.map((name) => <option key={name}>{name}</option>)}</select></label><label>设备<input list={`runner-devices-${runnerId}`} value={selection.device} placeholder="使用计划默认设备" onChange={(event) => updateSelection({ device: event.target.value })} /><datalist id={`runner-devices-${runnerId}`}>{props.devices.map((name) => <option key={name} value={name} />)}</datalist></label></div><div className="runner-actions">{!running ? <button className="button primary" disabled={!selection.plan} onClick={start}><Play size={16} />启动</button> : <button className="button danger" onClick={() => void window.bsManager.stopTask(taskId)}><CircleStop size={16} />停止</button>}<button className="button quiet" onClick={() => props.clearTaskLog(taskId)}><Trash2 size={15} />清日志</button><button className="icon-button" title="复制日志" onClick={() => void navigator.clipboard.writeText(rawLog)}><Copy size={16} /></button></div><div className="runner-metrics"><span>循环 {cycles || "--"}</span><span>点击 {clicks}</span><span className={errors ? "metric-error" : ""}>错误 {errors}</span><label className="runner-profit">单次收益<input value={selection.profitPerCycle} onChange={(event) => updateSelection({ profitPerCycle: event.target.value })} /></label><span>预期 {expectedProfit}</span><Toggle label="实时输出" checked={selection.showRealtimeLogs} onChange={(showRealtimeLogs) => updateSelection({ showRealtimeLogs })} /></div><section className="runner-variables"><div className="panel-title"><span>运行变量</span><button className="button quiet" onClick={() => void saveVariables()}><Save size={15} />保存变量</button></div>{variables.length ? <div className="runner-variable-list">{variables.map((variable, index) => <div key={`${variable.name}-${index}`}><strong>{variable.name}</strong><input aria-label={`${variable.name} 的值`} value={variable.value} onChange={(event) => updateVariable(index, "value", event.target.value)} onBlur={() => void saveVariables(variables)} /><input aria-label={`${variable.name} 的备注`} value={variable.note} placeholder="备注" onChange={(event) => updateVariable(index, "note", event.target.value)} onBlur={() => void saveVariables(variables)} /></div>)}</div> : <p className="empty-note padded-note">此计划暂无运行变量。</p>}</section><pre className="log-output">{log || "等待运行日志..."}</pre></section>
  </div>;
}
