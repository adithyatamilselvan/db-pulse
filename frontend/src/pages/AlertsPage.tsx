import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, Check, EmptyState, ErrorState, JsonView, LoadingState, Modal, Notice, PageHeader, Select, SeverityBadge, SourceBadge, Stat, StatusBadge, Table } from "../components/ui";
import { useApp } from "../hooks/AppContext";
import { useAction, useAsync } from "../hooks/useAsync";
import { api } from "../services/api";
import type { Alert } from "../types";
import { ago, dateTime } from "../utils/format";

const TYPES = ["SLOW_QUERY", "COLLECTION_SCAN", "LATENCY_INCREASE", "HIGH_DOCS_EXAMINED", "EXCESSIVE_QUERY_FREQUENCY", "INEFFICIENT_INDEX_USAGE", "CONNECTION_FAILURE", "MONITORING_FAILURE", "BENCHMARK_REGRESSION"];

export default function AlertsPage() {
  const { instanceId } = useApp();
  const [status, setStatus] = useState("open");
  const [severity, setSeverity] = useState("");
  const [type, setType] = useState("");
  const [synthetic, setSynthetic] = useState(true);
  const [scope, setScope] = useState<"instance" | "all">("instance");
  const [detail, setDetail] = useState<Alert | null>(null);
  const act = useAction();
  const { data, loading, error, reload } = useAsync(
    () => api.get<{ total: number; items: Alert[] }>("/alerts", { instance_id: scope === "instance" ? instanceId : undefined, status, severity, type, include_synthetic: synthetic, limit: 200 }),
    [instanceId, scope, status, severity, type, synthetic],
  );
  const summary = useAsync(() => api.get<{ by_status: Record<string, number>; by_severity_active: Record<string, number> }>("/alerts/summary"), [data]);

  const transition = async (a: Alert, action: "acknowledge" | "resolve") => {
    const r = await act.run(() => api.post<{ alert: Alert }>(`/alerts/${a.id}/${action}`));
    if (r) { reload(); setDetail((d) => (d && d.id === a.id ? { ...d, ...r.alert, id: a.id } : d)); }
  };

  return (
    <>
      <PageHeader title="Alerts center" subtitle="Duplicate alerts are folded into one with an occurrence count (cooldown configurable per instance)." />
      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Open" value={summary.data?.by_status.open ?? 0} tone="text-bad" testId="sum-open" />
        <Stat label="Acknowledged" value={summary.data?.by_status.acknowledged ?? 0} tone="text-warn" />
        <Stat label="Resolved" value={summary.data?.by_status.resolved ?? 0} tone="text-ok" />
        <Stat label="Active HIGH" value={summary.data?.by_severity_active.HIGH ?? 0} />
        <Stat label="Active CRITICAL" value={summary.data?.by_severity_active.CRITICAL ?? 0} />
      </div>
      <Card className="mb-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="w-40"><Select label="Status" value={status} onChange={(e) => setStatus(e.target.value)} data-testid="status-filter"><option value="">All</option><option value="open">Open</option><option value="acknowledged">Acknowledged</option><option value="resolved">Resolved</option></Select></div>
          <div className="w-40"><Select label="Severity" value={severity} onChange={(e) => setSeverity(e.target.value)}><option value="">All</option>{["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"].map((s) => <option key={s}>{s}</option>)}</Select></div>
          <div className="w-56"><Select label="Type" value={type} onChange={(e) => setType(e.target.value)}><option value="">All</option>{TYPES.map((s) => <option key={s}>{s}</option>)}</Select></div>
          <div className="w-44"><Select label="Scope" value={scope} onChange={(e) => setScope(e.target.value as "instance" | "all")}><option value="instance">Selected instance</option><option value="all">All instances</option></Select></div>
          <div className="pb-2"><Check label="Include synthetic" checked={synthetic} onChange={(e) => setSynthetic(e.target.checked)} /></div>
        </div>
      </Card>
      {act.error && <div className="mb-3"><Notice tone="bad">{act.error}</Notice></div>}
      <Card>
        {loading && !data ? <LoadingState /> : error ? <ErrorState message={error} onRetry={reload} /> : !data?.items.length ? <EmptyState title="No alerts match these filters" hint="Alerts appear when slow queries, collection scans, latency increases or connection failures are detected." /> : (
          <Table testId="alerts-table" head={<tr>{["Severity", "Alert", "Target", "Status", "×", "Last seen", ""].map((h) => <th key={h} className="th">{h}</th>)}</tr>}>
            {data.items.map((a) => (
              <tr key={a.id} data-testid="alert-row">
                <td className="td"><SeverityBadge severity={a.severity} /></td>
                <td className="td"><button className="text-left font-medium text-accent hover:underline" onClick={() => setDetail(a)}>{a.title}</button> <SourceBadge source={a.source} /><div className="text-xs text-muted">{a.type}</div></td>
                <td className="td text-xs">{a.database ? `${a.database}.${a.collection ?? "*"}` : "—"}</td>
                <td className="td"><StatusBadge status={a.status} /></td>
                <td className="td tabular-nums">{a.occurrences}</td>
                <td className="td text-xs text-muted">{ago(a.last_seen)}</td>
                <td className="td"><div className="flex gap-1">
                  {a.status === "open" && <button className="btn" disabled={act.busy} onClick={() => transition(a, "acknowledge")} data-testid="ack">Acknowledge</button>}
                  {a.status !== "resolved" && <button className="btn" disabled={act.busy} onClick={() => transition(a, "resolve")} data-testid="resolve">Resolve</button>}
                </div></td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      {detail && (
        <Modal title={detail.title} wide onClose={() => setDetail(null)}>
          <div className="flex flex-wrap items-center gap-2"><SeverityBadge severity={detail.severity} /><StatusBadge status={detail.status} /><SourceBadge source={detail.source} /><span className="text-xs text-muted">{detail.type} · ×{detail.occurrences} · first {dateTime(detail.created_at)}</span></div>
          <p className="text-sm">{detail.message}</p>
          <div><h3 className="label">Supporting evidence</h3><JsonView value={detail.evidence} maxHeight="14rem" /></div>
          {detail.fingerprint && detail.source !== "SYNTHETIC" && <Link className="btn btn-primary" to={`/investigate/${detail.instance_id}/${detail.fingerprint}`} onClick={() => setDetail(null)}>Investigate this query</Link>}
        </Modal>
      )}
    </>
  );
}
