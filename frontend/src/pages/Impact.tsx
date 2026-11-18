import { Calculator, FileBarChart, HeartPulse, Landmark, Scale, TriangleAlert, Users } from "lucide-react";
import { useState } from "react";
import { useApi } from "../api";
import { Card, Empty, ErrorState, Loading, Tip, fmtDate, pct } from "../components/ui";
import { useApp } from "../state";

function NumField({ label, value, onChange, step = 0.05, min = 0, max, help, suffix }: any) {
  return (
    <label className="block">
      <span className="flex items-center gap-1 text-xs font-medium text-ink">{label}{help && <Tip text={help} />}</span>
      <div className="mt-1 flex items-center gap-2">
        <input type="number" value={value} step={step} min={min} max={max} onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
          className="h-8 w-28 rounded-md border border-line px-2 text-sm tabular-nums" />
        {suffix && <span className="text-xs text-muted">{suffix}</span>}
      </div>
    </label>
  );
}

const BENEFITS = [
  { icon: HeartPulse, title: "Fewer severe heat-illness and respiratory emergencies", how: "Targeted controls start while exposure is still rising, before the clinical peak.", measured: "Lead time and share of excess visits still ahead at alert time are measured on synthetic data. Avoided cases are not measured." },
  { icon: Users, title: "Less sickness absence and fewer lost workdays", how: "Earlier site-level adjustments (work hours, water–rest–shade) for the affected zones only.", measured: "Excess sick-leave days during events are measured in the replay. The share avoided is an assumption; avoided absence is an estimate, never a measurement." },
  { icon: Scale, title: "Proportionate, less disruptive response", how: "Zone-level alerts with corroboration avoid district-wide shutdowns and alert fatigue.", measured: "False Action alerts per zone-month and alert de-duplication are measured." },
  { icon: Landmark, title: "Better evidence for regulators and planners", how: "Recorded alerts, decisions and the signals behind them support threshold reviews.", measured: "Recorded responses are stored. Policy impact is not measured in this prototype." },
];

