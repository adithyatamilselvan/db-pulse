import { useState, type FormEvent } from "react";
import { NoInstance, PeriodSelect, defaultPeriod, periodParams } from "../components/shared";
import { Badge, Card, Check, EmptyState, Input, LoadingState, Notice, PageHeader, Select, Spinner, Table } from "../components/ui";
import { useApp } from "../hooks/AppContext";
import { useAction, useAsync } from "../hooks/useAsync";
import { api } from "../services/api";
import type { Comparison, ReportMeta } from "../types";
import { dateTime } from "../utils/format";

type Fmt = "pdf" | "csv" | "json";

export default function ReportsPage() {
  const { instanceId, instance } = useApp();
  const [period, setPeriod] = useState(defaultPeriod());
  const [title, setTitle] = useState("");
  const [comparisonId, setComparisonId] = useState("");
  const [synthetic, setSynthetic] = useState(!!instance?.is_demo);
  const gen = useAction();
  const dl = useAction();
  const reports = useAsync(() => api.get<{ items: ReportMeta[] }>("/reports"), []);
  const comparisons = useAsync(() => api.get<{ items: Comparison[] }>("/benchmarks/comparisons"), []);
  if (!instanceId) return <NoInstance title="Reports" />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const r = await gen.run(() => api.post<{ report: ReportMeta }>("/reports", {
      instance_id: instanceId, ...periodParams(period), title: title || undefined, comparison_id: comparisonId || undefined, include_synthetic: synthetic }));
    if (r) { reports.reload(); setTitle(""); }
  };
  const download = (id: string, format: Fmt) => dl.run(() => api.download(`/reports/${id}/download`, { format }, `dbpulse-report.${format}`));

  return (
    <>
      <PageHeader title="Reports" subtitle="Investigation reports separate observed facts from estimated benefits and list their limitations." />
      <div className="grid gap-5 lg:grid-cols-3">
        <Card title="Generate report">
          <form onSubmit={submit} className="space-y-3" data-testid="report-form">
            <Notice tone="info">Instance: <b>{instance?.name}</b></Notice>
            <PeriodSelect value={period} onChange={setPeriod} />
            <Input label="Title (optional)" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={150} data-testid="report-title" />
            <Select label="Benchmark comparison to feature (optional)" value={comparisonId} onChange={(e) => setComparisonId(e.target.value)}>
              <option value="">None (use period)</option>{comparisons.data?.items.filter((c) => c.source === "observed").map((c) => <option key={c.id} value={c.id}>{c.before_label} → {c.after_label} · {c.verdict}</option>)}</Select>
            <Check label="Include SYNTHETIC demo records (labelled in the report)" checked={synthetic} onChange={(e) => setSynthetic(e.target.checked)} />
            {gen.error && <Notice tone="bad">{gen.error}</Notice>}
            <button className="btn btn-primary w-full" disabled={gen.busy} data-testid="generate-report">{gen.busy && <Spinner />} Generate report</button>
          </form>
        </Card>
        <Card title="Generated reports" className="lg:col-span-2">
          {dl.error && <div className="mb-3"><Notice tone="bad">{dl.error}</Notice></div>}
          {reports.loading ? <LoadingState /> : !reports.data?.items.length ? <EmptyState title="No reports yet" hint="Generate one to download it as PDF, CSV or JSON." /> : (
            <Table testId="reports-table" head={<tr>{["Title", "Created", "Period", "Download"].map((h) => <th key={h} className="th">{h}</th>)}</tr>}>
              {reports.data.items.map((r) => (
                <tr key={r.id} data-testid="report-row">
                  <td className="td font-medium">{r.title} {r.includes_synthetic_data && <Badge tone="border-warn/60 bg-warn/15 text-warn">includes synthetic</Badge>}</td>
                  <td className="td text-xs">{dateTime(r.created_at)}</td>
                  <td className="td text-xs">{dateTime(r.period.start)} → {dateTime(r.period.end)}</td>
                  <td className="td"><div className="flex gap-1">{(["pdf", "csv", "json"] as Fmt[]).map((f) => <button key={f} className="btn uppercase" disabled={dl.busy} onClick={() => download(r.id, f)} data-testid={`download-${f}`}>{f}</button>)}</div></td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      </div>
    </>
  );
}
