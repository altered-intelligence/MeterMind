import { type Bill } from "../../api";
import ParserDebugView from "../ParserDebugView";
import { EmptyState } from "../ui";

export default function DebugView({ bill }: { bill: Bill | null }) {
  if (!bill) {
    return <EmptyState title="No bill selected." hint="Upload a bill to inspect parser output." />;
  }
  return <ParserDebugView bill={bill} defaultOpen />;
}
