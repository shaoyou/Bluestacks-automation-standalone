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
  onCheck,
  onDownload,
  onInstall,
  onQuit,
}: {
  policy: UpdatePolicyState;
  update: UpdateState | null;
  onCheck: () => Promise<void>;
  onDownload: () => Promise<void>;
  onInstall: () => Promise<void>;
  onQuit: () => Promise<void>;
}) {
  const phase = update?.phase;
  const canDownload = phase === "available";
  const canInstall = phase === "downloaded";
  return <div className="license-modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="update-required-title">
    <section className="license-modal">
      <header><div><span className="eyebrow">强制更新</span><h2 id="update-required-title">当前版本需要更新</h2></div><AlertTriangle size={20} /></header>
      <p>{policy.message}</p>
      <p className="field-note">当前版本：{policy.currentVersion} · 渠道：{policy.channel}{policy.latestVersion ? ` · 最新：${policy.latestVersion}` : ""}{policy.minVersion ? ` · 最低可运行：${policy.minVersion}` : ""}</p>
      {update?.releaseNotes ? <pre className="command-result">{update.releaseNotes}</pre> : null}
      <footer>
        <button type="button" className="button secondary" onClick={() => void onQuit()}><Power size={16} />退出应用</button>
        <button type="button" className="button secondary" onClick={() => void onCheck()}><RefreshCw size={16} />重新检查</button>
        {canDownload ? <button type="button" className="button primary" onClick={() => void onDownload()}><Download size={16} />下载更新</button> : null}
        {canInstall ? <button type="button" className="button primary" onClick={() => void onInstall()}><RefreshCw size={16} />重启安装</button> : null}
      </footer>
    </section>
  </div>;
}
