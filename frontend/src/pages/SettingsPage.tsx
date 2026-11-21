import { useEffect, useState, type FormEvent } from "react";
import { NoInstance } from "../components/shared";
import { Badge, Card, Check, ConfirmDialog, Input, LoadingState, Notice, PageHeader, Select, Spinner, Tabs, TextArea } from "../components/ui";
import { useApp } from "../hooks/AppContext";
import { useAction, useAsync } from "../hooks/useAsync";
import { api } from "../services/api";
import type { DbInfo, InstanceSettings, ProfilerStatus, Severity, User } from "../types";
import { parseJson } from "../utils/format";

const ALERT_TYPES = ["SLOW_QUERY", "COLLECTION_SCAN", "LATENCY_INCREASE", "HIGH_DOCS_EXAMINED", "EXCESSIVE_QUERY_FREQUENCY", "INEFFICIENT_INDEX_USAGE", "CONNECTION_FAILURE", "MONITORING_FAILURE", "BENCHMARK_REGRESSION"];

function Profile() {
  const { user, setUser } = useApp();
  const [name, setName] = useState(user?.name ?? "");
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const a = useAction();
  const [ok, setOk] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setOk(false);
    const r = await a.run(() => api.patch<{ user: User }>("/auth/me", { name, ...(next ? { current_password: cur, new_password: next } : {}) }));
    if (r) { setUser(r.user); setCur(""); setNext(""); setOk(true); }
  };
  return (
    <Card title="Profile">
      <form onSubmit={submit} className="max-w-md space-y-3" data-testid="profile-form">
        <Input label="Email" value={user?.email ?? ""} disabled readOnly />
        <Input label="Name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} />
        <Input label="Current password (to change it)" type="password" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" />
        <Input label="New password" type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" hint="At least 10 characters with a letter and a digit." />
        {a.error && <Notice tone="bad">{a.error}</Notice>}{ok && <Notice tone="ok">Profile updated.</Notice>}
        <button className="btn btn-primary" disabled={a.busy}>{a.busy && <Spinner />} Save profile</button>
      </form>
    </Card>
  );
}

