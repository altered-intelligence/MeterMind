// Typed client for the Home Utility Intelligence backend.
// Money values arrive as decimal *strings* (precision-preserving) or null.
// PII is hashed/redacted server-side: only last4 + redacted forms are ever returned.

const API_BASE = (import.meta as any).env?.VITE_API_BASE ?? "/api";

export type Money = string | null;
export type Confidence = "high" | "medium" | "low";

export interface ParserWarning {
  code: string | null;
  message: string | null;
  severity: string | null;
}

export interface ChargeLineItem {
  name: string;
  amount: Money;
  quantity: string | null;
  unit: string | null;
  rate: string | null;
  is_credit: boolean;
  currency: string;
}

export interface AccountSummary {
  bill_date: string | null;
  due_date: string | null;
  previous_balance: Money;
  payment_received: Money;
  balance_forward: Money;
  electric_service_total: Money;
  gas_service_total: Money;
  current_charges_total: Money;
  total_amount_due: Money;
  amount_due_after_due_date: Money;
  autopay_status: string | null;
  account_number_last4: string | null;
  account_number_redacted: string | null;
  service_address_redacted: string | null;
}

export interface UtilityAccount {
  id: number;
  provider_name: string;
  account_number_last4: string | null;
  service_address_redacted: string | null;
}

export interface ElectricService {
  service_rate: string | null;
  meter_number_last4: string | null;
  service_start_date: string | null;
  service_end_date: string | null;
  billing_days: number | null;
  last_read: number | null;
  current_read: number | null;
  multiplier: string | null;
  kwh_used: number | null;
  usage_unit: string;
  total_current_charges: Money;
  average_kwh_per_day: Money;
  effective_rate_per_kwh: Money;
  average_cost_per_day: Money;
  line_items: ChargeLineItem[];
}

export interface GasService {
  service_rate: string | null;
  meter_number_last4: string | null;
  service_start_date: string | null;
  service_end_date: string | null;
  billing_days: number | null;
  last_read: number | null;
  current_read: number | null;
  multiplier: string | null;
  ccf_used: number | null;
  usage_unit: string;
  total_current_charges: Money;
  average_ccf_per_day: Money;
  effective_rate_per_ccf: Money;
  average_cost_per_day: Money;
  line_items: ChargeLineItem[];
}

export interface SnapshotService {
  average_cost_per_day_this_month: Money;
  average_cost_per_day_last_month: Money;
  average_cost_per_day_one_year_ago: Money;
}
export interface Snapshot {
  electric?: SnapshotService;
  gas?: SnapshotService;
}

export interface Derived {
  electric_avg_kwh_per_day: Money;
  gas_avg_ccf_per_day: Money;
  effective_electric_rate: Money;
  effective_gas_rate: Money;
  electric_avg_cost_per_day: Money;
  gas_avg_cost_per_day: Money;
  total_avg_cost_per_day: Money;
  electric_percentage_of_bill: Money;
  gas_percentage_of_bill: Money;
}

export interface FieldCorrection {
  id: number;
  bill_id: number;
  field_path: string;
  original_value: string | null;
  corrected_value: string | null;
  corrected_value_type: string | null;
  reason: string | null;
  corrected_by_user_id: number | null;
  created_at: string | null;
}

export interface Bill {
  id: number;
  created_at: string | null;
  provider_name: string;
  parser_version: string | null;
  parser_confidence: Confidence;
  bill_date: string | null;
  due_date: string | null;
  billing_period_start: string | null;
  billing_period_end: string | null;
  billing_days: number | null;
  account_summary: AccountSummary;
  utility_account: UtilityAccount | null;
  electric_service: ElectricService | null;
  gas_service: GasService | null;
  snapshot: Snapshot | null;
  derived: Derived;
  warnings: ParserWarning[];
  // Present on single-bill reads: which field_paths carry a manual correction + history.
  corrected_fields?: string[];
  corrections?: FieldCorrection[];
}

