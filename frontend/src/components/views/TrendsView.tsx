import { type Bill } from "../../api";
import UsageCharts from "../UsageCharts";
import { EmptyState } from "../ui";

export default function TrendsView({ bills }: { bills: Bill[] }) {
  if (bills.length === 0) {
    return <EmptyState title="No bills yet." hint="Upload bills to see usage and cost trends over time." />;
  }
  return (
    <div className="space-y-4">
      {bills.length < 2 && (
        <div className="rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-800">
          Only one bill so far — trends will become meaningful after you upload more bills.
          The charts below show the single data point for now.
        </div>
      )}
      <UsageCharts bills={bills} />
    </div>
  );
}
