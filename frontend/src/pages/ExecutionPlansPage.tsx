import { useState, type FormEvent } from "react";
import { NoInstance, PlanView, RecommendationCard } from "../components/shared";
import { Card, EmptyState, JsonView, LoadingState, Notice, PageHeader, Select, Spinner, Table, TextArea, Input } from "../components/ui";
import { useApp } from "../hooks/AppContext";
import { useAction, useAsync } from "../hooks/useAsync";
import { api } from "../services/api";
import type { DbInfo, CollectionInfo, ExecutionPlan } from "../types";
import { dateTime, num, parseJson, parseJsonArray } from "../utils/format";

export default function ExecutionPlansPage() {
  const { instanceId, instance } = useApp();
  const [db, setDb] = useState("");
  const [coll, setColl] = useState("");
  const [op, setOp] = useState("find");
  const [filter, setFilter] = useState('{ "customer_id": 1001, "status": "COMPLETED" }');
  const [sort, setSort] = useState("");
  const [projection, setProjection] = useState("");
  const [pipeline, setPipeline] = useState("");
  const [limit, setLimit] = useState("");
  const [plan, setPlan] = useState<ExecutionPlan | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const run = useAction();
  const dbs = useAsync(() => (instanceId && instance?.enabled ? api.get<{ databases: DbInfo[] }>(`/databases/${instanceId}/explorer`) : null), [instanceId, instance?.enabled]);
  const colls = useAsync(() => (instanceId && db ? api.get<{ collections: CollectionInfo[] }>(`/databases/${instanceId}/explorer/${db}/collections`) : null), [instanceId, db]);
  const history = useAsync(() => (instanceId ? api.get<{ items: ExecutionPlan[] }>("/execution-plans", { instance_id: instanceId, limit: 20 }) : null), [instanceId]);
  if (!instanceId) return <NoInstance title="Execution plan explorer" />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setFormError(null);
    let body: Record<string, unknown>;
    try {
      body = { database: db, collection: coll, operation: op, filter: parseJson(filter, "Filter"), sort: parseJson(sort, "Sort"),
        projection: parseJson(projection, "Projection"), pipeline: op === "aggregate" ? parseJsonArray(pipeline, "Pipeline") : undefined, limit: limit ? Number(limit) : undefined };
    } catch (err) { setFormError(err instanceof Error ? err.message : "Invalid input"); return; }
    const r = await run.run(() => api.post<{ plan: ExecutionPlan }>(`/execution-plans/${instanceId}/explain`, body));
    if (r) { setPlan(r.plan); history.reload(); }
  };
  const openPast = async (id: string) => {
    const r = await run.run(() => api.get<{ plan: ExecutionPlan }>(`/execution-plans/${id}`));
    if (r) setPlan(r.plan);
  };

  return (
    <>
      <PageHeader title="Execution plan explorer" subtitle="Runs a real explain(“executionStats”). Note: executionStats executes the query (read-only) and explain time is not production latency." />
      <div className="grid gap-5 xl:grid-cols-3">
        <Card title="Query" className="xl:col-span-1">
          <form onSubmit={submit} className="space-y-3" data-testid="explain-form">
            <Select label="Database" value={db} onChange={(e) => { setDb(e.target.value); setColl(""); }} required data-testid="db-select">
              <option value="">Select…</option>{dbs.data?.databases.map((d) => <option key={d.name}>{d.name}</option>)}</Select>
            <Select label="Collection" value={coll} onChange={(e) => setColl(e.target.value)} required disabled={!db} data-testid="coll-select">
              <option value="">Select…</option>{colls.data?.collections.filter((c) => c.type === "collection").map((c) => <option key={c.name} value={c.name}>{c.name} ({num(c.document_count)})</option>)}</Select>
            <Select label="Operation" value={op} onChange={(e) => setOp(e.target.value)}><option>find</option><option>count</option><option>aggregate</option></Select>
            {op !== "aggregate" ? (<>
              <TextArea label="Filter (JSON)" value={filter} onChange={(e) => setFilter(e.target.value)} data-testid="filter" />
              {op === "find" && <><TextArea label="Sort (JSON, optional)" value={sort} onChange={(e) => setSort(e.target.value)} rows={2} placeholder='{ "created_at": -1 }' />
                <TextArea label="Projection (JSON, optional)" value={projection} onChange={(e) => setProjection(e.target.value)} rows={2} />
                <Input label="Limit (optional)" type="number" min={1} value={limit} onChange={(e) => setLimit(e.target.value)} /></>}
            </>) : <TextArea label="Pipeline (JSON array)" rows={6} value={pipeline} onChange={(e) => setPipeline(e.target.value)} placeholder='[{ "$match": { "status": "PENDING" } }]' hint="Allowed stages: $match $project $sort $limit $skip $group $count $unwind $addFields $set $unset $sample" />}
            {formError && <Notice tone="bad">{formError}</Notice>}
            {run.error && <Notice tone="bad" testId="explain-error">{run.error}</Notice>}
            <button className="btn btn-primary w-full" disabled={run.busy || !coll} data-testid="run-explain">{run.busy && <Spinner />} Run explain</button>
          </form>
        </Card>
        <div className="space-y-5 xl:col-span-2">
          {plan ? (
            <>
              <Card title={`Plan for ${plan.target}`} actions={<span className="text-xs text-muted">{dateTime(plan.created_at)}</span>}>
                <PlanView a={plan.analysis} />
              </Card>
              <Card title="Index recommendation"><RecommendationCard rec={plan.recommendation} /></Card>
              <Card title="Raw explain output (unmodified)">{plan.raw_explain ? <JsonView value={plan.raw_explain} maxHeight="26rem" /> : <p className="text-sm text-muted">Not available.</p>}</Card>
            </>
          ) : <Card><EmptyState title="Run an explain to see the plan" hint="Pick a database and collection, enter a filter, and DB Pulse will show the winning plan, rejected plans, stage statistics and findings." /></Card>}
          <Card title="Previous explains">
            {history.loading ? <LoadingState /> : !history.data?.items.length ? <p className="text-sm text-muted">None yet.</p> : (
              <Table testId="plan-history" head={<tr>{["When", "Target", "Stages", "Examined", "Returned"].map((h) => <th key={h} className="th">{h}</th>)}</tr>}>
                {history.data.items.map((p) => (
                  <tr key={p.id} className="cursor-pointer hover:bg-raised" onClick={() => openPast(p.id)}>
                    <td className="td text-xs">{dateTime(p.created_at)}</td><td className="td">{p.target}</td>
                    <td className="td font-mono text-xs">{p.analysis.stage_names.join(" > ")}</td><td className="td">{num(p.analysis.documents_examined)}</td><td className="td">{num(p.analysis.documents_returned)}</td>
                  </tr>
                ))}
              </Table>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
