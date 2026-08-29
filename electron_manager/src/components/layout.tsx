import { useState, type ReactNode } from "react";
import { AlertTriangle, Copy, Download, KeyRound, Power, RefreshCw, TerminalSquare, Trash2, X } from "lucide-react";
import type { UpdatePolicyState, UpdateState } from "../types";

export function PageHeading({ title, detail, children }: { title: string; detail: string; children?: ReactNode }) {
  return <header className="page-heading"><div><h1>{title}</h1><p>{detail}</p></div><div className="heading-actions">{children}</div></header>;
}

export function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (next: boolean) => void }) {
  return <label className="toggle"><span>{label}</span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><i /></label>;
}

export function LogActions({ text, showRealtimeLogs, onToggleRealtimeLogs, onCopy, onClear }: { text: string; showRealtimeLogs: boolean; onToggleRealtimeLogs: (next: boolean) => void; onCopy?: () => void; onClear?: () => void }) {
  return <span className="log-actions">
    <Toggle label="实时输出" checked={showRealtimeLogs} onChange={onToggleRealtimeLogs} />
    <button className="icon-button" title="复制日志" aria-label="复制日志" disabled={!text} onClick={() => { if (onCopy) onCopy(); else void navigator.clipboard.writeText(text); }}><Copy size={15} /></button>
    <button className="icon-button" title="清空日志" aria-label="清空日志" disabled={!text} onClick={onClear}><Trash2 size={15} /></button>
  </span>;
}

export function LogPanel({ title, text, actions }: { title: string; text: string; actions?: ReactNode }) {
  return <section className="panel log-panel"><div className="panel-title"><span>{title}</span><span className="panel-title-actions">{actions}<TerminalSquare size={16} /></span></div><pre className="log-output">{text || "等待任务输出..."}</pre></section>;
}

export function LicenseActivationDialog({ onActivate, onClose, error }: { onActivate: (code: string) => Promise<boolean>; onClose: () => void; error?: string }) {
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!code.trim() || submitting) return;
    setSubmitting(true);
    try {
      if (await onActivate(code)) onClose();
    } finally {
      setSubmitting(false);
    }
  };
  return <div className="license-modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="license-activation-title">
    <form className="license-modal" onSubmit={(event) => void submit(event)}>
      <header><div><span className="eyebrow">专业版</span><h2 id="license-activation-title">激活专业版</h2></div><button type="button" className="icon-button" title="关闭" onClick={onClose}><X size={17} /></button></header>
      <label>激活码<textarea autoFocus value={code} placeholder="粘贴激活码" onChange={(event) => setCode(event.target.value)} /></label>
      {error && <p className="license-error" role="alert">{error}</p>}
      <footer><button type="button" className="button secondary" onClick={onClose}>取消</button><button className="button primary" disabled={!code.trim() || submitting} type="submit"><KeyRound size={16} />{submitting ? "正在激活" : "激活专业版"}</button></footer>
    </form>
  </div>;
}

export function UpdateRequiredDialog({
  policy,
  update,
  isPro,
  countdownMs,
  onCheck,
  onDownload,
  onInstall,
  onAcknowledge,
  onQuit,
}: {
  policy: UpdatePolicyState;
  update: UpdateState | null;
  isPro: boolean;
  countdownMs: number;
  onCheck: () => Promise<void>;
  onDownload: () => Promise<void>;
  onInstall: () => Promise<void>;
  onAcknowledge: () => Promise<void>;
  onQuit: () => Promise<void>;
}) {
  const formatDuration = (ms: number) => {
    const totalSeconds = Math.max(1, Math.round(ms / 1000));
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    if (minutes === 0) return `${totalSeconds} 秒`;
    if (seconds === 0) return `${minutes} 分钟`;
    return `${minutes} 分 ${seconds} 秒`;
  };
  const phase = update?.phase;
  const canDownload = phase === "available";
  const canInstall = phase === "downloaded";
  const countdownSeconds = Math.max(0, Math.ceil(countdownMs / 1000));
  const canContinue = countdownSeconds === 0;
  const minutes = Math.floor(countdownSeconds / 60);
  const seconds = countdownSeconds % 60;
  const countdownLabel = `${String(minutes)}:${String(seconds).padStart(2, "0")}`;
  const countdownWindowMs = policy.prompt?.countdownMs ?? 60_000;
  const snoozeMs = policy.prompt?.snoozeMs ?? 30 * 60_000;
  return <div className="license-modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="update-required-title">
    <section className="license-modal">
      <header><div><span className="eyebrow">强制更新</span><h2 id="update-required-title">当前版本需要更新</h2></div><AlertTriangle size={20} /></header>
      <p>{policy.message}</p>
      <p className="field-note">当前版本：{policy.currentVersion} · 渠道：{policy.channel}{policy.latestVersion ? ` · 最新：${policy.latestVersion}` : ""}{policy.minVersion ? ` · 最低可运行：${policy.minVersion}` : ""}</p>
      <p className="field-note">首次等待 {formatDuration(countdownWindowMs)} 后可继续，临时放行 {formatDuration(snoozeMs)} 后会再次弹出。</p>
      <p className="field-note">{isPro ? "专业版仅提示，不会停止脚本。" : "免费版会在此期间暂停脚本。"} {canContinue ? "现在可以继续临时使用。" : `还需等待 ${countdownLabel} 才能手动关闭。`}</p>
      {update?.releaseNotes ? <pre className="command-result">{update.releaseNotes}</pre> : null}
      <footer>
        <button type="button" className="button secondary" onClick={() => void onQuit()}><Power size={16} />退出应用</button>
        <button type="button" className="button secondary" onClick={() => void onCheck()}><RefreshCw size={16} />重新检查</button>
        {canDownload ? <button type="button" className="button primary" onClick={() => void onDownload()}><Download size={16} />下载更新</button> : null}
        {canInstall ? <button type="button" className="button primary" onClick={() => void onInstall()}><RefreshCw size={16} />重启安装</button> : null}
        <button type="button" className="button primary" disabled={!canContinue} onClick={() => void onAcknowledge()}><RefreshCw size={16} />{canContinue ? `继续使用 ${snoozeMinutes} 分钟` : countdownLabel}</button>
      </footer>
    </section>
  </div>;
}

export function UpdateCheckingDialog({
  policy,
  onCheck,
  onDismiss,
}: {
  policy: UpdatePolicyState;
  onCheck: () => Promise<void>;
  onDismiss: () => void;
}) {
  return <div className="license-modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="update-checking-title">
    <section className="license-modal">
      <header><div><span className="eyebrow">更新检测</span><h2 id="update-checking-title">正在检测更新</h2></div><RefreshCw size={20} /></header>
      <p>{policy.message || "正在检测更新，请稍候。"}</p>
      <p className="field-note">网络恢复后会继续检查；如果检测到新版本，会按正常流程提示升级。</p>
      <footer>
        <button type="button" className="button secondary" onClick={() => void onDismiss()}>继续使用</button>
        <button type="button" className="button primary" onClick={() => void onCheck()}><RefreshCw size={16} />重新检测</button>
      </footer>
    </section>
  </div>;
}