function InstanceSettingsForm() {
  const { instanceId, instance, reloadInstances } = useApp();
  const q = useAsync(() => (instanceId ? api.get<{ settings: InstanceSettings }>(`/databases/${instanceId}/settings`) : null), [instanceId]);
  const [s, setS] = useState<InstanceSettings | null>(null);
  const [colText, setColText] = useState("{}");
  const [opText, setOpText] = useState("{}");
  const [dbText, setDbText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const save = useAction();
  const cleanup = useAction();
  const [cleaned, setCleaned] = useState<string | null>(null);
  useEffect(() => {
    if (q.data) {
      setS(q.data.settings);
      setColText(JSON.stringify(q.data.settings.collection_thresholds_ms));
      setOpText(JSON.stringify(q.data.settings.operation_thresholds_ms));
      setDbText(q.data.settings.monitored_databases.join(", "));
    }
  }, [q.data]);
  if (!instanceId) return <NoInstance />;
  if (q.loading || !s) return <LoadingState />;
  const set = <K extends keyof InstanceSettings>(k: K, v: InstanceSettings[K]) => setS({ ...s, [k]: v });
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null); setSaved(false);
    let col, op;
    try { col = parseJson(colText, "Collection thresholds") ?? {}; op = parseJson(opText, "Operation thresholds") ?? {}; } catch (x) { setErr(x instanceof Error ? x.message : "Invalid"); return; }
    const body = { ...s, collection_thresholds_ms: col, operation_thresholds_ms: op, monitored_databases: dbText.split(",").map((x) => x.trim()).filter(Boolean) };
    const r = await save.run(() => api.put<{ settings: InstanceSettings }>(`/databases/${instanceId}/settings`, body));
    if (r) { setSaved(true); void reloadInstances(); }
  };
  const num = (k: keyof InstanceSettings, label: string, min = 1, hint?: string) => (
    <Input label={label} type="number" min={min} value={s[k] as number} onChange={(e) => set(k, Number(e.target.value) as never)} hint={hint} />
  );
  return (
    <Card title={`Monitoring & detection — ${instance?.name}`}>
      <form onSubmit={submit} className="space-y-5" data-testid="settings-form">
        <div className="grid gap-3 md:grid-cols-3">
          {num("monitoring_interval_s", "Metrics interval (s)", 5)}
          {num("detection_interval_s", "Slow-query detection interval (s)", 5)}
          {num("slow_threshold_ms", "Slow query threshold (ms)", 1)}
          {num("retention_days", "Data retention (days)", 1, "Older metrics, slow queries, plans and resolved alerts are deleted.")}
          {num("alert_cooldown_s", "Alert cooldown (s)", 0, "Identical alerts inside this window are folded together.")}
          {num("collscan_alert_min_docs", "Collection-scan alert: min docs examined", 0)}
          {num("high_examined_ratio", "High examined/returned ratio", 1)}
          {num("frequency_alert_count", "Frequency alert: slow executions", 1)}
          {num("frequency_window_s", "Frequency window (s)", 10)}
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <TextArea label="Collection-specific thresholds (JSON, ms)" value={colText} onChange={(e) => setColText(e.target.value)} rows={2} hint='e.g. { "orders": 50 }' data-testid="col-thresholds" />
          <TextArea label="Operation-specific thresholds (JSON, ms)" value={opText} onChange={(e) => setOpText(e.target.value)} rows={2} hint='e.g. { "aggregate": 500 } — beats collection thresholds' />
        </div>
        <Input label="Monitored databases (comma-separated; empty = all non-system)" value={dbText} onChange={(e) => setDbText(e.target.value)} />
        <div>
          <h3 className="label">Alert severity overrides</h3>
          <div className="grid gap-2 md:grid-cols-3">
            {ALERT_TYPES.map((t) => (
              <Select key={t} label={t} value={s.severity_overrides[t] ?? ""} onChange={(e) => { const o = { ...s.severity_overrides }; if (e.target.value) o[t] = e.target.value as Severity; else delete o[t]; set("severity_overrides", o); }}>
                <option value="">default</option>{["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"].map((x) => <option key={x}>{x}</option>)}</Select>
            ))}
          </div>
        </div>
        {err && <Notice tone="bad">{err}</Notice>}{save.error && <Notice tone="bad">{save.error}</Notice>}{saved && <Notice tone="ok" testId="saved">Settings saved.</Notice>}
        <div className="flex gap-2">
          <button className="btn btn-primary" disabled={save.busy} data-testid="save-settings">{save.busy && <Spinner />} Save settings</button>
          <button type="button" className="btn" disabled={cleanup.busy} onClick={async () => { const r = await cleanup.run(() => api.post<{ removed: Record<string, number> }>(`/monitoring/${instanceId}/cleanup`)); if (r) setCleaned(Object.entries(r.removed).map(([k, v]) => `${k}: ${v}`).join(", ")); }}>Apply retention now</button>
        </div>
        {cleaned && <Notice tone="ok">Removed — {cleaned}</Notice>}{cleanup.error && <Notice tone="bad">{cleanup.error}</Notice>}
      </form>
    </Card>
  );
}

