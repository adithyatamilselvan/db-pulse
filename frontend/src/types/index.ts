export type Severity = "INFO" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type Source = "observed" | "SYNTHETIC";

export interface User {
  id: string;
  email: string;
  name: string;
  role: "admin" | "user";
}

export interface InstanceSettings {
  monitoring_interval_s: number;
  detection_interval_s: number;
  slow_threshold_ms: number;
  collection_thresholds_ms: Record<string, number>;
  operation_thresholds_ms: Record<string, number>;
  alert_cooldown_s: number;
  retention_days: number;
  monitored_databases: string[];
  collscan_alert_min_docs: number;
  high_examined_ratio: number;
  frequency_alert_count: number;
  frequency_window_s: number;
  severity_overrides: Record<string, Severity>;
}

export interface Instance {
  id: string;
  name: string;
  host_display: string;
  status: "connected" | "disconnected" | "error";
  last_check_at: string | null;
  last_error: string | null;
  server_version: string | null;
  allow_modifications: boolean;
  enabled: boolean;
  is_demo: boolean;
  created_at: string;
  settings: InstanceSettings;
  last_metrics_at?: string | null;
  last_collection_at?: string | null;
}

export interface ConnectionTest {
  ok: boolean;
  version: string | null;
  latency_ms: number | null;
  error: string | null;
  error_code: string | null;
}

export interface DbInfo { name: string; size_on_disk: number | null; empty: boolean | null }
export interface CollectionInfo {
  name: string;
  type: string;
  document_count: number | null;
  size_bytes: number | null;
  avg_obj_size: number | null;
  index_count: number | null;
  total_index_size: number | null;
  stats_available: boolean;
}

export interface IndexRow {
  name: string;
  key: Record<string, number | string>;
  unique: boolean;
  sparse: boolean;
  partial: boolean;
  ttl: boolean;
  accesses: number | null;
  accesses_since: string | null;
  size_bytes: number | null;
}
export interface IndexIssue { type: string; severity: string; indexes: string[]; message: string }
export interface IndexInvestigation {
  database: string;
  collection: string;
  indexes: IndexRow[];
  issues: IndexIssue[];
  index_stats_available: boolean;
  caveats: string[];
}

export interface Finding {
  code: string;
  severity: Severity;
  kind: string;
  message: string;
  evidence: Record<string, unknown>;
}

export interface PlanNode {
  stage: string;
  raw_stage?: string;
  depth: number;
  index_name?: string;
  key_pattern?: Record<string, unknown>;
  direction?: string;
  index_bounds?: Record<string, unknown>;
  filter?: unknown;
  n_returned?: number | null;
  docs_examined?: number | null;
  keys_examined?: number | null;
  exec_time_ms_estimate?: number | null;
  children: PlanNode[];
}

export interface PlanAnalysis {
  parsed_ok: boolean;
  namespace: string | null;
  tree: PlanNode | null;
  stage_names: string[];
  collection_scan_detected: boolean;
  index_scan_detected: boolean;
  fetch_detected: boolean;
  sort_stage_detected: boolean;
  index_names: string[];
  documents_examined: number | null;
  documents_returned: number | null;
  keys_examined: number | null;
  execution_time_ms: number | null;
  efficiency_ratio: number | null;
  rejected_plans_count: number;
  rejected_plans: { stages: string[]; summary: string }[];
  plan_summary: string;
  has_execution_stats: boolean;
  engine: string | null;
  findings: Finding[];
  max_severity: Severity;
  finding_messages: string[];
}

export interface Recommendation {
  action: "create_index" | "none";
  status: string;
  reason?: string;
  problem?: string;
  confidence?: "LOW" | "MEDIUM" | "HIGH";
  suggested_index?: { keys: [string, number][]; key_doc: Record<string, number>; command: string };
  rationale?: Record<string, unknown>;
  supporting_evidence?: string[];
  expected_benefit?: string;
  costs?: string[];
  validation_steps?: string[];
  existing_indexes: { name: string; key: Record<string, number> }[];
  notes: string[];
}

export interface ExecutionPlan {
  id: string;
  instance_id: string;
  target: string;
  database: string;
  collection: string;
  operation: string;
  fingerprint: string;
  created_at: string;
  analysis: PlanAnalysis;
  recommendation: Recommendation;
  index_analysis?: IndexInvestigation;
  spec_redacted?: { filter?: Record<string, unknown>; sort?: Record<string, unknown>; projection?: Record<string, unknown> };
  raw_explain?: unknown;
}

export interface SlowQuery {
  id: string;
  instance_id: string;
  database: string;
  collection: string;
  operation: string;
  ts: string;
  duration_ms: number;
  threshold_ms: number;
  severity: Severity;
  fingerprint: string;
  plan_summary: string;
  collection_scan: boolean;
  index_scan: boolean;
  docs_examined: number | null;
  keys_examined: number | null;
  docs_returned: number | null;
  efficiency_ratio: number | null;
  command_redacted: Record<string, unknown>;
  source: Source;
}

