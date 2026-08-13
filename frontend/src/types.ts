// ── Statistics types ──────────────────────────────────────────────────────

export interface SetStatsResponse {
  total_sets: number
  total_issues: number
  clean_rate: number
  avg_issues_per_set: number
  severity_dist: Record<string, number>
  status_dist: Record<string, number>
  trends: { date: string; count: number }[]
  top_categories: { category: string; count: number }[]
}

// ── EMC Standards types ─────────────────────────────────────────────────

export interface StandardClause {
  clause: string
  title: string
  description: string
  limit_table: Record<string, string>[]
}

export interface EmcStandard {
  id: string
  code: string
  title: string
  organization: string
  category: string
  version: string
  clauses: StandardClause[]
  is_builtin: boolean
  normalized_code: string
  source_filename: string
  file_sha256: string
  page_count: number
  knowledge_status: 'manual' | 'pending' | 'processing' | 'ready' | 'failed'
  knowledge_error: string
  knowledge_meta: Record<string, unknown>
  chunk_count: number
  graph_status: 'not_started' | 'extracting' | 'pending_review' | 'published' | 'failed'
  graph_error: string
  graph_meta: Record<string, unknown>
  graph_clause_count: number
  graph_requirement_count: number
  graph_pending_count: number
  graph_confirmed_count: number
  graph_rejected_count: number
  latest_release_id: string
  latest_release_number: number
  release_count: number
  selected_release_id: string
  selected_release_number: number
  created_by: string
  created_at: string
  updated_at: string
}

export interface StandardRequirementParameter {
  id?: string
  name: string
  symbol: string
  comparator: string
  value: string
  value_min: string
  value_max: string
  unit: string
  raw_text: string
}

export interface StandardGraphRequirement {
  id: string
  standard_id: string
  clause_id: string
  clause_number: string
  clause_title: string
  requirement_type: string
  test_item: string
  statement: string
  original_statement: string
  interpretation_zh: string
  interpretation_status: 'pending' | 'confirmed' | 'rejected'
  applicability: string
  evidence_quote: string
  page_start: number
  page_end: number
  confidence: number
  review_status: 'pending' | 'confirmed' | 'rejected'
  review_comment: string
  reviewed_by: string
  reviewed_at: string
  parameters: StandardRequirementParameter[]
  relations: Array<{ relation_type: string; target_ref: string }>
}

export interface StandardGraphClause {
  id: string
  clause_number: string
  title: string
  page_start: number
  page_end: number
  requirements: StandardGraphRequirement[]
}

export interface StandardKnowledgeChunk {
  id: string
  standard_id: string
  chunk_index: number
  clause: string
  title: string
  page_start: number
  page_end: number
  content: string
  structured: Record<string, unknown>
  created_at: string
  score?: number
}

// ── Auth types ──────────────────────────────────────────────────────────

export type UserRole = 'admin' | 'reviewer' | 'standard_reviewer' | 'viewer'

export interface User {
  employee_id: string
  role: UserRole
  name?: string
  created_at?: string
}

export interface LoginRequest {
  employee_id: string
}

export interface LoginResponse {
  token: string
  user: User
}

export interface UserListResponse {
  users: User[]
  total: number
}

// ── Statistics types ──────────────────────────────────────────────────────

export interface Tag {
  id: string
  name: string
  created_by: string
  created_at: string
}

export interface ProjectGroup {
  id: string
  name: string
  description?: string
  members: string[]
  created_by: string
  category?: string
  created_at: string
}

// ── DocumentSet / Pipeline types ──

export interface DocumentSetListItem {
  set_id: string; status: string; created_at: string
  employee_id: string; doc_count: number
  project_group_id?: string; project_group_name?: string
}

export type DocType = 'order_form' | 'test_plan' | 'original_records' | 'final_report' | 'test_standard'

export interface SetDocument {
  doc_id: string; set_id: string; doc_type: DocType; filename: string
  file_size_kb: number; doc_version: number; parent_doc_id: string
  upload_order: number; extraction_status: string; created_at: string
  extraction_quality?: string; extraction_meta?: Record<string, unknown>
  reviewed_by?: string; reviewed_at?: string
}

