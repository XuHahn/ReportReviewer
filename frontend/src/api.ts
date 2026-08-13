import axios from 'axios'
import type { ArchiveMemberInventory, EmcStandard, StandardKnowledgeChunk, StandardGraphClause, StandardGraphRequirement, StandardRequirementParameter, User, LoginResponse, UserListResponse, Tag, ProjectGroup, SetStatsResponse } from './types'
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

const api = axios.create({ baseURL: '/api', timeout: 180000 })

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

export async function uploadStandard(
  file: File,
  metadata: { code?: string; title?: string; organization?: string; category?: string; version?: string } = {},
): Promise<{ id: string; duplicate: boolean; knowledge_status: string }> {
  const fd = new FormData()
  fd.append('file', file)
  Object.entries(metadata).forEach(([key, value]) => {
    if (value) fd.append(key, value)
  })
  const { data } = await api.post('/standards/upload', fd, { timeout: 0 })
  return data
}

export async function getStandardKnowledge(
  id: string, query = '', limit = 50,
): Promise<{ standard: EmcStandard; chunks: StandardKnowledgeChunk[]; total: number }> {
  const { data } = await api.get(`/standards/${id}/knowledge`, { params: { query, limit } })
  return data
}

export async function retryStandardKnowledge(id: string): Promise<{ id: string; knowledge_status: string }> {
  const { data } = await api.post(`/standards/${id}/knowledge/retry`)
  return data
}

export async function rebuildStandardGraph(id: string): Promise<{ id: string; graph_status: string }> {
  const { data } = await api.post(`/standards/${id}/graph/rebuild`)
  return data
}

export async function retryFailedStandardGraphUnits(id: string): Promise<{ id: string; graph_status: string; remaining_failed_unit_ids: string[] }> {
  const { data } = await api.post(`/standards/${id}/graph/retry-failed`)
  return data
}

export async function getStandardGraph(id: string): Promise<{
  standard: EmcStandard; clauses: StandardGraphClause[]; requirements: StandardGraphRequirement[]
}> {
  const { data } = await api.get(`/standards/${id}/graph`)
  return data
}

export async function reviewStandardRequirement(
  standardId: string,
  requirementId: string,
  body: {
    review_status: 'confirmed' | 'rejected'; comment?: string
    clause_number?: string; clause_title?: string; requirement_type?: string
    test_item?: string; statement?: string; interpretation_zh?: string; applicability?: string
    parameters?: StandardRequirementParameter[]
  },
): Promise<StandardGraphRequirement> {
  const { data } = await api.patch(`/standards/${standardId}/graph/requirements/${requirementId}`, body)
  return data
}

export async function publishStandardGraph(id: string): Promise<{
  id: string; graph_status: string
  counts: { release_id: string; release_number: number; embedding_model: string }
}> {
  const { data } = await api.post(`/standards/${id}/graph/publish`)
  return data
}

export async function getStandardReleases(id: string): Promise<{
  standard_id: string
  releases: Array<{
    id: string; release_number: number; status: string; source_file_sha256: string
    embedding_model: string; prompt_version: string; requirement_count: number
    published_by: string; published_at: string
  }>
}> {
  const { data } = await api.get(`/standards/${id}/releases`)
  return data
}

export async function getStandardRelease(standardId: string, releaseId: string): Promise<{
  id: string; standard_id: string; release_number: number
  snapshot: { requirements: StandardGraphRequirement[] }
}> {
  const { data } = await api.get(`/standards/${standardId}/releases/${releaseId}`)
  return data
}

export async function saveStandardRequirementMapping(
  standardId: string,
  body: {
    release_id: string; source_name: string; mapping_type: 'covered' | 'not_covered'
    requirement_id?: string; scope_type: 'standard' | 'project' | 'set'
    scope_value?: string; rationale?: string
  },
): Promise<{ id: string }> {
  const { data } = await api.post(`/standards/${standardId}/mappings`, body)
  return data
}

