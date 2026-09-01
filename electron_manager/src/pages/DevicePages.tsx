import { useEffect, useState } from "react";
import { Activity, Check, CircleStop, Copy, Crosshair, Download, FolderOpen, KeyRound, MonitorSmartphone, Radio, RefreshCw, Save, SlidersHorizontal, SquareDashedMousePointer, Trash2 } from "lucide-react";
import { LogPanel, PageHeading, Toggle } from "../components/layout";
import type { SharedProps } from "../app/shared";

const hdcDeviceSuffix = " [HarmonyOS/HDC]";

function splitDeviceBackend(device: string): { backend: "adb" | "hdc"; target: string } {
  const value = device.trim();
  if (value.endsWith(hdcDeviceSuffix)) return { backend: "hdc", target: value.slice(0, -hdcDeviceSuffix.length).trim() };
  if (value.startsWith("hdc:")) return { backend: "hdc", target: value.slice(4).trim() };
  return { backend: "adb", target: value };
}

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
  const command = async (args: string[]) => { try { const selected = splitDeviceBackend(device); const commandArgs = selected.backend === "hdc" ? ["-t", selected.target, ...args] : device ? ["-s", selected.target, ...args] : args; const output = await window.bsManager.adbRun({ adbPath: props.settings.adbPath, hdcPath: props.settings.hdcPath }, commandArgs, selected.backend); setScreen(output.text || `exit code ${output.code}`); if (output.code !== 0) props.setNotice(`设备命令失败，退出码 ${output.code}`); } catch (error) { setScreen(String(error)); props.setNotice(`设备命令失败: ${String(error)}`); } };
  const capture = async () => { if (!device) return props.setNotice("请填写目标设备序列号"); try { setImage(await window.bsManager.screenshot({ adbPath: props.settings.adbPath, hdcPath: props.settings.hdcPath }, device)); } catch (error) { setScreen(String(error)); } };
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
  return <div className="page"><PageHeading title="设备标定" detail="检查连接、屏幕尺寸，发送点击/滑动并抓取当前画面。"><button className="button secondary" onClick={() => void props.refreshDevices()}><RefreshCw size={16} />刷新设备</button></PageHeading><div className="calibration-grid"><section className="panel form-panel"><label>设备序列号<select value={device} onChange={(event) => setDevice(event.target.value)}><option value="">使用默认设备</option>{props.devices.map((item) => <option key={item}>{item}</option>)}</select></label><div className="button-grid"><button className="button secondary" onClick={() => void command(["get-state"])}>Ping</button><button className="button secondary" onClick={() => void command(["shell", "wm", "size"])}>屏幕尺寸</button><button className="button secondary" onClick={() => void command(["shell", "input", "tap", "500", "500"])}>测试点击</button><button className="button secondary" onClick={() => void command(["shell", "input", "swipe", "300", "600", "800", "600", "450"])}>测试滑动</button></div><div className="picker-actions">{!picking ? <button className="button secondary" onClick={startPicking}><Crosshair size={16} />开始取点</button> : <button className="button danger" onClick={() => void window.bsManager.stopTask("click-picker")}><CircleStop size={16} />停止取点</button>}<button className="button quiet" disabled={props.pickedCoordinates.length === 0} onClick={() => props.setPickedCoordinates([])}><Trash2 size={15} />清空坐标</button></div>{props.pickedCoordinates.length > 0 && <div className="coordinate-list">{props.pickedCoordinates.map((point, index) => <div key={`${point.capturedAt}-${index}`}><code>x={point.x}, y={point.y}</code><button className="button quiet" onClick={() => props.insertAction({ type: "click", x: point.x, y: point.y, remark: `取点 (${point.x}, ${point.y})` })}>插入 click</button></div>)}</div>}<button className="button primary full" onClick={capture}><MonitorSmartphone size={16} />抓取屏幕</button>{image && <div className="crop-tools"><label>模板名<input value={cropName} onChange={(event) => setCropName(event.target.value)} /></label><div className="crop-grid"><input aria-label="裁剪 X" value={cropX} onChange={(event) => setCropX(event.target.value)} /><input aria-label="裁剪 Y" value={cropY} onChange={(event) => setCropY(event.target.value)} /><input aria-label="裁剪宽度" value={cropWidth} onChange={(event) => setCropWidth(event.target.value)} /><input aria-label="裁剪高度" value={cropHeight} onChange={(event) => setCropHeight(event.target.value)} /></div><div className="crop-actions"><button className="button secondary" onClick={() => void saveCrop().catch((error) => props.setNotice(`保存模板失败: ${String(error)}`))}><SquareDashedMousePointer size={16} />裁剪并保存</button><button className="button secondary" onClick={() => void window.bsManager.templatesOpenFolder().then(() => props.setNotice("已打开模板目录")).catch((error) => props.setNotice(`打开模板目录失败: ${String(error)}`))}><FolderOpen size={16} />查看模版</button></div></div>}<pre className="command-result">{screen || "命令输出会显示在这里。"}</pre>{(picking || props.logs["click-picker"]) && <pre className="command-result picker-log">{props.logs["click-picker"] || "正在等待触摸事件..."}</pre>}</section><section className="panel screenshot-panel">{image ? <img src={image} alt="设备当前屏幕" title="点击截图取点" onClick={pickFromScreenshot} /> : <div className="screen-placeholder"><MonitorSmartphone size={42} /><p>抓取的设备屏幕会显示在这里</p></div>}</section></div></div>;
}

