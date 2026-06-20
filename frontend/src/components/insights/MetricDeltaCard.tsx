import { num, type ComparisonMetric } from "../../api";

const DIR = {
  increased: { arrow: "▲", tone: "text-amber-600" },
  decreased: { arrow: "▼", tone: "text-emerald-600" },
  unchanged: { arrow: "→", tone: "text-slate-400" },
  unavailable: { arrow: "–", tone: "text-slate-300" },
} as const;

function fmt(v: string | null, unit: string): string {
  if (v === null || v === "") return "—";
  if (unit === "USD" || unit === "USD/day") {
    const n = num(v);
    return n === null ? "—" : `$${n.toFixed(2)}`;
  }
  const n = num(v);
  if (n === null) return v;
  const digits = unit.includes("/day") ? 1 : 0;
  return `${n.toLocaleString(undefined, { maximumFractionDigits: digits })}`;
}

/** A single headline metric with its month-over-month delta. */
export default function MetricDeltaCard({ m }: { m: ComparisonMetric }) {
  const d = DIR[m.direction];
  const pct = num(m.percent_change);
  const delta =
    m.direction === "unavailable"
      ? "no prior value"
      : m.direction === "unchanged"
      ? "unchanged"
      : pct === null
      ? `${m.direction}`
      : `${m.direction} ${Math.abs(pct).toFixed(1)}%`;
  return (
    <div className={`rounded-lg border p-3 ${m.normalized ? "border-sky-200 bg-sky-50/40" : "border-slate-200 bg-white"}`}>
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{m.label}</span>
        {m.normalized && (
          <span className="rounded bg-sky-100 px-1.5 py-0.5 text-[10px] font-medium text-sky-700">per day</span>
        )}
      </div>
      <div className="mt-1 text-xl font-semibold text-slate-800">{fmt(m.current_value, m.unit)}</div>
      <div className={`mt-0.5 flex items-center gap-1 text-xs font-medium ${d.tone}`}>
        <span aria-hidden>{d.arrow}</span>
        <span>{delta}</span>
      </div>
    </div>
  );
}
