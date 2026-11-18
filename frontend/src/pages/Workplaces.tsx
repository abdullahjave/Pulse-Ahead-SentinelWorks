import { Building2, Download, Eye, EyeOff, Lock, ShieldCheck, Table2, Users } from "lucide-react";
import { useState } from "react";
import { downloadCsv, useApi } from "../api";
import { SignalChart } from "../components/SignalChart";
import { Card, CountCell, Empty, ErrorState, Loading, SelectBox, StatusBadge, cx, fmtDate } from "../components/ui";
import { useApp } from "../state";

function RoleScope() {
  const { meta, user } = useApp();
  const p = meta.permissions[user.role];
  const can = [
    ["Zones", p.zone_scope === "all" ? "All 6 zones" : "Zones where your employer has sites"],
    ["Zone-level signals", p.zone_signals.length === 6 ? "All signals" : "Environmental signals only"],
    ["District-level counts", p.district_series ? "Yes" : "No"],
    ["Site aggregates", p.site_scope === "all" ? "All sites" : p.site_scope === "employer" ? "Your employer's sites only" : "No"],
    ["Cohort breakdown", p.cohort_drilldown ? "Yes, weekly, with suppression" : "No"],
    ["Exports", p.exports.length ? p.exports.map((e: string) => (e === "zone_daily" ? "zone signals" : "site weekly table")).join(", ") : "None"],
  ];
  return (
    <Card title="What your role can see" icon={Eye} subtitle={p.label}>
      <p className="mb-3 text-sm text-ink">{p.summary}</p>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 text-xs sm:grid-cols-2">
        {can.map(([k, v]) => <div key={k} className="flex justify-between gap-3 border-b border-dashed border-line py-1"><dt className="text-muted">{k}</dt><dd className="text-right font-medium text-ink">{v}</dd></div>)}
      </dl>
      <div className="mt-4 rounded-lg bg-navy-900 px-4 py-3 text-xs text-slate-200">
        <div className="mb-1 flex items-center gap-1.5 font-semibold text-white"><Lock className="h-3.5 w-3.5" />Enforced by the backend for every role</div>
        <ul className="list-disc space-y-0.5 pl-5">
          <li>No names, employee IDs, patient records or disciplinary fields exist in any API response.</li>
          <li>Counts from 1 to {meta.privacy.small_cell_threshold - 1} are shown as &lt;{meta.privacy.small_cell_threshold} in tables, charts, totals, tooltips and CSV exports.</li>
          <li><span className="font-semibold text-white">Workplace counts (sick leave, fatigue) are released as complete weeks only.</span> No daily workplace count is released anywhere, so a weekly total cannot be combined with daily figures to work out a hidden one.</li>
          <li>When a small cell is hidden, a larger neighbouring cell or the total is withheld too (shown as “masked”).</li>
          <li>Your role comes from a server-issued token. The role switcher is a demo sign-in, not the access control itself.</li>
        </ul>
      </div>
    </Card>
  );
}