export function DiagnosticsPage(props: SharedProps) {
  const [device, setDevice] = useState("");
  const [connectTarget, setConnectTarget] = useState("127.0.0.1:5555");
  const [hdcTarget, setHdcTarget] = useState("");
  const touchTaskId = "diagnostic";
  const discoveryTaskId = "diagnostic-device-discovery";
  const hdcDiscoveryTaskId = "diagnostic-hdc-device-discovery";
  const touchRunning = !!props.running[touchTaskId];
  const discoveryRunning = !!props.running[discoveryTaskId];
  const hdcDiscoveryRunning = !!props.running[hdcDiscoveryTaskId];

  const copyDiagnosticLog = async (taskId: string) => {
    const text = props.logs[taskId] ?? "";
    if (!text) return props.setNotice("当前没有可复制的诊断日志");
    try {
      await navigator.clipboard.writeText(text);
      props.setNotice("已复制");
    } catch (error) {
      props.setNotice(`复制诊断日志失败: ${String(error)}`);
    }
  };

  useEffect(() => {
    if (!device && props.devices.length === 1) setDevice(props.devices[0]);
  }, [device, props.devices]);

  useEffect(() => {
    if (hdcTarget) return;
    const firstHdc = props.devices.find((item) => item.endsWith(" [HarmonyOS/HDC]"));
    if (firstHdc) setHdcTarget(firstHdc);
  }, [hdcTarget, props.devices]);

  const startTouchDiagnostic = () => {
    if (!props.runtime) return;
    const normalized = (device || "default").replace(/[^a-zA-Z0-9._-]/g, "_");
    const args = [`${props.runtime.root}/record_touch.py`, "--output", `${props.runtime.root}/diagnostics/${normalized}_recording_diagnostic.json`, "--adb", props.settings.adbPath, "--diagnose-self-heal"];
    if (device) args.push("--device", device, "--profile", `${props.runtime.root}/recording_profiles/${normalized}.json`);
    void props.startTask(touchTaskId, args);
  };

  const startDiscoveryDiagnostic = () => {
    if (!props.runtime) return;
    const args = [
      `${props.runtime.root}/device_discovery_diagnostic.py`,
      "--adb",
      props.settings.adbPath,
      "--report-dir",
      `${props.runtime.root}/diagnostics/device_discovery`,
    ];
    if (connectTarget.trim()) args.push("--connect-target", connectTarget.trim());
    if (device) args.push("--device", device);
    void props.startTask(discoveryTaskId, args);
  };

  const startHdcDiscoveryDiagnostic = () => {
    if (!props.runtime) return;
    const args = [
      `${props.runtime.root}/hdc_device_diagnostic.py`,
      "--hdc",
      props.settings.hdcPath,
      "--report-dir",
      `${props.runtime.root}/diagnostics/hdc_device_discovery`,
    ];
    if (hdcTarget.trim()) args.push("--device", hdcTarget.trim());
    void props.startTask(hdcDiscoveryTaskId, args);
  };

  return <div className="page"><PageHeading title="设备诊断" detail="分别检查安卓设备发现、鸿蒙设备发现和设备连接链路。"><button className="button secondary" onClick={() => void props.refreshDevices()}><RefreshCw size={16} />刷新设备</button></PageHeading><div className="diagnostic-stack"><div className="two-column diagnostic-android"><section className="panel form-panel"><label>ADB 连接目标<input value={connectTarget} onChange={(event) => setConnectTarget(event.target.value)} placeholder="127.0.0.1:5555" /></label><label>目标设备序列号<select value={device} onChange={(event) => setDevice(event.target.value)}><option value="">优先自动发现</option>{props.devices.map((item) => <option key={item}>{item}</option>)}</select></label><div className="diagnostic-note"><SlidersHorizontal size={18} /><p>检查 Android ADB 版本、设备列表、连接尝试和 shell 探测结果。</p></div>{!discoveryRunning ? <button className="button primary full" onClick={startDiscoveryDiagnostic}><Activity size={16} />开始安卓设备发现诊断</button> : <button className="button danger full" onClick={() => void window.bsManager.stopTask(discoveryTaskId)}><CircleStop size={16} />停止诊断</button>}</section><LogPanel title="安卓设备发现诊断输出" text={props.logs[discoveryTaskId] ?? ""} actions={<button className="icon-button" title="复制日志" aria-label="复制日志" disabled={!props.logs[discoveryTaskId]} onClick={() => void copyDiagnosticLog(discoveryTaskId)}><Copy size={15} /></button>} /></div><div className="two-column diagnostic-harmony"><section className="panel form-panel"><label>HDC 目标<input value={hdcTarget} onChange={(event) => setHdcTarget(event.target.value)} placeholder="12345 [HarmonyOS/HDC] 或 hdc:12345" /></label><div className="diagnostic-note"><SlidersHorizontal size={18} /><p>检查鸿蒙 HDC targets、目标探活和屏幕尺寸。</p></div>{!hdcDiscoveryRunning ? <button className="button primary full" onClick={startHdcDiscoveryDiagnostic}><Activity size={16} />开始鸿蒙设备发现诊断</button> : <button className="button danger full" onClick={() => void window.bsManager.stopTask(hdcDiscoveryTaskId)}><CircleStop size={16} />停止诊断</button>}</section><LogPanel title="鸿蒙设备发现诊断输出" text={props.logs[hdcDiscoveryTaskId] ?? ""} actions={<button className="icon-button" title="复制日志" aria-label="复制日志" disabled={!props.logs[hdcDiscoveryTaskId]} onClick={() => void copyDiagnosticLog(hdcDiscoveryTaskId)}><Copy size={15} /></button>} /></div><div className="two-column diagnostic-connection"><section className="panel form-panel"><label>设备序列号<select value={device} onChange={(event) => setDevice(event.target.value)}><option value="">自动选择健康设备</option>{props.devices.map((item) => <option key={item}>{item}</option>)}</select></label><div className="diagnostic-note"><SlidersHorizontal size={18} /><p>检查触摸映射、输入事件和设备连接稳定性。</p></div>{!touchRunning ? <button className="button primary full" onClick={startTouchDiagnostic}><Activity size={16} />开始设备连接诊断</button> : <button className="button danger full" onClick={() => void window.bsManager.stopTask(touchTaskId)}><CircleStop size={16} />停止诊断</button>}</section><LogPanel title="设备连接诊断输出" text={props.logs[touchTaskId] ?? ""} actions={<button className="icon-button" title="复制日志" aria-label="复制日志" disabled={!props.logs[touchTaskId]} onClick={() => void copyDiagnosticLog(touchTaskId)}><Copy size={15} /></button>} /></div></div></div>;
}

