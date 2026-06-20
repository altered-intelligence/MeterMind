import { api } from "../api";
import { Card, InfoTip } from "./ui";

const TIP =
  "Exports reflect your corrected (effective) data — what you see in the app. Account and " +
  "address fields are excluded by default for privacy.";

function DownloadLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a
      href={href}
      className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 hover:text-slate-900 focus:outline-none focus:ring-2 focus:ring-sky-300"
    >
      <span aria-hidden>↓</span>
      {children}
    </a>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
      <div className="flex flex-wrap gap-2">{children}</div>
    </div>
  );
}

export default function ExportPanel({ billId }: { billId?: number }) {
  return (
    <Card title="Export & reports" right={<InfoTip text={TIP} />}>
      <p className="mb-4 text-xs text-slate-500">
        Download your parsed bills, usage, and charges. CSV opens in any spreadsheet; JSON is the
        full structured record.
      </p>

      <div className="space-y-4">
        <Group title="All bills">
          <DownloadLink href={api.exportUrl("/export/bills.json")}>Bills (JSON)</DownloadLink>
          <DownloadLink href={api.exportUrl("/export/bills.csv")}>Bills (CSV)</DownloadLink>
          <DownloadLink href={api.exportUrl("/export/electric.csv")}>Electric usage (CSV)</DownloadLink>
          <DownloadLink href={api.exportUrl("/export/gas.csv")}>Gas usage (CSV)</DownloadLink>
          <DownloadLink href={api.exportUrl("/export/charges.csv")}>Charge line items (CSV)</DownloadLink>
        </Group>

        {billId != null && (
          <Group title="This bill">
            <DownloadLink href={api.exportUrl(`/bills/${billId}/export.json`)}>Bill (JSON)</DownloadLink>
            <DownloadLink href={api.exportUrl(`/bills/${billId}/report.pdf`)}>Report (PDF)</DownloadLink>
          </Group>
        )}
      </div>

      <p className="mt-4 text-[11px] text-slate-400">
        Sensitive fields (account last-4, address) are omitted. They can be included only if the
        server is configured to allow it.
      </p>
    </Card>
  );
}
