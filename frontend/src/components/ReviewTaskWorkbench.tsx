import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  downloadReviewRunExport,
  getDocumentSet,
  getReviewRun,
  listDocumentSets,
  listReviewRuns,
  startReviewRun,
} from '../api'
import type {
  DocumentSetListItem,
  DocumentSetOverview,
  EvidenceGraphRunSummary,
  EvidenceGraphSnapshot,
} from '../types'
import Icon from './Icons'
import ReviewIssueWorkspace from './ReviewIssueWorkspace'

interface Props {
  onCreateTask: () => void
  onEditTask: (setId: string, status: string) => void
}

const STATUS_LABEL: Record<string, string> = {
  incomplete: '待补资料', extracting: '提取中', locked: '可开始审核', reviewing: '审核中',
  reviewed: '已完成', revision: '修订中', error: '运行失败',
  building: '构建证据图', running: '审核中', machine_complete: '机器审核完成',
  machine_incomplete: '待人工确认', failed: '运行失败',
}

function formatTime(value: string) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function statusClass(status: string) {
  if (status === 'confirmed_error' || status === 'failed' || status === 'error') return 'danger'
  if (status.includes('unresolved') || status === 'machine_incomplete' || status === 'revision') return 'warning'
  if (status === 'confirmed_pass' || status === 'machine_complete' || status === 'reviewed') return 'success'
  return 'neutral'
}

