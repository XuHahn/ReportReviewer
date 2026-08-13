import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Modal } from '@mantine/core'
import { ChevronRight, FileCheck2, History, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { getDocumentSet, getReviewRun, getVersionHistory, listReviewRuns } from '../api'
import { useSession } from '../App'
import { demoOverview, demoSnapshot } from '../mockData'
import { formatDate, StatusPill } from '../components/common'
import { DOC_LABELS, STATUS_LABELS, type DocType } from '../types'
import { extractionGateFor, shouldPollOverview } from '../reviewLogic'
import IntakeStage from './review/IntakeStage'
import ExtractionStage from './review/ExtractionStage'
import RunStage from './review/RunStage'
import FindingsStage from './review/FindingsStage'
import CompleteStage from './review/CompleteStage'

export type ReviewStage = 'intake' | 'extraction' | 'run' | 'findings' | 'complete'
const stages: Array<{ key: ReviewStage; index: string; label: string; hint: string }> = [
  { key: 'intake', index: '01', label: '资料与范围', hint: '四份必传资料' },
  { key: 'extraction', index: '02', label: '提取核查', hint: '逐份确认原文' },
  { key: 'run', index: '03', label: '机器审核', hint: '证据图与规则' },
  { key: 'findings', index: '04', label: '问题裁决', hint: '人工结论' },
  { key: 'complete', index: '05', label: '审核完成', hint: '导出与修订' },
]
const versionDocTypes: DocType[] = ['order_form', 'test_plan', 'original_records', 'final_report']

export default function ReviewWorkspace() {
  const { setId = '', stage } = useParams<{ setId: string; stage: ReviewStage }>()
  const navigate = useNavigate()
  const { demo } = useSession()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [versionsOpen, setVersionsOpen] = useState(false)
  const current = stages.some(item => item.key === stage) ? stage as ReviewStage : 'intake'
  const overviewQuery = useQuery({
    queryKey: ['set', setId, demo],
    queryFn: () => demo ? Promise.resolve({ ...demoOverview, set_id: setId || demoOverview.set_id }) : getDocumentSet(setId),
    enabled: Boolean(setId),
    refetchInterval: query => shouldPollOverview(query.state.data, current, demo) ? 1500 : false,
    // Extraction is an application-owned background job. Keep the status
    // stream alive while the user switches browser tabs or moves to another
    // operation, and always refresh when they return to this route.
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: true,
    refetchOnMount: 'always',
  })
  const runsQuery = useQuery({
    queryKey: ['runs', setId, demo],
    queryFn: () => demo ? Promise.resolve({ runs: [demoSnapshot.run], total: 1 }) : listReviewRuns(setId),
    enabled: Boolean(setId),
    refetchInterval: query => {
      const latestStatus = query.state.data?.runs?.[0]?.status
      return current === 'run' && !demo && ['created', 'building', 'running'].includes(latestStatus || '') ? 1500 : false
    },
  })
  const runId = runsQuery.data?.runs?.[0]?.graph_id
  const snapshotQuery = useQuery({ queryKey: ['snapshot', setId, runId, demo], queryFn: () => demo ? Promise.resolve(demoSnapshot) : getReviewRun(setId, runId!), enabled: current === 'findings' || current === 'complete' ? Boolean(runId) : false })
  const versionsQuery = useQuery({
    queryKey: ['versions', setId],
    queryFn: async () => {
      const results = await Promise.allSettled(versionDocTypes.map(type => getVersionHistory(setId, type)))
      return Object.fromEntries(results.map((result, index) => [
        versionDocTypes[index],
        result.status === 'fulfilled'
          ? { versions: result.value.versions, failed: false }
          : { versions: [], failed: true },
      ])) as Record<DocType, { versions: any[]; failed: boolean }>
    },
    enabled: versionsOpen && !demo,
  })
  const overview = overviewQuery.data
  const latestRun = runsQuery.data?.runs?.[0]
  const requiredDocuments = overview?.documents?.filter(doc => doc.doc_type !== 'test_standard') || []
  const extractionGate = extractionGateFor(overview, demo)
  // A locked historical snapshot has already crossed the extraction gate. Do
  // not reopen the new exception-only path just because legacy metadata is
  // partial or does not contain the gate decision.
  const extractionCompleted = extractionGate.status === 'pass'
    || Boolean(latestRun && ['locked', 'reviewing', 'reviewed'].includes(overview?.status || ''))
  // Extraction review is no longer a user-facing workflow step. The quality
  // gate remains enforced in the intake stage; actionable failures become
  // findings, while the old full-page exception editor is not exposed again.
  const visibleStages = stages
    .filter(item => item.key !== 'extraction')
    .map((item, index) => ({ ...item, index: String(index + 1).padStart(2, '0') }))

  useEffect(() => setDrawerOpen(false), [current])
  const stageStatus = useMemo(() => ({
    intake: (overview?.documents?.length || 0) >= 4 ? 'done' : 'current',
    extraction: extractionCompleted ? 'done' : current === 'extraction' ? 'current' : 'todo',
    run: runsQuery.data?.runs?.length ? 'done' : current === 'run' ? 'current' : 'todo',
    findings: snapshotQuery.data?.decisions?.length ? 'current' : current === 'findings' ? 'current' : 'todo',
    complete: overview?.status === 'reviewed' && !latestRun?.pending_count ? 'done' : current === 'complete' ? 'current' : 'todo',
  }), [overview, requiredDocuments, latestRun, runsQuery.data, snapshotQuery.data, current])
  const stageAccess: Record<ReviewStage, boolean> = {
    intake: true,
    extraction: requiredDocuments.length > 0,
    run: ['locked', 'reviewing', 'reviewed'].includes(overview?.status || ''),
    findings: Boolean(latestRun),
    complete: Boolean(latestRun),
  }

  if (!setId) return <Navigate to="/tasks" replace />
  if (current === 'extraction' && !overviewQuery.isLoading) {
    const nextStage = ['locked', 'reviewing', 'reviewed'].includes(overview?.status || '') ? 'run' : 'intake'
    return <Navigate to={`/tasks/${setId}/${nextStage}`} replace />
  }
  return <div className={`review-shell ${current === 'findings' ? 'findings-mode' : ''}`}>
    <header className="review-header">
      <div className="review-heading"><div className="breadcrumbs"><span><Link to="/tasks">审核任务</Link></span><span><ChevronRight size={12}/>{setId}</span></div><div><FileCheck2 size={20}/><span><b>{overview?.title || setId}</b><small>{overview?.project_group_name || '未设置项目组'} · v{overview?.revision || 1}</small></span><StatusPill status={overview?.status || 'draft'} /></div></div>
      <div className="review-header-actions"><button className="text-button" onClick={() => setVersionsOpen(true)}><History size={15}/>版本记录</button>{current === 'findings' && <button className="text-button mobile-only" onClick={() => setDrawerOpen(v => !v)}>{drawerOpen ? <PanelLeftClose size={16}/> : <PanelLeftOpen size={16}/>}问题列表</button>}</div>
    </header>
    <nav className="stage-rail" aria-label="审核步骤">{visibleStages.map(item => <button key={item.key} disabled={!stageAccess[item.key]} className={`${current === item.key ? 'active' : ''} ${stageStatus[item.key]}`} onClick={() => navigate(`/tasks/${setId}/${item.key}`)}>
      <span>{stageStatus[item.key] === 'done' ? '✓' : item.index}</span><div><b>{item.label}</b><small>{item.hint}</small></div>{item.key !== 'complete' && <i />}
    </button>)}</nav>
    <div className="review-stage">
      {current === 'intake' && <IntakeStage setId={setId} overview={overview} loading={overviewQuery.isLoading} demo={demo} />}
      {current === 'extraction' && <ExtractionStage setId={setId} overview={overview} demo={demo} />}
      {current === 'run' && <RunStage setId={setId} overview={overview} runs={runsQuery.data?.runs || []} demo={demo} />}
      {current === 'findings' && <FindingsStage setId={setId} overview={overview} snapshot={snapshotQuery.data} loading={snapshotQuery.isLoading} demo={demo} drawerOpen={drawerOpen} onDrawerClose={() => setDrawerOpen(false)} />}
      {current === 'complete' && <CompleteStage setId={setId} overview={overview} snapshot={snapshotQuery.data} demo={demo} />}
    </div>
    <Modal opened={versionsOpen} onClose={() => setVersionsOpen(false)} title="四份资料版本记录" size="lg" closeButtonProps={{ 'aria-label': '关闭版本记录窗口' }}><div className="version-history-list">{demo ? <p>演示预览不提供真实版本记录。</p> : versionsQuery.isLoading ? <p>正在读取版本记录…</p> : versionsQuery.isError ? <p>版本记录读取失败，请稍后重试。</p> : versionDocTypes.map(type => { const state = versionsQuery.data?.[type]; return <section key={type}><h3>{DOC_LABELS[type]}</h3>{state?.failed ? <p className="version-load-error">该类资料的版本记录读取失败，请稍后重试。</p> : state?.versions.length ? state.versions.map(version => <div key={version.doc_id}><span>v{version.doc_version}</span><b>{version.filename}</b><small>{STATUS_LABELS[version.extraction_status || 'pending'] || version.extraction_status || '状态未记录'} · {version.created_at ? formatDate(version.created_at) : '时间未记录'}</small></div>) : <p>尚未上传</p>}</section> })}</div></Modal>
  </div>
}
