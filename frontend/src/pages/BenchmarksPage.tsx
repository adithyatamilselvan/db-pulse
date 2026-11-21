import { useEffect, useRef, useState, type FormEvent } from "react";
import { useLocation } from "react-router-dom";
import { PlotChart } from "../charts/PlotChart";
import { NoInstance, PlanView } from "../components/shared";
import { PlanTree } from "../charts/PlanTree";
import { Badge, Card, Check, EmptyState, Input, LoadingState, Notice, PageHeader, Select, SourceBadge, Spinner, StatusBadge, Table, TextArea } from "../components/ui";
import { useApp } from "../hooks/AppContext";
import { useAction, useAsync } from "../hooks/useAsync";
import { api } from "../services/api";
import type { Benchmark, CollectionInfo, Comparison, DbInfo, ExecutionPlan } from "../types";
import { dateTime, ms, num, parseJson, pct } from "../utils/format";

const VERDICT_TONE = { IMPROVED: "ok", REGRESSED: "bad", UNCHANGED: "info", NOT_VALIDATED: "warn", INCONCLUSIVE: "info" } as const;

function BenchResult({ b }: { b: Benchmark }) {
  const r = b.result;
  if (!r) return <p className="text-sm text-muted">No result{b.error ? `: ${b.error}` : "."}</p>;
  const l = r.latency_ms;
  const rows: [string, string][] = [["Average", ms(l.avg)], ["Median", ms(l.median)], ["P95", ms(l.p95)], ["P99", ms(l.p99)], ["Min", ms(l.min)], ["Max", ms(l.max)],
    ["Throughput", r.throughput_ops === null ? "—" : `${r.throughput_ops.toFixed(1)} ops/s`], ["Successful", num(r.successful)], ["Failed", num(r.failed)],
    ["Docs examined", num(r.docs_examined)], ["Keys examined", num(r.keys_examined)], ["Docs returned", num(r.docs_returned)]];
  return (
    <div className="space-y-4" data-testid="bench-result">
      <dl className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
        {rows.map(([k, v]) => <div key={k} className="rounded-md border border-line bg-raised px-3 py-2"><dt className="text-[11px] uppercase tracking-wide text-muted">{k}</dt><dd className="text-lg font-semibold tabular-nums">{v}</dd></div>)}
      </dl>
      {r.errors.length > 0 && <Notice tone="bad">Errors: {r.errors.map((e) => `${e.message} (×${e.count})`).join("; ")}</Notice>}
      <div className="grid gap-4 lg:grid-cols-2">
        <PlotChart height={240} testId="latency-chart" data={[{ y: r.latency_samples_ms, type: "scatter", mode: "lines", name: "Latency (ms)" }]} layout={{ xaxis: { title: { text: "operation #" } }, yaxis: { title: { text: "ms" } } }} />
        <PlotChart height={240} data={[{ x: r.histogram.edges.slice(0, -1).map((e, i) => (e + (r.histogram.edges[i + 1] ?? e)) / 2), y: r.histogram.counts, type: "bar", name: "Operations" }]} layout={{ xaxis: { title: { text: "latency (ms)" } }, yaxis: { title: { text: "count" } } }} />
      </div>
      {r.plan && <PlanView a={r.plan} title="Execution plan (explain after the measured phase)" />}
      <p className="text-xs text-muted">{b.methodology}</p>
    </div>
  );
}

