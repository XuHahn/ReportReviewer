import { useState, useEffect, useCallback, useRef } from 'react'
import {
  createDocumentSet, getDocumentSet, addDocumentToSet, deleteDocument, lockDocumentSet,
  triggerReview, getPipelineIssues, annotateIssue, getProjectGroups, saveOverrides,
  retryExtraction, cancelExtraction,
} from '../api'
import type { DocType, FileStatus, DocumentSetOverview, PipelineIssue, PipelineResultSummary } from '../types'
import DocumentReviewModal from './DocumentReviewModal'
import VersionHistoryModal from './VersionHistoryModal'
import TracePanel from './TracePanel'
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
  { order: 4, docType: 'test_standard',    label: '测试标准', format: 'PDF / DOCX',   required: false },
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
  if (fs.extraction_status === 'done') return 'done'
  if (fs.extraction_status === 'failed') return 'failed'
  if (fs.extraction_status === 'extracting' || fs.extraction_status === 'pending') return 'extracting'
  return 'empty'
}

// ── Pipeline progress steps ──────────────────────────────────────────────────

// Fallback steps used when SSE is unavailable
const FALLBACK_PIPELINE_STEPS = [
  '测试项覆盖校验',
  'TOC 三方覆盖',
  '原始记录结论扫描',
  '报告结论一致性',
  '仪器校准有效期',
  '仪器清单交叉比对',
  '基本信息比对',
  '日期跨度检查',
  '电压一致性',
  '测试模式一致性',
  '数据行数对比',
  '方法合规检查',
  'TOC 完整性',
  '多数投票',
  '格式规则校验',
  '物理范围校验',
  '余量验证 + 超标检测',
  '总体结论校验',
  '签发日期逻辑',
  '封面完整性',
  'LLM 语义审核',
  '供应商名称一致性',
  '样品数量一致性',
  '测试计划编号一致性',
]

// ── Component ────────────────────────────────────────────────────────────────

