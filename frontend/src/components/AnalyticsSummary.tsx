import { useEffect, useState } from "react";
import { api, money, num, rate, shortDate, type AnalyticsSummary, type MoM } from "../api";
import { Card, Stat } from "./ui";

function MoMChip({ m, kind }: { m: MoM | null; kind: "money" | "number" }) {
  if (!m) return <span className="text-slate-400">—</span>;
  const abs = num(m.absolute) ?? 0;
  const up = abs > 0;
  const color = abs === 0 ? "text-slate-500" : up ? "text-rose-600" : "text-emerald-600";
  const arrow = abs === 0 ? "→" : up ? "▲" : "▼";
  const val = kind === "money" ? money(m.absolute) : Math.abs(abs).toLocaleString();
  const pctTxt = m.percent === null ? "" : ` (${m.percent > 0 ? "+" : ""}${m.percent}%)`;
  return (
    <span className={color}>
      {arrow} {kind === "money" && abs !== 0 ? (up ? "+" : "-") : ""}
      {kind === "money" ? val.replace("-", "") : val}
      {pctTxt}
    </span>
  );
}

export default function AnalyticsSummaryCard({ version }: { version: number }) {
  const [data, setData] = useState<AnalyticsSummary | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .analyticsSummary()
      .then((d) => alive && setData(d))
      .catch(() => alive && setData(null));
    return () => {
      alive = false;
    };
  }, [version]);

  if (!data || data.total_bills === 0) return null;
  const mom = data.month_over_month;

  return (
    <Card title="Account analytics">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        <Stat label="Bills tracked" value={data.total_bills} />
        <Stat label="Latest bill" value={money(data.latest_bill_amount)} sub={shortDate(data.latest?.bill_date ?? null)} />
        <Stat label="Latest electric" value={data.latest_electric_usage_kwh?.toLocaleString() ?? "—"} sub="kWh" />
        <Stat label="Latest gas" value={data.latest_gas_usage_ccf?.toLocaleString() ?? "—"} sub="CCF" />
        <Stat label="MoM total bill" value={<MoMChip m={mom.total_bill} kind="money" />} />
        <Stat label="MoM electric" value={<MoMChip m={mom.electric_kwh} kind="number" />} sub="kWh" />
        <Stat label="MoM gas" value={<MoMChip m={mom.gas_ccf} kind="number" />} sub="CCF" />
        <Stat label="Avg electric rate" value={rate(data.average_electric_effective_rate)} sub="$/kWh" />
        <Stat label="Avg gas rate" value={rate(data.average_gas_effective_rate, 4)} sub="$/CCF" />
        <Stat
          label="Highest bill"
          value={money(data.highest_total_bill?.total_amount_due ?? null)}
          sub={shortDate(data.highest_total_bill?.bill_date ?? null)}
        />
        <Stat
          label="Peak electric"
          value={data.highest_electric_usage_bill?.kwh_used?.toLocaleString() ?? "—"}
          sub={`kWh · ${shortDate(data.highest_electric_usage_bill?.bill_date ?? null)}`}
        />
        <Stat
          label="Peak gas"
          value={data.highest_gas_usage_bill?.ccf_used?.toLocaleString() ?? "—"}
          sub={`CCF · ${shortDate(data.highest_gas_usage_bill?.bill_date ?? null)}`}
        />
      </div>
    </Card>
  );
}
