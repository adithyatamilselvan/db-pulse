import { Link } from "react-router-dom";
import { useApp } from "../hooks/AppContext";
import { Card, EmptyState, Notice, PageHeader, Select, SeverityBadge } from "./ui";
import { PlanTree } from "../charts/PlanTree";
import { num, ms } from "../utils/format";
import type { Finding, PlanAnalysis, Recommendation } from "../types";

export const PERIODS = [
  { id: "15m", label: "Last 15 minutes" },
  { id: "1h", label: "Last hour" },
  { id: "24h", label: "Last 24 hours" },
  { id: "7d", label: "Last 7 days" },
  { id: "30d", label: "Last 30 days" },
  { id: "custom", label: "Custom range" },
];

export interface PeriodValue { period: string; start: string; end: string }

export function PeriodSelect({ value, onChange }: { value: PeriodValue; onChange: (v: PeriodValue) => void }) {
  return (
    <div className="flex flex-wrap items-end gap-2">
      <div className="w-44">
        <Select label="Period" value={value.period} onChange={(e) => onChange({ ...value, period: e.target.value })} data-testid="period-select">
          {PERIODS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </Select>
      </div>
      {value.period === "custom" && (
        <>
          <div>
            <label className="label" htmlFor="p-start">From</label>
            <input id="p-start" type="datetime-local" className="input" value={value.start} onChange={(e) => onChange({ ...value, start: e.target.value })} />
          </div>
          <div>
            <label className="label" htmlFor="p-end">To</label>
            <input id="p-end" type="datetime-local" className="input" value={value.end} onChange={(e) => onChange({ ...value, end: e.target.value })} />
          </div>
        </>
      )}
    </div>
  );
}

/** Translate a PeriodValue into API query params (custom ranges -> ISO). */
export function periodParams(p: PeriodValue): Record<string, string | undefined> {
  if (p.period === "custom" && p.start && p.end) {
    return { period: "custom", start: new Date(p.start).toISOString(), end: new Date(p.end).toISOString() };
  }
  return { period: p.period === "custom" ? "24h" : p.period };
}

export const defaultPeriod = (): PeriodValue => ({ period: "24h", start: "", end: "" });

export function NoInstance({ title }: { title?: string }) {
  const { instances } = useApp();
  return (
    <>
      {title && <PageHeader title={title} />}
    <Card>
      <EmptyState
        title={instances.length ? "Select a database instance" : "No database instance registered yet"}
        hint="DB Pulse investigates MongoDB instances you register. You can also load the built-in demo from the Dashboard."
        action={<Link to="/explorer" className="btn btn-primary">Open Database explorer</Link>}
      />
    </Card>
    </>
  );
}

export function FindingsList({ findings }: { findings: Finding[] }) {
  if (!findings.length) return <p className="text-sm text-muted">No findings.</p>;
  return (
    <ul className="space-y-2" data-testid="findings">
      {findings.map((f, i) => (
        <li key={i} className="flex items-start gap-2 text-sm">
          <SeverityBadge severity={f.severity} />
          <span>
            {f.message}
            <span className="ml-2 text-xs text-muted">heuristic · {f.code}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

export function PlanMetrics({ a }: { a: PlanAnalysis }) {
  const items: [string, string][] = [
    ["Documents examined", num(a.documents_examined)],
    ["Documents returned", num(a.documents_returned)],
    ["Keys examined", num(a.keys_examined)],
    ["Explain time", ms(a.execution_time_ms)],
    ["Examined / returned", a.efficiency_ratio === null ? "—" : a.efficiency_ratio.toFixed(1)],
    ["Engine", a.engine ?? "—"],
  ];
  return (
    <dl className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6" data-testid="plan-metrics">
      {items.map(([k, v]) => (
        <div key={k} className="rounded-md border border-line bg-raised px-3 py-2">
          <dt className="text-[11px] uppercase tracking-wide text-muted">{k}</dt>
          <dd className="text-lg font-semibold tabular-nums">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function PlanView({ a, title }: { a: PlanAnalysis; title?: string }) {
  return (
    <div className="space-y-4" data-testid="plan-view">
      {title && <h3 className="text-sm font-semibold">{title}</h3>}
      <PlanMetrics a={a} />
      {!a.has_execution_stats && <Notice tone="warn">Only queryPlanner output was available: examined/returned figures are unavailable.</Notice>}
      <div className="grid gap-4 lg:grid-cols-2">
        <div><h4 className="label">Plan stages</h4><PlanTree tree={a.tree} /></div>
        <div><h4 className="label">Findings (heuristic)</h4><FindingsList findings={a.findings} />
          {a.rejected_plans.length > 0 && (
            <div className="mt-3"><h4 className="label">Rejected plans</h4>
              <ul className="text-xs text-muted">{a.rejected_plans.map((r, i) => <li key={i} className="font-mono">{r.stages.join(" > ") || "?"}</li>)}</ul>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function RecommendationCard({ rec, onTest }: { rec: Recommendation; onTest?: () => void }) {
  if (rec.action === "none") {
    return (
      <Notice tone="info" testId="recommendation">
        <b>No index change recommended.</b> {rec.reason}
        {rec.notes.length > 0 && <ul className="mt-1 list-disc pl-5 text-xs">{rec.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
      </Notice>
    );
  }
  return (
    <div className="space-y-3 rounded-md border border-accent/40 bg-accent/5 p-4" data-testid="recommendation">
      <div className="flex flex-wrap items-center gap-2">
        <b>Suggested index</b>
        <span className="rounded border border-warn/50 bg-warn/10 px-1.5 py-0.5 text-[11px] font-semibold uppercase text-warn">Unverified hypothesis</span>
        <span className="text-xs text-muted">confidence {rec.confidence}</span>
      </div>
      <code className="block overflow-auto rounded bg-bg p-2 font-mono text-sm" data-testid="suggested-index">{rec.suggested_index?.command}</code>
      <dl className="grid gap-3 text-sm md:grid-cols-2">
        <div><dt className="label">Problem detected</dt><dd>{rec.problem}</dd></div>
        <div><dt className="label">Supporting evidence</dt><dd><ul className="list-disc pl-5">{rec.supporting_evidence?.map((e, i) => <li key={i}>{e}</li>)}</ul></dd></div>
        <div><dt className="label">Existing indexes</dt><dd className="font-mono text-xs">{rec.existing_indexes.map((i) => `${i.name} ${JSON.stringify(i.key)}`).join(" · ")}</dd></div>
        <div><dt className="label">Expected benefit</dt><dd className="text-muted">{rec.expected_benefit}</dd></div>
        <div><dt className="label">Costs</dt><dd><ul className="list-disc pl-5">{rec.costs?.map((e, i) => <li key={i}>{e}</li>)}</ul></dd></div>
        <div><dt className="label">Validation steps</dt><dd><ol className="list-decimal pl-5">{rec.validation_steps?.map((e, i) => <li key={i}>{e}</li>)}</ol></dd></div>
      </dl>
      {rec.notes.length > 0 && <ul className="list-disc pl-5 text-xs text-muted">{rec.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
      {onTest && <button className="btn btn-primary" onClick={onTest}>Test this index in the Benchmark laboratory</button>}
    </div>
  );
}
