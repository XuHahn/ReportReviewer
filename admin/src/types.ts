export type UserRole = 'admin' | 'reviewer' | 'viewer'

export interface User {
  employee_id: string
  role: UserRole
  name?: string
  created_at?: string
}

export interface LoginResponse {
  token: string
  user: User
}

export interface UserListResponse {
  users: User[]
  total: number
}

export interface ProjectGroup {
  id: string
  name: string
  description: string
  category?: string
  members: string[]
  created_by: string
}

export interface AuditLogEntry {
  id: number
  timestamp: string
  client_ip: string
  employee_id: string
  action: string
  filename: string
  file_size_kb: number
  overall_result: string
  issue_count: number
  detail: string
}

export interface AuditLogListResponse {
  entries: AuditLogEntry[]
  total: number
}

export interface StatsOverview {
  total_reports: number
  total_issues: number
  pass_rate: number
  avg_issues_per_report: number
  avg_duration_ms: number
}

export interface SeverityDist {
  error: number
  warning: number
  info: number
}

export interface DailyTrend {
  date: string
  uploads: number
  pass_count: number
  fail_count: number
}

export interface TopLocation {
  location: string
  count: number
}

export interface AdminStatsResponse {
  overview: StatsOverview
  pass_fail: Record<string, number>
  severity_dist: SeverityDist
  trends: DailyTrend[]
  top_locations: TopLocation[]
  top_actions: Record<string, number>
  total_users: number
  today_uploads: number
  recent_activity: AuditLogEntry[]
}

export interface SystemSettings {
  settings: Record<string, string>
}

// ── Report types ────────────────────────────────────────────────────────

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

export interface Tag {
  id: string
  name: string
  created_by: string
  created_at: string
}
