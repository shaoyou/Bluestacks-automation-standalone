import { useEffect, useRef, useState } from "react";
import { BarChart3, CircleStop, FolderOpen, Play, RefreshCw, Settings2, UserRound, X } from "lucide-react";
import { LogActions, LogPanel, PageHeading } from "../components/layout";
import type { SharedProps } from "../app/shared";

const drawDeviceStorageKey = "draw-selected-device";
const drawUserStorageKey = "draw-selected-user";
const drawRealtimeLogsStorageKey = "draw-show-realtime-logs";
const defaultRedRoles = ["波斯王子", "卡卡西", "李白", "龙三", "绿巨人", "圣骑士", "我爱罗", "蜘蛛"];
type User = { id: string; name: string; createdAt: string };
type Range = "day" | "7d" | "month" | "custom";
type ImageRegion = { x: number; y: number; width: number; height: number };
type DrawTemplateConfig = { template: string; region: ImageRegion; preview?: string };
type DrawTemplateKey = "cancel" | "max" | "coin";
type DrawCalibrationData = { configs: Record<DrawTemplateKey, DrawTemplateConfig>; images: Record<DrawTemplateKey, string | null> };
const defaultDrawTemplates: Record<DrawTemplateKey, DrawTemplateConfig> = {
  cancel: { template: "role_cancel.png", region: { x: 465, y: 1542, width: 148, height: 72 } },
  max: { template: "role_count_max.png", region: { x: 636, y: 1258, width: 334, height: 140 } },
  coin: { template: "role_done.png", region: { x: 337, y: 1380, width: 386, height: 149 } },
};

function scaleRegion(region: ImageRegion, from: ImageRegion, to: ImageRegion): ImageRegion {
  return { x: Math.round(region.x * to.width / from.width), y: Math.round(region.y * to.height / from.height), width: Math.max(1, Math.round(region.width * to.width / from.width)), height: Math.max(1, Math.round(region.height * to.height / from.height)) };
}

function CropPanel({ image, size, region, onChange }: { image: string; size: ImageRegion; region: ImageRegion; onChange: (region: ImageRegion) => void }) {
  const imageRef = useRef<HTMLImageElement>(null);
  const dragStart = useRef<{ x: number; y: number } | null>(null);
  const point = (event: React.MouseEvent<HTMLImageElement>) => {
    const element = imageRef.current;
    if (!element?.naturalWidth) return null;
    const bounds = element.getBoundingClientRect();
    return { x: Math.max(0, Math.min(element.naturalWidth, Math.round((event.clientX - bounds.left) * element.naturalWidth / bounds.width))), y: Math.max(0, Math.min(element.naturalHeight, Math.round((event.clientY - bounds.top) * element.naturalHeight / bounds.height))) };
  };
  const move = (event: React.MouseEvent<HTMLImageElement>) => { const current = point(event); const start = dragStart.current; if (!current || !start) return; onChange({ x: Math.min(start.x, current.x), y: Math.min(start.y, current.y), width: Math.abs(current.x - start.x), height: Math.abs(current.y - start.y) }); };
  return <div className="draw-calibration-crop"><div className="capture-image-wrap"><img ref={imageRef} src={image} alt="设备截图" draggable={false} onMouseDown={(event) => { const start = point(event); if (start) { dragStart.current = start; onChange({ x: start.x, y: start.y, width: 0, height: 0 }); } }} onMouseMove={move} onMouseUp={(event) => { move(event); dragStart.current = null; }} /><i className="capture-selection" style={{ left: `${region.x / size.width * 100}%`, top: `${region.y / size.height * 100}%`, width: `${region.width / size.width * 100}%`, height: `${region.height / size.height * 100}%` }} /></div><code>{`x=${region.x}, y=${region.y}, ${region.width} x ${region.height}`}</code></div>;
}

