import { useMemo, useState } from 'react'
import type {
  DocType,
  DocumentEvidence,
  DocumentEvidenceEntry,
  DocumentEvidenceStatus,
  FileStatus,
  PipelineIssue,
  EmcStandard,
  TraceSource,
} from '../types'
import TracePanel from './TracePanel'
import StandardMappingModal from './StandardMappingModal'

const DOC_ORDER: DocType[] = ['order_form', 'test_plan', 'original_records', 'final_report']
const DOC_LABELS: Record<string, string> = {
  order_form: '委托单', test_plan: '试验计划',
  original_records: '原始记录', final_report: '检测报告',
}
const STATUS_META: Record<DocumentEvidenceStatus, { label: string; cls: string }> = {
  conflict: { label: '冲突', cls: 'conflict' },
  supporting: { label: '一致', cls: 'supporting' },
  evidence: { label: '有证据', cls: 'evidence' },
  missing: { label: '缺失', cls: 'missing' },
  not_involved: { label: '不参与', cls: 'not-involved' },
}

const CATEGORY_LABELS: Record<string, string> = {
  plan_missing: '计划项目缺失',
  plan_report: '计划与报告不一致',
  plan_raw: '计划与原始记录不一致',
  coverage: '测试项目覆盖',
  sample_coverage: '样品覆盖',
  conclusion: '测试结论',
  result: '测试结果',
  condition: '测试条件',
  completeness: '信息完整性',
  cover: '封面信息',
  basic_info: '基础信息',
  order_report: '委托信息',
  instrument: '仪器设备',
  calibration: '校准有效期',
  date: '日期一致性',
  method: '测试方法',
  toc: '目录完整性',
  data: '测试数据',
  formula: '计算结果',
  limit: '限值引用',
  background_noise: '背景噪声',
  level_consistency: '性能等级',
  sample_id: '样品编号',
  logic: '数据逻辑',
  range: '数值范围',
  module_diff: '测试模块',
  llm: '语义审核',
  standard_requirement: '标准要求',
  standard_applicability: '标准适用性',
  standard_retrieval_uncertain: '标准要求待定位',
  standard_dependency_missing: '引用标准未选择',
  missing_source_evidence: '来源证据缺失',
}

const CHECK_LABELS: Record<string, string> = {
  R01: '报告项目与原始记录核对',
  R02: '原始记录与报告项目核对',
  R03: '样品覆盖核对',
  R04: '测试结论核对',
  R05: '测试方法核对',
  R06: '测试计划覆盖核对',
  R07: '委托信息核对',
  R08: '测试日期核对',
  R09: '标准条款核对',
  R10: '测试条件与结果核对',
  C01: '报告封面完整性核对',
  C02: '测试结果计算核对',
  C03: '测试余量计算核对',
  C04: '测试限值引用核对',
  C05: '背景噪声余量核对',
  C06: '性能等级与判定核对',
  C07: '报告目录覆盖核对',
  C08: '样品编号一致性核对',
  C09: '报告内部语义核对',
  C10: '报告内部语义核对',
  C11: '报告内部语义核对',
  S03: '标准知识定位核对',
  S04: '标准引用依赖核对',
}

function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] || '其他一致性问题'
}

function checkLabel(checkId: string): string {
  return CHECK_LABELS[checkId] || '四文件一致性核对'
}

interface Props {
  issues: PipelineIssue[]
  files: FileStatus[]
  annotationLoading: Record<string, boolean>
  expandedIssues: Set<string>
  selectedSource: { issueId: string; srcIdx: number } | null
  onExpandedChange: (next: Set<string>) => void
  onSelectedSourceChange: (next: { issueId: string; srcIdx: number } | null) => void
  onAnnotate: (issueId: string, status: string) => void
  onOpenDocument?: (docType: DocType) => void
  standards?: EmcStandard[]
  setId?: string
  onMappingSaved?: () => void
}

function resolveDocType(source: string): DocType | null {
  if (source.includes('委托')) return 'order_form'
  if (source.includes('计划')) return 'test_plan'
  if (source.includes('原始记录') || source === '记录') return 'original_records'
  if (source.includes('报告') || source.includes('TOC')) return 'final_report'
  return null
}

