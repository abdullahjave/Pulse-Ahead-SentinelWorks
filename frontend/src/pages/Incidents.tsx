import { BookOpenCheck, CalendarClock, CheckCircle2, ClipboardCheck, ExternalLink, FileWarning, Gauge, Info, ListChecks, MapPin, ShieldAlert, Siren, Users, WifiOff } from "lucide-react";
import { useState } from "react";
import { api, useApi } from "../api";
import { SignalChart } from "../components/SignalChart";
import { Card, Empty, ErrorState, FAMILY_COLOR, Loading, STATUS, Segmented, StatusBadge, Tip, cx, fmtDate, pct, signed } from "../components/ui";
import { useApp } from "../state";

const DECISION_LABEL: Record<string, string> = {
  acknowledged: "Acknowledged", action_planned: "Response planned", not_actionable: "Reviewed: not actionable", noted_for_review: "Noted for policy review",
};

function IncidentList({ rows, counts, asOf, selected, onSelect, filter, setFilter }: any) {
  const shown = rows.filter((r: any) => filter === "all" || (filter === "past" ? r.status === "closed" : r.status !== "closed"));
  return (
    <Card title="Incidents" icon={Siren} subtitle="One incident per zone and risk-track episode" bodyClass="p-0">
      <div className="border-b border-line px-4 py-2.5">
        <Segmented label="Incident filter" value={filter} onChange={setFilter} options={[{ value: "open", label: `Open (${counts.open})` }, { value: "past", label: `Past (${counts.past})` }, { value: "all", label: `All (${counts.all})` }]} />
      </div>
      {!shown.length ? (
        <Empty title={filter === "past" ? "No past incidents" : filter === "open" ? "No open incidents" : "No incidents"} icon={CheckCircle2}>
          {filter === "past" ? `No incident has been closed by ${fmtDate(asOf)}.`
            : filter === "open" ? (counts.past > 0 ? `Nothing is open on ${fmtDate(asOf)}. ${counts.past} earlier incident${counts.past === 1 ? " was" : "s were"} raised and closed in this replay.` : `No incident has been raised for your scope by ${fmtDate(asOf)}.`)
            : `No incident has been raised for your scope by ${fmtDate(asOf)}.`}
          {filter === "open" && counts.past > 0 && <button onClick={() => setFilter("past")} className="mt-3 block rounded-md bg-navy-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-navy-800">View past incidents ({counts.past})</button>}
        </Empty>
      ) : (
        <ul className="scroll-thin max-h-[calc(100vh-260px)] divide-y divide-line overflow-y-auto">
          {shown.map((r: any) => (
            <li key={r.id}>
              <button onClick={() => onSelect(r.id)} aria-current={selected === r.id}
                className={cx("flex w-full flex-col gap-1 px-4 py-3 text-left transition-colors", selected === r.id ? "bg-teal-50/70" : "hover:bg-slate-50")}>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium text-ink">{r.zone.id} · {r.zone.name}</span>
                  <StatusBadge status={r.status === "closed" ? "normal" : r.max_tier} />
                </div>
                <span className="text-xs text-muted">{r.track_label}</span>
                <span className="flex flex-wrap gap-x-3 text-[11px] text-muted">
                  <span>Opened {fmtDate(r.opened)}</span>
                  {r.action_date && <span>Action {fmtDate(r.action_date)}</span>}
                  <span className="capitalize">{r.status === "monitoring" ? "cooling down" : r.status}</span>
                  {r.decision && <span className="font-medium text-teal-brand">{DECISION_LABEL[r.decision.status]}</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function ScoreGauge({ score, thresholds }: any) {
  const max = 4;
  if (score === null || score === undefined) {
    return <p className="flex items-start gap-1.5 rounded-md bg-slate-50 px-3 py-2 text-xs text-muted"><ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />Exact composite score withheld: a signal with fewer than 5 records contributes to it, and showing the total would let that count be worked out. The alert tier is unaffected.</p>;
  }
  const p = (v: number) => `${Math.min(v / max, 1) * 100}%`;
  return (
    <div>
      <div className="relative h-3 rounded-full bg-slate-100">
        <div className="absolute inset-y-0 left-0 rounded-full bg-navy-900" style={{ width: p(score || 0) }} />
        {[["Watch", thresholds.watch, "bg-watch"], ["Action", thresholds.action, "bg-action"]].map(([l, v, c]: any) => (
          <div key={l} className="absolute -top-1 h-5 w-0.5" style={{ left: p(v) }}><span className={cx("block h-full w-full", c)} /></div>
        ))}
      </div>
      <div className="relative mt-1 h-4 text-[11px] text-muted">
        <span className="absolute" style={{ left: p(thresholds.watch), transform: "translateX(-50%)" }}>Watch {thresholds.watch}</span>
        <span className="absolute" style={{ left: p(thresholds.action), transform: "translateX(-50%)" }}>Action {thresholds.action}</span>
      </div>
    </div>
  );
}

function Contributions({ rows }: any) {
  return (
    <ul className="space-y-3">
      {rows.map((c: any) => (
        <li key={c.signal}>
          <div className="flex flex-wrap items-baseline justify-between gap-x-3">
            <span className="flex items-center gap-2 text-sm font-medium text-ink">
              <span className="h-2.5 w-2.5 rounded-sm" style={{ background: FAMILY_COLOR[c.family] }} aria-hidden />{c.label}
              <span className="text-[11px] font-normal text-muted">{c.family_label} · weight {c.weight}</span>
            </span>
            <span className="text-sm font-semibold tabular-nums text-ink">{c.suppressed ? "suppressed" : c.share === null ? "no data" : pct(c.share)}</span>
          </div>
          <div className="mt-1 h-2 rounded-full bg-slate-100">
            <div className="h-2 rounded-full" style={{ width: `${(c.share || 0) * 100}%`, background: FAMILY_COLOR[c.family] }} />
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-muted">
            {c.weekly_only ? <span className="inline-flex items-center gap-1"><Info className="h-3 w-3" />Workplace counts are released as complete weeks only (see Workplaces &amp; privacy). Only this signal's share of the score is shown.</span> : c.suppressed ? <span className="inline-flex items-center gap-1"><ShieldAlert className="h-3 w-3" />Underlying count below 5: values withheld</span> : <>
              {c.share_basis && <span className="inline-flex items-center gap-1"><Info className="h-3 w-3" />share of visible signals only</span>}
              <span>z {signed(c.z)}</span>
              {c.pct_deviation !== null && <span>{signed(c.pct_deviation * 100, 0)}% vs expected</span>}
              {c.value !== null && <span>{c.value} {c.unit} (expected {c.expected})</span>}
              {c.values_hidden_for_role && <span className="inline-flex items-center gap-1"><ShieldAlert className="h-3 w-3" />Counts hidden for your role</span>}
              {c.date && <span>data for {fmtDate(c.date)}</span>}
              {c.status !== "current" && <span className="font-medium text-watch">{c.status.replace("_", " ")}</span>}
            </>}
          </div>
        </li>
      ))}
    </ul>
  );
}

function TierStrip({ timeline }: any) {
  return (
    <div>
      <div className="flex gap-0.5" role="img" aria-label="Daily alert tier timeline">
        {timeline.map((t: any) => (
          <Tip key={t.date} text={`${fmtDate(t.date)}: ${STATUS[t.tier].label}, score ${t.score ?? "withheld (small count)"}, data confidence ${t.confidence}`}>
            <span className={cx("block h-6 w-5 rounded-sm sm:w-6", STATUS[t.tier].dot, t.tier === "normal" && "opacity-40")} />
          </Tip>
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-muted"><span>{fmtDate(timeline[0]?.date)}</span><span>{fmtDate(timeline[timeline.length - 1]?.date)}</span></div>
    </div>
  );
}

function DecisionPanel({ d, onSaved }: any) {
  const { scenario, degraded, asOf, user } = useApp();
  const [status, setStatus] = useState<string>(d.allowed_decisions[0]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true); setErr(null); setOk(null);
    try {
      await api(`/api/incidents/${encodeURIComponent(d.id)}/decision`, { method: "POST", body: JSON.stringify({ scenario, degraded, as_of: asOf, status, note }) });
      setNote("");
      setOk("Response recorded. No one was notified.");
      onSaved();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Response record" icon={ClipboardCheck} subtitle="Records your acknowledgement or planned response in the prototype database, with your role and time. It does not notify anyone or dispatch anything; the responsible officer acts outside this system.">
      {d.recording_enabled === false ? (
        <div role="note" className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <span className="font-semibold">Read-only demo.</span> Recording a response is unavailable here. {d.recording_unavailable_reason}
        </div>
      ) : (
      <fieldset className="space-y-2">
        <legend className="sr-only">Response</legend>
        <div className="flex flex-wrap gap-2">
          {d.allowed_decisions.map((s: string) => (
            <label key={s} className={cx("flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-1.5 text-sm", status === s ? "border-navy-900 bg-navy-900 text-white" : "border-line bg-white text-ink hover:bg-slate-50")}>
              <input type="radio" name="decision" value={s} checked={status === s} onChange={() => setStatus(s)} className="sr-only" />{DECISION_LABEL[s]}
            </label>
          ))}
        </div>
        <label className="block">
          <span className="text-xs font-medium text-muted">Note (optional, no personal data)</span>
          <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={500}
            className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm" placeholder="e.g. Reviewed with site leads; heat plan activated at A-01" />
        </label>
        <div className="flex items-center gap-3">
          <button onClick={submit} disabled={busy} className="rounded-md bg-teal-brand px-3 py-1.5 text-sm font-medium text-white hover:bg-teal-800 disabled:opacity-50">
            {busy ? "Recording…" : "Record response"}
          </button>
          {ok && <span role="status" className="text-xs font-medium text-normal">{ok}</span>}
          {err && <span role="alert" className="text-xs font-medium text-action">{err}</span>}
        </div>
      </fieldset>
      )}
      <div className="mt-4 border-t border-line pt-3">
        <div className="mb-2 text-xs font-semibold text-ink">Response history</div>
        {!d.decisions.length ? <p className="text-xs text-muted">No response recorded yet.</p> : (
          <ol className="space-y-2">
            {[...d.decisions].reverse().map((r: any) => (
              <li key={r.id} className="rounded-md bg-slate-50 px-3 py-2 text-xs">
                <div className="flex flex-wrap justify-between gap-2"><span className="font-medium text-ink">{DECISION_LABEL[r.status]}</span>
                  <span className="text-muted">{new Date(r.recorded_at).toLocaleString("en-GB")} · replay date {fmtDate(r.replay_date)}</span></div>
                <div className="text-muted">{r.role.replace(/_/g, " ")}{r.user_id === user?.id ? " (you)" : ""}{r.note ? ` · “${r.note}”` : ""}</div>
              </li>
            ))}
          </ol>
        )}
      </div>
    </Card>
  );
}

function Detail({ id }: { id: string }) {
  const { runQuery, meta, bumpData, dataVersion } = useApp();
  const { data: d, error, reload } = useApi<any>(`/api/incidents/${encodeURIComponent(id)}?${runQuery}&v=${dataVersion}`);
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!d) return <Loading label="Loading incident…" />;
  const visible = new Set(meta.signals.filter((s: any) => s.zone_visible).map((s: any) => s.id));
  const chartSignals = d.contributions.map((c: any) => c.signal).filter((s: string) => visible.has(s));
  const hidden = d.contributions.filter((c: any) => !visible.has(c.signal));
  const highlight = d.when.opened ? { from: d.when.opened, to: d.when.last_active || d.when.opened } : null;
  // Chart window reaches back to 7 days before the incident opened, so past incidents show their own episode.
  const daysSinceOpen = d.when.opened ? Math.round((Date.parse(d.as_of) - Date.parse(d.when.opened)) / 86400000) : 0;
  const chartWindow = Math.min(90, Math.max(28, daysSinceOpen + 8));
  const pb = d.playbook;
  return (
    <div className="space-y-4 fade-in">
      <section className="rounded-xl border border-line bg-white p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs font-medium text-muted">{d.track_label} · Incident {d.id}</div>
            <h1 className="mt-0.5 text-lg font-semibold tracking-tight text-ink">{d.zone.id} {d.zone.name}, {d.where.district.name}</h1>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <StatusBadge status={d.status === "closed" ? "normal" : d.current_tier} size="md" />
              {d.max_tier === "action" && d.current_tier !== "action" && <span className="text-xs text-muted">Reached Action required on {fmtDate(d.when.action)}</span>}
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium capitalize text-slate-700">{d.status === "monitoring" ? "Cooling down" : d.status}</span>
              <Tip text={d.confidence.basis + " It describes the data, not the chance that an event is happening."}>
                <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700"><Gauge className="h-3.5 w-3.5" />Data confidence: {d.confidence.label}</span>
              </Tip>
            </div>
          </div>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-4">
            {[["Opened", d.when.opened], ["Action", d.when.action], ["Peak score", d.when.peak], [d.when.closed ? "Closed" : "Last active", d.when.closed || d.when.last_active]].map(([l, v]) => (
              <div key={l}><dt className="text-muted">{l}</dt><dd className="font-semibold text-ink">{v ? fmtDate(v) : "—"}</dd></div>
            ))}
          </dl>
        </div>
      </section>

      <div className="grid grid-cols-1 gap-4 2xl:grid-cols-12">
        <div className="space-y-4 2xl:col-span-7">
          <Card title="Why this alert" icon={Info} subtitle={`Evaluated on ${fmtDate(d.focus_date)}. Composite score = sum of weight × clipped z-score.`}>
            <div className="mb-4 grid grid-cols-1 gap-4 sm:grid-cols-[1fr_auto] sm:items-end">
              <ScoreGauge score={d.score} thresholds={d.thresholds} />
              <div className="text-right"><div className="text-xs text-muted">Composite score</div><div className="text-2xl font-semibold tabular-nums">{d.score === null ? "withheld" : d.score.toFixed(1)}</div></div>
            </div>
            <p className="mb-3 text-xs text-muted">Action required needs a score of at least {d.thresholds.action} with corroboration (z ≥ {d.thresholds.corroboration_z}) from at least {d.thresholds.min_families} different signal families, on two consecutive days. Corroborating families here: <span className="font-medium text-ink">{d.families.length ? d.families.join(", ") : "none"}</span>.</p>
            <Contributions rows={d.contributions} />
            <p className="mt-4 flex items-start gap-1.5 rounded-md bg-slate-50 px-3 py-2 text-[11px] text-muted"><Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />{d.explanation_note}</p>
          </Card>

          <Card title="Observed versus expected" icon={CalendarClock} subtitle="Dashed outline marks the incident window">
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              {chartSignals.map((s: string) => (
                <SignalChart key={s} title={meta.signals.find((x: any) => x.id === s).label}
                  path={`/api/zones/${d.zone.id}/timeseries?${runQuery}&signal=${s}&window=${chartWindow}`} height={170} showLegend={false} highlight={highlight} />
              ))}
            </div>
            {hidden.length > 0 && (
              <p className="mt-4 flex items-start gap-1.5 rounded-md bg-slate-50 px-3 py-2 text-xs text-muted"><ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                {hidden.map((c: any) => c.short).join(", ")}: zone-level counts are not shown to your role. Their contribution shares above come from the same backend calculation.</p>
            )}
            <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted">
              <li className="flex items-center gap-1.5"><span className="h-0.5 w-4 bg-navy-900" />Observed</li>
              <li className="flex items-center gap-1.5"><span className="w-4 border-t-2 border-dashed border-slate-500" />Expected</li>
              <li className="flex items-center gap-1.5"><span className="h-2.5 w-4 rounded-sm bg-amber-400/30" />Watch</li>
              <li className="flex items-center gap-1.5"><span className="h-2.5 w-4 rounded-sm bg-red-600/20" />Action required</li>
              <li className="flex items-center gap-1.5"><span className="h-2.5 w-4 rounded-sm bg-slate-400/30" />Pending / no data</li>
              <li className="flex items-center gap-1.5"><span className="h-2.5 w-[3px] rounded-full bg-slate-500" />Value withheld (gap, not zero)</li>
            </ul>
          </Card>

          <Card title="Where and when" icon={MapPin}>
            <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
              <div>
                <div className="mb-1 text-xs font-semibold text-ink">Daily tier (each replay day)</div>
                <TierStrip timeline={d.timeline} />
                <div className="mt-3 text-xs font-semibold text-ink">First corroborating deviation by signal</div>
                <ul className="mt-1 space-y-1 text-xs text-muted">
                  {d.when.signal_onsets.length ? d.when.signal_onsets.map((o: any) => <li key={o.signal} className="flex justify-between gap-2"><span>{o.label}</span><span className="font-medium tabular-nums text-ink">{fmtDate(o.date)}</span></li>)
                    : <li>No signal reached the corroboration level.</li>}
                </ul>
              </div>
              <div>
                <div className="mb-1 text-xs font-semibold text-ink">Spatial level: zone {d.zone.id}. Location quality of {d.where.district.name} clinic records</div>
                <ul className="space-y-1.5 text-xs">
                  {d.where.location_quality.levels.map((l: any) => (
                    <li key={l.id}>
                      <div className="flex justify-between text-muted"><span>{l.label}</span><span className="tabular-nums">{pct(l.share)}</span></div>
                      <div className="mt-0.5 h-1.5 rounded-full bg-slate-100"><div className="h-1.5 rounded-full bg-teal-brand" style={{ width: `${(l.share || 0) * 100}%`, opacity: { high: 1, medium: 0.7, low: 0.45, none: 0.25 }[l.confidence as string] }} /></div>
                    </li>
                  ))}
                </ul>
                <p className="mt-2 text-[11px] text-muted">Zone counts are adjusted for the share of district records that could be placed in a zone. District-only records stay in district totals.</p>
                {d.related.length > 0 && <>
                  <div className="mt-3 text-xs font-semibold text-ink">Related incidents (same risk, within 3 days)</div>
                  <ul className="mt-1 text-xs text-muted">{d.related.map((r: any) => <li key={r.id}>{r.zone.id} {r.zone.name} · {STATUS[r.max_tier].label}</li>)}</ul>
                </>}
              </div>
            </div>
          </Card>
        </div>

        <div className="space-y-4 2xl:col-span-5">
          <Card title="Data quality and uncertainty" icon={WifiOff} subtitle={`${pct(d.weight_available)} of this track's signal weight had current data`}>
            {!d.data_issues.length ? <p className="flex items-center gap-2 text-sm text-normal"><CheckCircle2 className="h-4 w-4" />All contributing feeds were current on the evaluation day.</p> : (
              <ul className="space-y-2">
                {d.data_issues.map((i: any, n: number) => (
                  <li key={n} className="flex items-start gap-2 rounded-md bg-watch-bg px-3 py-2 text-xs text-watch">
                    <FileWarning className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <span><span className="font-semibold">{meta.signals.find((s: any) => s.id === i.signal)?.label}: {i.issue.replace("_", " ")}</span>. {i.detail}.</span>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-3 text-[11px] text-muted">Missing or delayed feeds lower data confidence and can make a zone "Insufficient data". They are never counted as normal.</p>
          </Card>

          {pb ? (
            <Card title="Recommended response" icon={BookOpenCheck} subtitle={`Playbook for ${STATUS[pb.tier].label} · ${pb.title}`} className="border-teal-200">
              <div className="mb-3 flex items-start gap-2 rounded-md bg-teal-50 px-3 py-2">
                <Users className="mt-0.5 h-4 w-4 shrink-0 text-teal-brand" />
                <div className="text-sm"><div className="text-xs text-muted">Responsible role</div><div className="font-semibold text-ink">{pb.owner}</div></div>
              </div>
              <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-ink"><ListChecks className="h-3.5 w-3.5" />Suggested actions (proportionate, targeted)</div>
              <ol className="mb-4 list-decimal space-y-1 pl-5 text-sm text-ink">{pb.actions.map((a: string) => <li key={a}>{a}</li>)}</ol>
              <div className="mb-1 text-xs font-semibold text-ink">Evidence from this incident</div>
              <ul className="mb-4 space-y-1 text-xs text-muted">{d.evidence.map((e: any) => <li key={e.signal} className="flex gap-2"><span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-teal-brand" />{e.text}</li>)}</ul>
              <div className="mb-1 text-xs font-semibold text-ink">Supporting guidance</div>
              <ul className="mb-4 space-y-1.5 text-xs">
                {pb.guidance.map((g: any) => (
                  <li key={g.id}>
                    <a href={g.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-medium text-teal-brand hover:underline">{g.title}<ExternalLink className="h-3 w-3" /></a>
                    <div className="text-muted">{g.note}</div>
                  </li>
                ))}
              </ul>
              <div className="mb-1 text-xs font-semibold text-ink">Assumptions</div>
              <ul className="mb-3 list-disc space-y-1 pl-5 text-xs text-muted">{pb.assumptions.map((a: string) => <li key={a}>{a}</li>)}</ul>
              {pb.other_roles_with_playbooks.length > 0 && <p className="text-[11px] text-muted">Other roles receive their own playbook: {pb.other_roles_with_playbooks.join("; ")}.</p>}
              <p className="mt-2 flex items-start gap-1.5 rounded-md bg-amber-50 px-3 py-2 text-[11px] text-amber-900"><ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />{pb.review_note}</p>
            </Card>
          ) : <Card title="Recommended response" icon={BookOpenCheck}><Empty title="No playbook for this tier" /></Card>}

          <DecisionPanel d={d} onSaved={() => { bumpData(); }} />
        </div>
      </div>
    </div>
  );
}

export default function Incidents() {
  const { runQuery, route, navigate, dataVersion } = useApp();
  const [filter, setFilter] = useState<"open" | "past" | "all">("open");
  const { data, error, reload } = useApi<any>(`/api/incidents?${runQuery}&v=${dataVersion}`);
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return <Loading label="Loading incidents…" />;
  const counts = data.counts;
  const inFilter = (r: any) => filter === "all" || (filter === "past" ? r.status === "closed" : r.status !== "closed");
  const routed = route.id && data.incidents.find((r: any) => r.id === route.id) ? route.id : undefined;
  const selected = routed ?? data.incidents.find(inFilter)?.id;
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[320px_1fr]">
      <div><IncidentList rows={data.incidents} counts={counts} asOf={data.as_of} selected={selected} filter={filter} setFilter={setFilter} onSelect={(id: string) => navigate("incidents", id)} /></div>
      <div className="min-w-0">
        {route.id && !data.incidents.find((r: any) => r.id === route.id) && (
          <div className="mb-3"><ErrorState error={{ name: "x", message: `Incident ${route.id} has not been raised by this replay date or is outside your scope.`, status: 404 } as any} /></div>
        )}
        {selected ? <Detail id={selected} /> : (
          <Card>
            {counts.all === 0 ? <Empty title="No incidents raised yet" icon={Siren}>No incident has been raised for your scope by {fmtDate(data.as_of)}. Move the replay date forward or jump to an event.</Empty>
              : <Empty title={filter === "past" ? "No past incidents" : "No open incidents"} icon={CheckCircle2}>
                  {filter === "past" ? "No incident has been closed by this replay date." : `All ${counts.past} incident${counts.past === 1 ? "" : "s"} raised so far ${counts.past === 1 ? "has" : "have"} closed.`}
                  {counts.past > 0 && filter !== "past" && <button onClick={() => { setFilter("past"); const p = data.incidents.find((r: any) => r.status === "closed"); if (p) navigate("incidents", p.id); }} className="mt-3 block rounded-md bg-navy-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-navy-800">Open the most recent past incident</button>}
                </Empty>}
          </Card>
        )}
      </div>
    </div>
  );
}
