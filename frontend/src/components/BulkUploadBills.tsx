import { useRef, useState } from "react";
import {
  api,
  money,
  shortDate,
  type ImportBatch,
  type ImportBatchItem,
  type ImportItemStatus,
} from "../api";
import { Card } from "./ui";

const STATUS_STYLE: Record<ImportItemStatus, { chip: string; label: string }> = {
  imported: { chip: "bg-emerald-100 text-emerald-700 ring-emerald-200", label: "Imported" },
  warning: { chip: "bg-amber-100 text-amber-700 ring-amber-200", label: "Warning" },
  duplicate: { chip: "bg-slate-100 text-slate-600 ring-slate-200", label: "Duplicate" },
  failed: { chip: "bg-rose-100 text-rose-700 ring-rose-200", label: "Failed" },
};

function StatusChip({ status }: { status: ImportItemStatus }) {
  const s = STATUS_STYLE[status];
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ${s.chip}`}>
      {s.label}
    </span>
  );
}

function CountPill({ n, label, tone }: { n: number; label: string; tone: string }) {
  return (
    <div className={`rounded-lg px-3 py-2 text-center ${tone}`}>
      <div className="text-lg font-semibold">{n}</div>
      <div className="text-[11px] uppercase tracking-wide">{label}</div>
    </div>
  );
}

function ResultTable({
  batch,
  onOpenBill,
}: {
  batch: ImportBatch;
  onOpenBill?: (id: number) => void;
}) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <CountPill n={batch.total_files} label="Files" tone="bg-slate-50 text-slate-700" />
        <CountPill n={batch.imported_count} label="Imported" tone="bg-emerald-50 text-emerald-700" />
        <CountPill n={batch.warning_count} label="Warnings" tone="bg-amber-50 text-amber-700" />
        <CountPill n={batch.duplicate_count} label="Duplicates" tone="bg-slate-100 text-slate-600" />
        <CountPill n={batch.failed_count} label="Failed" tone="bg-rose-50 text-rose-700" />
      </div>

      <div className="overflow-hidden rounded-lg border border-slate-100">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs text-slate-500">
            <tr>
              <th className="px-3 py-2 text-left font-medium">File</th>
              <th className="px-3 py-2 text-left font-medium">Status</th>
              <th className="px-3 py-2 text-left font-medium">Bill date</th>
              <th className="px-3 py-2 text-right font-medium">Total</th>
              <th className="px-3 py-2 text-left font-medium">Detail</th>
            </tr>
          </thead>
          <tbody>
            {batch.items.map((it: ImportBatchItem, i) => (
              <tr key={`${it.filename}-${i}`} className="border-t border-slate-50">
                <td className="px-3 py-2 text-slate-700">
                  <span className="block max-w-[16rem] truncate" title={it.filename}>
                    {it.filename}
                  </span>
                </td>
                <td className="px-3 py-2">
                  <StatusChip status={it.status} />
                </td>
                <td className="px-3 py-2 text-slate-500">{shortDate(it.bill_date)}</td>
                <td className="px-3 py-2 text-right text-slate-700">
                  {it.total_amount_due ? money(it.total_amount_due) : "—"}
                </td>
                <td className="px-3 py-2 text-xs text-slate-500">
                  {it.bill_id && (it.status === "imported" || it.status === "warning") ? (
                    <button
                      onClick={() => onOpenBill?.(it.bill_id as number)}
                      className="font-medium text-sky-600 hover:text-sky-800"
                    >
                      View bill #{it.bill_id} →
                    </button>
                  ) : it.status === "duplicate" ? (
                    <span title={it.error ?? undefined}>
                      {it.bill_id ? (
                        <button
                          onClick={() => onOpenBill?.(it.bill_id as number)}
                          className="text-slate-500 underline decoration-dotted hover:text-slate-700"
                        >
                          already stored as #{it.bill_id}
                        </button>
                      ) : (
                        "already stored"
                      )}
                    </span>
                  ) : it.error ? (
                    <span className="text-rose-600">{it.error}</span>
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function BulkUploadBills({
  onComplete,
  onOpenBill,
}: {
  onComplete?: () => void;
  onOpenBill?: (id: number) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ImportBatch | null>(null);

  function addFiles(list: FileList | null) {
    if (!list) return;
    setError(null);
    const incoming = Array.from(list).filter((f) => f.name.toLowerCase().endsWith(".pdf"));
    const rejected = list.length - incoming.length;
    if (rejected > 0) setError(`${rejected} non-PDF file(s) were skipped.`);
    // De-dupe by name+size so the same file isn't queued twice.
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`));
      return [...prev, ...incoming.filter((f) => !seen.has(`${f.name}:${f.size}`))];
    });
  }

  function removeAt(idx: number) {
    setFiles((prev) => prev.filter((_, i) => i !== idx));
  }

  async function uploadAll() {
    if (files.length === 0) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const batch = await api.bulkUpload(files);
      setResult(batch);
      setFiles([]);
      if (inputRef.current) inputRef.current.value = "";
      onComplete?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Bulk import failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Bulk import bills">
      <p className="mb-3 text-xs text-slate-500">
        Backfill history by uploading many CoServ PDFs at once. Each file is processed
        independently — duplicates are skipped and one bad file won’t stop the rest.
      </p>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          addFiles(e.dataTransfer.files);
        }}
        className={`flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed px-6 py-6 text-center transition ${
          dragging ? "border-sky-400 bg-sky-50" : "border-slate-300 bg-slate-50"
        }`}
      >
        <p className="text-sm text-slate-600">Drag &amp; drop multiple CoServ PDFs here, or</p>
        <button
          type="button"
          disabled={busy}
          onClick={() => inputRef.current?.click()}
          className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-700 disabled:opacity-50"
        >
          Choose PDFs
        </button>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          multiple
          className="hidden"
          onChange={(e) => addFiles(e.target.files)}
        />
      </div>

      {error && <p className="mt-3 text-sm text-amber-600">{error}</p>}

      {files.length > 0 && (
        <div className="mt-4">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              {files.length} file{files.length === 1 ? "" : "s"} ready
            </h3>
            <button
              onClick={() => setFiles([])}
              disabled={busy}
              className="text-xs text-slate-400 hover:text-slate-600 disabled:opacity-50"
            >
              Clear all
            </button>
          </div>
          <ul className="mb-3 max-h-48 space-y-1 overflow-auto">
            {files.map((f, i) => (
              <li
                key={`${f.name}-${i}`}
                className="flex items-center justify-between rounded-md bg-slate-50 px-2 py-1 text-xs"
              >
                <span className="max-w-[18rem] truncate text-slate-600" title={f.name}>
                  {f.name}
                </span>
                <button
                  onClick={() => removeAt(i)}
                  disabled={busy}
                  className="ml-2 text-slate-400 hover:text-rose-600 disabled:opacity-50"
                  aria-label={`Remove ${f.name}`}
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
          <button
            onClick={uploadAll}
            disabled={busy}
            className="w-full rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-700 disabled:opacity-50"
          >
            {busy ? "Importing…" : `Upload all ${files.length} file${files.length === 1 ? "" : "s"}`}
          </button>
        </div>
      )}

      {busy && (
        <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
          <div className="h-full w-1/3 animate-pulse rounded-full bg-sky-500" />
        </div>
      )}

      {result && (
        <div className="mt-5">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Import results
          </h3>
          <ResultTable batch={result} onOpenBill={onOpenBill} />
        </div>
      )}
    </Card>
  );
}
