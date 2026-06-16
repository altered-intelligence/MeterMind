import { useState } from "react";
import { type Bill } from "../../api";
import UploadBill from "../UploadBill";
import BulkUploadBills from "../BulkUploadBills";
import SmartHubPlaceholder from "../SmartHubPlaceholder";
import { Card, ConfidenceBadge } from "../ui";

function severityClass(sev: string | null): string {
  if (sev === "error") return "text-rose-600";
  if (sev === "warning") return "text-amber-600";
  return "text-slate-500";
}

function ResultPanel({ bill }: { bill: Bill }) {
  return (
    <Card title="Parsed result" right={<ConfidenceBadge level={bill.parser_confidence} />}>
      <p className="mb-3 text-sm text-emerald-700">
        ✓ Parsed and stored as bill #{bill.id} ({bill.provider_name}).
      </p>

      {bill.warnings.length > 0 ? (
        <div className="mb-3">
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Validation warnings ({bill.warnings.length})
          </h3>
          <ul className="space-y-1">
            {bill.warnings.map((w, i) => (
              <li key={i} className="text-xs">
                <span className={severityClass(w.severity)}>[{w.severity}]</span>{" "}
                <span className="text-slate-600">{w.message}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="mb-3 text-xs text-slate-400">No validation warnings.</p>
      )}

      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
        Redacted parsed JSON
      </h3>
      <pre className="max-h-96 overflow-auto rounded-lg bg-slate-900 p-3 text-xs leading-relaxed text-slate-100">
        {JSON.stringify(bill, null, 2)}
      </pre>
      <p className="mt-2 text-xs text-slate-400">
        Full account/meter numbers are never included — only last-4 and a redacted address.
      </p>
    </Card>
  );
}

export default function UploadView({
  onUploaded,
  onBulkComplete,
  onOpenBill,
}: {
  onUploaded: (b: Bill) => void;
  onBulkComplete?: () => void;
  onOpenBill?: (id: number) => void;
}) {
  const [result, setResult] = useState<Bill | null>(null);

  function handle(b: Bill) {
    setResult(b);
    onUploaded(b);
  }

  return (
    <div className="space-y-6">
      <UploadBill onUploaded={handle} />
      {result && <ResultPanel bill={result} />}
      <BulkUploadBills onComplete={onBulkComplete} onOpenBill={onOpenBill} />
      <SmartHubPlaceholder />
    </div>
  );
}