export default function Impact() {
  const { scenario, meta } = useApp();
  const { data, error, reload } = useApi<any>(`/api/impact?scenario=${scenario}`);
  const [eff, setEff] = useState<number | "">(0.25);
  const [visitsPerPerson, setVisitsPerPerson] = useState<number | "">(1.2);
  const [cost, setCost] = useState<number | "">("");
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return <Loading label="Loading impact inputs…" />;
  const m = data.measured;
  const e = Number(eff || 0), vpp = Math.max(Number(visitsPerPerson || 1), 1);
  // Clinic visits and sick-leave days are different units and are estimated separately.
  const visitsAvoided = (share: number | null) => (m.excess_clinic_visits || 0) * (share || 0) * e;
  const absenceAvoided = (share: number | null) => (m.excess_sick_leave_days || 0) * (share || 0) * e;
  const rows = [
    { l: "SentinelWorks Action alert", v: visitsAvoided(m.share_of_excess_after_sentinel_alert), a: absenceAvoided(m.share_of_excess_absence_after_sentinel_alert) },
    { l: "Single-signal comparator", v: visitsAvoided(m.share_of_excess_after_comparator_alert), a: absenceAvoided(m.share_of_excess_absence_after_comparator_alert) },
  ];
  rows.push({ l: "Difference (value of the earlier alert)", v: rows[0].v - rows[1].v, a: rows[0].a - rows[1].a });
  const sc = meta.scenarios.find((s: any) => s.id === scenario);
  return (
    <div className="space-y-4 fade-in">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Health and resilience impact</h1>
        <p className="text-sm text-muted">How earlier, targeted alerts could translate into outcomes. Scenario: {sc?.label} (synthetic).</p>
      </div>
      <div role="note" className="flex items-start gap-3 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950">
        <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0" />
        <div><span className="font-semibold">Estimates, not proven outcomes.</span> Measured inputs come from a separate synthetic population and held-out validation. The effect of any action is an assumption you set below; no avoided case, visit or absence has been observed, and nothing here is a measured saving.</div>
      </div>

      {!m.excess_clinic_visits ? (
        <Card title="Benefit estimator" icon={Calculator}><Empty title="No health event in this scenario">The edge-case scenario contains exposure without a health response, so there are no excess cases to estimate. Choose the heatwave or air-quality scenario.</Empty></Card>
      ) : (
        <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-12">
          <Card className="xl:col-span-5" title="Measured inputs (synthetic replay)" icon={FileBarChart} subtitle="Separate simulated population (seed 901) and held-out validation">
            <dl className="space-y-2 text-sm">
              {[
                ["Excess clinic visits during the event(s)", `${m.excess_clinic_visits} visits`, "Observed minus past-only expected clinic visits, summed over event days in affected zones. A visit is not a unique person or case: one person can visit more than once, and clinic visitors are not only workers."],
                ["Excess sick-leave days during the event(s)", `${m.excess_sick_leave_days} days`, "Same method on the workplace HR feed (sites in the affected zones). This is the only workday measure in the data."],
                ["Outdoor workers at sites in affected zones", `${m.outdoor_workforce_in_event_zones} workers`, "From the synthetic site register."],
                ["Share of excess visits after the SentinelWorks Action alert", pct(m.share_of_excess_after_sentinel_alert), "Counted from the day after the alert, allowing a day for action to start."],
                ["Share of excess sick-leave days after that alert", pct(m.share_of_excess_absence_after_sentinel_alert), "Same timing rule applied to the sick-leave excess."],
                ["Same two shares for the single-signal comparator", `${pct(m.share_of_excess_after_comparator_alert)} · ${pct(m.share_of_excess_absence_after_comparator_alert)}`, "Comparator's first alert (0% if it never alerted)."],
                ["Median lead time vs comparator (validation)", m.lead_time_vs_comparator_days ? `${m.lead_time_vs_comparator_days.median} days (n = ${m.lead_time_vs_comparator_days.n})` : "—", "Held-out evaluation seeds."],
              ].map(([k, v, h]) => (
                <div key={k} className="flex items-start justify-between gap-4 border-b border-dashed border-line pb-2">
                  <dt className="flex items-center gap-1 text-muted">{k}<Tip text={h} /></dt><dd className="shrink-0 text-right font-semibold tabular-nums text-ink">{v}</dd>
                </div>
              ))}
            </dl>
            <ul className="mt-3 space-y-1 text-[11px] text-muted">
              {m.per_event.map((p: any) => <li key={p.zone + p.track}>{p.zone} ({p.track}): SentinelWorks Action {p.sentinel_action ? fmtDate(p.sentinel_action) : "none"}; comparator {p.comparator ? fmtDate(p.comparator) : "never alerted"}; {p.excess_visits} excess visits.</li>)}
            </ul>
          </Card>
          <Card className="xl:col-span-7" title="Benefit estimator (estimates only)" icon={Calculator} subtitle="Change the assumptions to see how sensitive the estimate is">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <NumField label="Action effectiveness" value={eff} onChange={setEff} max={1} suffix="share of post-alert excess prevented"
                help="Assumption. Share of the excess that occurs after the alert that targeted actions would prevent. No validated value exists in this prototype." />
              <NumField label="Clinic visits per person" value={visitsPerPerson} onChange={setVisitsPerPerson} step={0.1} min={1} suffix="visits"
                help="Assumption used only to convert visits to people. Visits are not unique cases." />
              <NumField label="Cost per sick-leave day" value={cost} onChange={setCost} step={10} suffix="(your currency)"
                help="Optional. Applied to estimated avoided sick-leave days only. Leave blank to skip." />
            </div>
            <div className="mt-5 overflow-x-auto">
              <table className="w-full min-w-[520px] text-sm">
                <thead><tr className="text-left text-xs text-muted"><th className="pb-1 font-medium">Alert rule</th><th className="pb-1 text-right font-medium">Est. avoided clinic visits</th><th className="pb-1 text-right font-medium">≈ fewer people visiting</th><th className="pb-1 text-right font-medium">Est. avoided sick-leave days</th>{cost !== "" && <th className="pb-1 text-right font-medium">Est. value</th>}</tr></thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={r.l} className={i === 2 ? "border-t border-line bg-teal-50 font-semibold" : "border-t border-line"}>
                      <td className="py-2 pr-2">{r.l}</td>
                      <td className="text-right tabular-nums">{r.v.toFixed(0)}</td>
                      <td className="text-right tabular-nums">{(r.v / vpp).toFixed(0)}</td>
                      <td className="text-right tabular-nums">{r.a.toFixed(0)}</td>
                      {cost !== "" && <td className="text-right tabular-nums">{(r.a * Number(cost)).toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-4 rounded-md bg-slate-50 px-3 py-2 font-mono text-[11px] leading-relaxed text-slate-700">
              avoided visits = excess visits ({m.excess_clinic_visits}) × share after alert × effectiveness ({e});<br />
              avoided sick-leave days = excess sick-leave days ({m.excess_sick_leave_days}) × share after alert × effectiveness;<br />
              people ≈ avoided visits ÷ visits per person ({vpp}). Lost workdays are not derived from clinic visits.
            </p>
            <p className="mt-2 text-[11px] text-muted">These are modelled estimates on synthetic data, not measured avoided absence or avoided hospital admissions. The difference row shows the value of the earlier alert under these assumptions only; real benefit would need a pilot with measured outcomes.</p>
          </Card>
        </div>
      )}

      <Card title="Expected benefit mechanisms" icon={HeartPulse} bodyClass="p-0">
        <ul className="grid grid-cols-1 divide-y divide-line md:grid-cols-2 md:divide-y-0">
          {BENEFITS.map((b) => (
            <li key={b.title} className="flex gap-3 border-line p-4 md:border-b md:odd:border-r">
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-teal-50"><b.icon className="h-4 w-4 text-teal-brand" /></span>
              <div>
                <div className="text-sm font-semibold text-ink">{b.title}</div>
                <p className="mt-0.5 text-xs text-muted">{b.how}</p>
                <p className="mt-1 text-[11px] text-slate-500"><span className="font-medium text-slate-700">What is measured:</span> {b.measured}</p>
              </div>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