function ProfilerControl() {
  const { instanceId, instance, user } = useApp();
  const [db, setDb] = useState("");
  const [level, setLevel] = useState(1);
  const [slowms, setSlowms] = useState(100);
  const [confirm, setConfirm] = useState(false);
  const dbs = useAsync(() => (instanceId && instance?.enabled ? api.get<{ databases: DbInfo[] }>(`/databases/${instanceId}/explorer`) : null), [instanceId, instance?.enabled]);
  const st = useAsync(() => (instanceId && db ? api.get<{ status: ProfilerStatus; changes: { id: string; action: string; created_at: string }[]; warning: string }>(`/monitoring/${instanceId}/profiler`, { database: db }) : null), [instanceId, db]);
  const act = useAction();
  const canChange = user?.role === "admin" && instance?.allow_modifications;
  if (!instanceId) return null;
  const apply = async () => { const r = await act.run(() => api.post(`/monitoring/${instanceId}/profiler`, { database: db, level, slowms, confirm: true })); if (r) { setConfirm(false); st.reload(); } };
  const restore = async (changeId: string) => { const r = await act.run(() => api.post(`/monitoring/${instanceId}/profiler/restore`, { change_id: changeId, confirm: true })); if (r) st.reload(); };
  const lastSet = st.data?.changes.find((c) => c.action === "set");
  return (
    <Card title="MongoDB profiler">
      <div className="max-w-xl space-y-3">
        <Notice tone="warn">The profiler is never enabled silently. Enabling it adds overhead on the monitored database. Changes need an administrator, an instance registered as modifiable, and explicit confirmation.</Notice>
        <Select label="Database" value={db} onChange={(e) => setDb(e.target.value)} data-testid="profiler-db"><option value="">Select…</option>{dbs.data?.databases.map((d) => <option key={d.name}>{d.name}</option>)}</Select>
        {db && (st.loading ? <LoadingState /> : st.data && (
          <div className="space-y-3" data-testid="profiler-status">
            {st.data.status.available
              ? <p className="text-sm">Current: <b>level {st.data.status.level}</b> ({st.data.status.level_description}), slowms {st.data.status.slowms}, system.profile {st.data.status.system_profile_present ? "present" : "absent"}</p>
              : <Notice tone="info">Profiler unavailable: {st.data.status.reason}</Notice>}
            <div className="grid grid-cols-2 gap-3">
              <Select label="Level" value={level} onChange={(e) => setLevel(Number(e.target.value))}><option value={0}>0 — off</option><option value={1}>1 — slow operations</option><option value={2}>2 — all operations</option></Select>
              <Input label="Slow threshold (ms)" type="number" min={0} value={slowms} onChange={(e) => setSlowms(Number(e.target.value))} />
            </div>
            <div className="flex gap-2">
              <button className="btn btn-primary" disabled={!canChange || !st.data.status.available} onClick={() => setConfirm(true)} data-testid="set-profiler">Change profiler…</button>
              {lastSet && <button className="btn" disabled={!canChange || act.busy} onClick={() => restore(lastSet.id)}>Restore previous configuration</button>}
            </div>
            {!canChange && <p className="text-xs text-muted">{user?.role !== "admin" ? "Administrator role required." : "Enable “allow modifications” on this instance first."}</p>}
          </div>
        ))}
        {act.error && <Notice tone="bad">{act.error}</Notice>}
      </div>
      {confirm && (
        <ConfirmDialog title="Change profiler configuration" confirmLabel="Apply change" busy={act.busy} onCancel={() => setConfirm(false)} onConfirm={apply}>
          <p className="text-sm">Set profiling on <b>{db}</b> to level <b>{level}</b> with slowms <b>{slowms}</b>. The previous configuration is recorded so you can restore it.</p>
        </ConfirmDialog>
      )}
    </Card>
  );
}

function AiSettings() {
  const { ai } = useApp();
  return (
    <Card title="AI provider">
      <div className="max-w-xl space-y-3 text-sm">
        <p>Active mode: <Badge tone={ai?.mode === "cohere" ? "border-accent/50 text-accent" : undefined}>{ai?.mode === "cohere" ? `Cohere (${ai.model})` : "Local rule-based"}</Badge></p>
        {ai?.note && <Notice tone="warn">{ai.note}</Notice>}
        <p className="text-muted">The provider is configured with environment variables on the server (secrets are never editable from the browser):</p>
        <pre className="rounded-md border border-line bg-bg p-3 font-mono text-xs">{`AI_PROVIDER=cohere\nCOHERE_API_KEY=<your key>\nCOHERE_MODEL=<a model available to your account>`}</pre>
        <p className="text-muted">Without a key DB Pulse stays fully functional using the local deterministic engine. If Cohere errors or is rate limited, answers fall back to local mode and say why.</p>
      </div>
    </Card>
  );
}

