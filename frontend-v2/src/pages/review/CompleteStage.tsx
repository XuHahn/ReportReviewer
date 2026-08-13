import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Archive, Check, Download, FileArchive, FileSpreadsheet, FileText, RotateCcw } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { notifications } from '@mantine/notifications'
import { apiErrorMessage, createDocumentSetRevision, downloadReviewRunExport } from '../../api'
import type { DocumentSetOverview, EvidenceGraphSnapshot } from '../../types'
import { EmptyState, EvidenceRail } from '../../components/common'
import { countDismissedMachineFindings } from '../../reviewLogic'

export default function CompleteStage({ setId, overview, snapshot }: { setId: string; overview?: DocumentSetOverview; snapshot?: EvidenceGraphSnapshot }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState('')
  const attention = snapshot?.findings.filter(item => item.status !== 'confirmed_pass' && item.status !== 'not_applicable') || []
  const decided = attention.filter(item => snapshot?.decisions.some(decision => decision.finding_id === item.finding_id)).length
  const revisionCount = snapshot?.decisions.filter(item => ['report_revision', 'raw_record_supplement', 'source_correction'].includes(item.resolution_code || '')).length || 0
  const complete = attention.length === decided

  async function exportFile(format: 'xlsx' | 'pdf' | 'evidence') {
    if (!snapshot) return
    setBusy(format)
    try { await downloadReviewRunExport(setId, snapshot.run.graph_id, format); notifications.show({ color: 'green', message: '导出文件已经开始下载' }) }
    catch (error) { notifications.show({ color: 'red', message: apiErrorMessage(error) }) }
    finally { setBusy('') }
  }
  async function revise() {
    setBusy('revision')
    try { await createDocumentSetRevision(setId); await queryClient.invalidateQueries({ queryKey: ['sets'] }); await queryClient.invalidateQueries({ queryKey: ['stats'] }); navigate(`/tasks/${setId}/intake`) }
    catch (error) { notifications.show({ color: 'red', message: apiErrorMessage(error) }) }
    finally { setBusy('') }
  }

  if (!snapshot) return <EmptyState title="还没有可完成的审核快照" description="先完成机器审核和问题裁决，再进入导出与归档。" />
  return <div className="stage-page complete-stage">
    <section className={`completion-hero ${complete ? 'ready' : ''}`}><span className="completion-mark">{complete ? <Check/> : `${decided}/${attention.length}`}</span><div><div className="eyebrow">REVIEW COMPLETION</div><h1>{complete ? '人工裁决已经闭合' : '还有问题等待人工结论'}</h1><p>{complete ? '可以导出审核结果、退回修订或确认归档。原始证据和裁决依据会随本轮快照保留。' : `已处理 ${decided} 条，仍有 ${attention.length - decided} 条问题没有处理依据。`}</p></div></section>
    <EvidenceRail states={{ order_form: 'ok', test_plan: 'ok', original_records: 'ok', final_report: revisionCount ? 'warn' : 'ok' }} />
    <div className="completion-grid"><section className="completion-summary"><div className="section-heading"><div><h2>本轮审核结论</h2><p>{overview?.title || setId} · v{overview?.revision || 1}</p></div></div><dl><div><dt>机器发现</dt><dd>{attention.length} 条</dd></div><div><dt>人工已处理</dt><dd>{decided} 条</dd></div><div><dt>要求修订</dt><dd>{revisionCount} 条</dd></div><div><dt>机器判断有误 / 不适用</dt><dd>{countDismissedMachineFindings(snapshot.decisions)} 条</dd></div></dl><div className="completion-note"><FileText/><p><b>归档不会删除任何问题。</b><br/>它冻结当前人工结论；后续修改业务资料需要创建新修订。</p></div></section>
      <section className="export-panel"><div className="section-heading"><div><h2>导出审核材料</h2><p>文件由后端根据当前不可变审核快照生成。</p></div></div><div className="export-options"><button onClick={() => exportFile('pdf')} disabled={Boolean(busy)}><span><FileText/></span><div><b>审核报告 PDF</b><small>适合签阅与归档</small></div><Download/></button><button onClick={() => exportFile('xlsx')} disabled={Boolean(busy)}><span><FileSpreadsheet/></span><div><b>问题明细 Excel</b><small>适合筛选和后续跟踪</small></div><Download/></button><button onClick={() => exportFile('evidence')} disabled={Boolean(busy)}><span><FileArchive/></span><div><b>完整证据包 ZIP</b><small>含高亮页、原文与裁决记录</small></div><Download/></button></div></section>
      <aside className="next-action-panel"><h2>下一步</h2>{revisionCount ? <><div className="action-callout warning"><RotateCcw/><p><b>{revisionCount} 条结论要求修改资料</b><br/>全部问题完成裁决后才能创建修订，当前快照会继续保留。</p></div><button className="primary-button" onClick={revise} disabled={!complete || Boolean(busy)}>退回并创建修订</button></> : complete ? <><div className="action-callout success"><Archive/><p><b>本轮人工审核已闭合</b><br/>证据、机器发现与人工结论会持续保留。</p></div><button className="primary-button" onClick={() => navigate('/tasks')}>返回已审核任务</button></> : <><div className="action-callout warning"><Archive/><p><b>本轮尚未完成</b><br/>还有问题缺少人工结论，不能标记为审核完成。</p></div><button className="primary-button" disabled>等待人工裁决完成</button></>}<button className="secondary-button" onClick={() => navigate(`/tasks/${setId}/findings`)}>返回检查处理依据</button></aside>
    </div>
  </div>
}
