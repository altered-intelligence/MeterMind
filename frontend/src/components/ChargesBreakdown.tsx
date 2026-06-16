import { money, num, rate as fmtRate, type ChargeLineItem } from "../api";
import { Card } from "./ui";

export default function ChargesBreakdown({
  title,
  items,
  total,
}: {
  title: string;
  items: ChargeLineItem[];
  total: string | null;
}) {
  return (
    <Card title={title}>
      {items.length === 0 ? (
        <p className="text-sm text-slate-400">No line items found.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-slate-400">
                <th className="pb-2 font-medium">Charge</th>
                <th className="pb-2 text-right font-medium">Qty</th>
                <th className="pb-2 text-right font-medium">Rate</th>
                <th className="pb-2 text-right font-medium">Amount</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => {
                const credit = (num(it.amount) ?? 0) < 0;
                const qty = num(it.quantity);
                return (
                  <tr key={it.name} className="border-t border-slate-50">
                    <td className="py-1.5 text-slate-700">{it.name}</td>
                    <td className="py-1.5 text-right text-slate-500">
                      {qty !== null ? `${qty.toLocaleString()} ${it.unit ?? ""}`.trim() : "—"}
                    </td>
                    <td className="py-1.5 text-right text-slate-500">
                      {it.rate ? fmtRate(it.rate) : "—"}
                    </td>
                    <td className={`py-1.5 text-right font-medium ${credit ? "text-emerald-600" : "text-slate-800"}`}>
                      {money(it.amount)}
                      {credit && <span className="ml-1 text-xs">CR</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr className="border-t-2 border-slate-200">
                <td className="pt-2 font-semibold text-slate-700" colSpan={3}>
                  Total current charges
                </td>
                <td className="pt-2 text-right font-bold text-slate-900">{money(total)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </Card>
  );
}