export async function downloadStandardFile(id: string): Promise<{ blob: Blob; filename: string }> {
  const response = await api.get(`/standards/${id}/file`, { responseType: 'blob' })
  const disposition = response.headers['content-disposition'] || ''
  let filename = `${id}.pdf`
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i)
  const asciiMatch = disposition.match(/filename="?([^";]+)"?/i)
  if (utf8Match?.[1]) filename = decodeURIComponent(utf8Match[1])
  else if (asciiMatch?.[1]) filename = decodeURIComponent(asciiMatch[1])
  return { blob: response.data, filename }
}

export async function previewStandardPdf(id: string): Promise<Blob> {
  const response = await api.get(`/standards/${id}/preview`, { responseType: 'blob' })
  return response.data
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

// ── DocumentSet API ──

import type { DocumentSetListItem, DocumentSetOverview, SetDocumentCreateRequest, PipelineReviewResult, PipelineIssue, VersionDiffItem, SetExtractionsResponse, DocumentExtractionResponse } from './types'

export async function createDocumentSet(): Promise<{ set_id: string; status: string }> {
  const { data } = await api.post('/sets'); return data
}

export async function listDocumentSets(): Promise<{ sets: DocumentSetListItem[]; total: number }> {
  const { data } = await api.get('/sets'); return data
}

export async function getSetStats(): Promise<import('./types').SetStatsResponse> {
  const { data } = await api.get('/sets/stats/overview'); return data
}

export async function getDocumentSet(setId: string): Promise<DocumentSetOverview> {
  const { data } = await api.get(`/sets/${setId}`); return data
}

export async function startReviewRun(setId: string): Promise<{ set_id: string; run_id: string; status: string }> {
  const { data } = await api.post(`/sets/${setId}/review-runs`)
  return data
}

export async function listReviewRuns(setId: string): Promise<{
  runs: import('./types').EvidenceGraphRunSummary[]; total: number
}> {
  const { data } = await api.get(`/sets/${setId}/review-runs`)
  return data
}

export async function getReviewRun(
  setId: string, runId: string,
): Promise<import('./types').EvidenceGraphSnapshot> {
  const { data } = await api.get(`/sets/${setId}/review-runs/${runId}`)
  return data
}

export async function downloadReviewRunExport(
  setId: string, runId: string, format: 'xlsx' | 'pdf' | 'evidence',
): Promise<void> {
  const response = await api.get(`/sets/${setId}/review-runs/${runId}/export`, {
    params: { format }, responseType: 'blob', timeout: 180000,
  })
  const disposition = String(response.headers['content-disposition'] || '')
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/)?.[1]
  const filename = encoded ? decodeURIComponent(encoded) : `${setId}-review.${format === 'evidence' ? 'zip' : format}`
  const url = URL.createObjectURL(response.data)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export async function decideReviewFinding(
  setId: string, runId: string, findingId: string,
  decision: 'confirmed' | 'dismissed' | 'advisory' | 'unresolved', comment: string,
  resolutionCode = '', resolutionStatus = '',
): Promise<void> {
  await api.put(`/sets/${setId}/review-runs/${runId}/findings/${findingId}/decision`, {
    decision, comment, resolution_code: resolutionCode, resolution_status: resolutionStatus,
  })
}

export async function getSystemSettings(): Promise<{ settings: Record<string, string> }> {
  const { data } = await api.get('/admin/settings')
  return data
}

export async function updateSystemSettings(
  settings: Record<string, string>,
): Promise<{ settings: Record<string, string> }> {
  const { data } = await api.put('/admin/settings', { settings })
  return data
}

export async function getSystemLogs(params: {
  source?: string; level?: string; reqId?: string; keyword?: string; limit?: number; offset?: number
} = {}): Promise<{ logs: Array<Record<string, any>>; total: number }> {
  const { data } = await api.get('/admin/logs/view', { params })
  return data
}

export async function updateDocumentSetProjectGroup(
  setId: string, projectGroupId: string,
): Promise<{ set_id: string; project_group_id: string; project_group_name: string }> {
  const { data } = await api.put(`/sets/${setId}/project-group`, { project_group_id: projectGroupId })
  return data
}

export async function getDocumentSetStandards(setId: string): Promise<{
  set_id: string; standards: EmcStandard[]; standard_review_enabled: boolean
}> {
  const { data } = await api.get(`/sets/${setId}/standards`); return data
}

export async function getDocumentSetStandardReferences(setId: string): Promise<{
  set_id: string
  references: Array<{ reference_code: string; normalized_code: string; sources: string[]; evidence: string[] }>
  resolved: unknown[]
  unresolved: unknown[]
  skips: Array<{ normalized_code: string; reference_code: string; reason: string }>
}> {
  const { data } = await api.get(`/sets/${setId}/standard-references`); return data
}

export async function updateDocumentSetStandards(
  setId: string, standardIds: string[],
  skips: Array<{ normalized_code: string; reference_code: string; reason: string }> = [],
): Promise<{
  set_id: string; standards: EmcStandard[]; standard_review_enabled: boolean
}> {
  const { data } = await api.put(`/sets/${setId}/standards`, { standard_ids: standardIds, skips }); return data
}

export async function addDocumentToSet(setId: string, file: File, docType: string, replaceDocId?: string, signal?: AbortSignal): Promise<any> {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('doc_type', docType)
  if (replaceDocId) fd.append('replace_doc_id', replaceDocId)
  const { data } = await api.post(`/sets/${setId}/documents`, fd, { signal })
  return data
}

export async function deleteDocument(setId: string, docId: string): Promise<{ deleted: boolean }> {
  const { data } = await api.delete(`/sets/${setId}/documents/${docId}`)
  return data
}

export async function lockDocumentSet(setId: string): Promise<{ set_id: string; status: string }> {
  const { data } = await api.post(`/sets/${setId}/lock`); return data
}

export async function createDocumentSetRevision(setId: string): Promise<{ set_id: string; status: string }> {
  const { data } = await api.post(`/sets/${setId}/revision`); return data
}

export async function triggerReview(setId: string): Promise<PipelineReviewResult> {
  const startedAt = Date.now()
  const started = await startReviewRun(setId)
  while (true) {
    const snapshot = await getReviewRun(setId, started.run_id)
    if (['machine_complete', 'machine_incomplete', 'failed', 'aborted'].includes(snapshot.run.status)) {
      const errors = snapshot.run.status === 'failed' ? ['证据图审核运行失败'] : []
      const confirmedErrors = snapshot.findings.filter(item => item.status === 'confirmed_error')
      const advisories = snapshot.findings.filter(item => item.status.includes('advisory'))
      const unresolved = snapshot.findings.filter(item => item.status === 'unresolved')
      return {
        set_id: setId,
        run_id: started.run_id,
        status: snapshot.run.status,
        is_clean: confirmedErrors.length === 0 && unresolved.length === 0 && !errors.length,
        duration_seconds: (Date.now() - startedAt) / 1000,
        issues: {
          critical: confirmedErrors.length,
          warning: unresolved.length,
          info: advisories.length,
        },
        errors,
      }
    }
    await new Promise(resolve => setTimeout(resolve, 1200))
  }
}

export async function streamReviewProgress(
  setId: string,
  onEvent: (event: Record<string, any>) => void,
  signal?: AbortSignal,
): Promise<void> {
  const labels: Record<string, string> = {
    created: '审核任务已创建', building: '构建证据图', running: '核验文档关系',
    machine_complete: '审核完成', machine_incomplete: '审核完成，存在待确认项',
    failed: '审核失败', aborted: '审核已取消',
  }
  while (!signal?.aborted) {
    const { runs } = await listReviewRuns(setId)
    const run = runs[0]
    if (run) {
      const terminal = ['machine_complete', 'machine_incomplete', 'failed', 'aborted'].includes(run.status)
      onEvent({
        step: terminal ? 3 : run.status === 'created' ? 1 : 2,
        total: 3,
        label: labels[run.status] || '证据图审核运行中',
        status: terminal ? (run.status === 'failed' ? 'error' : 'complete') : 'active',
      })
      if (terminal) return
    }
    await new Promise(resolve => setTimeout(resolve, 1000))
  }
}

export async function getPipelineIssues(setId: string): Promise<{ issues: PipelineIssue[]; total: number }> {
  const { runs } = await listReviewRuns(setId)
  if (!runs.length) return { issues: [], total: 0 }
  const snapshot = await getReviewRun(setId, runs[0].graph_id)
  const evidence = new Map(snapshot.evidence.map(item => [item.evidence_id, item]))
  const decisions = new Map(snapshot.decisions.map(item => [item.finding_id, item]))
  const issues: PipelineIssue[] = snapshot.findings
    .filter(item => item.status !== 'confirmed_pass' && item.status !== 'not_applicable')
    .map(item => {
      const decision = decisions.get(item.finding_id)
      const sources = Object.fromEntries(item.evidence_ids.map((id, index) => {
        const source = evidence.get(id)
        return [`evidence_${index + 1}`, source?.exact_quote || id]
      }))
      return {
        id: item.finding_id,
        severity: item.severity === 'error' ? 'CRITICAL' : item.severity === 'warning' ? 'WARNING' : 'INFO',
        category: item.check_id,
        field_name: item.title,
        description: item.description,
        sources,
        source_step: 'evidence_graph',
        validation_type: item.status,
        resolution: '',
        human_status: decision?.decision === 'confirmed' ? 'confirmed'
          : decision?.decision === 'dismissed' ? 'ignored' : 'pending',
        human_comment: decision?.comment || '',
        annotated_by: decision?.actor_id || '',
        annotated_at: decision?.created_at || '',
        check_id: item.check_id,
      }
    })
  return { issues, total: issues.length }
}

export async function getPipelineMetrics(setId: string, runId?: string): Promise<import('./types').PipelineMetricsResponse> {
  return { set_id: setId, run_id: runId || '', metrics: [], summary: {} }
}


export async function downloadAuditReportExcel(setId: string): Promise<{ blob: Blob; filename: string }> {
  const { runs } = await listReviewRuns(setId)
  if (!runs.length) throw new Error('当前任务还没有可导出的审核运行')
  const response = await api.get(`/sets/${setId}/review-runs/${runs[0].graph_id}/export`, {
    params: { format: 'xlsx' }, responseType: 'blob',
  })
  const disposition = response.headers['content-disposition'] || ''
  let filename = `${setId}_审核报告.xlsx`
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i)
  const asciiMatch = disposition.match(/filename="?([^";]+)"?/i)
  if (utf8Match?.[1]) {
    filename = decodeURIComponent(utf8Match[1])
  } else if (asciiMatch?.[1]) {
    filename = decodeURIComponent(asciiMatch[1])
  }
  return { blob: response.data, filename }
}

