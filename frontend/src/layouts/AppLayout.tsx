import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useApp } from "../hooks/AppContext";
import { Badge, cx, ErrorBoundary } from "../components/ui";

const NAV: { to: string; label: string; icon: string }[] = [
  { to: "/", label: "Dashboard", icon: "M3 12h4l3-8 4 16 3-8h4" },
  { to: "/explorer", label: "Database explorer", icon: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zm0 0v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" },
  { to: "/slow-queries", label: "Slow queries", icon: "M12 6v6l4 2M12 21a9 9 0 100-18 9 9 0 000 18z" },
  { to: "/execution-plans", label: "Execution plans", icon: "M6 3v6m0 0a3 3 0 103 3H18m-12-3a3 3 0 110 0M18 12v9" },
  { to: "/indexes", label: "Index investigator", icon: "M4 6h16M4 12h10M4 18h6" },
  { to: "/benchmarks", label: "Benchmark lab", icon: "M9 3h6M10 3v6L5 19a2 2 0 002 3h10a2 2 0 002-3l-5-10V3" },
  { to: "/alerts", label: "Alerts", icon: "M15 17h5l-1.4-1.4A2 2 0 0118 14.2V11a6 6 0 10-12 0v3.2a2 2 0 01-.6 1.4L4 17h5m6 0a3 3 0 11-6 0" },
  { to: "/assistant", label: "AI assistant", icon: "M8 10h8M8 14h5M21 12a9 9 0 11-4-7.5L21 3v5" },
  { to: "/reports", label: "Reports", icon: "M7 3h8l4 4v14H7zM15 3v4h4M10 12h6M10 16h6" },
  { to: "/settings", label: "Settings", icon: "M12 15a3 3 0 100-6 3 3 0 000 6zM19 12l2-1-2-4-2 1a7 7 0 00-2-1l-.5-2h-4L10 7a7 7 0 00-2 1l-2-1-2 4 2 1a7 7 0 000 2l-2 1 2 4 2-1a7 7 0 002 1l.5 2h4L14 19a7 7 0 002-1l2 1 2-4-2-1a7 7 0 000-2z" },
];

export function AppLayout() {
  const { user, logout, instances, instanceId, setInstanceId, ai, theme, toggleTheme } = useApp();
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  return (
    <div className="flex h-full">
      <aside
        className={cx(
          "fixed inset-y-0 left-0 z-40 w-60 shrink-0 border-r border-line bg-panel transition-transform md:static md:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
        aria-label="Primary"
      >
        <div className="flex h-14 items-center gap-2 border-b border-line px-4">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="rgb(var(--accent))" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M2 12h4l3-8 4 16 3-8h6" /></svg>
          <span className="text-lg font-semibold tracking-tight">DB Pulse</span>
        </div>
        <nav className="space-y-0.5 p-2" data-testid="main-nav">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === "/"}
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                cx("flex items-center gap-3 rounded-md px-3 py-2 text-sm", isActive ? "bg-accent/10 font-medium text-accent" : "text-muted hover:bg-raised hover:text-ink")
              }
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d={n.icon} /></svg>
              {n.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      {open && <div className="fixed inset-0 z-30 bg-black/50 md:hidden" onClick={() => setOpen(false)} aria-hidden />}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-panel px-4">
          <button className="btn md:hidden" aria-label="Open navigation" onClick={() => setOpen(true)}>☰</button>
          <label className="sr-only" htmlFor="instance-select">Active database instance</label>
          <select
            id="instance-select"
            data-testid="instance-select"
            className="input max-w-[16rem]"
            value={instanceId ?? ""}
            onChange={(e) => setInstanceId(e.target.value)}
            disabled={!instances.length}
          >
            {!instances.length && <option value="">No instances</option>}
            {instances.map((i) => <option key={i.id} value={i.id}>{i.name}{i.is_demo ? " (demo)" : ""}</option>)}
          </select>
          <div className="ml-auto flex items-center gap-3">
            {ai && (
              <Badge
                testId="ai-mode"
                tone={ai.mode === "cohere" ? "border-accent/50 bg-accent/10 text-accent" : "border-line text-muted"}
                title={ai.note ?? (ai.mode === "cohere" ? `Cohere model ${ai.model}` : "Deterministic rule-based analysis; no external AI")}
              >
                AI: {ai.mode === "cohere" ? "Cohere" : "Local"}
              </Badge>
            )}
            <button className="btn" onClick={toggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} data-testid="theme-toggle">
              {theme === "dark" ? "☀" : "☾"}
            </button>
            <span className="hidden text-sm text-muted sm:inline" data-testid="user-name">{user?.name} · {user?.role}</span>
            <button className="btn" data-testid="logout" onClick={() => { logout(); nav("/login"); }}>Log out</button>
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto p-4 md:p-6">
          <ErrorBoundary>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
