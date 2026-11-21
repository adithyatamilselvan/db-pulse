import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { PlotChart } from "../charts/PlotChart";
import { PlanView, RecommendationCard } from "../components/shared";
import { Card, ErrorState, JsonView, LoadingState, Notice, PageHeader, SeverityBadge, SourceBadge, Spinner, Stat, Table } from "../components/ui";
import { useAction, useAsync } from "../hooks/useAsync";
import { api } from "../services/api";
import type { AiAnswer, Alert, ExecutionPlan, QueryPattern, SlowQuery } from "../types";
import { dateTime, ms, num } from "../utils/format";

interface Detail { pattern: QueryPattern; executions: SlowQuery[]; plans: ExecutionPlan[]; alerts: Alert[] }

const QUICK = ["Why is this query slow?", "Could an index improve this query?", "Why is documentsExamined so high?", "What should I investigate next?"];

export default function InvestigationPage() {
  const { instanceId = "", fingerprint = "" } = useParams();
  const nav = useNavigate();
  const [search] = useSearchParams();
  const source = search.get("source") ?? undefined; // real and synthetic patterns can share a fingerprint
  const detail = useAsync(() => api.get<Detail>(`/queries/patterns/${instanceId}/${fingerprint}`, { source }), [instanceId, fingerprint, source]);
  const investigate = useAction();
  const ask = useAction();
  const [plan, setPlan] = useState<ExecutionPlan | null>(null);
  const [answer, setAnswer] = useState<AiAnswer | null>(null);
  const [question, setQuestion] = useState(QUICK[0]);

  if (detail.loading && !detail.data) return <LoadingState />;
  if (detail.error) return <ErrorState message={detail.error} onRetry={detail.reload} />;
  if (!detail.data) return null;
  const { pattern, executions, alerts } = detail.data;
  const synthetic = pattern.source === "SYNTHETIC";
  const shownPlan = plan ?? null;
  const storedPlan = detail.data.plans[0] ?? null;

  const runExplain = async () => {
    const r = await investigate.run(() => api.post<{ plan: ExecutionPlan }>(`/queries/patterns/${instanceId}/${fingerprint}/investigate`, {}, { source }));
    if (r) { setPlan(r.plan); detail.reload(); }
  };
  const askAi = async (q: string) => {
    const r = await ask.run(() => api.post<{ answer: AiAnswer }>("/ai/investigate", {
      instance_id: instanceId, question: q, fingerprint, source: source ?? pattern.source, plan_id: (shownPlan ?? storedPlan)?.id,
    }));
    if (r) setAnswer(r.answer);
  };
  const s = pattern.stats;
  const times = [...executions].reverse();

  return (
    <>
      <PageHeader
        title="Query investigation"
        subtitle={<><span className="font-mono">{pattern.database}.{pattern.collection}</span> · {pattern.operation} · fingerprint <span className="font-mono">{pattern.fingerprint}</span> <SourceBadge source={pattern.source} /></>}
        actions={<><button className="btn" onClick={() => nav(-1)}>Back</button>
          <button className="btn btn-primary" onClick={runExplain} disabled={investigate.busy || synthetic} data-testid="run-explain" title={synthetic ? "Synthetic patterns have no real query to explain" : undefined}>
            {investigate.busy && <Spinner />} Run explain now</button></>}
      />
      {synthetic && <div className="mb-4"><Notice tone="warn">This is a SYNTHETIC demo pattern. Its figures are generated, and no real query exists to explain.</Notice></div>}
      {investigate.error && <div className="mb-4"><Notice tone="bad">{investigate.error}</Notice></div>}
      <div className="space-y-5">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <Stat label="Executions" value={num(pattern.count)} /><Stat label="Average" value={ms(s.avg_ms)} />
          <Stat label="P95" value={ms(s.p95_ms)} /><Stat label="P99" value={ms(s.p99_ms)} /><Stat label="Max" value={ms(s.max_ms)} />
        </div>
        <Card title="Query shape (values redacted)"><JsonView value={pattern.shape} maxHeight="10rem" /></Card>
        <Card title="Execution duration history">
          {times.length < 2 ? <p className="text-sm text-muted">Not enough executions to chart.</p> : (
            <PlotChart height={240} data={[
              { x: times.map((t) => t.ts), y: times.map((t) => t.duration_ms), type: "scatter", mode: "lines+markers", name: "Duration (ms)" },
              { x: times.map((t) => t.ts), y: times.map((t) => t.threshold_ms), type: "scatter", mode: "lines", name: "Threshold", line: { dash: "dot", color: "#f87171" } },
            ]} layout={{ yaxis: { title: { text: "ms" } } }} />
          )}
        </Card>

        <Card title="Execution plan analysis" actions={(shownPlan ?? storedPlan) && <span className="text-xs text-muted">explained {dateTime((shownPlan ?? storedPlan)!.created_at)}</span>}>
          {(shownPlan ?? storedPlan) ? (
            <div className="space-y-4">
              <PlanView a={(shownPlan ?? storedPlan)!.analysis} />
              <RecommendationCard rec={(shownPlan ?? storedPlan)!.recommendation} onTest={() => nav("/benchmarks", { state: { prefill: { plan: shownPlan ?? storedPlan } } })} />
            </div>
          ) : <p className="text-sm text-muted">No explain has been run for this pattern yet. {synthetic ? "" : "Click “Run explain now” to run a real explain(executionStats)."}</p>}
        </Card>

        <Card title="Recent executions">
          <Table testId="executions-table" head={<tr>{["Time", "Duration", "Severity", "Examined", "Keys", "Returned", "Plan"].map((h) => <th key={h} className="th">{h}</th>)}</tr>}>
            {executions.slice(0, 20).map((e) => (
              <tr key={e.id}><td className="td text-xs">{dateTime(e.ts)}</td><td className="td tabular-nums">{ms(e.duration_ms)}</td><td className="td"><SeverityBadge severity={e.severity} /></td>
                <td className="td tabular-nums">{num(e.docs_examined)}</td><td className="td tabular-nums">{num(e.keys_examined)}</td><td className="td tabular-nums">{num(e.docs_returned)}</td>
                <td className="td font-mono text-xs">{e.plan_summary || "—"}</td></tr>
            ))}
          </Table>
        </Card>

        {alerts.length > 0 && (
          <Card title="Related alerts"><ul className="space-y-1 text-sm">{alerts.map((a) => <li key={a.id}><SeverityBadge severity={a.severity} /> {a.title} <span className="text-xs text-muted">×{a.occurrences} · {a.status}</span></li>)}
            <li><Link className="text-accent underline" to="/alerts">Open Alerts center</Link></li></ul></Card>
        )}

        <Card title="AI investigation">
          <div className="flex flex-wrap gap-2">
            {QUICK.map((q) => <button key={q} className="btn" onClick={() => { setQuestion(q); void askAi(q); }} disabled={ask.busy}>{q}</button>)}
          </div>
          <div className="mt-3 flex gap-2">
            <input className="input" aria-label="Your question" value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={1000} />
            <button className="btn btn-primary" disabled={ask.busy || question.trim().length < 3} onClick={() => askAi(question)} data-testid="ask-ai">{ask.busy && <Spinner />} Ask</button>
          </div>
          {ask.error && <div className="mt-3"><Notice tone="bad">{ask.error}</Notice></div>}
          {answer && <div className="mt-4"><AnswerView a={answer} /></div>}
        </Card>
      </div>
    </>
  );
}

