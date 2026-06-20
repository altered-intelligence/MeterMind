import { useEffect, useState } from "react";
import { api, money, num, type BillForecast, type ForecastConfidence } from "../api";
import { Card, EmptyState, InfoTip, Metric } from "./ui";

const CONFIDENCE: Record<ForecastConfidence, { label: string; tone: string }> = {
  low: { label: "Low", tone: "bg-rose-100 text-rose-700 ring-rose-200" },
  medium_low: { label: "Medium-low", tone: "bg-amber-100 text-amber-700 ring-amber-200" },
  medium: { label: "Medium", tone: "bg-sky-100 text-sky-700 ring-sky-200" },
  high: { label: "High", tone: "bg-emerald-100 text-emerald-700 ring-emerald-200" },
};

const TIP =
  "A projection from your past bills — not a live-usage estimate. It scales your recent " +
  "average cost and usage per day to the expected number of billing days.";

function ConfidenceBadge({ level }: { level: ForecastConfidence }) {
  const c = CONFIDENCE[level];
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ${c.tone}`}>
      {c.label} confidence
    </span>
  );
}

function usageStr(v: string | null, unit: string): string {
  const n = num(v);
  if (n === null) return "—";
  return `${n.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${unit}`;
}

export default function ForecastPanel({
  version,
  onGoUpload,
  preloaded,
}: {
  version: number;
  onGoUpload?: () => void;
  /** When provided (e.g. by the Insights page), use this for the initial render. */
  preloaded?: BillForecast | null;
}) {
  const [data, setData] = useState<BillForecast | null>(preloaded ?? null);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState<string>("");

  function load(expected?: number) {
    setError(null);
    (expected ? api.forecast(expected) : api.forecastNextBill())
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load forecast."));
  }

  useEffect(() => {
    // Use the preloaded forecast when supplied; recalculating with custom days still fetches.
    if (preloaded !== undefined) {
      setData(preloaded);
      setError(preloaded === null ? "Forecast is unavailable." : null);
      return;
    }
    setData(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version, preloaded]);

  if (error) {
    return (
      <Card title="Forecast">
        <p className="text-sm text-rose-600">{error}</p>
      </Card>
    );
  }
  if (!data) {
    return (
      <Card title="Forecast">
        <p className="text-sm text-slate-400">Loading…</p>
      </Card>
    );
  }

  if (data.status === "insufficient_history") {
    return (
      <Card title="Forecast">
        <EmptyState
          title="Not enough history to forecast yet."
          hint={
            onGoUpload ? (
              <button onClick={onGoUpload} className="font-medium text-sky-600 hover:text-sky-800">
                Upload a CoServ bill to start projecting →
              </button>
            ) : (
              "Upload at least one bill to start projecting."
            )
          }
        />
      </Card>
    );
  }

  return (
    <Card
      title="Forecast — next bill"
      right={<ConfidenceBadge level={data.confidence} />}
    >
      <p className="mb-3 text-xs text-slate-500">
        {data.explanation} <InfoTip text={TIP} />
      </p>

      {/* Headline projection */}
      <div className="mb-4 rounded-lg bg-slate-50 p-4">
        <div className="text-xs uppercase tracking-wide text-slate-400">Projected total</div>
        <div className="text-3xl font-bold text-slate-900">{money(data.projected_total_bill)}</div>
        <div className="mt-0.5 text-xs text-slate-500">
          for a {data.expected_billing_days}-day cycle · {money(data.projected_average_cost_per_day)}/day ·
          from {data.rolling_window} recent bill(s)
        </div>
      </div>

      {/* Electric / gas / usage */}
      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Metric label="Electric (proj.)" value={money(data.projected_electric_total)} />
        <Metric label="Gas (proj.)" value={money(data.projected_gas_total)} />
        <Metric label="Electric usage" value={usageStr(data.projected_kwh, "kWh")} />
        <Metric label="Gas usage" value={usageStr(data.projected_ccf, "CCF")} />
      </div>

      {/* Expected-days override */}
      <div className="mb-4 flex flex-wrap items-end gap-2">
        <label className="text-xs text-slate-500">
          Expected billing days
          <input
            value={days}
            onChange={(e) => setDays(e.target.value)}
            inputMode="numeric"
            placeholder={String(data.expected_billing_days)}
            className="mt-1 w-28 rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-sky-300"
          />
        </label>
        <button
          onClick={() => {
            const n = Number(days);
            load(Number.isFinite(n) && n > 0 ? n : undefined);
          }}
          className="rounded-md bg-sky-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-700 focus:outline-none focus:ring-2 focus:ring-sky-300"
        >
          Recalculate
        </button>
        {days && (
          <button
            onClick={() => {
              setDays("");
              load();
            }}
            className="rounded-md px-2 py-1.5 text-xs text-slate-500 hover:text-slate-700"
          >
            Reset
          </button>
        )}
      </div>

      {data.weather_context && (
        <p className="mb-3 rounded-md bg-sky-50/50 px-3 py-2 text-xs text-slate-600 ring-1 ring-sky-100">
          {data.weather_context}
        </p>
      )}

      {/* Assumptions */}
      <div className="mb-3">
        <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Assumptions</h3>
        <ul className="list-disc space-y-0.5 pl-5">
          {data.assumptions.map((a, i) => (
            <li key={i} className="text-xs text-slate-500">
              {a}
            </li>
          ))}
        </ul>
      </div>

      {data.warnings.length > 0 && (
        <div className="rounded-md bg-amber-50 px-3 py-2 ring-1 ring-amber-200">
          <ul className="list-disc space-y-0.5 pl-5">
            {data.warnings.map((w, i) => (
              <li key={i} className="text-xs text-amber-800">
                {w}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}
