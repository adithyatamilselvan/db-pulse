import { useState, type FormEvent } from "react";
import { Card, Check, ConfirmDialog, EmptyState, ErrorState, Input, LoadingState, Modal, Notice, PageHeader, Spinner, StatusBadge, Table, Badge } from "../components/ui";
import { NoInstance } from "../components/shared";
import { useApp } from "../hooks/AppContext";
import { useAction, useAsync } from "../hooks/useAsync";
import { api } from "../services/api";
import type { CollectionInfo, ConnectionTest, DbInfo, IndexInvestigation, Instance } from "../types";
import { ago, bytes, keyPattern, num } from "../utils/format";

function AddInstance({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState("");
  const [uri, setUri] = useState("mongodb://");
  const [allowMod, setAllowMod] = useState(false);
  const [test, setTest] = useState<ConnectionTest | null>(null);
  const t = useAction();
  const s = useAction();
  const doTest = async () => setTest((await t.run(() => api.post<ConnectionTest>("/databases/test", { uri }))) ?? null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const r = await s.run(() => api.post("/databases", { name, uri, allow_modifications: allowMod }));
    if (r) onDone();
  };
  return (
    <Modal title="Register a MongoDB instance" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3" data-testid="add-instance-form">
        <Input label="Display name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} data-testid="inst-name" />
        <Input label="Connection string" value={uri} onChange={(e) => { setUri(e.target.value); setTest(null); }} required data-testid="inst-uri"
          hint="Stored encrypted and never shown again. Prefer a read-only user." autoComplete="off" />
        <Check label={<>Allow DB Pulse to modify this instance (create indexes, change the profiler) <span className="text-muted">— off by default; each change still needs explicit confirmation</span></>}
          checked={allowMod} onChange={(e) => setAllowMod(e.target.checked)} />
        {test && (test.ok
          ? <Notice tone="ok" testId="test-result">Connected: MongoDB {test.version} in {test.latency_ms} ms</Notice>
          : <Notice tone="bad" testId="test-result">{test.error}</Notice>)}
        {s.error && <Notice tone="bad" testId="register-error">{s.error}</Notice>}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn" onClick={doTest} disabled={t.busy || uri.length < 10} data-testid="test-connection">{t.busy && <Spinner />} Test connection</button>
          <button className="btn btn-primary" disabled={s.busy} data-testid="register-instance">{s.busy && <Spinner />} Register</button>
        </div>
      </form>
    </Modal>
  );
}

function InstanceRow({ inst, selected, onSelect, onChanged }: { inst: Instance; selected: boolean; onSelect: () => void; onChanged: () => void }) {
  const act = useAction();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const call = (path: string) => act.run(async () => { await api.post(path); onChanged(); });
  return (
    <>
      <tr className={selected ? "bg-accent/5" : undefined} data-testid="instance-row">
        <td className="td"><button className="font-medium text-accent hover:underline" onClick={onSelect}>{inst.name}</button> {inst.is_demo && <Badge>demo</Badge>}</td>
        <td className="td font-mono text-xs">{inst.host_display}</td>
        <td className="td">{inst.server_version ?? "—"}</td>
        <td className="td"><StatusBadge status={inst.status} />{inst.last_error && <div className="mt-1 max-w-xs text-xs text-bad">{inst.last_error}</div>}</td>
        <td className="td">{inst.allow_modifications ? <Badge tone="border-warn/50 text-warn">modifiable</Badge> : <Badge>read-only</Badge>}</td>
        <td className="td text-xs text-muted">{ago(inst.last_check_at)}</td>
        <td className="td">
          <div className="flex flex-wrap gap-1">
            <button className="btn" disabled={act.busy} onClick={() => call(`/databases/${inst.id}/check`)}>Check</button>
            {inst.enabled
              ? <button className="btn" disabled={act.busy} onClick={() => call(`/databases/${inst.id}/disconnect`)} data-testid="disconnect">Disconnect</button>
              : <button className="btn" disabled={act.busy} onClick={() => call(`/databases/${inst.id}/connect`)}>Connect</button>}
            <button className="btn btn-danger" onClick={() => setConfirmDelete(true)} data-testid="delete-instance">Delete</button>
          </div>
          {act.error && <div className="mt-1 text-xs text-bad">{act.error}</div>}
        </td>
      </tr>
      {confirmDelete && (
        <ConfirmDialog title={`Delete ${inst.name}`} danger confirmLabel="Delete instance and its history" busy={act.busy}
          onCancel={() => setConfirmDelete(false)}
          onConfirm={async () => { await act.run(async () => { await api.del(`/databases/${inst.id}`); onChanged(); }); setConfirmDelete(false); }}>
          <p className="text-sm">This removes the registration and <b>all DB Pulse history</b> for it (metrics, slow queries, alerts, plans, benchmarks). The MongoDB server itself is not touched.</p>
        </ConfirmDialog>
      )}
    </>
  );
}

function CollectionDetail({ instanceId, db, coll }: { instanceId: string; db: string; coll: string }) {
  const { data, loading, error } = useAsync(() => api.get<IndexInvestigation>(`/databases/${instanceId}/explorer/${db}/${coll}/indexes`), [instanceId, db, coll]);
  if (loading) return <LoadingState />;
  if (error) return <ErrorState message={error} />;
  if (!data) return null;
  return (
    <div className="space-y-3">
      <Table head={<tr><th className="th">Index</th><th className="th">Keys</th><th className="th">Options</th><th className="th">Accesses</th><th className="th">Size</th></tr>}>
        {data.indexes.map((i) => (
          <tr key={i.name}>
            <td className="td font-mono text-xs">{i.name}</td>
            <td className="td font-mono text-xs">{keyPattern(i.key)}</td>
            <td className="td text-xs">{[i.unique && "unique", i.sparse && "sparse", i.partial && "partial", i.ttl && "ttl"].filter(Boolean).join(", ") || "—"}</td>
            <td className="td">{num(i.accesses)}</td>
            <td className="td">{bytes(i.size_bytes)}</td>
          </tr>
        ))}
      </Table>
      {!data.index_stats_available && <Notice tone="warn">$indexStats is unavailable here: access counts are shown as “—”, not zero.</Notice>}
      {data.issues.length > 0 && <ul className="space-y-1 text-sm">{data.issues.map((i, k) => <li key={k}><Badge>{i.type.replace("_", " ")}</Badge> {i.message}</li>)}</ul>}
    </div>
  );
}

export default function ExplorerPage() {
  const { instances, instance, instanceId, setInstanceId, reloadInstances } = useApp();
  const [adding, setAdding] = useState(false);
  const [db, setDb] = useState<string | null>(null);
  const [coll, setColl] = useState<string | null>(null);
  const dbs = useAsync(() => (instanceId && instance?.enabled ? api.get<{ databases: DbInfo[] }>(`/databases/${instanceId}/explorer`) : null), [instanceId, instance?.enabled, instance?.status]);
  const colls = useAsync(() => (instanceId && db ? api.get<{ collections: CollectionInfo[] }>(`/databases/${instanceId}/explorer/${db}/collections`) : null), [instanceId, db]);

  return (
    <>
      <PageHeader title="Database explorer" subtitle="Registered instances, databases, collections and indexes (subject to the connection's permissions)."
        actions={<button className="btn btn-primary" onClick={() => setAdding(true)} data-testid="add-instance">Register instance</button>} />
      <div className="space-y-5">
        <Card title="Registered instances">
          {instances.length === 0 ? <EmptyState title="No instances yet" hint="Register a MongoDB connection string to begin." /> : (
            <Table testId="instances-table" head={<tr>{["Name", "Host", "Version", "Status", "Mode", "Checked", ""].map((h) => <th key={h} className="th">{h}</th>)}</tr>}>
              {instances.map((i) => (
                <InstanceRow key={i.id} inst={i} selected={i.id === instanceId} onSelect={() => { setInstanceId(i.id); setDb(null); setColl(null); }} onChanged={() => { void reloadInstances(); }} />
              ))}
            </Table>
          )}
        </Card>
        {!instance ? <NoInstance /> : !instance.enabled ? <Notice tone="info">This instance is disconnected. Reconnect it to explore.</Notice> : (
          <div className="grid gap-5 xl:grid-cols-3">
            <Card title={`Databases on ${instance.name}`}>
              {dbs.loading ? <LoadingState /> : dbs.error ? <ErrorState message={dbs.error} onRetry={dbs.reload} /> :
                !dbs.data?.databases.length ? <EmptyState title="No visible databases" hint="The connection may lack permission to list databases." /> : (
                  <ul className="divide-y divide-line" data-testid="db-list">
                    {dbs.data.databases.map((d) => (
                      <li key={d.name}>
                        <button className={`flex w-full justify-between px-2 py-2 text-left text-sm hover:bg-raised ${db === d.name ? "text-accent" : ""}`} onClick={() => { setDb(d.name); setColl(null); }}>
                          <span>{d.name}</span><span className="text-muted">{bytes(d.size_on_disk)}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
            </Card>
            <Card title={db ? `Collections in ${db}` : "Collections"} className="xl:col-span-2">
              {!db ? <p className="text-sm text-muted">Select a database.</p> : colls.loading ? <LoadingState /> : colls.error ? <ErrorState message={colls.error} onRetry={colls.reload} /> : (
                <Table testId="collections-table" head={<tr>{["Collection", "Documents", "Size", "Avg doc", "Indexes", "Index size"].map((h) => <th key={h} className="th">{h}</th>)}</tr>}>
                  {colls.data?.collections.map((c) => (
                    <tr key={c.name} className={`cursor-pointer hover:bg-raised ${coll === c.name ? "bg-accent/5" : ""}`} onClick={() => setColl(c.name)}>
                      <td className="td font-medium text-accent">{c.name}{c.type !== "collection" && <span className="ml-1 text-xs text-muted">({c.type})</span>}</td>
                      <td className="td">{num(c.document_count)}</td><td className="td">{bytes(c.size_bytes)}</td><td className="td">{bytes(c.avg_obj_size)}</td>
                      <td className="td">{num(c.index_count)}</td><td className="td">{bytes(c.total_index_size)}</td>
                    </tr>
                  ))}
                </Table>
              )}
            </Card>
            {db && coll && instanceId && <Card title={`Indexes on ${db}.${coll}`} className="xl:col-span-3"><CollectionDetail instanceId={instanceId} db={db} coll={coll} /></Card>}
          </div>
        )}
      </div>
      {adding && <AddInstance onClose={() => setAdding(false)} onDone={() => { setAdding(false); void reloadInstances(); }} />}
    </>
  );
}
