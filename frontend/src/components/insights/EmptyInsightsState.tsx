export default function EmptyInsightsState({ onGoUpload }: { onGoUpload: () => void }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-200 bg-white px-6 py-12 text-center">
      <h2 className="text-lg font-semibold text-slate-800">No bills yet</h2>
      <p className="mx-auto mt-2 max-w-md text-sm text-slate-500">
        Upload a CoServ bill (PDF) and this page will summarize the latest statement, what changed,
        the rate breakdown, any anomalies, and questions worth investigating — all in one place.
      </p>
      <button
        onClick={onGoUpload}
        className="mt-4 rounded-md bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-700 focus:outline-none focus:ring-2 focus:ring-sky-300"
      >
        Upload a bill →
      </button>
    </div>
  );
}