export interface CorrectionsResponse {
  bill_id: number;
  supported_fields: string[];
  corrections: FieldCorrection[];
}
export interface CorrectionResult {
  success: boolean;
  correction: FieldCorrection;
  updated_bill: Bill;
  new_audit_result: AuditResult;
}

export interface DebugView {
  id: number;
  parser_version: string | null;
  parser_confidence: string | null;
  warnings: ParserWarning[];
  redacted_text_blocks: string;
}

export interface MoM {
  from: string;
  to: string;
  absolute: string;
  percent: number | null;
}
export interface BillRef {
  bill_id: number;
  bill_date: string | null;
  total_amount_due?: string | null;
  kwh_used?: number | null;
  ccf_used?: number | null;
}
export interface AnalyticsSummary {
  total_bills: number;
  latest: BillRef | null;
  latest_bill_amount: Money;
  latest_electric_usage_kwh: number | null;
  latest_gas_usage_ccf: number | null;
  month_over_month: {
    total_bill: MoM | null;
    electric_kwh: MoM | null;
    gas_ccf: MoM | null;
  };
  average_electric_effective_rate: Money;
  average_gas_effective_rate: Money;
  highest_electric_usage_bill: BillRef | null;
  highest_gas_usage_bill: BillRef | null;
  highest_total_bill: BillRef | null;
}

export interface BillSummaryResponse {
  bill_id: number;
  deterministic_summary: string;
  llm_enabled: boolean;
  llm_model: string | null;
  llm_summary: string | null;
  llm_status: string;
  redacted_input: Record<string, any>;
}

export interface Anomaly {
  type: string;
  metric?: string;
  message: string;
  severity: string;
  observed?: string | null;
  baseline_average?: string | null;
  threshold_percent?: number;
  change_percent?: number | null;
}
export interface Insights {
  summary: string;
  metrics: Record<string, string | number | null>;
  warnings: string[];
  anomalies: Anomaly[];
  recommendations: string[];
}

export type AuditStatus = "pass" | "warning" | "fail" | "skipped";
export interface AuditCheck {
  check_code: string;
  label: string;
  group: string;
  status: AuditStatus;
  severity: string;
  expected_value: string | null;
  actual_value: string | null;
  difference: string | null;
  tolerance: string | null;
  message: string;
  related_fields: string[];
}
export interface AuditResult {
  bill_id: number | null;
  audit_version: string;
  overall_status: "pass" | "warning" | "fail";
  checks: AuditCheck[];
  created_at: string | null;
}

export interface RateBreakdownCategory {
  key: string;
  label: string;
  amount: Money;
  percentage_of_service_total: Money; // fraction (e.g. "0.42"), or null
  line_item_names: string[];
}
export interface ServiceRateBreakdown {
  service_type: "electric" | "gas";
  total_current_charges: Money;
  total_categorized: Money;
  reconciles: boolean;
  categories: RateBreakdownCategory[];
  metrics: Record<string, string | null>;
  uncategorized_labels: string[];
  warnings: string[];
}
export interface RateBreakdown {
  bill_id: number | null;
  breakdown_version: string;
  electric: ServiceRateBreakdown | null;
  gas: ServiceRateBreakdown | null;
  generated_at: string | null;
}
export interface RateTrendPoint {
  bill_id: number;
  bill_date: string | null;
  electric_effective_rate_per_kwh: string | null;
  electric_energy_only_rate_per_kwh: string | null;
  electric_fixed_cost_percentage: string | null;
  electric_tax_fee_percentage: string | null;
  gas_effective_rate_per_ccf: string | null;
  gas_commodity_rate_per_ccf: string | null;
  gas_fixed_cost_percentage: string | null;
  gas_tax_fee_percentage: string | null;
}

export type ImportItemStatus = "imported" | "warning" | "duplicate" | "failed";
export interface ImportBatchItem {
  filename: string;
  status: ImportItemStatus;
  bill_id: number | null;
  bill_date: string | null;
  total_amount_due: Money;
  parser_confidence: string | null;
  warnings: string[];
  error: string | null;
}
export interface ImportBatch {
  id: number;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  total_files: number;
  imported_count: number;
  duplicate_count: number;
  failed_count: number;
  warning_count: number;
  items: ImportBatchItem[];
}