function TemplateCalibrationModal({ image, size, configs, templateImages, captureLatest, onClose, onSave, onReset }: { image: string; size: { width: number; height: number }; configs: Record<DrawTemplateKey, DrawTemplateConfig>; templateImages: Record<DrawTemplateKey, string | null>; captureLatest: () => Promise<{ image: string; size: { width: number; height: number } }>; onClose: () => void; onSave: (configs: Record<DrawTemplateKey, DrawTemplateConfig>, size: { width: number; height: number }) => void; onReset: () => void }) {
  const base = { width: 1080, height: 1920 };
  const initialDrafts = Object.fromEntries((Object.keys(configs) as DrawTemplateKey[]).map((key) => [key, { ...configs[key], region: scaleRegion(configs[key].region, { x: 0, y: 0, ...base }, { x: 0, y: 0, ...size }) }])) as Record<DrawTemplateKey, DrawTemplateConfig>;
  const [editing, setEditing] = useState<DrawTemplateKey | null>(null);
  const [drafts, setDrafts] = useState(initialDrafts);
  const [confirmed, setConfirmed] = useState<Record<DrawTemplateKey, boolean>>({ cancel: false, max: false, coin: false });
  const [workingRegion, setWorkingRegion] = useState<ImageRegion | null>(null);
  const [currentImage, setCurrentImage] = useState(image);
  const [currentSize, setCurrentSize] = useState(size);
  const [preview, setPreview] = useState<Record<DrawTemplateKey, string | null>>(templateImages);
  const begin = async (key: DrawTemplateKey) => {
    try {
      const latest = await captureLatest();
      setCurrentImage(latest.image);
      setCurrentSize(latest.size);
      setEditing(key);
      setWorkingRegion(scaleRegion(drafts[key].region, { x: 0, y: 0, ...currentSize }, { x: 0, y: 0, ...latest.size }));
    } catch { /* The parent reports the capture error. */ }
  };
  const confirm = async () => {
    if (!editing || !workingRegion || workingRegion.width < 1 || workingRegion.height < 1) return;
    const source = new Image(); source.src = currentImage;
    await new Promise<void>((resolve, reject) => { source.onload = () => resolve(); source.onerror = () => reject(new Error("截图加载失败")); });
    const canvas = document.createElement("canvas"); canvas.width = workingRegion.width; canvas.height = workingRegion.height;
    const context = canvas.getContext("2d"); if (!context) return;
    context.drawImage(source, workingRegion.x, workingRegion.y, workingRegion.width, workingRegion.height, 0, 0, workingRegion.width, workingRegion.height);
    setDrafts((current) => ({ ...current, [editing]: { ...current[editing], region: workingRegion, preview: canvas.toDataURL("image/png") } }));
    setPreview((current) => ({ ...current, [editing]: canvas.toDataURL("image/png") }));
    setConfirmed((current) => ({ ...current, [editing]: true }));
    setEditing(null);
  };
  const labels: Record<DrawTemplateKey, string> = { cancel: "放弃按钮", max: "最大倍数", coin: "金币" };
  if (editing) return <div className="capture-modal-backdrop" role="dialog" aria-modal="true" aria-label="截取按钮模板"><section className="capture-modal draw-calibration-modal"><header><div><strong>截取{labels[editing]}模板</strong><span>已重新获取最新设备屏幕，拖动选择区域，点击确定后仅更新临时预览。</span></div><button type="button" className="icon-button" title="关闭" onClick={() => setEditing(null)}><X size={17} /></button></header><div className="draw-calibration-stage"><CropPanel image={currentImage} size={{ x: 0, y: 0, ...currentSize }} region={workingRegion ?? drafts[editing].region} onChange={setWorkingRegion} /></div><footer><button type="button" className="button secondary" onClick={() => setEditing(null)}>取消</button><button type="button" className="button primary" disabled={!workingRegion || workingRegion.width < 1 || workingRegion.height < 1} onClick={() => void confirm()}>确定</button></footer></section></div>;
  return <div className="capture-modal-backdrop" role="dialog" aria-modal="true" aria-label="抽卡按钮模板校准"><section className="capture-modal draw-calibration-modal"><header><div><strong>抽卡按钮模板校准</strong><span>默认显示当前模板。每次点击自定义都会重新获取最新设备屏幕。</span></div><button type="button" className="icon-button" title="关闭" onClick={onClose}><X size={17} /></button></header><div className="draw-calibration-preview-grid">{(Object.keys(labels) as DrawTemplateKey[]).map((key) => <article key={key} className="draw-calibration-preview"><strong>{labels[key]}{confirmed[key] ? " ✓" : ""}</strong>{preview[key] ? <img src={preview[key] ?? undefined} alt={`${labels[key]}模板预览`} /> : <div className="draw-calibration-empty">模板图片不可用</div>}<code>{`x=${drafts[key].region.x}, y=${drafts[key].region.y}, ${drafts[key].region.width} x ${drafts[key].region.height}`}</code><button type="button" className="button secondary" onClick={() => void begin(key)}>自定义</button></article>)}</div><footer><button type="button" className="button quiet danger" onClick={onReset}>恢复默认</button><span /><button type="button" className="button secondary" onClick={onClose}>取消</button><button type="button" className="button primary" disabled={!(Object.keys(labels) as DrawTemplateKey[]).every((key) => confirmed[key])} onClick={() => onSave(drafts, size)}>保存校准</button></footer></section></div>;
}

