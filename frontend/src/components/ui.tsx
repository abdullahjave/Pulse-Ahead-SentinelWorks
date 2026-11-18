import { AlertTriangle, CircleCheck, CircleHelp, Info, Loader2, OctagonAlert, ShieldAlert, WifiOff } from "lucide-react";
import type { ReactNode } from "react";
import type { ApiError } from "../api";

export const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");

export const STATUS: Record<string, { label: string; text: string; bg: string; ring: string; dot: string; icon: any }> = {
  action: { label: "Action required", text: "text-action", bg: "bg-action-bg", ring: "ring-action/30", dot: "bg-action", icon: OctagonAlert },
  watch: { label: "Watch", text: "text-watch", bg: "bg-watch-bg", ring: "ring-watch/30", dot: "bg-watch", icon: AlertTriangle },
  normal: { label: "Normal", text: "text-normal", bg: "bg-normal-bg", ring: "ring-normal/25", dot: "bg-normal", icon: CircleCheck },
  insufficient_data: { label: "Insufficient data", text: "text-nodata", bg: "bg-nodata-bg", ring: "ring-nodata/30", dot: "bg-nodata", icon: WifiOff },
  out_of_scope: { label: "Outside your scope", text: "text-muted", bg: "bg-white", ring: "ring-line", dot: "bg-slate-300", icon: ShieldAlert },
};

export function StatusBadge({ status, size = "sm", className }: { status: string; size?: "sm" | "md"; className?: string }) {
  const s = STATUS[status] || STATUS.normal;
  const Icon = s.icon;
  return (
    <span className={cx("inline-flex items-center gap-1.5 rounded-full font-medium ring-1 ring-inset whitespace-nowrap", s.bg, s.text, s.ring,
      size === "sm" ? "px-2 py-0.5 text-xs" : "px-2.5 py-1 text-sm", className)}>
      <Icon className={size === "sm" ? "h-3.5 w-3.5" : "h-4 w-4"} aria-hidden />
      {s.label}
    </span>
  );
}

export function Card({ title, subtitle, actions, children, className, bodyClass, icon }: {
  title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; bodyClass?: string; icon?: any;
}) {
  const Icon = icon;
  return (
    <section className={cx("min-w-0 rounded-xl border border-line bg-white shadow-[0_1px_2px_rgba(16,24,40,0.04)]", className)}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
              {Icon && <Icon className="h-4 w-4 text-teal-brand" aria-hidden />}
              {title}
            </h2>
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cx("p-4", bodyClass)}>{children}</div>
    </section>
  );
}

export function Kpi({ label, value, sub, tone, icon }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string; icon?: any }) {
  const Icon = icon;
  const s = tone ? STATUS[tone] : null;
  return (
    <div className="rounded-xl border border-line bg-white px-4 py-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-muted">{label}</span>
        {Icon && (
          <span className={cx("grid h-7 w-7 place-items-center rounded-lg", s ? s.bg : "bg-teal-50")}>
            <Icon className={cx("h-4 w-4", s ? s.text : "text-teal-brand")} aria-hidden />
          </span>
        )}
      </div>
      <div className="mt-1 text-2xl font-semibold tracking-tight text-ink tabular-nums">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
    </div>
  );
}

export function Loading({ label = "Loading…", className }: { label?: string; className?: string }) {
  return (
    <div role="status" className={cx("flex items-center justify-center gap-2 py-10 text-sm text-muted", className)}>
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> {label}
    </div>
  );
}

export function ErrorState({ error, onRetry, compact }: { error: ApiError | Error | null; onRetry?: () => void; compact?: boolean }) {
  if (!error) return null;
  const status = (error as ApiError).status;
  const forbidden = status === 403;
  return (
    <div role={forbidden ? "note" : "alert"} className={cx("flex items-start gap-3 rounded-lg border px-4 text-sm",
      forbidden ? "border-line bg-slate-50 text-muted" : "border-action/30 bg-action-bg text-action", compact ? "py-2" : "py-4")}>
      {forbidden ? <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden /> : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />}
      <div className="min-w-0 flex-1">
        <p className="font-medium">{forbidden ? "Restricted for your role" : "Could not load this section"}</p>
        <p className="mt-0.5 text-xs opacity-90">{error.message}{status ? ` (HTTP ${status})` : ""}</p>
      </div>
      {onRetry && !forbidden && (
        <button onClick={onRetry} className="rounded-md border border-action/30 bg-white px-2 py-1 text-xs font-medium hover:bg-action-bg">Retry</button>
      )}
    </div>
  );
}

