import { useEffect, useState } from "react";
import { api, type AuditEvent, type PrivacyStatus } from "../api";
import { Card, InfoTip } from "./ui";

const EVENT_LABEL: Record<string, string> = {
  bill_uploaded: "Bill uploaded",
  bill_deleted: "Bill deleted",
  bill_exported: "Data exported",
  correction_created: "Correction made",
  parser_reprocess_started: "Reprocess started",
  parser_reprocess_completed: "Reprocess completed",
  weather_sync_started: "Weather sync",
  local_llm_summary_generated: "LLM summary generated",
};

function Pill({ on, onText, offText }: { on: boolean; onText: string; offText: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ${
        on ? "bg-emerald-100 text-emerald-700 ring-emerald-200" : "bg-slate-100 text-slate-500 ring-slate-200"
      }`}
    >
      {on ? onText : offText}
    </span>
  );
}

export default function PrivacySettingsPanel({ version }: { version: number }) {
  const [status, setStatus] = useState<PrivacyStatus | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .privacyStatus()
      .then((s) => alive && setStatus(s))
      .catch((e) => alive && setError(e instanceof Error ? e.message : "Could not load privacy status."));
    api.auditEvents(50).then((e) => alive && setEvents(e)).catch(() => setEvents([]));
    return () => {
      alive = false;
    };
  }, [version]);

  if (error) {
    return (
      <Card title="Privacy & security">
        <p className="text-sm text-rose-600">{error}</p>
      </Card>
    );
  }
  if (!status) {
    return (
      <Card title="Privacy & security">
        <p className="text-sm text-slate-400">Loading…</p>
      </Card>
    );
  }

  const integrations = Object.entries(status.integrations);

  return (
    <Card
      title="Privacy & security"
      right={
        <Pill
          on={status.privacy_mode_local_only}
          onText="Local-only mode ON"
          offText="Local-only mode OFF"
        />
      }
    >
      <p className="mb-3 text-xs text-slate-500">
        Sensitive values (account, meter, address) are redacted everywhere by default.{" "}
        <InfoTip text="In local-only privacy mode the app makes no external network calls (live weather, ZIP geocoding) and blocks sensitive exports. The local Ollama LLM still works — it runs on your machine." />
      </p>

      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-3">
        <div className="rounded-lg bg-slate-50 px-3 py-2">
          <div className="text-[11px] uppercase tracking-wide text-slate-400">External calls</div>
          <div className="mt-1">
            <Pill on={status.external_calls_allowed} onText="Allowed" offText="Blocked" />
          </div>
        </div>
        <div className="rounded-lg bg-slate-50 px-3 py-2">
          <div className="text-[11px] uppercase tracking-wide text-slate-400">Sensitive export</div>
          <div className="mt-1">
            <Pill on={status.sensitive_export_allowed} onText="Allowed" offText="Blocked" />
          </div>
        </div>
        <div className="rounded-lg bg-slate-50 px-3 py-2">
          <div className="text-[11px] uppercase tracking-wide text-slate-400">Telemetry / cloud</div>
          <div className="mt-1">
            <Pill on={!status.telemetry && !status.cloud_sync} onText="None" offText="Active" />
          </div>
        </div>
      </div>

      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Integrations</h3>
      <div className="mb-4 space-y-1">
        {integrations.map(([key, val]) => (
          <div key={key} className="flex items-center justify-between border-b border-slate-50 py-1 last:border-0">
            <span className="text-sm text-slate-600">
              {key.replace(/_/g, " ")}
              {val.provider ? <span className="text-slate-400"> · {val.provider}</span> : null}
            </span>
            <span className="flex items-center gap-1.5">
              {"enabled" in val && <Pill on={!!val.enabled} onText="enabled" offText="disabled" />}
              <Pill on={!val.external} onText="local" offText="external" />
            </span>
          </div>
        ))}
      </div>

      <h3 className="mb-2 flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
        Recent activity
        <InfoTip text="An append-only audit log of security-relevant actions. It never stores account numbers, addresses, or other sensitive values." />
      </h3>
      {events.length === 0 ? (
        <p className="text-sm text-slate-400">No audit events yet.</p>
      ) : (
        <div className="max-h-48 overflow-y-auto">
          <table className="w-full text-left text-xs">
            <tbody>
              {events.map((e) => (
                <tr key={e.id} className="border-b border-slate-50 last:border-0">
                  <td className="py-1 pr-3 text-slate-600">{EVENT_LABEL[e.event_type] ?? e.event_type}</td>
                  <td className="py-1 pr-3 text-slate-400">
                    {e.object_type}
                    {e.object_id ? ` #${e.object_id}` : ""}
                  </td>
                  <td className="py-1 text-right text-slate-400">
                    {e.created_at ? new Date(e.created_at).toLocaleString() : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
