import { useEffect, useState } from "react";
import {
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  api,
  num,
  type BillWeather,
  type WeatherNormalizedAnalytics,
} from "../api";
import { Card, InfoTip, Metric } from "./ui";

function f1(v: number | null | undefined, unit = ""): string {
  if (v === null || v === undefined) return "—";
  return `${v.toLocaleString(undefined, { maximumFractionDigits: 1 })}${unit}`;
}

const axis = { fontSize: 11, fill: "#94a3b8" };

function ScatterCard({
  title,
  xLabel,
  yLabel,
  data,
}: {
  title: string;
  xLabel: string;
  yLabel: string;
  data: { x: number; y: number; current: boolean }[];
}) {
  if (data.length === 0) {
    return (
      <div className="rounded-lg border border-slate-100 p-3">
        <h4 className="mb-1 text-xs font-semibold text-slate-600">{title}</h4>
        <p className="text-xs text-slate-400">Not enough synced bills to plot yet.</p>
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-slate-100 p-3">
      <h4 className="mb-1 text-xs font-semibold text-slate-600">{title}</h4>
      <div className="h-48">
        <ResponsiveContainer>
          <ScatterChart margin={{ top: 8, right: 12, bottom: 18, left: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
            <XAxis
              type="number"
              dataKey="x"
              name={xLabel}
              tick={axis}
              label={{ value: xLabel, position: "insideBottom", offset: -8, fontSize: 11, fill: "#94a3b8" }}
            />
            <YAxis type="number" dataKey="y" name={yLabel} tick={axis} width={36} />
            <Tooltip
              cursor={{ strokeDasharray: "3 3" }}
              formatter={(v: number, name: string) => [f1(v), name === "y" ? yLabel : xLabel]}
            />
            <Scatter data={data.filter((d) => !d.current)} fill="#cbd5e1" />
            <Scatter data={data.filter((d) => d.current)} fill="#0284c7" />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-1 text-[11px] text-slate-400">
        {xLabel} (x) vs {yLabel} (y). This bill is highlighted in blue.
      </p>
    </div>
  );
}

export default function WeatherContextPanel({
  billId,
  version,
  onGoSettings,
}: {
  billId: number;
  version: number;
  onGoSettings?: () => void;
}) {
  const [data, setData] = useState<BillWeather | null>(null);
  const [series, setSeries] = useState<WeatherNormalizedAnalytics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function load() {
    api
      .getBillWeather(billId)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load weather."));
    api.weatherNormalized().then(setSeries).catch(() => setSeries(null));
  }

  useEffect(() => {
    setData(null);
    setError(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [billId, version]);

  async function sync() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.syncBillWeather(billId);
      setData(res);
      const s = await api.weatherNormalized();
      setSeries(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Weather sync failed.");
    } finally {
      setBusy(false);
    }
  }

  const syncButton = (
    <button
      onClick={sync}
      disabled={busy}
      className="rounded-md bg-sky-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-700 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-sky-300"
    >
      {busy ? "Syncing…" : data?.summary ? "Re-sync weather" : "Sync weather"}
    </button>
  );

  if (error) {
    return (
      <Card title="Weather context" right={syncButton}>
        <p className="text-sm text-rose-600">{error}</p>
      </Card>
    );
  }
  if (!data) {
    return (
      <Card title="Weather context">
        <p className="text-sm text-slate-400">Loading…</p>
      </Card>
    );
  }

  const s = data.summary;
  const n = data.normalization;

  if (!s || n.status === "unavailable") {
    return (
      <Card title="Weather context" right={syncButton}>
        <p className="text-sm text-slate-500">
          No weather for this bill yet.{" "}
          {onGoSettings ? (
            <button onClick={onGoSettings} className="font-medium text-sky-600 hover:text-sky-800">
              Set a weather location
            </button>
          ) : (
            "Configure a weather location"
          )}
          , then sync to see how this period's temperatures line up with usage.
        </p>
        {n.message && <p className="mt-1 text-xs text-slate-400">{n.message}</p>}
      </Card>
    );
  }

  // Build scatter datasets (only points with both axes present).
  const elec = (series?.points ?? [])
    .map((p) => ({
      x: p.cooling_degree_days,
      y: num(p.kwh_per_day),
      current: p.bill_id === billId,
    }))
    .filter((d): d is { x: number; y: number; current: boolean } => d.x != null && d.y != null);
  const gas = (series?.points ?? [])
    .map((p) => ({
      x: p.heating_degree_days,
      y: num(p.ccf_per_day),
      current: p.bill_id === billId,
    }))
    .filter((d): d is { x: number; y: number; current: boolean } => d.x != null && d.y != null);

  return (
    <Card
      title="Weather context"
      right={syncButton}
    >
      <p className="mb-3 text-xs text-slate-500">
        How hot/cold this billing period was, and where usage sits relative to the weather.
        Context only — not a cause-and-effect claim.{" "}
        <InfoTip text="Degree-days measure how far the average temperature was from 65°F: cooling degree-days (above 65) track A/C demand, heating degree-days (below 65) track heating demand." />
      </p>

      {/* Period summary */}
      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Metric label="Avg temp" value={f1(s.avg_temp_f)} unit="°F" sub={`${s.days_observed} days`} />
        <Metric label="Avg min / max" value={`${f1(s.avg_min_temp_f)} / ${f1(s.avg_max_temp_f)}`} unit="°F" />
        <Metric label="Precipitation" value={f1(s.total_precipitation_inches)} unit="in" />
        <Metric
          label="Cooling degree-days"
          value={f1(s.total_cooling_degree_days_65)}
          tip="Sum over the period of max(0, avg °F − 65). Higher = hotter."
        />
        <Metric
          label="Heating degree-days"
          value={f1(s.total_heating_degree_days_65)}
          tip="Sum over the period of max(0, 65 − avg °F). Higher = colder."
        />
        <Metric label="Days > 95°F" value={s.days_over_95f} />
        <Metric label="Days > 100°F" value={s.days_over_100f} />
        <Metric label="Days < 32°F" value={s.days_below_32f} />
      </div>

      {/* Usage vs degree-days context (non-causal) */}
      <div className="mb-4 grid gap-2 sm:grid-cols-2">
        <div className="rounded-lg bg-sky-50/50 px-3 py-2 ring-1 ring-sky-100">
          <div className="text-[11px] font-medium uppercase tracking-wide text-sky-700">
            Electric vs cooling
          </div>
          <p className="mt-1 text-sm text-slate-700">{n.electric_usage_vs_cdd_context}</p>
          {n.kwh_per_cooling_degree_day && (
            <p className="mt-0.5 text-xs text-slate-500">
              {n.kwh_per_cooling_degree_day} kWh per cooling degree-day
            </p>
          )}
        </div>
        <div className="rounded-lg bg-amber-50/50 px-3 py-2 ring-1 ring-amber-100">
          <div className="text-[11px] font-medium uppercase tracking-wide text-amber-700">
            Gas vs heating
          </div>
          <p className="mt-1 text-sm text-slate-700">{n.gas_usage_vs_hdd_context}</p>
          {n.gas_ccf_per_heating_degree_day && (
            <p className="mt-0.5 text-xs text-slate-500">
              {n.gas_ccf_per_heating_degree_day} CCF per heating degree-day
            </p>
          )}
        </div>
      </div>

      {/* Scatter charts */}
      <div className="grid gap-3 sm:grid-cols-2">
        <ScatterCard
          title="Electric: kWh/day vs cooling degree-days"
          xLabel="Cooling degree-days"
          yLabel="kWh/day"
          data={elec}
        />
        <ScatterCard
          title="Gas: CCF/day vs heating degree-days"
          xLabel="Heating degree-days"
          yLabel="CCF/day"
          data={gas}
        />
      </div>
    </Card>
  );
}