function CohortPanel({ site }: { site: any }) {
  const { runQuery } = useApp();
  const { data, error, reload } = useApi<any>(`/api/sites/${site.id}/cohorts?${runQuery}`);
  if (error) return <ErrorState error={error} onRetry={reload} compact />;
  if (!data) return <Loading />;
  if (!data.week) return <p className="text-xs text-muted">No complete week of workplace data is available yet on this replay date.</p>;
  const cols: [string, string][] = [["headcount", "Headcount"], ["sick_leave_week", "Sick leave (last full week)"], ["fatigue_week", "Fatigue reports (last full week)"]];
  const rows = [...data.rows, data.total];
  return (
    <div>
      <p className="mb-2 text-xs text-muted">Latest complete week: {fmtDate(data.week.start)} to {fmtDate(data.week.end)}. The by-week columns show the six weeks ending {fmtDate(data.weeks[0]?.end)} to {fmtDate(data.weeks[data.weeks.length - 1]?.end)}. Weeks are released only after reporting is final.</p>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="border-b border-line text-left text-xs text-muted">
            <th className="py-2 pr-3 font-medium">Cohort</th>{cols.map(([k, l]) => <th key={k} className="py-2 pr-3 text-right font-medium">{l}</th>)}
            <th className="py-2 pl-2 text-right font-medium">Sick leave by week, oldest to newest</th>
          </tr></thead>
          <tbody>
            {rows.map((r: any) => (
              <tr key={r.cohort} className={cx("border-b border-line", r.cohort === "total" && "font-medium")}>
                <td className="py-2 pr-3 text-ink">{r.label}</td>
                {cols.map(([k]) => <td key={k} className="py-2 pr-3 text-right"><CountCell cell={r[k]} /></td>)}
                <td className="py-2 pl-2"><div className="flex justify-end gap-1">{r.sick_leave_by_week.map((c: any) => <CountCell key={c.start} cell={c} />)}</div></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11px] text-muted">{data.rule}</p>
    </div>
  );
}

export default function Workplaces() {
  const { runQuery, meta, user, scenario, degraded, asOf } = useApp();
  const perms = meta.permissions[user.role];
  const sites = useApi<any>(perms.site_scope !== "none" ? `/api/sites?${runQuery}` : null);
  const [sel, setSel] = useState<string | null>(null);
  const [exportMsg, setExportMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [district, setDistrict] = useState("DN");
  const [dSignal, setDSignal] = useState("heat_illness_visits");
  const site = sites.data?.sites.find((s: any) => s.id === sel) || sites.data?.sites[0];

  const doExport = async (kind: string) => {
    setExportMsg(null);
    try {
      const name = await downloadCsv(`/api/export?kind=${kind}&scenario=${scenario}&degraded=${degraded}&as_of=${asOf}`);
      setExportMsg({ ok: true, text: `Downloaded ${name} (suppression applied by the server).` });
    } catch (e: any) {
      setExportMsg({ ok: false, text: e.message });
    }
  };

  return (
    <div className="space-y-4 fade-in">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Workplaces and privacy</h1>
        <p className="text-sm text-muted">Weekly aggregated site information for your role. There are no personal worker records or disciplinary features in this system.</p>
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
        <div className="xl:col-span-5"><RoleScope /></div>
        <Card className="xl:col-span-7" title="Exports" icon={Download} subtitle="CSV files are generated by the server, scoped to your role, with small counts suppressed">
          <div className="flex flex-wrap gap-2">
            {[["zone_daily", "Zone signals (workplace counts weekly)"], ["sites", "Site weekly aggregates"]].map(([k, l]) => {
              const allowed = perms.exports.includes(k);
              return (
                <button key={k} onClick={() => doExport(k)} disabled={!allowed}
                  className={cx("inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm font-medium", allowed ? "border-navy-900 bg-navy-900 text-white hover:bg-navy-800" : "cursor-not-allowed border-line bg-slate-50 text-slate-400")}>
                  {allowed ? <Download className="h-4 w-4" /> : <Lock className="h-4 w-4" />}{l}{!allowed && " (not permitted)"}
                </button>
              );
            })}
          </div>
          {exportMsg && <p role="status" className={cx("mt-2 text-xs", exportMsg.ok ? "text-normal" : "text-action")}>{exportMsg.text}</p>}
          <p className="mt-3 text-xs text-muted">Exports carry a header stating that the data are synthetic and replayed as of {fmtDate(asOf)}, and that workplace counts are weekly.</p>
          {perms.district_series && (
            <div className="mt-4 border-t border-line pt-3">
              <div className="mb-2 flex flex-wrap items-end gap-3">
                <SelectBox label="District" value={district} onChange={setDistrict} options={meta.districts.map((d: any) => ({ value: d.id, label: d.name }))} />
                <SelectBox label="Signal" value={dSignal} onChange={setDSignal} options={meta.signals.filter((s: any) => s.kind === "count").map((s: any) => ({ value: s.id, label: s.short }))} />
              </div>
              <SignalChart path={`/api/districts/${district}/timeseries?${runQuery}&signal=${dSignal}&window=42`} height={180}
                title={["sick_leave", "fatigue_reports"].includes(dSignal) ? "District-level series, weekly (workplace counts are released as complete weeks only)" : "District-level series (includes district-only records)"} />
            </div>
          )}
        </Card>
      </div>

      {perms.site_scope === "none" ? (
        <Card title="Site-level views" icon={EyeOff}><Empty icon={Lock} title="Not available for your role">Policy & Regulatory Analysts work with zone status and district totals. Site and cohort data are restricted by the server.</Empty></Card>
      ) : sites.error ? <ErrorState error={sites.error} onRetry={sites.reload} /> : !sites.data ? <Loading /> : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
          <Card className="xl:col-span-7" title="Sites in your scope: last complete week" icon={Building2} subtitle={sites.data.window_note} bodyClass="p-0">
            {(() => {
              const rows = sites.data.sites;
              const withheld = rows.filter((s: any) => s.sick_leave_week?.suppressed).length;
              return rows.length > 0 && withheld > 0 ? (
                <p role="note" className="flex items-start gap-1.5 border-b border-line bg-amber-50 px-4 py-2 text-xs text-amber-950">
                  <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                  <span><span className="font-semibold">{withheld} of {rows.length} sites have a withheld sick-leave total this week.</span> “&lt;5” is a small count. “masked” is a larger cell withheld so that a small neighbour cannot be worked out as total minus the visible cells. Rates are shown only for released totals.</span>
                </p>
              ) : null;
            })()}
            {!sites.data.sites.length ? <Empty title="No complete week yet">Weekly workplace figures appear once a full Monday-Sunday week has been reported. Move the replay date forward.</Empty> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead><tr className="border-b border-line bg-slate-50 text-left text-xs text-muted">
                  <th className="px-4 py-2 font-medium">Site</th><th className="px-2 py-2 font-medium">Zone</th><th className="px-2 py-2 text-right font-medium">Workforce</th>
                  <th className="px-2 py-2 text-right font-medium">Sick leave, week</th><th className="px-2 py-2 text-right font-medium">Previous week</th><th className="px-2 py-2 text-right font-medium">Rate /100 worker-days</th><th className="px-2 py-2 text-right font-medium">Fatigue, week</th><th className="px-4 py-2 font-medium">Zone status</th>
                </tr></thead>
                <tbody>
                  {sites.data.sites.map((s: any) => (
                    <tr key={s.id} onClick={() => setSel(s.id)} className={cx("cursor-pointer border-b border-line", site?.id === s.id ? "bg-teal-50/70" : "hover:bg-slate-50")}>
                      <td className="px-4 py-2"><button className="text-left" onClick={() => setSel(s.id)}><div className="font-medium text-ink">{s.name}</div><div className="text-[11px] text-muted">{s.type} · {s.employer}</div></button></td>
                      <td className="px-2 py-2 text-xs">{s.zone.id}</td>
                      <td className="px-2 py-2 text-right tabular-nums">{s.workforce}</td>
                      <td className="px-2 py-2 text-right"><CountCell cell={s.sick_leave_week} /></td>
                      <td className="px-2 py-2 text-right"><CountCell cell={s.sick_leave_prev_week} /></td>
                      <td className="px-2 py-2 text-right tabular-nums">{s.absence_rate_per_100_worker_days ?? <span className="text-muted" title="Not shown: the weekly total is withheld.">—</span>}</td>
                      <td className="px-2 py-2 text-right"><CountCell cell={s.fatigue_week} /></td>
                      <td className="px-4 py-2"><StatusBadge status={s.zone_status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>)}
          </Card>
          <div className="space-y-4 xl:col-span-5">
            {site && (
              <Card title={`Sick-leave trend, weekly · ${site.name}`} icon={Table2}>
                <SignalChart path={`/api/sites/${site.id}/timeseries?${runQuery}&signal=sick_leave&window=84`} height={170} />
              </Card>
            )}
          </div>
          {site && (
            <Card className="xl:col-span-12" title={`Cohort breakdown · ${site.name}`} icon={Users} subtitle={`${site.type}, ${site.zone.id} ${site.zone.name}`}>
              <CohortPanel site={site} />
            </Card>
          )}
        </div>
      )}
      <p className="flex items-center gap-1.5 text-[11px] text-muted"><ShieldCheck className="h-3.5 w-3.5" />Employers, sites and workforces are synthetic.</p>
    </div>
  );
}
