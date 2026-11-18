import { Activity, Bell, Clock3, Database, LocateFixed, MapPinned, OctagonAlert, ShieldAlert, AlertTriangle, WifiOff, ArrowRight } from "lucide-react";
import { useMemo, useState } from "react";
import { useApi } from "../api";
import { SignalChart } from "../components/SignalChart";
import { Card, Empty, ErrorState, Kpi, Loading, STATUS, Segmented, SelectBox, StatusBadge, Tip, cx, fmtDate, pct } from "../components/ui";
import { useApp } from "../state";

const LOC_COLORS = ["#0e7c74", "#5fb3aa", "#8fb996", "#c7a34b", "#98a2b3"];

function ZoneGrid({ zones, selected, onSelect, districtFilter, trackFilter }: any) {
  const rows = [0, 1];
  return (
    <div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {rows.flatMap((r) => zones.filter((z: any) => z.row === r).sort((a: any, b: any) => a.col - b.col)).map((z: any) => {
          const dimmed = (districtFilter !== "all" && z.district !== districtFilter);
          if (!z.in_scope) {
            return (
              <div key={z.id} className="hatch flex min-h-[132px] flex-col justify-between rounded-lg border border-dashed border-line bg-white p-3 text-muted">
                <div className="flex items-center justify-between"><span className="text-xs font-semibold">{z.id} · {z.name}</span><ShieldAlert className="h-4 w-4" /></div>
                <span className="text-xs">Outside your role's scope</span>
              </div>
            );
          }
          const trackTier = trackFilter === "all" ? null : z.tracks[trackFilter];
          const status = trackTier ? (trackTier.tier !== "normal" ? trackTier.tier : (trackTier.insufficient ? "insufficient_data" : "normal")) : z.status;
          const s = STATUS[status];
          const score = trackTier ? trackTier.score : z.score;
          const isSel = selected === z.id;
          return (
            <button key={z.id} onClick={() => onSelect(z.id)} aria-pressed={isSel}
              className={cx("group relative flex min-h-[132px] flex-col justify-between rounded-lg border p-3 text-left transition-all",
                s.bg, status === "insufficient_data" && "hatch", isSel ? "border-navy-900 ring-2 ring-navy-900/20" : "border-transparent ring-1 ring-inset " + s.ring,
                dimmed && "opacity-40")}>
              <div>
                <div className="flex flex-wrap items-center justify-between gap-1">
                  <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">{z.district === "DN" ? "North" : "South"} · {z.id}</span>
                  <StatusBadge status={status} />
                </div>
                <div className="mt-1 text-sm font-semibold leading-snug text-ink">{z.name}</div>
                {z.track && status !== "normal" && status !== "insufficient_data" && <div className={cx("text-xs font-medium", s.text)}>{z.track === "heat" ? "Heat stress (outdoor work)" : "Air-quality respiratory risk"}</div>}
              </div>
              <div className="mt-2 flex items-end justify-between gap-2 text-xs">
                <div className="min-w-0 text-muted">
                  <div>Composite score {score === null || score === undefined
                    ? <Tip text="A small count (fewer than 5) contributes to this score, so the exact score is withheld to keep that count private."><span className="font-medium text-ink">withheld (small count)</span></Tip>
                    : <span className="font-semibold tabular-nums text-ink">{score.toFixed(1)}</span>}</div>
                  <div className="flex items-start gap-1"><LocateFixed className="mt-0.5 h-3 w-3 shrink-0" aria-hidden /><span>District records zone-located {pct(z.zone_resolved_share)}</span></div>
                </div>
              </div>
            </button>
          );
        })}
      </div>
      <p className="mt-3 flex items-center gap-1.5 text-[11px] text-muted"><MapPinned className="h-3.5 w-3.5" aria-hidden />Synthetic schematic of 6 zones in 2 districts, not real geography. Select a zone to update the signal timeline.</p>
    </div>
  );
}

