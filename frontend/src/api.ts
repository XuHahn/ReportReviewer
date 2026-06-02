import axios from 'axios'
import type { UploadResponse, ReportListResponse, ReportRecord, ReviewRule, EmcStandard, User, LoginResponse, UserListResponse, Tag, ProjectGroup, TimelineEntry, AnnotationResponse, StatsResponse } from './types'
import { logger } from './logger'

// ── Token management ──────────────────────────────────────────────────────

const TOKEN_KEY = 'emc_review_token'

export function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setStoredToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearStoredToken(): void {
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

// Response interceptor: extract reqId, handle 401/403, log errors
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
    if (error.response?.status === 403) {
      window.dispatchEvent(new CustomEvent('auth:forbidden'))
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

export async function uploadReport(
  file: File,
  tags?: string,
  compareWith?: string,
  onProgress?: (pct: number) => void,
): Promise<UploadResponse> {
  const formData = new FormData()
  formData.append('file', file)
  if (tags) formData.append('tags', tags)
  if (compareWith) formData.append('compare_with', compareWith)
  const { data } = await api.post<UploadResponse>('/reports/upload', formData, {
    timeout: 600000,
    onUploadProgress: (e) => {
      if (e.total && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    },
  })
  return data
}

// ── Shared SSE stream parser ────────────────────────────────────────────

export type SSEEventCallback = (event: any) => void

async function parseSSEStream(
  response: Response,
  onEvent: SSEEventCallback,
): Promise<void> {
  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let currentEvent = ''
  let currentData = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      if (line.startsWith('event: ')) {
        currentEvent = line.slice(7).trim()
      } else if (line.startsWith('data: ')) {
        currentData = line.slice(6)
      } else if (line === '') {
        if (currentData) {
          try {
            const parsed = JSON.parse(currentData)
            const eventType = currentEvent || parsed.type || 'unknown'
            onEvent({ type: eventType, data: parsed.data || parsed })
          } catch {
            // skip malformed SSE line
          }
        }
        currentEvent = ''
        currentData = ''
      }
    }
  }
}

const STREAM_TIMEOUT_MS = 600_000 // 10 minutes

// ── SSE streaming upload ───────────────────────────────────────────────

export async function uploadReportStream(
  file: File,
  tags: string | undefined,
  compareWith: string | undefined,
  onEvent: SSEEventCallback,
  signal: AbortSignal,
): Promise<void> {
  const formData = new FormData()
  formData.append('file', file)
  if (tags) formData.append('tags', tags)
  if (compareWith) formData.append('compare_with', compareWith)

  // Combine user cancel signal with a 10-minute timeout
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), STREAM_TIMEOUT_MS)
  const cleanup = () => clearTimeout(timeoutId)

  signal.throwIfAborted()
  signal.addEventListener('abort', () => controller.abort(), { once: true })
  controller.signal.addEventListener('abort', cleanup, { once: true })

  try {
    const token = getStoredToken()
    const response = await fetch('/api/reports/upload/stream', {
      method: 'POST',
      body: formData,
      signal: controller.signal,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })

    const reqId = response.headers.get('X-Request-Id')
    if (reqId) logger.setReqId(reqId)

    if (!response.ok) {
      if (response.status === 401) {
        clearStoredToken()
        window.dispatchEvent(new CustomEvent('auth:logout'))
        throw new Error('登录已过期，请重新登录')
      }
      const err = await response.json().catch(() => ({ detail: 'Upload failed' }))
      throw new Error(err.detail || '上传失败')
    }

    await parseSSEStream(response, onEvent)
  } finally {
    cleanup()
  }
}

export interface HistoryParams {
  limit?: number
  offset?: number
  keyword?: string
  overall_result?: string
  date_from?: string
  date_to?: string
  employee_id?: string
  tag?: string
}

export async function getHistory(params: HistoryParams = {}): Promise<ReportListResponse> {
  const { data } = await api.get<ReportListResponse>('/reports/history', { params })
  return data
}

export async function getReportDetail(id: string): Promise<ReportRecord> {
  const { data } = await api.get<ReportRecord>(`/reports/${id}`)
  return data
}

// ── Full-text search ──────────────────────────────────────────────────

export interface SearchParams {
  q: string
  limit?: number
  offset?: number
}

export interface SearchSnippets {
  location: string
  original_text: string
  error_description: string
  standard_reference: string
  suggestion: string
}

export interface SearchResultItem {
  report_id: string
  filename: string
  overall_result: string
  created_at: string
  employee_id: string
  uploader_name?: string
  snippets: SearchSnippets
  match_count: number
  rank: number
}

export interface SearchResponse {
  results: SearchResultItem[]
  total: number
  query: string
}

export async function searchReports(params: SearchParams): Promise<SearchResponse> {
  const { data } = await api.get<SearchResponse>('/reports/search', { params })
  return data
}

// ── Batch streaming upload ─────────────────────────────────────────────