export function Empty({ icon, title, children }: { icon?: any; title: string; children?: ReactNode }) {
  const Icon = icon || Info;
  return (
    <div className="flex flex-col items-center justify-center gap-1 px-6 py-10 text-center">
      <Icon className="h-6 w-6 text-slate-400" aria-hidden />
      <p className="mt-1 text-sm font-medium text-ink">{title}</p>
      {children && <p className="max-w-sm text-xs text-muted">{children}</p>}
    </div>
  );
}

export function Tip({ text, children, className }: { text: string; children?: ReactNode; className?: string }) {
  return (
    <span className={cx("group relative inline-flex items-center", className)} tabIndex={0} aria-label={text}>
      {children ?? <CircleHelp className="h-3.5 w-3.5 text-slate-400" aria-hidden />}
      <span role="tooltip" className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 w-64 -translate-x-1/2 rounded-md bg-navy-900 px-3 py-2 text-xs font-normal leading-snug text-white opacity-0 shadow-lg transition-opacity duration-150 group-hover:opacity-100 group-focus:opacity-100">
        {text}
      </span>
    </span>
  );
}

export function CountCell({ cell, tipText }: { cell: any; tipText?: string }) {
  if (!cell) return <span className="text-muted">—</span>;
  if (cell.suppressed) {
    const t = cell.reason === "complementary"
      ? "Masked so that a small group cannot be worked out from the total (complementary suppression)."
      : cell.reason === "derived"
      ? "Withheld so that a smaller group cannot be worked out from totals."
      : tipText || "Fewer than 5. Small counts are suppressed by the server to prevent re-identification and disciplinary misuse.";
    return (
      <Tip text={t}>
        <span className="inline-flex items-center gap-1 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-600 ring-1 ring-inset ring-slate-200">
          <ShieldAlert className="h-3 w-3" aria-hidden />{cell.display}
        </span>
      </Tip>
    );
  }
  return <span className="tabular-nums">{cell.display}</span>;
}

export function Segmented<T extends string>({ value, onChange, options, label, size = "sm" }: {
  value: T; onChange: (v: T) => void; options: { value: T; label: string }[]; label: string; size?: "sm" | "xs";
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg border border-line bg-slate-50 p-0.5">
      {options.map((o) => (
        <button key={o.value} role="radio" aria-checked={value === o.value} onClick={() => onChange(o.value)}
          className={cx("rounded-md font-medium transition-colors", size === "sm" ? "px-2.5 py-1 text-xs" : "px-2 py-0.5 text-[11px]",
            value === o.value ? "bg-white text-ink shadow-sm ring-1 ring-line" : "text-muted hover:text-ink")}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function SelectBox({ label, value, onChange, options, className, dark }: {
  label: string; value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; className?: string; dark?: boolean;
}) {
  return (
    <label className={cx("flex flex-col gap-0.5", className)}>
      <span className={cx("text-[11px] font-medium", dark ? "text-slate-300" : "text-muted")}>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}
        className={cx("h-8 rounded-md border px-2 pr-7 text-sm", dark ? "border-navy-600 bg-navy-800 text-white" : "border-line bg-white text-ink")}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  );
}

export const pct = (v: number | null | undefined, d = 0) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(d)}%`);
export const signed = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}`);
export const fmtDate = (s: string | null | undefined, opts: Intl.DateTimeFormatOptions = { day: "numeric", month: "short" }) =>
  s ? new Date(s + "T00:00:00").toLocaleDateString("en-GB", opts) : "—";

export const FAMILY_COLOR: Record<string, string> = { environmental: "#0e7c74", clinical: "#6d4fc2", occupational: "#2f6fb3" };
