import { useEffect, useState } from "react";
import { api, type AuditCheck, type AuditResult, type AuditStatus } from "../api";
import { Card } from "./ui";

const GROUPS: { key: string; title: string }[] = [
  { key: "account_summary", title: "Account Summary" },
  { key: "electric", title: "Electric" },
  { key: "gas", title: "Gas" },
  { key: "snapshot", title: "Snapshot" },
];

const STATUS_STYLE: Record<AuditStatus, { dot: string; mark: string; text: string }> = {
  pass: { dot: "bg-emerald-500", mark: "✓", text: "text-emerald-700" },
  warning: { dot: "bg-amber-500", mark: "▲", text: "text-amber-700" },
  fail: { dot: "bg-rose-500", mark: "✕", text: "text-rose-700" },
  skipped: { dot: "bg-slate-300", mark: "–", text: "text-slate-400" },
};

function OverallBadge({ status }: { status: AuditResult["overall_status"] }) {
  const map = {
    pass: "bg-emerald-100 text-emerald-700 ring-emerald-200",
    warning: "bg-amber-100 text-amber-700 ring-amber-200",
    fail: "bg-rose-100 text-rose-700 ring-rose-200",
  } as const;
  const label = { pass: "All checks passed", warning: "Warnings", fail: "Failed checks" }[status];
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ${map[status]}`}>
      {label}
    </span>
  );
}

function CheckRow({ c }: { c: AuditCheck }) {
  const s = STATUS_STYLE[c.status];
  const showValues = c.status !== "skipped" && (c.expected_value !== null || c.actual_value !== null);
  return (
    <div className="flex items-start gap-2 border-b border-slate-50 py-1.5 last:border-0">
      <span className={`mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white ${s.dot}`}>
        {s.mark}
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-sm text-slate-700">
          {c.label}
          {c.status === "skipped" && <span className="ml-2 text-xs text-slate-400">skipped</span>}
        </div>
        {showValues && (
          <div className="text-xs text-slate-400">
            expected {c.expected_value ?? "—"} · actual {c.actual_value ?? "—"}
            {c.difference != null && c.status !== "pass" ? ` · Δ ${c.difference}` : ""}
          </div>
        )}
        {c.status !== "pass" && c.message && (
          <div className={`text-xs ${s.text}`}>{c.message}</div>
        )}
      </div>
    </div>
  );
}

export default function BillAuditPanel({
  billId,
  version,
  preloaded,
}: {
  billId: number;
  version: number;
  /** When provided (e.g. by the Insights page), use this instead of fetching. */
  preloaded?: AuditResult | null;
}) {
  const [audit, setAudit] = useState<AuditResult | null>(preloaded ?? null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (preloaded !== undefined) {
      setAudit(preloaded);
      setError(preloaded === null ? "Audit is unavailable." : null);
      return;
    }
    let alive = true;
    setAudit(null);
    setError(null);
    api
      .getBillAudit(billId)
      .then((a) => alive && setAudit(a))
      .catch((e) => alive && setError(e instanceof Error ? e.message : "Could not load audit."));
    return () => {
      alive = false;
    };
  }, [billId, version, preloaded]);

  if (error) {
    return (
      <Card title="Bill audit">
        <p className="text-sm text-rose-600">{error}</p>
      </Card>
    );
  }
  if (!audit) {
    return (
      <Card title="Bill audit">
        <p className="text-sm text-slate-400">Loading…</p>
      </Card>
    );
  }

  const counts = audit.checks.reduce(
    (acc, c) => {
      acc[c.status] = (acc[c.status] ?? 0) + 1;
      return acc;
    },
    {} as Record<string, number>
  );

  return (
    <Card title="Bill audit" right={<OverallBadge status={audit.overall_status} />}>
      <p className="mb-3 text-xs text-slate-500">
        Deterministic internal-math checks (not fraud detection).{" "}
        {counts.pass ?? 0} passed
        {counts.warning ? ` · ${counts.warning} warning` : ""}
        {counts.fail ? ` · ${counts.fail} failed` : ""}
        {counts.skipped ? ` · ${counts.skipped} skipped` : ""}.
      </p>

      <div className="space-y-4">
        {GROUPS.map(({ key, title }) => {
          const groupChecks = audit.checks.filter((c) => c.group === key);
          if (groupChecks.length === 0) return null;
          return (
            <div key={key}>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                {title}
              </h3>
              <div>
                {groupChecks.map((c) => (
                  <CheckRow key={c.check_code} c={c} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
