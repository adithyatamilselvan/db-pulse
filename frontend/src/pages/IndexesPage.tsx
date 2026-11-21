import { useState } from "react";
import { NoInstance, RecommendationCard } from "../components/shared";
import { Badge, Card, Check, ConfirmDialog, EmptyState, ErrorState, LoadingState, Notice, PageHeader, Select, Spinner, Table, TextArea } from "../components/ui";
import { useApp } from "../hooks/AppContext";
import { useAction, useAsync } from "../hooks/useAsync";
import { api } from "../services/api";
import type { CollectionInfo, DbInfo, IndexChange, IndexInvestigation, Recommendation } from "../types";
import { bytes, dateTime, keyPattern, num, parseJson } from "../utils/format";

export default function IndexesPage() {
  const { instanceId, instance, user } = useApp();
  const [db, setDb] = useState("");
  const [coll, setColl] = useState("");
  const [filter, setFilter] = useState('{ "customer_id": 1001, "status": "COMPLETED" }');
  const [sort, setSort] = useState("");
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [ack1, setAck1] = useState(false);
  const [ack2, setAck2] = useState(false);
  const [rollback, setRollback] = useState<IndexChange | null>(null);
  const recommend = useAction();
  const apply = useAction();
  const dbs = useAsync(() => (instanceId && instance?.enabled ? api.get<{ databases: DbInfo[] }>(`/databases/${instanceId}/explorer`) : null), [instanceId, instance?.enabled]);
  const colls = useAsync(() => (instanceId && db ? api.get<{ collections: CollectionInfo[] }>(`/databases/${instanceId}/explorer/${db}/collections`) : null), [instanceId, db]);
  const inv = useAsync(() => (instanceId && db && coll ? api.get<IndexInvestigation>(`/indexes/${instanceId}/${db}/${coll}`) : null), [instanceId, db, coll]);
  const changes = useAsync(() => (instanceId ? api.get<{ items: IndexChange[] }>(`/indexes/${instanceId}/changes`) : null), [instanceId]);
  if (!instanceId) return <NoInstance title="Index investigator" />;

  const doRecommend = async () => {
    setFormError(null);
    let f, s;
    try { f = parseJson(filter, "Filter"); s = parseJson(sort, "Sort"); } catch (e) { setFormError(e instanceof Error ? e.message : "Invalid"); return; }
    const r = await recommend.run(() => api.post<{ recommendation: Recommendation }>(`/indexes/${instanceId}/recommend`, { database: db, collection: coll, filter: f, sort: s }));
    if (r) setRec(r.recommendation);
  };
  const doApply = async () => {
    if (!rec?.suggested_index) return;
    const r = await apply.run(() => api.post(`/indexes/${instanceId}/create`, { database: db, collection: coll, keys: rec.suggested_index!.keys, confirm: true, acknowledge_write_cost: true }));
    if (r) { setApplying(false); setAck1(false); setAck2(false); inv.reload(); changes.reload(); }
  };
  const doRollback = async () => {
    if (!rollback) return;
    const r = await apply.run(() => api.post(`/indexes/${instanceId}/rollback`, { change_id: rollback.id, confirm: true }));
    if (r) { setRollback(null); inv.reload(); changes.reload(); }
  };
  const canModify = user?.role === "admin" && instance?.allow_modifications;

  return (
    <>
      <PageHeader title="Index investigator" subtitle="Existing indexes, duplicates, redundant prefixes, and evidence-based recommendations. DB Pulse never creates or drops indexes automatically." />
      <div className="mb-5 grid gap-3 md:grid-cols-3">
        <Select label="Database" value={db} onChange={(e) => { setDb(e.target.value); setColl(""); setRec(null); }} data-testid="db-select">
          <option value="">Select…</option>{dbs.data?.databases.map((d) => <option key={d.name}>{d.name}</option>)}</Select>
        <Select label="Collection" value={coll} onChange={(e) => { setColl(e.target.value); setRec(null); }} disabled={!db} data-testid="coll-select">
          <option value="">Select…</option>{colls.data?.collections.filter((c) => c.type === "collection").map((c) => <option key={c.name}>{c.name}</option>)}</Select>
      </div>
      {!coll ? <Card><EmptyState title="Choose a collection to investigate its indexes" /></Card> : (
        <div className="space-y-5">
          <Card title={`Indexes on ${db}.${coll}`}>
            {inv.loading ? <LoadingState /> : inv.error ? <ErrorState message={inv.error} onRetry={inv.reload} /> : inv.data && (
              <div className="space-y-3">
                <Table testId="index-table" head={<tr>{["Name", "Keys", "Options", "Accesses", "Since", "Size"].map((h) => <th key={h} className="th">{h}</th>)}</tr>}>
                  {inv.data.indexes.map((i) => (
                    <tr key={i.name}><td className="td font-mono text-xs">{i.name}</td><td className="td font-mono text-xs">{keyPattern(i.key)}</td>
                      <td className="td text-xs">{[i.unique && "unique", i.sparse && "sparse", i.partial && "partial", i.ttl && "ttl"].filter(Boolean).join(", ") || "—"}</td>
                      <td className="td tabular-nums">{num(i.accesses)}</td><td className="td text-xs">{dateTime(i.accesses_since)}</td><td className="td">{bytes(i.size_bytes)}</td></tr>
                  ))}
                </Table>
                {!inv.data.index_stats_available && <Notice tone="warn">$indexStats is unavailable on this deployment/permission: access counts are unknown (shown “—”), not zero.</Notice>}
                <div data-testid="index-issues">
                  <h3 className="label">Findings</h3>
                  {inv.data.issues.length === 0 ? <p className="text-sm text-muted">No duplicate, redundant-prefix or unused-index issues detected.</p> :
                    <ul className="space-y-2 text-sm">{inv.data.issues.map((i, k) => <li key={k}><Badge tone="border-warn/50 text-warn">{i.type.replace("_", " ")}</Badge> {i.message}</li>)}</ul>}
                </div>
                <ul className="list-disc pl-5 text-xs text-muted">{inv.data.caveats.map((c, i) => <li key={i}>{c}</li>)}</ul>
              </div>
            )}
          </Card>
          <Card title="Recommend an index for a query">
            <div className="grid gap-3 md:grid-cols-2">
              <TextArea label="Filter (JSON)" value={filter} onChange={(e) => setFilter(e.target.value)} data-testid="rec-filter" />
              <TextArea label="Sort (JSON, optional)" value={sort} onChange={(e) => setSort(e.target.value)} />
            </div>
            {formError && <div className="mt-3"><Notice tone="bad">{formError}</Notice></div>}
            {recommend.error && <div className="mt-3"><Notice tone="bad">{recommend.error}</Notice></div>}
            <div className="mt-3"><button className="btn btn-primary" onClick={doRecommend} disabled={recommend.busy} data-testid="recommend">{recommend.busy && <Spinner />} Analyze</button></div>
            {rec && (
              <div className="mt-4 space-y-3">
                <RecommendationCard rec={rec} />
                {rec.action === "create_index" && (
                  <div className="flex items-center gap-3">
                    <button className="btn btn-primary" disabled={!canModify} onClick={() => setApplying(true)} data-testid="apply-index">Create this index…</button>
                    {!canModify && <span className="text-xs text-muted">{user?.role !== "admin" ? "Administrator role required." : "Instance is registered read-only (enable modifications in Settings)."}</span>}
                  </div>
                )}
              </div>
            )}
          </Card>
          <Card title="Index changes made through DB Pulse">
            {changes.data?.items.length ? (
              <Table head={<tr>{["When", "Action", "Target", "Index", ""].map((h) => <th key={h} className="th">{h}</th>)}</tr>}>
                {changes.data.items.map((c) => (
                  <tr key={c.id}><td className="td text-xs">{dateTime(c.created_at)}</td><td className="td">{c.action}</td><td className="td">{c.database}.{c.collection}</td><td className="td font-mono text-xs">{c.index_name}</td>
                    <td className="td">{c.action === "create" && canModify && !changes.data!.items.some((x) => x.action === "drop" && x.index_name === c.index_name && x.collection === c.collection) &&
                      <button className="btn btn-danger" onClick={() => setRollback(c)}>Roll back</button>}</td></tr>
                ))}
              </Table>
            ) : <p className="text-sm text-muted">No changes yet.</p>}
          </Card>
        </div>
      )}
      {applying && rec?.suggested_index && (
        <ConfirmDialog title="Create index on the monitored database" confirmLabel="Create index" busy={apply.busy} disabled={!(ack1 && ack2)} onCancel={() => setApplying(false)} onConfirm={doApply}>
          <p className="text-sm">You are about to run on <b>{instance?.name}</b>:</p>
          <code className="block rounded bg-bg p-2 font-mono text-sm">{rec.suggested_index.command}</code>
          <Check label="I understand this modifies the monitored database." checked={ack1} onChange={(e) => setAck1(e.target.checked)} data-testid="ack-modify" />
          <Check label="I accept the added write and storage cost, and will validate the result with a benchmark." checked={ack2} onChange={(e) => setAck2(e.target.checked)} data-testid="ack-cost" />
          {apply.error && <Notice tone="bad">{apply.error}</Notice>}
        </ConfirmDialog>
      )}
      {rollback && (
        <ConfirmDialog title="Roll back index" danger confirmLabel="Drop index" busy={apply.busy} onCancel={() => setRollback(null)} onConfirm={doRollback}>
          <p className="text-sm">Drop <b className="font-mono">{rollback.index_name}</b> from {rollback.database}.{rollback.collection}? Only indexes created through DB Pulse can be dropped here.</p>
          {apply.error && <Notice tone="bad">{apply.error}</Notice>}
        </ConfirmDialog>
      )}
    </>
  );
}
