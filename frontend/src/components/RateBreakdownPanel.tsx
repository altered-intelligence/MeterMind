import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  api,
  money,
  monthLabel,
  num,
  pct,
  rate,
  type RateBreakdown,
  type RateTrendPoint,
  type ServiceRateBreakdown,
} from "../api";
import { Card, InfoTip } from "./ui";

const SKY = "#0284c7";
const AMBER = "#d97706";
const SLATE = "#475569";

// Stable color per category key (shared across cards + chart).
const CATEGORY_COLOR: Record<string, string> = {
  fixed_charges: "#0ea5e9",
  variable_energy_charges: "#6366f1",
  variable_usage_charges: "#6366f1",
  purchased_gas_charges: "#8b5cf6",
  credits_and_adjustments: "#10b981",
  adjustments: "#10b981",
  local_fees: "#f59e0b",
  taxes: "#ef4444",
  optional_or_rounding: "#94a3b8",
  uncategorized: "#fb7185",
};

const axis = { fontSize: 11, stroke: "#94a3b8" } as const;

const TIP = {
  trueRate:
    "True effective rate includes ALL charges and fees on the service — fixed customer charge, energy/usage, adjustments, fees, and taxes — divided by usage.",
  energyOnly:
    "Energy-only rate includes only the raw energy charge divided by kWh. It ignores the fixed charge, fees, and taxes, so it is always lower than the true effective rate.",
  commodity:
    "Gas commodity rate is the Purchased Gas Factor (the wholesale cost of the gas itself) divided by CCF. It excludes the fixed charge, fees, and taxes.",
};

function fracPct(v: string | null | undefined): string {
  return v == null ? "—" : pct(v); // backend sends a fraction like "0.42" -> "42.0%"
}

function ReconcileBadge({ svc }: { svc: ServiceRateBreakdown }) {
  const issue = !svc.reconciles || svc.uncategorized_labels.length > 0;
  if (!issue) {
    return (
      <span className="inline-flex items-center rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700 ring-1 ring-emerald-200">
        ✓ reconciles
      </span>
    );
  }
  const label = !svc.reconciles
    ? "does not reconcile"
    : `${svc.uncategorized_labels.length} uncategorized`;
  return (
    <span className="inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700 ring-1 ring-amber-200">
      ▲ {label}
    </span>
  );
}

function RateHeadline({ svc }: { svc: ServiceRateBreakdown }) {
  const m = svc.metrics;
  const isElectric = svc.service_type === "electric";
  const trueRate = isElectric ? m.true_effective_rate_per_kwh : m.true_effective_rate_per_ccf;
  const rawRate = isElectric ? m.energy_only_rate_per_kwh : m.purchased_gas_rate_per_ccf;
  const unit = isElectric ? "kWh" : "CCF";
  const rawLabel = isElectric ? "Energy-only rate" : "Gas commodity rate";
  const rawTip = isElectric ? TIP.energyOnly : TIP.commodity;
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <div className="rounded-lg bg-slate-50 p-2">
        <div className="text-xs text-slate-500">
          True effective rate
          <InfoTip text={TIP.trueRate} />
        </div>
        <div className="text-base font-semibold text-slate-800">
          {rate(trueRate)} <span className="text-xs font-normal text-slate-400">/{unit}</span>
        </div>
      </div>
      <div className="rounded-lg bg-slate-50 p-2">
        <div className="text-xs text-slate-500">
          {rawLabel}
          <InfoTip text={rawTip} />
        </div>
        <div className="text-base font-semibold text-slate-800">
          {rate(rawRate)} <span className="text-xs font-normal text-slate-400">/{unit}</span>
        </div>
      </div>
      <div className="rounded-lg bg-slate-50 p-2">
        <div className="text-xs text-slate-500">Fixed cost share</div>
        <div className="text-base font-semibold text-slate-800">{fracPct(m.fixed_cost_percentage)}</div>
      </div>
      <div className="rounded-lg bg-slate-50 p-2">
        <div className="text-xs text-slate-500">Taxes &amp; fees share</div>
        <div className="text-base font-semibold text-slate-800">{fracPct(m.tax_fee_percentage)}</div>
      </div>
    </div>
  );
}