function ConnectionSettings() {
  const { instance, reloadInstances } = useApp();
  const a = useAction();
  const t = useAction();
  const [name, setName] = useState(instance?.name ?? "");
  const [uri, setUri] = useState("");
  const [test, setTest] = useState<{ ok: boolean; msg: string } | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => { setName(instance?.name ?? ""); setUri(""); setTest(null); setSaved(false); }, [instance?.id, instance?.name]);
  if (!instance) return <NoInstance />;
  const toggle = () => a.run(async () => { await api.patch(`/databases/${instance.id}`, { allow_modifications: !instance.allow_modifications }); await reloadInstances(); });
  const doTest = async () => {
    const r = await t.run(() => api.post<{ ok: boolean; version: string | null; error: string | null }>("/databases/test", { uri }));
    if (r) setTest({ ok: r.ok, msg: r.ok ? `Connected: MongoDB ${r.version}` : (r.error ?? "Failed") });
  };
  const save = async (e: FormEvent) => {
    e.preventDefault();
    setSaved(false);
    const body: Record<string, unknown> = { name };
    if (uri) body.uri = uri;
    const r = await a.run(() => api.patch(`/databases/${instance.id}`, body));
    if (r) { setUri(""); setSaved(true); await reloadInstances(); }
  };
  return (
    <Card title={`Connection — ${instance.name}`}>
      <form onSubmit={save} className="max-w-xl space-y-3 text-sm" data-testid="connection-form">
        <p>Host: <span className="font-mono">{instance.host_display}</span> · MongoDB {instance.server_version ?? "?"} · credentials are stored encrypted and never displayed.</p>
        <Input label="Display name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} />
        <Input label="New connection string (leave blank to keep the current one)" value={uri} onChange={(e) => { setUri(e.target.value); setTest(null); }} autoComplete="off" placeholder="mongodb://…" data-testid="new-uri" />
        {test && <Notice tone={test.ok ? "ok" : "bad"}>{test.msg}</Notice>}
        <Check checked={instance.allow_modifications} onChange={toggle} disabled={a.busy} label="Allow DB Pulse to modify this instance (create indexes, change profiler). Each change still requires explicit confirmation." />
        {(a.error || t.error) && <Notice tone="bad">{a.error ?? t.error}</Notice>}{saved && <Notice tone="ok">Connection saved.</Notice>}
        <div className="flex gap-2">
          <button type="button" className="btn" disabled={t.busy || uri.length < 10} onClick={doTest}>{t.busy && <Spinner />} Test new connection</button>
          <button className="btn btn-primary" disabled={a.busy}>{a.busy && <Spinner />} Save connection</button>
        </div>
      </form>
    </Card>
  );
}

export default function SettingsPage() {
  const [tab, setTab] = useState<"profile" | "monitoring" | "connection" | "profiler" | "ai">("monitoring");
  const { user } = useApp();
  return (
    <>
      <PageHeader title="Settings" subtitle={`Signed in as ${user?.email}`} />
      <Tabs tabs={[{ id: "monitoring", label: "Monitoring & thresholds" }, { id: "connection", label: "Connection" }, { id: "profiler", label: "Profiler" }, { id: "ai", label: "AI provider" }, { id: "profile", label: "Profile" }]} value={tab} onChange={setTab} />
      {tab === "profile" && <Profile />}
      {tab === "monitoring" && <InstanceSettingsForm />}
      {tab === "connection" && <ConnectionSettings />}
      {tab === "profiler" && <ProfilerControl />}
      {tab === "ai" && <AiSettings />}
    </>
  );
}
