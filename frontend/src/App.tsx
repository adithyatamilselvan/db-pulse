import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AppLayout } from "./layouts/AppLayout";
import { useApp } from "./hooks/AppContext";
import { LoadingState } from "./components/ui";
import LoginPage from "./pages/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import ExplorerPage from "./pages/ExplorerPage";
import SlowQueriesPage from "./pages/SlowQueriesPage";
import InvestigationPage from "./pages/InvestigationPage";
import ExecutionPlansPage from "./pages/ExecutionPlansPage";
import IndexesPage from "./pages/IndexesPage";
import BenchmarksPage from "./pages/BenchmarksPage";
import AlertsPage from "./pages/AlertsPage";
import AssistantPage from "./pages/AssistantPage";
import ReportsPage from "./pages/ReportsPage";
import SettingsPage from "./pages/SettingsPage";

function Protected({ children }: { children: JSX.Element }) {
  const { user, booting } = useApp();
  const loc = useLocation();
  if (booting) return <LoadingState label="Starting DB Pulse…" />;
  if (!user) return <Navigate to="/login" state={{ from: loc.pathname }} replace />;
  return children;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<Protected><AppLayout /></Protected>}>
        <Route index element={<DashboardPage />} />
        <Route path="explorer" element={<ExplorerPage />} />
        <Route path="slow-queries" element={<SlowQueriesPage />} />
        <Route path="investigate/:instanceId/:fingerprint" element={<InvestigationPage />} />
        <Route path="execution-plans" element={<ExecutionPlansPage />} />
        <Route path="indexes" element={<IndexesPage />} />
        <Route path="benchmarks" element={<BenchmarksPage />} />
        <Route path="alerts" element={<AlertsPage />} />
        <Route path="assistant" element={<AssistantPage />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