function isMissing(value: string): boolean {
  const text = String(value || '').trim().toLowerCase()
  return !text || ['-', '—', '/', '无', '空', 'none', 'null'].includes(text)
    || ['缺失', '未找到', '未提取', '不存在'].some(token => text.includes(token))
}

function fallbackEvidence(issue: PipelineIssue, files: FileStatus[]): Record<string, DocumentEvidence> {
  const result = Object.fromEntries(DOC_ORDER.map(docType => [docType, {
    doc_type: docType,
    label: DOC_LABELS[docType],
    doc_id: files.find(file => file.doc_type === docType)?.doc_id || '',
    filename: files.find(file => file.doc_type === docType)?.filename || '',
    status: 'not_involved' as DocumentEvidenceStatus,
    entries: [] as DocumentEvidenceEntry[],
    member_files: [],
  }])) as Record<string, DocumentEvidence>

  Object.entries(issue.sources || {}).forEach(([sourceKey, value], sourceIndex) => {
    const docType = resolveDocType(sourceKey)
    if (!docType) return
    const entry: DocumentEvidenceEntry = {
      source_index: sourceIndex, source_key: sourceKey, value: String(value),
      trace: issue.trace?.[sourceIndex],
    }
    result[docType].entries.push(entry)
  })
  for (const evidence of Object.values(result)) {
    if (!evidence.entries.length) continue
    evidence.status = evidence.entries.every(entry => isMissing(entry.value)) ? 'missing' : 'evidence'
  }
  return result
}

function compactValue(value: string): string {
  const text = String(value || '')
  try {
    const parsed = JSON.parse(text)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return text
    const labels: Record<string, string> = {
      code: '编号', name: '名称', is_executed: '执行', test_mode: '模式',
      standard_clause: '依据', acceptance: '判定', conclusion: '结论',
    }
    const parts = Object.entries(parsed)
      .filter(([, item]) => item !== '' && item !== null && item !== undefined)
      .slice(0, 6)
      .map(([key, item]) => `${labels[key] || key}: ${String(item)}`)
    return parts.join(' · ') || text
  } catch {
    return text
  }
}

function relationOf(evidence: Record<string, DocumentEvidence>): string {
  const involved = DOC_ORDER.filter(docType => evidence[docType]?.status !== 'not_involved')
  if (involved.length === 1 && involved[0] === 'final_report') return 'report'
  if (involved.length === 4) return 'four'
  if (involved.includes('test_plan') && involved.includes('original_records')) return 'plan-raw'
  if (involved.includes('original_records') && involved.includes('final_report')) return 'raw-report'
  return 'other'
}

function MatrixNode({ evidence }: { evidence: DocumentEvidence }) {
  const meta = STATUS_META[evidence.status]
  const firstValue = evidence.entries[0]?.value
  const rawCount = evidence.doc_type === 'original_records' ? evidence.member_files?.length || 0 : 0
  return (
    <div className={`issue-doc-node ${meta.cls}`} title={`${evidence.label}：${meta.label}`}>
      <div className="issue-doc-node-title"><i />{evidence.label}</div>
      <div className="issue-doc-node-value">
        {firstValue ? compactValue(firstValue) : meta.label}{rawCount > 1 ? ` · ${rawCount}份` : ''}
      </div>
    </div>
  )
}

