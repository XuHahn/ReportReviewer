export type UserRole = 'admin' | 'reviewer' | 'standard_reviewer' | 'viewer'
export type DocType = 'order_form' | 'test_plan' | 'original_records' | 'final_report' | 'test_standard'
export type Severity = 'error' | 'warning' | 'info'

export type VisionModelStatus = 'ready' | 'preparing' | 'unavailable'
export interface HealthStatus {
  status: string
  vision?: {
    status: VisionModelStatus | string
    configured?: boolean
    model?: string
    reason?: string
  }
}

export interface User {
  employee_id: string
  name?: string
  role: UserRole
  is_active?: boolean
  created_at?: string
}

export interface LoginResponse { token: string; user: User }
export interface UserListResponse { users: User[]; total: number }

export interface ProjectGroup {
  id: string
  name: string
  description?: string
  category?: string
  member_ids?: string[]
  members?: string[]
  member_count?: number
  created_at?: string
}

export interface EmcStandard {
  id: string
  code: string
  title: string
  organization?: string
  category?: string
  version?: string
  filename?: string
  knowledge_status?: string
  graph_status?: string
  chunks_count?: number
  requirements_count?: number
  pending_requirements_count?: number
  published_release_count?: number
  created_at?: string
}

export interface StandardReference {
  reference_code: string
  normalized_code: string
  sources: DocType[]
  evidence: string[]
  resolution?: 'selected' | 'skipped' | 'unresolved'
  standard_id?: string
  skip?: StandardReferenceSkip
}

export interface StandardReferenceSkip {
  reference_code: string
  normalized_code: string
  reason: string
  added_by?: string
  created_at?: string
}

export interface StandardReferenceResolution {
  set_id: string
  references: StandardReference[]
  resolved: StandardReference[]
  unresolved: StandardReference[]
  skips: StandardReferenceSkip[]
}

export interface StandardRequirementParameter {
  name: string
  value?: string
  unit?: string
  operator?: string
  evidence?: string
}

export interface StandardGraphClause {
  clause_id: string
  clause_number?: string
  title?: string
  parent_clause_id?: string
  level?: number
  text?: string
}

export interface StandardGraphRequirement {
  requirement_id: string
  clause_id?: string
  clause_number?: string
  clause_title?: string
  requirement_type?: string
  test_item?: string
  statement: string
  interpretation_zh?: string
  applicability?: string
  parameters?: StandardRequirementParameter[]
  review_status?: 'pending' | 'confirmed' | 'rejected'
  review_comment?: string
  evidence_quote?: string
}

export interface StandardKnowledgeChunk {
  chunk_id: string
  clause_number?: string
  clause_title?: string
  text: string
  page_number?: number
}

export interface SetDocument {
  doc_id: string
  doc_type: DocType
  filename: string
  file_size?: number
  file_size_kb?: number
  page_count?: number
  status?: string
  extraction_status?: string
  extraction_quality?: string
  extraction_error?: string
  reviewed_by?: string
  reviewed_at?: string
  doc_version?: number
  latest_version?: number
  created_at?: string
}

export interface ExtractionGateBlocker {
  doc_type: DocType | string
  label: string
  reason: string
}

export interface ExtractionQualityGate {
  status: 'pass' | 'block' | 'warn' | string
  title: string
  note: string
  blockers: ExtractionGateBlocker[]
  manual_review_required: boolean
  auto_gate_allowed: boolean
}

export interface DocumentSetListItem {
  set_id: string
  status: string
  created_at: string
  updated_at?: string
  created_by?: string
  project_group_id?: string
  project_group_name?: string
  documents?: SetDocument[]
  documents_count?: number
  doc_count?: number
  latest_run_status?: string
  finding_count?: number
  pending_count?: number
  title?: string
  source_status?: string
}

export interface DocumentSetOverview extends DocumentSetListItem {
  documents: SetDocument[]
  extraction_gate?: ExtractionQualityGate
  standards?: EmcStandard[]
  locked_at?: string
  revision?: number
}

export interface SetStatsResponse {
  total_sets: number
  draft_sets: number
  locked_sets: number
  reviewed_sets: number
  total_issues?: number
  confirmed_issues?: number
  pass_rate?: number
  average_review_seconds?: number
  status_counts?: Record<string, number>
  avg_issues_per_set?: number
  severity_dist?: Record<string, number>
  trends?: Array<{ date: string; count: number }>
  top_categories?: Array<{ category: string; count: number }>
}

export interface ExtractionField {
  key?: string
  field_name?: string
  label?: string
  value: unknown
  confidence?: number
  exact_quote?: string
  page_number?: number
  sheet_name?: string
  bbox?: number[]
  confirmed?: boolean
  source?: string
}

export interface DocExtraction {
  doc_id: string
  doc_type: DocType
  filename: string
  status: string
  method?: string
  confidence?: number
  fields?: ExtractionField[] | Record<string, unknown>
  data?: Record<string, unknown>
  error?: string
  plain_text?: string
}

export interface SetExtractionsResponse { set_id: string; extractions: DocExtraction[] }
export interface DocumentExtractionResponse extends DocExtraction { overrides?: Record<string, string> }

export interface EvidenceGraphRunSummary {
  graph_id: string
  set_id: string
  status: string
  created_at?: string
  updated_at?: string
  completed_at?: string
  finding_count?: number
  pending_count?: number
  metadata?: Record<string, unknown>
}

export interface EvidenceGraphEvidence {
  evidence_id: string
  doc_id: string
  doc_type: DocType
  filename: string
  page_number?: number
  sheet_name?: string
  cell_range?: string
  bbox?: number[]
  exact_quote: string
  extraction_method?: string
  metadata?: Record<string, unknown>
}

