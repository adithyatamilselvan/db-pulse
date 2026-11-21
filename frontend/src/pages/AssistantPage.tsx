import { useState } from "react";
import { NoInstance } from "../components/shared";
import { Card, EmptyState, LoadingState, Notice, PageHeader, Select, Spinner, Badge } from "../components/ui";
import { useApp } from "../hooks/AppContext";
import { useAction, useAsync } from "../hooks/useAsync";
import { api } from "../services/api";
import type { AiAnswer, Comparison, Conversation, ConversationSummary, QueryPattern } from "../types";
import { ago } from "../utils/format";
import { AnswerView } from "./InvestigationPage";

const PROMPTS = ["Why is this query slow?", "Explain this execution plan", "Why was COLLSCAN selected?", "Could an index improve this query?",
  "Why is documentsExamined so high?", "What changed between the two benchmarks?", "Why did query latency increase?", "What should I investigate next?"];

export default function AssistantPage() {
  const { instanceId, ai } = useApp();
  const [convoId, setConvoId] = useState<string | null>(null);
  const [patternKey, setPatternKey] = useState(""); // "<fingerprint>|<source>": a fingerprint alone can be ambiguous
  const [comparisonId, setComparisonId] = useState("");
  const [question, setQuestion] = useState("");
  const send = useAction();
  const convos = useAsync(() => api.get<{ items: ConversationSummary[] }>("/ai/conversations"), []);
  const convo = useAsync(() => (convoId ? api.get<{ conversation: Conversation }>(`/ai/conversations/${convoId}`) : null), [convoId]);
  const patterns = useAsync(() => (instanceId ? api.get<{ items: QueryPattern[] }>("/queries/patterns", { instance_id: instanceId, period: "30d" }) : null), [instanceId]);
  const comparisons = useAsync(() => api.get<{ items: Comparison[] }>("/benchmarks/comparisons"), []);
  if (!instanceId) return <NoInstance title="AI investigation assistant" />;

  const ask = async (q: string) => {
    if (q.trim().length < 3) return;
    const r = await send.run(() => api.post<{ conversation_id: string; answer: AiAnswer }>("/ai/investigate", {
      instance_id: instanceId, question: q, fingerprint: patternKey ? patternKey.split("|")[0] : undefined, source: patternKey ? patternKey.split("|")[1] : undefined, comparison_id: comparisonId || undefined, conversation_id: convoId || undefined }));
    if (r) { setQuestion(""); setConvoId(r.conversation_id); convo.reload(); convos.reload(); }
  };

  return (
    <>
      <PageHeader title="AI investigation assistant"
        subtitle="Answers use only a redacted evidence bundle (no raw documents, values or credentials). Observed evidence is always computed locally; causes and optimizations are hypotheses."
        actions={ai && <Badge testId="assistant-mode" tone={ai.mode === "cohere" ? "border-accent/50 bg-accent/10 text-accent" : undefined}>{ai.mode === "cohere" ? `Cohere · ${ai.model}` : "Local rule-based mode"}</Badge>} />
      {ai?.note && <div className="mb-4"><Notice tone="warn">{ai.note}</Notice></div>}
      <div className="grid gap-5 lg:grid-cols-4">
        <Card title="History" className="lg:col-span-1">
          <button className="btn mb-3 w-full" onClick={() => setConvoId(null)} data-testid="new-conversation">New investigation</button>
          {convos.loading ? <LoadingState /> : !convos.data?.items.length ? <p className="text-sm text-muted">No conversations yet.</p> : (
            <ul className="space-y-1" data-testid="conversation-list">
              {convos.data.items.map((c) => (
                <li key={c.id}><button onClick={() => setConvoId(c.id)} className={`w-full rounded px-2 py-1.5 text-left text-sm hover:bg-raised ${c.id === convoId ? "bg-accent/10 text-accent" : ""}`}>
                  <div className="truncate">{c.title}</div><div className="text-xs text-muted">{ago(c.updated_at)}</div></button></li>
              ))}
            </ul>
          )}
        </Card>
        <div className="space-y-4 lg:col-span-3">
          <Card title="Context" >
            <div className="grid gap-3 md:grid-cols-2">
              <Select label="Query pattern (evidence)" value={patternKey} onChange={(e) => setPatternKey(e.target.value)} data-testid="ctx-pattern">
                <option value="">None</option>{patterns.data?.items.map((p) => <option key={p.id} value={`${p.fingerprint}|${p.source}`}>{p.database}.{p.collection} {p.operation} · {p.fingerprint}{p.source === "SYNTHETIC" ? " (synthetic)" : ""}</option>)}</Select>
              <Select label="Benchmark comparison (evidence)" value={comparisonId} onChange={(e) => setComparisonId(e.target.value)}>
                <option value="">None</option>{comparisons.data?.items.map((c) => <option key={c.id} value={c.id}>{c.before_label} → {c.after_label} ({c.verdict})</option>)}</Select>
            </div>
          </Card>
          <Card title="Conversation">
            {!convoId && !send.busy ? <EmptyState title="Ask a question about your database performance" hint="Pick a query pattern or comparison as context, then choose a prompt below or type your own." /> : convo.loading && !convo.data ? <LoadingState /> : (
              <div className="space-y-4" data-testid="chat">
                {convo.data?.conversation.messages.map((m, i) => m.role === "user"
                  ? <div key={i} className="ml-auto max-w-[85%] rounded-lg bg-accent/10 px-3 py-2 text-sm" data-testid="user-msg">{m.content as string}</div>
                  : <div key={i} className="card p-4"><AnswerView a={m.content as AiAnswer} /></div>)}
                {send.busy && <div className="flex items-center gap-2 text-sm text-muted"><Spinner /> Investigating…</div>}
              </div>
            )}
            {send.error && <div className="mt-3"><Notice tone="bad" testId="ai-error">{send.error}</Notice></div>}
            <div className="mt-4 flex flex-wrap gap-2">{PROMPTS.map((p) => <button key={p} className="btn text-xs" disabled={send.busy} onClick={() => void ask(p)}>{p}</button>)}</div>
            <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); void ask(question); }}>
              <input className="input" aria-label="Your question" placeholder="Ask about a slow query, a plan, an index or a benchmark…" value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={1000} data-testid="question" />
              <button className="btn btn-primary" disabled={send.busy || question.trim().length < 3} data-testid="send">Ask</button>
            </form>
          </Card>
        </div>
      </div>
    </>
  );
}
