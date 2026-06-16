import { useEffect, useState } from "react";
import { api, shortDate, type ImportBatch } from "../api";
import { Card } from "./ui";

function Stat({ n, label, tone }: { n: number; label: string; tone: string }) {
  return (
    <div className={`rounded-lg px-3 py-2 text-center ${tone}`}>
      <div className="text-lg font-semibold">{n}</div>
      <div className="text-[11px] uppercase tracking-wide">{label}</div>
    </div>
  );
}

export default function ImportHistoryPanel({
  version,
  onGoUpload,
}: {
  version: number;
  onGoUpload?: () => void;
}) {
  const [batches, setBatches] = useState<ImportBatch[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .listImportBatches()
      .then((b) => alive && setBatches(b))
      .catch((e) => alive && setError(e instanceof Error ? e.message : "Could not load import history."));
    return () => {
      alive = false;
    };
  }, [version]);

  if (error) {
    return (
      <Card title="Import history">
        <p className="text-sm text-rose-600">{error}</p>
      </Card>
    );
  }
  if (!batches) {
    return (
      <Card title="Import history">
        <p className="text-sm text-slate-400">Loading…</p>
      </Card>
    );
  }
  if (batches.length === 0) {
    return (
      <Card title="Import history">
        <p className="text-sm text-slate-500">
          No bulk imports yet.{" "}
          {onGoUpload && (
            <button onClick={onGoUpload} className="font-medium text-sky-600 hover:text-sky-800">
              Bulk import bills →
            </button>
          )}
        </p>
      </Card>
    );
  }

  const latest = batches[0];
  return (
    <Card
      title="Import history"
      right={
        <span className="text-xs text-slate-400">
          {batches.length} batch{batches.length === 1 ? "" : "es"}
        </span>
      }
    >
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-700">
          Latest import
          <span className="ml-2 text-xs font-normal text-slate-400">
            {shortDate(latest.completed_at ?? latest.started_at)} · batch #{latest.id}
          </span>
        </h3>
        <span className="text-xs text-slate-400">{latest.status.replace(/_/g, " ")}</span>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <Stat n={latest.total_files} label="Files" tone="bg-slate-50 text-slate-700" />
        <Stat n={latest.imported_count} label="Imported" tone="bg-emerald-50 text-emerald-700" />
        <Stat n={latest.warning_count} label="Warnings" tone="bg-amber-50 text-amber-700" />
        <Stat n={latest.duplicate_count} label="Duplicates" tone="bg-slate-100 text-slate-600" />
        <Stat n={latest.failed_count} label="Failed" tone="bg-rose-50 text-rose-700" />
      </div>
      {latest.duplicate_count > 0 && (
        <p className="mt-2 text-xs text-slate-500">
          {latest.duplicate_count} file{latest.duplicate_count === 1 ? " was" : "s were"} skipped as
          duplicates (already stored).
        </p>
      )}
    </Card>
  );
}