function CategoryCards({ svc }: { svc: ServiceRateBreakdown }) {
  // Skip the presentational `total` bucket; show every real category (incl. zero/credit).
  const cats = svc.categories.filter((c) => c.key !== "total");
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      {cats.map((c) => {
        const negative = (num(c.amount) ?? 0) < 0;
        return (
          <div key={c.key} className="rounded-lg border border-slate-100 p-2">
            <div className="flex items-center gap-1.5">
              <span
                className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm"
                style={{ background: CATEGORY_COLOR[c.key] ?? SLATE }}
              />
              <span className="truncate text-xs text-slate-600">{c.label}</span>
            </div>
            <div className={`mt-0.5 text-sm font-semibold ${negative ? "text-emerald-600" : "text-slate-800"}`}>
              {money(c.amount)}
            </div>
            <div className="text-[11px] text-slate-400">{fracPct(c.percentage_of_service_total)} of total</div>
          </div>
        );
      })}
    </div>
  );
}

function CategoryChart({ svc }: { svc: ServiceRateBreakdown }) {
  const data = svc.categories
    .filter((c) => c.key !== "total")
    .map((c) => ({ key: c.key, label: c.label, amount: num(c.amount) ?? 0 }));
  return (
    <div className="rounded-lg border border-slate-100 p-3">
      <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
        Category composition
      </h4>
      <div style={{ width: "100%", height: 200 }}>
        <ResponsiveContainer>
          <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
            <XAxis type="number" tick={axis} tickFormatter={(v) => `$${v}`} />
            <YAxis type="category" dataKey="label" tick={{ fontSize: 10, fill: "#64748b" }} width={120} />
            <Tooltip formatter={(v: number) => money(String(v))} />
            <Bar dataKey="amount" radius={[0, 3, 3, 0]}>
              {data.map((d) => (
                <Cell key={d.key} fill={CATEGORY_COLOR[d.key] ?? SLATE} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function MappingTable({ svc }: { svc: ServiceRateBreakdown }) {
  const rows = svc.categories
    .filter((c) => c.key !== "total" && c.line_item_names.length > 0)
    .flatMap((c) => c.line_item_names.map((name) => ({ name, category: c.label, key: c.key })));
  if (rows.length === 0) return null;
  return (
    <div>
      <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
        Line-item mapping
      </h4>
      <div className="overflow-hidden rounded-lg border border-slate-100">
        <table className="w-full text-xs">
          <thead className="bg-slate-50 text-slate-500">
            <tr>
              <th className="px-2 py-1 text-left font-medium">Line item</th>
              <th className="px-2 py-1 text-left font-medium">Category</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.name}-${i}`} className="border-t border-slate-50">
                <td className="px-2 py-1 text-slate-700">{r.name}</td>
                <td className="px-2 py-1">
                  <span className="inline-flex items-center gap-1.5">
                    <span
                      className="inline-block h-2 w-2 rounded-sm"
                      style={{ background: CATEGORY_COLOR[r.key] ?? SLATE }}
                    />
                    {r.category}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ServiceSection({ svc }: { svc: ServiceRateBreakdown }) {
  const title = svc.service_type === "electric" ? "Electric" : "Gas";
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-700">
          {title}
          <span className="ml-2 text-xs font-normal text-slate-400">
            {money(svc.total_current_charges)} total
          </span>
        </h3>
        <ReconcileBadge svc={svc} />
      </div>
      <RateHeadline svc={svc} />
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <CategoryCards svc={svc} />
        <CategoryChart svc={svc} />
      </div>
      <MappingTable svc={svc} />
      {svc.warnings.length > 0 && (
        <ul className="space-y-0.5 rounded-lg bg-amber-50 p-2 text-xs text-amber-700">
          {svc.warnings.map((w, i) => (
            <li key={i}>▲ {w}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TrendChart({
  title,
  data,
  primaryKey,
  secondaryKey,
  primaryName,
  secondaryName,
  color,
}: {
  title: string;
  data: any[];
  primaryKey: string;
  secondaryKey: string;
  primaryName: string;
  secondaryName: string;
  color: string;
}) {
  const hasData = data.some((d) => d[primaryKey] != null);
  if (!hasData) return null;
  return (
    <div className="rounded-lg border border-slate-100 p-3">
      <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h4>
      <div style={{ width: "100%", height: 180 }}>
        <ResponsiveContainer>
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
            <XAxis dataKey="label" tick={axis} />
            <YAxis tick={axis} width={52} domain={["auto", "auto"]} tickFormatter={(v) => `$${v}`} />
            <Tooltip formatter={(v: number) => rate(String(v))} />
            <Line type="monotone" dataKey={primaryKey} name={primaryName} stroke={color} strokeWidth={2} dot />
            <Line
              type="monotone"
              dataKey={secondaryKey}
              name={secondaryName}
              stroke={SLATE}
              strokeWidth={1.5}
              strokeDasharray="4 3"
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-1 text-[11px] text-slate-400">
        Solid: true effective rate (all charges). Dashed: {secondaryName.toLowerCase()}.
      </p>
    </div>
  );
}

export default function RateBreakdownPanel({
  billId,
  version,
  preloaded,
}: {
  billId: number;
  version: number;
  /** When provided (e.g. by the Insights page), use this for the breakdown instead of
   * fetching it (the trend chart is still fetched, since it needs all bills' history). */
  preloaded?: RateBreakdown | null;
}) {
  const [data, setData] = useState<RateBreakdown | null>(preloaded ?? null);
  const [trends, setTrends] = useState<RateTrendPoint[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    if (preloaded !== undefined) {
      setData(preloaded);
      setError(preloaded === null ? "Rate breakdown is unavailable." : null);
    } else {
      setData(null);
      setError(null);
      api
        .rateBreakdown(billId)
        .then((d) => alive && setData(d))
        .catch((e) => alive && setError(e instanceof Error ? e.message : "Could not load rate breakdown."));
    }
    api
      .rateBreakdownTrends()
      .then((t) => alive && setTrends(t))
      .catch(() => alive && setTrends([]));
    return () => {
      alive = false;
    };
  }, [billId, version, preloaded]);

  const trendData = useMemo(
    () =>
      trends.map((p) => ({
        label: monthLabel(p.bill_date),
        elecTrue: num(p.electric_effective_rate_per_kwh),
        elecEnergy: num(p.electric_energy_only_rate_per_kwh),
        gasTrue: num(p.gas_effective_rate_per_ccf),
        gasCommodity: num(p.gas_commodity_rate_per_ccf),
      })),
    [trends]
  );

  if (error) {
    return (
      <Card title="Rate & fee breakdown">
        <p className="text-sm text-rose-600">{error}</p>
      </Card>
    );
  }
  if (!data) {
    return (
      <Card title="Rate & fee breakdown">
        <p className="text-sm text-slate-400">Loading…</p>
      </Card>
    );
  }

  return (
    <Card title="Rate & fee breakdown">
      <p className="mb-3 text-xs text-slate-500">
        Where each service dollar goes — fixed vs. variable usage vs. adjustments, fees, and taxes —
        and how the true effective rate (all charges) compares to the raw energy/commodity rate.
      </p>
      <div className="space-y-6">
        {data.electric && <ServiceSection svc={data.electric} />}
        {data.gas && <ServiceSection svc={data.gas} />}
        {trendData.length > 1 && (
          <div className="space-y-2">
            <h3 className="text-sm font-semibold text-slate-700">Effective rate over time</h3>
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              <TrendChart
                title="Electric ($/kWh)"
                data={trendData}
                primaryKey="elecTrue"
                secondaryKey="elecEnergy"
                primaryName="True effective"
                secondaryName="Energy-only"
                color={SKY}
              />
              <TrendChart
                title="Gas ($/CCF)"
                data={trendData}
                primaryKey="gasTrue"
                secondaryKey="gasCommodity"
                primaryName="True effective"
                secondaryName="Commodity-only"
                color={AMBER}
              />
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}
