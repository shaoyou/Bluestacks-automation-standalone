import { useEffect, useState } from "react";
import { Activity, Check, CircleStop, Copy, Crosshair, FolderOpen, KeyRound, MonitorSmartphone, Radio, RefreshCw, Save, SlidersHorizontal, SquareDashedMousePointer, Trash2 } from "lucide-react";
import { LogPanel, PageHeading, Toggle } from "../components/layout";
import type { SharedProps } from "../app/shared";

export function RecorderPage(props: SharedProps) {
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
  return <div className="page"><PageHeading title="触摸录制" detail="从设备原始触摸事件生成可编辑、可循环的 JSON 计划。"><button className="button secondary" onClick={() => void props.refreshDevices()}><RefreshCw size={16} />刷新设备</button></PageHeading><div className="two-column"><section className="panel form-panel"><label>输出文件<input value={name} onChange={(event) => setName(event.target.value)} /></label><label>设备序列号<select value={device} onChange={(event) => setDevice(event.target.value)}><option value="">自动选择健康设备</option>{props.devices.map((item) => <option key={item}>{item}</option>)}</select></label><label>循环次数<input value={loop} onChange={(event) => setLoop(event.target.value)} /></label><Toggle label="清理明显误触点击" checked={cleanNoise} onChange={setCleanNoise} /><Toggle label="反转 X 轴" checked={invertX} onChange={setInvertX} /><Toggle label="反转 Y 轴" checked={invertY} onChange={setInvertY} /><Toggle label="交换 X/Y 轴" checked={swapXY} onChange={setSwapXY} />{!running ? <button className="button primary full" onClick={start}><Radio size={16} />开始录制</button> : <button className="button danger full" onClick={() => void window.bsManager.stopTask("recorder")}><CircleStop size={16} />停止并保存</button>}</section><LogPanel title="录制日志" text={props.logs.recorder ?? ""} /></div></div>;
}