function CompareView({ c, before, after }: { c: Comparison; before: Benchmark; after: Benchmark }) {
  const tone = VERDICT_TONE[c.verdict];
  const lat = c.rows.filter((r) => ["avg", "median", "p95", "p99"].includes(r.key));
  return (
    <div className="space-y-4" data-testid="comparison">
      <Notice tone={tone} testId="verdict"><b data-testid="verdict-text">{c.verdict.replace("_", " ")}</b> — {c.summary}</Notice>
      <Table testId="comparison-table" head={<tr>{["Metric", `Before (${c.before_label})`, `After (${c.after_label})`, "Improvement", ""].map((h) => <th key={h} className="th">{h}</th>)}</tr>}>
        {c.rows.map((r) => (
          <tr key={r.key}><td className="td">{r.label}</td>
            <td className="td tabular-nums">{r.before === null ? "—" : num(r.before, 2)} {r.unit}</td><td className="td tabular-nums">{r.after === null ? "—" : num(r.after, 2)} {r.unit}</td>
            <td className={`td tabular-nums ${r.direction === "improved" ? "text-ok" : r.direction === "regressed" ? "text-bad" : ""}`}>{pct(r.change_pct, true)}</td>
            <td className="td text-xs text-muted">{r.direction}</td></tr>
        ))}
      </Table>
      <p className="text-xs text-muted">Improvement is positive when the metric got better: a latency <i>reduction</i> and a throughput <i>increase</i> are both positive. They are different quantities.</p>
      <PlotChart height={280} testId="compare-chart" data={[
        { x: lat.map((r) => r.label), y: lat.map((r) => r.before), type: "bar", name: c.before_label },
        { x: lat.map((r) => r.label), y: lat.map((r) => r.after), type: "bar", name: c.after_label },
      ]} layout={{ barmode: "group", yaxis: { title: { text: "ms" } } }} />
      <div className="grid gap-4 lg:grid-cols-2">
        <div><h4 className="label">Before: {c.before_label}</h4><PlanTree tree={before.result?.plan?.tree ?? null} /></div>
        <div><h4 className="label">After: {c.after_label}</h4><PlanTree tree={after.result?.plan?.tree ?? null} /></div>
      </div>
      <ul className="list-disc pl-5 text-xs text-muted">{c.caveats.map((x, i) => <li key={i}>{x}</li>)}</ul>
    </div>
  );
}

