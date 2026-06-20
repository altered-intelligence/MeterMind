import type { LatestInsights } from "../../api";

/** Compact badge summarizing how trustworthy/complete the latest insights are. */
export default function DataQualityBadge({ dq }: { dq: LatestInsights["data_quality"] }) {
  const degraded = dq.status === "degraded";
  const conf = (dq.parser_confidence || "").toLowerCase();
  const tone = degraded
    ? "bg-amber-100 text-amber-700 ring-amber-200"
    : conf === "low"
    ? "bg-amber-100 text-amber-700 ring-amber-200"
    : "bg-emerald-100 text-emerald-700 ring-emerald-200";
  const label = degraded ? "Partial data" : conf === "low" ? "Low parser confidence" : "Data OK";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ${tone}`}
      title={`Sections available: ${dq.sections_available.join(", ") || "none"}`}
    >
      {label}
      {dq.audit_status ? <span className="opacity-70">· audit {dq.audit_status}</span> : null}
    </span>
  );
}
