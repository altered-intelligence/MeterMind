import { useEffect, useMemo, useState } from "react";
import {
  api,
  shortDate,
  type AuditResult,
  type Bill,
  type FieldCorrection,
} from "../api";
import { Card, InfoTip } from "./ui";

// Human labels for the supported scalar field paths.
const FIELD_LABELS: Record<string, string> = {
  "account_summary.bill_date": "Account · Bill date",
  "account_summary.due_date": "Account · Due date",
  "account_summary.total_amount_due": "Account · Total amount due",
  "account_summary.electric_service_total": "Account · Electric service total",
  "account_summary.gas_service_total": "Account · Gas service total",
  "electric.kwh_used": "Electric · kWh used",
  "electric.billing_days": "Electric · Billing days",
  "electric.last_read": "Electric · Last meter read",
  "electric.current_read": "Electric · Current meter read",
  "electric.multiplier": "Electric · Meter multiplier",
  "electric.total_current_charges": "Electric · Total current charges",
  "gas.ccf_used": "Gas · CCF used",
  "gas.billing_days": "Gas · Billing days",
  "gas.last_read": "Gas · Last meter read",
  "gas.current_read": "Gas · Current meter read",
  "gas.multiplier": "Gas · Meter multiplier",
  "gas.total_current_charges": "Gas · Total current charges",
};

const AUDIT_TONE: Record<AuditResult["overall_status"], string> = {
  pass: "bg-emerald-100 text-emerald-700 ring-emerald-200",
  warning: "bg-amber-100 text-amber-700 ring-amber-200",
  fail: "bg-rose-100 text-rose-700 ring-rose-200",
};

/** Resolve the current (effective) value of a supported scalar path off the bill object. */
function currentValue(bill: Bill, path: string): string {
  const [section, field] = path.split(".");
  const src: Record<string, any> | null | undefined =
    section === "account_summary"
      ? (bill.account_summary as any)
      : section === "electric"
      ? (bill.electric_service as any)
      : section === "gas"
      ? (bill.gas_service as any)
      : null;
  if (!src) return "—";
  const v = src[field];
  return v === null || v === undefined ? "—" : String(v);
}

export default function BillCorrectionPanel({
  bill,
  version,
  onCorrected,
}: {
  bill: Bill;
  version: number;
  onCorrected?: () => void;
}) {
  const [supported, setSupported] = useState<string[]>([]);
  const [history, setHistory] = useState<FieldCorrection[]>([]);
  const [field, setField] = useState<string>("");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastAudit, setLastAudit] = useState<AuditResult | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .getCorrections(bill.id)
      .then((r) => {
        if (!alive) return;
        setSupported(r.supported_fields);
        setHistory(r.corrections);
        if (!field && r.supported_fields.length) setField(r.supported_fields[0]);
      })
      .catch((e) => alive && setError(e instanceof Error ? e.message : "Could not load corrections."));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bill.id, version]);

  // Corrected fields = any path that appears in this bill's correction history.
  const correctedPaths = useMemo(
    () => new Set([...(bill.corrected_fields ?? []), ...history.map((h) => h.field_path)]),
    [bill.corrected_fields, history]
  );

  async function submit() {
    if (!field) return;
    if (!reason.trim()) {
      setError("Please provide a reason for the correction.");
      return;
    }
    if (!value.trim()) {
      setError("Please enter a corrected value.");
      return;
    }
    setBusy(true);
    setError(null);
    setOkMsg(null);
    try {
      const res = await api.createCorrection(bill.id, {
        field_path: field,
        corrected_value: value.trim(),
        reason: reason.trim(),
      });
      setLastAudit(res.new_audit_result);
      setHistory((h) => [...h, res.correction]);
      setValue("");
      setReason("");
      setOkMsg(`Corrected ${FIELD_LABELS[field] ?? field}.`);
      onCorrected?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Correction failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Correct a parsed field">
      <p className="mb-3 text-xs text-slate-500">
        Fix a value the parser misread. The original parsed value is preserved — corrections
        are stored separately, and the audit &amp; analytics use the corrected value.
      </p>

      {error && <p className="mb-2 text-sm text-rose-600">{error}</p>}
      {okMsg && <p className="mb-2 text-sm text-emerald-700">✓ {okMsg}</p>}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="text-sm">
          <span className="mb-1 block text-xs font-medium text-slate-500">Field</span>
          <select
            value={field}
            onChange={(e) => setField(e.target.value)}
            className="w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
          >
            {supported.map((f) => (
              <option key={f} value={f}>
                {FIELD_LABELS[f] ?? f}
                {correctedPaths.has(f) ? "  • corrected" : ""}
              </option>
            ))}
          </select>
          {field && (
            <span className="mt-1 block text-xs text-slate-400">
              Current value: <span className="font-medium text-slate-600">{currentValue(bill, field)}</span>
              {correctedPaths.has(field) && (
                <span className="ml-1 inline-flex items-center rounded bg-sky-100 px-1.5 py-0.5 text-[10px] font-medium text-sky-700">
                  ✎ corrected
                </span>
              )}
            </span>
          )}
        </label>

        <label className="text-sm">
          <span className="mb-1 block text-xs font-medium text-slate-500">Corrected value</span>
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="e.g. 1725 or 2026-07-01"
            className="w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
          />
        </label>
      </div>

      <label className="mt-3 block text-sm">
        <span className="mb-1 block text-xs font-medium text-slate-500">
          Reason <span className="text-rose-500">*</span>
          <InfoTip text="A short note on why this value was wrong — kept in the correction history for auditability." />
        </span>
        <input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Parser read 172S because of a PDF extraction issue"
          className="w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
        />
      </label>

      <button
        onClick={submit}
        disabled={busy}
        className="mt-3 rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-700 disabled:opacity-50"
      >
        {busy ? "Saving…" : "Save correction & re-run audit"}
      </button>

      {lastAudit && (
        <div className="mt-3 flex items-center gap-2 text-sm">
          <span className="text-slate-500">Audit after correction:</span>
          <span
            className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ${AUDIT_TONE[lastAudit.overall_status]}`}
          >
            {lastAudit.overall_status}
          </span>
        </div>
      )}

      <div className="mt-5">
        <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Correction history {history.length > 0 && `(${history.length})`}
        </h3>
        {history.length === 0 ? (
          <p className="text-xs text-slate-400">No corrections yet for this bill.</p>
        ) : (
          <div className="overflow-hidden rounded-lg border border-slate-100">
            <table className="w-full text-xs">
              <thead className="bg-slate-50 text-slate-500">
                <tr>
                  <th className="px-2 py-1 text-left font-medium">Field</th>
                  <th className="px-2 py-1 text-left font-medium">Original → Corrected</th>
                  <th className="px-2 py-1 text-left font-medium">Reason</th>
                  <th className="px-2 py-1 text-left font-medium">When</th>
                </tr>
              </thead>
              <tbody>
                {history.map((c) => (
                  <tr key={c.id} className="border-t border-slate-50">
                    <td className="px-2 py-1 text-slate-700">{FIELD_LABELS[c.field_path] ?? c.field_path}</td>
                    <td className="px-2 py-1">
                      <span className="text-slate-400 line-through">{c.original_value ?? "—"}</span>
                      <span className="mx-1 text-slate-400">→</span>
                      <span className="font-medium text-slate-700">{c.corrected_value ?? "—"}</span>
                    </td>
                    <td className="px-2 py-1 text-slate-500">{c.reason ?? "—"}</td>
                    <td className="px-2 py-1 text-slate-400">{shortDate(c.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Card>
  );
}
