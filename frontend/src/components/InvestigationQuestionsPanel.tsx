import { useEffect, useState } from "react";
import { api, type InvestigationQuestion } from "../api";
import { Card, InfoTip } from "./ui";

const CATEGORIES: { key: string; title: string }[] = [
  { key: "billing", title: "Billing" },
  { key: "electric", title: "Electric" },
  { key: "gas", title: "Gas" },
  { key: "weather", title: "Weather" },
  { key: "parser", title: "Data quality" },
  { key: "future_integration", title: "Future integrations" },
];

const SEVERITY: Record<string, string> = {
  high: "bg-rose-100 text-rose-700 ring-rose-200",
  medium: "bg-amber-100 text-amber-700 ring-amber-200",
  low: "bg-sky-100 text-sky-700 ring-sky-200",
  info: "bg-slate-100 text-slate-600 ring-slate-200",
};

const INTEGRATION_LABEL: Record<string, string> = {
  tesla: "Tesla",
  ecobee: "ecobee",
  rainbird: "Rain Bird",
};

function QuestionRow({ q }: { q: InvestigationQuestion }) {
  const future = q.status === "needs_future_integration";
  return (
    <div className="border-b border-slate-50 py-2 last:border-0">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-medium text-slate-700">{q.question}</p>
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium uppercase ring-1 ${
            SEVERITY[q.severity] ?? SEVERITY.info
          }`}
        >
          {q.severity}
        </span>
      </div>
      <p className="mt-0.5 text-xs text-slate-500">{q.why_it_matters}</p>
      <div className="mt-1 flex flex-wrap items-center gap-2">
        {future ? (
          <span className="rounded bg-violet-50 px-1.5 py-0.5 text-[10px] font-medium text-violet-700 ring-1 ring-violet-200">
            Future only{q.required_future_integration ? ` · ${INTEGRATION_LABEL[q.required_future_integration] ?? q.required_future_integration}` : ""}
          </span>
        ) : (
          <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 ring-1 ring-emerald-200">
            Answerable now
          </span>
        )}
      </div>
    </div>
  );
}

export default function InvestigationQuestionsPanel({
  billId,
  version,
  preloaded,
}: {
  billId: number;
  version?: number;
  /** When provided (e.g. by the Insights page), use this instead of fetching. */
  preloaded?: InvestigationQuestion[] | null;
}) {
  const [questions, setQuestions] = useState<InvestigationQuestion[] | null>(preloaded ?? null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (preloaded !== undefined) {
      setQuestions(preloaded);
      setError(null);
      return;
    }
    let alive = true;
    setQuestions(null);
    setError(null);
    api
      .investigationQuestions(billId)
      .then((r) => alive && setQuestions(r.questions))
      .catch((e) => alive && setError(e instanceof Error ? e.message : "Could not load questions."));
    return () => {
      alive = false;
    };
  }, [billId, version, preloaded]);

  return (
    <Card
      title="Questions to investigate"
      right={
        <InfoTip text="Deterministic, rules-based prompts derived from your bill, comparisons, audit, and weather. Not an LLM — and never a definitive explanation." />
      }
    >
      {error && <p className="text-sm text-rose-600">{error}</p>}
      {!questions && !error && <p className="text-sm text-slate-400">Loading…</p>}
      {questions && questions.length === 0 && (
        <p className="text-sm text-slate-500">Nothing notable to investigate on this bill.</p>
      )}
      {questions && questions.length > 0 && (
        <div className="space-y-4">
          {CATEGORIES.map(({ key, title }) => {
            const inCat = questions.filter((q) => q.category === key);
            if (inCat.length === 0) return null;
            return (
              <div key={key}>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  {title}
                </h3>
                <div>
                  {inCat.map((q) => (
                    <QuestionRow key={q.question_id} q={q} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}
