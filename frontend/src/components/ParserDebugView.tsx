import { useEffect, useState, type ReactNode } from "react";
import { api, type Bill, type DebugView } from "../api";
import { Card } from "./ui";

export default function ParserDebugView({ bill, defaultOpen = false }: { bill: Bill; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const [tab, setTab] = useState<"json" | "text">("json");
  const [debug, setDebug] = useState<DebugView | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setDebug(null);
    setErr(null);
  }, [bill.id]);

  useEffect(() => {
    if (open && tab === "text" && !debug) {
      api
        .getBillDebug(bill.id)
        .then(setDebug)
        .catch((e) => setErr(e instanceof Error ? e.message : "Failed to load debug text."));
    }
  }, [open, tab, debug, bill.id]);

  const json = JSON.stringify(bill, null, 2);

  function download() {
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `coserv-bill-${bill.id}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <Card
      title="Parser debug & export"
      right={
        <button onClick={() => setOpen((o) => !o)} className="text-xs font-medium text-sky-600 hover:text-sky-800">
          {open ? "Hide" : "Show"}
        </button>
      }
    >
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="text-slate-500">
          Confidence: <span className="font-medium text-slate-800">{bill.parser_confidence}</span>
        </span>
        <span className="text-slate-500">
          Parser v{bill.parser_version}
        </span>
        <span className="text-slate-500">
          Warnings: <span className="font-medium text-slate-800">{bill.warnings.length}</span>
        </span>
        <button onClick={download} className="ml-auto rounded-md border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50">
          Download JSON
        </button>
      </div>

      {bill.warnings.length > 0 && (
        <ul className="mt-3 space-y-1">
          {bill.warnings.map((w, i) => (
            <li key={i} className="text-xs">
              <span
                className={
                  w.severity === "error"
                    ? "text-rose-600"
                    : w.severity === "warning"
                    ? "text-amber-600"
                    : "text-slate-500"
                }
              >
                [{w.severity}]
              </span>{" "}
              <span className="text-slate-600">{w.message}</span>
            </li>
          ))}
        </ul>
      )}

      {open && (
        <div className="mt-4">
          <div className="mb-2 flex gap-2">
            <TabBtn active={tab === "json"} onClick={() => setTab("json")}>
              Parsed JSON
            </TabBtn>
            <TabBtn active={tab === "text"} onClick={() => setTab("text")}>
              Redacted text
            </TabBtn>
          </div>
          {tab === "json" ? (
            <pre className="max-h-96 overflow-auto rounded-lg bg-slate-900 p-3 text-xs leading-relaxed text-slate-100">
              {json}
            </pre>
          ) : err ? (
            <p className="text-sm text-rose-600">{err}</p>
          ) : !debug ? (
            <p className="text-sm text-slate-400">Loading…</p>
          ) : (
            <pre className="max-h-96 overflow-auto rounded-lg bg-slate-900 p-3 text-xs leading-relaxed text-slate-100">
              {debug.redacted_text_blocks}
            </pre>
          )}
          <p className="mt-2 text-xs text-slate-400">
            PII (account #, name, address) is redacted in this view and never logged.
          </p>
        </div>
      )}
    </Card>
  );
}

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`rounded-md px-3 py-1 text-xs font-medium ${
        active ? "bg-slate-800 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"
      }`}
    >
      {children}
    </button>
  );
}
