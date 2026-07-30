import { useEffect, useRef, useState } from "react";
import { CircleStop, FolderOpen, Play, RefreshCw } from "lucide-react";
import { LogPanel, PageHeading } from "../components/layout";
import type { SharedProps } from "../app/shared";

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

export function DrawPage(props: SharedProps) {
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

