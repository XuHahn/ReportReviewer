import { useState, useEffect, useCallback, useRef } from 'react'
import {
  createDocumentSet, getDocumentSet, addDocumentToSet, deleteDocument, lockDocumentSet,
  triggerReview, getPipelineIssues, annotateIssue, getProjectGroups, saveOverrides, confirmDocumentReview,
  retryExtraction, cancelExtraction, getPipelineMetrics, downloadAuditReportExcel,
  getStandards, getDocumentSetStandardReferences, updateDocumentSetStandards, streamReviewProgress,
  updateDocumentSetProjectGroup,
} from '../api'
import type { DocType, FileStatus, DocumentSetOverview, PipelineIssue, PipelineResultSummary, PipelineMetricsResponse, EmcStandard } from '../types'
import DocumentReviewModal from './DocumentReviewModal'
import VersionHistoryModal from './VersionHistoryModal'
import IssueDocumentMatrix from './IssueDocumentMatrix'
import Icon from './Icons'
import { logger } from '../logger'

// ── Doc type metadata ────────────────────────────────────────────────────────

interface DocSlotMeta {
  order: number
  docType: DocType
  label: string
  format: string
  required: boolean
}

const DOC_SLOTS: DocSlotMeta[] = [
  { order: 0, docType: 'order_form',      label: '委托单',   format: '.xls 格式',    required: true  },
  { order: 1, docType: 'test_plan',        label: '试验计划', format: 'PDF / DOCX',   required: true  },
  { order: 2, docType: 'original_records', label: '原始记录', format: 'ZIP 压缩包',   required: true  },
  { order: 3, docType: 'final_report',     label: '检测报告', format: 'PDF / DOCX',   required: true  },
]

const ICON_COLORS: Record<DocType, string> = {
  order_form:      '#4f46e5',
  test_plan:        '#16a34a',
  original_records: '#ea580c',
  final_report:     '#4f46e5',
  test_standard:    '#64748b',
}

const DOC_LABELS: Record<DocType, string> = {
  order_form: '委托单', test_plan: '试验计划', original_records: '原始记录',
  final_report: '检测报告', test_standard: '测试标准',
}

// ── SVG Icons ─────────────────────────────────────────────────────────────────