export interface PatternStats { avg_ms: number | null; max_ms: number | null; p95_ms: number | null; p99_ms: number | null; count: number | null }
export interface QueryPattern {
  id: string;
  instance_id: string;
  fingerprint: string;
  database: string;
  collection: string;
  operation: string;
  count: number;
  first_seen: string;
  last_seen: string;
  source: Source;
  last_plan_summary: string | null;
  stats: PatternStats;
  shape: Record<string, unknown>;
}

export interface Alert {
  id: string;
  instance_id: string;
  type: string;
  severity: Severity;
  status: "open" | "acknowledged" | "resolved";
  title: string;
  message: string;
  evidence: Record<string, unknown>;
  database: string | null;
  collection: string | null;
  fingerprint: string | null;
  occurrences: number;
  source: Source;
  created_at: string;
  last_seen: string;
}

export interface LatencySummary { avg: number | null; median: number | null; p95: number | null; p99: number | null; min: number | null; max: number | null }
export interface BenchmarkResult {
  latency_ms: LatencySummary;
  throughput_ops: number | null;
  wall_clock_s: number;
  successful: number;
  failed: number;
  errors: { message: string; count: number }[];
  docs_returned: number | null;
  docs_examined: number | null;
  keys_examined: number | null;
  latency_samples_ms: number[];
  histogram: { edges: number[]; counts: number[] };
  plan: PlanAnalysis | null;
  cancelled: boolean;
}
export interface Benchmark {
  id: string;
  instance_id: string;
  label: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  target: string;
  config: Record<string, unknown> & { iterations?: number };
  result: BenchmarkResult | null;
  warnings: string[];
  methodology: string;
  source: Source;
  created_at: string;
  progress?: { completed: number; failed: number };
  target_iterations?: number;
  error?: string;
}

export interface CompareRow {
  key: string;
  label: string;
  before: number | null;
  after: number | null;
  unit: string;
  lower_is_better: boolean;
  change_pct: number | null;
  direction: string;
}
export interface Comparison {
  id: string;
  before_id: string;
  after_id: string;
  before_label: string;
  after_label: string;
  target: string;
  rows: CompareRow[];
  plan: {
    before_stages: string[]; after_stages: string[]; before_indexes: string[]; after_indexes: string[];
    collscan_before: boolean; collscan_after: boolean; plan_changed: boolean; index_usage_changed: boolean;
  };
  verdict: "IMPROVED" | "REGRESSED" | "UNCHANGED" | "NOT_VALIDATED" | "INCONCLUSIVE";
  summary: string;
  caveats: string[];
  same_query: boolean;
  source: Source;
  created_at: string;
}

export interface ProfilerStatus {
  database: string;
  available: boolean;
  reason?: string;
  level?: number;
  level_description?: string;
  slowms?: number;
  sample_rate?: number;
  system_profile_present?: boolean;
}

export interface MetricPoint {
  ts: string;
  source: Source;
  server: {
    connections_current: number | null;
    rates: Record<string, number | null> | null;
    op_latency_ms: Record<string, number | null>;
    version: string | null;
  };
  databases: { name: string; data_size: number | null; collections: number | null; indexes: number | null }[];
  unavailable: string[];
}

export interface DemoSetupResult {
  instance: { id: string };
  real_dataset: { database: string; orders: number; customers: number; products: number; status: "created" | "already_present" };
  synthetic_records: { status: "created" | "already_present" | "refreshed" };
}

export interface Dashboard {
  period: { start: string; end: string };
  instances: { total: number; connected: number; items: Instance[] };
  slow_queries: { total: number; avg_ms: number | null; p95_ms: number | null; p99_ms: number | null; note: string };
  collection_scans: number;
  index_usage: { index_scans: number; collection_scans: number; index_scan_share: number | null };
  active_alerts: { open: number; by_severity: Record<string, number> };
  recent_investigations: { id: string; title: string; updated_at: string; instance_id: string }[];
  trend: { ts: string; count: number; avg_ms: number; p95_ms: number; p99_ms: number }[];
  includes_synthetic: boolean;
}

export interface AiAnswer {
  problem_summary: string;
  observed_evidence: string[];
  likely_causes: string[];
  possible_optimizations: string[];
  risks_tradeoffs: string[];
  validation_procedure: string[];
  confidence: "LOW" | "MEDIUM" | "HIGH";
  mode: "local" | "cohere";
  fallback_reason: string | null;
  model: string | null;
  evidence_refs: { type: string; id: string; label: string }[];
  hypothesis_notice: string;
}
export interface AiStatus { mode: "local" | "cohere"; requested_provider: string; cohere_configured: boolean; model: string | null; note: string | null }
export interface ConversationSummary { id: string; title: string; updated_at: string; instance_id: string }
export interface ConversationMessage { role: "user" | "assistant"; content: string | AiAnswer; ts: string; mode?: string }
export interface Conversation extends ConversationSummary { messages: ConversationMessage[] }

export interface ReportMeta { id: string; title: string; created_at: string; includes_synthetic_data: boolean; period: { start: string; end: string } }
export interface IndexChange { id: string; action: "create" | "drop"; database: string; collection: string; index_name: string; keys?: [string, number][]; created_at: string }
