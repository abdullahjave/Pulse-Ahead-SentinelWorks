import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, qs, setToken } from "./api";

export type Page = "overview" | "incidents" | "workplaces" | "validation" | "impact";

interface Route {
  page: Page;
  id?: string;
}

function parseHash(): Route {
  const h = window.location.hash.replace(/^#\/?/, "");
  const [page, id] = h.split("/");
  const pages: Page[] = ["overview", "incidents", "workplaces", "validation", "impact"];
  return { page: (pages.includes(page as Page) ? page : "overview") as Page, id: id ? decodeURIComponent(id) : undefined };
}

interface AppState {
  user: any | null;
  meta: any | null;
  authError: string | null;
  signIn: (userId: string) => Promise<void>;
  scenario: string;
  setScenario: (s: string) => void;
  degraded: boolean;
  setDegraded: (d: boolean) => void;
  asOf: string | null;
  setAsOf: (d: string) => void;
  route: Route;
  navigate: (page: Page, id?: string) => void;
  selectedZone: string | null;
  setSelectedZone: (z: string | null) => void;
  runQuery: string;
  dataVersion: number;
  bumpData: () => void;
}

const Ctx = createContext<AppState | null>(null);

const saved = (k: string, fallback: string) => {
  try {
    return sessionStorage.getItem(k) ?? fallback;
  } catch {
    return fallback;
  }
};
const save = (k: string, v: string) => {
  try {
    sessionStorage.setItem(k, v);
  } catch {
    /* storage unavailable */
  }
};

export function AppProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<any | null>(null);
  const [meta, setMeta] = useState<any | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [scenario, setScenarioRaw] = useState(saved("sw.scenario", "heatwave"));
  const [degraded, setDegradedRaw] = useState(saved("sw.degraded", "false") === "true");
  const [asOf, setAsOfRaw] = useState<string | null>(saved("sw.asOf", "") || null);
  const [route, setRoute] = useState<Route>(parseHash());
  const [selectedZone, setSelectedZone] = useState<string | null>(null);
  const [dataVersion, setDataVersion] = useState(0);

  const signIn = useCallback(async (userId: string) => {
    try {
      const r = await api<any>("/api/auth/login", { method: "POST", body: JSON.stringify({ user_id: userId }) });
      setToken(r.token);
      const m = await api<any>("/api/meta");
      setMeta(m);
      setUser(r.user);
      setAuthError(null);
      save("sw.user", userId);
    } catch (e: any) {
      setAuthError(e.message || "Sign-in failed");
    }
  }, []);

  useEffect(() => {
    signIn(saved("sw.user", "pho-north"));
  }, [signIn]);

  useEffect(() => {
    const on = () => setRoute(parseHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  // pick the scenario's default replay date if none is stored / scenario changed
  useEffect(() => {
    if (meta && !asOf) {
      const s = meta.scenarios.find((x: any) => x.id === scenario);
      if (s) setAsOfRaw(s.default_as_of);
    }
  }, [meta, asOf, scenario]);

  const setScenario = (s: string) => {
    setScenarioRaw(s);
    save("sw.scenario", s);
    const sc = meta?.scenarios.find((x: any) => x.id === s);
    if (sc) {
      setAsOfRaw(sc.default_as_of);
      save("sw.asOf", sc.default_as_of);
    }
    if (route.page === "incidents" && route.id) navigate("incidents");
  };
  const setDegraded = (d: boolean) => {
    setDegradedRaw(d);
    save("sw.degraded", String(d));
    if (route.page === "incidents" && route.id) navigate("incidents");
  };
  const setAsOf = (d: string) => {
    setAsOfRaw(d);
    save("sw.asOf", d);
  };
  const navigate = (page: Page, id?: string) => {
    window.location.hash = `/${page}${id ? "/" + encodeURIComponent(id) : ""}`;
  };

  // `_u` makes every data hook refetch when the signed-in demo user changes (ignored by the API).
  const runQuery = useMemo(() => qs({ scenario, degraded, as_of: asOf, _u: user?.id }), [scenario, degraded, asOf, user?.id]);

  const value: AppState = {
    user, meta, authError, signIn, scenario, setScenario, degraded, setDegraded, asOf, setAsOf,
    route, navigate, selectedZone, setSelectedZone, runQuery, dataVersion, bumpData: () => setDataVersion((v) => v + 1),
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useApp = () => {
  const c = useContext(Ctx);
  if (!c) throw new Error("useApp outside provider");
  return c;
};
