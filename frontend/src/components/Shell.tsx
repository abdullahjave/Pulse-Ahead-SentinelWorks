import { Activity, BarChart3, Building2, ChevronLeft, ChevronRight, FlaskConical, LayoutDashboard, Lock, Menu, Pause, Play, Siren, SlidersHorizontal, UserRound, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useApp, type Page } from "../state";
import { cx, fmtDate } from "./ui";

const NAV: { page: Page; label: string; icon: any }[] = [
  { page: "overview", label: "Situation overview", icon: LayoutDashboard },
  { page: "incidents", label: "Incidents", icon: Siren },
  { page: "workplaces", label: "Workplaces & privacy", icon: Building2 },
  { page: "validation", label: "Validation", icon: FlaskConical },
  { page: "impact", label: "Impact estimates", icon: BarChart3 },
];

function addDays(iso: string, n: number) {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
const dayDiff = (a: string, b: string) => Math.round((Date.parse(b) - Date.parse(a)) / 86400000);

function ReplayControls() {
  const { meta, asOf, setAsOf, scenario } = useApp();
  const [playing, setPlaying] = useState(false);
  const timer = useRef<number | null>(null);
  const start = meta?.dates.detection_start as string;
  const end = meta?.dates.end as string;
  const total = start && end ? dayDiff(start, end) : 0;
  const idx = asOf && start ? dayDiff(start, asOf) : 0;

  useEffect(() => {
    if (!playing) return;
    timer.current = window.setInterval(() => {
      const cur = sessionStorage.getItem("sw.asOf") || asOf || start;
      if (cur >= end) {
        setPlaying(false);
        return;
      }
      setAsOf(addDays(cur, 1));
    }, 1400);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [playing]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!meta || !asOf) return null;
  const keyDates: any[] = (meta.scenarios.find((s: any) => s.id === scenario)?.key_dates || []).map((k: any, i: number) => ({ ...k, id: `${k.id}#${i}` }));
  const step = (n: number) => {
    const next = addDays(asOf, n);
    if (next >= start && next <= end) setAsOf(next);
  };
  return (
    <div className="flex min-w-0 flex-wrap items-end gap-2">
      <div className="flex flex-col gap-0.5">
        <span className="text-[11px] font-medium text-slate-300">Replay date</span>
        <div className="flex h-8 items-center gap-1 rounded-md border border-navy-600 bg-navy-800 px-1">
          <button aria-label="Previous day" onClick={() => step(-1)} className="rounded p-1 text-slate-200 hover:bg-navy-700 disabled:opacity-40" disabled={asOf <= start}>
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="w-[104px] whitespace-nowrap text-center text-sm font-medium tabular-nums text-white" aria-live="polite">{fmtDate(asOf, { day: "numeric", month: "short", year: "numeric" })}</span>
          <button aria-label="Next day" onClick={() => step(1)} className="rounded p-1 text-slate-200 hover:bg-navy-700 disabled:opacity-40" disabled={asOf >= end}>
            <ChevronRight className="h-4 w-4" />
          </button>
          <button aria-label={playing ? "Pause replay" : "Play replay"} onClick={() => setPlaying((p) => !p)}
            className={cx("ml-0.5 rounded p-1", playing ? "bg-teal-600 text-white" : "text-teal-300 hover:bg-navy-700")}>
            {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
          </button>
        </div>
      </div>
      <input type="range" min={0} max={total} value={idx} aria-label="Replay date slider"
        onChange={(e) => setAsOf(addDays(start, Number(e.target.value)))}
        className="replay mb-2 hidden w-36 xl:block 2xl:w-44" />
      <label className="flex flex-col gap-0.5">
        <span className="text-[11px] font-medium text-slate-300">Jump to event</span>
        <select aria-label="Jump to a scenario event date" value={keyDates.find((k: any) => k.date === asOf)?.id ?? ""}
          onChange={(e) => { const k = keyDates.find((x: any) => x.id === e.target.value); if (k) { setPlaying(false); setAsOf(k.date); } }}
          title="Jump the replay to a date defined by this scenario's own event timing"
          className="h-8 w-[190px] max-w-full truncate rounded-md border border-navy-600 bg-navy-800 px-2 text-sm text-white">
          <option value="" disabled>Choose a date…</option>
          {keyDates.map((k: any) => <option key={k.id + k.date} value={k.id}>{fmtDate(k.date)} · {k.label}</option>)}
        </select>
      </label>
    </div>
  );
}

function TopBar({ onMenu }: { onMenu: () => void }) {
  const { meta, user, signIn, scenario, setScenario, degraded, setDegraded, asOf } = useApp();
  const [open, setOpen] = useState(false);
  return (
    <div className="bg-navy-900 text-white">
      <div className="flex flex-wrap items-end gap-x-4 gap-y-2 px-4 py-2.5 lg:px-6">
        <button className="mb-1 rounded p-1 text-slate-200 hover:bg-navy-800 lg:hidden" aria-label="Open navigation" onClick={onMenu}>
          <Menu className="h-5 w-5" />
        </button>
        <button className="mb-1 flex items-center gap-1.5 rounded-md border border-navy-600 bg-navy-800 px-2.5 py-1 text-xs font-medium text-slate-100 lg:hidden"
          aria-expanded={open} aria-controls="replay-controls" onClick={() => setOpen((o) => !o)}>
          <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden />{open ? "Hide controls" : `Scenario, replay and role${asOf ? " · " + fmtDate(asOf) : ""}`}
        </button>
        <div id="replay-controls" className={cx(open ? "flex" : "hidden", "w-full flex-wrap items-end gap-x-4 gap-y-2 lg:contents")}>
        <label className="flex min-w-[200px] max-w-[400px] flex-1 flex-col gap-0.5">
          <span className="text-[11px] font-medium text-slate-300">Scenario (synthetic replay)</span>
          <select value={scenario} onChange={(e) => setScenario(e.target.value)}
            title={meta?.scenarios.find((s: any) => s.id === scenario)?.label}
            className="h-8 truncate rounded-md border border-navy-600 bg-navy-800 px-2 text-sm text-white">
            {meta?.scenarios.map((s: any) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
        </label>
        <ReplayControls />
        <label className="mb-0.5 flex cursor-pointer items-center gap-2 rounded-md border border-navy-600 bg-navy-800 px-2.5 py-1.5 text-xs">
          <input type="checkbox" checked={degraded} onChange={(e) => setDegraded(e.target.checked)} className="h-3.5 w-3.5 accent-amber-500" />
          <span className="leading-tight">
            <span className="font-medium text-white">Simulate degraded inputs</span>
            <span className="block text-[11px] text-slate-300">30% GPS removed · clinic +2 d</span>
          </span>
        </label>
        <div className="flex items-end gap-2 xl:ml-auto">
          <label className="flex flex-col gap-0.5">
            <span className="flex items-center gap-1 text-[11px] font-medium text-slate-300"><UserRound className="h-3 w-3" />Role (demo sign-in)</span>
            <select value={user?.id || ""} onChange={(e) => signIn(e.target.value)}
              className="h-8 rounded-md border border-navy-600 bg-navy-800 px-2 text-sm text-white">
              {meta?.users.map((u: any) => <option key={u.id} value={u.id}>{u.role_label}</option>)}
            </select>
          </label>
        </div>
        </div>
      </div>
      <div className={cx("flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-1.5 text-xs lg:px-6", degraded ? "bg-amber-100 text-amber-950" : "bg-teal-50 text-teal-950")}>
        <span className="inline-flex items-center gap-1.5 font-semibold"><Activity className="h-3.5 w-3.5" aria-hidden />Synthetic data · Replay mode</span>
        <span className="opacity-80">Replayed from a reproducible synthetic dataset; not a live feed. Each replay date shows only data that would have been available on that day.</span>
        {degraded && <span className="font-semibold">Degraded-input simulation is ON.</span>}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-navy-800 bg-navy-950 px-4 py-1.5 text-xs text-slate-200 lg:px-6" role="note" aria-label="Privacy guard">
        <span className="inline-flex items-center gap-1.5 font-semibold text-white"><Lock className="h-3.5 w-3.5 text-teal-300" aria-hidden />Privacy guard active</span>
        <span>Counts below 5 are masked · workplace counts are released as complete weeks only · aggregates only: no names, employee IDs or disciplinary fields</span>
      </div>
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const { route, navigate, user } = useApp();
  const [open, setOpen] = useState(false);
  const nav = useMemo(() => NAV, []);
  const side = (
    <nav aria-label="Main" className="flex h-full flex-col bg-navy-950 text-slate-200">
      <div className="flex items-center gap-2.5 px-5 py-4">
        <img src="/logo.png" alt="SentinelWorks logo" className="h-9 w-9 rounded-lg" />
        <div>
          <div className="text-sm font-semibold tracking-tight text-white">SentinelWorks</div>
          <div className="text-[11px] text-slate-400">Workforce health early warning</div>
        </div>
      </div>
      <ul className="mt-2 flex flex-col gap-0.5 px-3">
        {nav.map((n) => {
          const active = route.page === n.page;
          const Icon = n.icon;
          return (
            <li key={n.page}>
              <button onClick={() => { navigate(n.page); setOpen(false); }} aria-current={active ? "page" : undefined}
                className={cx("flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                  active ? "bg-navy-800 font-medium text-white ring-1 ring-navy-600" : "text-slate-300 hover:bg-navy-900 hover:text-white")}>
                <Icon className={cx("h-4 w-4", active ? "text-teal-300" : "text-slate-400")} aria-hidden />
                {n.label}
              </button>
            </li>
          );
        })}
      </ul>
      <div className="mt-auto space-y-2 border-t border-navy-800 px-5 py-4 text-[11px] leading-relaxed text-slate-400">
        <p><span className="font-medium text-slate-200">Signed in as</span><br />{user?.name || "…"}</p>
        <p>Population Health · Challenge 06 prototype. Suggested actions need review by the responsible officer.</p>
      </div>
    </nav>
  );
  return (
    <div className="flex h-full">
      <aside className="hidden w-60 shrink-0 lg:block">{side}</aside>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <div className="absolute inset-0 bg-navy-950/60" onClick={() => setOpen(false)} />
          <div className="relative h-full w-64">
            {side}
            <button className="absolute right-2 top-3 rounded p-1 text-slate-300 hover:bg-navy-800" aria-label="Close navigation" onClick={() => setOpen(false)}><X className="h-5 w-5" /></button>
          </div>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar onMenu={() => setOpen(true)} />
        <main className="scroll-thin min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
          <div className="mx-auto max-w-[1440px] px-4 py-5 lg:px-6">{children}</div>
        </main>
      </div>
    </div>
  );
}
