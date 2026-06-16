import { useMemo, type ReactElement } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { monthLabel, num, type Bill } from "../api";
import { Card } from "./ui";

const SKY = "#0284c7";
const AMBER = "#d97706";
const SLATE = "#475569";
const EMERALD = "#059669";

function ChartFrame({ title, children }: { title: string; children: ReactElement }) {
  return (
    <div className="rounded-lg border border-slate-100 p-3">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
      <div style={{ width: "100%", height: 180 }}>
        <ResponsiveContainer>{children}</ResponsiveContainer>
      </div>
    </div>
  );
}

const axis = { fontSize: 11, stroke: "#94a3b8" } as const;

export default function UsageCharts({ bills }: { bills: Bill[] }) {
  const data = useMemo(
    () =>
      bills.map((b) => {
        const days = b.electric_service?.billing_days ?? b.gas_service?.billing_days ?? null;
        return {
          label: monthLabel(b.account_summary.bill_date),
          kwh: b.electric_service?.kwh_used ?? null,
          ccf: b.gas_service?.ccf_used ?? null,
          // Cost-per-day comes pre-computed (as exact Decimals) from the backend.
          elecPerDay: num(b.derived.electric_avg_cost_per_day),
          gasPerDay: num(b.derived.gas_avg_cost_per_day),
          effElec: num(b.derived.effective_electric_rate),
          effGas: num(b.derived.effective_gas_rate),
          total: num(b.account_summary.total_amount_due),
          days,
        };
      }),
    [bills]
  );

  return (
    <Card title="Trends by billing cycle">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <ChartFrame title="Electric usage (kWh)">
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
            <XAxis dataKey="label" tick={axis} />
            <YAxis tick={axis} width={44} />
            <Tooltip />
            <Bar dataKey="kwh" name="kWh" fill={SKY} radius={[3, 3, 0, 0]} />
          </BarChart>
        </ChartFrame>

        <ChartFrame title="Gas usage (CCF)">
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
            <XAxis dataKey="label" tick={axis} />
            <YAxis tick={axis} width={36} />
            <Tooltip />
            <Bar dataKey="ccf" name="CCF" fill={AMBER} radius={[3, 3, 0, 0]} />
          </BarChart>
        </ChartFrame>

        <ChartFrame title="Electric cost per day ($)">
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
            <XAxis dataKey="label" tick={axis} />
            <YAxis tick={axis} width={44} />
            <Tooltip />
            <Line type="monotone" dataKey="elecPerDay" name="$/day" stroke={SKY} strokeWidth={2} dot />
          </LineChart>
        </ChartFrame>

        <ChartFrame title="Gas cost per day ($)">
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
            <XAxis dataKey="label" tick={axis} />
            <YAxis tick={axis} width={44} />
            <Tooltip />
            <Line type="monotone" dataKey="gasPerDay" name="$/day" stroke={AMBER} strokeWidth={2} dot />
          </LineChart>
        </ChartFrame>

        <ChartFrame title="Effective electric rate ($/kWh)">
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
            <XAxis dataKey="label" tick={axis} />
            <YAxis tick={axis} width={52} domain={["auto", "auto"]} />
            <Tooltip />
            <Line type="monotone" dataKey="effElec" name="$/kWh" stroke={SKY} strokeWidth={2} dot />
          </LineChart>
        </ChartFrame>

        <ChartFrame title="Effective gas rate ($/CCF)">
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
            <XAxis dataKey="label" tick={axis} />
            <YAxis tick={axis} width={52} domain={["auto", "auto"]} />
            <Tooltip />
            <Line type="monotone" dataKey="effGas" name="$/CCF" stroke={AMBER} strokeWidth={2} dot />
          </LineChart>
        </ChartFrame>

        <ChartFrame title="Total bill amount ($)">
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
            <XAxis dataKey="label" tick={axis} />
            <YAxis tick={axis} width={48} />
            <Tooltip />
            <Bar dataKey="total" name="Total $" fill={EMERALD} radius={[3, 3, 0, 0]} />
          </BarChart>
        </ChartFrame>

        <ChartFrame title="Billing-cycle duration (days)">
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
            <XAxis dataKey="label" tick={axis} />
            <YAxis tick={axis} width={36} />
            <Tooltip />
            <Bar dataKey="days" name="Days" fill={SLATE} radius={[3, 3, 0, 0]} />
          </BarChart>
        </ChartFrame>
      </div>
    </Card>
  );
}
