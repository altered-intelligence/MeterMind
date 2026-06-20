import { useEffect, useState } from "react";
import {
  api,
  money,
  num,
  pct,
  rate,
  type BillComparison,
  type ChangeDirection,
  type ComparisonMetric,
} from "../api";
import { Card, EmptyState, InfoTip } from "./ui";

const SECTIONS: { key: "account" | "electric" | "gas"; title: string }[] = [
  { key: "account", title: "Account" },
  { key: "electric", title: "Electric" },
  { key: "gas", title: "Gas" },
];

const DIRECTION: Record<ChangeDirection, { arrow: string; tone: string }> = {
  increased: { arrow: "▲", tone: "text-amber-600" },
  decreased: { arrow: "▼", tone: "text-emerald-600" },
  unchanged: { arrow: "→", tone: "text-slate-400" },
  unavailable: { arrow: "–", tone: "text-slate-300" },
};

/** Format a metric value according to its unit. */
function fmt(value: string | null, unit: string): string {
  if (value === null || value === "") return "—";
  if (unit === "USD" || unit === "USD/day") return money(value);
  if (unit === "USD/kWh" || unit === "USD/CCF") return rate(value, 4);
  if (unit === "fraction") return pct(value);
  // counts / days / per-day usage
  const n = num(value);
  if (n === null) return value;
  const digits = unit.includes("/day") ? 1 : 0;
  return `${n.toLocaleString(undefined, { maximumFractionDigits: digits })}${
    unit && unit !== "days" ? ` ${unit}` : unit === "days" ? " days" : ""
  }`;
}

function deltaLabel(m: ComparisonMetric): string {
  if (m.direction === "unavailable") return "no prior value";
  if (m.direction === "unchanged") return "unchanged";
  if (m.percent_change === null) {
    // No percent (previous was zero) — fall back to the absolute change.
    return `${m.direction} (${fmt(m.absolute_change, m.unit)})`;
  }
  const mag = Math.abs(num(m.percent_change) ?? 0);
  return `${m.direction} ${mag.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
}

function MetricCard({ m }: { m: ComparisonMetric }) {
  const d = DIRECTION[m.direction];
  return (
    <div
      className={`rounded-lg border p-3 ${
        m.normalized ? "border-sky-200 bg-sky-50/40" : "border-slate-200 bg-white"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-medium uppercase tracking-wide text-slate-500">
          {m.label}
        </span>
        {m.normalized && (
          <span className="rounded bg-sky-100 px-1.5 py-0.5 text-[10px] font-medium text-sky-700">
            per day
          </span>
        )}
      </div>
      <div className="mt-1 text-lg font-semibold text-slate-800">{fmt(m.current_value, m.unit)}</div>
      <div className="mt-0.5 text-xs text-slate-400">
        from {fmt(m.previous_value, m.unit)}
      </div>
      <div className={`mt-1 flex items-center gap-1 text-xs font-medium ${d.tone}`}>
        <span aria-hidden>{d.arrow}</span>
        <span>{deltaLabel(m)}</span>
      </div>
    </div>
  );
}

export default function WhatChangedPanel({
  billId,
  version,
  onGoUpload,
  preloaded,
}: {
  billId: number;
  version: number;
  onGoUpload?: () => void;
  /** When provided (e.g. by the Insights page), use this instead of fetching. */
  preloaded?: BillComparison | null;
}) {
  const [data, setData] = useState<BillComparison | null>(preloaded ?? null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (preloaded !== undefined) {
      setData(preloaded);
      setError(preloaded === null ? "Comparison is unavailable." : null);
      return;
    }
    let alive = true;
    setData(null);
    setError(null);
    api
      .billComparison(billId)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(e instanceof Error ? e.message : "Could not load comparison."));
    return () => {
      alive = false;
    };
  }, [billId, version, preloaded]);

  if (error) {
    return (
      <Card title="What changed">
        <p className="text-sm text-rose-600">{error}</p>
      </Card>
    );
  }
  if (!data) {
    return (
      <Card title="What changed">
        <p className="text-sm text-slate-400">Loading…</p>
      </Card>
    );
  }

  if (data.status === "insufficient_history") {
    return (
      <Card title="What changed">
        <EmptyState
          title="Only one bill so far."
          hint={
            onGoUpload ? (
              <button onClick={onGoUpload} className="font-medium text-sky-600 hover:text-sky-800">
                Upload another CoServ bill to compare month over month →
              </button>
            ) : (
              "Upload another bill to compare month over month."
            )
          }
        />
      </Card>
    );
  }

  return (
    <Card
      title="What changed"
      right={
        <InfoTip text="This bill compared to the immediately previous bill for the same account. Usage and cost are normalized by billing days; per-day cards are highlighted." />
      }
    >
      <p className="mb-2 text-sm font-medium text-slate-800">{data.narrative.headline}</p>
      <ul className="mb-3 space-y-0.5">
        {data.narrative.lines.slice(1).map((line, i) => (
          <li key={i} className="text-xs text-slate-500">
            {line}
          </li>
        ))}
      </ul>

      {data.billing_days_changed && (
        <div className="mb-4 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800 ring-1 ring-amber-200">
          Billing period changed ({data.previous_billing_days} → {data.current_billing_days} days).
          Compare the highlighted per-day values rather than raw totals.
        </div>
      )}

      <div className="space-y-4">
        {SECTIONS.map(({ key, title }) => {
          const metrics = data.metrics.filter((m) => m.group === key);
          if (metrics.length === 0) return null;
          return (
            <div key={key}>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                {title}
              </h3>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {metrics.map((m) => (
                  <MetricCard key={m.key} m={m} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
