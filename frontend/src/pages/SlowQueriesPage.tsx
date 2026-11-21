import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { NoInstance, PeriodSelect, defaultPeriod, periodParams } from "../components/shared";
import { Card, Check, EmptyState, ErrorState, Input, LoadingState, Notice, PageHeader, Select, SeverityBadge, SourceBadge, Spinner, Table, Tabs } from "../components/ui";
import { useApp } from "../hooks/AppContext";
import { useAction, useAsync } from "../hooks/useAsync";
import { api } from "../services/api";
import type { QueryPattern, SlowQuery } from "../types";
import { dateTime, ms, num } from "../utils/format";

const PAGE = 25;

export default function SlowQueriesPage() {
  const { instanceId } = useApp();
  const nav = useNavigate();
  const [tab, setTab] = useState<"executions" | "patterns">("executions");
  const [period, setPeriod] = useState(defaultPeriod());
  const [search, setSearch] = useState("");
  const [severity, setSeverity] = useState("");
  const [scanOnly, setScanOnly] = useState(false);
  const [synthetic, setSynthetic] = useState(true);
  const [sort, setSort] = useState("ts");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(0);
  const collect = useAction();
  const [collectMsg, setCollectMsg] = useState<string | null>(null);

  const rows = useAsync(
    () => instanceId ? api.get<{ total: number; items: SlowQuery[] }>("/queries/slow", {
      instance_id: instanceId, ...periodParams(period), search, severity, collection_scan: scanOnly ? true : undefined,
      include_synthetic: synthetic, sort, order, limit: PAGE, offset: page * PAGE,
    }) : null,
    [instanceId, period.period, period.start, period.end, search, severity, scanOnly, synthetic, sort, order, page],
  );
  const patterns = useAsync(
    () => instanceId && tab === "patterns" ? api.get<{ items: QueryPattern[] }>("/queries/patterns", { instance_id: instanceId, ...periodParams(period), include_synthetic: synthetic }) : null,
    [instanceId, tab, period.period, period.start, period.end, synthetic],
  );
  if (!instanceId) return <NoInstance title="Slow query monitor" />;

  const collectNow = async () => {
    const r = await collect.run(() => api.post<{ results: Record<string, { available: boolean; reason?: string; ingested?: number }> }>(`/monitoring/${instanceId}/collect-slow`));
    if (r) {
      const parts = Object.entries(r.results).map(([db, v]) => v.available ? `${db}: ${v.ingested} new` : `${db}: ${v.reason}`);
      setCollectMsg(parts.join(" · ") || "No databases to read.");
      rows.reload();
    }
  };
  const sortBy = (key: string) => {
    if (sort === key) setOrder(order === "desc" ? "asc" : "desc");
    else { setSort(key); setOrder("desc"); }
    setPage(0);
  };
  const th = (key: string, label: string) => (
    <th className="th" aria-sort={sort === key ? (order === "desc" ? "descending" : "ascending") : "none"}>
      <button onClick={() => sortBy(key)} className="uppercase hover:text-ink">{label}{sort === key ? (order === "desc" ? " ↓" : " ↑") : ""}</button>
    </th>
  );
  const total = rows.data?.total ?? 0;

  return (
    <>
      <PageHeader title="Slow query monitor" subtitle="Operations above the configured threshold, read from the MongoDB profiler. Query values are never stored — only their shape."
        actions={<button className="btn btn-primary" onClick={collectNow} disabled={collect.busy} data-testid="collect-now">{collect.busy && <Spinner />} Collect from profiler now</button>} />
      {collectMsg && <div className="mb-4"><Notice tone="info" testId="collect-result">{collectMsg}</Notice></div>}
      {collect.error && <div className="mb-4"><Notice tone="bad">{collect.error}</Notice></div>}
      <Card className="mb-4">
        <div className="flex flex-wrap items-end gap-3">
          <PeriodSelect value={period} onChange={(p) => { setPeriod(p); setPage(0); }} />
          <div className="w-56"><Input label="Search" placeholder="fingerprint, collection, plan…" value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} data-testid="search" /></div>
          <div className="w-36"><Select label="Severity" value={severity} onChange={(e) => { setSeverity(e.target.value); setPage(0); }}>
            <option value="">All</option>{["LOW", "MEDIUM", "HIGH", "CRITICAL"].map((s) => <option key={s}>{s}</option>)}</Select></div>
          <div className="pb-2"><Check label="Collection scans only" checked={scanOnly} onChange={(e) => { setScanOnly(e.target.checked); setPage(0); }} /></div>
          <div className="pb-2"><Check label="Include synthetic" checked={synthetic} onChange={(e) => setSynthetic(e.target.checked)} /></div>
        </div>
      </Card>
      <Tabs tabs={[{ id: "executions", label: "Executions" }, { id: "patterns", label: "Patterns (grouped by fingerprint)" }]} value={tab} onChange={setTab} />
      {tab === "executions" ? (
        <Card>
          {rows.loading && !rows.data ? <LoadingState /> : rows.error ? <ErrorState message={rows.error} onRetry={rows.reload} /> :
            !rows.data?.items.length ? <EmptyState title="No slow queries match" hint="Either nothing exceeded the threshold, the profiler is off for the monitored databases, or filters are too narrow. Use “Collect from profiler now” after enabling profiling." /> : (
              <>
                <Table testId="slow-table" head={<tr>{th("ts", "Time")}{th("duration_ms", "Duration")}<th className="th">Severity</th><th className="th">Collection</th><th className="th">Op</th>{th("docs_examined", "Docs examined")}{th("docs_returned", "Returned")}<th className="th">Plan</th><th className="th">Fingerprint</th></tr>}>
                  {rows.data.items.map((q) => (
                    <tr key={q.id} className="cursor-pointer hover:bg-raised" data-testid="slow-row" onClick={() => nav(`/investigate/${q.instance_id}/${q.fingerprint}${q.source === "SYNTHETIC" ? "?source=SYNTHETIC" : ""}`)}>
                      <td className="td whitespace-nowrap text-xs">{dateTime(q.ts)}</td>
                      <td className="td font-semibold tabular-nums">{ms(q.duration_ms)}</td>
                      <td className="td"><SeverityBadge severity={q.severity} /></td>
                      <td className="td">{q.database}.{q.collection} <SourceBadge source={q.source} /></td>
                      <td className="td">{q.operation}</td>
                      <td className="td tabular-nums">{num(q.docs_examined)}</td>
                      <td className="td tabular-nums">{num(q.docs_returned)}</td>
                      <td className={`td font-mono text-xs ${q.collection_scan ? "text-bad" : ""}`}>{q.plan_summary || "—"}</td>
                      <td className="td font-mono text-xs text-accent">{q.fingerprint}</td>
                    </tr>
                  ))}
                </Table>
                <div className="mt-3 flex items-center justify-between text-sm text-muted">
                  <span>{num(total)} results</span>
                  <span className="flex items-center gap-2">
                    <button className="btn" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
                    Page {page + 1} of {Math.max(1, Math.ceil(total / PAGE))}
                    <button className="btn" disabled={(page + 1) * PAGE >= total} onClick={() => setPage(page + 1)}>Next</button>
                  </span>
                </div>
              </>
            )}
        </Card>
      ) : (
        <Card>
          {patterns.loading ? <LoadingState /> : patterns.error ? <ErrorState message={patterns.error} onRetry={patterns.reload} /> :
            !patterns.data?.items.length ? <EmptyState title="No patterns yet" /> : (
              <Table testId="pattern-table" head={<tr>{["Collection", "Op", "Count", "Avg", "P95", "P99", "Max", "Last plan", "Fingerprint"].map((h) => <th key={h} className="th">{h}</th>)}</tr>}>
                {patterns.data.items.map((p) => (
                  <tr key={p.id} className="cursor-pointer hover:bg-raised" onClick={() => nav(`/investigate/${p.instance_id}/${p.fingerprint}${p.source === "SYNTHETIC" ? "?source=SYNTHETIC" : ""}`)}>
                    <td className="td">{p.database}.{p.collection} <SourceBadge source={p.source} /></td><td className="td">{p.operation}</td>
                    <td className="td tabular-nums">{num(p.count)}</td><td className="td tabular-nums">{ms(p.stats.avg_ms)}</td>
                    <td className="td tabular-nums">{ms(p.stats.p95_ms)}</td><td className="td tabular-nums">{ms(p.stats.p99_ms)}</td><td className="td tabular-nums">{ms(p.stats.max_ms)}</td>
                    <td className="td font-mono text-xs">{p.last_plan_summary || "—"}</td><td className="td font-mono text-xs text-accent">{p.fingerprint}</td>
                  </tr>
                ))}
              </Table>
            )}
        </Card>
      )}
    </>
  );
}
