import { memo, useCallback, useEffect, useRef, useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { requestNotificationPermission, notify } from '../notify'
import { uploadReportStream, uploadBatchStream, getHistory, getProjectGroups } from '../api'
import type { UploadResponse, StreamingState, ReviewItem, BatchStreamingState, BatchFileResult } from '../types'
import { SEV_LABEL } from '../constants'
import { logger } from '../logger'

interface Props {
  onResult: (data: UploadResponse) => void
  onBatchResult: (results: BatchFileResult[]) => void
}

const INITIAL_STREAM: StreamingState = {
  phase: 'idle',
  phaseMessage: '',
  checklistSummary: null,
  reviewItems: [],
  finalResult: null,
  error: null,
  elapsed: 0,
}

const INITIAL_BATCH: BatchStreamingState = {
  phase: 'parsing',
  total: 0,
  completed: 0,
  progress: [],
  results: [],
  error: null,
  elapsed: 0,
}

export default function ReportUploader({ onResult, onBatchResult }: Props) {
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [elapsed, setElapsed] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [streamState, setStreamState] = useState<StreamingState>(INITIAL_STREAM)
  const [batchState, setBatchState] = useState<BatchStreamingState>(INITIAL_BATCH)
  const [tags, setTags] = useState(localStorage.getItem('last_project_group') || '')
  const tagsLabel = tags || '不选择'
  const [projectGroups, setProjectGroups] = useState<any[]>([])
  const [compareWith, setCompareWith] = useState('')
  const [compareLabel, setCompareLabel] = useState('')
  const [recentGroups, setRecentGroups] = useState<Array<{id:string, filename:string, group_id:string}>>([])
  const [toast, setToast] = useState<string | null>(null)
  const [autoMatchToast, setAutoMatchToast] = useState<string | null>(null)
  const [batchMode, setBatchMode] = useState(false)
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  const [showGroupModal, setShowGroupModal] = useState(false)
  const [showCompareModal, setShowCompareModal] = useState(false)
  const [gsearch, setGsearch] = useState('')
  const [csearch, setCsearch] = useState('')
  const [groupTab, setGroupTab] = useState('all')
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    getProjectGroups().then(setProjectGroups).catch((e: any) => { logger.error('Failed to load project groups', { component: 'ReportUploader' }, e) })
    getHistory({ limit: 50 }).then(res => {
      const seen = new Set<string>()
      const groups: typeof recentGroups = []
      for (const r of res.reports) {
        const key = r.group_id || r.id
        if (!seen.has(key)) { seen.add(key); groups.push({ id: r.id, filename: r.filename, group_id: r.group_id || '' }) }
      }
      setRecentGroups(groups)
    }).catch((e: any) => { logger.error('Failed to load recent groups', { component: 'ReportUploader' }, e) })
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
      abortRef.current?.abort()
    }
  }, [])

  const startTimer = () => {
    const startTime = Date.now()
    timerRef.current = setInterval(() => {
      setElapsed(Math.round((Date.now() - startTime) / 1000))
    }, 1000)
  }

  const stopTimer = () => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
  }

  // ── On drop: just store files, don't upload yet ──────────────────

  const onDrop = useCallback((acceptedFiles: File[]) => {
    if (acceptedFiles.length === 0) return
    setError(null)
    setStreamState(INITIAL_STREAM)
    setBatchState(INITIAL_BATCH)
    setPendingFiles(acceptedFiles)
    // Auto-detect compare target by filename similarity
    if (acceptedFiles.length === 1 && recentGroups.length > 0) {
      const fname = acceptedFiles[0].name.replace(/\.[^.]+$/, '').replace(/[_\-\s]*v\d+$/i, '').toLowerCase()
      let best: { id: string; label: string; score: number } | null = null
      for (const r of recentGroups) {
        const rname = r.filename.replace(/\.[^.]+$/, '').toLowerCase()
        if (rname === fname) { best = { id: r.id, label: r.filename, score: 100 }; break }
        if (rname.includes(fname) || fname.includes(rname)) {
          const s = Math.min(rname.length, fname.length) / Math.max(rname.length, fname.length)
          if (s > 0.6 && (!best || s > best.score)) best = { id: r.id, label: r.filename, score: s }
        }
      }
      if (best) {
        setCompareWith(best.id); setCompareLabel(best.label)
        setAutoMatchToast(`已自动匹配对比目标：${best.label}`)
        setTimeout(() => setAutoMatchToast(null), 4000)
      }
    }
  }, [recentGroups])

  // ── Start upload after user clicks button ────────────────────────

  const handleStart = useCallback(async () => {
    if (pendingFiles.length === 0) return
    const acceptedFiles = pendingFiles
    setPendingFiles([])
    setError(null)
    setUploading(true)
    setProgress(0)
    setElapsed(0)
    setBatchMode(acceptedFiles.length > 1)

    if (acceptedFiles.length === 1) {
      // Delegate to single-file streaming
      const file = acceptedFiles[0]
      const controller = new AbortController()
      abortRef.current = controller
      startTimer()

      try {
        await uploadReportStream(file, tags || undefined, compareWith || undefined, (evt) => {
          setStreamState((prev) => {
            switch (evt.type) {
              case 'progress':
                return { ...prev, phase: evt.data.phase, phaseMessage: evt.data.message }
              case 'checklist_summary':
                return { ...prev, checklistSummary: evt.data }
              case 'review_item':
                return { ...prev, reviewItems: [...prev.reviewItems, evt.data] }
              case 'result': {
                const result: UploadResponse = {
                  ...evt.data,
                  review_items: evt.data.review_items || prev.reviewItems,
                }
                setTimeout(() => onResult(result), 0)
                return { ...prev, finalResult: evt.data }
              }
              case 'error':
                return { ...prev, error: evt.data.message }
              default:
                return prev
            }
          })
        }, controller.signal)
      } catch (err: any) {
        if (err?.name === 'AbortError') {
          setError('审核已取消')
        } else {
          setError(err?.response?.data?.detail || err?.message || '上传失败，请重试')
          setUploading(false)
        }
      } finally {
        stopTimer()
        setUploading(false)
        abortRef.current = null
      }
      return
    }

    // Multi-file batch mode
    const controller = new AbortController()
    abortRef.current = controller
    startTimer()
    requestNotificationPermission()

    try {
      await uploadBatchStream(acceptedFiles, tags || undefined, compareWith || undefined, (evt) => {
        setBatchState((prev) => {
          switch (evt.type) {
            case 'batch_start':
              return {
                ...prev,
                phase: 'processing',
                total: evt.data.total,
              }
            case 'batch_progress':
              return {
                ...prev,
                completed: prev.completed + 1,
                progress: [...prev.progress, evt.data],
              }
            case 'batch_done': {
              const done = evt.data
              const passCount = done.results.filter((r: any) => r.overall_result === 'pass').length
              const failCount = done.results.filter((r: any) => r.overall_result === 'fail').length
              const msg = `审核完成：${done.results.length} 份报告（${passCount} 通过${failCount > 0 ? `，${failCount} 不通过` : ''}）`
              setToast(msg)
              setTimeout(() => setToast(null), 8000)
              notify('EMC 报告审核', msg)
              setTimeout(() => onBatchResult(done.results), 0)
              return {
                ...prev,
                phase: 'done',
                results: evt.data.results,
              }
            }
            case 'error':
              return { ...prev, error: evt.data.message }
            default:
              return prev
          }
        })
      }, controller.signal)
    } catch (err: any) {
      if (err?.name === 'AbortError') {
        setError('批量审核已取消')
      } else {
        setError(err?.response?.data?.detail || err?.message || '批量上传失败，请重试')
      }
    } finally {
      stopTimer()
      setUploading(false)
      abortRef.current = null
    }
  }, [onResult, onBatchResult, tags, compareWith, pendingFiles])

  const handleCancel = () => {
    abortRef.current?.abort()
  }

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'application/pdf': ['.pdf'],
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
    },
    maxFiles: 10,
  })

  // ── streaming phase helpers ─────────────────────────────────────

  const phaseLabel = (phase: string): string => {
    switch (phase) {
      case 'stage1_scanning': return '正在快速扫描报告...'
      case 'stage1_done': return '扫描完成'
      case 'stage2_reviewing': return '正在深度审核...'
      case 'highlighting': return '正在标注报告原文...'
      case 'saving': return '正在保存结果...'
      default: return '处理中...'
    }
  }

  // ── render ──────────────────────────────────────────────────────

  if (uploading && batchMode) {
    return (
      <div className="report-uploader">
        <div className="dropzone" style={{ cursor: 'default' }}>
          <div className="streaming-panel">
            <div className="stream-phase-row">
              <span className="stream-phase-badge">
                {batchState.phase === 'parsing' ? '正在解析文件...' :
                 batchState.phase === 'processing' ? `正在审核 (${batchState.completed}/${batchState.total})` :
                 '批量审核完成'}
              </span>
              <span className="stream-elapsed">{elapsed}s</span>
            </div>

            <div className="stream-progress-bar">
              <div
                className="stream-progress-fill stream-progress-stage1_done"
                style={{
                  width: batchState.total > 0
                    ? `${Math.round((batchState.completed / batchState.total) * 100)}%`
                    : '0%',
                  animation: batchState.phase === 'processing' ? 'progressPulse 2s ease-in-out infinite' : 'none',
                }}
              />
            </div>

            {batchState.progress.map((p) => (
              <div key={p.file_index} className={`batch-file-card batch-file-${p.phase}`}>
                <div className="batch-file-card-header">
                  <span className="batch-file-name">{p.filename}</span>
                  {p.phase === 'done' && (
                    <span className={`result-sm result-badge result-${p.overall_result}`}>
                      {p.overall_result === 'pass' ? '通过' :
                       p.overall_result === 'fail' ? '不通过' :
                       p.overall_result === 'error' ? '错误' : '警告'}
                    </span>
                  )}
                  {p.phase === 'error' && (
                    <span className="result-sm result-badge result-fail">解析失败</span>
                  )}
                </div>
                <div className="batch-file-card-meta">
                  {p.phase === 'done' && (
                    <span>{p.issues} 处问题</span>
                  )}
                  {p.error && <span className="batch-file-error">{p.error}</span>}
                </div>
              </div>
            ))}

            {batchState.phase === 'parsing' && (
              <div className="stream-finalizing">
                <div className="spinner" />
                <span>正在解析文件...</span>
              </div>
            )}

            {batchState.phase === 'processing' && batchState.completed < batchState.total && (
              <div className="stream-live-count">
                已完成 {batchState.completed}/{batchState.total} 个文件
              </div>
            )}

            {batchState.phase === 'done' && (
              <div className="checklist-summary-card">
                <div className="checklist-summary-title">
                  批量审核完成：{batchState.total} 个文件，共 {
                    batchState.results.reduce((s, r) => s + (r.review_items?.length || 0), 0)
                  } 处问题
                </div>
              </div>
            )}

            {batchState.phase !== 'done' && (
              <button className="stream-cancel-btn" onClick={handleCancel}>
                取消审核
              </button>
            )}
          </div>
        </div>
        {batchState.error && (
          <div className="upload-error" role="alert">{batchState.error}</div>
        )}
        {error && <div className="upload-error" role="alert">{error}</div>}
      </div>
    )
  }

  if (uploading) {
    return (
      <div className="report-uploader">
        <div className="dropzone" style={{ cursor: 'default' }}>
          <div className="streaming-panel">
            {/* phase indicator */}
            <div className="stream-phase-row">
              {streamState.phase !== 'idle' && (
                <span className="stream-phase-badge">{phaseLabel(streamState.phase)}</span>
              )}
              <span className="stream-elapsed">{elapsed}s</span>
            </div>

            {/* animated progress bar */}
            <div className="stream-progress-bar">
              <div className={`stream-progress-fill stream-progress-${streamState.phase}`} />
            </div>

            {/* stage 1 done: checklist summary */}
            {streamState.checklistSummary && (
              <div className="checklist-summary-card">
                <div className="checklist-summary-title">
                  扫描发现 {streamState.checklistSummary.total_flags} 处疑似问题
                </div>
                <div className="checklist-summary-sevs">
                  {Object.entries(streamState.checklistSummary.severities).map(([sev, count]) => (
                    <span key={sev} className={`sev-tag sev-${sev}`}>
                      {SEV_LABEL[sev] || sev} {count}
                    </span>
                  ))}
                </div>
                {streamState.checklistSummary.sample_flags.length > 0 && (
                  <ul className="checklist-flags">
                    {streamState.checklistSummary.sample_flags.map((f, i) => (
                      <li key={i}>{f}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            {/* stage 2: live error count */}
            {streamState.phase === 'stage2_reviewing' && streamState.reviewItems.length > 0 && (
              <div className="stream-live-count">
                已发现 {streamState.reviewItems.length} 处问题
              </div>
            )}
            <div className="stream-errors-list">
              {streamState.reviewItems.map((item, idx) => (
                <StreamErrorCard key={idx} idx={idx} item={item} />
              ))}
            </div>

            {/* finalizing */}
            {(streamState.phase === 'highlighting' || streamState.phase === 'saving') && (
              <div className="stream-finalizing">
                <div className="spinner" />
                <span>{phaseLabel(streamState.phase)}</span>
              </div>
            )}

            {/* cancel */}
            <button className="stream-cancel-btn" onClick={handleCancel}>
              取消审核
            </button>
          </div>
        </div>
        {streamState.error && (
          <div className="upload-error" role="alert">{streamState.error}
            <button className="filter-btn" style={{marginLeft:12}} onClick={() => { setError(null); setStreamState(INITIAL_STREAM); setUploading(false) }}>重试</button>
          </div>
        )}
        {error && <div className="upload-error" role="alert">{error}
          <button className="filter-btn" style={{marginLeft:12}} onClick={() => setError(null)}>重试</button>
        </div>}
      </div>
    )
  }

  return (
    <div className="report-uploader">
      {toast && <div className="notify-toast" role="status" aria-live="polite">{toast}</div>}
      <div
        {...getRootProps()}
        className={`dropzone ${isDragActive ? 'dropzone-active' : ''}`}
      >
        <input {...getInputProps()} />
        {isDragActive ? (
          <p className="upload-text">释放以选择</p>
        ) : pendingFiles.length > 0 ? (
          <div className="upload-prompt">
            <p className="upload-text" style={{fontSize:14,color:'#475467',marginBottom:8}}>
              已选择 {pendingFiles.length} 个文件
            </p>
            {pendingFiles.map((f, i) => <p key={i} style={{fontSize:13,color:'#667085',margin:0}}>{f.name}</p>)}
          </div>
        ) : (
          <div className="upload-prompt">
            <svg className="upload-icon" viewBox="0 0 24 24" fill="none" stroke="#0f3460" strokeWidth="1.8">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
            <p className="upload-text">点击或拖拽上传测试报告</p>
            <p className="upload-hint">支持 PDF / Word (.docx) · 单次最多10个文件</p>
          </div>
        )}
      </div>
      {autoMatchToast && (
        <div className="auto-match-toast">
          <span>🔗</span><span>{autoMatchToast}</span>
          <span className="auto-match-undo" onClick={() => { setCompareWith(''); setCompareLabel(''); setAutoMatchToast(null) }}>撤销</span>
          <span className="auto-match-close" onClick={() => setAutoMatchToast(null)}>×</span>
        </div>
      )}
      <div className="upload-bar">
        <button className={`chip-btn ${tags ? 'chip-filled' : ''}`} onClick={() => { setGsearch(''); setGroupTab('all'); setShowGroupModal(true) }}>
          项目组<span className="chip-val">{tagsLabel || '不选择'}</span><span className="chip-arrow">▾</span>
        </button>
        <button className={`chip-btn ${compareWith ? 'chip-filled' : ''}`} onClick={() => { setCsearch(''); setShowCompareModal(true) }}>
          对比目标<span className="chip-val">{compareLabel || '不对比'}</span><span className="chip-arrow">▾</span>
        </button>
        <button className="btn-start" disabled={pendingFiles.length === 0} onClick={handleStart}>开始审核</button>
        {pendingFiles.length > 0 && <button className="btn-clear" onClick={() => setPendingFiles([])}>清空</button>}
      </div>
      {error && <div className="upload-error">{error}</div>}

      {/* 项目组弹窗 */}
      {showGroupModal && (
        <div className="modal-overlay" onClick={() => setShowGroupModal(false)}>
          <div className="modal-container" onClick={e => e.stopPropagation()} style={{maxWidth:460}}>
            <div className="modal-header"><span className="modal-title">选择项目组</span><button onClick={() => setShowGroupModal(false)} style={{background:'none',border:'none',fontSize:20,cursor:'pointer',color:'#667085'}} aria-label="关闭">✕</button></div>
            <div className="modal-body" style={{padding:16}}>
              <input className="filter-input" style={{width:'100%',marginBottom:8}} placeholder="搜索项目组..." value={gsearch} onChange={e => setGsearch(e.target.value)} />
              <div className="pg-role-tabs">
                {['all', 'mine', 'recent'].map(t => (
                  <button key={t} className={`pg-role-tab ${groupTab === t ? 'pg-role-tab-on' : ''}`} onClick={() => setGroupTab(t)}>
                    {t === 'all' ? '全部' : t === 'mine' ? '我创建的' : '最近使用'}
                  </button>
                ))}
              </div>
              <div className="pg-member-list">
                {projectGroups.filter(g => {
                  if (gsearch && !g.name.includes(gsearch)) return false
                  if (groupTab === 'mine' && g.created_by !== 'current') return true  // all visible for now
                  return true
                }).map((g: any) => (
                  <label key={g.id} className={`pg-member-item ${tags === g.name ? 'pg-member-on' : ''}`} onClick={() => { setTags(g.name); localStorage.setItem('last_project_group', g.name); setShowGroupModal(false) }}>
                    <div style={{flex:1}}><span style={{fontWeight:500}}>{g.name}</span><span style={{fontSize:11,color:'#98a2b3',marginLeft:6}}>{(g.members||[]).length} 位成员</span></div>
                    {tags === g.name && <span style={{color:'#067647'}}>✓</span>}
                  </label>
                ))}
                <label className={`pg-member-item ${!tags ? 'pg-member-on' : ''}`} onClick={() => { setTags(''); localStorage.removeItem('last_project_group'); setShowGroupModal(false) }}>
                  <div style={{flex:1}}><span style={{fontWeight:500,color:'#98a2b3'}}>不选择（仅自己可见）</span></div>
                  {!tags && <span style={{color:'#067647'}}>✓</span>}
                </label>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 对比目标弹窗 */}
      {showCompareModal && (
        <div className="modal-overlay" onClick={() => setShowCompareModal(false)}>
          <div className="modal-container" onClick={e => e.stopPropagation()} style={{maxWidth:500}}>
            <div className="modal-header"><span className="modal-title">选择对比目标</span><button onClick={() => setShowCompareModal(false)} style={{background:'none',border:'none',fontSize:20,cursor:'pointer',color:'#667085'}} aria-label="关闭">✕</button></div>
            <div className="modal-body" style={{padding:16}}>
              <input className="filter-input" style={{width:'100%',marginBottom:8}} placeholder="按文件名搜索..." value={csearch} onChange={e => setCsearch(e.target.value)} />
              <div className="pg-member-list">
                <label className={`pg-member-item ${!compareWith ? 'pg-member-on' : ''}`} onClick={() => { setCompareWith(''); setCompareLabel(''); setShowCompareModal(false) }}>
                  <div style={{flex:1}}><span style={{fontWeight:500,color:'#98a2b3'}}>不对比（生成新报告组）</span></div>
                  {!compareWith && <span style={{color:'#067647'}}>✓</span>}
                </label>
                {recentGroups.filter(r => !csearch || r.filename.includes(csearch)).map(r => (
                  <label key={r.id} className={`pg-member-item ${compareWith === r.id ? 'pg-member-on' : ''}`} onClick={() => { setCompareWith(r.id); setCompareLabel(r.filename); setShowCompareModal(false) }}>
                    <div style={{flex:1}}><span style={{fontWeight:500}}>{r.filename}</span><span style={{fontSize:11,color:'#98a2b3',marginLeft:6}}>{r.group_id}</span></div>
                    {compareWith === r.id && <span style={{color:'#067647'}}>✓</span>}
                  </label>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Streaming error card ───────────────────────────────────────────────

const StreamErrorCard = memo(function StreamErrorCard({ idx, item }: { idx: number; item: ReviewItem }) {
  return (
    <div className={`error-item error-${item.severity} streaming-item`}>
      <div className="error-item-header">
        <span className={`sev-badge sev-badge-${item.severity}`}>
          {SEV_LABEL[item.severity]}
        </span>
        <span className="error-item-index">#{idx + 1}</span>
      </div>
      <div className="error-location">{item.location}</div>
      <div>
        <mark className={`error-highlight-inline error-${item.severity}`}>
          {item.original_text}
        </mark>
      </div>
      <div className="error-field">
        <span className="error-field-label">原因</span>
        <br />{item.error_description}
      </div>
      <div className="error-field">
        <span className="error-field-label">标准依据</span>
        <br />{item.standard_reference}
      </div>
      <div className="error-field">
        <span className="error-field-label">建议</span>
        <br />{item.suggestion}
      </div>
    </div>
  )
})
