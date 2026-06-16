import type { ReactNode } from "react";
import type { Confidence } from "../api";

export function Card({
  title,
  right,
  children,
  className = "",
}: {
  title?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-slate-200 bg-white shadow-sm ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700">{title}</h2>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function ConfidenceBadge({ level }: { level: Confidence }) {
  const styles: Record<Confidence, string> = {
    high: "bg-emerald-100 text-emerald-700 ring-emerald-200",
    medium: "bg-amber-100 text-amber-700 ring-amber-200",
    low: "bg-rose-100 text-rose-700 ring-rose-200",
  };
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ${styles[level] ?? styles.low}`}>
      {level} confidence
    </span>
  );
}

/** Small ⓘ with a hover/focus tooltip — used to explain calculated fields. */
export function InfoTip({ text }: { text: string }) {
  return (
    <span
      className="group relative ml-1 inline-flex align-middle"
      tabIndex={0}
      role="note"
      aria-label={text}
    >
      <span
        aria-hidden="true"
        className="flex h-3.5 w-3.5 cursor-help items-center justify-center rounded-full border border-slate-300 text-[9px] font-bold text-slate-400"
      >
        i
      </span>
      <span
        role="tooltip"
        className="pointer-events-none invisible absolute bottom-full left-1/2 z-20 mb-1 w-52 -translate-x-1/2 rounded-md bg-slate-800 px-2 py-1 text-[11px] font-normal normal-case leading-snug text-white opacity-0 shadow-lg transition group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100"
      >
        {text}
      </span>
    </span>
  );
}

export function Stat({ label, value, sub }: { label: ReactNode; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="rounded-lg bg-slate-50 px-3 py-2">
      <div className="flex items-center text-xs text-slate-500">{label}</div>
      <div className="text-lg font-semibold text-slate-800">{value}</div>
      {sub && <div className="text-xs text-slate-400">{sub}</div>}
    </div>
  );
}

/** A labelled metric with optional unit + explanatory tooltip (for calculated fields). */
export function Metric({
  label,
  value,
  unit,
  tip,
  sub,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  tip?: string;
  sub?: ReactNode;
}) {
  return (
    <div className="overflow-visible rounded-lg border border-slate-100 bg-slate-50 px-3 py-2">
      <div className="flex items-center text-xs text-slate-500">
        {label}
        {tip && <InfoTip text={tip} />}
      </div>
      <div className="text-lg font-semibold text-slate-800">
        {value}
        {unit && <span className="ml-1 text-sm font-normal text-slate-400">{unit}</span>}
      </div>
      {sub && <div className="text-xs text-slate-400">{sub}</div>}
    </div>
  );
}

export function Row({ label, value }: { label: ReactNode; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-slate-50 py-1.5 last:border-0">
      <span className="text-sm text-slate-500">{label}</span>
      <span className="text-right text-sm font-medium text-slate-800">{value}</span>
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center">
      <p className="text-slate-600">{title}</p>
      {hint && <p className="mt-1 text-sm text-slate-400">{hint}</p>}
    </div>
  );
}
