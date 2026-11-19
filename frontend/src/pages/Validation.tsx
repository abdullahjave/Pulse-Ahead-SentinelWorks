import { FlaskConical, Gauge, History, Info, LocateFixed, RefreshCw, Target, Timer } from "lucide-react";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { api, useApi } from "../api";
import { Card, ErrorState, Loading, cx, fmtDate, pct } from "../components/ui";
import { useApp } from "../state";

const f2 = (v: number | null | undefined, d = 2) => (v === null || v === undefined ? "—" : v.toFixed(d));
const days = (s: any) => (s ? `${s.median > 0 ? "" : ""}${s.median} d` : "—");

function Metric({ label, value, sub, target }: any) {
  return (
    <div className="rounded-xl border border-line bg-white px-4 py-3">
      <div className="text-xs font-medium text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums text-ink">{value}</div>
      <div className="mt-0.5 text-xs text-muted">{sub}</div>
      {target && <div className="mt-2 inline-flex items-center gap-1 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600"><Target className="h-3 w-3" />Design target {target} (not a result)</div>}
    </div>
  );
}

function hist(values: number[]) {
  const m: Record<number, number> = {};
  values.forEach((v) => (m[v] = (m[v] || 0) + 1));
  return m;
}

export default function Validation() {
  const { data, error, reload, loading } = useApi<any>("/api/validation");
  const { bumpData, meta } = useApp();
  START = meta.dates.start;
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const rerun = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await api("/api/validation/rerun", { method: "POST" });
      setMsg(`Re-computed in ${r.runtime_seconds} s.`);
      reload(); bumpData();
    } catch (e: any) { setMsg(e.message); } finally { setBusy(false); }
  };
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return <Loading label="Loading validation results…" />;
  const S = data.holdout.conditions.standard;
  const D = data.holdout.conditions.degraded;
  const hs = hist(S.lead_vs_comparator_days?.values || []);
  const hd = hist(D.lead_vs_comparator_days?.values || []);
  const keys = Array.from(new Set([...Object.keys(hs), ...Object.keys(hd)].map(Number))).sort((a, b) => a - b);
  const leadData = keys.map((k) => ({ lead: `${k} d`, Standard: hs[k] || 0, Degraded: hd[k] || 0 }));
  const rows: [string, (c: any) => string, (c: any) => string][] = [
    ["Sensitivity (Action tier)", (c) => pct(c.sensitivity_action), (c) => pct(c.comparator.sensitivity)],
    ["Sensitivity (Watch or higher)", (c) => pct(c.sensitivity_watch), () => "n/a"],
    ["Positive predictive value (Action)", (c) => pct(c.ppv_action), (c) => pct(c.comparator.ppv)],
    ["False Action alerts per zone-month", (c) => f2(c.false_action_per_zone_month, 3), (c) => f2(c.comparator.false_alarms_per_zone_month, 3)],
    ["Watch-only incidents not linked to an event, per zone-month", (c) => f2(c.watch_only_unlinked_per_zone_month), () => "n/a"],
    ["Median lead vs single-signal comparator", (c) => days(c.lead_vs_comparator_days), () => "reference"],
    ["Median lead vs weekly report", (c) => days(c.lead_vs_weekly_report_days), () => "n/a"],
    ["Median Action day relative to health-signal onset", (c) => (c.action_day_relative_to_health_onset ? `+${c.action_day_relative_to_health_onset.median} d` : "—"), () => "n/a"],
    ["Events detected that the comparator missed", (c) => String(c.events_detected_but_missed_by_comparator), () => "n/a"],
  ];
  const trade = data.holdout_tradeoff.grid;
  const cmpPt = data.holdout_tradeoff.comparator;
  const sel = trade.find((g: any) => g.params.action_score === data.params.action_score && g.params.corroboration_z === data.params.corroboration_z);
  const lat = data.holdout.latency;
  const loc = data.holdout.location;
  return (
    <div className={cx("space-y-4 fade-in", loading && "opacity-80")}>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">Operational validation</h1>
          <p className="max-w-3xl text-sm text-muted">Measured by running the full pipeline on {data.evaluation_seeds.length} held-out synthetic worlds (seeds {data.evaluation_seeds[0]}–{data.evaluation_seeds[data.evaluation_seeds.length - 1]}), {S.events} injected health events. Thresholds were chosen on separate tuning seeds {data.tuning.seeds[0]}–{data.tuning.seeds[data.tuning.seeds.length - 1]}. Computed {new Date(data.generated_at).toLocaleString("en-GB")}.</p>
        </div>
        <div className="flex items-center gap-3">
          {msg && <span role="status" className="text-xs text-muted">{msg}</span>}
{data.rerun?.available === false ? (
            <span role="note" className="max-w-sm rounded-md border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs text-amber-900"><span className="font-semibold">Precomputed report.</span> Re-run is disabled on this hosted demo. {data.rerun.reason}</span>
          ) : (
                    <button onClick={rerun} disabled={busy} className="inline-flex items-center gap-2 rounded-md border border-line bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-50 disabled:opacity-50">
            <RefreshCw className={cx("h-4 w-4", busy && "animate-spin")} />{busy ? `Re-running back-test (last run took ${Math.round(data.runtime_seconds)} s)…` : "Re-run back-test"}
          </button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Metric label="Sensitivity (Action tier)" value={pct(S.sensitivity_action)} sub={`Degraded inputs: ${pct(D.sensitivity_action)}`} target={data.targets.sensitivity} />
        <Metric label="Positive predictive value" value={pct(S.ppv_action)} sub={`${S.action_true} of ${S.action_alerts} Action alerts · degraded ${pct(D.ppv_action)}`} target={data.targets.ppv} />
        <Metric label="False Action alerts" value={f2(S.false_action_per_zone_month, 3)} sub={`per zone-month (${S.false_action_alerts} in ${S.runs} runs) · degraded ${f2(D.false_action_per_zone_month, 3)}`} />
        <Metric label="Median lead time" value={days(S.lead_vs_comparator_days)} sub={`earlier than single-signal clinic surveillance. Interquartile range ${S.lead_vs_comparator_days?.p25}–${S.lead_vs_comparator_days?.p75} d; measured on ${S.lead_vs_comparator_days?.n} of ${S.events_detected_action} detected events (the comparator never flagged the other ${S.events_detected_but_missed_by_comparator}). Degraded ${days(D.lead_vs_comparator_days)}`} target={data.targets.lead_time} />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
        <Card className="xl:col-span-7" title="Measured results: standard vs degraded inputs" icon={FlaskConical} subtitle={`Degraded = ${Math.round(data.degraded_definition.gps_removed_share * 100)}% of GPS tags removed and the clinic feed delayed by ${data.degraded_definition.clinic_extra_delay_days} extra days`} bodyClass="p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="border-b border-line bg-slate-50 text-left text-xs text-muted">
                <th className="px-4 py-2 font-medium">Metric</th><th className="px-3 py-2 text-right font-medium">Standard</th><th className="px-3 py-2 text-right font-medium">Degraded</th><th className="px-4 py-2 text-right font-medium">Comparator (standard)</th>
              </tr></thead>
              <tbody>
                {rows.map(([l, f, c]) => (
                  <tr key={l} className="border-b border-line">
                    <td className="px-4 py-2 text-ink">{l}</td><td className="px-3 py-2 text-right font-medium tabular-nums">{f(S)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{f(D)}</td><td className="px-4 py-2 text-right tabular-nums text-muted">{c(S)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="space-y-1 px-4 py-3 text-[11px] text-muted">
            <p><span className="font-medium text-ink">Comparator:</span> {S.comparator.definition}</p>
            <p><span className="font-medium text-ink">Matching rule:</span> {data.matching_rule}</p>
          </div>
        </Card>
        <Card className="xl:col-span-5" title="Lead-time distribution" icon={Timer} subtitle="Days by which the first Action alert preceded the comparator, per detected event">
          <div className="h-56" role="img" aria-label="Lead time histogram">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={leadData} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
                <CartesianGrid stroke="#eef1f5" vertical={false} />
                <XAxis dataKey="lead" tickLine={false} axisLine={{ stroke: "#d6dce4" }} />
                <YAxis allowDecimals={false} tickLine={false} axisLine={false} label={{ value: "events", angle: -90, position: "insideLeft", offset: 24, style: { fontSize: 11, fill: "#5b6b7d" } }} />
                <Tooltip cursor={{ fill: "#f4f6f9" }} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="Standard" fill="#0b1f33" radius={[3, 3, 0, 0]} />
                <Bar dataKey="Degraded" fill="#c7a34b" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="text-[11px] text-muted">Negative values would mean the comparator was earlier. Events the comparator never flagged are counted separately above.</p>
        </Card>
      </div>

      <Card title="Sensitivity, PPV and false alerts: the trade-off" icon={Target}
        subtitle={`All ${data.holdout_tradeoff.grid.length} threshold settings evaluated on the ${data.evaluation_seeds.length} held-out worlds. Display only: the setting was chosen on the tuning seeds, not from this chart.`}>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <div>
          <div className="h-60" role="img" aria-label="Scatter of sensitivity and PPV against false Action alerts per zone-month for each threshold setting">
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 6, right: 12, left: 14, bottom: 22 }}>
                <CartesianGrid stroke="#eef1f5" />
                <XAxis type="number" dataKey="x" name="False Action alerts per zone-month" domain={[0, "auto"]} tickLine={false} axisLine={{ stroke: "#d6dce4" }}
                  label={{ value: "False Action alerts per zone-month (lower is better)", position: "insideBottom", offset: -10, style: { fontSize: 11, fill: "#5b6b7d" } }} />
                <YAxis type="number" dataKey="y" name="Share" domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} tickLine={false} axisLine={false}
                  label={{ value: "Share of events / alerts", angle: -90, position: "insideLeft", offset: 0, style: { fontSize: 11, fill: "#5b6b7d", textAnchor: "middle" } }} />
                <ZAxis range={[60, 60]} />
                <Tooltip cursor={{ strokeDasharray: "3 3" }} formatter={(v: any, n: any) => (n === "Share" ? `${(v * 100).toFixed(0)}%` : Number(v).toFixed(3))} />
                <Scatter name="Sensitivity (each setting)" data={trade.map((g: any) => ({ x: g.false_per_zone_month, y: g.sensitivity }))} fill="#0b1f33" />
                <Scatter name="PPV (each setting)" data={trade.map((g: any) => ({ x: g.false_per_zone_month, y: g.ppv }))} fill="#c7a34b" shape="triangle" />
                <Scatter name="Comparator: sensitivity" data={[{ x: cmpPt.false_alarms_per_zone_month, y: cmpPt.sensitivity }]} fill="#ffffff" stroke="#0b1f33" strokeWidth={2} />
                <Scatter name="Comparator: PPV" data={[{ x: cmpPt.false_alarms_per_zone_month, y: cmpPt.ppv }]} fill="#ffffff" stroke="#c7a34b" strokeWidth={2} shape="triangle" />
              </ScatterChart>
            </ResponsiveContainer>
          </div>
          <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted">
            <li className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-navy-900" />Sensitivity, each setting</li>
            <li className="flex items-center gap-1.5"><span className="h-0 w-0 border-x-[5px] border-b-[9px] border-x-transparent border-b-[#c7a34b]" />PPV, each setting</li>
            <li className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full border-2 border-navy-900 bg-white" />Comparator sensitivity</li>
            <li className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rotate-45 border-2 border-[#c7a34b] bg-white" />Comparator PPV</li>
          </ul>
          </div>
          <div className="space-y-2 text-xs text-muted">
            <p><span className="font-medium text-ink">What the chart shows.</span> Lower Action thresholds raise sensitivity but add false alerts and lower PPV. The selected setting ({data.params.action_score} / {data.params.corroboration_z}) gives sensitivity {pct(sel?.sensitivity)}, PPV {pct(sel?.ppv)} and {f2(sel?.false_per_zone_month, 3)} false alerts per zone-month.</p>
            <p><span className="font-medium text-ink">Against the single-signal comparator.</span> SentinelWorks detects more events ({pct(S.sensitivity_action)} vs {pct(cmpPt.sensitivity)}) and earlier, but the comparator was more precise: PPV {pct(cmpPt.ppv)} and {f2(cmpPt.false_alarms_per_zone_month, 3)} false alerts per zone-month. SentinelWorks buys earlier and broader detection with more false Action alerts.</p>
            {data.params.action_score === Math.max(...trade.map((g: any) => g.params.action_score)) && (
              <p><span className="font-medium text-ink">Grid edge.</span> The selected Action score ({data.params.action_score}) is the highest value that was tried, so a better setting may lie beyond the grid. It was not explored.</p>
            )}
            <p><span className="font-medium text-ink">Reading the lead time.</span> The lead time depends strongly on the timing that we injected into the synthetic worlds and on how the comparator is defined (it needs two complete reporting days, and clinic data arrive with a lag). The spread is narrow (interquartile range {S.lead_vs_comparator_days?.p25}–{S.lead_vs_comparator_days?.p75} d). It is not evidence of the same lead time against real-world reporting.{data.stress && " The stress test below shows how it moves when the exposure-to-health lag changes."}</p>
            <p><span className="font-medium text-ink">Degraded inputs.</span> Sensitivity falls from {pct(S.sensitivity_action)} to {pct(D.sensitivity_action)} when GPS tags and clinic timeliness are degraded.</p>
          </div>
        </div>
        <details className="mt-3 text-xs">
          <summary className="cursor-pointer font-medium text-ink">Data behind the chart (held-out evaluation)</summary>
          <table className="mt-2 w-full max-w-2xl text-xs">
            <thead><tr className="text-left text-muted"><th className="py-1">Action score</th><th>Corroboration z</th><th className="text-right">Sensitivity</th><th className="text-right">PPV</th><th className="text-right">Action alerts</th><th className="text-right">False</th><th className="text-right">False / zone-month</th></tr></thead>
            <tbody>{trade.map((g: any, i: number) => (
              <tr key={i} className={cx("border-t border-line", g === sel && "bg-teal-50 font-semibold")}><td className="py-1">{g.params.action_score}</td><td>{g.params.corroboration_z}</td><td className="text-right">{pct(g.sensitivity)}</td><td className="text-right">{pct(g.ppv)}</td><td className="text-right">{g.action_alerts}</td><td className="text-right">{g.false_action_alerts}</td><td className="text-right">{f2(g.false_per_zone_month, 3)}</td></tr>
            ))}</tbody>
          </table>
        </details>
      </Card>

      {data.stress && (() => {
        const R = data.stress.rows;
        const weak = R.filter((r: any) => (r.sensitivity_action ?? 0) < 0.85);
        const leads = R.map((r: any) => r.lead_vs_comparator_median).filter((x: any) => x !== null && x !== undefined);
        const ppvLow = R.filter((r: any) => (r.ppv_action ?? 1) < 0.6);
        return (
          <Card title="Stress test: varied environment-to-health lag" icon={Timer}
            subtitle={`Fixed thresholds (Action score ${data.stress.params.action_score}, corroboration z ${data.stress.params.corroboration_z}), ${data.stress.seeds.length} held-out worlds per row (seeds ${data.stress.seeds[0]}–${data.stress.seeds[data.stress.seeds.length - 1]}). Nothing was re-tuned.`}>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-sm">
                <thead><tr className="border-b border-line bg-slate-50 text-left text-xs text-muted">
                  <th className="px-3 py-2 font-medium">Condition</th><th className="px-2 py-2 text-right font-medium">Events</th><th className="px-2 py-2 text-right font-medium">Sensitivity</th><th className="px-2 py-2 text-right font-medium">PPV</th>
                  <th className="px-2 py-2 text-right font-medium">False Action / zone-month</th><th className="px-2 py-2 text-right font-medium">Comparator sensitivity</th><th className="px-2 py-2 text-right font-medium">Median lead vs comparator</th><th className="px-2 py-2 text-right font-medium">Action day vs health onset</th><th className="px-3 py-2 text-right font-medium">Missed</th>
                </tr></thead>
                <tbody>{R.map((r: any) => (
                  <tr key={r.label} className="border-b border-line">
                    <td className="px-3 py-2 text-ink">{r.label}</td><td className="px-2 py-2 text-right tabular-nums">{r.events}</td>
                    <td className={cx("px-2 py-2 text-right tabular-nums", (r.sensitivity_action ?? 0) < 0.85 && "font-semibold text-action")}>{pct(r.sensitivity_action)}</td>
                    <td className={cx("px-2 py-2 text-right tabular-nums", (r.ppv_action ?? 1) < 0.6 && "font-semibold text-action")}>{pct(r.ppv_action)}</td>
                    <td className="px-2 py-2 text-right tabular-nums">{f2(r.false_action_per_zone_month, 3)}</td><td className="px-2 py-2 text-right tabular-nums text-muted">{pct(r.comparator_sensitivity)}</td>
                    <td className="px-2 py-2 text-right tabular-nums">{r.lead_vs_comparator_median === null || r.lead_vs_comparator_median === undefined ? "—" : `${r.lead_vs_comparator_median} d (n = ${r.lead_vs_comparator_n})`}</td>
                    <td className="px-2 py-2 text-right tabular-nums">{r.action_day_vs_health_onset_median === null || r.action_day_vs_health_onset_median === undefined ? "—" : `${r.action_day_vs_health_onset_median > 0 ? "+" : ""}${r.action_day_vs_health_onset_median} d`}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{r.events_missed}</td>
                  </tr>))}</tbody>
              </table>
            </div>
            <div className="mt-3 space-y-1.5 text-xs text-muted">
              <p><span className="font-medium text-ink">What this shows.</span> {weak.length === 0 ? "Action sensitivity stayed at or above the 85% design target in every row." : `Action sensitivity fell below the 85% design target in ${weak.length} of ${R.length} rows (${weak.map((r: any) => r.label).join("; ")}). These are failures of the fixed thresholds, not noise to be tuned away.`}{ppvLow.length > 0 && ` PPV fell below 60% in: ${ppvLow.map((r: any) => r.label).join("; ")}.`}</p>
              <p><span className="font-medium text-ink">Lead time depends on the injected timing.</span> {leads.length && Math.min(...leads) === Math.max(...leads) ? `The median lead over the comparator is ${leads[0]} days in every row, even as detection itself degrades.` : `The median lead over the comparator ranges from ${leads.length ? Math.min(...leads) : "—"} to ${leads.length ? Math.max(...leads) : "—"} days across these rows.`} That constancy comes from how the comparator is defined (two complete reporting days), so the headline lead time above is a property of the scenarios and the comparator we built, not a general result.</p>
              <p>{data.stress.definition}</p>
            </div>
          </Card>
        );
      })()}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
        <Card className="xl:col-span-4" title="Results by risk track" icon={Gauge} bodyClass="p-0">
          <table className="w-full text-sm">
            <thead><tr className="border-b border-line bg-slate-50 text-left text-xs text-muted"><th className="px-4 py-2 font-medium">Track</th><th className="px-2 py-2 text-right font-medium">Events</th><th className="px-2 py-2 text-right font-medium">Sens.</th><th className="px-4 py-2 text-right font-medium">Degraded</th></tr></thead>
            <tbody>{Object.entries(S.by_track).map(([k, v]: any) => (
              <tr key={k} className="border-b border-line"><td className="px-4 py-2">{k === "heat" ? "Heat stress" : "Respiratory"}</td><td className="px-2 py-2 text-right tabular-nums">{v.events}</td>
                <td className="px-2 py-2 text-right tabular-nums">{pct(v.sensitivity_action)}</td><td className="px-4 py-2 text-right tabular-nums">{pct(D.by_track[k].sensitivity_action)}</td></tr>
            ))}</tbody>
          </table>
        </Card>
        <Card className="xl:col-span-4" title="Data latency by feed" icon={History} subtitle="Days from observation to availability (one evaluation world)" bodyClass="p-0">
          <table className="w-full text-sm">
            <thead><tr className="border-b border-line bg-slate-50 text-left text-xs text-muted"><th className="px-4 py-2 font-medium">Feed</th><th className="px-2 py-2 text-right font-medium">Median / p90</th><th className="px-4 py-2 text-right font-medium">Degraded</th></tr></thead>
            <tbody>{Object.entries(lat.standard).map(([k, v]: any) => (
              <tr key={k} className="border-b border-line"><td className="px-4 py-2 text-xs">{k.replace(/_/g, " ")}</td>
                <td className="px-2 py-2 text-right tabular-nums">{v.median_days} / {v.p90_days} d</td><td className="px-4 py-2 text-right tabular-nums">{lat.degraded[k].median_days} / {lat.degraded[k].p90_days} d</td></tr>
            ))}</tbody>
          </table>
        </Card>
        <Card className="xl:col-span-4" title="Location fallback" icon={LocateFixed} subtitle="Clinic records in one evaluation world" bodyClass="p-0">
          <table className="w-full text-sm">
            <thead><tr className="border-b border-line bg-slate-50 text-left text-xs text-muted"><th className="px-4 py-2 font-medium">Records</th><th className="px-2 py-2 text-right font-medium">Standard</th><th className="px-4 py-2 text-right font-medium">Degraded</th></tr></thead>
            <tbody>
              {[["Received", "records_in"], ["Retained", "records_retained"], ["Dropped", "records_dropped"]].map(([l, k]) => (
                <tr key={k} className="border-b border-line"><td className="px-4 py-2">{l}</td><td className="px-2 py-2 text-right tabular-nums">{loc.standard[k].toLocaleString()}</td><td className="px-4 py-2 text-right tabular-nums">{loc.degraded[k].toLocaleString()}</td></tr>
              ))}
              {Object.keys(loc.standard.by_level).map((k) => (
                <tr key={k} className="border-b border-line text-xs text-muted"><td className="px-4 py-1.5">placed at {k.replace("zone_gps", "zone (GPS)").replace("zone_site", "zone (site ID)").replace("zone_postal", "zone (postal crosswalk)").replace("region", "unlocated (region only)")}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{loc.standard.by_level[k].toLocaleString()}</td><td className="px-4 py-1.5 text-right tabular-nums">{loc.degraded.by_level[k].toLocaleString()}</td></tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>

      <Card title="Scenario replay (seed 42, the dashboard scenarios)" icon={History} subtitle="The same scenarios you can replay from the top bar" bodyClass="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="border-b border-line bg-slate-50 text-left text-xs text-muted">
              <th className="px-4 py-2 font-medium">Scenario</th><th className="px-2 py-2 font-medium">Inputs</th><th className="px-2 py-2 text-right font-medium">True events</th><th className="px-2 py-2 text-right font-medium">Action alerts</th>
              <th className="px-2 py-2 text-right font-medium">False Action</th><th className="px-2 py-2 text-right font-medium">Alert changes</th><th className="px-2 py-2 text-right font-medium">Zone-days ≥ Watch</th><th className="px-4 py-2 font-medium">First Action vs comparator</th>
            </tr></thead>
            <tbody>{data.demo_scenarios.map((r: any) => (
              <tr key={r.scenario + r.degraded} className="border-b border-line">
                <td className="px-4 py-2 text-ink">{r.label}</td><td className="px-2 py-2 text-xs">{r.degraded ? "Degraded" : "Standard"}</td>
                <td className="px-2 py-2 text-right tabular-nums">{r.true_events}</td><td className="px-2 py-2 text-right tabular-nums">{r.action_alerts}</td>
                <td className="px-2 py-2 text-right tabular-nums">{r.false_action_alerts}</td><td className="px-2 py-2 text-right tabular-nums">{r.alert_events}</td><td className="px-2 py-2 text-right tabular-nums">{r.zone_days_at_or_above_watch}</td>
                <td className="px-4 py-2 text-xs">{r.events.length ? r.events.map((e: any) => `${e.zone}: ${e.action_day !== null ? fmtDate(isoDay(e.action_day)) : "not detected"} vs ${e.comparator_day !== null ? fmtDate(isoDay(e.comparator_day)) : "not flagged"}`).join(" · ") : "No true event (edge case)"}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </Card>

      <Card title="Method notes" icon={Info}>
        <ul className="grid grid-cols-1 gap-x-8 gap-y-1.5 text-xs text-muted md:grid-cols-2">
          <li>{data.scenario_mix}</li>
          <li>Every replay day uses only records available on that day (observation and availability times are tracked separately).</li>
          <li>Baselines use the {data.baseline_definition.window_days} days ending {data.baseline_definition.guard_days} days before the evaluated day (past-only, with day-of-week factors for counts).</li>
          <li>Selected thresholds: Action score ≥ {data.params.action_score}, corroboration z ≥ {data.params.corroboration_z}. {data.tuning.criterion}</li>
          <li>Duplicate Action alerts for an event already detected: {S.duplicate_action_alerts}. Each detected event counts as one true positive, so repeats cannot raise PPV.</li>
          <li>All data are synthetic, so these figures show how the method behaves on simulated patterns. They are not evidence of real-world performance.</li>
          <li>Design targets come from the earlier proposal and are shown only for comparison.</li>
        </ul>
        <details className="mt-3 text-xs">
          <summary className="cursor-pointer font-medium text-ink">Tuning grid ({data.tuning.grid.length} settings, tuning seeds only)</summary>
          <table className="mt-2 w-full max-w-xl text-xs">
            <thead><tr className="text-left text-muted"><th className="py-1">Action score</th><th>Corroboration z</th><th className="text-right">Sensitivity</th><th className="text-right">PPV</th><th className="text-right">F1</th><th className="text-right">False / zone-month</th></tr></thead>
            <tbody>{data.tuning.grid.map((g: any, i: number) => {
              const sel = g.params.action_score === data.params.action_score && g.params.corroboration_z === data.params.corroboration_z;
              return <tr key={i} className={cx("border-t border-line", sel && "bg-teal-50 font-semibold")}><td className="py-1">{g.params.action_score}</td><td>{g.params.corroboration_z}</td><td className="text-right">{pct(g.sensitivity)}</td><td className="text-right">{pct(g.ppv)}</td><td className="text-right">{f2(g.f1)}</td><td className="text-right">{f2(g.false_per_zone_month, 3)}</td></tr>;
            })}</tbody>
          </table>
        </details>
      </Card>
    </div>
  );
}

let START = "2026-06-01";
function isoDay(d: number) {
  const base = new Date(START + "T00:00:00Z");
  base.setUTCDate(base.getUTCDate() + d);
  return base.toISOString().slice(0, 10);
}
