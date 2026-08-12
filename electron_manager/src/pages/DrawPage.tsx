import { useEffect, useRef, useState } from "react";
import { BarChart3, CircleStop, FolderOpen, Play, RefreshCw, UserRound, X } from "lucide-react";
import { LogActions, LogPanel, PageHeading } from "../components/layout";
import type { SharedProps } from "../app/shared";

const drawDeviceStorageKey = "draw-selected-device";
const drawUserStorageKey = "draw-selected-user";
const drawRealtimeLogsStorageKey = "draw-show-realtime-logs";
const defaultRedRoles = ["波斯王子", "卡卡西", "李白", "龙三", "绿巨人", "圣骑士", "我爱罗", "蜘蛛"];
type User = { id: string; name: string; createdAt: string };
type Range = "day" | "7d" | "month" | "custom";

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
  const [reportRange, setReportRange] = useState<Range>("day");
  const [reportEndDay, setReportEndDay] = useState(() => new Date().toISOString().slice(0, 10));
  const [reportStartDay, setReportStartDay] = useState("");
  const running = !!props.running.draw;
  const rawLog = props.logs.draw ?? "";
  const log = showRealtimeLogs ? rawLog : rawLog.split(/\r?\n/).filter((line) => !/if_image \[\d+\/\d+\] template .+ not matched|CMD adb shell input tap/.test(line)).join("\n");
  const plan = props.plans.includes("choukaka.json") ? "choukaka.json" : "";
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
    const args = [`${props.runtime.root}/adb_bot.py`, "--plan", `${props.runtime.plansDir}/${plan}`, "--adb", props.settings.adbPath, "--user-id", userId];
    if (device) args.push("--device", device);
    void props.startTask("draw", args);
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
    <PageHeading title="抽卡控制台" detail="按用户保存抽卡结果、红卡校准和概率统计。"><div className="chest-heading-actions"><button className="button secondary" onClick={() => void loadUsers().then(() => setShowUsers(true))}><UserRound size={16} />切换用户：{currentUser.name}</button><button className="button secondary" onClick={() => setShowReport(true)}><BarChart3 size={16} />导出报表</button><button className="button secondary" onClick={() => void props.refreshDevices()}><RefreshCw size={16} />刷新设备</button><button className="button secondary" onClick={() => void window.bsManager.drawOpenScreenshots()}><FolderOpen size={16} />打开截图</button><button className="button secondary" onClick={() => void refreshHistory()}><RefreshCw size={16} />刷新记录</button></div></PageHeading>
    <div className="draw-console-layout"><section className="panel form-panel"><label>计划<input readOnly value={plan || "未找到 choukaka.json"} /></label><label>设备<select value={device} onChange={(event) => { setDevice(event.target.value); window.localStorage.setItem(drawDeviceStorageKey, event.target.value); }}>{props.devices.map((name) => <option key={name}>{name}</option>)}</select></label>{!running ? <button className="button primary full" disabled={!plan} onClick={start}><Play size={16} />开始抽卡</button> : <button className="button danger full" onClick={() => void window.bsManager.stopTask("draw")}><CircleStop size={16} />停止抽卡</button>}<LogPanel title="抽卡日志" text={log} actions={<LogActions text={rawLog} showRealtimeLogs={showRealtimeLogs} onToggleRealtimeLogs={(value) => { setShowRealtimeLogs(value); window.localStorage.setItem(drawRealtimeLogsStorageKey, String(value)); }} onCopy={() => void navigator.clipboard.writeText(rawLog)} onClear={() => props.clearTaskLog("draw")} />} /></section>
      <section className="draw-workspace">{selectedSession ? <><section className="panel draw-current-status"><div className="panel-title"><span>当前抽卡状态</span><span className={`run-state ${running ? "live" : ""}`}>{running ? "运行中，自动刷新" : "已停止"}</span></div><div className="metric-grid"><Metric label="抽卡次数" value={selectedSession.summary.draw_started_count} /><Metric label="红卡出现" value={selectedSession.summary.target_seen_count} detail={rate(selectedSession.summary.target_seen_count, selectedSession.summary.draw_started_count)} /><Metric label="实际命中" value={selectedSession.summary.target_hit_count} detail={rate(selectedSession.summary.target_hit_count, selectedSession.summary.draw_started_count)} /></div><div className="draw-result-summary"><span>抽卡结果</span><strong>{drawResultSummary(selectedSession.summary)}</strong></div></section><div className="draw-history-layout"><section className="panel draw-session-list"><div className="panel-title"><span>抽卡记录</span><span className="counter">{sessions.length}</span></div><div className="draw-list-scroll">{sessions.map((item) => { const id = String(item.summary.session_id ?? ""); return <button key={item.file} className={`draw-list-item ${id === String(selectedSession.summary.session_id ?? "") ? "selected" : ""}`} onClick={() => { selectedSessionIdRef.current = id; setSelectedSessionId(id); void loadSession(id); }}><strong>{id}</strong><span>{String(item.summary.updated_at ?? "")}</span><span>抽卡 {String(item.summary.draw_started_count ?? 0)} · 命中 {String(item.summary.target_hit_count ?? 0)}</span><small>{drawResultSummary(item.summary)}</small></button>; })}</div></section><section className="draw-details"><section className="panel"><div className="panel-title"><span>结果截图</span><span className="counter">{pairs.length}</span></div>{pairs.length ? <div className="draw-pairs"><div className="pair-list">{pairs.map((pair) => <button key={pairKey(pair)} className={`pair-list-item ${pairKey(pair) === pairKey(selectedPair) ? "selected" : ""}`} onClick={() => setSelectedPairId(pairKey(pair))}><strong>{pairTitle(pair)}</strong><span>{String(pair.after_saved_at ?? pair.before_saved_at ?? "")}</span></button>)}</div><div className="pair-preview"><div className="pair-preview-heading"><strong>{pairTitle(selectedPair)}</strong><span>{String(selectedPair?.after_saved_at ?? selectedPair?.before_saved_at ?? "")}</span></div><DrawImage label="抽卡前" image={beforeImage} placeholder="截图已清理或不存在" /><DrawImage label="抽卡后" image={afterImage} placeholder="截图已清理或不存在" />{String(selectedPair?.drawn_role ?? "") === "unknown_red_role" || events.some((event) => String(event.pair_prefix ?? "") === pairKey(selectedPair) && String(event.matched_template ?? "") === "unknown_red_role") ? <button className="button primary calibration-add-item" onClick={() => calibrate(selectedPair)}>校准红卡</button> : null}</div></div> : <p className="empty-note padded-note">当前会话尚未保存红卡前后截图。</p>}</section><section className="panel"><div className="panel-title"><span>事件时间线</span><span className="counter">{events.length}</span></div><div className="draw-event-list">{events.length ? [...events].reverse().map((event, index) => <div className="draw-event" key={`${String(event.timestamp ?? "")}-${index}`}><div><strong>{eventTitle(String(event.event ?? ""))}</strong>{event.draw_type ? <span className="event-type">{String(event.draw_type).toUpperCase()}</span> : null}</div><time>{String(event.timestamp ?? "")}</time><p>抽卡 {String(event.draw_started_count ?? 0)} · 出现 {String(event.target_seen_count ?? 0)} · 命中 {String(event.target_hit_count ?? 0)}{event.matched_role_note || event.matched_template ? ` · ${String(event.matched_role_note || event.matched_template)}` : ""}</p></div>) : <p className="empty-note padded-note">当前会话还没有事件记录。</p>}</div></section></section></div></> : <section className="panel empty-draw-state"><strong>暂无抽卡记录</strong><p>开始抽卡后，这里会自动显示当前用户的会话、角色结果和红卡截图。</p></section>}</section></div>
    {showUsers && <Modal title="抽卡用户管理" onClose={() => setShowUsers(false)}><div className="chest-label-list">{users.map((user) => <div className={`chest-user-row ${user.id === userId ? "selected" : ""}`} key={user.id}><button className="button quiet" onClick={() => selectUser(user.id)}>使用</button><input value={drafts[user.id] ?? user.name} onChange={(event) => setDrafts({ ...drafts, [user.id]: event.target.value })} /><button className="button quiet" onClick={() => void window.bsManager.drawRenameUser(user.id, drafts[user.id] ?? user.name).then(loadUsers)}>重命名</button></div>)}<div className="chest-user-create"><input placeholder="新用户名称" value={newUser} onChange={(event) => setNewUser(event.target.value)} /><button className="button primary" onClick={createUser}>新增</button></div></div></Modal>}
    {showReport && <Modal title="导出抽卡统计报表" onClose={() => setShowReport(false)}><div className="chest-export-options"><label>用户<select value={userId} onChange={(event) => setUserId(event.target.value)}>{users.map((user) => <option key={user.id} value={user.id}>{user.name}</option>)}</select></label><label>统计范围<select value={reportRange} onChange={(event) => setReportRange(event.target.value as Range)}><option value="day">当天</option><option value="7d">近 7 天</option><option value="month">近 30 天</option><option value="custom">自定义</option></select></label>{reportRange === "custom" ? <label>开始日期<input type="date" value={reportStartDay} onChange={(event) => setReportStartDay(event.target.value)} /></label> : null}<label>结束日期<input type="date" value={reportEndDay} onChange={(event) => setReportEndDay(event.target.value)} /></label><button className="button primary" onClick={exportReport}>导出 CSV</button></div></Modal>}
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
