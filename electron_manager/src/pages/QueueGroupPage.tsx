import { useCallback, useEffect, useState } from "react";
import { ArrowDown, ArrowUp, CircleStop, Copy, CopyPlus, Layers, Pencil, PencilLine, Play, Plus, RefreshCw, Save, Trash2, X } from "lucide-react";
import { PageHeading, PromptDialog, Toggle } from "../components/layout";
import type { QueueGroup, QueueGroupItem, QueueItemResult, QueueItemStatus, QueueRunSnapshot } from "../types";
import { newestFirstLog, type SharedProps } from "../app/shared";

type DraftItem = { plan: string; minutesText: string; cycleDaysText: string };
type QueueGroupDraft = {
  id: string | null;
  name: string;
  items: DraftItem[];
  onError: "continue" | "stop";
  repeat: boolean;
};

const ITEM_STATUS_META: Record<QueueItemStatus, { text: string; className: string }> = {
  pending: { text: "等待", className: "queue-badge pending" },
  running: { text: "运行中", className: "queue-badge running" },
  ok: { text: "完成", className: "queue-badge ok" },
  failed: { text: "失败", className: "queue-badge failed" },
  skipped: { text: "已跳过", className: "queue-badge skipped" },
  stopped: { text: "已停止", className: "queue-badge stopped" },
};

function planLabel(plan: string) {
  return plan.replace(/\.json$/i, "");
}

