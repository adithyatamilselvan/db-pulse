import { useState } from "react";
import { Link } from "react-router-dom";
import { PlotChart } from "../charts/PlotChart";
import { PeriodSelect, defaultPeriod, periodParams } from "../components/shared";
import { Card, Check, ConfirmDialog, EmptyState, ErrorState, LoadingState, Notice, PageHeader, SourceBadge, Stat, StatusBadge, Badge } from "../components/ui";
import { useApp } from "../hooks/AppContext";
import { useAction, useAsync } from "../hooks/useAsync";
import { api } from "../services/api";
import type { Dashboard, DemoSetupResult } from "../types";
import { ago, ms, num, pct } from "../utils/format";

export default function DashboardPage() {
  const { user, reloadInstances, setInstanceId } = useApp();
  const [period, setPeriod] = useState(defaultPeriod());
  const [synthetic, setSynthetic] = useState(true);
  const [demoOpen, setDemoOpen] = useState(false);
  const setup = useAction();
  const { data, loading, error, reload } = useAsync(
    () => api.get<Dashboard>("/monitoring/dashboard", { ...periodParams(period), include_synthetic: synthetic }),
    [period.period, period.start, period.end, synthetic],
  );

  const [demoMsg, setDemoMsg] = useState<string | null>(null);

  const loadDemo = async () => {
    const r = await setup.run(() => api.post<DemoSetupResult>("/demo/setup", { orders: 20000, seed: 42, confirm: true }));
    if (r) {
      await reloadInstances();
      setInstanceId(r.instance.id);
      setDemoOpen(false);
      const d = r.real_dataset;
      const history = r.synthetic_records.status === "refreshed" ? " SYNTHETIC history was moved forward to the present so it appears in recent periods." : "";
      setDemoMsg(
        (d.status === "created"
          ? `Demo data loaded: ${num(d.orders)} orders, ${num(d.customers)} customers and ${num(d.products)} products in "${d.database}". Open the Database explorer to browse them.`
          : `Demo data was already present in "${d.database}" (${num(d.orders)} orders); nothing was overwritten.`) + history,
      );
      reload();
    }
  };

  return (
    <>
      <PageHeader
        title="Dashboard"
        subtitle="Slow-query figures are computed over recorded slow operations (above each instance's threshold), not over all queries."
        actions={
          <>
            <PeriodSelect value={period} onChange={setPeriod} />
            <div className="pt-5"><Check label="Include synthetic demo records" checked={synthetic} onChange={(e) => setSynthetic(e.target.checked)} /></div>
            {user?.role === "admin" && (
              <div className="pt-5"><button className="btn" data-testid="load-demo-header" onClick={() => setDemoOpen(true)}>Load demo data</button></div>
            )}
          </>
        }
      />
      {loading && !data ? <LoadingState /> : error ? <ErrorState message={error} onRetry={reload} /> : data && (
        <div className="space-y-5">
          {demoMsg && <Notice tone="ok" testId="demo-result">{demoMsg}</Notice>}
          {data.includes_synthetic && (
            <Notice tone="warn" testId="synthetic-notice">
              This view includes <b>SYNTHETIC</b> demo records. They are generated for exploration and are not real MongoDB measurements.
              Untick “Include synthetic demo records” to see observed data only.
            </Notice>
          )}
          {data.instances.total === 0 && (
            <Card>
              <EmptyState
                title="Welcome to DB Pulse"
                hint="Register a MongoDB instance in the Database explorer, or load the demo: a real 20,000-order collection plus clearly labelled synthetic history."
                action={
                  <div className="flex gap-2">
                    <Link to="/explorer" className="btn">Register a database</Link>
                    {user?.role === "admin" && <button className="btn btn-primary" data-testid="load-demo" onClick={() => setDemoOpen(true)}>Load demo</button>}
                  </div>
                }
              />
            </Card>
          )}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-7">
            <Stat testId="stat-instances" label="Instances" value={`${data.instances.connected}/${data.instances.total}`} hint="connected" />
            <Stat testId="stat-slow" label="Slow queries" value={num(data.slow_queries.total)} />
            <Stat testId="stat-avg" label="Avg latency" value={ms(data.slow_queries.avg_ms)} />
            <Stat testId="stat-p95" label="P95 latency" value={ms(data.slow_queries.p95_ms)} />
            <Stat testId="stat-p99" label="P99 latency" value={ms(data.slow_queries.p99_ms)} />
            <Stat testId="stat-scans" label="Collection scans" value={num(data.collection_scans)}
              tone={data.collection_scans ? "text-bad" : undefined}
              hint={`index scans ${pct(data.index_usage.index_scan_share === null ? null : data.index_usage.index_scan_share * 100)}`} />
            <Stat testId="stat-alerts" label="Active alerts" value={num(data.active_alerts.open)} tone={data.active_alerts.open ? "text-warn" : undefined}
              hint={Object.entries(data.active_alerts.by_severity).map(([k, v]) => `${k} ${v}`).join(" · ") || "none"} />
          </div>

          <Card title="Query latency trend (slow operations)">
            {data.trend.length === 0 ? (
              <EmptyState title="No slow queries in this period" hint="Enable the MongoDB profiler on a monitored database (Settings → Profiler) or widen the period." />
            ) : (
              <PlotChart
                testId="trend-chart"
                data={[
                  { x: data.trend.map((t) => t.ts), y: data.trend.map((t) => t.count), type: "bar", name: "Slow ops", yaxis: "y2", opacity: 0.35, marker: { color: "#64748b" } },
                  { x: data.trend.map((t) => t.ts), y: data.trend.map((t) => t.avg_ms), type: "scatter", mode: "lines", name: "Avg ms" },
                  { x: data.trend.map((t) => t.ts), y: data.trend.map((t) => t.p95_ms), type: "scatter", mode: "lines", name: "P95 ms" },
                  { x: data.trend.map((t) => t.ts), y: data.trend.map((t) => t.p99_ms), type: "scatter", mode: "lines", name: "P99 ms" },
                ]}
                layout={{ yaxis: { title: { text: "latency (ms)" } }, yaxis2: { overlaying: "y", side: "right", showgrid: false, title: { text: "count" } } }}
              />
            )}
          </Card>

          <div className="grid gap-5 lg:grid-cols-2">
            <Card title="Database instances">
              {data.instances.items.length === 0 ? <p className="text-sm text-muted">None registered.</p> : (
                <ul className="divide-y divide-line">
                  {data.instances.items.map((i) => (
                    <li key={i.id} className="flex items-center justify-between py-2 text-sm">
                      <span><b>{i.name}</b> {i.is_demo && <Badge>demo</Badge>}<span className="ml-2 text-xs text-muted">{i.host_display} · MongoDB {i.server_version ?? "?"}</span></span>
                      <span className="flex items-center gap-2"><span className="text-xs text-muted">checked {ago(i.last_check_at)}</span><StatusBadge status={i.status} /></span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
            <Card title="Recent investigations">
              {data.recent_investigations.length === 0 ? <p className="text-sm text-muted">No AI investigations yet. Ask a question in the <Link className="text-accent underline" to="/assistant">AI assistant</Link>.</p> : (
                <ul className="divide-y divide-line">
                  {data.recent_investigations.map((r) => (
                    <li key={r.id} className="py-2 text-sm"><Link className="text-accent hover:underline" to="/assistant">{r.title}</Link><span className="ml-2 text-xs text-muted">{ago(r.updated_at)}</span></li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
          <SourceBadge source={data.includes_synthetic ? "SYNTHETIC" : undefined} />
        </div>
      )}
      {demoOpen && (
        <ConfirmDialog title="Load demo data" confirmLabel={setup.busy ? "Loading…" : "Load demo data"} busy={setup.busy} onConfirm={loadDemo} onCancel={() => setDemoOpen(false)}>
          <p className="text-sm">This writes a <b>real</b> 20,000-order dataset into the configured demo MongoDB database and creates <b>SYNTHETIC</b> history records so the dashboards are explorable. It is safe to repeat: data that is already there is kept and nothing is deleted or dropped. Existing SYNTHETIC history is only moved forward in time so it shows up in recent periods. The MongoDB profiler is not changed.</p>
          {setup.error && <Notice tone="bad">{setup.error}</Notice>}
        </ConfirmDialog>
      )}
    </>
  );
}