function IconDocOrder() { return (<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="7" y1="9" x2="17" y2="9" opacity=".5"/><line x1="7" y1="12" x2="14" y2="12" opacity=".3"/></svg>) }
function IconDocPlan() { return (<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="3" y="4" width="18" height="16" rx="2"/><rect x="7" y="8" width="4" height="4" rx="1" opacity=".5"/><line x1="13" y1="10" x2="17" y2="10" opacity=".3"/></svg>) }
function IconDocRecords() { return (<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="2" y="6" width="20" height="14" rx="2"/><path d="M2 10h20" opacity=".5"/><rect x="6" y="13" width="12" height="4" rx="1" strokeDasharray="1.5,1.5" opacity=".4"/></svg>) }
function IconDocReport() { return (<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="4" y="2" width="16" height="20" rx="2"/><polyline points="8 12 11 16 16 8" strokeLinecap="round" strokeLinejoin="round"/></svg>) }
function IconDocStandard() { return (<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M4 4h16v16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z"/><path d="M4 4l3-2h10l3 2"/></svg>) }

const DOC_ICONS: Record<DocType, JSX.Element> = {
  order_form: <IconDocOrder />, test_plan: <IconDocPlan />, original_records: <IconDocRecords />,
  final_report: <IconDocReport />, test_standard: <IconDocStandard />,
}

function IconCheck({ size }: { size?: number }) { const s = size || 12; return (<svg width={s} height={s} viewBox="0 0 20 20" style={{display:'inline-block',verticalAlign:'middle'}}><circle cx="10" cy="10" r="8" stroke="currentColor" strokeWidth="1.5" fill="none"/><path d="M6 10l3 3 5-6" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round"/></svg>) }
function IconWarn({ size }: { size?: number }) { const s = size || 12; return (<svg width={s} height={s} viewBox="0 0 20 20" style={{display:'inline-block',verticalAlign:'middle'}}><path d="M10 3L2 20h16L10 3z" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinejoin="round"/><line x1="10" y1="10" x2="10" y2="14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/><circle cx="10" cy="16.5" r=".8" fill="currentColor"/></svg>) }
function IconSpinner() { return (<svg width="12" height="12" viewBox="0 0 24 24" style={{animation:'spin .7s linear infinite'}}><circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2" fill="none" opacity=".25"/><path d="M12 3a9 9 0 019 9" stroke="currentColor" strokeWidth="2" fill="none"/></svg>) }

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatSize(kb: number): string {
  if (kb >= 1024) return `${(kb / 1024).toFixed(1)}MB`
  return `${kb}KB`
}

function fileForType(files: FileStatus[], docType: DocType): FileStatus | undefined {
  return files.find(f => f.doc_type === docType)
}

type SlotStatus = 'empty' | 'uploading' | 'extracting' | 'done' | 'failed'

function getSlotStatus(meta: DocSlotMeta, files: FileStatus[], uploading: Record<string, boolean>): SlotStatus {
  if (uploading[meta.docType]) return 'uploading'
  const fs = fileForType(files, meta.docType)
  if (!fs) return 'empty'
  if (fs.extraction_status === 'done' || fs.extraction_status === 'partial') return 'done'
  if (fs.extraction_status === 'failed') return 'failed'
  if (fs.extraction_status === 'extracting' || fs.extraction_status === 'pending') return 'extracting'
  return 'empty'
}

const METRIC_STAGE_LABELS: Record<string, string> = {
  pipeline_done: '整轮审核完成',
  report_driven_summary: '报告驱动审核汇总',
  module_llm_audit: '测试模块语义审核',
  legacy_cross_validator: '补充一致性检查',
  report_driven_deterministic: '四文件确定性核对',
  identity_resolution: '测试项名称识别',
  load_extraction: '读取文档提取结果',
}

const METRIC_TYPE_LABELS: Record<string, string> = {
  pipeline_run: '整轮审核',
  validation_stage: '一致性审核阶段',
  module_audit: '测试模块审核',
  identity_resolution: '测试项名称识别',
  document_input: '文档数据读取',
}

const METRIC_STATUS_LABELS: Record<string, string> = {
  partial: '已完成（发现问题）',
  done: '已完成',
  complete: '已完成',
  cached: '已使用缓存',
  loaded: '已读取',
  failed: '失败',
  error: '异常',
  skipped: '已跳过',
  running: '进行中',
  active: '进行中',
  passed: '已通过',
}

const METRIC_MODULE_LABELS: Record<string, string> = {
  order_form: '委托单',
  test_plan: '试验计划',
  raw_records: '原始记录',
  original_records: '原始记录',
  final_report: '检测报告',
}

function metricStageLabel(stage: string, metricType: string): string {
  return METRIC_STAGE_LABELS[stage] || METRIC_TYPE_LABELS[metricType] || stage || metricType || '审核阶段'
}

function metricModuleLabel(moduleKey: string, displayCode?: unknown): string {
  const rawValue = moduleKey || (typeof displayCode === 'string' ? displayCode : '')
  const cleanValue = rawValue.replace(/^NAME:/i, '').trim()
  return METRIC_MODULE_LABELS[cleanValue] || cleanValue || '—'
}

function metricStatusLabel(status: string): string {
  return METRIC_STATUS_LABELS[status] || status || '已完成'
}

function formatMetricDuration(durationMs: number): string {
  const safeDuration = Math.max(0, Number(durationMs) || 0)
  if (safeDuration >= 60_000) {
    const minutes = Math.floor(safeDuration / 60_000)
    const seconds = (safeDuration % 60_000) / 1000
    return `${minutes}分${seconds.toFixed(1)}秒`
  }
  if (safeDuration < 1000) return `${Math.round(safeDuration)}毫秒`
  return `${(safeDuration / 1000).toFixed(1)}秒`
}

// ── Pipeline progress steps ──────────────────────────────────────────────────

// Fallback steps used when SSE is unavailable
const FALLBACK_PIPELINE_STEPS = [
  '报告模块切分与原始记录匹配',
  '模块开放参数与语义审核',
  '报告驱动审核汇总',
  '封面完整性与签发日期',
  '余量公式验证',
  '超标检测',
  '限值参考校验',
  '背景噪声校验',
  '结论与测试等级一致性',
  '目录与测试项覆盖',
  '样品标识一致性',
  '备注与 DUT 状态一致性',
  '限值表频率范围连续性',
  '试验步骤标准引用合规',
  '供应商与客户字段一致性',
  '基本信息与计划编号一致性',
  '测试项三方覆盖',
  '开放参数一致性',
  '性能判据等级一致性',
  '操作步骤一致性',
  '日期跨度与物理范围',
  '仪器清单与校准有效期',
]

// ── Component ────────────────────────────────────────────────────────────────

interface DocumentSetUploaderProps {
  revisionTarget?: { setId: string; nonce: number } | null
  onReviewComplete?: (setId: string) => void
}

export default function DocumentSetUploader({ revisionTarget, onReviewComplete }: DocumentSetUploaderProps) {
  // Core state
  const [setId, setSetId] = useState<string | null>(null)
  const [setStatus, setSetStatus] = useState<string>('new')
  const [files, setFiles] = useState<FileStatus[]>([])
  const [locked, setLocked] = useState(false)

  // Upload tracking
  const [uploading, setUploading] = useState<Record<string, boolean>>({})
  const [error, setError] = useState<string | null>(null)

  // Review state
  const [reviewedDocs, setReviewedDocs] = useState<Set<string>>(new Set())
  const [editedCounts, setEditedCounts] = useState<Record<string, number>>({})
  const [reviewModal, setReviewModal] = useState<DocType | null>(null)
  const [versionModal, setVersionModal] = useState<DocType | null>(null)
  const [deleting, setDeleting] = useState<Record<string, boolean>>({})

  // Reusable standard knowledge selection (optional)
  const [selectedStandards, setSelectedStandards] = useState<EmcStandard[]>([])
  const [standardModalOpen, setStandardModalOpen] = useState(false)
  const [availableStandards, setAvailableStandards] = useState<EmcStandard[]>([])
  const [standardDraftIds, setStandardDraftIds] = useState<Set<string>>(new Set())
  const [standardSearch, setStandardSearch] = useState('')
  const [standardLoading, setStandardLoading] = useState(false)
  const [standardReferences, setStandardReferences] = useState<Array<{ reference_code: string; normalized_code: string; sources: string[]; evidence: string[] }>>([])
  const [standardSkipReasons, setStandardSkipReasons] = useState<Record<string, string>>({})

  // Pipeline state
  const [pipelineRunning, setPipelineRunning] = useState(false)
  const [pipelineResult, setPipelineResult] = useState<PipelineResultSummary | null>(null)
  const [pipelineIssues, setPipelineIssues] = useState<PipelineIssue[]>([])
  const [pipelineSteps, setPipelineSteps] = useState<string[]>(FALLBACK_PIPELINE_STEPS)
  const [pipelineStepStatuses, setPipelineStepStatuses] = useState<Record<number, 'wait' | 'active' | 'done'>>({})
  const [pipelineStepDurations, setPipelineStepDurations] = useState<Record<number, number>>({})
  const [pipelineMetrics, setPipelineMetrics] = useState<PipelineMetricsResponse | null>(null)
  const [annotationLoading, setAnnotationLoading] = useState<Record<string, boolean>>({})
  const [exportingExcel, setExportingExcel] = useState(false)
  const [expandedIssues, setExpandedIssues] = useState<Set<string>>(new Set())
  const [selectedSource, setSelectedSource] = useState<{issueId: string, srcIdx: number} | null>(null)

  // Group picker
  const [selectedGroup, setSelectedGroup] = useState('')
  const [selectedGroupId, setSelectedGroupId] = useState('')
  const [groupModalOpen, setGroupModalOpen] = useState(false)
  const [groupPickSearch, setGroupPickSearch] = useState('')
  const [groupPickCat, setGroupPickCat] = useState('全部')
  const [pickGroups, setPickGroups] = useState<any[]>([])
  const [pickCats, setPickCats] = useState<string[]>(['全部'])
  const [pickCatMap, setPickCatMap] = useState<Map<string, any[]>>(new Map())
  const [quickGroups, setQuickGroups] = useState<any[]>([])

  // Confirm dialog
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [locking, setLocking] = useState(false)

  // Toast
  const [toast, setToast] = useState<string | null>(null)
  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(null), 3000) }

  // Refs
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const fileInputRefs = useRef<Record<string, HTMLInputElement | null>>({})
  const replaceDocRef = useRef<string>('')
  const selectedSourceRef = useRef(selectedSource)
  const createSetLock = useRef<Promise<string> | null>(null)  // prevent concurrent set creation
  const uploadAbortControllers = useRef<Record<string, AbortController>>({})  // per-docType cancel tokens
  useEffect(() => { selectedSourceRef.current = selectedSource }, [selectedSource])

  // ── Load an existing set when "My Reports" opens a revision draft ───────────

  const loadExistingSet = useCallback(async (targetSetId: string, asRevision = false) => {
    setError(null)
    try {
      const overview = await getDocumentSet(targetSetId)
      const loadedFiles = overview.files || []
      setSetId(overview.set_id)
      setSetStatus(overview.status)
      setFiles(loadedFiles)
      setSelectedStandards(overview.standards || [])
      setSelectedGroup(overview.project_group_name || '')
      setSelectedGroupId(overview.project_group_id || '')
      setLocked(overview.status === 'locked' || overview.status === 'reviewing')
      setPipelineRunning(false)
      setPipelineResult(null)
      setPipelineIssues([])
      setPipelineMetrics(null)
      setPipelineStepStatuses({})
      setPipelineStepDurations({})
      setExpandedIssues(new Set())
      setSelectedSource(null)

      setReviewedDocs(new Set(
        loadedFiles.filter(file => Boolean(file.reviewed_at)).map(file => file.doc_type),
      ))

      if (asRevision || overview.status === 'revision') {
        showToast('已载入修订草稿，请替换需要修改的文件')
      }
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e.message || '载入修订草稿失败'
      setError(msg)
      logger.error('Failed to load existing document set', { component: 'DocumentSetUploader', setId: targetSetId }, e)
    }
  }, [])

  useEffect(() => {
    if (!revisionTarget?.setId) return
    loadExistingSet(revisionTarget.setId, true)
  }, [revisionTarget?.setId, revisionTarget?.nonce, loadExistingSet])

  const loadPipelineMetrics = useCallback(async (targetSetId: string, runId?: string) => {
    try {
      const metrics = await getPipelineMetrics(targetSetId, runId)
      setPipelineMetrics(metrics.metrics?.length ? metrics : null)
    } catch (e) {
      logger.warn('Failed to load pipeline metrics', {
        component: 'DocumentSetUploader',
        setId: targetSetId,
        error: e instanceof Error ? e.message : String(e),
      })
    }
  }, [])

  useEffect(() => {
    if (!setId || pipelineRunning) return
    if (setStatus === 'reviewed' || pipelineIssues.length > 0 || pipelineResult) {
      loadPipelineMetrics(setId, pipelineResult?.run_id)
    }
  }, [setId, setStatus, pipelineRunning, pipelineIssues.length, pipelineResult?.run_id, loadPipelineMetrics])

  // ── Poll extraction status ──────────────────────────────────────────────────

  const pollStatus = useCallback(async () => {
    if (!setId || locked) return
    try {
      const overview: DocumentSetOverview = await getDocumentSet(setId)
      setFiles(overview.files || [])
      setSelectedStandards(overview.standards || [])
      setSetStatus(overview.status)
      if (overview.status === 'locked') {
        setLocked(true)
      }
    } catch (e: any) {
      logger.error('Failed to poll document set status', { component: 'DocumentSetUploader', setId }, e)
    }
  }, [setId, locked])

  useEffect(() => {
    if (!setId || locked) {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
      return
    }
    pollStatus()
    pollRef.current = setInterval(pollStatus, 2000)
    return () => {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
    }
  }, [setId, locked, pollStatus])

  // ── Load quick groups ───────────────────────────────────────────────────────

  useEffect(() => {
    getProjectGroups().then(gs => {
      setQuickGroups((gs || []).slice(0, 5))
    }).catch((e: any) => {
      logger.error('Failed to load project groups', { component: 'DocumentSetUploader' }, e)
    })
  }, [])

  // ── Clean up polling on auth failure ────────────────────────────────────────

  useEffect(() => {
    const handleAuthLogout = () => {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
    }
    window.addEventListener('auth:logout', handleAuthLogout)
    return () => window.removeEventListener('auth:logout', handleAuthLogout)
  }, [])

  // ── Esc key: dismiss trace panel then collapse cards ───────────────────────

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (selectedSourceRef.current) {
          setSelectedSource(null)
        } else {
          setExpandedIssues(new Set())
        }
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  // ── Group picker logic ──────────────────────────────────────────────────────

  async function openGroupPick() {
    try {
      const gs = await getProjectGroups()
      const cats = new Map<string, any[]>()
      cats.set('全部', gs)
      for (const g of gs) {
        const cat = (g as any).category || '其他'
        if (!cats.has(cat)) cats.set(cat, [])
        cats.get(cat)!.push(g)
      }
      setPickGroups(gs)
      setPickCats(Array.from(cats.keys()))
      setPickCatMap(cats)
      setGroupPickSearch('')
      setGroupPickCat('全部')
      setGroupModalOpen(true)
    } catch (e: any) {
      logger.error('Failed to open group picker', { component: 'DocumentSetUploader' }, e)
    }
  }

  const groupPickFiltered = (() => {
    const base = groupPickCat === '全部' ? pickGroups : (pickCatMap.get(groupPickCat) || [])
    if (!groupPickSearch.trim()) return base
    const q = groupPickSearch.toLowerCase()
    return base.filter((g: any) => g.name.toLowerCase().includes(q) || (g.description || '').toLowerCase().includes(q))
  })()

  async function selectProjectGroup(groupId: string, name: string) {
    const nextId = selectedGroupId === groupId ? '' : groupId
    const nextName = nextId ? name : ''
    const previousId = selectedGroupId
    const previousName = selectedGroup
    setSelectedGroupId(nextId)
    setSelectedGroup(nextName)
    if (!setId) return
    try {
      await updateDocumentSetProjectGroup(setId, nextId)
    } catch (e: any) {
      setSelectedGroupId(previousId)
      setSelectedGroup(previousName)
      showToast(e?.response?.data?.detail || '项目组保存失败')
    }
  }

  async function openStandardPicker() {
    if (locked || pipelineRunning) return
    setStandardLoading(true)
    setStandardSearch('')
    try {
      const sid = await ensureSet()
      const [list, referenceResult] = await Promise.all([
        getStandards(), getDocumentSetStandardReferences(sid),
      ])
      setAvailableStandards(list.filter((item) => item.knowledge_status === 'ready' && item.graph_status === 'published'))
      setStandardReferences(referenceResult.references || [])
      setStandardSkipReasons(Object.fromEntries((referenceResult.skips || []).map(item => [item.normalized_code, item.reason])))
      setStandardDraftIds(new Set(selectedStandards.map((item) => item.id)))
      setStandardModalOpen(true)
    } catch (e: any) {
      showToast(e?.response?.data?.detail || '标准库加载失败')
    } finally {
      setStandardLoading(false)
    }
  }

  function toggleStandardDraft(id: string) {
    setStandardDraftIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  function isReferenceSelected(reference: { normalized_code: string }) {
    return availableStandards.some(standard => {
      if (!standardDraftIds.has(standard.id)) return false
      const code = standard.normalized_code || standard.code.toUpperCase().replace(/[^A-Z0-9\u4e00-\u9fff]+/g, '')
      const version = (standard.version || '').toUpperCase().replace(/[^A-Z0-9]+/g, '')
      return reference.normalized_code === code
        || (!!version && [code + version, code + version.slice(-4)].includes(reference.normalized_code))
    })
  }

  const unresolvedStandardReferences = standardReferences.filter(reference => (
    !isReferenceSelected(reference) && !standardSkipReasons[reference.normalized_code]?.trim()
  ))

  async function saveStandardSelection() {
    setStandardLoading(true)
    try {
      const sid = await ensureSet()
      const result = await updateDocumentSetStandards(
        sid,
        Array.from(standardDraftIds),
        standardReferences.filter(reference => standardSkipReasons[reference.normalized_code]?.trim()).map(reference => ({
          normalized_code: reference.normalized_code,
          reference_code: reference.reference_code,
          reason: standardSkipReasons[reference.normalized_code].trim(),
        })),
      )
      setSelectedStandards(result.standards || [])
      setStandardModalOpen(false)
      showToast(result.standard_review_enabled ? `已选择 ${result.standards.length} 份标准` : '未选择标准，将跳过标准条款审核')
    } catch (e: any) {
      showToast(e?.response?.data?.detail || '标准选择保存失败')
    } finally {
      setStandardLoading(false)
    }
  }

  // ── Init document set ───────────────────────────────────────────────────────

  async function ensureSet(): Promise<string> {
    if (setId) return setId
    // Serialise concurrent calls so only one set is ever created
    if (createSetLock.current) {
      try { await createSetLock.current } catch { /* previous attempt failed — retry below */ }
      if (setId) return setId
    }
    const promise = (async () => {
      try {
        const { set_id } = await createDocumentSet()
        if (selectedGroupId) {
          await updateDocumentSetProjectGroup(set_id, selectedGroupId)
        }
        setSetId(set_id)
        setSetStatus('open')
        return set_id
      } catch (e: any) {
        const msg = '创建文档集失败，请重试'
        setError(msg)
        logger.error(msg, { component: 'DocumentSetUploader' }, e)
        throw e
      } finally {
        createSetLock.current = null
      }
    })()
    createSetLock.current = promise
    return promise
  }

  // ── File upload handler ─────────────────────────────────────────────────────

  async function handleSlotUpload(docType: DocType, replaceDocId?: string) {
    replaceDocRef.current = replaceDocId || ''
    const input = fileInputRefs.current[docType]
    if (!input) return
    input.click()
  }

  async function handleDelete(docType: DocType, docId?: string) {
    const sid = setId
    if (!docId || !sid) {
      showToast(`删除失败：缺少参数 (setId=${sid}, docId=${docId})`)
      return
    }
    setDeleting(prev => ({ ...prev, [docType]: true }))
    try {
      const result = await deleteDocument(sid, docId)
      if (!result.deleted) {
        showToast('删除失败：文档未找到')
        return
      }
      const overview = await getDocumentSet(sid)
      setFiles(overview.files || [])
      setSetStatus(overview.status)
      setReviewedDocs(prev => { const n = new Set(prev); n.delete(docType); return n })
      showToast(`已删除 ${DOC_LABELS[docType]} 文件`)
    } catch (e: any) {
      const resp = (e as any)?.response
      const status = resp?.status || ''
      const detail = resp?.data?.detail || (e as any).message || '未知错误'
      logger.error('Delete document failed', { component: 'DocumentSetUploader', docType, setId: sid, docId, status, detail })
      showToast(`删除失败 [${status}]: ${detail}`)
    } finally {
      setDeleting(prev => ({ ...prev, [docType]: false }))
    }
  }

  async function handleRetryExtraction(docType: DocType, docId: string) {
    if (!setId || !docId) return
    setUploading(prev => ({ ...prev, [docType]: true }))
    try {
      const result = await retryExtraction(setId, docId)
      showToast(result.message || `${DOC_LABELS[docType]} 重新提取已触发`)
      // Poll immediately to update UI
      setTimeout(async () => {
        try {
          const overview = await getDocumentSet(setId)
          setFiles(overview.files || [])
        } catch { /* ignore */ }
      }, 1500)
    } catch (e: any) {
      const detail = (e as any)?.response?.data?.detail || (e as any).message || '未知错误'
      showToast(`重试提取失败: ${detail}`)
      logger.error('Retry extraction failed', { component: 'DocumentSetUploader', docType, docId }, e)
    } finally {
      setUploading(prev => ({ ...prev, [docType]: false }))
    }
  }

  async function handleCancelUpload(docType: DocType) {
    // Cancel in-progress upload
    const ctrl = uploadAbortControllers.current[docType]
    if (ctrl) {
      ctrl.abort()
      delete uploadAbortControllers.current[docType]
    }
    setUploading(prev => ({ ...prev, [docType]: false }))

    // Also try cancelling extraction if doc exists
    const fs = fileForType(files, docType)
    if (fs?.doc_id && setId) {
      try {
        await cancelExtraction(setId, fs.doc_id)
        showToast(`${DOC_LABELS[docType]} 上传/提取已取消`)
        // Refresh to get updated status
        const overview = await getDocumentSet(setId)
        setFiles(overview.files || [])
      } catch { /* best-effort */ }
    } else {
      showToast(`${DOC_LABELS[docType]} 上传已取消`)
    }
  }

  async function handleFileSelected(docType: DocType, e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    e.target.value = ''
    setError(null)

    // Cancel any previous upload for this slot
    const prevCtrl = uploadAbortControllers.current[docType]
    if (prevCtrl) { prevCtrl.abort(); delete uploadAbortControllers.current[docType] }

    // Create new abort controller for this upload
    const abortCtrl = new AbortController()
    uploadAbortControllers.current[docType] = abortCtrl
    setUploading(prev => ({ ...prev, [docType]: true }))

    let sid = ''
    try {
      sid = await ensureSet()
      const replaceId = replaceDocRef.current
      replaceDocRef.current = ''
      await addDocumentToSet(sid, file, docType, replaceId || undefined, abortCtrl.signal)
      setReviewedDocs(prev => {
        const next = new Set(prev)
        next.delete(docType)
        return next
      })
      setPipelineResult(null)
      setPipelineIssues([])
      setExpandedIssues(new Set())
      setSelectedSource(null)
      // Refresh file list — do it twice with a short gap to ensure DB write is visible
      const refreshFiles = async () => {
        try {
          const overview = await getDocumentSet(sid)
          setFiles(overview.files || [])
          setSetStatus(overview.status)
        } catch (e: any) {
          logger.error('Failed to refresh after upload', { component: 'DocumentSetUploader', docType }, e)
        }
      }
      await refreshFiles()
      setTimeout(refreshFiles, 800)
      delete uploadAbortControllers.current[docType]
      setUploading(prev => ({ ...prev, [docType]: false }))
    } catch (e: any) {
      const axiosErr = e as any
      const isCancelled = axiosErr?.code === 'ERR_CANCELED' || axiosErr?.message?.includes('cancel')
      const isTimeout = axiosErr?.code === 'ECONNABORTED' || axiosErr?.message?.includes('timeout')

      if (isCancelled) {
        // User cancelled — silently clean up, no error banner
        delete uploadAbortControllers.current[docType]
        setUploading(prev => ({ ...prev, [docType]: false }))
      } else if (isTimeout) {
        // File may have been saved despite timeout — poll with backoff.
        // Keep uploading=true so the card stays in "上传中..." during recovery.
        delete uploadAbortControllers.current[docType]
        setError(null)
        // Note: do NOT set uploading=false here; keep it true for recovery UI
        const pollRecovery = async (delay: number, remaining: number) => {
          if (remaining <= 0) {
            setUploading(prev => ({ ...prev, [docType]: false }))
            setError(`上传超时，${DOC_LABELS[docType]} 未能成功保存。请重试上传。`)
            return
          }
          await new Promise(r => setTimeout(r, delay))
          try {
            const overview = await getDocumentSet(sid)
            const found = (overview.files || []).some(f => f.doc_type === docType)
            if (found) {
              setFiles(overview.files || [])
              setSetStatus(overview.status)
              setUploading(prev => ({ ...prev, [docType]: false }))
              showToast(`${DOC_LABELS[docType]} 已在后台完成上传`)
            } else {
              // Not found yet — poll again with longer delay
              pollRecovery(Math.min(delay * 2, 10000), remaining - 1)
            }
          } catch {
            pollRecovery(Math.min(delay * 2, 10000), remaining - 1)
          }
        }
        pollRecovery(2000, 5)  // up to 5 retries: 2s, 4s, 8s, 10s, 10s ≈ 34s total
      } else {
        delete uploadAbortControllers.current[docType]
        const msg = `上传失败: ${axiosErr?.response?.data?.detail || axiosErr?.message || '未知错误'}`
        setError(msg)
        setUploading(prev => ({ ...prev, [docType]: false }))
        logger.error('Upload failed', { component: 'DocumentSetUploader', docType }, e)
      }
    }
  }

  // ── Review handlers ─────────────────────────────────────────────────────────

  function markReviewed(docType: DocType) {
    setReviewedDocs(prev => {
      const next = new Set(prev)
      next.add(docType)
      return next
    })
  }

  // ── Lock and submit pipeline ────────────────────────────────────────────────

  async function handleLockAndSubmit() {
    setConfirmOpen(false)
    if (!setId) return
    setLocking(true)
    try {
      await updateDocumentSetProjectGroup(setId, selectedGroupId)
      await lockDocumentSet(setId)
      setLocked(true)
      setSetStatus('locked')

      setPipelineRunning(true)
      setPipelineSteps([])
      setPipelineStepStatuses({})
      setPipelineStepDurations({})

      // Open an authenticated fetch stream before triggering the review so the
      // bearer token never appears in URLs or access logs.
      const progressAbort = new AbortController()
      const useSse = typeof ReadableStream !== 'undefined'

      if (useSse) {
        streamReviewProgress(setId, (data) => {
          try {
            const step = Number(data.step)
            const total = Number(data.total)
            if (data.label && Number.isFinite(step) && step > 0) {
              if (data.status === 'active' || data.status === 'done') {
                setPipelineStepStatuses(prev => ({ ...prev, [step]: data.status }))
              }
              const durationMs = Number(data.duration_ms)
              if (data.status === 'done' && Number.isFinite(durationMs) && durationMs >= 0) {
                setPipelineStepDurations(prev => ({ ...prev, [step]: durationMs }))
              }
              setPipelineSteps(prev => {
                const next = [...prev]
                const targetLength = Number.isFinite(total) && total > 0
                  ? total
                  : Math.max(next.length, step)
                while (next.length < targetLength) {
                  next.push(`步骤 ${next.length + 1}`)
                }
                next[step - 1] = data.label
                return next
              })
            }
            if (data.status === 'complete' || data.status === 'error') {
              progressAbort.abort()
            }
          } catch {}
        }, progressAbort.signal).catch(error => {
          if (error?.name !== 'AbortError') {
            logger.warn('Pipeline progress stream failed', {
              component: 'DocumentSetUploader', setId, error: String(error),
            })
          }
        })
      }

      if (!useSse) {
        // Without stream support, do not invent per-step progress or timing.
        setPipelineSteps(['审核执行中（当前浏览器不支持实时步骤）'])
        setPipelineStepStatuses({ 1: 'active' })
      }

      try {
        const result = await triggerReview(setId)
        setPipelineResult(result)
        setSetStatus('reviewed')
        showToast('审核完成！')

        const { issues } = await getPipelineIssues(setId)
        setPipelineIssues(issues)
        await loadPipelineMetrics(setId, result.run_id)
        onReviewComplete?.(setId)
      } catch (e: any) {
        logger.error('Pipeline trigger failed', { component: 'DocumentSetUploader', setId }, e)
        const errorMessage = (e as any)?.response?.data?.detail || (e as any).message || '未知错误'
        let previousIssues: PipelineIssue[] = []
        try {
          const existing = await getPipelineIssues(setId)
          previousIssues = existing.issues || []
          setPipelineIssues(previousIssues)
        } catch (issueErr) {
          logger.warn('Failed to reload previous pipeline issues after review failure', {
            component: 'DocumentSetUploader',
            setId,
            error: issueErr instanceof Error ? issueErr.message : String(issueErr),
          })
        }
        // The lock request already succeeded before review startup. A model
        // preflight/runtime failure leaves the backend set locked so it can be
        // retried from the task workbench; mirror that persisted state instead
        // of presenting a non-existent editable revision locally.
        setLocked(true)
        setSetStatus('locked')
        setError(`审核未完成：${errorMessage}`)
        setPipelineResult({
          set_id: setId, status: 'error', is_clean: false,
          duration_seconds: 0, issues: { critical: 0, warning: 0, info: 0 },
          errors: [
            previousIssues.length > 0
              ? `${errorMessage}；已保留上一轮 ${previousIssues.length} 条问题结果`
              : errorMessage,
          ],
        })
        await loadPipelineMetrics(setId)
        showToast('审核未完成，已保留上一轮结果')
      } finally {
        progressAbort.abort()
      }
    } catch (e: any) {
      const msg = `锁定失败: ${(e as any)?.response?.data?.detail || (e as any).message || '未知错误'}`
      setError(msg)
      logger.error('Lock failed', { component: 'DocumentSetUploader', setId }, e)
    } finally {
      setLocking(false)
      setPipelineRunning(false)
    }
  }

  // ── Issue annotation ────────────────────────────────────────────────────────

  async function handleAnnotate(issueId: string, status: string) {
    if (!setId) return
    setAnnotationLoading(prev => ({ ...prev, [issueId]: true }))
    try {
      await annotateIssue(setId, issueId, status)
      setPipelineIssues(prev =>
        prev.map(iss => iss.id === issueId ? { ...iss, human_status: status } : iss)
      )
      showToast(status === 'confirmed' ? '已标记为确认' : status === 'ignored' ? '已标记为忽略' : '已重置')
    } catch (e: any) {
      logger.error('Annotation failed', { component: 'DocumentSetUploader', issueId }, e)
    } finally {
      setAnnotationLoading(prev => ({ ...prev, [issueId]: false }))
    }
  }

  async function handleExportAuditExcel() {
    if (!setId) {
      showToast('暂无可导出的文档集')
      return
    }
    setExportingExcel(true)
    try {
      const { blob, filename } = await downloadAuditReportExcel(setId)
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)
      showToast('审核报告 Excel 已导出')
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e.message || '导出审核报告失败'
      showToast(msg)
      logger.error('Failed to export audit report Excel', { component: 'DocumentSetUploader', setId }, e)
    } finally {
      setExportingExcel(false)
    }
  }

  // ── Computed ────────────────────────────────────────────────────────────────

  const requiredSlots = DOC_SLOTS.filter(s => s.required)
  const requiredDone = requiredSlots.every(s => getSlotStatus(s, files, uploading) === 'done')
  const allReviewed = requiredSlots.every(s => reviewedDocs.has(s.docType))
  const doneCount = DOC_SLOTS.filter(s => getSlotStatus(s, files, uploading) === 'done').length
  const canLock = requiredDone && allReviewed && !locked

  // ── Step indicator state ────────────────────────────────────────────────────

  const step1Done = requiredDone                                     // all 4 required extracted
  const step2Active = requiredDone && !locked                        // ready for human review
  const step2Done = allReviewed                                      // all human-reviewed (persists after lock)
  const step3Active = locked || pipelineRunning                      // lock or pipeline running
  const step3Done = setStatus === 'reviewed'                         // pipeline finished

  // ── Results summary ─────────────────────────────────────────────────────────

  const resultCritical = pipelineIssues.filter(i => i.severity === 'CRITICAL').length
  const resultWarning = pipelineIssues.filter(i => i.severity === 'WARNING').length
  const resultInfo = pipelineIssues.filter(i => i.severity === 'INFO').length
  const resultCategoryCount = new Set(pipelineIssues.map(i => i.category || '未分类')).size
  const showResults = (locked && !pipelineRunning) || pipelineIssues.length > 0 || pipelineResult !== null
  const pipelineFailed = pipelineResult?.status === 'error' || Boolean(pipelineResult?.errors?.length)
  const resultHeaderText = pipelineFailed
    ? (pipelineIssues.length > 0 ? `${pipelineIssues.length} 条上一轮问题 · 本次审核未完成` : '本次审核未完成')
    : (pipelineIssues.length > 0 ? `${pipelineIssues.length} 条明细 · ${resultCategoryCount} 类问题` : '无问题')
  const passSummary = pipelineFailed
    ? '失败'
    : pipelineIssues.length > 0
      ? resultCategoryCount
      : (pipelineResult?.is_clean ? '通过' : `${pipelineSteps.length - (pipelineResult?.errors?.length || 0)}/${pipelineSteps.length}`)
  const metricItems = pipelineMetrics?.metrics || []
  const failedMetricCount = metricItems.filter(m => ['failed', 'error'].includes(m.status)).length
  const moduleMetricCount = metricItems.filter(m => m.metric_type === 'module_audit').length
  const pipelineRunDuration = metricItems
    .filter(m => m.metric_type === 'pipeline_run' || m.stage === 'pipeline_done')
    .reduce((maxDuration, m) => Math.max(maxDuration, Number(m.duration_ms) || 0), 0)
  const totalMetricDuration = Number(pipelineMetrics?.summary?.duration_ms) || pipelineRunDuration || metricItems
    .reduce((maxDuration, m) => Math.max(maxDuration, Number(m.duration_ms) || 0), 0)
  const slowestMetrics = [...metricItems]
    .filter(m => Number(m.duration_ms) > 0)
    .sort((a, b) => Number(b.duration_ms || 0) - Number(a.duration_ms || 0))
    .slice(0, 6)
  const modeLabel = setStatus === 'revision'
    ? '修订中'
    : setStatus === 'reviewed'
      ? '已审核'
      : locked
        ? '审核中'
        : '新审核'
  const submitLabel = setStatus === 'revision' ? '重新提交审核' : '锁定并提交审核'

  // ═════════════════════════════════════════════════════════════════════════════
  // RENDER
  // ═════════════════════════════════════════════════════════════════════════════

  return (
    <div className="report-uploader" style={{ maxWidth: 1180, margin: '0 auto', padding: '32px 0 72px' }}>
      {/* Toast */}
      {toast && <div className="notify-toast" role="status" aria-live="polite">{toast}</div>}

      {/* ═══════════════════════════════════════════════════════════════════════ */}
      {/* TOP BAR: Title + Set ID                                                 */}
      {/* ═══════════════════════════════════════════════════════════════════════ */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 42 }}>
        <h2 style={{ fontSize: '1.35rem', fontWeight: 700, color: '#0f172a', margin: 0, display: 'flex', alignItems: 'center', gap: 12 }}>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <rect x="3" y="3" width="18" height="18" rx="3"/><path d="M9 12l2 2 4-4" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          报告审核
          <span style={{ fontSize: '.7rem', fontWeight: 600, padding: '5px 12px', borderRadius: 18, background: '#eef2ff', color: '#4f46e5' }}>
            {setId ? modeLabel : '新审核'}
          </span>
        </h2>
        {setId && (
          <span style={{ fontSize: '.73rem', color: '#94a3b8', fontFamily: 'monospace', background: '#fff', padding: '5px 12px', borderRadius: 7, border: '1px solid #e2e8f0' }}>
            {setId}
          </span>
        )}
      </div>

      {/* ═══════════════════════════════════════════════════════════════════════ */}
      {/* ERROR BANNER                                                            */}
      {/* ═══════════════════════════════════════════════════════════════════════ */}
      {error && (
        <div className="upload-error" style={{ marginBottom: 30 }}>
          {error}
          <button style={{ marginLeft: 14, background: 'none', border: 'none', color: 'var(--color-error)', cursor: 'pointer', textDecoration: 'underline', fontSize: 14 }} onClick={() => setError(null)}>关闭</button>
        </div>
      )}

      {setStatus === 'revision' && (
        <div style={{
          marginBottom: 30,
          padding: '12px 16px',
          background: '#fffaeb',
          border: '1px solid #fedf89',
          borderRadius: 10,
          color: '#b54708',
          fontSize: '.76rem',
          lineHeight: 1.6,
        }}>
          已从“我的报告”创建修订。请替换修改后的文件；替换完成后只需重新核查被替换的文档，再提交重新审核。
        </div>
      )}

      {/* ═══════════════════════════════════════════════════════════════════════ */}
      {/* PROJECT GROUP — chip selector                                          */}
      {/* ═══════════════════════════════════════════════════════════════════════ */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 38 }}>
        <span style={{ fontSize: '.78rem', fontWeight: 600, color: '#64748b', whiteSpace: 'nowrap' }}>项目组</span>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {quickGroups.map(g => (
            <button
              key={g.id}
              onClick={() => selectProjectGroup(g.id, g.name)}
              style={{
                fontSize: '.7rem', padding: '6px 16px', borderRadius: 22,
                border: `1.5px solid ${selectedGroup === g.name ? '#4f46e5' : '#e2e8f0'}`,
                background: selectedGroup === g.name ? '#eef2ff' : '#fff',
                color: selectedGroup === g.name ? '#4f46e5' : '#64748b',
                fontWeight: selectedGroup === g.name ? 600 : 500,
                cursor: 'pointer', fontFamily: 'inherit', transition: 'all .12s',
              }}
            >{g.name}</button>
          ))}
          <button
            onClick={openGroupPick}
            style={{
              fontSize: '.7rem', padding: '6px 16px', borderRadius: 22,
              border: '1.5px dashed #e2e8f0', background: '#f8fafc', color: '#94a3b8',
              cursor: 'pointer', fontFamily: 'inherit', fontWeight: 500,
            }}
          >+ 选择其他…</button>
        </div>
      </div>

      {/* ═══════════════════════════════════════════════════════════════════════ */}
      {/* 3-STEP INDICATOR                                                       */}
      {/* ═══════════════════════════════════════════════════════════════════════ */}
      <div style={{
        display: 'flex', marginBottom: 42, background: '#fff', borderRadius: 16,
        padding: 6, boxShadow: '0 1px 3px rgba(0,0,0,.04)',
      }}>
        {[
          { num: 1, title: '上传文档', sub: '4 必传 + 可选标准库', done: step1Done, active: !step1Done },
          { num: 2, title: '核查提取结果', sub: '逐份确认 AI 提取数据', done: step2Done, active: step2Active },
          { num: 3, title: '证据图审核', sub: '锁定 → 建图 → 出结论', done: step3Done, active: step3Active },
        ].map((s, i) => {
          const isActive = s.active && !s.done
          const isDone = s.done
          return (
            <div key={s.num} style={{
              flex: 1, display: 'flex', alignItems: 'center', gap: 12,
              padding: '16px 22px', borderRadius: 12,
              background: isActive ? '#eef2ff' : isDone ? '#f0fdf4' : 'transparent',
              transition: 'all .2s',
            }}>
              <div style={{
                width: 40, height: 36, borderRadius: '50%',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontWeight: 700, fontSize: '.85rem', flexShrink: 0,
                background: isActive ? '#4f46e5' : isDone ? '#16a34a' : '#f1f5f9',
                color: (isActive || isDone) ? '#fff' : '#94a3b8',
              }}>
                {isDone ? <IconCheck size={16} /> : s.num}
              </div>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 700, fontSize: '.85rem', color: isActive ? '#4f46e5' : isDone ? '#16a34a' : '#334155' }}>
                  {s.title}
                </div>
                <div style={{ fontSize: '.68rem', color: '#94a3b8', marginTop: 1 }}>{s.sub}</div>
              </div>
            </div>
          )
        })}
      </div>

      {/* ═══════════════════════════════════════════════════════════════════════ */}
      {/* DOCUMENT SECTION                                                        */}
      {/* ═══════════════════════════════════════════════════════════════════════ */}
      <div style={{
        background: '#fff', borderRadius: 16, padding: 28,
        boxShadow: '0 1px 3px rgba(0,0,0,.04)', marginBottom: 30,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 30 }}>
          <h2 style={{ fontSize: '1.05rem', fontWeight: 700, color: '#0f172a', margin: 0 }}>上传文档</h2>
          <span style={{ fontSize: '.75rem', color: '#94a3b8' }}>点击卡片上传，支持 PDF / DOCX / XLS / ZIP</span>
        </div>

        {/* 5 Doc cards */}
        <div className="document-card-grid">
          {DOC_SLOTS.map(meta => {
            const status = getSlotStatus(meta, files, uploading)
            const fs = fileForType(files, meta.docType)
            const isReviewed = reviewedDocs.has(meta.docType)

            // Determine card styling based on status
            let cardBg = '#fafbfc'
            let cardBorder = '2px dashed #e2e8f0'
            let cardCursor: React.CSSProperties['cursor'] = 'pointer'

            if (status === 'done') {
              cardCursor = 'default'
              if (isReviewed) {
                cardBg = '#f0fdf4'; cardBorder = '2px solid #bbf7d0'
              } else {
                cardBg = '#fffbeb'; cardBorder = '2px solid #fde68a'
              }
            } else if (status === 'failed') {
              cardBg = '#fef2f2'; cardBorder = '2px solid #fecaca'; cardCursor = 'pointer'
            } else if (status === 'extracting') {
              cardBg = '#eef2ff'; cardBorder = '2px solid #c7d2fe'; cardCursor = 'default'
            } else if (status === 'uploading') {
              cardBg = '#eef2ff'; cardBorder = '2px solid #c7d2fe'; cardCursor = 'default'
            }

            if (locked) cardCursor = 'default'

            // Status dot color
            let dotColor = '#d4d4d8'
            let dotPulse = false
            if (status === 'uploading' || status === 'extracting') { dotColor = '#f59e0b'; dotPulse = true }
            else if (status === 'done' && !isReviewed) { dotColor = '#f59e0b' }
            else if (status === 'done' && isReviewed) { dotColor = '#16a34a' }
            else if (status === 'failed') { dotColor = '#dc2626' }

            // Status text
            let statusEl: JSX.Element | null = null
            if (status === 'empty') {
              statusEl = <span style={{ color: '#94a3b8', fontSize: '.66rem', fontWeight: 600 }}>点击上传</span>
            } else if (status === 'uploading') {
              statusEl = <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: '#f59e0b', fontSize: '.66rem', fontWeight: 600 }}><IconSpinner /> 上传中...</span>
            } else if (status === 'extracting') {
              statusEl = <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: '#f59e0b', fontSize: '.66rem', fontWeight: 600 }}><IconSpinner /> AI 提取中...</span>
            } else if (status === 'failed') {
              const errMsg = fs?.extraction_error || ''
              statusEl = (
                <span style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: '#dc2626', fontSize: '.66rem', fontWeight: 600 }}>
                    <IconWarn /> 提取失败
                  </span>
                  {errMsg && (
                    <span style={{ color: '#e03131', fontSize: '.56rem', textAlign: 'center', lineHeight: 1.4, maxWidth: 160, wordBreak: 'break-word' }}
                      title={errMsg}>
                      {errMsg.length > 60 ? errMsg.slice(0, 60) + '...' : errMsg}
                    </span>
                  )}
                </span>
              )
            } else if (status === 'done') {
              statusEl = (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: isReviewed ? '#16a34a' : '#f59e0b', fontSize: '.66rem', fontWeight: 600 }}>
                  <IconCheck /> 提取完成{isReviewed ? ' · 已核查' : ' · 待核查'}
                  {isReviewed && (editedCounts[meta.docType] || 0) > 0 && (
                    <span style={{ color: '#7c3aed', fontSize: '.58rem', fontWeight: 600 }}>{editedCounts[meta.docType]}处人工修改</span>
                  )}
                </span>
              )
            }

            // Doc icon color class
            const iconColor = ICON_COLORS[meta.docType]
            const iconBgMap: Record<string, string> = {
              '#4f46e5': '#eef2ff', '#16a34a': '#f0fdf4', '#ea580c': '#fff7ed', '#64748b': '#f1f5f9',
            }

            // Find delete doc_id (latest version)
            const versions = fs?.versions || []
            const latestVer = versions.length > 0 ? versions.reduce((a, b) => a.doc_version > b.doc_version ? a : b) : null
            const deleteId = latestVer?.doc_id || fs?.doc_id
            const canDelete = setId && fs && !locked && status !== 'extracting' && status !== 'uploading'

            return (
              <div
                key={meta.docType}
                onClick={() => {
                  if ((status === 'empty' || status === 'failed') && !locked && !pipelineRunning) handleSlotUpload(meta.docType)
                }}
                onMouseEnter={e => {
                  if (status === 'empty' && !locked && !pipelineRunning) {
                    (e.currentTarget as HTMLElement).style.borderColor = '#4f46e5'
                    ;(e.currentTarget as HTMLElement).style.background = '#f8f7ff'
                  }
                }}
                onMouseLeave={e => {
                  if (status === 'empty' && !locked) {
                    (e.currentTarget as HTMLElement).style.borderColor = '#e2e8f0'
                    ;(e.currentTarget as HTMLElement).style.background = '#fafbfc'
                  }
                }}
                style={{
                  borderRadius: 14, padding: '28px 18px 22px', textAlign: 'center',
                  position: 'relative', transition: 'all .18s',
                  minHeight: 210, display: 'flex', flexDirection: 'column',
                  alignItems: 'center', justifyContent: 'center',
                  background: cardBg, border: cardBorder, cursor: cardCursor,
                  opacity: locked ? .82 : 1,
                }}
              >
                {/* Status dot (top-left) */}
                <div style={{
                  position: 'absolute', top: 10, left: 12,
                  width: 10, height: 10, borderRadius: '50%', background: dotColor,
                  animation: dotPulse ? 'pulse 1.2s ease-in-out infinite' : undefined,
                }} />

                {/* Required badge (top-right) */}
                <span style={{
                  position: 'absolute', top: 8, right: 8,
                  fontSize: '.55rem', padding: '3px 8px', borderRadius: 10,
                  fontWeight: 700,
                  background: meta.required ? '#fef2f2' : '#f1f5f9',
                  color: meta.required ? '#dc2626' : '#64748b',
                }}>
                  {meta.required ? '必传' : '选传'}
                </span>

                {/* Delete X button */}
                {canDelete && (
                  <button
                    onClick={e => { e.stopPropagation(); handleDelete(meta.docType, deleteId) }}
                    disabled={deleting[meta.docType]}
                    title="删除当前文件"
                    style={{
                      position: 'absolute', top: 8, right: 46,
                      width: 22, height: 22, borderRadius: '50%',
                      border: '1px solid #e2e8f0', background: '#fff', color: '#dc2626',
                      cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: '.66rem', fontWeight: 700, lineHeight: 1, padding: 0,
                      opacity: deleting[meta.docType] ? .5 : undefined,
                    }}
                  >{deleting[meta.docType] ? <IconSpinner /> : <Icon name="close" size={14} />}</button>
                )}

                {/* Icon */}
                <div style={{
                  width: 50, height: 50, borderRadius: 14,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  marginBottom: 12, background: iconBgMap[iconColor] || '#f1f5f9', color: iconColor,
                }}>
                  {DOC_ICONS[meta.docType]}
                </div>

                {/* Label */}
                <div style={{ fontWeight: 700, fontSize: '.8rem', marginBottom: 3, color: '#1e293b' }}>
                  {meta.label}
                </div>

                {/* Format / Size */}
                <div style={{ fontSize: '.64rem', color: '#94a3b8' }}>
                  {fs ? `${formatSize(fs.file_size_kb)} · v${fs.latest_version}` : meta.format}
                </div>

                {/* Filename (plain text, no click) */}
                {fs && fs.latest_version > 0 && (
                  <div
                    style={{ fontSize: '.62rem', color: '#64748b', marginTop: 5, wordBreak: 'break-all', lineHeight: 1.4, width: '100%', minWidth: 0 }}
                  >{fs.filename}</div>
                )}

                {/* Status text */}
                <div style={{ marginTop: 10 }}>{statusEl}</div>

                {/* Action buttons */}
                {status === 'done' && !locked && (
                  <div style={{ display: 'flex', gap: 6, marginTop: 12, flexWrap: 'wrap', justifyContent: 'center' }}>
                    <button
                      className="pg-btn"
                      style={{ fontSize: '.62rem', padding: '5px 12px', borderRadius: 8, fontWeight: 600, background: '#4f46e5', color: '#fff', borderColor: '#4f46e5' }}
                      onClick={e => { e.stopPropagation(); setReviewModal(meta.docType) }}
                    >核查</button>
                    <button
                      className="pg-btn"
                      style={{ fontSize: '.62rem', padding: '5px 12px', borderRadius: 8, fontWeight: 600, background: '#fff', color: '#4f46e5', borderColor: '#c7d2fe' }}
                      onClick={e => { e.stopPropagation(); handleSlotUpload(meta.docType, fs?.doc_id) }}
                      title="上传新版本替换当前文件"
                    >替换</button>
                    <button
                      className="pg-btn"
                      style={{ fontSize: '.62rem', padding: '5px 12px', borderRadius: 8, fontWeight: 600, background: '#fff', color: '#64748b', borderColor: '#e2e8f0' }}
                      onClick={e => { e.stopPropagation(); setVersionModal(meta.docType) }}
                    >历史版本</button>
                  </div>
                )}

                {status === 'failed' && !locked && (
                  <div style={{ display: 'flex', gap: 5, marginTop: 12, flexWrap: 'wrap', justifyContent: 'center' }}>
                    <button
                      className="pg-btn"
                      style={{ fontSize: '.62rem', padding: '5px 12px', borderRadius: 8, fontWeight: 600, background: '#f59e0b', color: '#fff', borderColor: '#f59e0b' }}
                      onClick={e => { e.stopPropagation(); handleRetryExtraction(meta.docType, fs?.doc_id || '') }}
                      disabled={uploading[meta.docType]}
                    >{uploading[meta.docType] ? '重试中...' : '重试提取'}</button>
                    <button
                      className="pg-btn"
                      style={{ fontSize: '.62rem', padding: '5px 12px', borderRadius: 8, fontWeight: 600, background: '#dc2626', color: '#fff', borderColor: '#dc2626' }}
                      onClick={e => { e.stopPropagation(); handleSlotUpload(meta.docType, fs?.doc_id) }}
                    >重新上传</button>
                    <button
                      className="pg-btn"
                      style={{ fontSize: '.62rem', padding: '5px 12px', borderRadius: 8, fontWeight: 600, background: '#fff', color: '#64748b', borderColor: '#e2e8f0' }}
                      onClick={e => { e.stopPropagation(); setVersionModal(meta.docType) }}
                    >历史版本</button>
                  </div>
                )}

                {(status === 'uploading' || status === 'extracting') && !locked && (
                  <div style={{ display: 'flex', gap: 5, marginTop: 12, flexWrap: 'wrap', justifyContent: 'center' }}>
                    {status === 'extracting' && (
                      <button
                        className="pg-btn"
                        style={{ fontSize: '.62rem', padding: '5px 12px', borderRadius: 8, fontWeight: 600, background: '#fff', color: '#4f46e5', borderColor: '#c7d2fe' }}
                        onClick={e => { e.stopPropagation(); handleRetryExtraction(meta.docType, fs?.doc_id || '') }}
                        disabled={uploading[meta.docType]}
                        title="如果长时间停留在此状态，点击重新触发提取"
                      >{uploading[meta.docType] ? '重试中...' : '强制重试'}</button>
                    )}
                    <button
                      className="pg-btn"
                      style={{ fontSize: '.62rem', padding: '5px 12px', borderRadius: 8, fontWeight: 600, background: '#fff', color: '#dc2626', borderColor: '#fecaca' }}
                      onClick={e => { e.stopPropagation(); handleCancelUpload(meta.docType) }}
                      title={status === 'uploading' ? '取消上传' : '取消提取'}
                    ><Icon name="close" size={13} />取消</button>
                  </div>
                )}

                {/* Hidden file input */}
                <input
                  ref={el => { fileInputRefs.current[meta.docType] = el }}
                  type="file"
                  accept={meta.docType === 'original_records' ? '.zip' : '.pdf,.docx,.doc,.xls,.xlsx'}
                  style={{ display: 'none' }}
                  onChange={e => handleFileSelected(meta.docType, e)}
                />
              </div>
            )
          })}

          {/* Standards are selected from the reusable knowledge base, never uploaded here. */}
          <div
            onClick={openStandardPicker}
            style={{
              borderRadius: 14, padding: '28px 18px 22px', textAlign: 'center',
              position: 'relative', minHeight: 210, display: 'flex', flexDirection: 'column',
              alignItems: 'center', justifyContent: 'center', transition: 'all .18s',
              background: selectedStandards.length ? '#f0fdf4' : '#fafbfc',
              border: selectedStandards.length ? '2px solid #bbf7d0' : '2px dashed #e2e8f0',
              cursor: locked || pipelineRunning ? 'default' : 'pointer', opacity: locked ? .82 : 1,
            }}
          >
            <div style={{ position: 'absolute', top: 10, left: 12, width: 10, height: 10, borderRadius: '50%', background: selectedStandards.length ? '#16a34a' : '#d4d4d8' }} />
            <span style={{ position: 'absolute', top: 8, right: 8, fontSize: '.55rem', padding: '3px 8px', borderRadius: 10, fontWeight: 700, background: '#f1f5f9', color: '#64748b' }}>可选</span>
            <div style={{ width: 50, height: 50, borderRadius: 14, display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 12, background: '#f1f5f9', color: '#64748b' }}>
              <IconDocStandard />
            </div>
            <div style={{ fontWeight: 700, fontSize: '.8rem', marginBottom: 3, color: '#1e293b' }}>测试标准</div>
            <div style={{ fontSize: '.64rem', color: '#94a3b8' }}>从标准库选择</div>
            {selectedStandards.length > 0 ? (
              <div style={{ width: '100%', marginTop: 8 }}>
                {selectedStandards.slice(0, 2).map((std) => <div key={std.id} title={`${std.code} ${std.title}`} style={{ fontSize: '.61rem', color: '#475569', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginTop: 3 }}>{std.code}{std.version ? ` · ${std.version}` : ''}</div>)}
                {selectedStandards.length > 2 && <div style={{ fontSize: '.6rem', color: '#94a3b8', marginTop: 3 }}>另有 {selectedStandards.length - 2} 份</div>}
                {!locked && <button className="pg-btn" onClick={(e) => { e.stopPropagation(); openStandardPicker() }} style={{ fontSize: '.62rem', padding: '5px 12px', borderRadius: 8, marginTop: 10, color: '#4f46e5', borderColor: '#c7d2fe', background: '#fff' }}>更改选择</button>}
              </div>
            ) : (
              <div style={{ marginTop: 10, color: '#b45309', fontSize: '.61rem', lineHeight: 1.45 }}>未选择时跳过<br />标准条款审核</div>
            )}
          </div>
        </div>

        {/* ── Progress bar + Lock action ─────────────────────────────────── */}
        {!showResults && (
          <>
            <div style={{ marginTop: 24, display: 'flex', alignItems: 'center', gap: 16 }}>
              <div style={{ flex: 1, height: 7, borderRadius: 4, background: '#e2e8f0', overflow: 'hidden' }}>
                <div style={{
                  height: '100%', borderRadius: 4,
                  background: canLock ? '#16a34a' : '#4f46e5',
                  width: `${Math.round((doneCount / requiredSlots.length) * 100)}%`,
                  transition: 'width .4s ease',
                }} />
              </div>
              <span style={{ fontSize: '.75rem', fontWeight: 600, color: '#64748b', whiteSpace: 'nowrap' }}>
                {doneCount}/{requiredSlots.length} 就绪
              </span>
            </div>

            <div style={{
              display: 'flex', alignItems: 'center', gap: 14, marginTop: 24,
              padding: '18px 24px', borderRadius: 14,
              background: canLock ? '#f0fdf4' : '#f8fafc',
              border: `1.5px solid ${canLock ? '#bbf7d0' : '#e2e8f0'}`,
            }}>
              <span style={{ color: canLock ? '#16a34a' : '#94a3b8', display: 'flex', alignItems: 'center' }}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                  <rect x="5" y="11" width="14" height="10" rx="2"/>
                  <path d="M8 11V7a4 4 0 018 0v4" strokeLinecap="round"/>
                </svg>
              </span>
              <span style={{
                fontSize: '.75rem', color: canLock ? '#16a34a' : '#64748b',
                flex: 1, fontWeight: canLock ? 600 : 400,
              }}>
                {locked
                  ? '文档集已锁定，审核已提交'
                  : canLock
                    ? `4/4 上传 + 提取 + 核查全部完成 · ${selectedStandards.length ? `已选择 ${selectedStandards.length} 份标准` : '未选择标准，将跳过标准条款审核'} · ${setStatus === 'revision' ? '可重新提交审核' : '可提交审核'}`
                    : !requiredDone
                      ? `还需上传 ${requiredSlots.filter(s => getSlotStatus(s, files, uploading) !== 'done').map(s => DOC_LABELS[s.docType]).join('、')}（已就绪 ${doneCount}/4）`
                      : !allReviewed
                        ? `还需核查 ${requiredSlots.filter(s => !reviewedDocs.has(s.docType)).map(s => DOC_LABELS[s.docType]).join('、')}`
                        : `需要上传全部 4 个必传文件并完成提取后才能提交（已就绪 ${doneCount}/4）`
                }
              </span>
              <button
                className="btn ok"
                disabled={!canLock || locking}
                onClick={() => setConfirmOpen(true)}
                style={{
                  fontSize: '.8rem', padding: '10px 26px', borderRadius: 12,
                  fontWeight: 700, fontFamily: 'inherit',
                  background: canLock ? '#16a34a' : '#e2e8f0',
                  borderColor: canLock ? '#16a34a' : '#e2e8f0',
                  color: canLock ? '#fff' : '#94a3b8',
                  cursor: canLock ? 'pointer' : 'not-allowed',
                  border: '1.5px solid transparent',
                }}
              >
                {locking ? (pipelineRunning ? '审核中...' : '锁定中...') : locked ? '已锁定' : submitLabel}
              </button>
            </div>
          </>
        )}
      </div>

      {/* ═══════════════════════════════════════════════════════════════════════ */}
      {/* PIPELINE PROGRESS                                                       */}
      {/* ═══════════════════════════════════════════════════════════════════════ */}
      {pipelineRunning && (
        <div style={{
          marginTop: 0, marginBottom: 30, padding: '24px 28px',
          background: '#fff', border: '1.5px solid #e2e8f0', borderRadius: 16,
        }}>
          <div style={{ fontWeight: 700, fontSize: '.9rem', marginBottom: 16, color: '#4f46e5', display: 'flex', alignItems: 'center', gap: 10 }}>
            <Icon name="settings" size={17} />统一证据图审核执行中...
          </div>
          {pipelineSteps.length === 0 && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 14, padding: '7px 0',
              fontSize: '.78rem', color: '#64748b', fontWeight: 600,
            }}>
              <div style={{
                width: 30, height: 28, borderRadius: '50%',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: '.66rem', fontWeight: 700, flexShrink: 0,
                background: '#eef2ff', color: '#4f46e5',
              }}><IconSpinner /></div>
              <span style={{ flex: 1 }}>等待后端开始审核...</span>
            </div>
          )}
          {pipelineSteps.map((step, i) => {
            const stepNumber = i + 1
            const stepStatus = pipelineStepStatuses[stepNumber] || 'wait'
            const isDone = stepStatus === 'done'
            const isActive = stepStatus === 'active'
            const durationMs = pipelineStepDurations[stepNumber]
            return (
              <div key={`pipeline-step-${i}`} style={{
                display: 'flex', alignItems: 'center', gap: 14, padding: '7px 0',
                fontSize: '.78rem', color: isDone ? '#16a34a' : isActive ? '#4f46e5' : '#94a3b8',
                fontWeight: isActive ? 600 : 400,
              }}>
                <div style={{
                  width: 30, height: 28, borderRadius: '50%',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: '.66rem', fontWeight: 700, flexShrink: 0,
                  background: isDone ? '#dcfce7' : isActive ? '#eef2ff' : '#f1f5f9',
                  color: isDone ? '#16a34a' : isActive ? '#4f46e5' : '#94a3b8',
                }}>
                  {isDone ? <IconCheck size={13} /> : isActive ? <IconSpinner /> : i + 1}
                </div>
                <span style={{ flex: 1 }}>{step}</span>
                {isDone && durationMs !== undefined && (
                  <span style={{ fontSize: '.68rem', color: '#94a3b8' }}>
                    {durationMs < 100 ? '<0.1s' : `${(durationMs / 1000).toFixed(1)}s`}
                  </span>
                )}
                {isActive && <span style={{ fontSize: '.68rem', color: '#4f46e5' }}>进行中...</span>}
              </div>
            )
          })}
        </div>
      )}

      {/* ═══════════════════════════════════════════════════════════════════════ */}
      {/* RESULTS SECTION                                                         */}
      {/* ═══════════════════════════════════════════════════════════════════════ */}
      {showResults && !pipelineRunning && (
        <div style={{
          background: '#fff', borderRadius: 16,
          boxShadow: '0 1px 3px rgba(0,0,0,.04)', overflow: 'hidden',
        }}>
          {/* Results header */}
          <div style={{
            padding: '16px 22px', background: '#f8fafc', borderBottom: '1px solid #e2e8f0',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          }}>
            <span style={{ fontWeight: 700, fontSize: '.9rem' }}>审核结果</span>
            <span style={{ fontSize: '.68rem', color: '#94a3b8' }}>
              {setId && <span>{setId} · </span>}
              {pipelineResult?.duration_seconds ? `耗时 ${pipelineResult.duration_seconds.toFixed(1)}s · ` : ''}
              {resultHeaderText}
            </span>
          </div>

          {selectedStandards.length === 0 && (
            <div style={{
              margin: '14px 18px 0', padding: '10px 12px', background: '#fffbeb',
              border: '1px solid #fde68a', borderRadius: 10, color: '#92400e',
              fontSize: '.72rem', lineHeight: 1.55,
            }}>
              本次未选择测试标准，标准条款、适用范围、参数和限值相关审查未执行；四份业务文档之间的一致性审核不受影响。
            </div>
          )}


          {pipelineFailed && (
            <div style={{
              margin: '14px 18px 0',
              padding: '10px 12px',
              background: '#fff1f2',
              border: '1px solid #fecdd3',
              borderRadius: 10,
              color: '#be123c',
              fontSize: '.72rem',
              lineHeight: 1.55,
              fontWeight: 600,
            }}>
              本次审核未完成：{pipelineResult?.errors?.[0] || '请求中断或服务异常'}。
              {pipelineIssues.length > 0 ? ' 下方显示的是上一轮已保存的问题结果。' : ' 当前没有可展示的问题结果，请重新提交审核。'}
            </div>
          )}

          {/* Summary cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, padding: 18 }}>
            <div style={{ textAlign: 'center', padding: '14px 10px', borderRadius: 12, background: '#f8fafc' }}>
              <div style={{ fontSize: '1.6rem', fontWeight: 700, color: '#dc2626' }}>{resultCritical}</div>
              <div style={{ fontSize: '.64rem', color: '#64748b', marginTop: 4, fontWeight: 600 }}>严重 CRITICAL</div>
            </div>
            <div style={{ textAlign: 'center', padding: '14px 10px', borderRadius: 12, background: '#f8fafc' }}>
              <div style={{ fontSize: '1.6rem', fontWeight: 700, color: '#f59e0b' }}>{resultWarning}</div>
              <div style={{ fontSize: '.64rem', color: '#64748b', marginTop: 4, fontWeight: 600 }}>警告 WARNING</div>
            </div>
            <div style={{ textAlign: 'center', padding: '14px 10px', borderRadius: 12, background: '#f8fafc' }}>
              <div style={{ fontSize: '1.6rem', fontWeight: 700, color: '#4f46e5' }}>{resultInfo}</div>
              <div style={{ fontSize: '.64rem', color: '#64748b', marginTop: 4, fontWeight: 600 }}>提示 INFO</div>
            </div>
            <div style={{ textAlign: 'center', padding: '14px 10px', borderRadius: 12, background: '#f8fafc' }}>
              <div style={{ fontSize: '1.6rem', fontWeight: 700, color: pipelineFailed ? '#dc2626' : '#16a34a' }}>
                {passSummary}
              </div>
              <div style={{ fontSize: '.64rem', color: '#64748b', marginTop: 4, fontWeight: 600 }}>
                {pipelineFailed ? '审核状态' : pipelineIssues.length > 0 ? '问题类别' : '检查通过'}
              </div>
            </div>
          </div>

          {/* Annotation progress */}
          {pipelineIssues.length > 0 && (
            <div style={{
              display: 'flex', gap: 10, margin: '0 16px 8px', padding: '8px 12px',
              background: '#f8fafc', borderRadius: 10, fontSize: '.64rem', alignItems: 'center',
            }}>
              <span style={{ fontWeight: 600 }}>标注进度</span>
              <span style={{ color: '#dc2626' }}>{pipelineIssues.filter(i => i.human_status === 'pending').length} 待处理</span>
              <span style={{ color: '#16a34a' }}>{pipelineIssues.filter(i => i.human_status === 'confirmed').length} 已确认</span>
              <span style={{ color: '#94a3b8' }}>{pipelineIssues.filter(i => i.human_status === 'ignored').length} 已忽略</span>
              <span style={{ marginLeft: 'auto', color: '#94a3b8' }}>
                {pipelineIssues.filter(i => i.human_status !== 'pending').length}/{pipelineIssues.length} 已标注
              </span>
            </div>
          )}

          {pipelineMetrics && metricItems.length > 0 && (
            <div style={{
              margin: '0 16px 12px',
              border: '1px solid #e2e8f0',
              borderRadius: 10,
              overflow: 'hidden',
              background: '#fff',
            }}>
              <div style={{
                padding: '9px 12px',
                background: '#f8fafc',
                borderBottom: '1px solid #e2e8f0',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 12,
              }}>
                <span style={{ fontSize: '.72rem', fontWeight: 700 }}>运行观测</span>
                <span style={{ fontSize: '.58rem', color: '#94a3b8' }}>
                  运行批次 {pipelineMetrics.run_id || '最新'}
                </span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 8, padding: 10 }}>
                {[
                  ['指标数', metricItems.length],
                  ['模块审核', moduleMetricCount],
                  ['异常阶段', failedMetricCount],
                  ['实际总耗时', formatMetricDuration(totalMetricDuration)],
                ].map(([label, value]) => (
                  <div key={label} style={{ background: '#f8fafc', borderRadius: 8, padding: '8px 10px' }}>
                    <div style={{ fontSize: '.56rem', color: '#64748b', fontWeight: 600 }}>{label}</div>
                    <div style={{ fontSize: '.9rem', color: label === '异常阶段' && Number(value) > 0 ? '#dc2626' : '#0f172a', fontWeight: 800, marginTop: 3 }}>{value}</div>
                  </div>
                ))}
              </div>
              {slowestMetrics.length > 0 && (
                <div style={{ padding: '0 10px 10px' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.62rem' }}>
                    <thead>
                      <tr style={{ color: '#64748b', background: '#fafbfc' }}>
                        <th style={{ textAlign: 'left', padding: '6px 8px', borderBottom: '1px solid #e2e8f0' }}>阶段</th>
                        <th style={{ textAlign: 'left', padding: '6px 8px', borderBottom: '1px solid #e2e8f0' }}>模块</th>
                        <th style={{ textAlign: 'left', padding: '6px 8px', borderBottom: '1px solid #e2e8f0' }}>状态</th>
                        <th style={{ textAlign: 'right', padding: '6px 8px', borderBottom: '1px solid #e2e8f0' }}>耗时</th>
                        <th style={{ textAlign: 'right', padding: '6px 8px', borderBottom: '1px solid #e2e8f0' }}>问题</th>
                      </tr>
                    </thead>
                    <tbody>
                      {slowestMetrics.map((m, idx) => (
                        <tr key={`${m.metric_type}-${m.stage}-${m.module_key}-${idx}`} style={{ borderBottom: '1px solid #f1f5f9' }}>
                          <td style={{ padding: '6px 8px', color: '#334155', fontWeight: 600 }}>{metricStageLabel(m.stage, m.metric_type)}</td>
                          <td style={{ padding: '6px 8px', color: '#64748b' }}>{metricModuleLabel(m.module_key, m.details?.display_code)}</td>
                          <td style={{ padding: '6px 8px', color: ['failed', 'error'].includes(m.status) ? '#dc2626' : m.status === 'partial' ? '#f59e0b' : '#16a34a', fontWeight: 700 }}>{metricStatusLabel(m.status)}</td>
                          <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'monospace' }}>{formatMetricDuration(Number(m.duration_ms || 0))}</td>
                          <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'monospace' }}>{m.issue_count || 0}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div style={{ padding: '7px 8px 0', color: '#64748b', fontSize: '.58rem', lineHeight: 1.6 }}>
                    各阶段可能并行执行或包含子阶段，阶段耗时不能相加；实际总耗时以整轮审核从开始到完成的时间为准。
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Issue list */}
          <div style={{ padding: '0 16px 12px' }}>
            <div style={{ fontWeight: 700, fontSize: '.78rem', marginBottom: 10 }}>
              问题明细
              <span style={{ fontSize: '.62rem', color: '#94a3b8', fontWeight: 400, marginLeft: 8 }}>
                共 {pipelineIssues.length} 条，四份文件统一对比
              </span>
            </div>
            {pipelineIssues.length === 0 && !pipelineFailed && (
              <div style={{ padding: 20, textAlign: 'center', color: '#16a34a', fontSize: '.78rem' }}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}><IconCheck /> 未发现问题，所有检查项通过</span>
              </div>
            )}
            {pipelineIssues.length === 0 && pipelineFailed && (
              <div style={{ padding: 20, textAlign: 'center', color: '#be123c', fontSize: '.78rem', fontWeight: 600 }}>
                审核未完成，未生成可信结果。请重新提交审核。
              </div>
            )}
            {pipelineIssues.length > 0 && (
              <IssueDocumentMatrix
                issues={pipelineIssues}
                files={files}
                annotationLoading={annotationLoading}
                expandedIssues={expandedIssues}
                selectedSource={selectedSource}
                onExpandedChange={setExpandedIssues}
                onSelectedSourceChange={setSelectedSource}
                onAnnotate={handleAnnotate}
                onOpenDocument={docType => setReviewModal(docType)}
                standards={selectedStandards}
                setId={setId || undefined}
                onMappingSaved={() => showToast('映射已保存，请重新提交审核以应用新规则')}
              />
            )}
          </div>

          {/* Footer actions */}
          <div style={{
            padding: '14px 24px', borderTop: '1px solid #f1f5f9',
            display: 'flex', gap: 10, alignItems: 'center',
          }}>
            <button className="pg-btn" style={{ fontSize: '.75rem' }}>操作记录</button>
            <button
              className="pg-btn"
              onClick={handleExportAuditExcel}
              disabled={exportingExcel || !setId}
              style={{
                fontSize: '.75rem',
                background: '#4f46e5',
                color: '#fff',
                borderColor: '#4f46e5',
                opacity: exportingExcel || !setId ? .65 : 1,
              }}
            >
              {exportingExcel ? '导出中…' : '导出审核报告'}
            </button>
            <div style={{ flex: 1 }} />
            <button className="pg-btn" style={{ fontSize: '.75rem' }} onClick={() => {
              setSetId(null); setSetStatus('new'); setFiles([]); setLocked(false)
              setSelectedGroup(''); setSelectedGroupId('')
              setReviewedDocs(new Set()); setEditedCounts({}); setPipelineResult(null); setPipelineIssues([])
              setPipelineStepStatuses({}); setPipelineStepDurations({}); setExpandedIssues(new Set()); setSelectedSource(null)
            }}>+ 新建文档集</button>
          </div>
        </div>
      )}

      {/* ═══════════════════════════════════════════════════════════════════════ */}
      {/* MODALS                                                                  */}
      {/* ═══════════════════════════════════════════════════════════════════════ */}

      {/* Review Modal */}
      {reviewModal && setId && (() => {
        const currentFs = fileForType(files, reviewModal)
        const reviewDocId = currentFs?.doc_id || ''
        return (
        <DocumentReviewModal
          setId={setId}
          docType={reviewModal}
          docId={reviewDocId}
          label={DOC_LABELS[reviewModal]}
          onClose={() => setReviewModal(null)}
          onConfirm={async (editedValues: Record<string, string>) => {
            if (!reviewDocId) return
            try {
              if (Object.keys(editedValues).length > 0) {
                await saveOverrides(setId, reviewDocId, editedValues)
                setEditedCounts(prev => ({ ...prev, [reviewModal]: Object.keys(editedValues).length }))
              }
              await confirmDocumentReview(setId, reviewDocId)
            } catch (e: any) {
              logger.error('Failed to confirm document review', { component: 'DocumentSetUploader', docType: reviewModal }, e)
              showToast(e?.response?.data?.detail || '核查结果保存失败，请重新确认')
              throw e
            }
            markReviewed(reviewModal)
            const editNote = Object.keys(editedValues).length > 0 ? `（${Object.keys(editedValues).length} 处人工修改）` : ''
            showToast(`${DOC_LABELS[reviewModal]} 核查完成${editNote}`)
          }}
          onSaveOnly={async (editedValues: Record<string, string>) => {
            // Save edits without marking as reviewed — allows 存疑 / 未复核 fields
            if (reviewDocId && Object.keys(editedValues).length > 0) {
              try {
                await saveOverrides(setId, reviewDocId, editedValues)
                setEditedCounts(prev => ({ ...prev, [reviewModal]: Object.keys(editedValues).length }))
              } catch (e: any) {
                logger.error('Failed to save overrides', { component: 'DocumentSetUploader', docType: reviewModal }, e)
                showToast(e?.response?.data?.detail || '人工修改保存失败')
                throw e
              }
            }
            showToast(`${DOC_LABELS[reviewModal]} 编辑已保存（未完成核查）`)
          }}
        />
        )
      })()}

      {/* Version History Modal */}
      {versionModal && setId && (
        <VersionHistoryModal
          setId={setId}
          docType={versionModal}
          label={DOC_LABELS[versionModal]}
          onClose={() => setVersionModal(null)}
        />
      )}

      {/* Group Picker Modal */}
      {groupModalOpen && (
        <div className="modal-overlay" onClick={() => setGroupModalOpen(false)}>
          <div className="modal-container" onClick={e => e.stopPropagation()} style={{ maxWidth: 520 }}>
            <div className="modal-header">
              <span className="modal-title">选择项目组</span>
              <button onClick={() => setGroupModalOpen(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#667085' }} aria-label="关闭"><Icon name="close" /></button>
            </div>
            <div className="modal-body" style={{ padding: 18 }}>
              <input className="filter-input" style={{ width: '100%', marginBottom: 12 }} placeholder="搜索项目组..." value={groupPickSearch} onChange={e => { setGroupPickSearch(e.target.value); setGroupPickCat('全部') }} autoFocus />
              <div style={{ display: 'flex', gap: 12, height: 260 }}>
                <div style={{ width: 100, borderRight: '1px solid #f0f2f5', overflowY: 'auto', flexShrink: 0 }}>
                  {pickCats.map(cat => (
                    <div key={cat} onClick={() => setGroupPickCat(cat)}
                      style={{ padding: '7px 10px', fontSize: 14, cursor: 'pointer', borderRadius: 7, marginBottom: 3, color: groupPickCat === cat ? '#b54708' : '#667085', background: groupPickCat === cat ? '#fff7ed' : 'transparent', fontWeight: groupPickCat === cat ? 500 : 400 }}>
                      {cat} <span style={{ fontSize: 10, color: groupPickCat === cat ? '#b54708' : '#98a2b3' }}>{(pickCatMap.get(cat) || []).length}</span>
                    </div>
                  ))}
                </div>
                <div style={{ flex: 1, overflowY: 'auto' }}>
                  <div onClick={() => { void selectProjectGroup('', ''); setGroupModalOpen(false) }} style={{ padding: '9px 12px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 10, fontSize: 14, borderBottom: '1px solid #f9fafb', borderRadius: 7, background: selectedGroup === '' ? '#fff7ed' : undefined, color: selectedGroup === '' ? '#b54708' : '#667085' }}>
                    <span style={{ width: 6, height: 7, borderRadius: '50%', background: '#b54708', opacity: selectedGroup === '' ? 1 : 0, flexShrink: 0 }} /> 不选择
                  </div>
                  {groupPickFiltered.map((g: any) => {
                    const sel = selectedGroup === g.name
                    return (
                      <div key={g.id} onClick={() => { void selectProjectGroup(g.id, g.name); setGroupModalOpen(false) }} style={{ padding: '9px 12px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 10, fontSize: 14, borderBottom: '1px solid #f9fafb', borderRadius: 7, background: sel ? '#fff7ed' : undefined, color: sel ? '#b54708' : '#344054' }}>
                        <span style={{ width: 6, height: 7, borderRadius: '50%', background: '#b54708', opacity: sel ? 1 : 0, flexShrink: 0 }} />
                        <div><div style={{ fontWeight: sel ? 600 : 500 }}>{g.name}</div>{g.description && <div style={{ fontSize: 10, color: '#98a2b3', marginTop: 1 }}>{g.description}</div>}</div>
                      </div>
                    )
                  })}
                  {groupPickFiltered.length === 0 && <div style={{ padding: 20, textAlign: 'center', color: '#98a2b3', fontSize: 13 }}>无匹配</div>}
                </div>
              </div>
            </div>
            <div style={{ padding: '12px 16px', borderTop: '1px solid #f0f2f5', display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
              <button className="filter-btn" style={{ fontSize: 14 }} onClick={() => setGroupModalOpen(false)}>取消</button>
              <button className="filter-btn" style={{ fontSize: 14, background: '#b54708', color: '#fff', border: 'none' }} onClick={() => setGroupModalOpen(false)}>确定</button>
            </div>
          </div>
        </div>
      )}

      {/* Standard knowledge-base picker */}
      {standardModalOpen && (
        <div className="modal-overlay" onClick={() => !standardLoading && setStandardModalOpen(false)}>
          <div className="modal-container" onClick={e => e.stopPropagation()} style={{ maxWidth: 720 }}>
            <div className="modal-header">
              <div><span className="modal-title">选择测试标准</span><div style={{ color: '#94a3b8', fontSize: '.68rem', marginTop: 3 }}>仅显示已完成人工确认并发布的标准，可多选，也可不选择</div></div>
              <button onClick={() => setStandardModalOpen(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#667085' }} aria-label="关闭"><Icon name="close" /></button>
            </div>
            <div className="modal-body" style={{ padding: 18 }}>
              {standardReferences.length > 0 && <div style={{ border: '1px solid #bfdbfe', background: '#eff6ff', padding: 12, marginBottom: 12 }}>
                <strong style={{ fontSize: '.76rem', color: '#1e3a8a' }}>四份文件中识别到的标准引用</strong>
                <div style={{ color: '#64748b', fontSize: '.66rem', margin: '4px 0 10px' }}>每一项都必须选择已发布标准，或填写本次跳过原因后才能提交审核。</div>
                {standardReferences.map(reference => {
                  const selected = isReferenceSelected(reference)
                  return <div key={reference.normalized_code} style={{ display: 'grid', gridTemplateColumns: '150px 80px 1fr', gap: 8, alignItems: 'center', marginTop: 7 }}>
                    <span style={{ fontSize: '.72rem', fontWeight: 700 }}>{reference.reference_code}</span>
                    <span style={{ fontSize: '.66rem', color: selected ? '#15803d' : '#b45309' }}>{selected ? '已选择对应版本' : '待处理'}</span>
                    {!selected && <input className="filter-input" value={standardSkipReasons[reference.normalized_code] || ''} onChange={event => setStandardSkipReasons(prev => ({ ...prev, [reference.normalized_code]: event.target.value }))} placeholder="不选择时填写原因" />}
                  </div>
                })}
              </div>}
              <input className="filter-input" style={{ width: '100%', marginBottom: 12 }} placeholder="搜索标准编号、名称或版本..." value={standardSearch} onChange={e => setStandardSearch(e.target.value)} autoFocus />
              <div style={{ border: '1px solid #e2e8f0', maxHeight: 360, overflowY: 'auto' }}>
                <label style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '13px 14px', cursor: 'pointer', borderBottom: '1px solid #eef2f7', background: standardDraftIds.size === 0 ? '#fff7ed' : '#fff' }}>
                  <input type="checkbox" checked={standardDraftIds.size === 0} onChange={() => setStandardDraftIds(new Set())} />
                  <div><strong style={{ fontSize: '.78rem', color: '#334155' }}>不选择标准</strong><div style={{ fontSize: '.66rem', color: '#b45309', marginTop: 3 }}>本次不审查标准条款，并在结果中提示审核范围</div></div>
                </label>
                {availableStandards.filter((std) => {
                  const q = standardSearch.trim().toLowerCase()
                  return !q || `${std.code} ${std.title} ${std.version}`.toLowerCase().includes(q)
                }).map((std) => (
                  <label key={std.id} style={{ display: 'flex', gap: 12, alignItems: 'flex-start', padding: '13px 14px', cursor: 'pointer', borderBottom: '1px solid #eef2f7', background: standardDraftIds.has(std.id) ? '#f0fdf4' : '#fff' }}>
                    <input type="checkbox" checked={standardDraftIds.has(std.id)} onChange={() => toggleStandardDraft(std.id)} style={{ marginTop: 2 }} />
                    <div style={{ minWidth: 0, flex: 1 }}><strong style={{ fontSize: '.78rem', color: '#1e293b' }}>{std.code}</strong>{std.version && <span style={{ color: '#64748b', fontSize: '.68rem', marginLeft: 8 }}>{std.version}</span>}<div style={{ color: '#64748b', fontSize: '.69rem', marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{std.title}</div><div style={{ color: '#94a3b8', fontSize: '.62rem', marginTop: 4 }}>{std.page_count} 页 · {std.chunk_count} 个知识块 · {std.organization || '未分类机构'}</div></div>
                  </label>
                ))}
                {availableStandards.length === 0 && <div style={{ padding: 28, textAlign: 'center', color: '#94a3b8', fontSize: '.75rem' }}>标准库暂无已发布版本，请先到“标准库”完成逐条确认并发布</div>}
              </div>
            </div>
            <div style={{ padding: '12px 16px', borderTop: '1px solid #f0f2f5', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ color: '#64748b', fontSize: '.7rem' }}>已选择 {standardDraftIds.size} 份</span>
              <div style={{ display: 'flex', gap: 10 }}><button className="filter-btn" onClick={() => setStandardModalOpen(false)} disabled={standardLoading}>取消</button><button className="filter-btn" style={{ background: '#4f46e5', borderColor: '#4f46e5', color: '#fff' }} onClick={saveStandardSelection} disabled={standardLoading || unresolvedStandardReferences.length > 0}>{standardLoading ? '保存中...' : unresolvedStandardReferences.length ? `还有 ${unresolvedStandardReferences.length} 项未处理` : '确认选择'}</button></div>
            </div>
          </div>
        </div>
      )}

      {/* Confirm Lock Modal */}
      {confirmOpen && (
        <div className="modal-overlay" onClick={() => { if (!locking) setConfirmOpen(false) }}>
          <div className="modal-container" onClick={e => e.stopPropagation()} style={{ maxWidth: 420 }}>
            <div className="modal-header">
              <span className="modal-title">确认提交</span>
              {!locking && (
                <button onClick={() => setConfirmOpen(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#667085' }} aria-label="关闭"><Icon name="close" /></button>
              )}
            </div>
            <div className="modal-body" style={{ padding: 28, textAlign: 'center' }}>
              <div style={{ marginBottom: 12, color: '#e67700' }}>
                <svg width="36" height="36" viewBox="0 0 24 24">
                  <rect x="5" y="11" width="14" height="10" rx="2" stroke="currentColor" strokeWidth="1.5" fill="none"/>
                  <path d="M8 11V7a4 4 0 018 0v4" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round"/>
                </svg>
              </div>
              <div style={{ fontWeight: 700, fontSize: '.9rem', marginBottom: 8 }}>确认提交交叉验证？</div>
              <div style={{ fontSize: '.8rem', color: 'var(--color-gray-400)', marginBottom: 30, lineHeight: 1.7 }}>
                锁定后所有文件的当前版本将被冻结<br/>
                系统将对 {requiredSlots.length} 份文档进行交叉一致性校验<br/>
                {selectedStandards.length > 0 ? `同时使用已选 ${selectedStandards.length} 份标准知识库审查条款` : '未选择标准，本次将跳过标准条款相关审查'}<br/>
                审核完成前不可修改文件
              </div>
              <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
                <button className="pg-btn" onClick={() => setConfirmOpen(false)} disabled={locking}>取消</button>
                <button className="pg-add-btn" style={{ background: '#16a34a' }} disabled={locking} onClick={handleLockAndSubmit}>
                  {locking ? '提交中...' : '确认提交'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Spin + pulse keyframes */}
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
      `}</style>
    </div>
  )
}
