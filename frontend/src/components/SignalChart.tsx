import { Area, CartesianGrid, ComposedChart, Line, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "../api";
import { useApp } from "../state";
import { ErrorState, Loading, cx, fmtDate } from "./ui";

function spans(rows: { date: string; tier: string }[], tier: string) {
  const out: { x1: string; x2: string }[] = [];
  let open = false;
  for (const r of rows) {
    if (r.tier === tier) {
      if (open) out[out.length - 1].x2 = r.date;
      else out.push({ x1: r.date, x2: r.date });
      open = true;
    } else open = false;
  }
  return out;
}

function TooltipBody({ active, payload, unit }: any) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-lg border border-line bg-white px-3 py-2 text-xs shadow-lg">
      <div className="mb-1 font-semibold text-ink">{p.week_end ? `Week ${fmtDate(p.date)} to ${fmtDate(p.week_end)}` : fmtDate(p.date, { weekday: "short", day: "numeric", month: "short" })}</div>
      {p.suppressed ? (
        p.derived
          ? <div className="text-slate-600">Observed: <span className="font-mono">masked</span> (withheld so that a smaller group cannot be worked out)</div>
          : <div className="text-slate-600">Observed: <span className="font-mono">&lt;5</span> (suppressed)</div>
      ) : p.observed === null ? (
        <div className="text-slate-600">Observed: {p.status === "pending" ? "pending (reporting incomplete)" : "no data"}</div>
      ) : (
        <div>Observed{p.status === "provisional" ? " (provisional estimate)" : ""}: <span className="font-semibold tabular-nums">{p.observed}</span> {unit}</div>
      )}
      {p.expected !== null && <div className="text-muted">Expected: <span className="tabular-nums">{p.expected}</span> {unit} (band {p.lower}–{p.upper})</div>}
      {p.z !== null && <div className="text-muted">Deviation: z = {p.z > 0 ? "+" : ""}{p.z}</div>}
      {p.reported_so_far && <div className="text-muted">Records reported so far: {p.reported_so_far.display}</div>}
      {p.completeness !== null && p.status !== "complete" && <div className="text-muted">Estimated reporting completeness: {Math.round(p.completeness * 100)}%</div>}
    </div>
  );
}