export function DrawPage(props: SharedProps) {
  const [device, setDevice] = useState(() => window.localStorage.getItem(drawDeviceStorageKey) ?? "");
  const [userId, setUserId] = useState(() => window.localStorage.getItem(drawUserStorageKey) ?? "default");
  const [users, setUsers] = useState<User[]>([]);
  const [showUsers, setShowUsers] = useState(false);
  const [newUser, setNewUser] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [sessions, setSessions] = useState<Array<{ file: string; summary: Record<string, unknown> }>>([]);
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([]);
  const [pairs, setPairs] = useState<Array<Record<string, unknown>>>([]);
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const selectedSessionIdRef = useRef("");
  const [selectedPairId, setSelectedPairId] = useState("");
  const [beforeImage, setBeforeImage] = useState<string | null>(null);
  const [afterImage, setAfterImage] = useState<string | null>(null);
  const [showRealtimeLogs, setShowRealtimeLogs] = useState(() => window.localStorage.getItem(drawRealtimeLogsStorageKey) === "true");
  const [showReport, setShowReport] = useState(false);
  const [calibrationImage, setCalibrationImage] = useState<string | null>(null);
  const [calibrationSize, setCalibrationSize] = useState({ width: 1080, height: 1920 });
  const [calibrationConfigs, setCalibrationConfigs] = useState<DrawCalibrationData | null>(null);
  const [reportRange, setReportRange] = useState<Range>("day");
  const [reportEndDay, setReportEndDay] = useState(() => new Date().toISOString().slice(0, 10));
  const [reportStartDay, setReportStartDay] = useState("");
  const running = !!props.running.draw;
  const rawLog = props.logs.draw ?? "";
  const log = showRealtimeLogs ? rawLog : rawLog.split(/\r?\n/).filter((line) => !/if_image \[\d+\/\d+\] template .+ not matched|CMD (?:adb|hdc) shell input tap/.test(line)).join("\n");
  const plan = "choukaka.json";
  const selectedSession = sessions.find((item) => String(item.summary.session_id ?? "") === selectedSessionId) ?? sessions[0];
  const selectedPair = pairs.find((pair) => pairKey(pair) === selectedPairId) ?? pairs[0];
  const currentUser = users.find((user) => user.id === userId) ?? { id: "default", name: "默认用户", createdAt: "" };

  const loadUsers = async () => {
    const next = await window.bsManager.drawUsers();
    setUsers(next);
    setDrafts(Object.fromEntries(next.map((user) => [user.id, user.name])));
    if (!next.some((user) => user.id === userId)) setUserId("default");
  };
  const loadSession = async (sessionId: string, activeUserId = userId) => {
    if (!sessionId) { setEvents([]); setPairs([]); setSelectedPairId(""); return; }
    const [nextEvents, nextPairs] = await Promise.all([window.bsManager.drawEvents(sessionId, activeUserId), window.bsManager.drawScreenshotPairs(sessionId, activeUserId)]);
    setEvents(nextEvents);
    setPairs(nextPairs);
    setSelectedPairId((current) => nextPairs.some((pair) => pairKey(pair) === current) ? current : pairKey(nextPairs[0]));
  };
  const refreshHistory = async () => {
    try {
      const items = await window.bsManager.drawListSessions(userId);
      setSessions(items);
      const preferred = selectedSessionIdRef.current;
      const sessionId = items.some((item) => String(item.summary.session_id ?? "") === preferred) ? preferred : String(items[0]?.summary.session_id ?? "");
      selectedSessionIdRef.current = sessionId;
      setSelectedSessionId(sessionId);
      await loadSession(sessionId);
    } catch (error) { props.setNotice(`读取抽卡记录失败: ${String(error)}`); }
  };

  useEffect(() => { void loadUsers().catch((error) => props.setNotice(`读取用户失败: ${String(error)}`)); }, []);
  useEffect(() => { void refreshHistory(); }, [userId]);
  useEffect(() => {
    setDevice((current) => {
      const next = props.devices.includes(current) ? current : (props.devices[0] ?? "");
      if (next) window.localStorage.setItem(drawDeviceStorageKey, next);
      return next;
    });
  }, [props.devices]);
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void refreshHistory(), 2000);
    return () => window.clearInterval(timer);
  }, [running, userId]);
  useEffect(() => {
    const refresh = () => void refreshHistory();
    window.addEventListener("draw-task-finished", refresh);
    return () => window.removeEventListener("draw-task-finished", refresh);
  }, [userId]);
  useEffect(() => {
    const beforePath = String(selectedPair?.before_path ?? "");
    const afterPath = String(selectedPair?.after_path ?? "");
    void Promise.all([beforePath ? window.bsManager.drawImage(beforePath) : Promise.resolve(null), afterPath ? window.bsManager.drawImage(afterPath) : Promise.resolve(null)]).then(([before, after]) => { setBeforeImage(before); setAfterImage(after); });
  }, [selectedPair?.before_path, selectedPair?.after_path]);

  const selectUser = (next: string) => { window.localStorage.setItem(drawUserStorageKey, next); setUserId(next); setShowUsers(false); };
  const createUser = () => {
    const name = newUser.trim();
    if (!name) return;
    void window.bsManager.drawCreateUser(name).then(async (user) => { setNewUser(""); await loadUsers(); selectUser(user.id); }).catch((error) => props.setNotice(`新增用户失败: ${String(error)}`));
  };
  const start = () => {
    if (!props.runtime || !plan) return props.setNotice("找不到 choukaka.json 计划");
    const args = [`${props.runtime.root}/adb_bot.py`, "--plan", `${props.runtime.internalPlansDir}/${plan}`, "--adb", props.settings.adbPath, "--hdc", props.settings.hdcPath, "--user-id", userId];
    if (device) args.push("--device", device);
    void props.startTask("draw", args);
  };
  const openTemplateCalibration = async () => {
    if (!device) return props.setNotice("请先选择设备");
    try {
    const [image, config] = await Promise.all([
        window.bsManager.screenshot({ adbPath: props.settings.adbPath, hdcPath: props.settings.hdcPath }, device),
        window.bsManager.drawTemplateConfig(),
      ]);
      const size = await new Promise<{ width: number; height: number }>((resolve, reject) => { const source = new Image(); source.onload = () => resolve({ width: source.naturalWidth, height: source.naturalHeight }); source.onerror = () => reject(new Error("截图加载失败")); source.src = image; });
      setCalibrationImage(image);
      setCalibrationSize(size);
      const templateImages = await Promise.all([window.bsManager.templatesImage(config.cancel.template), window.bsManager.templatesImage(config.max.template), window.bsManager.templatesImage(config.coin.template)]);
      setCalibrationConfigs({ configs: config, images: { cancel: templateImages[0], max: templateImages[1], coin: templateImages[2] } });
    } catch (error) { props.setNotice(`打开模板校准失败: ${String(error)}`); }
  };
  const captureLatestCalibration = async () => {
    if (!device) throw new Error("请先选择设备");
    const latestImage = await window.bsManager.screenshot({ adbPath: props.settings.adbPath, hdcPath: props.settings.hdcPath }, device);
    const latestSize = await new Promise<{ width: number; height: number }>((resolve, reject) => { const source = new Image(); source.onload = () => resolve({ width: source.naturalWidth, height: source.naturalHeight }); source.onerror = () => reject(new Error("截图加载失败")); source.src = latestImage; });
    return { image: latestImage, size: latestSize };
  };
  const saveTemplateCalibration = async (drafts: Record<DrawTemplateKey, DrawTemplateConfig>, size: { width: number; height: number }) => {
    if (!calibrationImage) return;
    try {
      const source = new Image(); source.src = calibrationImage;
      await new Promise<void>((resolve, reject) => { source.onload = () => resolve(); source.onerror = () => reject(new Error("截图加载失败")); });
      const saved: Record<DrawTemplateKey, DrawTemplateConfig> = { cancel: { ...drafts.cancel }, max: { ...drafts.max }, coin: { ...drafts.coin } };
      for (const key of ["cancel", "max", "coin"] as const) {
        const region = drafts[key].region;
        const name = `${drafts[key].template.replace(/\.png$/i, "").replace(/[\\/:*?"<>|]/g, "") || (key === "coin" ? "金币" : `${key}_button`)}.png`;
        let dataUrl = drafts[key].preview;
        if (!dataUrl) {
          const canvas = document.createElement("canvas"); canvas.width = region.width; canvas.height = region.height;
          const context = canvas.getContext("2d"); if (!context) throw new Error("无法创建图片画布");
          context.drawImage(source, region.x, region.y, region.width, region.height, 0, 0, region.width, region.height);
          dataUrl = canvas.toDataURL("image/png");
        }
        saved[key] = { template: (await window.bsManager.templatesSaveCapture(name, dataUrl)).split("/").pop() ?? name, region: scaleRegion(region, { x: 0, y: 0, width: size.width, height: size.height }, { x: 0, y: 0, width: 1080, height: 1920 }) };
      }
      await window.bsManager.drawTemplateSave(saved);
      setCalibrationImage(null); setCalibrationConfigs(null);
      props.setNotice("抽卡按钮模板已保存，下一次抽卡生效");
    } catch (error) { props.setNotice(`保存模板校准失败: ${String(error)}`); }
  };
  const resetTemplateCalibration = async () => {
    try {
      if (typeof window.bsManager.drawTemplateReset === "function") await window.bsManager.drawTemplateReset();
      else await window.bsManager.drawTemplateSave(defaultDrawTemplates);
      setCalibrationImage(null); setCalibrationConfigs(null);
      props.setNotice("抽卡按钮模板已恢复默认");
    } catch (error) { props.setNotice(`恢复默认模板失败: ${String(error)}`); }
  };
  const calibrate = (pair: Record<string, unknown>) => {
    const role = window.prompt("校准红卡角色", defaultRedRoles[0]);
    if (!role?.trim()) return;
    void window.bsManager.drawCorrectResult(pairKey(pair), role.trim(), userId).then(() => { props.setNotice(`已校准为 ${role.trim()}`); void refreshHistory(); }).catch((error) => props.setNotice(`红卡校准失败: ${String(error)}`));
  };
  const exportReport = () => {
    void window.bsManager.drawExportReport(reportEndDay, reportRange, userId, reportStartDay).then((result) => {
      props.setNotice(`抽卡报表已导出：${String(result.file ?? "")}`);
      setShowReport(false);
      return window.bsManager.drawOpenReportDirectory();
    }).catch((error) => props.setNotice(`导出抽卡报表失败: ${String(error)}`));
  };

  return <div className="page">
    <PageHeading title="抽卡控制台" detail="按用户保存抽卡结果、红卡校准和概率统计。"><div className="chest-heading-actions"><button className="button secondary" onClick={() => void openTemplateCalibration()}><Settings2 size={16} />模板校准</button><button className="button secondary" onClick={() => void loadUsers().then(() => setShowUsers(true))}><UserRound size={16} />切换用户：{currentUser.name}</button><button className="button secondary" onClick={() => setShowReport(true)}><BarChart3 size={16} />导出报表</button><button className="button secondary" onClick={() => void props.refreshDevices()}><RefreshCw size={16} />刷新设备</button><button className="button secondary" onClick={() => void window.bsManager.drawOpenScreenshots()}><FolderOpen size={16} />打开截图</button><button className="button secondary" onClick={() => void refreshHistory()}><RefreshCw size={16} />刷新记录</button></div></PageHeading>
    <div className="draw-console-layout"><section className="panel form-panel"><label>计划<input readOnly value={plan || "未找到 choukaka.json"} /></label><label>设备<select value={device} onChange={(event) => { setDevice(event.target.value); window.localStorage.setItem(drawDeviceStorageKey, event.target.value); }}>{props.devices.map((name) => <option key={name}>{name}</option>)}</select></label>{!running ? <button className="button primary full" disabled={!plan} onClick={start}><Play size={16} />开始抽卡</button> : <button className="button danger full" onClick={() => void window.bsManager.stopTask("draw")}><CircleStop size={16} />停止抽卡</button>}<LogPanel title="抽卡日志" text={log} actions={<LogActions text={rawLog} showRealtimeLogs={showRealtimeLogs} onToggleRealtimeLogs={(value) => { setShowRealtimeLogs(value); window.localStorage.setItem(drawRealtimeLogsStorageKey, String(value)); }} onCopy={() => void navigator.clipboard.writeText(rawLog)} onClear={() => props.clearTaskLog("draw")} />} /></section>
      <section className="draw-workspace">{selectedSession ? <><section className="panel draw-current-status"><div className="panel-title"><span>当前抽卡状态</span><span className={`run-state ${running ? "live" : ""}`}>{running ? "运行中，自动刷新" : "已停止"}</span></div><div className="metric-grid"><Metric label="抽卡次数" value={selectedSession.summary.draw_started_count} /><Metric label="红卡出现" value={selectedSession.summary.target_seen_count} detail={rate(selectedSession.summary.target_seen_count, selectedSession.summary.draw_started_count)} /><Metric label="实际命中" value={selectedSession.summary.target_hit_count} detail={rate(selectedSession.summary.target_hit_count, selectedSession.summary.draw_started_count)} /></div><div className="draw-result-summary"><span>抽卡结果</span><strong>{drawResultSummary(selectedSession.summary)}</strong></div></section><div className="draw-history-layout"><section className="panel draw-session-list"><div className="panel-title"><span>抽卡记录</span><span className="counter">{sessions.length}</span></div><div className="draw-list-scroll">{sessions.map((item) => { const id = String(item.summary.session_id ?? ""); return <button key={item.file} className={`draw-list-item ${id === String(selectedSession.summary.session_id ?? "") ? "selected" : ""}`} onClick={() => { selectedSessionIdRef.current = id; setSelectedSessionId(id); void loadSession(id); }}><strong>{id}</strong><span>{String(item.summary.updated_at ?? "")}</span><span>抽卡 {String(item.summary.draw_started_count ?? 0)} · 命中 {String(item.summary.target_hit_count ?? 0)}</span><small>{drawResultSummary(item.summary)}</small></button>; })}</div></section><section className="draw-details"><section className="panel"><div className="panel-title"><span>结果截图</span><span className="counter">{pairs.length}</span></div>{pairs.length ? <div className="draw-pairs"><div className="pair-list">{pairs.map((pair) => <button key={pairKey(pair)} className={`pair-list-item ${pairKey(pair) === pairKey(selectedPair) ? "selected" : ""}`} onClick={() => setSelectedPairId(pairKey(pair))}><strong>{pairTitle(pair)}</strong><span>{String(pair.after_saved_at ?? pair.before_saved_at ?? "")}</span></button>)}</div><div className="pair-preview"><div className="pair-preview-heading"><strong>{pairTitle(selectedPair)}</strong><span>{String(selectedPair?.after_saved_at ?? selectedPair?.before_saved_at ?? "")}</span></div><DrawImage label="抽卡前" image={beforeImage} placeholder="截图已清理或不存在" /><DrawImage label="抽卡后" image={afterImage} placeholder="截图已清理或不存在" />{String(selectedPair?.drawn_role ?? "") === "unknown_red_role" || events.some((event) => String(event.pair_prefix ?? "") === pairKey(selectedPair) && String(event.matched_template ?? "") === "unknown_red_role") ? <button className="button primary calibration-add-item" onClick={() => calibrate(selectedPair)}>校准红卡</button> : null}</div></div> : <p className="empty-note padded-note">当前会话尚未保存红卡前后截图。</p>}</section><section className="panel"><div className="panel-title"><span>事件时间线</span><span className="counter">{events.length}</span></div><div className="draw-event-list">{events.length ? [...events].reverse().map((event, index) => <div className="draw-event" key={`${String(event.timestamp ?? "")}-${index}`}><div><strong>{eventTitle(String(event.event ?? ""))}</strong>{event.draw_type ? <span className="event-type">{String(event.draw_type).toUpperCase()}</span> : null}</div><time>{String(event.timestamp ?? "")}</time><p>抽卡 {String(event.draw_started_count ?? 0)} · 出现 {String(event.target_seen_count ?? 0)} · 命中 {String(event.target_hit_count ?? 0)}{event.matched_role_note || event.matched_template ? ` · ${String(event.matched_role_note || event.matched_template)}` : ""}</p></div>) : <p className="empty-note padded-note">当前会话还没有事件记录。</p>}</div></section></section></div></> : <section className="panel empty-draw-state"><strong>暂无抽卡记录</strong><p>开始抽卡后，这里会自动显示当前用户的会话、角色结果和红卡截图。</p></section>}</section></div>
    {showUsers && <Modal title="抽卡用户管理" onClose={() => setShowUsers(false)}><div className="chest-label-list">{users.map((user) => <div className={`chest-user-row ${user.id === userId ? "selected" : ""}`} key={user.id}><button className="button quiet" onClick={() => selectUser(user.id)}>使用</button><input value={drafts[user.id] ?? user.name} onChange={(event) => setDrafts({ ...drafts, [user.id]: event.target.value })} /><button className="button quiet" onClick={() => void window.bsManager.drawRenameUser(user.id, drafts[user.id] ?? user.name).then(loadUsers)}>重命名</button></div>)}<div className="chest-user-create"><input placeholder="新用户名称" value={newUser} onChange={(event) => setNewUser(event.target.value)} /><button className="button primary" onClick={createUser}>新增</button></div></div></Modal>}
    {showReport && <Modal title="导出抽卡统计报表" onClose={() => setShowReport(false)}><div className="chest-export-options"><label>用户<select value={userId} onChange={(event) => setUserId(event.target.value)}>{users.map((user) => <option key={user.id} value={user.id}>{user.name}</option>)}</select></label><label>统计范围<select value={reportRange} onChange={(event) => setReportRange(event.target.value as Range)}><option value="day">当天</option><option value="7d">近 7 天</option><option value="month">近 30 天</option><option value="custom">自定义</option></select></label>{reportRange === "custom" ? <label>开始日期<input type="date" value={reportStartDay} onChange={(event) => setReportStartDay(event.target.value)} /></label> : null}<label>结束日期<input type="date" value={reportEndDay} onChange={(event) => setReportEndDay(event.target.value)} /></label><button className="button primary" onClick={exportReport}>导出 CSV</button></div></Modal>}
    {calibrationImage && calibrationConfigs && <TemplateCalibrationModal image={calibrationImage} size={calibrationSize} configs={calibrationConfigs.configs} templateImages={calibrationConfigs.images} captureLatest={captureLatestCalibration} onClose={() => { setCalibrationImage(null); setCalibrationConfigs(null); }} onReset={() => void resetTemplateCalibration()} onSave={(drafts, size) => void saveTemplateCalibration(drafts, size)} />}
  </div>;
}

