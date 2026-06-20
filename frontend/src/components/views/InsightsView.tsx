import { useEffect, useState } from "react";
import { api, type Anomaly, type LatestInsights } from "../../api";
import BillSummaryCard from "../BillSummaryCard";
import WhatChangedPanel from "../WhatChangedPanel";
import BillAuditPanel from "../BillAuditPanel";
import RateBreakdownPanel from "../RateBreakdownPanel";
import ForecastPanel from "../ForecastPanel";
import InvestigationQuestionsPanel from "../InvestigationQuestionsPanel";
import ParserMetadataPanel from "../ParserMetadataPanel";
import { Card } from "../ui";
import InsightSection from "../insights/InsightSection";
import MetricDeltaCard from "../insights/MetricDeltaCard";
import DataQualityBadge from "../insights/DataQualityBadge";
import EmptyInsightsState from "../insights/EmptyInsightsState";

const HEADLINE_KEYS = ["total_amount_due", "average_total_cost_per_day", "kwh_per_day", "ccf_per_day"];

const SEV_TONE: Record<string, string> = {
  high: "text-rose-700",
  medium: "text-amber-700",
  low: "text-sky-700",
  info: "text-slate-600",
};

function AnomalyList({ anomalies }: { anomalies: Anomaly[] }) {
  if (!anomalies || anomalies.length === 0) {
    return <p className="text-sm text-slate-500">No anomalies detected for the latest bill.</p>;
  }
  return (
    <div className="space-y-1">
      {anomalies.map((a, i) => (
        <div key={i} className="rounded-lg border border-slate-100 px-3 py-2">
          <p className={`text-sm font-medium ${SEV_TONE[a.severity] ?? "text-slate-700"}`}>{a.message}</p>
          {a.metric && (
            <p className="text-xs text-slate-400">
              {a.metric}
              {a.observed != null ? ` · observed ${a.observed}` : ""}
              {a.baseline_average != null ? ` · baseline ${a.baseline_average}` : ""}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

export default function InsightsView({
  version,
  onGoUpload,
}: {
  version: number;
  onGoUpload: () => void;
}) {
  const [ins, setIns] = useState<LatestInsights | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setIns(null);
    setError(null);
    api
      .latestInsights()
      .then((d) => alive && setIns(d))
      .catch((e) => alive && setError(e instanceof Error ? e.message : "Could not load insights."));
    return () => {
      alive = false;
    };
  }, [version]);

  if (error) return <p className="text-sm text-rose-600">{error}</p>;
  if (!ins) return <p className="text-slate-400">Loading insights…</p>;
  if (ins.status === "no_bills" || !ins.bill) return <EmptyInsightsState onGoUpload={onGoUpload} />;

  const bill = ins.bill;
  const comparison = ins.comparison;
  const headline =
    comparison && comparison.status === "ok"
      ? HEADLINE_KEYS.map((k) => comparison.metrics.find((m) => m.key === k)).filter(Boolean)
      : [];
  const weatherAvailable = ins.data_quality.weather_available;
  const forecastAvailable = ins.data_quality.forecast_available;
  const parserWarnings = ins.parser?.warnings ?? [];

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-slate-900">Latest bill insights</h1>
          <p className="text-xs text-slate-400">
            Everything about your most recent statement, in one place.
          </p>
        </div>
        <DataQualityBadge dq={ins.data_quality} />
      </div>

      {/* Parser warnings surfaced up top (never buried) */}
      {parserWarnings.length > 0 && (
        <div className="rounded-lg bg-amber-50 px-4 py-2 text-sm text-amber-800 ring-1 ring-amber-200">
          The parser flagged {parserWarnings.length} warning(s) on this bill — values may need review.
        </div>
      )}
      {ins.warnings.length > 0 && (
        <div className="rounded-lg bg-slate-50 px-4 py-2 text-xs text-slate-500 ring-1 ring-slate-200">
          Some sections are unavailable: {ins.warnings.join(" ")}
        </div>
      )}

      {/* Headline deltas */}
      {headline.length > 0 && (
        <InsightSection title="At a glance" subtitle="Key changes vs the previous bill">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {headline.map((m) => m && <MetricDeltaCard key={m.key} m={m} />)}
          </div>
        </InsightSection>
      )}

      {/* 1. Latest Bill Summary */}
      <InsightSection title="Latest bill summary">
        <BillSummaryCard bill={bill} isLatest />
      </InsightSection>

      {/* 2. What changed (reuses the aggregated comparison — no extra fetch) */}
      <InsightSection title="What changed">
        <WhatChangedPanel
          billId={bill.id}
          version={version}
          onGoUpload={onGoUpload}
          preloaded={comparison}
        />
      </InsightSection>

      {/* 3. Bill audit */}
      <InsightSection title="Bill audit">
        <BillAuditPanel billId={bill.id} version={version} preloaded={ins.audit} />
      </InsightSection>

      {/* 4. Rate & fee breakdown */}
      <InsightSection title="Rate & fee breakdown">
        <RateBreakdownPanel billId={bill.id} version={version} preloaded={ins.rate_breakdown} />
      </InsightSection>

      {/* 5. Anomalies */}
      <InsightSection title="Anomalies">
        <Card title="Anomalies">
          <AnomalyList anomalies={ins.anomalies?.anomalies ?? []} />
          {ins.anomalies && ins.anomalies.reliable === false && (
            <p className="mt-2 text-xs text-slate-400">
              More history is needed before anomaly detection is reliable.
            </p>
          )}
        </Card>
      </InsightSection>

      {/* 6. Weather context — compact, from the aggregated normalization (no extra fetch).
            The full interactive weather panel (charts + sync) lives on the Dashboard. */}
      <InsightSection
        title="Weather context"
        available={weatherAvailable}
        unavailableText="Weather isn't synced for this bill. Configure a location and sync on the Dashboard to see it."
      >
        {ins.weather && (
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="rounded-lg bg-sky-50/50 px-3 py-2 ring-1 ring-sky-100">
              <div className="text-[11px] font-medium uppercase tracking-wide text-sky-700">
                Electric vs cooling
              </div>
              <p className="mt-1 text-sm text-slate-700">{ins.weather.electric_usage_vs_cdd_context}</p>
            </div>
            <div className="rounded-lg bg-amber-50/50 px-3 py-2 ring-1 ring-amber-100">
              <div className="text-[11px] font-medium uppercase tracking-wide text-amber-700">
                Gas vs heating
              </div>
              <p className="mt-1 text-sm text-slate-700">{ins.weather.gas_usage_vs_hdd_context}</p>
            </div>
          </div>
        )}
      </InsightSection>

      {/* 7. Forecast (if enough history) — reuses the aggregated forecast */}
      <InsightSection
        title="Forecast"
        available={forecastAvailable}
        unavailableText="Not enough history to forecast yet — add more bills."
      >
        <ForecastPanel version={version} onGoUpload={onGoUpload} preloaded={ins.forecast} />
      </InsightSection>

      {/* 8. Questions to investigate (reuses the aggregated questions — no extra fetch) */}
      <InsightSection title="Questions to investigate">
        <InvestigationQuestionsPanel billId={bill.id} version={version} preloaded={ins.questions} />
      </InsightSection>

      {/* 9. Parser / data quality */}
      <InsightSection title="Parser & data quality">
        <ParserMetadataPanel bill={bill} version={version} />
      </InsightSection>
    </div>
  );
}
