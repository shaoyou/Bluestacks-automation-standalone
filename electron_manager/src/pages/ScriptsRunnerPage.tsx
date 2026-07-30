import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronLeft, ChevronRight, ChevronUp, CircleStop, Copy, Crosshair, FileCode2, ImagePlus, LockKeyhole, MonitorSmartphone, PanelsTopLeft, Play, Plus, RefreshCw, Save, ScanText, SquareDashedMousePointer, Trash2, Upload, X } from "lucide-react";
import { PageHeading, Toggle } from "../components/layout";
import { updateVariables, type RunnerSelection, type ScriptVariable, type SharedProps } from "../app/shared";

type ImageRegion = { x: number; y: number; width: number; height: number };
type WheelTurn = { id: number; angle: string; seconds: string };
type CaptureMode = "template" | "region";

function numberValue(value: string, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function compactNumber(value: number) {
  return Number.isInteger(value) ? String(value) : String(Math.round(value * 1000) / 1000);
}

function imagePath(name: string) {
  return `../image_templates/${name}`;
}

function actionRemark(type: string, label: string) {
  return `${type}: ${label}`;
}

function actionsBounds(source: string) {
  const match = /"actions"\s*:\s*\[/.exec(source);
  if (!match || match.index === undefined) return null;
  const start = match.index + match[0].lastIndexOf("[");
  let depth = 0;
  let quoted = false;
  let escaped = false;
  for (let index = start; index < source.length; index += 1) {
    const character = source[index];
    if (quoted) {
      if (escaped) escaped = false;
      else if (character === "\\") escaped = true;
      else if (character === "\"") quoted = false;
      continue;
    }
    if (character === "\"") quoted = true;
    else if (character === "[") depth += 1;
    else if (character === "]") {
      depth -= 1;
      if (depth === 0) return { start, end: index };
    }
  }
  return null;
}

function cursorIsInArray(source: string, cursor: number) {
  const stack: string[] = [];
  let quoted = false;
  let escaped = false;
  for (let index = 0; index < cursor; index += 1) {
    const character = source[index];
    if (quoted) {
      if (escaped) escaped = false;
      else if (character === "\\") escaped = true;
      else if (character === "\"") quoted = false;
      continue;
    }
    if (character === "\"") quoted = true;
    else if (character === "{" || character === "[") stack.push(character);
    else if (character === "}" || character === "]") stack.pop();
  }
  return stack.at(-1) === "[";
}

function insertActionAtCursor(source: string, cursor: number, action: Record<string, unknown>) {
  const bounds = actionsBounds(source);
  if (!bounds || cursor < bounds.start + 1 || cursor > bounds.end || !cursorIsInArray(source, cursor)) return null;
  const before = source.slice(bounds.start + 1, cursor).match(/\S(?=\s*$)/)?.[0] ?? "";
  const after = source.slice(cursor, bounds.end).match(/\S/)?.[0] ?? "";
  const lineStart = source.lastIndexOf("\n", cursor - 1) + 1;
  const indent = source.slice(lineStart, cursor).match(/^[\t ]*/)?.[0] ?? "  ";
  const formattedAction = JSON.stringify(action, null, 2).split("\n").map((line) => `${indent}${line}`).join("\n");
  const prefix = before && before !== "[" && before !== "," ? "," : "";
  const suffix = after && after !== "]" && after !== "," ? "," : "";
  const insertion = `${prefix}\n${formattedAction}${suffix}${after && after !== "]" ? `\n${indent}` : ""}`;
  return { source: `${source.slice(0, cursor)}${insertion}${source.slice(cursor)}`, cursor: cursor + insertion.length };
}

function ScreenshotSelector({
  image,
  mode,
  onClose,
  onSelect,
}: {
  image: string;
  mode: CaptureMode;
  onClose: () => void;
  onSelect: (region: ImageRegion) => void;
}) {
  const imageRef = useRef<HTMLImageElement>(null);
  const [selection, setSelection] = useState<ImageRegion | null>(null);
  const dragStart = useRef<{ x: number; y: number } | null>(null);
  const toImagePoint = (event: React.MouseEvent<HTMLImageElement>) => {
    const element = imageRef.current;
    if (!element || !element.naturalWidth || !element.naturalHeight) return null;
    const bounds = element.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(element.naturalWidth, Math.round((event.clientX - bounds.left) * element.naturalWidth / bounds.width))),
      y: Math.max(0, Math.min(element.naturalHeight, Math.round((event.clientY - bounds.top) * element.naturalHeight / bounds.height))),
    };
  };
  const updateSelection = (point: { x: number; y: number }) => {
    const start = dragStart.current;
    if (!start) return;
    setSelection({
      x: Math.min(start.x, point.x),
      y: Math.min(start.y, point.y),
      width: Math.abs(point.x - start.x),
      height: Math.abs(point.y - start.y),
    });
  };
  const overlayStyle = () => {
    const element = imageRef.current;
    if (!selection || !element || !element.naturalWidth) return { display: "none" };
    return {
      left: `${selection.x / element.naturalWidth * 100}%`,
      top: `${selection.y / element.naturalHeight * 100}%`,
      width: `${selection.width / element.naturalWidth * 100}%`,
      height: `${selection.height / element.naturalHeight * 100}%`,
    };
  };
  return <div className="capture-modal-backdrop" role="dialog" aria-modal="true" aria-label={mode === "template" ? "框选图像模板" : "框选搜索区域"}>
    <section className="capture-modal">
      <header><div><strong>{mode === "template" ? "框选图像模板" : "框选图像搜索区域"}</strong><span>拖动鼠标框选设备截图中的区域</span></div><button className="icon-button" title="关闭" onClick={onClose}><X size={17} /></button></header>
      <div className="capture-canvas">
        <div className="capture-image-wrap">
          <img ref={imageRef} src={image} alt="设备截图" draggable={false} onMouseDown={(event) => { const point = toImagePoint(event); if (!point) return; dragStart.current = point; setSelection({ x: point.x, y: point.y, width: 0, height: 0 }); }} onMouseMove={(event) => { const point = toImagePoint(event); if (point) updateSelection(point); }} onMouseUp={(event) => { const point = toImagePoint(event); if (point) updateSelection(point); dragStart.current = null; }} />
          <i className="capture-selection" style={overlayStyle()} />
        </div>
      </div>
      <footer><span>{selection && selection.width > 0 && selection.height > 0 ? `x=${selection.x}, y=${selection.y}, ${selection.width} x ${selection.height}` : "尚未选择区域"}</span><div><button className="button secondary" onClick={onClose}>取消</button><button className="button primary" disabled={!selection || selection.width < 1 || selection.height < 1} onClick={() => selection && onSelect(selection)}>{mode === "template" ? "裁剪并保存" : "使用此区域"}</button></div></footer>
    </section>
  </div>;
}