export default function BenchmarksPage() {
  const { instanceId, instance } = useApp();
  const loc = useLocation();
  const prefill = (loc.state as { prefill?: { plan?: ExecutionPlan } } | null)?.prefill?.plan;
  const [db, setDb] = useState(prefill?.database ?? "");
  const [coll, setColl] = useState(prefill?.collection ?? "");
  const [filter, setFilter] = useState(prefill?.spec_redacted?.filter ? JSON.stringify(prefill.spec_redacted.filter) : '{ "customer_id": 1001, "status": "COMPLETED" }');
  const [sort, setSort] = useState("");
  const [op, setOp] = useState("find");
  const [limit, setLimit] = useState("");
  const [label, setLabel] = useState("baseline");
  const [warmup, setWarmup] = useState(5);
  const [iterations, setIterations] = useState(100);
  const [concurrency, setConcurrency] = useState(1);
  const [duration, setDuration] = useState("");
  const [confirmHigh, setConfirmHigh] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [picks, setPicks] = useState<string[]>([]);
  const [comparison, setComparison] = useState<{ c: Comparison; before: Benchmark; after: Benchmark } | null>(null);
  const start = useAction();
  const cmp = useAction();
  const cancel = useAction();

  const dbs = useAsync(() => (instanceId && instance?.enabled ? api.get<{ databases: DbInfo[] }>(`/databases/${instanceId}/explorer`) : null), [instanceId, instance?.enabled]);
  const colls = useAsync(() => (instanceId && db ? api.get<{ collections: CollectionInfo[] }>(`/databases/${instanceId}/explorer/${db}/collections`) : null), [instanceId, db]);
  const list = useAsync(() => (instanceId ? api.get<{ items: Benchmark[] }>("/benchmarks", { instance_id: instanceId, limit: 30 }) : null), [instanceId]);
  const active = useAsync(() => (activeId ? api.get<{ benchmark: Benchmark }>(`/benchmarks/${activeId}`) : null), [activeId]);
  const running = active.data?.benchmark.status === "running" || active.data?.benchmark.status === "queued";

  const reloadActive = active.reload;
  const reloadList = list.reload;
  useEffect(() => {
    if (!running) return;
    const t = setInterval(reloadActive, 700);
    return () => clearInterval(t);
  }, [running, reloadActive]);
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) reloadList();
    wasRunning.current = running;
  }, [running, reloadList]);

  if (!instanceId) return <NoInstance title="Benchmark laboratory" />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setFormError(null);
    let body: Record<string, unknown>;
    try {
      body = { database: db, collection: coll, operation: op, filter: parseJson(filter, "Filter"), sort: parseJson(sort, "Sort"), limit: limit ? Number(limit) : undefined,
        label, warmup_iterations: warmup, iterations, concurrency, duration_s: duration ? Number(duration) : undefined, confirm_high_load: confirmHigh };
    } catch (err) { setFormError(err instanceof Error ? err.message : "Invalid input"); return; }
    const r = await start.run(() => api.post<{ benchmark: Benchmark }>(`/benchmarks/${instanceId}/run`, body));
    if (r) { setActiveId(r.benchmark.id); setComparison(null); list.reload(); }
  };
  const compare = async () => {
    const [b, a] = picks;
    const r = await cmp.run(async () => {
      const res = await api.post<{ comparison: Comparison }>("/benchmarks/compare", { before_id: b, after_id: a });
      const detail = await api.get<{ comparison: Comparison; before: Benchmark; after: Benchmark }>(`/benchmarks/comparisons/${res.comparison.id}`);
      return { c: detail.comparison, before: detail.before, after: detail.after };
    });
    if (r) setComparison(r);
  };
  const toggle = (id: string) => setPicks((p) => p.includes(id) ? p.filter((x) => x !== id) : p.length >= 2 ? [p[1]!, id] : [...p, id]);
  const b = active.data?.benchmark;
  const highLoad = iterations > 2000 || concurrency > 8 || Number(duration || 0) > 60;

  return (
    <>
      <PageHeader title="Benchmark laboratory" subtitle="Controlled, read-only benchmarks. Every number is measured on this run; nothing is estimated. Results vary with cache state, load, hardware and data." />
      <div className="grid gap-5 xl:grid-cols-3">
        <Card title="Configure benchmark">
          <form onSubmit={submit} className="space-y-3" data-testid="bench-form">
            {prefill && <Notice tone="info">Prefilled from an investigation. The filter shows the query <i>shape</i>: replace “?” with real values.</Notice>}
            <Select label="Database" value={db} onChange={(e) => { setDb(e.target.value); setColl(""); }} required data-testid="db-select">
              <option value="">Select…</option>{dbs.data?.databases.map((d) => <option key={d.name}>{d.name}</option>)}</Select>
            <Select label="Collection" value={coll} onChange={(e) => setColl(e.target.value)} required disabled={!db} data-testid="coll-select">
              <option value="">Select…</option>{colls.data?.collections.filter((c) => c.type === "collection").map((c) => <option key={c.name}>{c.name}</option>)}</Select>
            <Select label="Operation" value={op} onChange={(e) => setOp(e.target.value)}><option>find</option><option>count</option></Select>
            <TextArea label="Filter (JSON)" value={filter} onChange={(e) => setFilter(e.target.value)} data-testid="bench-filter" />
            {op === "find" && <><TextArea label="Sort (JSON, optional)" value={sort} onChange={(e) => setSort(e.target.value)} rows={2} /><Input label="Limit (default cap 10,000)" type="number" min={1} value={limit} onChange={(e) => setLimit(e.target.value)} /></>}
            <Input label="Label" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={60} data-testid="bench-label" hint="e.g. baseline, with-index" />
            <div className="grid grid-cols-3 gap-2">
              <Input label="Warm-up" type="number" min={0} value={warmup} onChange={(e) => setWarmup(Number(e.target.value))} />
              <Input label="Iterations" type="number" min={1} value={iterations} onChange={(e) => setIterations(Number(e.target.value))} data-testid="bench-iterations" />
              <Input label="Concurrency" type="number" min={1} value={concurrency} onChange={(e) => setConcurrency(Number(e.target.value))} />
            </div>
            <Input label="Duration (s, optional — overrides iterations)" type="number" min={1} value={duration} onChange={(e) => setDuration(e.target.value)} />
            <Notice tone="info">Target: <b className="font-mono">{db || "?"}.{coll || "?"}</b> on <b>{instance?.name}</b> (read-only operations only).</Notice>
            {highLoad && <Check label={<><b>High load:</b> I confirm this heavier workload may affect the monitored database.</>} checked={confirmHigh} onChange={(e) => setConfirmHigh(e.target.checked)} data-testid="confirm-high-load" />}
            {formError && <Notice tone="bad">{formError}</Notice>}
            {start.error && <Notice tone="bad" testId="bench-error">{start.error}</Notice>}
            <button className="btn btn-primary w-full" disabled={start.busy || running || !coll} data-testid="run-benchmark">{(start.busy || running) && <Spinner />} Run benchmark</button>
          </form>
        </Card>

        <div className="space-y-5 xl:col-span-2">
          {b && (
            <Card title={<span className="flex items-center gap-2">{b.label} <StatusBadge status={b.status} /> <SourceBadge source={b.source} /></span>}
              actions={running && <button className="btn btn-danger" disabled={cancel.busy} onClick={() => cancel.run(() => api.post(`/benchmarks/${b.id}/cancel`))} data-testid="cancel-benchmark">Cancel</button>}>
              {running ? (
                <div className="space-y-2" data-testid="bench-progress">
                  <div className="h-2 overflow-hidden rounded bg-raised"><div className="h-full bg-accent transition-all" style={{ width: `${Math.min(100, ((b.progress?.completed ?? 0) / (b.target_iterations ?? 1)) * 100)}%` }} /></div>
                  <p className="text-sm text-muted">{num(b.progress?.completed ?? 0)} completed · {num(b.progress?.failed ?? 0)} failed</p>
                </div>
              ) : <BenchResult b={b} />}
              {b.warnings.map((w, i) => <p key={i} className="mt-2 text-xs text-warn">{w}</p>)}
            </Card>
          )}
          <Card title="Benchmark history" actions={<button className="btn btn-primary" disabled={picks.length !== 2 || cmp.busy} onClick={compare} data-testid="compare-btn">{cmp.busy && <Spinner />} Compare selected (before → after)</button>}>
            {list.loading ? <LoadingState /> : !list.data?.items.length ? <EmptyState title="No benchmarks yet" hint="Run a baseline, make an authorized optimization (Index investigator), run again, then compare." /> : (
              <>
                <p className="mb-2 text-xs text-muted">Tick two completed runs: the first ticked is “before”, the second “after”.</p>
                <Table testId="bench-history" head={<tr>{["", "Label", "Target", "Status", "P95", "Throughput", "Docs examined", "When"].map((h) => <th key={h} className="th">{h}</th>)}</tr>}>
                  {list.data.items.map((x) => (
                    <tr key={x.id} className="hover:bg-raised">
                      <td className="td"><input type="checkbox" aria-label={`Select ${x.label}`} disabled={x.status !== "completed"} checked={picks.includes(x.id)} onChange={() => toggle(x.id)} /></td>
                      <td className="td"><button className="text-accent hover:underline" onClick={() => { setActiveId(x.id); setComparison(null); }}>{x.label}</button> <SourceBadge source={x.source} /></td>
                      <td className="td">{x.target}</td><td className="td"><StatusBadge status={x.status} /></td>
                      <td className="td tabular-nums">{ms(x.result?.latency_ms.p95 ?? null)}</td>
                      <td className="td tabular-nums">{x.result?.throughput_ops == null ? "—" : `${x.result.throughput_ops.toFixed(1)}/s`}</td>
                      <td className="td tabular-nums">{num(x.result?.docs_examined ?? null)}</td><td className="td text-xs">{dateTime(x.created_at)}</td>
                    </tr>
                  ))}
                </Table>
              </>
            )}
            {cmp.error && <div className="mt-3"><Notice tone="bad">{cmp.error}</Notice></div>}
          </Card>
          {comparison && <Card title="Before / after comparison" actions={<Badge>{comparison.c.same_query ? "same query" : "different queries"}</Badge>}><CompareView {...comparison} /></Card>}
        </div>
      </div>
    </>
  );
}
