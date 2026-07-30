import type { ReactNode } from "react";
import { Bot, RefreshCw, TerminalSquare } from "lucide-react";
import type { EnvironmentState } from "../types";

export function PageHeading({ title, detail, children }: { title: string; detail: string; children?: ReactNode }) {
  return <header className="page-heading"><div><h1>{title}</h1><p>{detail}</p></div><div className="heading-actions">{children}</div></header>;
}

export function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (next: boolean) => void }) {
  return <label className="toggle"><span>{label}</span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><i /></label>;
}

export function LogPanel({ title, text }: { title: string; text: string }) {
  return <section className="panel log-panel"><div className="panel-title"><span>{title}</span><TerminalSquare size={16} /></div><pre className="log-output">{text || "等待任务输出..."}</pre></section>;
}

export function EnvironmentSetup({ state, onRetry, onCancel }: { state: EnvironmentState | null; onRetry: () => void; onCancel: () => void }) {
  const running = state?.phase === "running" || !state;
  const canRetry = state?.phase === "cancelled" || state?.phase === "failed";
  return <main className="environment-shell"><section className="environment-panel"><div className="brand"><Bot size={24} /><span>熊熊乐园小助手</span></div><h1>正在准备自动化环境</h1><p>{state?.message ?? "正在读取环境状态..."}</p><div className="progress-track"><i style={{ width: `${state?.progress ?? 0}%` }} /></div><div className="environment-steps"><span>内置自动化后端</span><span>Android Platform Tools</span><span>ADB 通信验证</span></div>{state?.error ? <pre className="environment-error">{state.error}</pre> : null}<div className="environment-actions">{running ? <button className="button secondary" onClick={onCancel}>取消</button> : null}{canRetry ? <button className="button primary" onClick={onRetry}><RefreshCw size={16} />重新准备</button> : null}</div><small>完成环境检测后，自动化、录制和设备标定功能才会启用。</small></section></main>;
}