export async function annotateIssue(setId: string, issueId: string, humanStatus: string, humanComment?: string): Promise<any> {
  const { runs } = await listReviewRuns(setId)
  if (!runs.length) throw new Error('当前任务还没有审核运行')
  const decision = humanStatus === 'confirmed' ? 'confirmed'
    : humanStatus === 'ignored' ? 'dismissed' : 'unresolved'
  await decideReviewFinding(
    setId, runs[0].graph_id, issueId, decision,
    humanComment || (decision === 'confirmed' ? '人工确认问题' : decision === 'dismissed' ? '人工排除问题' : ''),
  )
  return { updated: true }
}

export async function getVersionHistory(setId: string, docType: string): Promise<{ versions: any[]; total: number }> {
  const { data } = await api.get(`/sets/${setId}/versions/${docType}`); return data
}

export async function diffVersions(setId: string, oldDocId: string, newDocId: string): Promise<{ diffs: VersionDiffItem[]; changed: VersionDiffItem[] }> {
  const { data } = await api.get(`/sets/${setId}/diff`, { params: { old_doc_id: oldDocId, new_doc_id: newDocId } }); return data
}

export async function getExtractions(setId: string): Promise<SetExtractionsResponse> {
  const { data } = await api.get(`/sets/${setId}/extractions`); return data
}

