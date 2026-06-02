import axios from 'axios'
import type {
  LoginResponse, User, UserListResponse,
  AuditLogListResponse, AdminStatsResponse, SystemSettings,
  ReportListResponse, UploadResponse, ProjectGroup, Tag,
} from './types'
import { logger } from './logger'

// ── Token management (shared with main app via same localStorage key) ──

const TOKEN_KEY = 'emc_review_token'

export function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

function setStoredToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

function clearStoredToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

const api = axios.create({ baseURL: '/api' })

// Request interceptor: attach Bearer token
api.interceptors.request.use((config) => {
  const token = getStoredToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Response interceptor: extract reqId, handle 401
api.interceptors.response.use(
  (response) => {
    const reqId = response.headers['x-request-id']
    if (reqId) logger.setReqId(reqId)
    return response
  },
  (error) => {
    if (error.response?.status === 401) {
      clearStoredToken()
      window.dispatchEvent(new CustomEvent('auth:logout'))
    }
    if (error.response?.status && error.response.status >= 400) {
      logger.error('API request failed', {
        method: error.config?.method?.toUpperCase(),
        path: error.config?.url,
        status: error.response.status,
      }, error)
    }
    return Promise.reject(error)
  },
)

// ── Auth API ────────────────────────────────────────────────────────────

export async function login(employeeId: string): Promise<LoginResponse> {
  const { data } = await api.post<LoginResponse>('/auth/login', { employee_id: employeeId })
  setStoredToken(data.token)
  return data
}

export async function getCurrentUser(): Promise<User> {
  const { data } = await api.get<User>('/auth/me')
  return data
}

// ── Users API ───────────────────────────────────────────────────────────

export async function listUsers(): Promise<UserListResponse> {
  const { data } = await api.get<UserListResponse>('/users')
  return data
}

export async function createUser(employeeId: string, role: string, name?: string): Promise<User> {
  const { data } = await api.post<User>('/users', { employee_id: employeeId, role, name: name || '' })
  return data
}

export async function updateUserRole(employeeId: string, role: string): Promise<User> {
  const { data } = await api.put<User>(`/users/${employeeId}/role`, { role })
  return data
}

export async function updateUserName(employeeId: string, name: string): Promise<User> {
  const { data } = await api.put<User>(`/users/${employeeId}/name`, { name })
  return data
}

export async function deleteUser(employeeId: string): Promise<void> {
  await api.delete(`/users/${employeeId}`)
}

// ── Admin API ───────────────────────────────────────────────────────────

export async function getAdminStats(): Promise<AdminStatsResponse> {
  const { data } = await api.get<AdminStatsResponse>('/admin/stats')
  return data
}

export async function getSettings(): Promise<SystemSettings> {
  const { data } = await api.get<SystemSettings>('/admin/settings')
  return data
}

export async function updateSettings(settings: Record<string, string>): Promise<SystemSettings> {
  const { data } = await api.put<SystemSettings>('/admin/settings', { settings })
  return data
}

export interface AuditLogParams {
  limit?: number
  offset?: number
  action?: string
  ip?: string
  employee_id?: string
  filename?: string
  date_from?: string
  date_to?: string
}

export async function getAuditLogs(params: AuditLogParams = {}): Promise<AuditLogListResponse> {
  const { data } = await api.get<AuditLogListResponse>('/logs', { params })
  return data
}

// ── Reports API ─────────────────────────────────────────────────────────

export interface ReportHistoryParams {
  limit?: number
  offset?: number
  keyword?: string
  overall_result?: string
  date_from?: string
  date_to?: string
  employee_id?: string
  tag?: string
}

export async function getReportHistory(params: ReportHistoryParams = {}): Promise<ReportListResponse> {
  const { data } = await api.get<ReportListResponse>('/reports/history', { params })
  return data
}

export async function getReportDetail(id: string): Promise<UploadResponse> {
  const { data } = await api.get<UploadResponse>(`/reports/${id}`)
  return data
}

export async function updateItemAnnotation(
  reportId: string,
  itemIndex: number,
  humanStatus: string,
  humanComment?: string,
  since?: string,
): Promise<any> {
  const { data } = await api.patch(`/reports/${reportId}/items/${itemIndex}/annotation`, {
    human_status: humanStatus,
    human_comment: humanComment || '',
    since: since || '',
  })
  return data
}


export async function getReportTimeline(reportId: string): Promise<{entries: any[], total: number}> {
  const { data } = await api.get<{entries: any[], total: number}>(`/reports/${reportId}/timeline`)
  return data
}

export async function deleteReport(id: string): Promise<void> {
  await api.delete(`/reports/${id}`)
}

// ── Groups API ───────────────────────────────────────────────────────────

export async function getProjectGroups(): Promise<ProjectGroup[]> {
  const { data } = await api.get<ProjectGroup[]>('/groups')
  return data
}

export async function createProjectGroup(name: string, description: string, member_ids: string[], category?: string): Promise<{id: string}> {
  const { data } = await api.post<{id: string}>('/groups', { name, description, member_ids, category })
  return data
}

export async function updateProjectGroup(id: string, name?: string, description?: string, member_ids?: string[], category?: string): Promise<any> {
  const { data } = await api.put<any>(`/groups/${id}`, { name, description, member_ids, category })
  return data
}

export async function deleteProjectGroup(id: string): Promise<any> {
  const { data } = await api.delete<any>(`/groups/${id}`)
  return data
}

export async function updateReportTags(reportId: string, tags: string): Promise<any> {
  const { data } = await api.patch<any>(`/reports/${reportId}/tags`, { tags })
  return data
}

export async function downloadBatchExcel(ids: string[]): Promise<void> {
  const resp = await api.get('/reports/export/batch', {
    params: { ids: ids.join(',') },
    responseType: 'blob',
  })
  const url = URL.createObjectURL(resp.data)
  const a = document.createElement('a')
  a.href = url
  a.download = `batch_export_${ids.length}.xlsx`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

// ── Tags API ──────────────────────────────────────────────────────────

export async function getTags(): Promise<Tag[]> {
  const { data } = await api.get<Tag[]>('/tags')
  return data
}

export async function createTag(name: string): Promise<{ id: string; name: string }> {
  const { data } = await api.post<{ id: string; name: string }>('/tags', { name })
  return data
}

export async function updateTag(id: string, name: string): Promise<{ updated: boolean }> {
  const { data } = await api.put<{ updated: boolean }>(`/tags/${id}`, { name })
  return data
}

export async function deleteTag(id: string): Promise<void> {
  await api.delete(`/tags/${id}`)
}

// ── Logs API ──────────────────────────────────────────────────────────

export interface LogViewParams {
  source?: string
  level?: string
  keyword?: string
  reqId?: string
  limit?: number
  offset?: number
}

export interface LogViewResponse {
  logs: any[]
  total: number
  offset: number
  limit: number
}

export async function getLogs(params: LogViewParams = {}): Promise<LogViewResponse> {
  const { data } = await api.get<LogViewResponse>('/admin/logs/view', { params })
  return data
}
