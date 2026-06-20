import { useEffect, useMemo, useState } from "react";
import { api, type Bill } from "./api";
import Nav, { type View } from "./components/Nav";
import BillSelector from "./components/BillSelector";
import ErrorBanner from "./components/ErrorBanner";
import UploadView from "./components/views/UploadView";
import InsightsView from "./components/views/InsightsView";
import DashboardView from "./components/views/DashboardView";
import ServiceDetailView from "./components/views/ServiceDetailView";
import TrendsView from "./components/views/TrendsView";
import DebugView from "./components/views/DebugView";

const TITLES: Record<View, string> = {
  insights: "Insights",
  upload: "Upload bill",
  dashboard: "Dashboard",
  electric: "Electric service",
  gas: "Gas service",
  trends: "Trends",
  debug: "Parser debug",
};
const PER_BILL: View[] = ["dashboard", "electric", "gas", "debug"];

export default function App() {
  const [bills, setBills] = useState<Bill[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [view, setView] = useState<View>("dashboard");
  const [loading, setLoading] = useState(true);
  const [connError, setConnError] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);

  async function refresh() {
    setConnError(null);
    try {
      setBills(await api.listBills());
    } catch (e) {
      setConnError(e instanceof Error ? e.message : "Cannot reach the backend.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  const latest = bills.length ? bills[bills.length - 1] : null;
  const selected = useMemo(
    () => bills.find((b) => b.id === selectedId) ?? latest,
    [bills, selectedId, latest]
  );

  function onUploaded(bill: Bill) {
    setBills((prev) =>
      prev.some((b) => b.id === bill.id)
        ? prev.map((b) => (b.id === bill.id ? bill : b))
        : [...prev, bill]
    );
    setSelectedId(bill.id);
    setRefreshTick((t) => t + 1);
  }

  async function removeBill(id: number) {
    try {
      await api.deleteBill(id);
    } catch {
      /* best-effort; the list refresh below reflects reality */
    }
    setBills((prev) => prev.filter((b) => b.id !== id));
    if (selectedId === id) setSelectedId(null);
    setRefreshTick((t) => t + 1);
  }

  const showSelector = PER_BILL.includes(view) && bills.length > 0;

  function renderView() {
    switch (view) {
      case "insights":
        return <InsightsView version={refreshTick} onGoUpload={() => setView("upload")} />;
      case "upload":
        return (
          <UploadView
            onUploaded={onUploaded}
            onBulkComplete={() => {
              refresh();
              setRefreshTick((t) => t + 1);
            }}
            onOpenBill={(id) => {
              setSelectedId(id);
              setView("dashboard");
            }}
          />
        );
      case "dashboard":
        return (
          <DashboardView
            bill={selected}
            isLatest={!!selected && selected.id === latest?.id}
            version={refreshTick}
            onGoUpload={() => setView("upload")}
            onChanged={() => {
              refresh();
              setRefreshTick((t) => t + 1);
            }}
          />
        );
      case "electric":
        return <ServiceDetailView kind="electric" bill={selected} />;
      case "gas":
        return <ServiceDetailView kind="gas" bill={selected} />;
      case "trends":
        return <TrendsView bills={bills} />;
      case "debug":
        return <DebugView bill={selected} />;
    }
  }

  return (
    <div className="flex min-h-screen bg-slate-50 text-slate-800">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:shadow focus:ring-2 focus:ring-sky-400"
      >
        Skip to content
      </a>
      {/* Sidebar (desktop) */}
      <aside className="hidden w-56 shrink-0 flex-col border-r border-slate-200 bg-white md:flex">
        <div className="border-b border-slate-100 px-4 py-4">
          <h1 className="text-base font-bold leading-tight text-slate-900">
            Home Utility
            <br />
            Intelligence
          </h1>
          <p className="mt-1 text-[11px] text-slate-400">CoServ console · deterministic parsing</p>
        </div>
        <Nav view={view} onSelect={setView} orientation="vertical" />
        <div className="mt-auto px-4 py-4 text-[11px] leading-relaxed text-slate-400">
          Local &amp; private. Account/meter numbers are hashed — only the last 4 digits are shown.
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Mobile top bar + nav */}
        <div className="border-b border-slate-200 bg-white md:hidden">
          <div className="px-4 py-3">
            <h1 className="text-base font-bold text-slate-900">Home Utility Intelligence</h1>
          </div>
          <Nav view={view} onSelect={setView} orientation="horizontal" />
        </div>

        <main id="main-content" className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">
          <header className="mb-5 flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-xl font-semibold text-slate-900">{TITLES[view]}</h2>
            {showSelector && (
              <div className="flex items-center gap-3">
                <BillSelector bills={bills} selectedId={selectedId} onSelect={setSelectedId} />
                {selected && (
                  <button
                    onClick={() => {
                      if (confirm("Delete this bill? This cannot be undone.")) removeBill(selected.id);
                    }}
                    className="rounded text-xs text-slate-500 hover:text-rose-600 focus:outline-none focus:ring-2 focus:ring-rose-300"
                  >
                    Delete
                  </button>
                )}
              </div>
            )}
          </header>

          <ErrorBanner
            message={connError ? `Connection error: ${connError}. Is the backend running on :8077?` : null}
            onRetry={refresh}
          />

          {loading ? <p className="text-slate-400">Loading…</p> : renderView()}
        </main>
      </div>
    </div>
  );
}