export function CalibrationPage(props: SharedProps) {
  const [device, setDevice] = useState("");
  const [screen, setScreen] = useState("");
  const [image, setImage] = useState("");
  const [cropName, setCropName] = useState("captured_template");
  const [cropX, setCropX] = useState("0");
  const [cropY, setCropY] = useState("0");
  const [cropWidth, setCropWidth] = useState("1080");
  const [cropHeight, setCropHeight] = useState("1920");
  useEffect(() => { if (!device && props.devices.length === 1) setDevice(props.devices[0]); }, [device, props.devices]);
  const picking = !!props.running["click-picker"];
  const command = async (args: string[]) => { try { const output = await window.bsManager.adbRun(props.settings.adbPath, device ? ["-s", device, ...args] : args); setScreen(output.text || `exit code ${output.code}`); if (output.code !== 0) props.setNotice(`ADB 命令失败，退出码 ${output.code}`); } catch (error) { setScreen(String(error)); props.setNotice(`ADB 命令失败: ${String(error)}`); } };
  const capture = async () => { if (!device) return props.setNotice("请填写目标设备序列号"); try { setImage(await window.bsManager.screenshot(props.settings.adbPath, device)); } catch (error) { setScreen(String(error)); } };
  const saveCrop = async () => {
    if (!image) return;
    const source = new Image(); source.src = image;
    await new Promise<void>((resolve, reject) => { source.onload = () => resolve(); source.onerror = () => reject(new Error("截图加载失败")); });
    const x = Math.max(0, Math.min(source.naturalWidth - 1, Math.round(Number(cropX) || 0)));
    const y = Math.max(0, Math.min(source.naturalHeight - 1, Math.round(Number(cropY) || 0)));
    const width = Math.max(1, Math.min(source.naturalWidth - x, Math.round(Number(cropWidth) || source.naturalWidth)));
    const height = Math.max(1, Math.min(source.naturalHeight - y, Math.round(Number(cropHeight) || source.naturalHeight)));
    const canvas = document.createElement("canvas"); canvas.width = width; canvas.height = height;
    const context = canvas.getContext("2d"); if (!context) throw new Error("无法创建图片画布");
    context.drawImage(source, x, y, width, height, 0, 0, width, height);
    const output = await window.bsManager.templatesSaveCapture(cropName, canvas.toDataURL("image/png"));
    props.setNotice(`已保存模板: ${output}`);
  };
  const startPicking = () => { if (!props.runtime || !device) return props.setNotice("请先刷新并选择设备"); props.setPickedCoordinates([]); void props.startTask("click-picker", [`${props.runtime.root}/record_touch.py`, "--output", `${props.runtime.root}/diagnostics/click_picker.json`, "--adb", props.settings.adbPath, "--device", device, "--print-clicks-only"]); };
  const pickFromScreenshot = (event: React.MouseEvent<HTMLImageElement>) => { const target = event.currentTarget; if (!target.naturalWidth || !target.naturalHeight) return; const bounds = target.getBoundingClientRect(); const x = Math.max(0, Math.min(target.naturalWidth - 1, Math.round((event.clientX - bounds.left) * target.naturalWidth / bounds.width))); const y = Math.max(0, Math.min(target.naturalHeight - 1, Math.round((event.clientY - bounds.top) * target.naturalHeight / bounds.height))); props.setPickedCoordinates([{ x, y, device, capturedAt: new Date().toISOString() }, ...props.pickedCoordinates].slice(0, 10)); props.setNotice(`已从截图取点: (${x}, ${y})`); };
  return <div className="page"><PageHeading title="设备标定" detail="检查连接、屏幕尺寸，发送点击/滑动并抓取当前画面。"><button className="button secondary" onClick={() => void props.refreshDevices()}><RefreshCw size={16} />刷新设备</button></PageHeading><div className="calibration-grid"><section className="panel form-panel"><label>设备序列号<select value={device} onChange={(event) => setDevice(event.target.value)}><option value="">使用默认设备</option>{props.devices.map((item) => <option key={item}>{item}</option>)}</select></label><div className="button-grid"><button className="button secondary" onClick={() => void command(["get-state"])}>Ping</button><button className="button secondary" onClick={() => void command(["shell", "wm", "size"])}>屏幕尺寸</button><button className="button secondary" onClick={() => void command(["shell", "input", "tap", "500", "500"])}>测试点击</button><button className="button secondary" onClick={() => void command(["shell", "input", "swipe", "300", "600", "800", "600", "450"])}>测试滑动</button></div><div className="picker-actions">{!picking ? <button className="button secondary" onClick={startPicking}><Crosshair size={16} />开始取点</button> : <button className="button danger" onClick={() => void window.bsManager.stopTask("click-picker")}><CircleStop size={16} />停止取点</button>}<button className="button quiet" disabled={props.pickedCoordinates.length === 0} onClick={() => props.setPickedCoordinates([])}><Trash2 size={15} />清空坐标</button></div>{props.pickedCoordinates.length > 0 && <div className="coordinate-list">{props.pickedCoordinates.map((point, index) => <div key={`${point.capturedAt}-${index}`}><code>x={point.x}, y={point.y}</code><button className="button quiet" onClick={() => props.insertAction({ type: "click", x: point.x, y: point.y, remark: `取点 (${point.x}, ${point.y})` })}>插入 click</button></div>)}</div>}<button className="button primary full" onClick={capture}><MonitorSmartphone size={16} />抓取屏幕</button>{image && <div className="crop-tools"><label>模板名<input value={cropName} onChange={(event) => setCropName(event.target.value)} /></label><div className="crop-grid"><input aria-label="裁剪 X" value={cropX} onChange={(event) => setCropX(event.target.value)} /><input aria-label="裁剪 Y" value={cropY} onChange={(event) => setCropY(event.target.value)} /><input aria-label="裁剪宽度" value={cropWidth} onChange={(event) => setCropWidth(event.target.value)} /><input aria-label="裁剪高度" value={cropHeight} onChange={(event) => setCropHeight(event.target.value)} /></div><button className="button secondary full" onClick={() => void saveCrop().catch((error) => props.setNotice(`保存模板失败: ${String(error)}`))}><SquareDashedMousePointer size={16} />裁剪并保存模板</button></div>}<pre className="command-result">{screen || "命令输出会显示在这里。"}</pre>{(picking || props.logs["click-picker"]) && <pre className="command-result picker-log">{props.logs["click-picker"] || "正在等待触摸事件..."}</pre>}</section><section className="panel screenshot-panel">{image ? <img src={image} alt="设备当前屏幕" title="点击截图取点" onClick={pickFromScreenshot} /> : <div className="screen-placeholder"><MonitorSmartphone size={42} /><p>抓取的设备屏幕会显示在这里</p></div>}</section></div></div>;
}