export interface AssistantStatus {
  enabled: boolean;
  model: string | null;
  sources: string[];
}

export interface ModelsInfo {
  enabled: boolean;
  active: string;
  available: string[];
}

export interface PortalStatus {
  enabled: boolean;
  browser_automation_enabled: boolean;
  available: boolean;
  message: string;
  supported_failure_modes: string[];
  constraints: string[];
}

/** Error carrying the backend's structured code (e.g. "invalid_pdf", "duplicate_bill"). */
export class ApiError extends Error {
  code: string;
  status: number;
  constructor(message: string, code: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, init);
  } catch {
    throw new ApiError("Cannot reach the backend.", "network_error", 0);
  }
  if (!res.ok) {
    let message = res.statusText;
    let code = "error";
    try {
      const body = await res.json();
      if (body?.error) {
        message = body.error.message ?? message;
        code = body.error.code ?? code;
      } else if (body?.detail) {
        message = body.detail;
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(message || `Request failed (${res.status})`, code, res.status);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  listBills: () => request<Bill[]>("/bills"),
  getBill: (id: number) => request<Bill>(`/bills/${id}`),
  getBillJson: (id: number) => request<Bill>(`/bills/${id}/json`),
  getBillDebug: (id: number) => request<DebugView>(`/bills/${id}/debug`),
  getBillAudit: (id: number) => request<AuditResult>(`/bills/${id}/audit`),
  rateBreakdown: (id: number) => request<RateBreakdown>(`/bills/${id}/rate-breakdown`),
  rateBreakdownTrends: () => request<RateTrendPoint[]>("/analytics/rate-breakdown-trends"),
  getCorrections: (id: number) => request<CorrectionsResponse>(`/bills/${id}/corrections`),
  createCorrection: (id: number, body: { field_path: string; corrected_value: string; reason?: string }) =>
    request<CorrectionResult>(`/bills/${id}/corrections`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  deleteBill: (id: number) => request<void>(`/bills/${id}`, { method: "DELETE" }),
  uploadBill: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<Bill>("/bills/upload", { method: "POST", body: form });
  },
  bulkUpload: (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    return request<ImportBatch>("/bills/bulk-upload", { method: "POST", body: form });
  },
  listImportBatches: () => request<ImportBatch[]>("/import-batches"),
  getImportBatch: (id: number) => request<ImportBatch>(`/import-batches/${id}`),
  analyticsSummary: () => request<AnalyticsSummary>("/analytics/summary"),
  analyticsElectric: () => request<any>("/analytics/electric"),
  analyticsGas: () => request<any>("/analytics/gas"),
  insights: () => request<Insights>("/analytics/insights"),
  billSummary: (id: number) => request<BillSummaryResponse>(`/bills/${id}/summary`),
  assistantStatus: () => request<AssistantStatus>("/assistant/status"),
  assistantModels: () => request<ModelsInfo>("/assistant/models"),
  setAssistantModel: (model: string) =>
    request<{ active: string }>("/assistant/model", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model }),
    }),
  portalStatus: () => request<PortalStatus>("/portal/status"),
};

// ---- formatting helpers ----

export function num(v: Money | string | number | null | undefined): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

export function money(v: Money, opts: { dash?: string } = {}): string {
  const n = num(v);
  if (n === null) return opts.dash ?? "—";
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function pct(v: Money): string {
  const n = num(v);
  return n === null ? "—" : `${(n * 100).toFixed(1)}%`;
}

export function rate(v: Money, digits = 4): string {
  const n = num(v);
  if (n === null) return "—";
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toFixed(digits)}`;
}

export function shortDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso + (iso.length === 10 ? "T00:00:00" : ""));
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function monthLabel(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso + (iso.length === 10 ? "T00:00:00" : ""));
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { month: "short", year: "2-digit" });
}