function pairKey(pair: Record<string, unknown> | undefined) { return String(pair?.pair_prefix ?? ""); }
function pairTitle(pair: Record<string, unknown> | undefined) { return `${String(pair?.before_label ?? pair?.after_label ?? "记录").toUpperCase()} #${String(pair?.pair_index ?? "")}`; }
function rate(numerator: unknown, denominator: unknown) { const total = Number(denominator ?? 0); return total > 0 ? `${(Number(numerator ?? 0) / total * 100).toFixed(1)}%` : "0%"; }
function drawResultSummary(summary: Record<string, unknown>) { const counts = summary.role_hit_counts && typeof summary.role_hit_counts === "object" ? summary.role_hit_counts as Record<string, unknown> : {}; return Object.entries(counts).sort((a, b) => Number(b[1]) - Number(a[1])).map(([role, count]) => `${role.replace(/\.[^.]+$/, "")}*${Number(count)}`).join(" · ") || "暂无红卡命中"; }
function eventTitle(event: string) { return ({ draw_started: "开启抽卡", target_seen: "目标出现", target_hit: "命中目标卡", target_miss: "未命中目标卡" } as Record<string, string>)[event] ?? (event || "事件"); }
function DrawImage({ label, image, placeholder }: { label: string; image: string | null; placeholder: string }) { return <div className="draw-image"><span>{label}</span>{image ? <img src={image} alt={label} /> : <div className="draw-image-placeholder">{placeholder}</div>}</div>; }
function Metric({ label, value, detail }: { label: string; value: unknown; detail?: string }) { return <div><span>{label}</span><strong>{String(value ?? 0)}</strong>{detail ? <small>{detail}</small> : null}</div>; }
function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) { return <div className="chest-label-backdrop"><section className="chest-label-modal chest-user-modal"><header><strong>{title}</strong><button className="icon-button" title="关闭" onClick={onClose}><X size={16} /></button></header>{children}</section></div>; }