export function SignalChart({ path, height = 220, title, showLegend = true, highlight }: {
  path: string | null; height?: number; title?: string; showLegend?: boolean; highlight?: { from: string; to: string } | null;
}) {
  const { data, error, loading, reload } = useApi<any>(path);
  if (error) return <ErrorState error={error} onRetry={reload} compact />;
  if (!data) return <Loading className="py-6" />;
  const unit = data.signal.unit;
  // Suppressed days carry observed = null: they are drawn as a gap in the line, never as zero.
  const pts = data.points.map((p: any) => ({ ...p, band: p.lower !== null ? [Math.max(0, p.lower), p.upper] : null }));
  const suppressedDates: string[] = pts.filter((p: any) => p.suppressed).map((p: any) => p.date);
  const tierRows = Object.values(data.tiers || {})[0] as any[] | undefined;
  const watch = tierRows ? spans(tierRows, "watch") : [];
  const action = tierRows ? spans(tierRows, "action") : [];
  const pending = spans(pts.map((p: any) => ({ date: p.date, tier: p.status === "pending" || (p.status === "missing") ? "x" : "" })), "x");
  const anySuppressed = pts.some((p: any) => p.suppressed);
  // Withheld days are marked with a short tick on the baseline (never drawn as a value of zero).
  const ymax = Math.max(1, ...pts.map((p: any) => Math.max(p.observed ?? 0, p.upper ?? 0)));
  const tickH = ymax * 0.07;
  const isCount = data.signal.kind === "count";
  const isWeek = data.grain === "week";
  return (
    <div className={cx("relative", loading && "opacity-70 transition-opacity")}>
      {title && <div className="mb-1 text-xs font-semibold text-ink">{title}</div>}
      <div style={{ height }} aria-label={`${data.signal.label} observed versus expected chart`} role="img">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={pts} margin={{ top: 4, right: 8, bottom: 0, left: 6 }}>
            <CartesianGrid stroke="#eef1f5" vertical={false} />
            {watch.map((s, i) => <ReferenceArea key={`w${i}`} x1={s.x1} x2={s.x2} fill="#f5b544" fillOpacity={0.16} ifOverflow="extendDomain" />)}
            {action.map((s, i) => <ReferenceArea key={`a${i}`} x1={s.x1} x2={s.x2} fill="#d92d20" fillOpacity={0.12} ifOverflow="extendDomain" />)}
            {pending.map((s, i) => <ReferenceArea key={`p${i}`} x1={s.x1} x2={s.x2} fill="#98a2b3" fillOpacity={0.18} ifOverflow="extendDomain" />)}
            {highlight && !isWeek && <ReferenceArea x1={highlight.from} x2={highlight.to} stroke="#0e7c74" strokeDasharray="3 3" fill="none" />}
            <XAxis dataKey="date" tickFormatter={(d) => fmtDate(d)} minTickGap={24} tickLine={false} axisLine={{ stroke: "#d6dce4" }} />
            <YAxis width={44} tickLine={false} axisLine={false} domain={isCount ? [0, "auto"] : ["auto", "auto"]} allowDecimals={false} label={{ value: unit, angle: -90, position: "insideLeft", offset: 4, style: { fontSize: 11, fill: "#5b6b7d", textAnchor: "middle" } }} />
            <Tooltip content={<TooltipBody unit={unit} />} />
            <Area dataKey="band" stroke="none" fill="#0e7c74" fillOpacity={0.09} isAnimationActive={false} name="Expected range" />
            <Line dataKey="expected" stroke="#5b6b7d" strokeDasharray="4 3" strokeWidth={1.5} dot={false} isAnimationActive={false} name="Expected (past-only baseline)" />
            <Line dataKey="observed" stroke="#0b1f33" strokeWidth={2} isAnimationActive={false} connectNulls={false} name="Observed"
              dot={(props: any) => {
                const p = props.payload;
                if (p.observed === null) return <g key={props.key} />;
                const prov = p.status === "provisional";
                return <circle key={props.key} cx={props.cx} cy={props.cy} r={prov ? 3.2 : 2.2} fill={prov ? "#fff" : "#0b1f33"} stroke="#0b1f33" strokeWidth={1.4} />;
              }} />
            {suppressedDates.map((d) => <ReferenceLine key={`s${d}`} segment={[{ x: d, y: 0 }, { x: d, y: tickH }]} stroke="#667085" strokeWidth={3} strokeLinecap="round" ifOverflow="extendDomain" />)}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      {showLegend && (
        <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-muted">
          <li className="flex items-center gap-1.5"><span className="h-0.5 w-4 bg-navy-900" />Observed</li>
          <li className="flex items-center gap-1.5"><span className="h-0 w-4 border-t-2 border-dashed border-slate-500" />{isWeek ? "Expected (mean of the four preceding released weeks)" : <>Expected (28-day past-only baseline{isCount ? ", day-of-week adjusted" : ""}; withheld days are left out)</>}</li>
          <li className="flex items-center gap-1.5"><span className="h-2.5 w-4 rounded-sm bg-teal-700/15" />Expected range (±2 SD)</li>
          {isCount && !isWeek && <li className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full border-[1.5px] border-navy-900 bg-white" />Provisional (reporting incomplete)</li>}
          {isWeek && <li className="flex items-center gap-1.5">Each point is one complete Monday-Sunday week; no daily workplace counts are released.</li>}
          {tierRows && <li className="flex items-center gap-1.5"><span className="h-2.5 w-4 rounded-sm bg-amber-400/30" />Watch</li>}
          {tierRows && <li className="flex items-center gap-1.5"><span className="h-2.5 w-4 rounded-sm bg-red-600/20" />Action required</li>}
          {pending.length > 0 && <li className="flex items-center gap-1.5"><span className="h-2.5 w-4 rounded-sm bg-slate-400/30" />Pending / no data</li>}
          {anySuppressed && <li className="flex items-center gap-1.5"><span className="h-2.5 w-[3px] rounded-full bg-slate-500" />Grey tick on the baseline: value withheld (a count below 5, or a cell that protects one). The line has a gap there; it is not zero.</li>}
        </ul>
      )}
    </div>
  );
}

export function useZoneSeriesPath(zone: string | null, signal: string, window: number) {
  const { runQuery } = useApp();
  return zone ? `/api/zones/${zone}/timeseries?${runQuery}&signal=${signal}&window=${window}` : null;
}