/** Get extraction data for a single document (decoupled from other docs' states). */
export async function getDocumentExtraction(setId: string, docId: string): Promise<DocumentExtractionResponse> {
  const { data } = await api.get(`/sets/${setId}/documents/${docId}/extraction`); return data
}

export async function saveOverrides(setId: string, docId: string, overrides: Record<string, string>): Promise<{ saved: number }> {
  const { data } = await api.patch(`/sets/${setId}/documents/${docId}/overrides`, { overrides }); return data
}

export async function confirmDocumentReview(
  setId: string, docId: string,
): Promise<{ doc_id: string; reviewed_by: string; reviewed_at: string }> {
  const { data } = await api.post(`/sets/${setId}/documents/${docId}/review-confirmation`)
  return data
}

export async function getOverrides(setId: string, docId: string): Promise<{ overrides: Record<string, string>; count: number }> {
  const { data } = await api.get(`/sets/${setId}/documents/${docId}/overrides`); return data
}

export async function retryExtraction(setId: string, docId: string): Promise<{ doc_id: string; status: string; message: string }> {
  const { data } = await api.post(`/sets/${setId}/documents/${docId}/retry-extraction`); return data
}

export async function cancelExtraction(setId: string, docId: string): Promise<{ cancelled: boolean; doc_id: string; message: string }> {
  const { data } = await api.post(`/sets/${setId}/documents/${docId}/cancel-extraction`); return data
}