export interface DocumentSetOverview {
  set_id: string; status: string; employee_id: string; created_at: string
  source_status?: string; workflow_status?: string
  project_group_id?: string; project_group_name?: string
  files: FileStatus[]; missing_types: string[]; is_ready: boolean
  standards: EmcStandard[]
  standard_review_enabled: boolean
}

export interface FileStatus {
  doc_id: string; doc_type: DocType; label: string; filename: string
  file_size_kb: number; latest_version: number; extraction_status: string
  extraction_quality?: string; extraction_meta?: Record<string, unknown>
  extraction_error?: string; required: boolean; versions: VersionInfo[]
  reviewed_by?: string; reviewed_at?: string
}

export interface VersionInfo { doc_id: string; doc_version: number; filename: string; created_at: string }

export interface SetDocumentCreateRequest {
  doc_type: DocType; filename: string; file_size_kb?: number; replace_doc_id?: string
}

export interface PipelineReviewResult {
  set_id: string; run_id?: string; status: string; is_clean: boolean; duration_seconds: number
  issues: { critical: number; warning: number; info: number }; errors: string[]
  standard_review_enabled?: boolean
  standard_review_notice?: string
}

export interface PipelineIssue {
  id: string; severity: string; category: string; field_name: string
  description: string; sources: Record<string,string>
  source_step: string; validation_type: string; resolution: string
  human_status: string; human_comment: string; annotated_by: string; annotated_at: string
  check_id: string  // C01-C11 (report-internal) or X01-X10 (cross-document)
  trace?: Record<number, TraceSource>
  document_evidence?: Record<string, DocumentEvidence>
}


export type EvidenceGraphFindingStatus =
  | 'confirmed_pass' | 'confirmed_error' | 'unresolved'
  | 'confirmed_advisory' | 'unresolved_advisory' | 'not_applicable'

export interface EvidenceGraphRunSummary {
  graph_id: string
  set_id: string
  scope: 'run' | 'definition'
  status: string
  graph_version: string
  rule_version: string
  standard_release_ids: string[]
  model_manifest: Record<string, unknown>
  node_count: number
  edge_count: number
  finding_count: number
  error_count: number
  unresolved_count: number
  created_at: string
  updated_at: string
}

export interface EvidenceGraphEvidence {
  evidence_id: string
  graph_id: string
  doc_id: string
  doc_type: string
  filename: string
  state: string
  page_number: number
  sheet_name: string
  cell_range: string
  table_id: string
  bbox: number[]
  exact_quote: string
  extraction_method: string
  model_version: string
  confidence: number
  metadata: Record<string, unknown>
}

export interface EvidenceGraphNode {
  node_id: string
  graph_id: string
  node_type: string
  label: string
  canonical_key: string
  resolution_status: string
  properties: Record<string, any>
  created_by: string
}

export interface EvidenceGraphEdge {
  edge_id: string
  graph_id: string
  source_node_id: string
  target_node_id: string
  relation_type: string
  status: string
  origin: string
  confidence: number
  evidence_ids: string[]
  rationale: string
  metadata: Record<string, unknown>
}

export interface EvidenceGraphFinding {
  finding_id: string
  graph_id: string
  check_id: string
  status: EvidenceGraphFindingStatus
  severity: 'error' | 'warning' | 'info'
  title: string
  description: string
  subject_node_ids: string[]
  evidence_ids: string[]
  dedupe_key: string
  rule_version: string
  metadata: Record<string, any>
}

export interface EvidenceGraphEvent {
  event_id: string
  graph_id: string
  event_type: string
  actor_type: string
  actor_id: string
  subject_type: string
  subject_id: string
  old_state: string
  new_state: string
  reason_code: string
  evidence_ids: string[]
  payload: Record<string, unknown>
}

