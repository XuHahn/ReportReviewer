import { useMemo } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, CircleDashed, Clock3, Play, RotateCcw, ShieldCheck } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { notifications } from '@mantine/notifications'
import { apiErrorMessage, getHealth, startReviewRun } from '../../api'
import type { DocumentSetOverview, EvidenceGraphRunSummary } from '../../types'
import { EmptyState, StatusPill } from '../../components/common'
import { presentCheckExecution, type CheckExecutionRecord } from '../../reviewLogic'

const phases = [
  { label: '读取锁定快照', note: '四份业务资料与原始记录成员' },
  { label: '识别测试项身份', note: '按原文测试项目、样品和模式建立身份' },
  { label: '建立统一证据图', note: '连接计划、记录、报告和已发布标准' },
  { label: '执行检查并生成问题', note: '通用检查、文档检查与标准建议' },
  { label: '汇总可处理结论', note: '保存证据、来源与规则执行状态' },
]

function elapsed(start?: string, end?: string) {
  if (!start || !end) return '—'
  const seconds = Math.max(0, Math.round((new Date(end).getTime() - new Date(start).getTime()) / 1000))
  return seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`
}

export default function RunStage({ setId, overview, runs, demo }: { setId: string; overview?: DocumentSetOverview; runs: EvidenceGraphRunSummary[]; demo: boolean }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const healthQuery = useQuery({
    queryKey: ['health', demo],
    queryFn: () => demo ? Promise.resolve({ status: 'demo', vision: { status: 'ready' } }) : getHealth(),
    refetchOnWindowFocus: true,
    refetchInterval: query => query.state.data?.vision?.status === 'preparing' ? 2000 : 15000,
  })
  const visionStatus = healthQuery.data?.vision?.status
  const visionUnavailable = !demo && (healthQuery.isError || visionStatus === 'unavailable')
  const visionPreparing = !demo && (healthQuery.isLoading || visionStatus === 'preparing')
  const latest = runs[0]
  const terminal = latest && ['machine_complete', 'machine_incomplete', 'failed', 'aborted'].includes(latest.status)
  const progress = terminal ? 100 : latest ? undefined : 0

  const start = useMutation({ mutationFn: () => demo ? Promise.resolve({ run_id: 'run-demo-3' }) : startReviewRun(setId), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['runs', setId] }); queryClient.invalidateQueries({ queryKey: ['set', setId, demo] }); queryClient.invalidateQueries({ queryKey: ['sets', demo] }); queryClient.invalidateQueries({ queryKey: ['stats', demo] }); notifications.show({ color: 'blue', title: '审核已开始', message: '可以留在本页观察，也可以稍后返回。' }) }, onError: error => notifications.show({ color: 'red', title: '无法启动审核', message: apiErrorMessage(error) }) })
  const checks = useMemo(() => {
    const metadata = latest?.metadata || {}
    return ['coverage_check_execution', 'document_check_execution', 'general_check_execution', 'standard_check_execution']
      .flatMap(key => Array.isArray(metadata[key]) ? metadata[key] as CheckExecutionRecord[] : [])
  }, [latest])
  const incompleteChecks = checks
    .map(check => ({ check, presentation: presentCheckExecution(check) }))
    .filter(item => ['system_incomplete', 'failed'].includes(item.presentation.state))
  const failedChecks = checks
    .map(check => ({ check, presentation: presentCheckExecution(check) }))
    .filter(item => item.presentation.state === 'failed')
  const runIncomplete = latest?.status === 'machine_incomplete'
  const runFailed = latest?.status === 'failed' || latest?.status === 'aborted'
  const sourceMissingChecks = checks
    .map(check => ({ check, presentation: presentCheckExecution(check) }))
    .filter(item => item.presentation.state === 'source_missing')
  const systemIncomplete = incompleteChecks.length > 0 || (runIncomplete && checks.length === 0)
  const sourceIncomplete = !systemIncomplete && sourceMissingChecks.length > 0
  const originalRecordCount = overview?.documents?.find(doc => doc.doc_type === 'original_records')?.page_count

  if (!latest && !['locked', 'reviewing', 'reviewed'].includes(overview?.status || '')) return <EmptyState icon="error" title="资料尚未锁定" description="请返回资料与范围，完成四份资料的提取核查后再启动机器审核。" />
  return <div className="stage-page run-stage">
    <div className="stage-intro"><div className="eyebrow">证据图审核</div><h1>让每一步审核，都有迹可循</h1><p>资料缺失与系统未完成将如实呈现，每项检查均保留清晰的执行轨迹与判断依据。</p></div>
    {!latest ? <section className="run-ready"><span><ShieldCheck/></span><div><h2>锁定资料已经准备好</h2><p>输入：v{overview?.revision || 1} · {overview?.documents?.length || 0} 份业务资料 · {overview?.standards?.length ? overview.standards.map(item => item.code).join('、') : '未选择标准'}</p><ul><li>机器审核不会修改原始文件</li><li>所有问题都必须带原文证据</li><li>失败会明确保留失败状态，不产生伪通过</li></ul>{(visionPreparing || visionUnavailable) && <div className={`run-model-gate ${visionUnavailable ? 'warning' : ''}`} role={visionUnavailable ? 'alert' : 'status'}><AlertTriangle size={15}/><span>{visionUnavailable ? '视觉模型暂不可用，恢复后才能开始审核。' : '视觉模型准备中，后端已启动；模型就绪后此按钮会自动开放。'}</span></div>}<button className="primary-button" onClick={() => start.mutate()} disabled={start.isPending || visionPreparing || visionUnavailable}><Play size={16}/>{start.isPending ? '正在创建运行…' : visionPreparing ? '等待视觉模型就绪…' : visionUnavailable ? '视觉模型不可用' : '开始统一证据图审核'}</button></div></section> : <>
      <section className={`run-progress ${runFailed || runIncomplete ? 'failed' : ''}`}><div className="run-progress-head"><div><StatusPill status={latest.status}/><h2>{terminal ? runFailed ? '本次审核未完成' : systemIncomplete ? '本次审核存在系统未完成项' : sourceIncomplete ? '机器检查已完成，资料尚待补齐' : '机器审核已经完成' : '本次审核运行中'}</h2><p>运行 ID：{latest.graph_id}</p></div><b>{progress === 100 ? runFailed || systemIncomplete ? '未完成' : sourceIncomplete ? '待补资料' : '完成' : '运行中'}</b></div><div className="progress-track"><i className={progress === undefined ? 'indeterminate' : ''} style={progress === undefined ? undefined : { width: `${progress}%` }}/></div>
        <div className="phase-list">{phases.map((phase, index) => { const state = terminal && !runFailed && !systemIncomplete ? 'done' : terminal && latest.status === 'machine_complete' ? 'done' : runFailed ? 'failed' : systemIncomplete && index === phases.length - 1 ? 'failed' : systemIncomplete ? 'done' : 'active'; return <div className={state} key={phase.label}><span>{state === 'done' ? <Check/> : state === 'failed' ? <AlertTriangle/> : <CircleDashed className="spin"/>}</span><div><b>{phase.label}</b><small>{phase.note}</small></div><em>{state === 'done' ? '已记录' : state === 'active' ? '运行状态由后端更新' : '未完成'}</em></div>})}</div>
        <div className="run-actions">{runFailed && <button className="secondary-button" onClick={() => start.mutate()}><RotateCcw size={15}/>重新运行</button>}{terminal && !runFailed && <button className="primary-button" onClick={() => navigate(`/tasks/${setId}/findings`)}>进入问题裁决</button>}</div>
      </section>
      {(runIncomplete || runFailed || incompleteChecks.length > 0) && <section className="run-explanation"><div className="section-heading"><div><h2>{runFailed ? '运行未完成' : systemIncomplete ? '系统未完成' : '资料尚待补充'}</h2><p>{systemIncomplete || runFailed ? '系统不会把未完成包装成通过，也不会把系统未完成误报成资料缺失。' : '机器检查已经执行完毕；以下缺口来自源文件未填写必要信息，并非系统运行失败。'}</p></div></div><dl><div><dt>{sourceIncomplete ? '已执行' : '已完成'}</dt><dd>{sourceIncomplete ? checks.length : checks.length - incompleteChecks.length} 项检查已保存执行状态</dd></div><div><dt>{sourceIncomplete ? '待补资料' : '未完成'}</dt><dd>{sourceIncomplete ? `${sourceMissingChecks.length} 项检查缺少源文件字段` : incompleteChecks.length || (runFailed ? '本轮运行在检查明细形成前终止' : '未形成完整检查明细')}</dd></div><div><dt>原因</dt><dd>{sourceIncomplete ? sourceMissingChecks.map(item => `${item.check.check_id || '未命名检查'}：${item.presentation.reasonLabel}`).join('；') : failedChecks.length ? failedChecks.map(item => `${item.check.check_id || '未命名检查'}：${item.presentation.reasonLabel}`).join('；') : runFailed ? '后端运行状态为失败或中止' : '部分规则的输入门禁尚未满足'}</dd></div><div><dt>影响</dt><dd>{sourceIncomplete ? '相关检查暂不能形成完整结论；其他已执行检查和原文证据不受影响。' : '本次运行不能作为完整审核结论；已有证据和已完成检查仍可追溯查看。'}</dd></div></dl></section>}
      <div className="run-details"><section><div className="section-heading"><div><h2>检查实际执行情况</h2><p>直接读取本次运行保存的检查门禁记录。</p></div></div><div className="check-list">{checks.length ? checks.map((check, index) => { const { state, expected, checked, reasonLabel } = presentCheckExecution(check); return <div key={`${check.check_id || 'check'}-${index}`}><span className={state}>{state === 'passed' ? <Check/> : state === 'issues_found' ? '!' : state === 'source_missing' ? '?' : '×'}</span><div><b>{check.check_id || '未命名检查'}</b><small>已核查证据 {checked} / {expected} · 发现 {check.finding_count || 0} 项 · {reasonLabel}</small></div><StatusPill status={state}>{state === 'passed' ? '已完成' : state === 'issues_found' ? '发现问题' : state === 'source_missing' ? '资料缺失' : state === 'not_applicable' ? '不适用' : '系统未完成'}</StatusPill></div> }) : <p>本次运行尚未写入检查执行明细。</p>}</div></section>
        <aside><h2>本轮输入</h2><dl><div><dt>锁定版本</dt><dd>v{overview?.revision || 1}</dd></div><div><dt>业务资料</dt><dd>{overview?.documents?.length || 0} / 4</dd></div><div><dt>原始记录</dt><dd>{originalRecordCount ? `${originalRecordCount} 个成员` : '未记录成员数'}</dd></div><div><dt>标准范围</dt><dd>{overview?.standards?.length || 0} 份发布标准</dd></div><div><dt>运行耗时</dt><dd><Clock3 size={13}/>{elapsed(latest.created_at, latest.completed_at || latest.updated_at)}</dd></div></dl><p>{overview?.standards?.length ? '只使用已人工确认并发布的标准要求。' : '本次未选择标准，标准条款审查将明确跳过。'}</p></aside>
      </div>
    </>}
  </div>
}