export function DiagnosticsPage(props: SharedProps) {
  const [device, setDevice] = useState("");
  const running = !!props.running.diagnostic;
  const start = () => { if (!props.runtime) return; const normalized = (device || "default").replace(/[^a-zA-Z0-9._-]/g, "_"); const args = [`${props.runtime.root}/record_touch.py`, "--output", `${props.runtime.root}/diagnostics/${normalized}_recording_diagnostic.json`, "--adb", props.settings.adbPath, "--diagnose-self-heal"]; if (device) args.push("--device", device, "--profile", `${props.runtime.root}/recording_profiles/${normalized}.json`); void props.startTask("diagnostic", args); };
  return <div className="page"><PageHeading title="录制诊断" detail="采集触摸映射样本，生成诊断报告，并在可靠时保存改进后的 profile。"><button className="button secondary" onClick={() => void props.refreshDevices()}><RefreshCw size={16} />刷新设备</button></PageHeading><div className="two-column"><section className="panel form-panel"><label>设备序列号<select value={device} onChange={(event) => setDevice(event.target.value)}><option value="">自动选择健康设备</option>{props.devices.map((item) => <option key={item}>{item}</option>)}</select></label><div className="diagnostic-note"><SlidersHorizontal size={18} /><p>诊断过程会执行映射校验；请确保设备屏幕可见且不要在过程中切换模拟器窗口。</p></div>{!running ? <button className="button primary full" onClick={start}><Activity size={16} />开始诊断</button> : <button className="button danger full" onClick={() => void window.bsManager.stopTask("diagnostic")}><CircleStop size={16} />停止诊断</button>}</section><LogPanel title="诊断输出" text={props.logs.diagnostic ?? ""} /></div></div>;
}

export function SettingsPage(props: SharedProps) {
  const save = async () => { const saved = await window.bsManager.settingsSave(props.settings); props.setSettings(saved); props.setNotice("设置已保存"); };
  const [code, setCode] = useState("");
  const [copied, setCopied] = useState(false);
  const license = props.license;
  const isPro = license?.tier === "pro" && license.valid;
  const copyInstallId = async () => {
    if (!license?.installId) return;
    try {
      await navigator.clipboard.writeText(license.installId);
      setCopied(true);
      props.setNotice("安装 ID 已复制");
      window.setTimeout(() => setCopied(false), 1800);
    } catch (error) {
      props.setNotice(`复制安装 ID 失败: ${String(error)}`);
    }
  };
  return <div className="page"><PageHeading title="环境设置" detail="配置跨平台运行时使用的 ADB 与 Python 可执行文件。" /><div className="settings-stack"><section className="panel form-panel"><label>ADB 可执行文件<input value={props.settings.adbPath} onChange={(event) => props.setSettings({ ...props.settings, adbPath: event.target.value })} /></label><label>Python 可执行文件<input value={props.settings.pythonPath} onChange={(event) => props.setSettings({ ...props.settings, pythonPath: event.target.value })} /></label><p className="field-note">Windows 可以填 `adb.exe` / `python.exe` 或完整路径；macOS 可填 `adb` / `python3`。</p><button className="button primary" onClick={() => void save()}><Save size={16} />保存设置</button></section><section className="panel form-panel"><div className="panel-title"><span>专业版授权</span><span className={`run-state ${isPro ? "live" : ""}`}>{isPro ? "专业版" : "免费版"}</span></div><p className="field-note">{license?.message ?? "正在读取授权状态..."}</p><label>安装 ID<div className="inline-input-action"><input readOnly value={license?.installId ?? ""} /><button className="icon-button" title="复制安装 ID" disabled={!license?.installId} onClick={() => void copyInstallId()}>{copied ? <Check size={15} /> : <Copy size={15} />}</button></div>{copied && <small className="copy-success">已复制</small>}</label>{!isPro ? <><label>激活码<textarea className="license-code-input" value={code} placeholder="粘贴专业版激活码" onChange={(event) => setCode(event.target.value)} /></label><button className="button primary" disabled={!code.trim()} onClick={() => void props.activateLicense(code)}><KeyRound size={16} />激活专业版</button></> : <><p className="field-note">最多可同时运行 {license?.maxConcurrentRunners ?? 3} 个自动化任务。</p><button className="button quiet danger" onClick={() => void props.clearLicense()}><Trash2 size={15} />移除本机授权</button></>}</section><section className="panel path-panel"><span className="eyebrow">用户运行目录</span><code>{props.runtime?.root ?? "加载中..."}</code><p>脚本、模板、录制配置和诊断数据都存放在这里；应用升级不会覆盖用户创建的计划文件。</p><button className="button secondary" onClick={() => void window.bsManager.templatesList().then(() => props.setNotice("模板目录可通过系统文件管理器查看"))}><FolderOpen size={16} />检查模板</button></section></div></div>;
}
