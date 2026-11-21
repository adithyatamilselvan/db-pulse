import { Component, useEffect, useId, useRef, type ErrorInfo, type ReactNode } from "react";
import type { Severity, Source } from "../types";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

// ---------- status / severity / provenance badges ----------
const SEV: Record<string, string> = {
  INFO: "border-info/40 bg-info/10 text-info",
  LOW: "border-ok/40 bg-ok/10 text-ok",
  MEDIUM: "border-warn/40 bg-warn/10 text-warn",
  HIGH: "border-bad/40 bg-bad/10 text-bad",
  CRITICAL: "border-crit/60 bg-crit/15 text-crit",
};
export function Badge({ children, tone, title, testId }: { children: ReactNode; tone?: string; title?: string; testId?: string }) {
  return (
    <span
      title={title}
      data-testid={testId}
      className={cx("inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide", tone ?? "border-line text-muted")}
    >
      {children}
    </span>
  );
}
export const SeverityBadge = ({ severity }: { severity: Severity | string }) => (
  <Badge tone={SEV[severity] ?? SEV.INFO} testId="severity-badge">{severity}</Badge>
);
const STATUS: Record<string, string> = {
  connected: SEV.LOW, open: SEV.HIGH, acknowledged: SEV.MEDIUM, resolved: SEV.LOW,
  disconnected: "border-line text-muted", error: SEV.HIGH, completed: SEV.LOW,
  running: SEV.INFO, queued: SEV.INFO, failed: SEV.HIGH, cancelled: "border-line text-muted",
};
export const StatusBadge = ({ status }: { status: string }) => <Badge tone={STATUS[status]}>{status}</Badge>;
export const SourceBadge = ({ source }: { source: Source | string | undefined }) =>
  source === "SYNTHETIC" ? (
    <Badge tone="border-warn/60 bg-warn/15 text-warn" title="Generated demo record, not a real measurement" testId="synthetic-badge">
      Synthetic
    </Badge>
  ) : null;

// ---------- layout atoms ----------
export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx("card fade-in", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold">{title}</h1>
        {subtitle && <p className="mt-0.5 text-sm text-muted">{subtitle}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2">{actions}</div>
    </div>
  );
}

export function Stat({ label, value, hint, tone, testId }: { label: string; value: ReactNode; hint?: ReactNode; tone?: string; testId?: string }) {
  return (
    <div className="card p-4" data-testid={testId}>
      <div className="text-xs font-medium uppercase tracking-wide text-muted">{label}</div>
      <div className={cx("mt-1 text-2xl font-semibold tabular-nums", tone)}>{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  );
}

// ---------- states ----------
export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <span role="status" aria-label={label} className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-line border-t-accent" />
  );
}
export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 p-10 text-sm text-muted" data-testid="loading">
      <Spinner label={label} /> {label}
    </div>
  );
}
export function EmptyState({ title, hint, action }: { title: string; hint?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 p-10 text-center" data-testid="empty-state">
      <div className="text-sm font-medium">{title}</div>
      {hint && <div className="max-w-md text-sm text-muted">{hint}</div>}
      {action}
    </div>
  );
}
export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-col items-center gap-3 p-8 text-center" data-testid="error-state">
      <div className="text-sm text-bad">{message}</div>
      {onRetry && <button className="btn" onClick={onRetry}>Retry</button>}
    </div>
  );
}
export function Notice({ tone = "info", children, testId }: { tone?: "info" | "warn" | "bad" | "ok"; children: ReactNode; testId?: string }) {
  const t = { info: "border-info/40 bg-info/10", warn: "border-warn/40 bg-warn/10", bad: "border-bad/40 bg-bad/10", ok: "border-ok/40 bg-ok/10" }[tone];
  return (
    <div role={tone === "bad" ? "alert" : "status"} data-testid={testId} className={cx("rounded-md border px-3 py-2 text-sm", t)}>
      {children}
    </div>
  );
}

