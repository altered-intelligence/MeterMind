import { money, pct, shortDate, type Bill } from "../api";
import { Card, ConfidenceBadge, InfoTip, Row, Stat } from "./ui";

export default function BillSummaryCard({ bill, isLatest }: { bill: Bill; isLatest?: boolean }) {
  const s = bill.account_summary;
  const d = bill.derived;

  return (
    <Card
      title={
        <span>
          Bill — {shortDate(s.bill_date)}
          {isLatest && <span className="ml-2 text-xs font-normal normal-case text-sky-600">latest</span>}
        </span>
      }
      right={<ConfidenceBadge level={bill.parser_confidence} />}
    >
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Stat label="Total amount due" value={money(s.total_amount_due)} sub={`due ${shortDate(s.due_date)}`} />
        <Stat label="Electric service" value={money(s.electric_service_total)} sub={pct(d.electric_percentage_of_bill) + " of bill"} />
        <Stat label="Gas service" value={money(s.gas_service_total)} sub={pct(d.gas_percentage_of_bill) + " of bill"} />
        <Stat label="Billing days" value={bill.billing_days ?? "—"} />
        <Stat
          label={
            <>
              Avg cost / day
              <InfoTip text="Total amount due ÷ billing days" />
            </>
          }
          value={money(d.total_avg_cost_per_day)}
        />
      </div>

      <div className="mt-4 grid gap-x-8 gap-y-0 sm:grid-cols-2">
        <div>
          <Row label="Previous balance" value={money(s.previous_balance)} />
          <Row label="Payment received" value={money(s.payment_received)} />
          <Row label="Balance forward" value={money(s.balance_forward)} />
          <Row label="Current charges" value={money(s.current_charges_total)} />
        </div>
        <div>
          <Row label="Amount due after due date" value={money(s.amount_due_after_due_date)} />
          <Row label="Autopay status" value={s.autopay_status ?? "—"} />
          <Row label="Account #" value={s.account_number_redacted ?? "—"} />
          <Row label="Service address" value={s.service_address_redacted ?? "—"} />
        </div>
      </div>

      <p className="mt-3 text-xs text-slate-400">
        Account &amp; meter numbers are hashed; only the last 4 digits and a redacted address are stored.
      </p>
    </Card>
  );
}
