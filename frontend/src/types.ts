export interface ReviewItem {
  severity: 'error' | 'warning' | 'info'
  location: string
  original_text: string
  error_description: string
  standard_reference: string
  suggestion: string
  highlighted: boolean
  human_status: string
  human_comment: string
  annotated_by: string
  annotated_at: string
}

export interface UploadResponse {
  report_id: string
  filename: string
  overall_result: 'pass' | 'fail' | 'warning' | 'error'
  review_items: ReviewItem[]
  highlighted_html: string
  created_at: string
  estimated_tokens: number
  token_limit: number
  truncated: boolean
  employee_id: string
  uploader_name?: string
  tags: string
  original_result?: string
  group_id: string
  comparison: string
  compared_with: string
}

export interface ReportRecord {
  id: string
  filename: string
  overall_result: string
  original_result?: string
  review_items: ReviewItem[]
  highlighted_html: string
  created_at: string
  employee_id: string
  uploader_name?: string
  tags: string
  group_id: string
  comparison: string
  compared_with: string
}

export interface ReportListResponse {
  reports: ReportRecord[]
  total: number
}

// ── SSE Streaming types ────────────────────────────────────────────────

export type SSEProgressPhase =
  | 'stage1_scanning' | 'stage1_done'
  | 'stage2_reviewing'
  | 'highlighting' | 'saving';

export interface SSEProgressEvent {
  phase: SSEProgressPhase;
  message: string;
  elapsed_seconds: number;
}

export interface SSEChecklistSummary {
  total_flags: number;
  severities: Record<string, number>;
  sample_flags: string[];
}

export type SSEResultEvent = UploadResponse;

export type SSEEvent =
  | { type: 'progress'; data: SSEProgressEvent }
  | { type: 'checklist_summary'; data: SSEChecklistSummary }
  | { type: 'review_item'; data: ReviewItem }
  | { type: 'result'; data: SSEResultEvent }
  | { type: 'error'; data: { message: string } };

export interface StreamingState {
  phase: SSEProgressPhase | 'idle';
  phaseMessage: string;
  checklistSummary: SSEChecklistSummary | null;
  reviewItems: ReviewItem[];
  finalResult: SSEResultEvent | null;
  error: string | null;
  elapsed: number;
}

// ── Batch review types ──────────────────────────────────────────────────

export interface BatchFileResult {
  index: number;
  filename: string;
  report_id: string;
  overall_result: string;
  review_items: ReviewItem[];
  highlighted_html: string;
  estimated_tokens?: number;
  token_limit?: number;
  truncated?: boolean;
  summary?: string;
  created_at?: string;
  duration_ms?: number;
  employee_id?: string; uploader_name?: string; tags?: string;
  original_result?: string;
  group_id?: string;
  comparison?: string;
  compared_with?: string;
  error?: string | null;
}

export interface BatchStartEvent {
  total: number;
  filenames: string[];
  errors: { index: number; filename: string; error: string }[];
}

export interface BatchProgressEvent {
  file_index: number;
  filename: string;
  phase: string;
  overall_result: string;
  issues: number;
  error?: string;
}

export interface BatchDoneEvent {
  total_files: number;
  total_issues: number;
  duration_ms: number;
  results: BatchFileResult[];
}

export interface BatchStreamingState {
  phase: 'parsing' | 'processing' | 'done';
  total: number;
  completed: number;
  progress: BatchProgressEvent[];
  results: BatchFileResult[];
  error: string | null;
  elapsed: number;
}

// ── Statistics types ──────────────────────────────────────────────────────

export interface StatsOverview {
  total_reports: number;
  total_issues: number;
  pass_rate: number;
  avg_issues_per_report: number;
  avg_duration_ms: number;
}

export interface SeverityDist {
  error: number;
  warning: number;
  info: number;
}

export interface DailyTrend {
  date: string;
  uploads: number;
  pass_count: number;
  fail_count: number;
}

export interface TopLocation {
  location: string;
  count: number;
}

// ── Review Rules types ──────────────────────────────────────────────────

export interface ReviewRule {
  id: string
  name: string
  description: string
  category: 'limit' | 'consistency' | 'logic' | 'format' | 'other'
  severity: 'error' | 'warning' | 'info'
  keywords: string[]
  pattern: string
  standard_id: string
  suggestion_template: string
  enabled: boolean
  created_at: string
  updated_at: string
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
  created_at: string
}

// ── Auth types ──────────────────────────────────────────────────────────

export type UserRole = 'admin' | 'reviewer' | 'viewer'

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

export interface StatsResponse {
  overview: StatsOverview;
  pass_fail: Record<string, number>;
  severity_dist: SeverityDist;
  trends: DailyTrend[];
  top_locations: TopLocation[];
  top_actions: Record<string, number>;
}

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
  created_at: string
}

export interface TimelineEntry {
  action: string
  detail: string
  employee_id: string
  timestamp: string
  name?: string
}

export interface AnnotationResponse {
  new_overall_result?: string
}