export async function uploadBatchStream(
  files: File[],
  tags: string | undefined,
  compareWith: string | undefined,
  onEvent: SSEEventCallback,
  signal: AbortSignal,
): Promise<void> {
  const formData = new FormData()
  for (const file of files) {
    formData.append('files', file)
  }
  if (tags) formData.append('tags', tags)
  if (compareWith) formData.append('compare_with', compareWith)

  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), STREAM_TIMEOUT_MS)
  const cleanup = () => clearTimeout(timeoutId)

  signal.throwIfAborted()
  signal.addEventListener('abort', () => controller.abort(), { once: true })
  controller.signal.addEventListener('abort', cleanup, { once: true })

  try {
    const token = getStoredToken()
    const response = await fetch('/api/reports/upload/batch', {
      method: 'POST',
      body: formData,
      signal: controller.signal,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })

    const reqId = response.headers.get('X-Request-Id')
    if (reqId) logger.setReqId(reqId)

    if (!response.ok) {
      if (response.status === 401) {
        clearStoredToken()
        window.dispatchEvent(new CustomEvent('auth:logout'))
        throw new Error('登录已过期，请重新登录')
      }
      const err = await response.json().catch(() => ({ detail: 'Batch upload failed' }))
      throw new Error(err.detail || '批量上传失败')
    }

    await parseSSEStream(response, onEvent)
  } finally {
    cleanup()
  }
}

export async function getStats(): Promise<StatsResponse> {
  const { data } = await api.get('/reports/stats')
  return data
}

export async function downloadExportPdf(id: string): Promise<void> {
  const resp = await api.get(`/reports/${id}/export/pdf`, { responseType: 'blob' })
  const url = URL.createObjectURL(resp.data)
  const a = document.createElement('a')
  a.href = url
  a.download = `report_${id}.pdf`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

export async function downloadExportWord(id: string): Promise<void> {
  const resp = await api.get(`/reports/${id}/export/word`, { responseType: 'blob' })
  const url = URL.createObjectURL(resp.data)
  const a = document.createElement('a')
  a.href = url
  a.download = `report_${id}.docx`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

// ── Review Rules API ──────────────────────────────────────────────────

export async function getRules(params: { keyword?: string; category?: string } = {}): Promise<ReviewRule[]> {
  const { data } = await api.get<ReviewRule[]>('/rules', { params })
  return data
}

export async function createRule(rule: Partial<ReviewRule>): Promise<{ id: string }> {
  const { data } = await api.post<{ id: string }>('/rules', rule)
  return data
}

export async function updateRule(id: string, updates: Partial<ReviewRule>): Promise<{ updated: boolean }> {
  const { data } = await api.put<{ updated: boolean }>(`/rules/${id}`, updates)
  return data
}

export async function deleteRule(id: string): Promise<void> {
  await api.delete(`/rules/${id}`)
}

// ── EMC Standards API ─────────────────────────────────────────────────

export async function getStandards(params: { organization?: string; category?: string; keyword?: string } = {}): Promise<EmcStandard[]> {
  const { data } = await api.get<EmcStandard[]>('/standards', { params })
  return data
}

export async function getStandardDetail(id: string): Promise<EmcStandard> {
  const { data } = await api.get<EmcStandard>(`/standards/${id}`)
  return data
}

export async function createStandard(std: Partial<EmcStandard>): Promise<{ id: string }> {
  const { data } = await api.post<{ id: string }>('/standards', std)
  return data
}

export async function deleteStandard(id: string): Promise<void> {
  await api.delete(`/standards/${id}`)
}

export async function deleteReport(id: string, cascade: boolean = false): Promise<{deleted: boolean; group_deleted?: boolean}> {
  const { data } = await api.delete(`/reports/${id}`, { params: { cascade } })
  return data
}

export async function getReportTimeline(id: string): Promise<{ entries: TimelineEntry[] }> {
  const { data } = await api.get(`/reports/${id}/timeline`)
  return data
}

// ── Project Groups API ──────────────────────────────────────────

export async function getProjectGroups(): Promise<ProjectGroup[]> {
  const { data } = await api.get('/groups')
  return data
}

export async function createProjectGroup(name: string, description: string, member_ids: string[], category?: string): Promise<{id:string}> {
  const { data } = await api.post('/groups', { name, description, member_ids, category })
  return data
}

export async function updateProjectGroup(id: string, name?: string, description?: string, member_ids?: string[], category?: string): Promise<ProjectGroup> {
  const { data } = await api.put(`/groups/${id}`, { name, description, member_ids, category })
  return data
}

export async function deleteProjectGroup(id: string): Promise<ProjectGroup> {
  const { data } = await api.delete(`/groups/${id}`)
  return data
}

export async function updateReportTags(reportId: string, tags: string): Promise<{tags: string}> {
  const { data } = await api.patch(`/reports/${reportId}/tags`, { tags })
  return data
}

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


export async function checkReportUpdates(reportId: string, since: string, by: string): Promise<{has_update: boolean, overall_result: string}> {
  const { data } = await api.get(`/reports/${reportId}/check-updates`, { params: { since, by } })
  return data
}

export async function updateItemAnnotation(
  reportId: string,
  itemIndex: number,
  humanStatus: string,
  humanComment?: string,
  since?: string,
): Promise<AnnotationResponse> {
  const { data } = await api.patch(`/reports/${reportId}/items/${itemIndex}/annotation`, {
    human_status: humanStatus,
    human_comment: humanComment || '',
    since: since || '',
  })
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