export default function ReviewTaskWorkbench({ onCreateTask, onEditTask }: Props) {
  const [sets, setSets] = useState<DocumentSetListItem[]>([])
  const [selectedSetId, setSelectedSetId] = useState('')
  const [overview, setOverview] = useState<DocumentSetOverview | null>(null)
  const [runs, setRuns] = useState<EvidenceGraphRunSummary[]>([])
  const [snapshot, setSnapshot] = useState<EvidenceGraphSnapshot | null>(null)
  const [loading, setLoading] = useState(true)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const loadRequestId = useRef(0)

  const refreshSets = useCallback(async () => {
    const response = await listDocumentSets()
    setSets(response.sets)
    setSelectedSetId(current => current || response.sets[0]?.set_id || '')
  }, [])

  useEffect(() => {
    refreshSets().catch(err => setError(err?.response?.data?.detail || err.message || '任务加载失败'))
      .finally(() => setLoading(false))
  }, [refreshSets])

  const loadTask = useCallback(async (setId: string) => {
    if (!setId) return
    const requestId = ++loadRequestId.current
    setError('')
    const [task, runList] = await Promise.all([getDocumentSet(setId), listReviewRuns(setId)])
    const nextSnapshot = runList.runs[0]
      ? await getReviewRun(setId, runList.runs[0].graph_id)
      : null
    if (requestId !== loadRequestId.current) return
    setOverview(task)
    setRuns(runList.runs)
    setSnapshot(nextSnapshot)
  }, [])

  const selectTask = (setId: string) => {
    if (setId === selectedSetId) return
    // Clear the previous task in the same render as the id change. Otherwise
    // its graph can briefly be requested through the newly selected set id.
    loadRequestId.current += 1
    setSelectedSetId(setId)
    setOverview(null)
    setRuns([])
    setSnapshot(null)
  }

  useEffect(() => {
    loadTask(selectedSetId).catch(err => setError(err?.response?.data?.detail || err.message || '任务详情加载失败'))
  }, [selectedSetId, loadTask])

  useEffect(() => {
    const active = runs[0]?.status
    if (!selectedSetId || !['created', 'building', 'running'].includes(active || '')) return
    const timer = window.setInterval(() => loadTask(selectedSetId).catch(() => undefined), 3000)
    return () => window.clearInterval(timer)
  }, [selectedSetId, runs, loadTask])

  const counts = useMemo(() => ({
    attention: sets.filter(item => ['incomplete', 'revision', 'error'].includes(item.status)).length,
    reviewing: sets.filter(item => ['extracting', 'reviewing'].includes(item.status)).length,
    ready: sets.filter(item => item.status === 'locked').length,
    completed: sets.filter(item => item.status === 'reviewed').length,
  }), [sets])

  async function start() {
    if (!selectedSetId) return
    setStarting(true)
    setError('')
    try {
      await startReviewRun(selectedSetId)
      await loadTask(selectedSetId)
    } catch (err: any) {
      const detail = err?.response?.data?.detail
      setError(typeof detail === 'string' ? detail : detail?.message || err.message || '启动审核失败')
    } finally {
      setStarting(false)
    }
  }

  const resultReady = snapshot && !['created', 'building', 'running'].includes(snapshot.run.status)
  if (loading) return <div className="task-empty">正在加载审核任务…</div>

  return <div className="task-workbench">
    <header className="task-hero">
      <div><span className="task-eyebrow">EVIDENCE GRAPH REVIEW</span><h1>审核任务工作台</h1><p>从资料完整性、证据提取到人工裁决，在一个任务中完成。</p></div>
      <button className="task-primary" onClick={onCreateTask}><Icon name="plus" size={16} />新建审核任务</button>
    </header>

    <section className="task-metrics" aria-label="任务概况">
      <div><span>需要处理</span><strong>{counts.attention}</strong><small>缺资料、修订或失败</small></div>
      <div><span>正在运行</span><strong>{counts.reviewing}</strong><small>提取与审核任务</small></div>
      <div><span>可以审核</span><strong>{counts.ready}</strong><small>四份资料已锁定</small></div>
      <div><span>已经完成</span><strong>{counts.completed}</strong><small>可查看与导出</small></div>
    </section>

    {error && <div className="task-alert" role="alert">{error}</div>}

    <div className={`task-grid ${resultReady ? 'review-active' : ''}`}>
      <section className="task-list-panel">
        <div className="task-panel-head"><div><h2>审核任务</h2><p>{sets.length} 个任务</p></div></div>
        <div className="task-list">
          {sets.map(item => <button key={item.set_id} className={`task-row ${selectedSetId === item.set_id ? 'active' : ''}`} onClick={() => selectTask(item.set_id)}>
            <span className="task-row-main"><b>{item.set_id}</b><small>{item.project_group_name || '未分组'} · {item.doc_count} 份资料</small></span>
            <span className={`task-status ${statusClass(item.status)}`}>{STATUS_LABEL[item.status] || item.status}</span>
          </button>)}
          {!sets.length && <div className="task-empty">还没有审核任务</div>}
        </div>
      </section>

      <section className="task-detail-panel">
        {!overview ? <div className="task-empty">选择一个任务查看详情</div> : <>
          <div className="task-detail-head">
            <div><span className="task-eyebrow">当前任务</span><h2>{overview.set_id}</h2><p>创建于 {formatTime(overview.created_at)}</p></div>
            <div className="task-detail-actions">
              {['incomplete', 'revision'].includes(overview.status) && <button className="task-secondary" onClick={() => onEditTask(selectedSetId, overview.status)}>编辑资料</button>}
              {overview.status === 'reviewed' && <button className="task-secondary" onClick={() => onEditTask(selectedSetId, overview.status)}>创建修订</button>}
              <button className="task-primary" onClick={start} disabled={starting || !['locked', 'reviewed'].includes(overview.status) || runs.some(item => ['created', 'building', 'running'].includes(item.status))}>
                <Icon name={runs.length ? 'refresh' : 'play'} size={15} />
                {starting ? '正在启动…' : !['locked', 'reviewed'].includes(overview.status) ? '请先补齐并锁定' : runs.length ? '重新审核' : '开始审核'}
              </button>
            </div>
          </div>

          <div className="task-lifecycle" aria-label="审核流程">
            {[
              ['1', '资料齐套', overview.files.filter(file => file.required && file.doc_id).length >= 4],
              ['2', '提取核查', ['locked', 'reviewing', 'reviewed'].includes(overview.status)],
              ['3', '机器审核', Boolean(runs[0])],
              ['4', '人工裁决', Boolean(snapshot?.decisions?.length)],
              ['5', '归档导出', overview.status === 'reviewed' && Boolean(snapshot)],
            ].map(([step, label, done]) => <div className={done ? 'done' : ''} key={String(step)}><span>{done ? <Icon name="check" size={13} /> : step}</span><b>{label}</b></div>)}
          </div>

          <div className="task-files">
            {overview.files.filter(file => file.required).map(file => <div key={file.doc_type} className="task-file"><span className={`task-file-dot ${file.doc_id ? 'ready' : ''}`} /><div><b>{file.label}</b><small>{file.filename || '尚未上传'}</small></div><em>{file.extraction_status || '等待文件'}</em></div>)}
            <div className="task-file optional"><span className={`task-file-dot ${overview.standard_review_enabled ? 'ready' : ''}`} /><div><b>测试标准 <i>可选</i></b><small>{overview.standards?.map(item => item.code).join('、') || '未选择，不执行标准参考检查'}</small></div></div>
          </div>

          {runs[0] && <div className="run-summary">
            <div><span>最新运行</span><b className={`task-status ${statusClass(runs[0].status)}`}>{STATUS_LABEL[runs[0].status] || runs[0].status}</b></div>
            <div><span>节点</span><strong>{runs[0].node_count}</strong></div><div><span>关系</span><strong>{runs[0].edge_count}</strong></div>
            <div><span>确定问题</span><strong className="danger-text">{runs[0].error_count}</strong></div><div><span>待确认</span><strong className="warning-text">{runs[0].unresolved_count}</strong></div>
          </div>}

          {resultReady && <div className="review-export-bar"><span>导出本次审核</span>
            <button onClick={() => downloadReviewRunExport(selectedSetId, snapshot.run.graph_id, 'pdf')}><Icon name="download" size={13} />PDF 报告</button>
            <button onClick={() => downloadReviewRunExport(selectedSetId, snapshot.run.graph_id, 'xlsx')}><Icon name="download" size={13} />Excel 明细</button>
            <button onClick={() => downloadReviewRunExport(selectedSetId, snapshot.run.graph_id, 'evidence')}><Icon name="download" size={13} />证据包 ZIP</button>
          </div>}

          {resultReady && <ReviewIssueWorkspace setId={selectedSetId} overview={overview} snapshot={snapshot} onRefresh={() => loadTask(selectedSetId)} onReturnRevision={() => onEditTask(selectedSetId, overview.status)} />}
        </>}
      </section>
    </div>
  </div>
}