function AlertFeed({ feed, trackFilter, districtFilter, onOpen }: any) {
  const rows = feed.filter((e: any) => (trackFilter === "all" || e.track === trackFilter) && (districtFilter === "all" || e.zone.district === districtFilter));
  if (!rows.length) return <Empty icon={Bell} title="No alerts for these filters">Alerts appear when a zone reaches Watch or Action Required.</Empty>;
  return (
    <ol className="scroll-thin max-h-[340px] divide-y divide-line overflow-y-auto">
      {rows.map((e: any, i: number) => {
        const st = e.type === "closed" ? "normal" : e.tier;
        const Icon = e.type === "closed" ? Activity : e.tier === "action" ? OctagonAlert : AlertTriangle;
        return (
          <li key={i}>
            <button onClick={() => onOpen(e.incident)} className="flex w-full items-start gap-3 px-4 py-2.5 text-left hover:bg-slate-50">
              <span className={cx("mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-md", STATUS[st].bg)}><Icon className={cx("h-3.5 w-3.5", STATUS[st].text)} aria-hidden /></span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm text-ink"><span className="font-medium">{e.zone.id} {e.zone.name}</span> · {e.message}</span>
                <span className="text-xs text-muted">{fmtDate(e.date, { weekday: "short", day: "numeric", month: "short" })} · {STATUS[st].label}</span>
              </span>
              <ArrowRight className="mt-1 h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden />
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function FeedHealth({ feeds, asOf }: any) {
  return (
    <ul className="divide-y divide-line">
      {feeds.map((f: any) => {
        const tone = f.status === "current" ? "normal" : f.status === "delayed" ? "watch" : "insufficient_data";
        const label = f.status === "current" ? "Current" : f.status === "delayed" ? "Delayed" : "Missing";
        return (
          <li key={f.signal} className="py-2">
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm font-medium text-ink">{f.label}</span>
              <span className={cx("inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset", STATUS[tone].bg, STATUS[tone].text, STATUS[tone].ring)}>
                {f.status === "current" ? <Activity className="h-3 w-3" /> : f.status === "delayed" ? <Clock3 className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}{label}
              </span>
            </div>
            <div className="mt-0.5 text-[11px] text-muted">
              {f.source} · {f.newest_date ? <>newest usable day {fmtDate(f.newest_date)} ({f.lag_days === 0 ? "same day" : `${f.lag_days} d lag`})</> : "no usable data"}
              {(f.zones_delayed > 0 || f.zones_missing > 0) && <> · <span className="font-medium text-watch">{f.zones_delayed + f.zones_missing}/{f.zones_total} zones affected</span></>}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export default function Overview() {
  const { runQuery, meta, navigate, selectedZone, setSelectedZone, asOf, scenario } = useApp();
  const scen = meta?.scenarios.find((s: any) => s.id === scenario);
  const { data, error, loading, reload } = useApi<any>(`/api/overview?${runQuery}`);
  const [district, setDistrict] = useState("all");
  const [track, setTrack] = useState("all");
  const [windowDays, setWindowDays] = useState("42");
  const visibleSignals = useMemo(() => (meta?.signals || []).filter((s: any) => s.zone_visible), [meta]);
  const [signal, setSignal] = useState<string>("heat_index");
  const sig = visibleSignals.find((s: any) => s.id === signal) ? signal : visibleSignals[0]?.id;

  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return <Loading label="Loading situation overview…" />;

  const inScope = data.zones.filter((z: any) => z.in_scope);
  const zone = inScope.find((z: any) => z.id === selectedZone) || inScope.find((z: any) => z.status === "action") || inScope.find((z: any) => z.status === "watch") || inScope[0];
  const k = data.kpis;
  return (
    <div className={cx("space-y-4 fade-in", loading && "opacity-80")}>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">Situation overview</h1>
          <p className="text-sm text-muted">Status as of {fmtDate(data.as_of, { weekday: "long", day: "numeric", month: "long", year: "numeric" })}, using only data available on that day.</p>
          <p className="mt-1 text-sm text-ink"><span className="font-medium">Scenario:</span> {scen?.label} <span className="text-muted">(synthetic). {scen?.description}</span></p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <SelectBox label="District" value={district} onChange={setDistrict}
            options={[{ value: "all", label: "All districts" }, ...meta.districts.map((d: any) => ({ value: d.id, label: d.name }))]} />
          <SelectBox label="Risk track" value={track} onChange={setTrack}
            options={[{ value: "all", label: "All risk tracks" }, ...Object.values(meta.tracks).map((t: any) => ({ value: t.id, label: t.label }))]} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        <Kpi label="Action required" value={k.action_incidents} sub="Active incidents at Action tier" tone="action" icon={OctagonAlert} />
        <Kpi label="Watch" value={k.watch_incidents} sub={`${k.monitoring_incidents} more cooling down`} tone="watch" icon={AlertTriangle} />
        <Kpi label="Insufficient data" value={`${k.zones_insufficient} / ${k.zones_in_scope}`} sub="Zones that cannot be called normal" tone="insufficient_data" icon={WifiOff} />
        <Kpi label="Alerts, last 14 days" value={k.alerts_14d} sub={<>from {k.zone_days_above_watch_14d} zone-days above Watch <Tip text="One incident per zone episode. Daily re-evaluations update the incident instead of raising a new alert (deduplication and cooldown)." /></>} icon={Bell} />
        <Kpi label="Records located to a zone" value={pct(k.zone_resolved_share)} sub="Clinic records via GPS or site ID; others kept at district or region level" icon={LocateFixed} />
      </div>

      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-12">
        <Card className="xl:col-span-7" title="Zone status" icon={MapPinned} subtitle="Highest tier across risk tracks. Colours are always paired with a text label."
          actions={<span className="hidden text-[11px] text-muted sm:inline">{Object.entries(data.counts).filter(([, v]) => (v as number) > 0).map(([s, v]) => `${v} ${STATUS[s].label.toLowerCase()}`).join(" · ")}</span>}>
          <ZoneGrid zones={data.zones} selected={zone?.id} onSelect={setSelectedZone} districtFilter={district} trackFilter={track} />
        </Card>
        <Card className="xl:col-span-5" title="Alert feed" icon={Bell} subtitle="Incident changes only: raised, escalated, re-opened, closed" bodyClass="p-0">
          <AlertFeed feed={data.alert_feed} trackFilter={track} districtFilter={district} onOpen={(id: string) => navigate("incidents", id)} />
        </Card>
      </div>

      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-12">
        <Card className="xl:col-span-8" title={zone ? `Signal timeline · ${zone.id} ${zone.name}` : "Signal timeline"} icon={Activity}
          subtitle="Observed versus expected. Shaded spans show the zone's alert tier on each replay day."
          actions={<>
            <SelectBox label="Signal" value={sig} onChange={setSignal} options={visibleSignals.map((s: any) => ({ value: s.id, label: s.short }))} />
            <div className="hidden flex-col gap-0.5 sm:flex"><span className="text-[11px] font-medium text-muted">Time range</span>
              <Segmented label="Time range" value={windowDays} onChange={setWindowDays} options={[{ value: "14", label: "14 d" }, { value: "28", label: "28 d" }, { value: "42", label: "42 d" }]} /></div>
          </>}>
          {zone ? <SignalChart path={`/api/zones/${zone.id}/timeseries?${runQuery}&signal=${sig}&window=${windowDays}`} height={230} /> : <Empty title="No zone in scope" />}
          {["sick_leave", "fatigue_reports"].includes(sig as string) && (
            <p className="mt-3 flex items-start gap-1.5 rounded-md bg-slate-50 px-3 py-2 text-xs text-muted"><ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              Workplace counts are released as complete Monday-Sunday weeks only. The alert tiers on this page still use the underlying daily data.</p>
          )}
          {visibleSignals.length < (meta?.signals.length || 0) && (
            <p className="mt-3 flex items-start gap-1.5 rounded-md bg-slate-50 px-3 py-2 text-xs text-muted"><ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              Zone-level clinical and workplace counts are not available to your role. Alerts still show each signal's contribution share.</p>
          )}
          {zone?.active_incidents?.length > 0 && (
            <button onClick={() => navigate("incidents", zone.active_incidents[0])} className="mt-3 inline-flex items-center gap-1.5 rounded-md bg-navy-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-navy-800">
              Open incident details <ArrowRight className="h-3.5 w-3.5" />
            </button>
          )}
        </Card>
        <Card className="xl:col-span-4" title="Data feeds" icon={Database} subtitle="Freshness on the replay date across zones in your scope">
          <FeedHealth feeds={data.feeds} asOf={asOf} />
          <div className="mt-3 border-t border-line pt-3">
            <div className="mb-1.5 text-xs font-semibold text-ink">Location quality of clinic records</div>
            <div className="flex h-2.5 overflow-hidden rounded-full bg-slate-100" role="img" aria-label="Share of records by location level">
              {data.location_quality.levels.map((l: any, i: number) => (
                <div key={l.id} style={{ width: `${(l.share || 0) * 100}%`, background: ["#0e7c74", "#5fb3aa", "#c7a34b", "#98a2b3"][i] }} />
              ))}
            </div>
            <ul className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-muted">
              {data.location_quality.levels.map((l: any, i: number) => (
                <li key={l.id} className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm" style={{ background: LOC_COLORS[i] }} />{l.label}: {pct(l.share)}</li>
              ))}
            </ul>
            <p className="mt-2 text-[11px] text-muted">No record is dropped when GPS is missing. It falls back to a site ID, then to a postal code that the documented crosswalk maps to exactly one zone, then to its district. Boundary, unknown or missing codes are never guessed: they stay at district level or are labelled unlocated.</p>
            <details className="mt-1 text-[11px] text-muted">
              <summary className="cursor-pointer font-medium text-ink">Postal-code crosswalk used (synthetic)</summary>
              <ul className="mt-1 grid grid-cols-2 gap-x-3">{meta.postal_crosswalk.map((c: any) => <li key={c.code}><span className="font-mono">{c.code}</span> → {c.zone ? `zone ${c.zone}` : `district ${c.district} only (${c.note})`}</li>)}</ul>
              <p className="mt-1">Any other code is treated as unmapped.</p>
            </details>
          </div>
        </Card>
      </div>
    </div>
  );
}
