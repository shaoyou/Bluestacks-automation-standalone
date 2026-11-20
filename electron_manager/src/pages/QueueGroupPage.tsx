import { useCallback, useEffect, useState } from "react";
import { ArrowDown, ArrowUp, CircleStop, Layers, Pencil, Play, Plus, RefreshCw, Save, Trash2, X } from "lucide-react";
import { PageHeading, Toggle } from "../components/layout";
import type { QueueGroup, QueueGroupItem, QueueItemResult, QueueItemStatus, QueueRunSnapshot } from "../types";
import type { SharedProps } from "../app/shared";

type DraftItem = { plan: string; minutesText: string };
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

function formatMinutes(seconds: number) {
  const minutes = seconds / 60;
  return Number.isInteger(minutes) ? String(minutes) : String(Math.round(minutes * 100) / 100);
}

type PlanMeta = { maxRuntimeSec: number; unbounded: boolean };

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
      return { plan: item.plan, minutesText: item.maxRuntimeSec > 0 ? formatMinutes(item.maxRuntimeSec) : planDefault > 0 ? formatMinutes(planDefault) : "" };
    }),
    onError: group.onError,
    repeat: group.repeat,
  };
}

export function QueueGroupPage(props: SharedProps) {
  const [groups, setGroups] = useState<QueueGroup[]>([]);
  const [states, setStates] = useState<Record<string, QueueRunSnapshot>>({});
  const [planMeta, setPlanMeta] = useState<Record<string, PlanMeta>>({});
  const [device, setDevice] = useState(() => window.localStorage.getItem("queue-device") ?? "");
  const [draft, setDraft] = useState<QueueGroupDraft | null>(null);
  const [editorError, setEditorError] = useState("");

  useEffect(() => { window.localStorage.setItem("queue-device", device); }, [device]);

  useEffect(() => {
    void window.bsManager.queueGroupsList().then(setGroups).catch((error) => props.setNotice(`读取计划组失败: ${String(error)}`));
    void window.bsManager.queueStates().then((snapshots) => {
      setStates(Object.fromEntries(snapshots.map((snapshot) => [snapshot.groupId, snapshot])));
    }).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => window.bsManager.onQueueEvent((event) => {
    setStates((current) => ({ ...current, [event.groupId]: event }));
  }), []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const entries = await Promise.all(props.plans.map(async (name) => {
        try {
          const plan = JSON.parse(await window.bsManager.plansRead(name)) as { max_runtime_sec?: unknown; actions?: unknown };
          const seconds = Number(plan.max_runtime_sec ?? 0);
          return [name, { maxRuntimeSec: Number.isFinite(seconds) && seconds > 0 ? seconds : 0, unbounded: planHasUnboundedLoop(plan.actions) }] as const;
        } catch { return [name, { maxRuntimeSec: 0, unbounded: false }] as const; }
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
    setDraft((current) => {
      if (!current || current.items.some((item) => item.plan === plan)) return current;
      return { ...current, items: [...current.items, { plan, minutesText }] };
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
      items.push({ plan: item.plan, maxRuntimeSec: seconds, enabled: true });
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

  const startGroup = async (group: QueueGroup) => {
    if (!props.runtime) return props.setNotice("运行环境尚未加载完成");
    if (!device.trim()) return props.setNotice("请先选择设备");
    const taskId = `runner-queue-${group.id}`;
    props.clearTaskLog(taskId);
    try {
      await window.bsManager.queueStart(group.id, device.trim());
      props.setNotice(`计划组「${group.name}」开始执行`);
    } catch (error) {
      props.setNotice(`启动失败: ${String(error)}`);
    }
  };

  const stopGroup = async (group: QueueGroup) => {
    try {
      await window.bsManager.queueStop(group.id);
      props.setNotice("正在停止计划组…");
    } catch (error) {
      props.setNotice(`停止失败: ${String(error)}`);
    }
  };

  const removeGroup = async (group: QueueGroup) => {
    if (!window.confirm(`删除计划组「${group.name}」？`)) return;
    try {
      await window.bsManager.queueGroupsDelete(group.id);
      await refresh();
      props.setNotice("计划组已删除");
    } catch (error) {
      props.setNotice(`删除失败: ${String(error)}`);
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
    <PageHeading title="计划组" detail="把多个脚本组成串行队列：按顺序自动执行，可调整顺序、限制每项时长、失败跳过或整队循环。">
      <button className="button secondary" onClick={() => void props.refreshDevices()}><RefreshCw size={16} />刷新设备</button>
      <button className="button primary" onClick={() => openEditor(null)}><Plus size={16} />新建计划组</button>
    </PageHeading>
    <section className="device-strip panel">
      <Layers size={18} />
      <strong>执行设备</strong>
      <select aria-label="计划组执行设备" value={device} onChange={(event) => setDevice(event.target.value)}>
        <option value="">选择设备</option>
        {props.devices.map((name) => <option key={name} value={name}>{name}</option>)}
      </select>
      <span>{props.devices.join("  ·  ") || "请检查 ADB 路径、模拟器或 USB 调试连接"}</span>
    </section>
    {draft && <section className="panel queue-editor">
      <div className="panel-title"><span>{draft.id ? "编辑计划组" : "新建计划组"}</span></div>
      <label className="queue-name-row">名称<input value={draft.name} placeholder="例如：日常挂机" onChange={(event) => { setEditorError(""); setDraft({ ...draft, name: event.target.value }); }} /></label>
      <div className="queue-editor-columns">
        <div className="queue-selected">
          <div className="panel-title"><span>已选脚本（按此顺序执行）</span><span className="counter">{draft.items.length}</span></div>
          <p className="empty-note queue-duration-note">时长留空 = 跑完为止（会自己结束的脚本）；标 ∞ 的脚本会无限运行，必须填时长，或放在最后并关闭整队循环。</p>
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
        const tail = rawLog.split(/\r?\n/).filter(Boolean).slice(-8).join("\n");
        return <section className="panel queue-card" key={group.id}>
          <header className="queue-card-header">
            <div className="queue-card-title">
              <strong>{group.name}</strong>
              <span>{group.items.map((item) => planLabel(item.plan)).join(" → ") || "（空）"}</span>
            </div>
            <span className={`run-state ${running ? "live" : ""}`}>{statusLabel(group)}</span>
          </header>
          {snapshot && <div className="queue-card-progress">
            {snapshot.results.map((result: QueueItemResult, index: number) => {
              const meta = ITEM_STATUS_META[result.status];
              return <span key={`${result.plan}-${index}`} className={meta.className} title={result.error ?? (result.exitCode != null ? `退出码 ${result.exitCode}` : meta.text)}>{planLabel(result.plan)} · {meta.text}</span>;
            })}
          </div>}
          {snapshot?.error && <p className="queue-error-line">最后错误：{snapshot.error}</p>}
          {running && tail && <pre className="queue-log-preview">{tail}</pre>}
          <footer className="queue-card-actions">
            {running
              ? <button className="button danger" onClick={() => void stopGroup(group)}><CircleStop size={15} />停止</button>
              : <button className="button primary" disabled={group.items.length === 0} onClick={() => void startGroup(group)}><Play size={15} />启动</button>}
            <button className="button secondary" onClick={() => openEditor(group)}><Pencil size={15} />编辑</button>
            <button className="button quiet danger" onClick={() => void removeGroup(group)}><Trash2 size={15} />删除</button>
          </footer>
        </section>;
      })}
    </div>
  </div>;
}
