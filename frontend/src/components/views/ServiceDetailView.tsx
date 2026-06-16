import { money, num, rate, shortDate, type Bill, type ElectricService, type GasService } from "../../api";
import ChargesBreakdown from "../ChargesBreakdown";
import { Card, EmptyState, Metric, Row } from "../ui";

export default function ServiceDetailView({
  kind,
  bill,
}: {
  kind: "electric" | "gas";
  bill: Bill | null;
}) {
  const isElec = kind === "electric";
  const title = isElec ? "Electric service" : "Gas service";

  if (!bill) {
    return <EmptyState title="No bill selected." hint="Upload a bill to see service detail." />;
  }
  const svc = isElec ? bill.electric_service : bill.gas_service;
  if (!svc) {
    return (
      <Card title={title}>
        <p className="text-sm text-slate-400">No {kind} service was found on this bill.</p>
      </Card>
    );
  }

  const d = bill.derived;
  const unit = isElec ? "kWh" : "CCF";
  const usage = isElec ? (svc as ElectricService).kwh_used : (svc as GasService).ccf_used;
  const avgPerDay = isElec ? d.electric_avg_kwh_per_day : d.gas_avg_ccf_per_day;
  const effRate = isElec ? d.effective_electric_rate : d.effective_gas_rate;
  const avgCost = isElec ? d.electric_avg_cost_per_day : d.gas_avg_cost_per_day;

  return (
    <div className="space-y-6">
      <Card title={`${title} — ${shortDate(bill.bill_date)}`}>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <Metric label={`${unit} used`} value={usage?.toLocaleString() ?? "—"} unit={unit} />
          <Metric label="Billing days" value={svc.billing_days ?? "—"} />
          <Metric
            label={`Avg ${unit} / day`}
            value={num(avgPerDay)?.toLocaleString() ?? "—"}
            tip={`${unit} used ÷ billing days`}
          />
          <Metric label="Total charges" value={money(svc.total_current_charges)} />
          <Metric
            label="Effective rate"
            value={rate(effRate)}
            unit={`/${unit}`}
            tip={`Total ${kind} charges ÷ ${unit} used`}
          />
          <Metric
            label="Avg cost / day"
            value={money(avgCost)}
            tip={`${title} charges ÷ billing days`}
          />
        </div>
      </Card>

      <Card title="Meter read">
        <div className="grid gap-x-8 sm:grid-cols-2">
          <div>
            <Row label="Service rate" value={svc.service_rate ?? "—"} />
            <Row label="Meter #" value={svc.meter_number_last4 ? `••••${svc.meter_number_last4}` : "—"} />
            <Row label="Multiplier" value={num(svc.multiplier) ?? "—"} />
          </div>
          <div>
            <Row label="Read start" value={shortDate(svc.service_start_date)} />
            <Row label="Read end" value={shortDate(svc.service_end_date)} />
            <Row
              label="Readings (start → end)"
              value={`${svc.last_read?.toLocaleString() ?? "—"} → ${svc.current_read?.toLocaleString() ?? "—"}`}
            />
          </div>
        </div>
      </Card>

      <ChargesBreakdown title={`${title} line items`} items={svc.line_items} total={svc.total_current_charges} />
    </div>
  );
}