function formatLastRun(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (number: number) => String(number).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function formatMinutes(seconds: number) {
  const minutes = seconds / 60;
  return Number.isInteger(minutes) ? String(minutes) : String(Math.round(minutes * 100) / 100);
}

type PlanMeta = { maxRuntimeSec: number; unbounded: boolean; cycleDays: number };

const CYCLE_VARIABLE_NAMES = ["EXEC_CYCLE_DAYS", "CYCLE_DAYS", "执行周期"];

function planDefaultCycleDays(variables: unknown): number {
  const entries: Array<[string, unknown]> = [];
  if (Array.isArray(variables)) {
    for (const entry of variables) {
      if (!entry || typeof entry !== "object") continue;
      const record = entry as Record<string, unknown>;
      entries.push([String(record.name ?? ""), record.value]);
    }
  } else if (variables && typeof variables === "object") {
    entries.push(...Object.entries(variables as Record<string, unknown>));
  }
  for (const [name, value] of entries) {
    const normalized = name.trim().toUpperCase();
    if (!CYCLE_VARIABLE_NAMES.some((candidate) => candidate.toUpperCase() === normalized)) continue;
    const days = Number(String(value ?? "").replace(/[^\d.]/g, ""));
    if (Number.isFinite(days) && days > 0) return Math.round(days);
  }
  return 0;
}

function cycleRemainingMs(item: QueueGroupItem) {
  const days = item.cycleDays ?? 0;
  if (days <= 0) return 0;
  if (!item.lastRunAt) return 0;
  const last = Date.parse(item.lastRunAt);
  if (!Number.isFinite(last)) return 0;
  return Math.max(0, days * 86_400_000 - (Date.now() - last));
}

function planHasUnboundedLoop(value: unknown): boolean {
  if (!Array.isArray(value)) return false;
  for (const entry of value) {
    if (!entry || typeof entry !== "object") continue;
    const action = entry as Record<string, unknown>;
    const type = String(action.type ?? "").toLowerCase();
    if (type === "loop" && Number(action.count ?? 1) < 0) return true;
    if (type === "patrol" && Number(action.rounds ?? 1) < 0) return true;
    if (planHasUnboundedLoop(action.actions) || planHasUnboundedLoop(action.then_actions) || planHasUnboundedLoop(action.else_actions)) return true;
  }
  return false;
}

function emptyDraft(): QueueGroupDraft {
  return { id: null, name: "", items: [], onError: "continue", repeat: false };
}

function draftFromGroup(group: QueueGroup, planMeta: Record<string, PlanMeta>): QueueGroupDraft {
  return {
    id: group.id,
    name: group.name,
    items: group.items.map((item) => {
      const planDefault = planMeta[item.plan]?.maxRuntimeSec ?? 0;
      const cycleDefault = planMeta[item.plan]?.cycleDays ?? 0;
      const cycleDays = item.cycleDays && item.cycleDays > 0 ? item.cycleDays : cycleDefault;
      return {
        plan: item.plan,
        minutesText: item.maxRuntimeSec > 0 ? formatMinutes(item.maxRuntimeSec) : planDefault > 0 ? formatMinutes(planDefault) : "",
        cycleDaysText: cycleDays > 0 ? String(cycleDays) : "",
      };
    }),
    onError: group.onError,
    repeat: group.repeat,
  };
}

export function QueueGroupPage(props: SharedProps) {
  const [groups, setGroups] = useState<QueueGroup[]>([]);
  const [states, setStates] = useState<Record<string, QueueRunSnapshot>>({});
  const [planMeta, setPlanMeta] = useState<Record<string, PlanMeta>>({});
  const [groupDevices, setGroupDevices] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<QueueGroupDraft | null>(null);
  const [editorError, setEditorError] = useState("");
  const [renameTarget, setRenameTarget] = useState<QueueGroup | null>(null);
  const [cyclePrompt, setCyclePrompt] = useState<{ group: QueueGroup; device: string; notDue: QueueGroupItem[] } | null>(null);

  useEffect(() => {
    setGroupDevices((current) => {
      let changed = false;
      const next = { ...current };
      for (const group of groups) {
        if (group.id in next) continue;
        next[group.id] = window.localStorage.getItem(`queue-device-${group.id}`) ?? window.localStorage.getItem("queue-device") ?? "";
        changed = true;
      }
      return changed ? next : current;
    });
  }, [groups]);

  const updateGroupDevice = (groupId: string, value: string) => {
    setGroupDevices((current) => ({ ...current, [groupId]: value }));
    window.localStorage.setItem(`queue-device-${groupId}`, value);
  };

  const capacity = props.license?.maxConcurrentRunners ?? 1;
  const runningCount = groups.filter((group) => {
    const status = states[group.id]?.status;
    return status === "running" || status === "stopping";
  }).length;
  const licenseFree = props.license?.tier !== "pro" || !props.license?.valid;

  useEffect(() => {
    void window.bsManager.queueGroupsList().then(setGroups).catch((error) => props.setNotice(`读取计划组失败: ${String(error)}`));
    void window.bsManager.queueStates().then((snapshots) => {
      setStates(Object.fromEntries(snapshots.map((snapshot) => [snapshot.groupId, snapshot])));
    }).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => window.bsManager.onQueueEvent((event) => {
    setStates((current) => ({ ...current, [event.groupId]: event }));
    // Item completions update per-plan lastRunAt on disk; refresh the group list.
    void refresh();
  }), []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const entries = await Promise.all(props.plans.map(async (name) => {
        try {
          const plan = JSON.parse(await window.bsManager.plansRead(name)) as { max_runtime_sec?: unknown; actions?: unknown; variables?: unknown };
          const seconds = Number(plan.max_runtime_sec ?? 0);
          return [name, { maxRuntimeSec: Number.isFinite(seconds) && seconds > 0 ? seconds : 0, unbounded: planHasUnboundedLoop(plan.actions), cycleDays: planDefaultCycleDays(plan.variables) }] as const;
        } catch { return [name, { maxRuntimeSec: 0, unbounded: false, cycleDays: 0 }] as const; }
      }));
      if (!cancelled) setPlanMeta(Object.fromEntries(entries));
    })();
    return () => { cancelled = true; };
  }, [props.plans]);

  const refresh = useCallback(async () => {
    try {
      setGroups(await window.bsManager.queueGroupsList());
    } catch (error) {
      props.setNotice(`读取计划组失败: ${String(error)}`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openEditor = (group: QueueGroup | null) => {
    const next = group ? draftFromGroup(group, planMeta) : emptyDraft();
    setDraft(next);
    setEditorError("");
  };

  const moveDraftItem = (index: number, offset: number) => {
    if (!draft) return;
    setEditorError("");
    const target = index + offset;
    if (target < 0 || target >= draft.items.length) return;
    const items = [...draft.items];
    [items[index], items[target]] = [items[target], items[index]];
    setDraft({ ...draft, items });
  };

  const removeDraftItem = (index: number) => {
    setEditorError("");
    setDraft((current) => current ? { ...current, items: current.items.filter((_, i) => i !== index) } : current);
  };

  const addDraftItem = (plan: string) => {
    setEditorError("");
    const planDefault = planMeta[plan]?.maxRuntimeSec ?? 0;
    const minutesText = planDefault > 0 ? formatMinutes(planDefault) : "";
    const cycleDefault = planMeta[plan]?.cycleDays ?? 0;
    setDraft((current) => {
      if (!current || current.items.some((item) => item.plan === plan)) return current;
      return { ...current, items: [...current.items, { plan, minutesText, cycleDaysText: cycleDefault > 0 ? String(cycleDefault) : "" }] };
    });
  };

  const saveDraft = async () => {
    if (!draft) return;
    const fail = (message: string) => {
      setEditorError(message);
      props.setNotice(message);
    };
    if (!draft.name.trim()) return fail("请填写计划组名称");
    if (draft.items.length === 0) return fail("请至少选择一个脚本");
    const items: QueueGroupItem[] = [];
    for (const [index, item] of draft.items.entries()) {
      const minutesText = item.minutesText.trim();
      const minutes = Number(minutesText);
      const seconds = Number.isFinite(minutes) && minutes > 0 ? Math.round(minutes * 60) : 0;
      const meta = planMeta[item.plan];
      const planDefault = meta?.maxRuntimeSec ?? 0;
      const unbounded = meta?.unbounded ?? false;
      const allowedForever = unbounded && index === draft.items.length - 1 && !draft.repeat;
      if (seconds <= 0 && !(planDefault > 0) && unbounded && !allowedForever) {
        return fail(`${planLabel(item.plan)} 会无限运行且未设置时长：请填写分钟数，或把它放在最后并关闭整队循环`);
      }
      const cycleText = item.cycleDaysText.trim();
      const cycleNumber = Number(cycleText);
      const cycleDays = Number.isFinite(cycleNumber) && cycleNumber > 0 ? Math.round(cycleNumber) : 0;
      if (cycleText && cycleDays <= 0) {
        return fail(`${planLabel(item.plan)} 的执行周期需填写正整数天数（留空表示每次都执行）`);
      }
      items.push({ plan: item.plan, maxRuntimeSec: seconds, enabled: true, cycleDays });
    }
    try {
      await window.bsManager.queueGroupsSave({ id: draft.id ?? "", name: draft.name.trim(), items, onError: draft.onError, repeat: draft.repeat });
      const savedName = draft.name.trim();
      setDraft(null);
      setEditorError("");
      await refresh();
      props.setNotice(`计划组「${savedName}」已保存`);
    } catch (error) {
      fail(`保存失败: ${String(error)}`);
    }
  };

  const beginGroupStart = async (group: QueueGroup, device: string, skipPlans: string[]) => {
    const runnable = group.items.filter((item) => item.enabled !== false && !skipPlans.includes(item.plan));
    if (runnable.length === 0) {
      props.setNotice("所有计划都未到执行周期，本次未启动");
      return;
    }
    const taskId = `runner-queue-${group.id}`;
    props.clearTaskLog(taskId);
    try {
      await window.bsManager.queueStart(group.id, device, skipPlans);
      await refresh();
      props.setNotice(`计划组「${group.name}」开始执行${skipPlans.length > 0 ? `（跳过 ${skipPlans.length} 个未到期计划）` : ""}`);
    } catch (error) {
      props.setNotice(`启动失败: ${String(error)}`);
    }
  };

  const startGroup = async (group: QueueGroup) => {
    if (!props.runtime) return props.setNotice("运行环境尚未加载完成");
    const device = (groupDevices[group.id] ?? "").trim();
    if (!device) return props.setNotice("请先在卡片中选择该计划组的执行设备");
    const notDue = group.items.filter((item) => item.enabled !== false && cycleRemainingMs(item) > 0);
    if (notDue.length > 0) {
      setCyclePrompt({ group, device, notDue });
      return;
    }
    await beginGroupStart(group, device, []);
  };

  const stopGroup = async (group: QueueGroup) => {
    try {
      await window.bsManager.queueStop(group.id);
      props.setNotice("正在停止计划组…");
    } catch (error) {
      props.setNotice(`停止失败: ${String(error)}`);
    }
  };

  const duplicateGroup = async (group: QueueGroup) => {
    const baseName = `${group.name}（副本）`;
    const names = new Set(groups.map((item) => item.name));
    let name = baseName;
    let index = 2;
    while (names.has(name)) {
      name = `${group.name}（副本 ${index}）`;
      index += 1;
    }
    try {
      await window.bsManager.queueGroupsSave({ id: "", name, items: group.items.map((item) => ({ ...item })), onError: group.onError, repeat: group.repeat });
      await refresh();
      props.setNotice(`已复制为「${name}」`);
    } catch (error) {
      props.setNotice(`复制失败: ${String(error)}`);
    }
  };

  const renameGroup = async (group: QueueGroup, name: string) => {
    try {
      await window.bsManager.queueGroupsSave({ id: group.id, name, items: group.items, onError: group.onError, repeat: group.repeat });
      await refresh();
      props.setNotice(`已改名为「${name}」`);
    } catch (error) {
      props.setNotice(`改名失败: ${String(error)}`);
    }
  };

  const deleteGroup = async (groupId: string, groupName: string) => {
    if (!window.confirm(`删除计划组「${groupName}」？`)) return;
    try {
      await window.bsManager.queueGroupsDelete(groupId);
      setDraft(null);
      setEditorError("");
      await refresh();
      props.setNotice("计划组已删除");
    } catch (error) {
      const message = `删除失败: ${String(error)}`;
      setEditorError(message);
      props.setNotice(message);
    }
  };

  const statusLabel = (group: QueueGroup) => {
    const snapshot = states[group.id];
    if (!snapshot) return "待命";
    if (snapshot.status === "completed") return "已完成";
    if (snapshot.status === "stopped") return "已停止";
    if (snapshot.status === "stopping") return "正在停止";
    const position = snapshot.total ? `第 ${Math.min(snapshot.index + 1, snapshot.total)}/${snapshot.total} 项` : "";
    const round = snapshot.round > 1 ? ` · 第 ${snapshot.round} 轮` : "";
    return `运行中 ${position}${round}`;
  };

  return <div className="page queue-page">
    <PageHeading title="计划组" detail="把多个脚本组成串行队列：按顺序自动执行，可调整顺序、限制每项时长；多个计划组可同时运行（受授权并发数限制），每组单独选择设备。">
      <button className="button secondary" onClick={() => void props.refreshDevices()}><RefreshCw size={16} />刷新设备</button>
      <button className="button primary" onClick={() => openEditor(null)}><Plus size={16} />新建计划组</button>
    </PageHeading>
    <section className="device-strip panel">
      <Layers size={18} />
      <strong>已发现 {props.devices.length} 个设备</strong>
      <span>{props.devices.join("  ·  ") || "请检查 ADB 路径、模拟器或 USB 调试连接"}</span>
      <span className="queue-concurrency">同时运行上限 {capacity} 个（当前运行 {runningCount} 个）{licenseFree ? " · 免费版仅支持 1 个，多开需专业版" : ""}</span>
    </section>
    {draft && <section className="panel queue-editor">
      <div className="panel-title"><span>{draft.id ? "编辑计划组" : "新建计划组"}</span></div>
      <label className="queue-name-row">名称<input value={draft.name} placeholder="例如：日常挂机" onChange={(event) => { setEditorError(""); setDraft({ ...draft, name: event.target.value }); }} /></label>
      <div className="queue-editor-columns">
        <div className="queue-selected">
          <div className="panel-title"><span>已选脚本（按此顺序执行）</span><span className="counter">{draft.items.length}</span></div>
          <p className="empty-note queue-duration-note">时长留空 = 跑完为止（会自己结束的脚本）；标 ∞ 的脚本会无限运行，必须填时长，或放在最后并关闭整队循环。</p>
          <p className="empty-note queue-duration-note">主城出发的脚本建议把“G_主城校准出发点”放到第一位置。</p>
          <p className="empty-note queue-duration-note">执行周期（天）留空 = 每次都执行；默认值读取脚本变量 EXEC_CYCLE_DAYS（天），可在右侧覆盖。</p>
          {draft.items.length === 0 && <p className="empty-note">还没有选择脚本，请从右侧列表添加。</p>}
          {draft.items.map((item, index) => {
            const meta = planMeta[item.plan];
            const planDefault = meta?.maxRuntimeSec ?? 0;
            const unbounded = meta?.unbounded ?? false;
            const minutes = Number(item.minutesText.trim() || NaN);
            const allowedForever = unbounded && index === draft.items.length - 1 && !draft.repeat;
            const needDuration = unbounded && !allowedForever && !(Number.isFinite(minutes) && minutes > 0) && !(planDefault > 0);
            return <div className={`queue-item-row ${needDuration ? "queue-item-warning" : ""}`} key={`${item.plan}-${index}`}>
              <span className="queue-order">{index + 1}</span>
              <strong title={item.plan}>{planLabel(item.plan)}</strong>
              <input className="queue-minutes" title="留空 = 跑完为止（仅会自己结束的脚本）；∞ 无限循环脚本必须填时长，或放在最后并关闭整队循环" aria-label={`${planLabel(item.plan)} 的运行时长（分钟）`} value={item.minutesText} placeholder={planDefault > 0 ? `默认 ${formatMinutes(planDefault)}` : unbounded ? (allowedForever ? "可留空" : "必填") : "不限"} onChange={(event) => { setEditorError(""); const value = event.target.value; setDraft((current) => current && { ...current, items: current.items.map((entry, i) => (i === index ? { ...entry, minutesText: value } : entry)) }); }} />
              <span className="queue-minutes-unit">分钟</span>
              <input className="queue-cycle" title="执行周期（天）：未到周期时启动会提示跳过；留空 = 每次都执行" aria-label={`${planLabel(item.plan)} 的执行周期（天）`} value={item.cycleDaysText} placeholder={meta && meta.cycleDays > 0 ? `周期${meta.cycleDays}` : "周期(天)"} onChange={(event) => { setEditorError(""); const value = event.target.value; setDraft((current) => current && { ...current, items: current.items.map((entry, i) => (i === index ? { ...entry, cycleDaysText: value } : entry)) }); }} />
              <span className="queue-minutes-unit">天</span>
              <button className="icon-button" title="上移" aria-label={`上移 ${planLabel(item.plan)}`} disabled={index === 0} onClick={() => moveDraftItem(index, -1)}><ArrowUp size={14} /></button>
              <button className="icon-button" title="下移" aria-label={`下移 ${planLabel(item.plan)}`} disabled={index === draft.items.length - 1} onClick={() => moveDraftItem(index, 1)}><ArrowDown size={14} /></button>
              <button className="icon-button" title="移除" aria-label={`移除 ${planLabel(item.plan)}`} onClick={() => removeDraftItem(index)}><Trash2 size={14} /></button>
            </div>;
          })}
        </div>
        <div className="queue-available">
          <div className="panel-title"><span>可选脚本</span><span className="counter">{props.plans.length}</span></div>
          <div className="queue-plan-list">
            {props.plans.map((name) => {
              const added = draft.items.some((item) => item.plan === name);
              const unbounded = planMeta[name]?.unbounded ?? false;
              return <button key={name} className={`plan-item ${added ? "selected" : ""}`} disabled={added} onClick={() => addDraftItem(name)}><Plus size={14} /><span>{planLabel(name)}</span>{unbounded && <em className="queue-infinite" title="无限循环脚本">∞</em>}</button>;
            })}
          </div>
        </div>
      </div>
      <div className="queue-editor-options">
        <label>某项失败时<select value={draft.onError} onChange={(event) => { setEditorError(""); setDraft({ ...draft, onError: event.target.value === "stop" ? "stop" : "continue" }); }}><option value="continue">跳过并继续下一项</option><option value="stop">停止整个队列</option></select></label>
        <Toggle label="整队循环（最后一项跑完回到第一项）" checked={draft.repeat} onChange={(repeat) => { setEditorError(""); setDraft({ ...draft, repeat }); }} />
      </div>
      {editorError && <p className="queue-editor-error" role="alert">{editorError}</p>}
      <div className="queue-editor-actions">
        {draft.id && <button className="button quiet danger queue-delete-button" onClick={() => { if (draft.id) void deleteGroup(draft.id, draft.name); }}><Trash2 size={15} />删除计划组</button>}
        <button className="button secondary" onClick={() => { setDraft(null); setEditorError(""); }}>取消</button>
        <button className="button primary" onClick={() => void saveDraft()}><Save size={15} />保存计划组</button>
      </div>
    </section>}
    {groups.length === 0 && !draft && <p className="empty-note padded-note">还没有计划组，点击右上角“新建计划组”创建：命名 → 勾选脚本 → 调整顺序 → 保存。</p>}
    <div className="queue-group-list">
      {groups.map((group) => {
        const snapshot = states[group.id];
        const running = snapshot?.status === "running" || snapshot?.status === "stopping";
        const taskId = `runner-queue-${group.id}`;
        const rawLog = props.logs[taskId] ?? "";
        const tail = rawLog.split(/\r?\n/).filter(Boolean).slice(-8).reverse().join("\n");
        return <section className="panel queue-card" key={group.id}>
          <header className="queue-card-header">
            <div className="queue-card-title">
              <strong>{group.name}</strong>
              <span>{group.items.map((item) => planLabel(item.plan)).join(" → ") || "（空）"}</span>
            </div>
            <div className="queue-card-side">
              <span className={`run-state ${running ? "live" : ""}`}>{statusLabel(group)}</span>
              <span className="queue-last-run" title={group.lastRunAt ?? ""}>{group.lastRunAt ? `全部执行 ${formatLastRun(group.lastRunAt)}` : "尚未全部执行"}</span>
              {group.items.filter((item) => (item.cycleDays ?? 0) > 0).map((item) => (
                <span className="queue-last-run queue-cycle-last" key={`cycle-${item.plan}`} title={`每 ${item.cycleDays} 天执行一次`}>
                  {planLabel(item.plan)}：{item.lastRunAt ? formatLastRun(item.lastRunAt) : "未执行"} · 每{item.cycleDays}天
                </span>
              ))}
            </div>
          </header>
          {snapshot && <div className="queue-card-progress">
            {snapshot.results.map((result: QueueItemResult, index: number) => {
              const meta = ITEM_STATUS_META[result.status];
              return <span key={`${result.plan}-${index}`} className={meta.className} title={result.error ?? (result.exitCode != null ? `退出码 ${result.exitCode}` : meta.text)}>{planLabel(result.plan)} · {meta.text}</span>;
            })}
          </div>}
          {snapshot?.error && <p className="queue-error-line">最后错误：{snapshot.error}</p>}
          {tail && <div className="queue-log-bar"><span>最近输出（最新在上）</span><button className="icon-button" title="复制完整日志" aria-label="复制完整日志" disabled={!rawLog} onClick={() => void navigator.clipboard.writeText(newestFirstLog(rawLog))}><Copy size={14} /></button></div>}
          {tail && <pre className="queue-log-preview">{tail}</pre>}
          <footer className="queue-card-actions">
            <select className="queue-device-select" aria-label={`${group.name} 执行设备`} value={groupDevices[group.id] ?? ""} onChange={(event) => updateGroupDevice(group.id, event.target.value)} disabled={running}>
              <option value="">选择设备</option>
              {props.devices.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
            {running
              ? <button className="button danger" onClick={() => void stopGroup(group)}><CircleStop size={15} />停止</button>
              : <button className="button primary" disabled={group.items.length === 0} onClick={() => void startGroup(group)}><Play size={15} />启动</button>}
            <button className="button secondary" onClick={() => openEditor(group)}><Pencil size={15} />编辑</button>
            <button className="icon-button" title="复制计划组" aria-label={`复制 ${group.name}`} onClick={() => void duplicateGroup(group)}><CopyPlus size={15} /></button>
            <button className="icon-button" title="改名" aria-label={`改名 ${group.name}`} onClick={() => setRenameTarget(group)}><PencilLine size={15} /></button>
          </footer>
        </section>;
      })}
    </div>
    {cyclePrompt && <div className="chest-label-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setCyclePrompt(null); }}>
      <section className="chest-label-modal cycle-prompt-modal" role="dialog" aria-modal="true" aria-label="执行周期提醒">
        <header><div><strong>执行周期提醒</strong><span>以下计划未到执行周期</span></div><button className="icon-button" title="关闭" onClick={() => setCyclePrompt(null)}><X size={16} /></button></header>
        <div className="cycle-prompt-list">
          {cyclePrompt.notDue.map((item) => {
            const remainingDays = Math.max(1, Math.ceil(cycleRemainingMs(item) / 86_400_000));
            return <div className="cycle-prompt-item" key={`cycle-prompt-${item.plan}`}>
              <strong>{planLabel(item.plan)}</strong>
              <span>每 {item.cycleDays} 天执行一次 · 上次 {item.lastRunAt ? formatLastRun(item.lastRunAt) : "未执行"} · 还差约 {remainingDays} 天</span>
            </div>;
          })}
        </div>
        <footer>
          <button className="button quiet" onClick={() => setCyclePrompt(null)}>取消</button>
          <button className="button secondary" onClick={() => { const { group, device, notDue } = cyclePrompt; setCyclePrompt(null); void beginGroupStart(group, device, notDue.map((item) => item.plan)); }}>跳过未到期</button>
          <button className="button primary" onClick={() => { const { group, device } = cyclePrompt; setCyclePrompt(null); void beginGroupStart(group, device, []); }}>全部执行</button>
        </footer>
      </section>
    </div>}
    {renameTarget && <PromptDialog title="计划组改名" defaultValue={renameTarget.name} confirmLabel="保存" onConfirm={(value) => void renameGroup(renameTarget, value)} onClose={() => setRenameTarget(null)} />}
  </div>;
}
