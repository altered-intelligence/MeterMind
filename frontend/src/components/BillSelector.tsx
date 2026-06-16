import { money, shortDate, type Bill } from "../api";

export default function BillSelector({
  bills,
  selectedId,
  onSelect,
}: {
  bills: Bill[];
  selectedId: number | null;
  onSelect: (id: number) => void;
}) {
  if (bills.length === 0) return null;
  const latestId = bills[bills.length - 1]?.id;
  return (
    <label className="flex items-center gap-2 text-sm text-slate-500">
      <span className="hidden sm:inline">Bill</span>
      <select
        value={selectedId ?? latestId}
        onChange={(e) => onSelect(Number(e.target.value))}
        className="rounded-md border border-slate-300 bg-white px-2 py-1 text-sm text-slate-800"
      >
        {[...bills]
          .slice()
          .reverse()
          .map((b) => (
            <option key={b.id} value={b.id}>
              {shortDate(b.account_summary.bill_date)} · {money(b.account_summary.total_amount_due)}
              {b.id === latestId ? "  (latest)" : ""}
            </option>
          ))}
      </select>
    </label>
  );
}
