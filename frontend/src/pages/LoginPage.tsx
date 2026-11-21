import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useApp } from "../hooks/AppContext";
import { useAction } from "../hooks/useAsync";
import { Input, Notice, Spinner, Tabs } from "../components/ui";

export default function LoginPage() {
  const { user, login, register } = useApp();
  const nav = useNavigate();
  const loc = useLocation();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const { busy, error, run, setError } = useAction();
  const from = (loc.state as { from?: string } | null)?.from ?? "/";
  if (user) return <Navigate to={from} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const ok = await run(async () => {
      if (mode === "login") await login(email, password);
      else await register(email, password, name);
      return true;
    });
    if (ok) nav(from, { replace: true });
  };

  return (
    <div className="flex min-h-full items-center justify-center p-4">
      <div className="card w-full max-w-md p-6 fade-in">
        <div className="mb-5 flex items-center gap-2">
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="rgb(var(--accent))" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M2 12h4l3-8 4 16 3-8h6" /></svg>
          <h1 className="text-2xl font-semibold">DB Pulse</h1>
        </div>
        <p className="mb-4 text-sm text-muted">MongoDB performance investigation. Evidence first, recommendations second.</p>
        <Tabs tabs={[{ id: "login", label: "Sign in" }, { id: "register", label: "Register" }]} value={mode} onChange={(m) => { setMode(m); setError(null); }} />
        <form onSubmit={submit} className="space-y-3" data-testid={`${mode}-form`}>
          {mode === "register" && <Input label="Name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} autoComplete="name" data-testid="name" />}
          <Input label="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" data-testid="email" />
          <Input
            label="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required
            autoComplete={mode === "login" ? "current-password" : "new-password"} minLength={mode === "register" ? 10 : undefined}
            hint={mode === "register" ? "At least 10 characters with a letter and a digit. The first account becomes administrator." : undefined}
            data-testid="password"
          />
          {error && <Notice tone="bad" testId="auth-error">{error}</Notice>}
          <button className="btn btn-primary w-full" disabled={busy} data-testid="submit">
            {busy && <Spinner />} {mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>
      </div>
    </div>
  );
}