function EvidenceColumn({
  issue, evidence, selectedSource, onSelectedSourceChange, onOpenDocument,
}: {
  issue: PipelineIssue
  evidence: DocumentEvidence
  selectedSource: { issueId: string; srcIdx: number } | null
  onSelectedSourceChange: Props['onSelectedSourceChange']
  onOpenDocument: Props['onOpenDocument']
}) {
  const meta = STATUS_META[evidence.status]
  const canOpen = Boolean(onOpenDocument && (evidence.doc_id || evidence.filename))
  return (
    <section className={`issue-evidence-column ${meta.cls}`}>
      <div className="issue-evidence-column-head">
        <div className="issue-evidence-source-title">
          <strong>{evidence.label}</strong><span>{meta.label}</span>
        </div>
        <div className="issue-evidence-filename" title={evidence.filename}>{evidence.filename || '本问题无对应文件证据'}</div>
      </div>
      <div className="issue-evidence-column-body">
        {evidence.status === 'not_involved' && (
          <div className="issue-evidence-empty">该文件不参与本问题判断</div>
        )}
        {evidence.entries.map((entry, index) => {
          const trace = entry.trace || issue.trace?.[entry.source_index]
          const selected = selectedSource?.issueId === issue.id && selectedSource.srcIdx === entry.source_index
          return (
            <div className="issue-evidence-entry" key={`${entry.source_key}-${index}`}>
              <div className="issue-evidence-field">{entry.source_key}</div>
              <div className={`issue-evidence-value ${isMissing(entry.value) ? 'missing' : ''}`}>{compactValue(entry.value)}</div>
              {trace && <div className="issue-evidence-location">{trace.location || trace.path}</div>}
              {trace && (
                <button className="issue-link-button" onClick={() => onSelectedSourceChange(selected ? null : { issueId: issue.id, srcIdx: entry.source_index })}>
                  {selected ? '收起溯源' : '查看溯源'}
                </button>
              )}
            </div>
          )
        })}
        {evidence.doc_type === 'original_records' && evidence.member_files?.length > 0 && (
          <div className="issue-raw-members">
            <div className="issue-evidence-field">ZIP 内文件</div>
            {evidence.member_files.slice(0, 8).map((member, index) => (
              <div className="issue-raw-member" key={`${member.display_name}-${index}`}>
                <span title={member.filename || member.display_name}>{member.display_name}</span>
                <em className={member.relation === 'related' ? 'related' : ''}>{member.relation === 'related' ? '相关' : '已检索'}</em>
                {(member.sequence || member.test_mode) && <small>{[member.sequence && `序号 ${member.sequence}`, member.test_mode].filter(Boolean).join(' · ')}</small>}
              </div>
            ))}
            {evidence.member_files.length > 8 && <div className="issue-more-files">另有 {evidence.member_files.length - 8} 份文件</div>}
          </div>
        )}
        {canOpen && (
          <button className="issue-link-button document" onClick={() => onOpenDocument?.(evidence.doc_type as DocType)}>核查该文件</button>
        )}
      </div>
    </section>
  )
}

