import type { ReactNode } from "react";

/** A titled section wrapper for the Insights page, with optional unavailable state. */
export default function InsightSection({
  title,
  subtitle,
  available = true,
  unavailableText,
  right,
  children,
}: {
  title: string;
  subtitle?: string;
  available?: boolean;
  unavailableText?: string;
  right?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <section className="scroll-mt-4">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{title}</h2>
          {subtitle && <p className="text-xs text-slate-400">{subtitle}</p>}
        </div>
        {right}
      </div>
      {available ? (
        children
      ) : (
        <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50/50 px-4 py-3 text-sm text-slate-400">
          {unavailableText ?? "Not available yet."}
        </div>
      )}
    </section>
  );
}