export function AnswerView({ a }: { a: AiAnswer }) {
  const list = (items: string[]) => <ul className="list-disc space-y-1 pl-5">{items.map((x, i) => <li key={i}>{x}</li>)}</ul>;
  return (
    <div className="space-y-3 text-sm" data-testid="ai-answer">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded border border-line px-1.5 py-0.5 text-[11px] font-semibold uppercase" data-testid="answer-mode">{a.mode === "cohere" ? `Cohere${a.model ? ` · ${a.model}` : ""}` : "Local rule-based"}</span>
        <span className="text-xs text-muted">confidence {a.confidence}</span>
      </div>
      {a.fallback_reason && <Notice tone="warn" testId="fallback-reason">{a.fallback_reason}</Notice>}
      <div><h4 className="label">Problem summary</h4><p>{a.problem_summary}</p></div>
      <div className="rounded-md border border-ok/40 bg-ok/5 p-3"><h4 className="label text-ok">Observed evidence (measured)</h4>{list(a.observed_evidence)}</div>
      <div className="rounded-md border border-warn/40 bg-warn/5 p-3"><h4 className="label text-warn">Likely causes (hypotheses)</h4>{list(a.likely_causes)}</div>
      <div><h4 className="label">Possible optimizations</h4>{list(a.possible_optimizations)}</div>
      <div><h4 className="label">Risks and trade-offs</h4>{list(a.risks_tradeoffs)}</div>
      <div><h4 className="label">Validation procedure</h4><ol className="list-decimal space-y-1 pl-5">{a.validation_procedure.map((x, i) => <li key={i}>{x}</li>)}</ol></div>
      {a.evidence_refs.length > 0 && <div><h4 className="label">Evidence referenced</h4><ul className="text-xs text-muted">{a.evidence_refs.map((r) => <li key={r.type + r.id}>{r.type}: {r.label}</li>)}</ul></div>}
      <p className="text-xs text-muted">{a.hypothesis_notice}</p>
    </div>
  );
}