export default function IssueDocumentMatrix(props: Props) {
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('all')
  const [relation, setRelation] = useState('all')
  const [mappingIssue, setMappingIssue] = useState<PipelineIssue | null>(null)
  const categories = useMemo(() => Array.from(new Set(props.issues.map(issue => issue.category || '未分类'))).sort(), [props.issues])

  const enriched = useMemo(() => props.issues.map(issue => ({
    issue,
    evidence: issue.document_evidence || fallbackEvidence(issue, props.files),
  })), [props.issues, props.files])

  const filtered = enriched.filter(({ issue, evidence }) => {
    const haystack = `${issue.field_name} ${issue.description} ${issue.category} ${Object.values(evidence).map(item => `${item.filename} ${item.entries.map(entry => entry.value).join(' ')}`).join(' ')}`.toLowerCase()
    return (!query.trim() || haystack.includes(query.trim().toLowerCase()))
      && (category === 'all' || (issue.category || '未分类') === category)
      && (relation === 'all' || relationOf(evidence) === relation)
  })

  return (
    <div className="issue-matrix-list">
      <div className="issue-matrix-toolbar">
        <select value={relation} onChange={event => setRelation(event.target.value)} aria-label="文件关系筛选">
          <option value="all">全部文件关系</option><option value="four">四文件对比</option>
          <option value="plan-raw">计划 ↔ 原始记录</option><option value="raw-report">原始记录 ↔ 检测报告</option>
          <option value="report">检测报告内部</option><option value="other">其他关系</option>
        </select>
        <select value={category} onChange={event => setCategory(event.target.value)} aria-label="问题类别筛选">
          <option value="all">全部类别</option>{categories.map(item => <option key={item} value={item}>{categoryLabel(item)}</option>)}
        </select>
        <input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索问题、字段、文件名..." aria-label="搜索问题" />
        <span>{filtered.length} / {props.issues.length} 条</span>
      </div>
      <div className="issue-matrix-legend"><b>文件状态</b><span className="conflict"><i />冲突</span><span className="supporting"><i />一致</span><span className="evidence"><i />有证据</span><span className="missing"><i />缺失</span><span className="not-involved"><i />不参与</span></div>
      <div className="issue-matrix-table-head"><span>序号</span><span>级别</span><span>问题类别</span><span>问题描述</span><span>四文件关系</span><span>人工状态</span></div>
      {filtered.map(({ issue, evidence }, filteredIndex) => {
        const expanded = props.expandedIssues.has(issue.id)
        const severityClass = issue.severity === 'CRITICAL' ? 'critical' : issue.severity === 'WARNING' ? 'warning' : 'info'
        const selectedTrace: TraceSource | undefined = props.selectedSource?.issueId === issue.id ? issue.trace?.[props.selectedSource.srcIdx] : undefined
        const selectedValue = props.selectedSource?.issueId === issue.id
          ? Object.values(issue.sources)[props.selectedSource.srcIdx] : ''
        const statuses = Object.values(evidence).map(item => item.status)
        const involvedCount = statuses.filter(status => status !== 'not_involved').length
        const conflictCount = statuses.filter(status => status === 'conflict').length
        const missingCount = statuses.filter(status => status === 'missing').length
        return (
          <article className={`issue-matrix-item ${severityClass}`} key={issue.id}>
            <div className="issue-matrix-row">
              <div className="issue-matrix-index">{filteredIndex + 1}</div>
              <div><span className={`issue-severity ${severityClass}`}>{issue.severity === 'CRITICAL' ? '严重' : issue.severity === 'WARNING' ? '警告' : '提示'}</span></div>
              <div><span className="issue-category" title={issue.category ? `内部类型：${issue.category}` : undefined}>{categoryLabel(issue.category || '')}</span></div>
              <div className="issue-matrix-description"><strong>{issue.field_name}<em>不一致</em></strong><p>{issue.description}</p><button onClick={() => {
                const next = new Set(props.expandedIssues)
                if (next.has(issue.id)) { next.delete(issue.id); props.onSelectedSourceChange(null) } else next.add(issue.id)
                props.onExpandedChange(next)
              }}>{expanded ? '▲ 收起四文件对比' : '▼ 展开四文件对比'}</button></div>
              <div className="issue-doc-map">{DOC_ORDER.map(docType => <MatrixNode key={docType} evidence={evidence[docType]} />)}</div>
              <div><select className={`issue-status-select ${severityClass}`} value={issue.human_status || 'pending'} disabled={props.annotationLoading[issue.id]} onChange={event => props.onAnnotate(issue.id, event.target.value)} aria-label={`${issue.field_name}人工状态`}><option value="pending">待处理</option><option value="confirmed">已确认</option><option value="ignored">已忽略</option></select></div>
            </div>
            {expanded && (
              <div className="issue-matrix-detail">
                <div className="issue-matrix-detail-head"><strong>四文件证据</strong><span>{involvedCount} 份参与{conflictCount ? ` · ${conflictCount} 份冲突` : ''}{missingCount ? ` · ${missingCount} 份缺失` : ''}</span></div>
                <div className="issue-evidence-matrix">{DOC_ORDER.map(docType => <EvidenceColumn key={docType} issue={issue} evidence={evidence[docType]} selectedSource={props.selectedSource} onSelectedSourceChange={props.onSelectedSourceChange} onOpenDocument={props.onOpenDocument} />)}</div>
                <div className={`issue-decision ${severityClass}`}><div><strong>{issue.description}</strong><p>{issue.resolution || `${checkLabel(issue.check_id)}：请结合四份文件的原文证据确认处理。`}</p>{issue.category === 'standard_retrieval_uncertain' && props.standards?.length && props.setId ? <button className="issue-link-button document" onClick={() => setMappingIssue(issue)}>人工定位标准要求</button> : null}</div><span title={issue.check_id ? `内部检查编号：${issue.check_id}` : undefined}>{checkLabel(issue.check_id)}</span></div>
                {selectedTrace && <TracePanel trace={selectedTrace} sourceValue={String(selectedValue || '')} onClose={() => props.onSelectedSourceChange(null)} onViewFullDoc={props.onOpenDocument ? () => props.onOpenDocument?.(selectedTrace.doc_type as DocType) : undefined} />}
              </div>
            )}
          </article>
        )
      })}
      {filtered.length === 0 && <div className="issue-matrix-empty">没有符合当前筛选条件的问题</div>}
      {mappingIssue && props.standards?.length && props.setId && <StandardMappingModal issue={mappingIssue} standards={props.standards} setId={props.setId} onClose={() => setMappingIssue(null)} onSaved={() => props.onMappingSaved?.()} />}
    </div>
  )
}