/** Return a same-origin resource path. Authentication is sent as a header. */
export function getDocumentFileUrl(setId: string, docId: string): string {
  return `/api/sets/${setId}/documents/${docId}/file`
}

/** Open one source file inside an uploaded raw-record ZIP. */
export function getArchiveMemberFileUrl(setId: string, docId: string, memberIndex: number): string {
  return `/api/sets/${setId}/documents/${docId}/archive-members/${memberIndex}/file`
}

/** Render one evidence source page as a highlighted PNG. */
export function getEvidencePagePreviewUrl(setId: string, graphId: string, evidenceId: string): string {
  // This version bypasses preview images cached before normalized highlighting
  // was introduced. The endpoint is no-store, so later renderer changes do not
  // require another cache migration.
  return `/api/sets/${setId}/review-runs/${graphId}/evidence/${evidenceId}/preview?renderer=normalized-quote-v2`
}

export async function fetchAuthenticatedBlob(url: string): Promise<Blob> {
  const response = await api.get(url.startsWith('/api/') ? url.slice(4) : url, { responseType: 'blob' })
  return response.data
}

export async function openAuthenticatedResource(url: string): Promise<void> {
  const popup = window.open('', '_blank')
  try {
    const objectUrl = URL.createObjectURL(await fetchAuthenticatedBlob(url))
    if (popup) popup.location.href = objectUrl
    else {
      const link = document.createElement('a')
      link.href = objectUrl
      link.target = '_blank'
      link.rel = 'noreferrer'
      link.click()
    }
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
  } catch (error) {
    popup?.close()
    throw error
  }
}

export async function getArchiveMemberInventory(setId: string, docId: string): Promise<ArchiveMemberInventory> {
  const { data } = await api.get(`/sets/${setId}/documents/${docId}/archive-members`)
  return data
}