export function SettingsPage(props: SharedProps) {
  const save = async () => { const saved = await window.bsManager.settingsSave(props.settings); props.setSettings(saved); props.setNotice("设置已保存"); };
  const [code, setCode] = useState("");
  const [copied, setCopied] = useState(false);
  const [refreshingDevices, setRefreshingDevices] = useState(false);
  const [hdcConfigBusy, setHdcConfigBusy] = useState(false);
  const license = props.license;
  const isPro = license?.tier === "pro" && license.valid;
  const update = props.update;
  const updateBusy = update?.phase === "checking" || update?.phase === "downloading";
  const migrateHistory = async () => {
    try {
      const result = await window.bsManager.historyMigrate();
      props.setNotice(`历史数据已迁移到本地数据库：宝箱 ${result.chest ?? 0}，抽卡会话 ${result.drawSessions ?? 0}`);
    } catch (error) {
      props.setNotice(`历史数据迁移失败: ${String(error)}`);
    }
  };
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
  const forceRefresh = async () => {
    if (refreshingDevices) return;
    setRefreshingDevices(true);
    try {
      const devices = await props.forceRefreshDevices(true);
      window.setTimeout(() => {
        props.setNotice(devices.length ? `发现 ${devices.length} 个设备` : "未发现可用设备");
        setRefreshingDevices(false);
      }, 2000);
    } catch (error) {
      window.setTimeout(() => {
        props.setNotice(`设备强制刷新失败: ${String(error)}`);
        setRefreshingDevices(false);
      }, 2000);
    }
  };
  const chooseHdcDirectory = async () => {
    try {
      const selected = await window.bsManager.hdcChooseToolsDirectory();
      if (!selected) return;
      props.setSettings({ ...props.settings, hdcToolsDir: selected });
      props.setNotice(`已选择目录: ${selected}`);
    } catch (error) {
      props.setNotice(`选择目录失败: ${String(error)}`);
    }
  };
  const configureHdc = async () => {
    if (hdcConfigBusy) return;
    setHdcConfigBusy(true);
    try {
      const directory = props.settings.hdcToolsDir.trim();
      if (!directory) {
        props.setNotice("请先选择 Command Line Tools 目录");
        return;
      }
      const saved = await window.bsManager.hdcConfigureTools(directory);
      props.setSettings(saved);
      props.setNotice(`已配置 HDC: ${saved.hdcPath}`);
    } catch (error) {
      props.setNotice(`HDC 配置失败: ${String(error)}`);
    } finally {
      setHdcConfigBusy(false);
    }
  };
  return <div className="page"><PageHeading title="环境设置" detail="配置跨平台运行时使用的 ADB、HDC 与 Python 可执行文件。" /><div className="settings-stack"><section className="panel form-panel"><label>ADB 可执行文件<input value={props.settings.adbPath} onChange={(event) => props.setSettings({ ...props.settings, adbPath: event.target.value })} /></label><label>HDC 工具目录<div className="inline-input-action"><input value={props.settings.hdcToolsDir} onChange={(event) => props.setSettings({ ...props.settings, hdcToolsDir: event.target.value })} placeholder="选择 command-line-tools 目录" /><button className="icon-button" title="选择目录" onClick={() => void chooseHdcDirectory()}><FolderOpen size={15} /></button></div></label><label>HDC 可执行文件<input value={props.settings.hdcPath} onChange={(event) => props.setSettings({ ...props.settings, hdcPath: event.target.value })} /></label><label>Python 可执行文件<input value={props.settings.pythonPath} onChange={(event) => props.setSettings({ ...props.settings, pythonPath: event.target.value })} /></label><p className="field-note">选择 `command-line-tools` 目录后，应用会自动定位 `sdk/default/openharmony/toolchains/hdc.exe` 或 `hdc`，再写入可执行文件路径。</p><div className="button-grid"><button className="button primary" onClick={() => void save()}><Save size={16} />保存设置</button><button className="button secondary" disabled={hdcConfigBusy || !props.settings.hdcToolsDir.trim()} onClick={() => void configureHdc()}><FolderOpen size={16} />{hdcConfigBusy ? "配置中" : "一键配置 HDC"}</button><button className="button secondary" disabled={refreshingDevices} onClick={() => void forceRefresh()}><RefreshCw className={refreshingDevices ? "spin" : ""} size={16} />{refreshingDevices ? "刷新中" : "强制刷新设备"}</button></div></section><section className="panel form-panel"><div className="panel-title"><span>历史数据</span></div><p className="field-note">将现有开宝箱和抽卡记录写入本地 SQLite 数据库。截图仅作为可选关联资源，清理截图不会删除历史记录和统计。</p><button className="button secondary" onClick={() => void migrateHistory()}><Save size={16} />迁移历史数据</button></section><section className="panel form-panel"><div className="panel-title"><span>应用更新</span><span className={`run-state ${update?.phase === "available" || update?.phase === "downloaded" ? "live" : ""}`}>{update?.currentVersion ? `v${update.currentVersion}` : "读取中"}</span></div><p className="field-note">{update?.message ?? "正在读取更新状态..."}</p>{update?.releaseNotes && <pre className="command-result">{update.releaseNotes}</pre>}{update?.phase === "downloading" && <div className="progress-track"><i style={{ width: `${update.progress ?? 0}%` }} /></div>}<div className="button-grid">{update?.phase === "available" && <button className="button primary" onClick={() => void props.downloadUpdate()}><Download size={16} />下载 {update.version ? `v${update.version}` : "更新"}</button>}{update?.phase === "downloaded" && <button className="button primary" onClick={() => void props.installUpdate()}><RefreshCw size={16} />重启安装</button>}<button className="button secondary" disabled={!update?.supported || updateBusy} onClick={() => void props.checkForUpdates()}><RefreshCw size={16} />{updateBusy ? "处理中" : "检查更新"}</button></div></section><section className="panel form-panel"><div className="panel-title"><span>专业版授权</span><span className={`run-state ${isPro ? "live" : ""}`}>{isPro ? "专业版" : "免费版"}</span></div><p className="field-note">{license?.message ?? "正在读取授权状态..."}</p><label>安装 ID<div className="inline-input-action"><input readOnly value={license?.installId ?? ""} /><button className="icon-button" title="复制安装 ID" disabled={!license?.installId} onClick={() => void copyInstallId()}>{copied ? <Check size={15} /> : <Copy size={15} />}</button></div>{copied && <small className="copy-success">已复制</small>}</label>{!isPro ? <><label>激活码<textarea className="license-code-input" value={code} placeholder="粘贴专业版激活码" onChange={(event) => setCode(event.target.value)} /></label><button className="button primary" disabled={!code.trim()} onClick={() => void props.activateLicense(code)}><KeyRound size={16} />激活专业版</button></> : <><p className="field-note">最多可同时运行 {license?.maxConcurrentRunners ?? 3} 个自动化任务。</p><button className="button quiet danger" onClick={() => void props.clearLicense()}><Trash2 size={15} />移除本机授权</button></>}</section><section className="panel path-panel"><span className="eyebrow">用户运行目录</span><code>{props.runtime?.root ?? "加载中..."}</code><p>脚本、模板、录制配置和诊断数据都存放在这里；应用升级不会覆盖用户创建的计划文件。</p><button className="button secondary" onClick={() => void window.bsManager.templatesList().then(() => props.setNotice("模板目录可通过系统文件管理器查看"))}><FolderOpen size={16} />检查模板</button></section></div></div>;
}