export function ScriptsPage(props: SharedProps) {
  const dirty = props.source !== props.savedSource;
  const [templates, setTemplates] = useState<string[]>([]);
  const [variables, setVariables] = useState<ScriptVariable[]>([]);
  const [quickX, setQuickX] = useState("540");
  const [quickY, setQuickY] = useState("960");
  const [quickSeconds, setQuickSeconds] = useState("1");
  const [quickText, setQuickText] = useState("START");
  const [quickTextLang, setQuickTextLang] = useState("eng");
  const [quickTextTimeout, setQuickTextTimeout] = useState("8");
  const [quickTemplate, setQuickTemplate] = useState("");
  const [quickThreshold, setQuickThreshold] = useState("0.92");
  const [quickImageTimeout, setQuickImageTimeout] = useState("8");
  const [previewOnly, setPreviewOnly] = useState(false);
  const [editorDevice, setEditorDevice] = useState("");
  const [captureImage, setCaptureImage] = useState("");
  const [captureMode, setCaptureMode] = useState<CaptureMode | null>(null);
  const [captureSize, setCaptureSize] = useState({ width: 1080, height: 1920 });
  const [region, setRegion] = useState<ImageRegion | null>(null);
  const [wheelCenterX, setWheelCenterX] = useState("540");
  const [wheelBottomInset, setWheelBottomInset] = useState("200");
  const [wheelSeconds, setWheelSeconds] = useState("2");
  const [wheelDistance, setWheelDistance] = useState("180");
  const [wheelAngle, setWheelAngle] = useState("0");
  const [wheelTurns, setWheelTurns] = useState<WheelTurn[]>([]);
  const turnCounter = useRef(0);
  const editorRef = useRef<HTMLTextAreaElement>(null);
  const editorSelection = useRef({ start: 0, end: 0 });
  useEffect(() => { void window.bsManager.templatesList().then(setTemplates); }, []);
  useEffect(() => { if (!editorDevice && props.devices.length === 1) setEditorDevice(props.devices[0]); }, [editorDevice, props.devices]);
  useEffect(() => { editorSelection.current = { start: 0, end: 0 }; }, [props.activePlan]);
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
  const numeric = numberValue;
  const append = (action: Record<string, unknown>) => {
    if (!props.activePlan) return props.setNotice("请先选择计划文件");
    const insertion = insertActionAtCursor(props.source, editorSelection.current.start, action);
    if (!insertion) return props.setNotice("请将光标放在 actions 数组内，再插入动作");
    props.setSource(insertion.source);
    props.setNotice("动作已插入当前光标位置，请保存");
    editorSelection.current = { start: insertion.cursor, end: insertion.cursor };
    window.requestAnimationFrame(() => {
      editorRef.current?.focus();
      editorRef.current?.setSelectionRange(insertion.cursor, insertion.cursor);
    });
  };
  const imageSearchRegion = region ?? { x: 0, y: 0, width: captureSize.width, height: captureSize.height };
  const insertImage = (kind: "find_image" | "find_image_click" | "if_image") => {
    if (!quickTemplate) return props.setNotice("请先选择图像模板");
    const template = imagePath(quickTemplate);
    const threshold = Math.min(1, Math.max(0.1, numeric(quickThreshold, 0.92)));
    const timeout = Math.max(0.1, numeric(quickImageTimeout, 8));
    if (kind === "if_image") {
      append({ type: kind, template, threshold, timeout_sec: timeout, interval_sec: 0.6, region: imageSearchRegion, remark: actionRemark("if_image", quickTemplate), then_actions: [{ type: "click_match", remark: "点击当前命中的图标" }], else_actions: [{ type: "wait", seconds: 0.5, remark: "未识别到图标后的分支" }] });
      return;
    }
    append({ type: kind, template, threshold, timeout_sec: timeout, interval_sec: 0.6, region: imageSearchRegion, ...(kind === "find_image_click" && previewOnly ? { preview_only: true } : {}), remark: actionRemark(kind, quickTemplate) });
  };
  const capture = async (mode: CaptureMode) => {
    if (!editorDevice) return props.setNotice("请先选择设备");
    try {
      const image = await window.bsManager.screenshot(props.settings.adbPath, editorDevice);
      const dimensions = await new Promise<{ width: number; height: number }>((resolve, reject) => {
        const source = new Image();
        source.onload = () => resolve({ width: source.naturalWidth, height: source.naturalHeight });
        source.onerror = () => reject(new Error("截图加载失败"));
        source.src = image;
      });
      setCaptureImage(image);
      setCaptureSize(dimensions);
      setCaptureMode(mode);
    } catch (error) { props.setNotice(`抓取设备截图失败: ${String(error)}`); }
  };
  const saveTemplate = async (selection: ImageRegion) => {
    try {
      const source = new Image();
      source.src = captureImage;
      await new Promise<void>((resolve, reject) => { source.onload = () => resolve(); source.onerror = () => reject(new Error("截图加载失败")); });
      const suggested = window.prompt("模板文件名", "captured_template");
      if (!suggested) return;
      const canvas = document.createElement("canvas");
      canvas.width = selection.width;
      canvas.height = selection.height;
      const context = canvas.getContext("2d");
      if (!context) throw new Error("无法创建图片画布");
      context.drawImage(source, selection.x, selection.y, selection.width, selection.height, 0, 0, selection.width, selection.height);
      const saved = await window.bsManager.templatesSaveCapture(suggested, canvas.toDataURL("image/png"));
      const name = saved.split("/").pop() ?? "";
      setQuickTemplate(name);
      setTemplates(await window.bsManager.templatesList());
      setCaptureMode(null);
      props.setNotice(`已保存模板: ${saved}`);
    } catch (error) { props.setNotice(`保存模板失败: ${String(error)}`); }
  };
  const setSearchRegion = (selection: ImageRegion) => {
    setRegion(selection);
    if (quickThreshold === "0.92") setQuickThreshold("0.88");
    setCaptureMode(null);
    props.setNotice(`已设置图像搜索区域: x=${selection.x}, y=${selection.y}, ${selection.width} x ${selection.height}`);
  };
  const wheelCenterY = Math.max(0, captureSize.height - Math.round(numeric(wheelBottomInset, 200)));
  const wheelTrace = (steps: Array<{ angle: number; seconds: number }>) => {
    const centerX = Math.round(numeric(wheelCenterX, captureSize.width / 2));
    const distance = Math.max(1, Math.round(numeric(wheelDistance, 180)));
    let elapsed = 0;
    const points: Array<{ x: number; y: number; t_ms: number }> = [{ x: centerX, y: wheelCenterY, t_ms: 0 }];
    for (const step of steps) {
      const duration = Math.max(100, Math.round(step.seconds * 1000));
      const radians = step.angle * Math.PI / 180;
      const x = centerX + Math.round(Math.cos(radians) * distance);
      const y = wheelCenterY - Math.round(Math.sin(radians) * distance);
      points.push({ x, y, t_ms: Math.min(elapsed + 100, elapsed + duration) });
      elapsed += duration;
      if (points.at(-1)?.t_ms !== elapsed) points.push({ x, y, t_ms: elapsed });
    }
    append({ type: "trace", remark: steps.map((step) => `${compactNumber(step.angle)}度${compactNumber(step.seconds)}秒`).join(" -> "), points, mode: "motion", min_segment_ms: 1, max_segment_ms: 1000 });
  };
  const insertDirection = (angle: number) => wheelTrace([{ angle, seconds: Math.max(0.1, numeric(wheelSeconds, 2)) }]);
  const addTurn = (angle = numeric(wheelAngle, 0)) => setWheelTurns((current) => [...current, { id: ++turnCounter.current, angle: compactNumber(angle), seconds: compactNumber(Math.max(0.1, numeric(wheelSeconds, 2))) }]);
  const startPicker = () => {
    if (!props.runtime || !editorDevice) return props.setNotice("请先选择设备");
    props.setPickedCoordinates([]);
    void props.startTask("click-picker", [`${props.runtime.root}/record_touch.py`, "--output", `${props.runtime.root}/diagnostics/editor_click_picker.json`, "--adb", props.settings.adbPath, "--device", editorDevice, "--print-clicks-only"]);
  };
  const debugTaskId = "editor-debug";
  const debug = () => {
    if (!props.runtime || !props.activePlan) return props.setNotice("请先选择计划文件");
    if (dirty) return props.setNotice("请先保存计划，再进行调试运行");
    const args = [`${props.runtime.root}/adb_bot.py`, "--plan", `${props.runtime.plansDir}/${props.activePlan}`, "--adb", props.settings.adbPath];
    if (editorDevice) args.push("--device", editorDevice);
    void props.startTask(debugTaskId, args);
  };
  return <div className="page scripts-page">
    <PageHeading title="脚本工作区" detail="编辑 JSON 计划，并从设备取点、裁图和生成常用自动化动作。"><button className="button secondary" onClick={props.createPlan}><Plus size={16} />新建</button><button className="button secondary" onClick={() => void props.refreshDevices()}><RefreshCw size={16} />刷新设备</button>{props.running[debugTaskId] ? <button className="button danger" onClick={() => void window.bsManager.stopTask(debugTaskId)}><CircleStop size={16} />停止调试</button> : <button className="button secondary" disabled={!props.activePlan || dirty} onClick={debug}><Play size={16} />调试运行</button>}<button className="button primary" disabled={!props.activePlan || !dirty} onClick={() => void props.savePlan()}><Save size={16} />保存</button></PageHeading>
    <div className="script-layout">
      <section className="plan-list panel"><div className="panel-title"><span>计划文件</span><button className="icon-button" title="刷新" onClick={() => void refresh()}><RefreshCw size={15} /></button></div><div className="plan-scroll">{props.plans.map((name) => <button key={name} className={`plan-item ${name === props.activePlan ? "selected" : ""}`} onClick={() => void props.loadPlan(name)}><FileCode2 size={16} /><span>{name}</span></button>)}</div><div className="list-footer"><button className="button quiet danger" disabled={!props.activePlan} onClick={() => void props.deletePlan()}><Trash2 size={15} />删除</button></div></section>
      <section className="editor-column"><div className="editor-toolbar"><div><span className="eyebrow">JSON 计划</span><strong>{props.activePlan ?? "未选择计划"}</strong>{dirty && <span className="dirty-mark">未保存</span>}</div><button className="button quiet" onClick={() => void window.bsManager.templatesImport().then(async (value) => { if (value) { setTemplates(await window.bsManager.templatesList()); props.setNotice(`模板已导入: ${value}`); } })}><Upload size={15} />导入模板</button></div><textarea ref={editorRef} className="code-editor" aria-label="脚本 JSON 编辑器" spellCheck={false} value={props.source} onSelect={(event) => { editorSelection.current = { start: event.currentTarget.selectionStart, end: event.currentTarget.selectionEnd }; }} onBlur={(event) => { editorSelection.current = { start: event.currentTarget.selectionStart, end: event.currentTarget.selectionEnd }; }} onChange={(event) => { editorSelection.current = { start: event.currentTarget.selectionStart, end: event.currentTarget.selectionEnd }; props.setSource(event.target.value); props.setNotice("正在编辑"); }} /></section>
      <aside className="inspector">
        <section className="panel script-device-panel"><div className="panel-title"><span>编辑设备</span><span className={`run-state ${props.running[debugTaskId] ? "live" : ""}`}>{props.running[debugTaskId] ? "调试中" : "待命"}</span></div><div><select aria-label="编辑设备" value={editorDevice} onChange={(event) => setEditorDevice(event.target.value)}><option value="">选择设备</option>{props.devices.map((device) => <option key={device}>{device}</option>)}</select><div className="picker-actions">{!props.running["click-picker"] ? <button className="button secondary" onClick={startPicker}><Crosshair size={15} />开始取点</button> : <button className="button danger" onClick={() => void window.bsManager.stopTask("click-picker")}><CircleStop size={15} />停止取点</button>}<button className="button quiet" disabled={!props.pickedCoordinates.length} onClick={() => props.setPickedCoordinates([])}><Trash2 size={14} />清空</button></div>{props.pickedCoordinates.length > 0 && <div className="coordinate-list">{props.pickedCoordinates.map((point, index) => <div key={`${point.capturedAt}-${index}`}><code>x={point.x}, y={point.y}</code><span><button className="icon-button" title="插入点击" onClick={() => append({ type: "click", x: point.x, y: point.y, remark: `点击(${point.x},${point.y})` })}><Plus size={14} /></button><button className="icon-button" title="设为摇杆中心" onClick={() => { setWheelCenterX(String(point.x)); setWheelBottomInset(String(Math.max(0, captureSize.height - point.y))); }}><Crosshair size={14} /></button><button className="icon-button" title="复制坐标" onClick={() => void navigator.clipboard.writeText(`x=${point.x}, y=${point.y}`)}><Copy size={14} /></button></span></div>)}</div>}</div></section>
        <section className="panel"><div className="panel-title"><span>运行变量</span><button className="icon-button" title="添加变量" onClick={() => setVariables([...variables, { name: "", value: "", note: "" }])}><Plus size={15} /></button></div><div className="variables">{variables.length === 0 && <p className="empty-note">此计划暂无变量。</p>}{variables.map((item, index) => <div className="variable-row" key={`${item.name}-${index}`}><input aria-label="变量名" value={item.name} placeholder="NAME" onChange={(event) => setVariables(variables.map((v, i) => i === index ? { ...v, name: event.target.value } : v))} /><input aria-label="变量值" value={item.value} placeholder="value" onChange={(event) => setVariables(variables.map((v, i) => i === index ? { ...v, value: event.target.value } : v))} /><input aria-label="变量备注" value={item.note} placeholder="备注" onChange={(event) => setVariables(variables.map((v, i) => i === index ? { ...v, note: event.target.value } : v))} /><button className="icon-button" title="删除变量" onClick={() => setVariables(variables.filter((_, i) => i !== index))}><Trash2 size={14} /></button></div>)}</div><button className="button secondary full" onClick={applyVariables}><Save size={15} />应用变量</button></section>
        <section className="panel template-panel"><div className="panel-title"><span>图像模板</span><span className="counter">{templates.length}</span></div><div className="template-list">{templates.slice(0, 12).map((name) => <button key={name} title={name} onClick={() => setQuickTemplate(name)} className={quickTemplate === name ? "selected-template" : ""}><SquareDashedMousePointer size={14} />{name}</button>)}</div></section>
        <section className="panel quick-actions-panel"><div className="panel-title"><span>快捷插入</span></div><label>坐标<div className="quick-grid"><input aria-label="快捷 X" value={quickX} onChange={(event) => setQuickX(event.target.value)} /><input aria-label="快捷 Y" value={quickY} onChange={(event) => setQuickY(event.target.value)} /></div></label><button className="button secondary" onClick={() => append({ type: "click", x: Math.round(numeric(quickX, 540)), y: Math.round(numeric(quickY, 960)), remark: `点击(${quickX},${quickY})` })}>插入 click</button><label>等待秒数<div className="quick-grid"><input aria-label="等待秒数" value={quickSeconds} onChange={(event) => setQuickSeconds(event.target.value)} /><button className="button secondary" onClick={() => append({ type: "wait", seconds: Math.max(0, numeric(quickSeconds, 1)), remark: `等待${quickSeconds}秒` })}>插入 wait</button></div></label><label>文字识别<input aria-label="识别文字" value={quickText} onChange={(event) => setQuickText(event.target.value)} /></label><div className="quick-grid"><input aria-label="识别语言" value={quickTextLang} onChange={(event) => setQuickTextLang(event.target.value)} /><input aria-label="文字识别超时秒数" value={quickTextTimeout} onChange={(event) => setQuickTextTimeout(event.target.value)} /></div><button className="button secondary" onClick={() => append({ type: "find_text_click", text: quickText.trim() || "START", match: "contains", lang: quickTextLang.trim() || "eng", timeout_sec: Math.max(0.1, numeric(quickTextTimeout, 8)), interval_sec: 0.8, remark: `识别文字${quickText.trim() || "START"}并点击` })}><ScanText size={15} />插入 find_text_click</button></section>
        <section className="panel quick-actions-panel"><div className="panel-title"><span>图像识别</span></div><select aria-label="图像模板" value={quickTemplate} onChange={(event) => setQuickTemplate(event.target.value)}><option value="">选择图像模板</option>{templates.map((name) => <option key={name}>{name}</option>)}</select><div className="quick-grid"><button className="button secondary" disabled={!editorDevice} onClick={() => void capture("template")}><ImagePlus size={15} />截图裁图</button><button className="button secondary" disabled={!editorDevice} onClick={() => void capture("region")}><SquareDashedMousePointer size={15} />选择区域</button></div><div className="image-region-summary"><span>区域</span><code>{region ? `x=${region.x}, y=${region.y}, ${region.width} x ${region.height}` : `全屏 ${captureSize.width} x ${captureSize.height}`}</code>{region && <button className="icon-button" title="恢复全屏" onClick={() => setRegion(null)}><X size={14} /></button>}</div><div className="quick-grid"><input aria-label="图像阈值" value={quickThreshold} onChange={(event) => setQuickThreshold(event.target.value)} /><input aria-label="图像超时秒数" value={quickImageTimeout} onChange={(event) => setQuickImageTimeout(event.target.value)} /></div><Toggle label="仅预览不点击" checked={previewOnly} onChange={setPreviewOnly} /><div className="quick-grid"><button className="button secondary" onClick={() => insertImage("find_image")}>插入 find_image</button><button className="button secondary" onClick={() => insertImage("find_image_click")}>插入 find_image_click</button></div><button className="button secondary" onClick={() => insertImage("if_image")}>插入 if_image</button></section>
        <section className="panel quick-actions-panel"><div className="panel-title"><span>摇杆轨迹</span></div><div className="quick-grid"><input aria-label="摇杆中心 X" value={wheelCenterX} onChange={(event) => setWheelCenterX(event.target.value)} /><input aria-label="距离底部" value={wheelBottomInset} onChange={(event) => setWheelBottomInset(event.target.value)} /></div><div className="quick-grid"><input aria-label="拖动秒数" value={wheelSeconds} onChange={(event) => setWheelSeconds(event.target.value)} /><input aria-label="拖动距离" value={wheelDistance} onChange={(event) => setWheelDistance(event.target.value)} /></div><div className="quick-grid"><input aria-label="拖动角度" value={wheelAngle} onChange={(event) => setWheelAngle(event.target.value)} /><button className="button secondary" onClick={() => insertDirection(numeric(wheelAngle, 0))}>插入角度拖动</button></div><div className="direction-buttons"><button className="icon-button" title="向左拖动" onClick={() => insertDirection(180)}><ChevronLeft size={18} /></button><button className="icon-button" title="向上拖动" onClick={() => insertDirection(90)}><ChevronUp size={18} /></button><button className="icon-button" title="向下拖动" onClick={() => insertDirection(-90)}><ChevronDown size={18} /></button><button className="icon-button" title="向右拖动" onClick={() => insertDirection(0)}><ChevronRight size={18} /></button><button className="button quiet" onClick={() => addTurn()}>加入多转向</button></div>{wheelTurns.length > 0 && <div className="wheel-turns">{wheelTurns.map((turn) => <div key={turn.id}><input aria-label="多转向角度" value={turn.angle} onChange={(event) => setWheelTurns((current) => current.map((item) => item.id === turn.id ? { ...item, angle: event.target.value } : item))} /><input aria-label="多转向秒数" value={turn.seconds} onChange={(event) => setWheelTurns((current) => current.map((item) => item.id === turn.id ? { ...item, seconds: event.target.value } : item))} /><button className="icon-button" title="删除步骤" onClick={() => setWheelTurns((current) => current.filter((item) => item.id !== turn.id))}><Trash2 size={14} /></button></div>)}<div className="quick-grid"><button className="button secondary" onClick={() => wheelTrace(wheelTurns.map((turn) => ({ angle: numeric(turn.angle, 0), seconds: Math.max(0.1, numeric(turn.seconds, 2)) })))}>插入多转向</button><button className="button quiet" onClick={() => setWheelTurns([])}>清空步骤</button></div></div>}</section>
      </aside>
    </div>
    {captureMode && captureImage && <ScreenshotSelector image={captureImage} mode={captureMode} onClose={() => setCaptureMode(null)} onSelect={(selection) => { if (captureMode === "template") void saveTemplate(selection); else setSearchRegion(selection); }} />}
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
  const canMultiRun = props.license?.tier === "pro" && props.license.valid;
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
    <PageHeading title={props.standalone ? "运行窗口" : "运行中心"} detail={props.standalone ? "此窗口的计划、设备、变量和日志均独立运行。" : "当前页面内置一个运行器；专业版可继续打开多个独立运行窗口并行执行。"}><button className="button secondary" onClick={() => void props.refreshDevices()}><RefreshCw size={16} />刷新设备</button>{!props.standalone && <button className="button primary" onClick={() => { if (!canMultiRun) { props.openLicenseActivation(); return; } void window.bsManager.openRunWindow(selection.plan).catch((error) => props.setNotice(`无法打开运行窗口: ${String(error)}`)); }}>{canMultiRun ? <PanelsTopLeft size={16} /> : <LockKeyhole size={16} />}{canMultiRun ? "多开运行" : "专业版多开"}</button>}</PageHeading>
    <div className="device-strip panel"><MonitorSmartphone size={18} /><strong>已发现 {props.devices.length} 个设备</strong><span>{props.devices.join("  ·  ") || "请检查 ADB 路径、模拟器或 USB 调试连接"}</span></div>
    <section className="runner-card panel"><div className="runner-header"><div><span className="eyebrow">自动化进程</span><h2>{props.standalone ? `运行窗口 ${runnerId.slice(-6)}` : "内置运行器"}</h2></div><span className={`run-state ${running ? "live" : ""}`}>{running ? "运行中" : "待命"}</span></div><div className="runner-fields"><label>计划<select value={selection.plan} onChange={(event) => updateSelection({ plan: event.target.value })}>{props.plans.map((name) => <option key={name}>{name}</option>)}</select></label><label>设备<input list={`runner-devices-${runnerId}`} value={selection.device} placeholder="使用计划默认设备" onChange={(event) => updateSelection({ device: event.target.value })} /><datalist id={`runner-devices-${runnerId}`}>{props.devices.map((name) => <option key={name} value={name} />)}</datalist></label></div><div className="runner-actions">{!running ? <button className="button primary" disabled={!selection.plan} onClick={start}><Play size={16} />启动</button> : <button className="button danger" onClick={() => void window.bsManager.stopTask(taskId)}><CircleStop size={16} />停止</button>}<button className="button quiet" onClick={() => props.clearTaskLog(taskId)}><Trash2 size={15} />清日志</button><button className="icon-button" title="复制日志" onClick={() => void navigator.clipboard.writeText(rawLog)}><Copy size={16} /></button></div><div className="runner-metrics"><span>循环 {cycles || "--"}</span><span>点击 {clicks}</span><span className={errors ? "metric-error" : ""}>错误 {errors}</span><label className="runner-profit">单次收益<input value={selection.profitPerCycle} onChange={(event) => updateSelection({ profitPerCycle: event.target.value })} /></label><span>预期 {expectedProfit}</span><Toggle label="实时输出" checked={selection.showRealtimeLogs} onChange={(showRealtimeLogs) => updateSelection({ showRealtimeLogs })} /></div><section className="runner-variables"><div className="panel-title"><span>运行变量</span><button className="button quiet" onClick={() => void saveVariables()}><Save size={15} />保存变量</button></div>{variables.length ? <div className="runner-variable-list">{variables.map((variable, index) => <div key={`${variable.name}-${index}`}><strong>{variable.name}</strong><input aria-label={`${variable.name} 的值`} value={variable.value} onChange={(event) => updateVariable(index, "value", event.target.value)} onBlur={() => void saveVariables(variables)} /><input aria-label={`${variable.name} 的备注`} value={variable.note} placeholder="备注" onChange={(event) => updateVariable(index, "note", event.target.value)} onBlur={() => void saveVariables(variables)} /></div>)}</div> : <p className="empty-note padded-note">此计划暂无运行变量。</p>}</section><pre className="log-output">{log || "等待运行日志..."}</pre></section>
  </div>;
}
