import { useEffect, useState } from "react";
import { api, type ConnectorStatus } from "../api";
import { Card, InfoTip } from "./ui";

export default function ConnectorSettingsPanel({ onGoUpload }: { onGoUpload?: () => void }) {
  const [status, setStatus] = useState<ConnectorStatus | null>(null);

  useEffect(() => {
    let alive = true;
    api.connectorStatus().then((s) => alive && setStatus(s)).catch(() => setStatus(null));
    return () => {
      alive = false;
    };
  }, []);

  const planned = status && status.state !== "disabled";

  return (
    <Card
      title="CoServ SmartHub connector"
      right={
        <span className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-600 ring-1 ring-slate-200">
          {planned ? "Planned" : "Planned / Disabled"}
        </span>
      }
    >
      <p className="mb-3 text-sm text-slate-600">
        Automated login to CoServ SmartHub is <span className="font-medium">not available</span>.{" "}
        <span className="font-medium text-slate-700">Manual PDF upload is the recommended, reliable way</span>{" "}
        to add bills today.
        {onGoUpload && (
          <>
            {" "}
            <button onClick={onGoUpload} className="font-medium text-sky-600 hover:text-sky-800">
              Upload a bill →
            </button>
          </>
        )}
        <InfoTip text="There is no public CoServ customer API. A future, optional connector could log in with your authorization and download your own bill PDFs — but it is not built yet." />
      </p>

      <div className="mb-3 rounded-lg border border-slate-200 bg-slate-50/50 p-3">
        <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
          If this is built in the future, it will:
        </h3>
        <ul className="list-disc space-y-0.5 pl-5 text-sm text-slate-600">
          <li>Use your own authorized login (you complete sign-in and MFA yourself)</li>
          <li>Download your bill PDFs and run them through the same parser as manual uploads</li>
          <li><span className="font-medium">Never</span> make payments</li>
          <li><span className="font-medium">Never</span> change account settings</li>
          <li><span className="font-medium">Never</span> bypass MFA, CAPTCHA, or bot-detection</li>
        </ul>
      </div>

      <p className="text-[11px] text-slate-400">
        No username or password is collected here. {status?.message}
      </p>
    </Card>
  );
}
