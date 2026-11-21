import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, setUnauthorizedHandler, tokenStore } from "../services/api";
import type { AiStatus, Instance, User } from "../types";

interface AuthResponse { access_token: string; user: User }

interface Ctx {
  user: User | null;
  booting: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name: string) => Promise<void>;
  logout: () => void;
  setUser: (u: User) => void;
  instances: Instance[];
  reloadInstances: () => Promise<void>;
  instanceId: string | null;
  setInstanceId: (id: string) => void;
  instance: Instance | null;
  ai: AiStatus | null;
  theme: "dark" | "light";
  toggleTheme: () => void;
}

const AppCtx = createContext<Ctx | null>(null);

function safeGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function safeSet(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* ignore */
  }
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [booting, setBooting] = useState(true);
  const [instances, setInstances] = useState<Instance[]>([]);
  const [instanceId, setInstanceIdState] = useState<string | null>(safeGet("dbpulse.instance"));
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [theme, setTheme] = useState<"dark" | "light">(
    document.documentElement.dataset.theme === "light" ? "light" : "dark",
  );

  const logout = useCallback(() => {
    tokenStore.set(null);
    setUser(null);
    setInstances([]);
    setAi(null);
  }, []);

  const reloadInstances = useCallback(async () => {
    const r = await api.get<{ instances: Instance[] }>("/databases");
    setInstances(r.instances);
    setInstanceIdState((cur) => {
      const next = cur && r.instances.some((i) => i.id === cur) ? cur : (r.instances[0]?.id ?? null);
      if (next) safeSet("dbpulse.instance", next);
      return next;
    });
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(logout);
    return () => setUnauthorizedHandler(null);
  }, [logout]);

  useEffect(() => {
    if (!tokenStore.get()) {
      setBooting(false);
      return;
    }
    api
      .get<{ user: User }>("/auth/me")
      .then((r) => setUser(r.user))
      .catch(() => tokenStore.set(null))
      .finally(() => setBooting(false));
  }, []);

  useEffect(() => {
    if (!user) return;
    reloadInstances().catch(() => undefined);
    api.get<AiStatus>("/ai/status").then(setAi).catch(() => undefined);
  }, [user, reloadInstances]);

  const finishAuth = (r: AuthResponse) => {
    tokenStore.set(r.access_token);
    setUser(r.user);
  };

  const value = useMemo<Ctx>(
    () => ({
      user,
      booting,
      login: async (email, password) => finishAuth(await api.post<AuthResponse>("/auth/login", { email, password })),
      register: async (email, password, name) =>
        finishAuth(await api.post<AuthResponse>("/auth/register", { email, password, name })),
      logout,
      setUser,
      instances,
      reloadInstances,
      instanceId,
      setInstanceId: (id) => {
        safeSet("dbpulse.instance", id);
        setInstanceIdState(id);
      },
      instance: instances.find((i) => i.id === instanceId) ?? null,
      ai,
      theme,
      toggleTheme: () => {
        const next = theme === "dark" ? "light" : "dark";
        document.documentElement.dataset.theme = next;
        safeSet("dbpulse.theme", next);
        setTheme(next);
      },
    }),
    [user, booting, instances, instanceId, ai, theme, logout, reloadInstances],
  );
  return <AppCtx.Provider value={value}>{children}</AppCtx.Provider>;
}

export function useApp(): Ctx {
  const c = useContext(AppCtx);
  if (!c) throw new Error("useApp must be used inside AppProvider");
  return c;
}