// ---------- form controls ----------
export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="label">{label}</label>
      <div id={id} role="group" aria-label={label}>{children}</div>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );
}
export function Input({ label, hint, ...p }: React.InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: ReactNode }) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="label">{label}</label>
      <input id={id} className="input" {...p} />
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );
}
export function Select({ label, children, ...p }: React.SelectHTMLAttributes<HTMLSelectElement> & { label: string }) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="label">{label}</label>
      <select id={id} className="input" {...p}>{children}</select>
    </div>
  );
}
export function TextArea({ label, hint, ...p }: React.TextareaHTMLAttributes<HTMLTextAreaElement> & { label: string; hint?: ReactNode }) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="label">{label}</label>
      <textarea id={id} className="input font-mono text-xs" rows={3} spellCheck={false} {...p} />
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );
}
export function Check({ label, ...p }: React.InputHTMLAttributes<HTMLInputElement> & { label: ReactNode }) {
  return (
    <label className="flex cursor-pointer items-start gap-2 text-sm">
      <input type="checkbox" className="mt-0.5 h-4 w-4 accent-[rgb(var(--accent))]" {...p} />
      <span>{label}</span>
    </label>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: string }[]; value: T; onChange: (t: T) => void }) {
  return (
    <div role="tablist" className="mb-4 flex gap-1 border-b border-line">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={cx("-mb-px border-b-2 px-3 py-2 text-sm font-medium", value === t.id ? "border-accent text-accent" : "border-transparent text-muted hover:text-ink")}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

// ---------- modal / confirm ----------
export function Modal({ title, onClose, children, footer, wide }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      prev?.focus?.();
    };
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title} className={cx("card max-h-[90vh] w-full overflow-auto shadow-2xl", wide ? "max-w-3xl" : "max-w-lg")}>
        <header className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2 className="font-semibold">{title}</h2>
          <button aria-label="Close dialog" className="text-muted hover:text-ink" onClick={onClose}>✕</button>
        </header>
        <div className="space-y-4 p-4">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-line px-4 py-3">{footer}</footer>}
      </div>
    </div>
  );
}

export function ConfirmDialog({ title, children, confirmLabel, danger, busy, disabled, onConfirm, onCancel }: {
  title: string; children: ReactNode; confirmLabel: string; danger?: boolean; busy?: boolean; disabled?: boolean; onConfirm: () => void; onCancel: () => void;
}) {
  return (
    <Modal
      title={title}
      onClose={onCancel}
      footer={
        <>
          <button className="btn" onClick={onCancel}>Cancel</button>
          <button data-testid="confirm-action" className={cx("btn", danger ? "btn-danger" : "btn-primary")} disabled={busy || disabled} onClick={onConfirm}>
            {busy && <Spinner />} {confirmLabel}
          </button>
        </>
      }
    >
      {children}
    </Modal>
  );
}

// ---------- table ----------
export function Table({ head, children, testId }: { head: ReactNode; children: ReactNode; testId?: string }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse" data-testid={testId}>
        <thead className="border-b border-line">{head}</thead>
        <tbody className="divide-y divide-line">{children}</tbody>
      </table>
    </div>
  );
}

export function JsonView({ value, maxHeight = "20rem" }: { value: unknown; maxHeight?: string }) {
  return (
    <pre className="overflow-auto rounded-md border border-line bg-bg p-3 font-mono text-xs leading-relaxed" style={{ maxHeight }} data-testid="json-view">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

// ---------- error boundary ----------
interface EBState { error: Error | null }
export class ErrorBoundary extends Component<{ children: ReactNode }, EBState> {
  state: EBState = { error: null };
  static getDerivedStateFromError(error: Error): EBState {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("UI error", error, info.componentStack);
  }
  render() {
    if (this.state.error) {
      return (
        <div className="p-8" role="alert">
          <ErrorState message={`Something went wrong rendering this page: ${this.state.error.message}`} onRetry={() => this.setState({ error: null })} />
        </div>
      );
    }
    return this.props.children;
  }
}
