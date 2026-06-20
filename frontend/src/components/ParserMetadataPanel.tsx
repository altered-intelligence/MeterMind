import { useEffect, useState } from "react";
import { api, type Bill, type ParserInfo, type ParserRun } from "../api";
import { Card, ConfidenceBadge, InfoTip } from "./ui";

const TIP = {
  parserVersion:
    "The extraction-logic version that produced this bill's current data. CoServ tweaks its layout over time; reprocessing re-runs the latest parser over the stored PDF.",
  schemaVersion:
    "The output-shape contract (CoservBill). Bumped only when fields are added, removed, or renamed — independently of the parser version.",
  runHistory:
    "Every parse of this bill's stored PDF — the first ingest plus any reprocesses — with its parser version, outcome, and timing. An auditable trail of how the data was produced.",
};

function statusTone(status: string): string {
  if (status === "failed") return "bg-rose-100 text-rose-700 ring-rose-200";
  if (status === "completed_with_warnings") return "bg-amber-100 text-amber-700 ring-amber-200";
  return "bg-emerald-100 text-emerald-700 ring-emerald-200";
}

function statusLabel(status: string): string {
  if (status === "completed_with_warnings") return "completed · warnings";
  return status.replace(/_/g, " ");
}

function triggerLabel(trigger: string | null): string {
  if (trigger === "ingest") return "Initial import";
  if (trigger === "reprocess") return "Reprocess";
  if (trigger === "backfill") return "Backfilled";
  return trigger ?? "—";
}

function when(run: ParserRun): string {
  const t = run.completed_at ?? run.started_at;
  if (!t) return "—";
  const d = new Date(t);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function Field({
  label,
  value,
  tip,
  mono = false,
}: {
  label: string;
  value: React.ReactNode;
  tip?: string;
  mono?: boolean;
}) {
  return (
    <div>
      <div className="flex items-center gap-1 text-[11px] font-medium uppercase tracking-wide text-slate-400">
        {label}
        {tip && <InfoTip text={tip} />}
      </div>
      <div className={`mt-0.5 text-sm text-slate-700 ${mono ? "font-mono" : ""}`}>{value}</div>
    </div>
  );
}

export default function ParserMetadataPanel({
  bill,
  version,
  onChanged,
}: {
  bill: Bill;
  version: number;
  onChanged?: () => void;
}) {
  const [latest, setLatest] = useState<ParserInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .getProviderLatest((bill.provider_name || "coserv").toLowerCase())
      .then((l) => alive && setLatest(l))
      .catch(() => alive && setLatest(null));
    return () => {
      alive = false;
    };
  }, [bill.provider_name, version]);

  const runs = (bill.parser_runs ?? []).slice().sort((a, b) => a.id - b.id);
  const totalWarnings = bill.warnings.length;
  const isOutdated =
    latest != null && bill.parser_version != null && bill.parser_version !== latest.parser_version;

  async function reprocess() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await api.reprocessBill(bill.id);
      const broken = res.correction_warnings.filter((c) => !c.still_applies).length;
      setNotice(
        `Reprocessed with ${res.parser_run.parser_version ?? "latest"} ` +
          `(${res.parser_run.parse_duration_ms ?? "—"} ms).` +
          (broken > 0 ? ` ${broken} correction(s) may no longer apply — review below.` : "")
      );
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Reprocess failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Parser & versioning"
      right={
        <button
          onClick={reprocess}
          disabled={busy}
          className="rounded-md bg-sky-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-700 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-sky-300"
        >
          {busy ? "Reprocessing…" : "Reprocess from stored PDF"}
        </button>
      }
    >
      <p className="mb-3 text-xs text-slate-500">
        Which parser produced this bill's data, and an auditable history of every parse. Reprocessing
        re-runs the latest parser over the original PDF — manual corrections are preserved.
      </p>

      {isOutdated && (
        <div className="mb-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800 ring-1 ring-amber-200">
          A newer parser ({latest?.parser_version}) is available. Reprocess to update this bill's data.
        </div>
      )}
      {notice && (
        <div className="mb-3 rounded-md bg-emerald-50 px-3 py-2 text-xs text-emerald-800 ring-1 ring-emerald-200">
          {notice}
        </div>
      )}
      {error && (
        <div className="mb-3 rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-700 ring-1 ring-rose-200">
          {error}
        </div>
      )}

      <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
        <Field
          label="Parser version"
          tip={TIP.parserVersion}
          mono
          value={
            <span className="inline-flex items-center gap-1.5">
              {bill.parser_version ?? "—"}
              {!isOutdated && latest && (
                <span className="rounded bg-emerald-100 px-1 py-0.5 text-[10px] font-medium text-emerald-700">
                  latest
                </span>
              )}
            </span>
          }
        />
        <Field label="Schema version" tip={TIP.schemaVersion} mono value={bill.parser_schema_version ?? "—"} />
        <Field label="Confidence" value={<ConfidenceBadge level={bill.parser_confidence} />} />
        <Field label="Parsed at" value={when({ completed_at: bill.parsed_at } as ParserRun)} />
        <Field
          label="Parse time"
          value={bill.parse_duration_ms != null ? `${bill.parse_duration_ms} ms` : "—"}
        />
        <Field
          label="Warnings"
          value={
            <span className={totalWarnings ? "text-amber-700" : "text-slate-700"}>
              {totalWarnings}
            </span>
          }
        />
      </div>

      <div className="mt-5">
        <h3 className="mb-1 flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Parser run history
          <InfoTip text={TIP.runHistory} />
        </h3>
        {runs.length === 0 ? (
          <p className="text-sm text-slate-400">No recorded runs.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-[11px] uppercase tracking-wide text-slate-400">
                <tr className="border-b border-slate-100">
                  <th className="py-1.5 pr-3 font-medium">When</th>
                  <th className="py-1.5 pr-3 font-medium">Trigger</th>
                  <th className="py-1.5 pr-3 font-medium">Version</th>
                  <th className="py-1.5 pr-3 font-medium">Status</th>
                  <th className="py-1.5 pr-3 font-medium">Warn</th>
                  <th className="py-1.5 font-medium">Time</th>
                </tr>
              </thead>
              <tbody>
                {runs
                  .slice()
                  .reverse()
                  .map((r) => (
                    <tr key={r.id} className="border-b border-slate-50 last:border-0">
                      <td className="py-1.5 pr-3 text-slate-600">{when(r)}</td>
                      <td className="py-1.5 pr-3 text-slate-600">{triggerLabel(r.trigger)}</td>
                      <td className="py-1.5 pr-3 font-mono text-slate-600">{r.parser_version ?? "—"}</td>
                      <td className="py-1.5 pr-3">
                        <span
                          className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ${statusTone(
                            r.status
                          )}`}
                        >
                          {statusLabel(r.status)}
                        </span>
                      </td>
                      <td className="py-1.5 pr-3 text-slate-600">{r.warning_count}</td>
                      <td className="py-1.5 text-slate-500">
                        {r.parse_duration_ms != null ? `${r.parse_duration_ms} ms` : "—"}
                      </td>
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