export interface FindingLlmSupplement {
  status?: 'ready' | 'not_generated' | 'unavailable' | string
  judgment?: string
  basis?: string
  uncertainty?: string
  handling?: string
  evidence_ids?: string[]
}

export interface FindingPresentation {
  version?: string
  status_key?: string
  status_label?: string
  subject?: string
  checked?: string
  issue?: string
  impact?: string
  llm_supplement?: FindingLlmSupplement
}

export interface EvidenceGraphNode {
  node_id: string
  node_type: string
  label?: string
  value?: unknown
  evidence_ids?: string[]
  metadata?: Record<string, unknown>
}

export interface EvidenceGraphEdge {
  edge_id: string
  source_node_id: string
  target_node_id: string
  relation: string
  evidence_ids?: string[]
  metadata?: Record<string, unknown>
}

export interface EvidenceGraphFinding {
  finding_id: string
  check_id: string
  status: string
  severity: Severity
  title: string
  description: string
  subject_node_ids: string[]
  evidence_ids: string[]
  metadata?: Record<string, any>
}

export interface FindingDecision {
  finding_id: string
  decision: 'confirmed' | 'dismissed' | 'advisory' | 'unresolved'
  comment: string
  resolution_code?: string
  resolution_status?: string
  actor_id?: string
  created_at?: string
}

export interface EvidenceGraphSnapshot {
  run: EvidenceGraphRunSummary
  evidence: EvidenceGraphEvidence[]
  nodes: EvidenceGraphNode[]
  edges: EvidenceGraphEdge[]
  findings: EvidenceGraphFinding[]
  decisions: FindingDecision[]
  events?: Array<Record<string, unknown>>
  metadata?: Record<string, any>
}

export interface VersionDiffItem { path: string; old_value: unknown; new_value: unknown; change_type?: string }
export interface ArchiveMemberInventoryItem {
  member_index: number
  filename: string
  source_filename?: string
  test_item?: string
  sample_id?: string
  page_count?: number
  status?: string
  metadata?: Record<string, unknown>
}
export interface ArchiveMemberInventory { doc_id: string; members: ArchiveMemberInventoryItem[]; total: number }

export interface Tag { id: string; name: string }

export const DOC_LABELS: Record<DocType, string> = {
  order_form: '委托单',
  test_plan: '试验计划',
  original_records: '原始记录',
  final_report: '检测报告',
  test_standard: '测试标准',
}

export const CHECK_CATEGORY_LABELS: Record<string, string> = {
  'DOC-STRUCTURE-001': '文档结构',
  'DOC-STRUCTURE-002': '文档结构',
  'DOC-TIMELINE-001': '时间逻辑',
  'DOC-TIMELINE-002': '时间逻辑',
  'DOC-TIMELINE-003': '时间逻辑',
  'DOC-REQUIRED-FIELD-001': '必填字段',
  'DOC-SIGNATURE-001': '签名审批',
  'DOC-PAGINATION-001': '页码完整性',
  'DOC-REFERENCE-001': '引用完整性',
  'DOC-CROSS-FIELD-001': '跨文档校验',
  'DOC-PLAN-REF-001': '计划一致性',
  'DOC-RESULT-CONSISTENCY-001': '跨文档校验',
  'DOC-INSTRUMENT-CONSISTENCY-001': '仪器设备',
  'RESULT-DIMENSION-001': '结果完整性',
  'RESULT-EMPTY-001': '结果完整性',
  'RESULT-TYPE-001': '数据类型',
  'RESULT-DUPLICATE-001': '数据类型',
  'RESULT-FORMULA-001': '结果计算',
  'RESULT-LIMIT-001': '结果计算',
  'INSTRUMENT-IDENTITY-001': '仪器设备',
  'INSTRUMENT-CAL-001': '仪器设备',
  'ANOMALY-CONCLUSION-001': '异常与结论',
  'EVIDENCE-PROFILE-001': '证据完整性',
  'SEMANTIC-VARIABLE-001': '语义一致性',
  'GRAPH-CONCLUSION-001': '图表校验',
  'GRAPH-COVERAGE-001': '测试项覆盖',
  'GRAPH-COVERAGE-002': '计划外项目',
  'GRAPH-SAMPLE-001': '样品信息',
  'REPORT-RESULT-CONFLICT-001': '报告内校验',
  'REPORT-SPEC-RESULT-001': '报告内校验',
  'PLAN-ACCEPTANCE-CONFLICT-001': '计划一致性',
  'PLAN-CLAUSE-CONSISTENCY-001': '计划一致性',
  'STANDARD-PARAMETER-CONSISTENCY-001': '标准符合性',
  'STANDARD-ACCEPTANCE-CONSISTENCY-001': '标准符合性',
  'STANDARD-PLAN-COVERAGE-001': '标准覆盖',
  'STANDARD-SCOPE-ADVISORY-001': '标准符合性',
}

export const STATUS_LABELS: Record<string, string> = {
  draft: '资料准备', incomplete: '资料准备', revision: '修订中', locked: '已锁定', reviewing: '审核中', reviewed: '审核完成',
  created: '已创建', building: '构建证据图', running: '规则审核中', machine_complete: '机器审核完成',
  machine_incomplete: '存在待确认项', failed: '运行失败', aborted: '已取消',
  pending: '等待提取', queued: '排队提取', extracting: '提取中', processing: '处理中',
  done: '提取完成', completed: '提取完成', partial: '提取不完整',
}