export default function DocumentSetUploader() {
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

  // Pipeline state
  const [pipelineRunning, setPipelineRunning] = useState(false)
  const [pipelineResult, setPipelineResult] = useState<PipelineResultSummary | null>(null)
  const [pipelineIssues, setPipelineIssues] = useState<PipelineIssue[]>([])
  const [pipelineStepIdx, setPipelineStepIdx] = useState(-1)
  const [pipelineSteps, setPipelineSteps] = useState<string[]>(FALLBACK_PIPELINE_STEPS)
  const [annotationLoading, setAnnotationLoading] = useState<Record<string, boolean>>({})
  const [expandedIssues, setExpandedIssues] = useState<Set<string>>(new Set())
  const [selectedSource, setSelectedSource] = useState<{issueId: string, srcIdx: number} | null>(null)

  // Group picker
  const [selectedGroup, setSelectedGroup] = useState('')
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

  // ── Poll extraction status ──────────────────────────────────────────────────

  const pollStatus = useCallback(async () => {
    if (!setId || locked) return
    try {
      const overview: DocumentSetOverview = await getDocumentSet(setId)
      setFiles(overview.files || [])
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

  function selectQuickGroup(name: string) {
    setSelectedGroup(selectedGroup === name ? '' : name)
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
      await lockDocumentSet(setId)
      setLocked(true)
      setSetStatus('locked')

      setPipelineRunning(true)
      setPipelineStepIdx(0)

      // Try SSE for real-time progress; fall back to fake animation
      const token = localStorage.getItem('emc_review_token') || ''
      const sseUrl = `/api/sets/${setId}/review/progress?token=${encodeURIComponent(token)}`
      let eventSource: EventSource | null = null
      let useSse = false

      try {
        eventSource = new EventSource(sseUrl)
        // Set up SSE listener before triggering review
        eventSource.onmessage = (evt) => {
          try {
            const data = JSON.parse(evt.data)
            if (data.label && data.step) {
              setPipelineStepIdx(data.step - 1)
              setPipelineSteps(prev => {
                const next = [...prev]
                if (data.label && data.step <= next.length) {
                  next[data.step - 1] = data.label
                }
                return next
              })
            }
            if (data.status === 'complete' || data.status === 'timeout') {
              eventSource?.close()
            }
          } catch {}
        }
        eventSource.onerror = () => {
          eventSource?.close()
          useSse = false
        }
        useSse = true
      } catch {
        // SSE not supported, fall back to fake progress
        useSse = false
      }

      if (!useSse) {
        // Fallback: fake progress animation
        for (let i = 0; i < FALLBACK_PIPELINE_STEPS.length; i++) {
          await new Promise(r => setTimeout(r, 200 + Math.random() * 300))
          setPipelineStepIdx(i)
        }
      }

      try {
        const result = await triggerReview(setId)
        setPipelineResult(result)
        setSetStatus('reviewed')
        showToast('审核完成！')

        const { issues } = await getPipelineIssues(setId)
        setPipelineIssues(issues)
      } catch (e: any) {
        logger.error('Pipeline trigger failed', { component: 'DocumentSetUploader', setId }, e)
        setPipelineResult({
          set_id: setId, status: 'error', is_clean: false,
          duration_seconds: 0, issues: { critical: 0, warning: 0 },
          errors: [(e as any)?.response?.data?.detail || (e as any).message || '未知错误'],
        })
      } finally {
        eventSource?.close()
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
  const showResults = (locked && !pipelineRunning) || pipelineIssues.length > 0 || pipelineResult !== null

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
          四源校验
          <span style={{ fontSize: '.7rem', fontWeight: 600, padding: '5px 12px', borderRadius: 18, background: '#eef2ff', color: '#4f46e5' }}>
            {setId ? (locked ? '审核中' : '新审核') : '新审核'}
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

      {/* ═══════════════════════════════════════════════════════════════════════ */}
      {/* PROJECT GROUP — chip selector                                          */}
      {/* ═══════════════════════════════════════════════════════════════════════ */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 38 }}>
        <span style={{ fontSize: '.78rem', fontWeight: 600, color: '#64748b', whiteSpace: 'nowrap' }}>项目组</span>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {quickGroups.map(g => (
            <button
              key={g.id}
              onClick={() => selectQuickGroup(g.name)}
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
          { num: 1, title: '上传文档', sub: '4 必传 + 1 选传', done: step1Done, active: !step1Done },
          { num: 2, title: '核查提取结果', sub: '逐份确认 AI 提取数据', done: step2Done, active: step2Active },
          { num: 3, title: '交叉审核', sub: '锁定 → 管线 → 出结果', done: step3Done, active: step3Active },
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
                {isDone ? '✓' : s.num}
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
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 16 }}>
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
                  <IconCheck /> 提取完成{isReviewed ? ' · 已核查 ✓' : ' · 待核查'}
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
                  >{deleting[meta.docType] ? '⋯' : '×'}</button>
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
                    >✕ 取消</button>
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
        </div>

        {/* ── Progress bar + Lock action ─────────────────────────────────── */}
        {!showResults && (
          <>
            <div style={{ marginTop: 24, display: 'flex', alignItems: 'center', gap: 16 }}>
              <div style={{ flex: 1, height: 7, borderRadius: 4, background: '#e2e8f0', overflow: 'hidden' }}>
                <div style={{
                  height: '100%', borderRadius: 4,
                  background: canLock ? '#16a34a' : '#4f46e5',
                  width: `${Math.round((doneCount / DOC_SLOTS.length) * 100)}%`,
                  transition: 'width .4s ease',
                }} />
              </div>
              <span style={{ fontSize: '.75rem', fontWeight: 600, color: '#64748b', whiteSpace: 'nowrap' }}>
                {doneCount}/{DOC_SLOTS.length} 就绪
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
                    ? '4/4 上传 + 提取 + 核查全部完成 · 可提交审核'
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
                {locking ? '锁定中...' : locked ? '已锁定' : '锁定并提交审核'}
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
            ⚙️ 交叉验证管线执行中...
          </div>
          {pipelineSteps.map((step, i) => {
            const isDone = i < pipelineStepIdx
            const isActive = i === pipelineStepIdx
            return (
              <div key={step} style={{
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
                  {isDone ? '✓' : isActive ? '⟳' : i + 1}
                </div>
                <span style={{ flex: 1 }}>{step}</span>
                {isDone && <span style={{ fontSize: '.68rem', color: '#94a3b8' }}>{(0.1 + i * 0.2).toFixed(1)}s</span>}
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
              {pipelineIssues.length > 0 ? `${pipelineIssues.length} 个问题` : '无问题'}
            </span>
          </div>

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
              <div style={{ fontSize: '1.6rem', fontWeight: 700, color: '#16a34a' }}>
                {pipelineResult?.is_clean ? '✓' : `${pipelineSteps.length - (pipelineResult?.errors?.length || 0)}/${pipelineSteps.length}`}
              </div>
              <div style={{ fontSize: '.64rem', color: '#64748b', marginTop: 4, fontWeight: 600 }}>检查通过</div>
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

          {/* Issue list */}
          <div style={{ padding: '0 16px 12px' }}>
            <div style={{ fontWeight: 700, fontSize: '.78rem', marginBottom: 10 }}>
              发现的问题
              <span style={{ fontSize: '.62rem', color: '#94a3b8', fontWeight: 400, marginLeft: 8 }}>点击标签直接标注</span>
            </div>
            {pipelineIssues.length === 0 && (
              <div style={{ padding: 20, textAlign: 'center', color: '#16a34a', fontSize: '.78rem' }}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}><IconCheck /> 未发现问题，所有检查项通过</span>
              </div>
            )}
            {pipelineIssues.map(issue => {
              const sevColor = issue.severity === 'CRITICAL' ? '#dc2626' : issue.severity === 'WARNING' ? '#f59e0b' : '#4f46e5'
              const isIgnored = issue.human_status === 'ignored'
              const isConfirmed = issue.human_status === 'confirmed'
              const statusColor = isConfirmed ? '#16a34a' : isIgnored ? '#94a3b8' : sevColor
              const isExpanded = expandedIssues.has(issue.id)
              const hasSources = issue.sources && Object.keys(issue.sources).length > 1

              return (
                <div key={issue.id} style={{
                  padding: '10px 14px', borderRadius: 10, marginBottom: 8,
                  borderLeft: `3px solid ${isIgnored ? '#adb5bd' : sevColor}`,
                  background: isIgnored ? 'rgba(108,117,125,.03)' : issue.severity === 'CRITICAL' ? '#fef2f2' : issue.severity === 'WARNING' ? '#fffbeb' : '#f8f7ff',
                  opacity: isIgnored ? .4 : 1,
                  fontSize: '.73rem', display: 'flex', gap: 14, alignItems: 'flex-start',
                }}>
                  <span style={{
                    fontSize: '.58rem', padding: '3px 8px', borderRadius: 6,
                    background: sevColor, color: '#fff', fontWeight: 700, flexShrink: 0,
                  }}>
                    {issue.severity === 'CRITICAL' ? '严重' : issue.severity === 'WARNING' ? '警告' : '提示'}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 700, marginBottom: 4 }}>
                      {issue.field_name}
                      {hasSources && (
                        <span onClick={() => {
                          setExpandedIssues(prev => {
                            const next = new Set(prev)
                            if (next.has(issue.id)) { next.delete(issue.id); setSelectedSource(null) }
                            else next.add(issue.id)
                            return next
                          })
                        }} style={{ fontSize: '.6rem', color: '#4f46e5', cursor: 'pointer', marginLeft: 10, userSelect: 'none' }}>
                          {isExpanded ? '▲ 收起对比' : '▼ 展开对比'}
                        </span>
                      )}
                    </div>
                    <div style={{ color: '#64748b', fontSize: '.64rem', lineHeight: 1.5 }}>{issue.description}</div>
                    {issue.sources && Object.keys(issue.sources).length > 0 && !isExpanded && (
                      <div style={{ fontSize: '.6rem', color: '#94a3b8', marginTop: 4 }}>
                        来源: {Object.entries(issue.sources).map(([k, v]) => `${k}(${v})`).join(' · ')}
                      </div>
                    )}
                    {/* Expandable comparison */}
                    {isExpanded && hasSources && (
                      <div style={{ marginTop: 10, border: '1px solid #e2e8f0', borderRadius: 7, overflow: 'hidden', background: '#fff' }}>
                        <div style={{ padding: '5px 8px', background: '#f8fafc', fontSize: '.6rem', fontWeight: 600, color: '#64748b', borderBottom: '1px solid #e2e8f0', display: 'flex', alignItems: 'center', gap: 5 }}>
                          四源数据对比
                          <span style={{ fontSize: '.53rem', color: '#94a3b8', fontWeight: 400 }}>— 点击行查看提取溯源</span>
                        </div>
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.62rem' }}>
                          <thead>
                            <tr style={{ background: '#fafbfc' }}>
                              <th style={{ textAlign: 'left', padding: '5px 8px', borderBottom: '1px solid #e9ecef', fontSize: '.6rem', color: '#64748b' }}>数据源</th>
                              <th style={{ textAlign: 'left', padding: '5px 8px', borderBottom: '1px solid #e9ecef', fontSize: '.6rem', color: '#64748b' }}>值</th>
                            </tr>
                          </thead>
                          <tbody>
                            {Object.entries(issue.sources).map(([source, value], idx) => {
                              const allValues = Object.values(issue.sources)
                              const uniqueValues = new Set(allValues)
                              const isMajority = allValues.filter(v => v === value).length >= allValues.length / 2
                              const isDisputed = uniqueValues.size > 1
                              const isMissing = typeof value === 'string' && (value.startsWith('未') || value.startsWith('缺失'))
                              const cellBg = isDisputed ? (isMajority ? 'rgba(22,163,74,.04)' : 'rgba(220,38,38,.04)') : 'transparent'
                              const hasTrace = issue.trace && issue.trace[idx]
                              const isSelected = selectedSource?.issueId === issue.id && selectedSource?.srcIdx === idx
                              return (
                                <tr key={source}
                                  onClick={() => {
                                    if (!hasTrace) return
                                    setExpandedIssues(prev => { const next = new Set(prev); next.add(issue.id); return next })
                                    setSelectedSource(prev => (prev?.issueId === issue.id && prev?.srcIdx === idx) ? null : { issueId: issue.id, srcIdx: idx })
                                  }}
                                  title={hasTrace ? '点击查看溯源详情' : undefined}
                                  style={{
                                    borderBottom: '1px solid #f1f5f9', background: isSelected ? '#f0f6ff' : cellBg,
                                    cursor: hasTrace ? 'pointer' : 'default',
                                    transition: 'background .12s',
                                  }}>
                                  <td style={{
                                    padding: '5px 8px', fontWeight: 600, fontSize: '.62rem',
                                    color: isSelected ? '#175cd3' : undefined,
                                  }}>{source}</td>
                                  <td style={{
                                    padding: '5px 8px', fontFamily: 'monospace', fontSize: '.62rem',
                                    color: isMissing ? '#94a3b8' : (isSelected ? '#175cd3' : (isDisputed ? (isMajority ? '#16a34a' : '#dc2626') : '#344054')),
                                    fontWeight: isDisputed && !isMajority ? 600 : 400,
                                    fontStyle: isMissing ? 'italic' : undefined,
                                  }}>
                                    {value}
                                    {isDisputed && !isMajority && !isMissing && <span style={{ fontSize: '.53rem', color: '#dc2626', marginLeft: 5 }}>← 不一致</span>}
                                    {hasTrace && <span style={{ opacity: isSelected ? 1 : .4, marginLeft: 4, transition: 'opacity .12s' }}>
                                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ verticalAlign: -1 }}><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                                    </span>}
                                  </td>
                                </tr>
                              )
                            })}
                          </tbody>
                        </table>
                        {issue.source_step && (
                          <div style={{ padding: '5px 10px', fontSize: '.55rem', color: '#94a3b8', borderTop: '1px solid #f1f5f9' }}>
                            检测步骤: {issue.source_step} · 验证类型: {issue.validation_type}
                          </div>
                        )}

                        {/* Trace panel */}
                        {selectedSource?.issueId === issue.id && issue.trace?.[selectedSource.srcIdx] && (() => {
                          const tr = issue.trace![selectedSource.srcIdx]
                          const srcValues = Object.values(issue.sources)
                          return (
                            <TracePanel
                              trace={tr}
                              sourceValue={typeof srcValues[selectedSource.srcIdx] === 'string' ? srcValues[selectedSource.srcIdx] as string : String(srcValues[selectedSource.srcIdx])}
                              onClose={() => setSelectedSource(null)}
                              onViewFullDoc={() => {
                                const docTypeMap: Record<string, string> = {
                                  order_form: 'order_form', test_plan: 'test_plan',
                                  original_records: 'original_records', final_report: 'final_report',
                                }
                                const dt = docTypeMap[tr.doc_type] || tr.doc_type
                                setReviewModal(dt as DocType)
                              }}
                            />
                          )
                        })()}
                      </div>
                    )}
                    {issue.human_status !== 'pending' && issue.human_comment && (
                      <div style={{
                        marginTop: 5, padding: '4px 8px',
                        background: isConfirmed ? 'rgba(22,163,74,.05)' : 'rgba(108,117,125,.05)',
                        borderRadius: 4, fontSize: '.6rem',
                        display: 'flex', alignItems: 'center', gap: 8,
                      }}>
                        <span style={{ color: statusColor, fontWeight: 600 }}>
                          {isConfirmed ? '已确认' : '已忽略'}
                          {issue.annotated_by && ` · ${issue.annotated_by}`}
                          {issue.annotated_at && ` · ${issue.annotated_at.slice(0, 16)}`}
                        </span>
                        <span style={{ color: '#64748b' }}>{issue.human_comment}</span>
                      </div>
                    )}
                  </div>
                  <select
                    value={issue.human_status}
                    onChange={e => handleAnnotate(issue.id, e.target.value)}
                    disabled={annotationLoading[issue.id]}
                    style={{
                      fontSize: '.6rem', padding: '3px 6px', borderRadius: 6,
                      border: `1px solid ${statusColor}`,
                      fontFamily: 'inherit', flexShrink: 0,
                      background: isConfirmed ? 'rgba(22,163,74,.04)' : isIgnored ? 'rgba(108,117,125,.04)' : undefined,
                      opacity: isIgnored ? .5 : 1,
                    }}
                  >
                    <option value="pending">待处理</option>
                    <option value="confirmed">已确认</option>
                    <option value="ignored">误报/忽略</option>
                  </select>
                </div>
              )
            })}
          </div>

          {/* Footer actions */}
          <div style={{
            padding: '14px 24px', borderTop: '1px solid #f1f5f9',
            display: 'flex', gap: 10, alignItems: 'center',
          }}>
            <button className="pg-btn" style={{ fontSize: '.75rem' }}>操作记录</button>
            <button className="pg-btn" style={{ fontSize: '.75rem', background: '#4f46e5', color: '#fff', borderColor: '#4f46e5' }}>导出审核报告</button>
            <div style={{ flex: 1 }} />
            <button className="pg-btn" style={{ fontSize: '.75rem' }} onClick={() => {
              setSetId(null); setSetStatus('new'); setFiles([]); setLocked(false)
              setReviewedDocs(new Set()); setEditedCounts({}); setPipelineResult(null); setPipelineIssues([])
              setPipelineStepIdx(-1); setExpandedIssues(new Set()); setSelectedSource(null)
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
            // Save human overrides to backend
            if (reviewDocId && Object.keys(editedValues).length > 0) {
              try {
                await saveOverrides(setId, reviewDocId, editedValues)
                setEditedCounts(prev => ({ ...prev, [reviewModal]: Object.keys(editedValues).length }))
              } catch (e: any) {
                logger.error('Failed to save overrides', { component: 'DocumentSetUploader', docType: reviewModal }, e)
              }
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
              <button onClick={() => setGroupModalOpen(false)} style={{ background: 'none', border: 'none', fontSize: 20, cursor: 'pointer', color: '#667085' }} aria-label="关闭">✕</button>
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
                  <div onClick={() => { setSelectedGroup(''); setGroupModalOpen(false) }} style={{ padding: '9px 12px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 10, fontSize: 14, borderBottom: '1px solid #f9fafb', borderRadius: 7, background: selectedGroup === '' ? '#fff7ed' : undefined, color: selectedGroup === '' ? '#b54708' : '#667085' }}>
                    <span style={{ width: 6, height: 7, borderRadius: '50%', background: '#b54708', opacity: selectedGroup === '' ? 1 : 0, flexShrink: 0 }} /> 不选择
                  </div>
                  {groupPickFiltered.map((g: any) => {
                    const sel = selectedGroup === g.name
                    return (
                      <div key={g.id} onClick={() => { setSelectedGroup(g.name); setGroupModalOpen(false) }} style={{ padding: '9px 12px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 10, fontSize: 14, borderBottom: '1px solid #f9fafb', borderRadius: 7, background: sel ? '#fff7ed' : undefined, color: sel ? '#b54708' : '#344054' }}>
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

      {/* Confirm Lock Modal */}
      {confirmOpen && (
        <div className="modal-overlay" onClick={() => { if (!locking) setConfirmOpen(false) }}>
          <div className="modal-container" onClick={e => e.stopPropagation()} style={{ maxWidth: 420 }}>
            <div className="modal-header">
              <span className="modal-title">确认提交</span>
              {!locking && (
                <button onClick={() => setConfirmOpen(false)} style={{ background: 'none', border: 'none', fontSize: 20, cursor: 'pointer', color: '#667085' }} aria-label="关闭">✕</button>
              )}
            </div>
            <div className="modal-body" style={{ padding: 28, textAlign: 'center' }}>
              <div style={{ marginBottom: 12, color: '#e67700' }}>
                <svg width="36" height="36" viewBox="0 0 24 24">
                  <rect x="5" y="11" width="14" height="10" rx="2" stroke="currentColor" strokeWidth="1.5" fill="none"/>
                  <path d="M8 11V7a4 4 0 018 0v4" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round"/>
                </svg>
              </div>
              <div style={{ fontWeight: 700, fontSize: '.9rem', marginBottom: 8 }}>确认提交四源交叉验证？</div>
              <div style={{ fontSize: '.8rem', color: 'var(--color-gray-400)', marginBottom: 30, lineHeight: 1.7 }}>
                锁定后所有文件的当前版本将被冻结<br/>
                系统将对 {requiredSlots.length} 份文档进行交叉一致性校验<br/>
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
