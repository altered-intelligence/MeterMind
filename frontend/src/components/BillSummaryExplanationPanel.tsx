import { useEffect, useState } from "react";
import { api, type SummaryExplanation } from "../api";
import { Card, InfoTip } from "./ui";

function List({ title, items }: { title: string; items: string[] }) {
  if (!items || items.length === 0) return null;
  return (
    <div>
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
      <ul className="list-disc space-y-0.5 pl-5">
        {items.map((it, i) => (
          <li key={i} className="text-sm text-slate-600">
            {it}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function BillSummaryExplanationPanel({
  billId,
  version,
}: {
  billId: number;
  version: number;
}) {
  const [exp, setExp] = useState<SummaryExplanation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    setExp(null);
    setError(null);
    api
      .billSummary(billId)
      .then((s) => alive && setExp(s.explanation))
      .catch((e) => alive && setError(e instanceof Error ? e.message : "Could not load summary."));
    return () => {
      alive = false;
    };
  }, [billId, version]);

  async function regenerate() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.regenerateBillSummary(billId);
      setExp(r.explanation);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Regeneration failed.");
    } finally {
      setBusy(false);
    }
  }

  const local = exp?.generated_locally;
  const badge = exp ? (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ${
        local
          ? "bg-violet-100 text-violet-700 ring-violet-200"
          : "bg-slate-100 text-slate-600 ring-slate-200"
      }`}
    >
      {local ? "Generated locally with Ollama" : "Deterministic"}
    </span>
  ) : null;

  return (
    <Card
      title="Bill explanation"
      right={
        <div className="flex items-center gap-2">
          {badge}
          {exp?.llm_enabled && (
            <button
              onClick={regenerate}
              disabled={busy}
              className="rounded-md bg-violet-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-700 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-violet-300"
            >
              {busy ? "Generating…" : "Regenerate with Ollama"}
            </button>
          )}
        </div>
      }
    >
      <p className="mb-3 text-xs text-slate-500">
        Plain-English explanation built from the parsed, validated metrics only.{" "}
        <InfoTip text="The local LLM (if enabled) only rephrases these numbers — it never parses the PDF or sees account/meter/address data. Every figure comes from the deterministic parser." />
      </p>

      {error && <p className="mb-2 text-sm text-rose-600">{error}</p>}
      {!exp && !error && <p className="text-sm text-slate-400">Loading…</p>}

      {exp && (
        <div className="space-y-3">
          <p className="text-sm leading-relaxed text-slate-700">{exp.summary}</p>
          <List title="Notable changes" items={exp.notable_changes} />
          <List title="Questions to investigate" items={exp.questions_to_investigate} />
          <List title="Data quality notes" items={exp.data_quality_notes} />

          {!local && exp.fallback_reason && (
            <p className="rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-500">
              Showing the deterministic summary. {exp.fallback_reason}
              {exp.llm_enabled ? " You can try regenerating with the local model." : ""}
            </p>
          )}

          <details className="text-xs text-slate-400">
            <summary className="cursor-pointer select-none hover:text-slate-600">
              Source metrics sent to the model (redacted, no PII)
            </summary>
            <pre className="mt-2 overflow-x-auto rounded bg-slate-50 p-2 text-[11px] text-slate-600">
              {JSON.stringify(exp.source_metrics, null, 2)}
            </pre>
          </details>

          <p className="text-[11px] italic text-slate-400">{exp.disclaimer}</p>
        </div>
      )}
    </Card>
  );
}