export interface EvidenceGraphSnapshot {
  run: {
    graph_id: string
    scope: 'run' | 'definition'
    set_id: string
    status: string
    graph_version: string
    rule_version: string
    standard_release_ids: string[]
    model_manifest: Record<string, unknown>
    metadata: Record<string, unknown>
  }
  evidence: EvidenceGraphEvidence[]
  nodes: EvidenceGraphNode[]
  edges: EvidenceGraphEdge[]
  findings: EvidenceGraphFinding[]
  events: EvidenceGraphEvent[]
  decisions: Array<{
    graph_id: string; finding_id: string
    decision: 'confirmed' | 'dismissed' | 'advisory' | 'unresolved'
    resolution_code: '' | 'report_revision' | 'raw_record_supplement' | 'source_correction'
      | 'not_applicable' | 'false_positive' | 'deferred' | 'other'
    resolution_status: '' | 'open' | 'not_applicable' | 'dismissed' | 'deferred' | 'resolved'
    comment: string; actor_id: string; created_at: string
  }>
}

export type DocumentEvidenceStatus = 'not_involved' | 'evidence' | 'supporting' | 'conflict' | 'missing'

export interface DocumentEvidenceEntry {
  source_index: number
  source_key: string
  value: string
  trace?: TraceSource
}

export interface RawRecordEvidenceFile {
  display_name: string
  filename: string
  test_item_code: string
  test_item_name: string
  sequence: string
  test_mode: string
  relation?: 'related' | 'searched'
}

export interface ArchiveMemberInventoryItem {
  member_index: number
  source_filename: string
  test_item_name: string
  sequence: string
  test_mode: string
  sample_id: string
  conclusion: string
}

export interface ArchiveMemberInventory {
  doc_id: string
  filename: string
  total: number
  members: ArchiveMemberInventoryItem[]
}

export interface DocumentEvidence {
  doc_type: string
  label: string
  doc_id: string
  filename: string
  status: DocumentEvidenceStatus
  entries: DocumentEvidenceEntry[]
  member_files: RawRecordEvidenceFile[]
}

export interface TraceSource {
  doc_type: string
  doc_id?: string
  filename?: string
  path: string
  raw_value: string
  confidence: number
  location: string
  method?: string
  match_strategy?: string
}

export interface VersionDiffItem {
  field_name: string; display_label: string; old_value: string; new_value: string; changed: boolean
}

// ── Extraction Review types ───────────────────────────────────────────

export interface ExtractionField {
  field_name: string; field_value: string; source_text: string; confidence: number
  source_location: string
}

export interface DocExtraction {
  status: string; doc_id: string; filename: string; error?: string
  extraction_quality?: string; extraction_meta?: Record<string, unknown>
  fields: ExtractionField[]; structured: Record<string, any> | null
  cell_map: Record<string, string> | null
  cell_values: Record<string, string> | null
}

export interface SetExtractionsResponse {
  set_id: string; extractions: Record<string, DocExtraction>
}

/** Single-document extraction response from GET .../documents/{doc_id}/extraction */
export interface DocumentExtractionResponse extends DocExtraction {
  set_id: string; doc_type: string
}

export interface PipelineProgressStep {
  id: number; label: string; status: 'wait' | 'active' | 'done'; time?: string
}

export interface PipelineResultSummary {
  set_id: string; run_id?: string; status: string; is_clean: boolean; duration_seconds: number
  issues: { critical: number; warning: number; info: number }; errors: string[]
  standard_review_enabled?: boolean
  standard_review_notice?: string
}

export interface PipelineMetric {
  metric_type: string
  stage: string
  module_key: string
  status: string
  duration_ms: number
  input_length: number
  output_length: number
  retry_count: number
  parse_status: string
  issue_count: number
  details: Record<string, any>
  created_at: string
}

export interface PipelineMetricsResponse {
  set_id: string
  run_id: string
  metrics: PipelineMetric[]
  summary: {
    total_metrics?: number
    failed?: number
    partial?: number
    duration_ms?: number
    module_count?: number
  }
}
