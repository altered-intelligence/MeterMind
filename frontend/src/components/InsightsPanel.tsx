import { useEffect, useState } from "react";
import { api, type Insights } from "../api";
import { Card } from "./ui";

function ChangePct({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) return <span className="text-slate-400">—</span>;
  const up = value > 0;
  const cls = value === 0 ? "text-slate-500" : up ? "text-rose-600" : "text-emerald-600";
  return (
    <span className={cls}>
      {up ? "▲" : value < 0 ? "▼" : "→"} {value > 0 ? "+" : ""}
      {value}%
    </span>
  );
}

export default function InsightsPanel({ version }: { version: number }) {
  const [data, setData] = useState<Insights | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .insights()
      .then((d) => alive && setData(d))
      .catch(() => alive && setData(null));
    return () => {
      alive = false;
    };
  }, [version]);

  if (!data) return null;
  const m = data.metrics;

  return (
    <Card title="Insights">
      <p className="text-sm leading-relaxed text-slate-700">{data.summary}</p>

      {data.anomalies.length > 0 && (
        <div className="mt-3 space-y-1">
          {data.anomalies.map((a, i) => (
            <div
              key={i}
              className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800"
            >
              {a.message}
            </div>
          ))}
        </div>
      )}

      {data.warnings.length > 0 && (
        <div className="mt-3 text-xs text-slate-500">
          {data.warnings.map((w, i) => (
            <p key={i}>· {w}</p>
          ))}
        </div>
      )}

      {data.recommendations.length > 0 && (
        <ul className="mt-3 list-disc space-y-0.5 pl-5 text-xs text-slate-600">
          {data.recommendations.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}

      {/* Key deterministic metrics (vs the previous bill). */}
      <div className="mt-4 grid grid-cols-3 gap-3 border-t border-slate-100 pt-3 text-sm">
        <div>
          <div className="text-xs text-slate-500">Total bill</div>
          <div className="font-medium text-slate-800">
            <ChangePct value={typeof m.total_bill_change_percent === "number" ? m.total_bill_change_percent : null} />
          </div>
          <div className="text-xs text-slate-400">
            {m.previous_total_bill ? `from $${m.previous_total_bill}` : "—"}
          </div>
        </div>
        <div>
          <div className="text-xs text-slate-500">Electric kWh</div>
          <div className="font-medium text-slate-800">
            <ChangePct value={typeof m.kwh_change_percent === "number" ? m.kwh_change_percent : null} />
          </div>
          <div className="text-xs text-slate-400">avg {m.rolling_average_kwh_per_day ?? "—"} kWh/day</div>
        </div>
        <div>
          <div className="text-xs text-slate-500">Gas CCF</div>
          <div className="font-medium text-slate-800">
            <ChangePct value={typeof m.ccf_change_percent === "number" ? m.ccf_change_percent : null} />
          </div>
          <div className="text-xs text-slate-400">avg {m.rolling_average_ccf_per_day ?? "—"} CCF/day</div>
        </div>
      </div>
      <p className="mt-2 text-[11px] text-slate-400">
        All figures are computed deterministically from your stored bills (no AI estimates).
      </p>
    </Card>
  );
}
