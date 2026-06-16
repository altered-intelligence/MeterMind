import { useEffect, useState } from "react";
import { api, type PortalStatus } from "../api";
import { Card } from "./ui";

const FALLBACK =
  "Future feature: connect to CoServ SmartHub. For now, upload PDF bills manually.";

export default function SmartHubPlaceholder() {
  const [status, setStatus] = useState<PortalStatus | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .portalStatus()
      .then((s) => alive && setStatus(s))
      .catch(() => alive && setStatus(null));
    return () => {
      alive = false;
    };
  }, []);

  const message = status?.message ?? FALLBACK;

  return (
    <Card title="Connect CoServ SmartHub">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm text-slate-600">{message}</p>
          <p className="mt-1 text-xs text-slate-400">
            Manual PDF upload is the primary, reliable path.
          </p>
        </div>
        <button
          type="button"
          disabled
          title="Coming soon"
          aria-disabled="true"
          className="shrink-0 cursor-not-allowed rounded-lg border border-slate-200 bg-slate-100 px-4 py-2 text-sm font-medium text-slate-400"
        >
          Connect (coming soon)
        </button>
      </div>

      {status && status.constraints.length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-xs text-slate-400">
            Planned safeguards when this ships
          </summary>
          <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-slate-500">
            {status.constraints.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </details>
      )}
    </Card>
  );
}
