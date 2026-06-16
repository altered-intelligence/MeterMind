import { type Bill } from "../../api";
import BillSummaryCard from "../BillSummaryCard";
import BillAuditPanel from "../BillAuditPanel";
import BillCorrectionPanel from "../BillCorrectionPanel";
import RateBreakdownPanel from "../RateBreakdownPanel";
import ImportHistoryPanel from "../ImportHistoryPanel";
import AnalyticsSummaryCard from "../AnalyticsSummary";
import InsightsPanel from "../InsightsPanel";
import BillAiSummary from "../BillAiSummary";
import AssistantChat from "../AssistantChat";
import { EmptyState } from "../ui";

export default function DashboardView({
  bill,
  isLatest,
  version,
  onGoUpload,
  onChanged,
}: {
  bill: Bill | null;
  isLatest: boolean;
  version: number;
  onGoUpload: () => void;
  onChanged?: () => void;
}) {
  if (!bill) {
    return (
      <EmptyState
        title="No bills yet."
        hint={
          <button onClick={onGoUpload} className="font-medium text-sky-600 hover:text-sky-800">
            Upload a CoServ bill to get started →
          </button>
        }
      />
    );
  }
  return (
    <div className="space-y-6">
      <BillSummaryCard bill={bill} isLatest={isLatest} />
      <BillAuditPanel billId={bill.id} version={version} />
      <BillCorrectionPanel bill={bill} version={version} onCorrected={onChanged} />
      <RateBreakdownPanel billId={bill.id} version={version} />
      <InsightsPanel version={version} />
      <ImportHistoryPanel version={version} onGoUpload={onGoUpload} />
      <BillAiSummary billId={bill.id} version={version} />
      <AssistantChat version={version} />
      <AnalyticsSummaryCard version={version} />
    </div>
  );
}
