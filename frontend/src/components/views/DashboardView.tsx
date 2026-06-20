import { type Bill } from "../../api";
import BillSummaryCard from "../BillSummaryCard";
import WhatChangedPanel from "../WhatChangedPanel";
import ForecastPanel from "../ForecastPanel";
import BillAuditPanel from "../BillAuditPanel";
import BillCorrectionPanel from "../BillCorrectionPanel";
import ParserMetadataPanel from "../ParserMetadataPanel";
import RateBreakdownPanel from "../RateBreakdownPanel";
import WeatherSettingsPanel from "../WeatherSettingsPanel";
import WeatherContextPanel from "../WeatherContextPanel";
import ExportPanel from "../ExportPanel";
import InvestigationQuestionsPanel from "../InvestigationQuestionsPanel";
import ConnectorSettingsPanel from "../ConnectorSettingsPanel";
import ImportHistoryPanel from "../ImportHistoryPanel";
import AnalyticsSummaryCard from "../AnalyticsSummary";
import InsightsPanel from "../InsightsPanel";
import BillSummaryExplanationPanel from "../BillSummaryExplanationPanel";
import BillAiSummary from "../BillAiSummary";
import PrivacySettingsPanel from "../PrivacySettingsPanel";
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
      <WhatChangedPanel billId={bill.id} version={version} onGoUpload={onGoUpload} />
      <ForecastPanel version={version} onGoUpload={onGoUpload} />
      <BillAuditPanel billId={bill.id} version={version} />
      <ParserMetadataPanel bill={bill} version={version} onChanged={onChanged} />
      <BillCorrectionPanel bill={bill} version={version} onCorrected={onChanged} />
      <RateBreakdownPanel billId={bill.id} version={version} />
      <InvestigationQuestionsPanel billId={bill.id} version={version} />
      <WeatherContextPanel billId={bill.id} version={version} />
      <WeatherSettingsPanel version={version} onChanged={onChanged} />
      <InsightsPanel version={version} />
      <ExportPanel billId={bill.id} />
      <ConnectorSettingsPanel onGoUpload={onGoUpload} />
      <PrivacySettingsPanel version={version} />
      <ImportHistoryPanel version={version} onGoUpload={onGoUpload} />
      <BillSummaryExplanationPanel billId={bill.id} version={version} />
      <BillAiSummary billId={bill.id} version={version} />
      <AssistantChat version={version} />
      <AnalyticsSummaryCard version={version} />
    </div>
  );
}
