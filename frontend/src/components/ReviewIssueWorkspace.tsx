import { Fragment, useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  decideReviewFinding,
  getArchiveMemberInventory,
  getArchiveMemberFileUrl,
  getDocumentFileUrl,
  getEvidencePagePreviewUrl,
  fetchAuthenticatedBlob,
  openAuthenticatedResource,
} from '../api'
import type {
  DocumentSetOverview,
  ArchiveMemberInventoryItem,
  EvidenceGraphEvidence,
  EvidenceGraphSnapshot,
} from '../types'
import Icon from './Icons'
import {
  buildReviewDisplayIssues,
  crossFieldValueGroups,
  documentTypesForIssue,
  isHiddenReviewFinding,
  ISSUE_GROUPS,
  REVIEW_DOC_LABELS,
  type IssueGroupKey,
  type ReviewDisplayIssue,
} from './reviewIssuePresentation'
import './ReviewIssueWorkspace.css'

interface Props {
  setId: string
  overview: DocumentSetOverview
  snapshot: EvidenceGraphSnapshot
  onRefresh: () => Promise<void>
  onReturnRevision: () => void
}

type ResolutionCode = 'report_revision' | 'raw_record_supplement' | 'source_correction'
  | 'not_applicable' | 'false_positive' | 'deferred' | 'other'

const RESOLUTIONS: Array<{
  code: ResolutionCode
  label: string
  help: string
  decision: 'confirmed' | 'dismissed' | 'advisory' | 'unresolved'
  status: 'open' | 'not_applicable' | 'dismissed' | 'deferred'
}> = [
  { code: 'report_revision', label: '要求修改报告', help: '原始记录可信，报告内容需要修正。', decision: 'confirmed', status: 'open' },
  { code: 'raw_record_supplement', label: '要求补充原始记录', help: '计划要求执行，但缺少可追溯的原始记录。', decision: 'confirmed', status: 'open' },
  { code: 'source_correction', label: '要求核对并修正资料', help: '暂时不能确定哪份资料正确，由提交方核对后统一修正。', decision: 'confirmed', status: 'open' },
  { code: 'not_applicable', label: '本次不适用', help: '该项目不属于本次委托或执行范围，必须说明依据。', decision: 'dismissed', status: 'not_applicable' },
  { code: 'false_positive', label: '机器判断有误', help: '原文已能证明不存在此问题，请写明反证位置。', decision: 'dismissed', status: 'dismissed' },
  { code: 'deferred', label: '暂缓判断', help: '现有资料不足，先保留并等待补充信息。', decision: 'unresolved', status: 'deferred' },
]

const RESOLUTION_LABEL: Record<string, string> = Object.fromEntries(
  RESOLUTIONS.map(item => [item.code, item.label]),
)

interface CheckExecutionItem {
  check_id: string
  input_count: number
  finding_count: number
  state: 'passed' | 'issues_found' | 'not_applicable' | 'source_missing' | 'system_incomplete'
  reason_code?: string
  applicability?: 'applicable' | 'not_applicable' | 'unknown'
  expected_input_count?: number
  anchored_input_count?: number
  document_types?: string[]
}

const CHECK_LABELS: Record<string, string> = {
  'GRAPH-COVERAGE-001': '计划、原始记录与报告项目覆盖',
  'GRAPH-COVERAGE-002': '原始记录、报告与计划范围反向覆盖',
  'DOC-CROSS-FIELD-001': '跨资料关键字段一致性',
  'DOC-PLAN-REF-001': '报告引用的计划编号',
  'GRAPH-SAMPLE-001': '计划样品数与报告样品编号',
  'DOC-TIMELINE-001': '试验日期是否在报告区间内',
  'DOC-TIMELINE-002': '报告签发日期是否晚于试验',
  'DOC-TIMELINE-003': '样品接收日期是否早于试验',
  'DOC-REQUIRED-FIELD-001': '报告必填字段',
  'DOC-SIGNATURE-001': '报告签署信息',
  'DOC-PAGINATION-001': '目录与实际页数',
  'REPORT-RESULT-CONFLICT-001': '报告内部同次试验结果',
  'REPORT-SPEC-RESULT-001': '报告要求与实际结果',
  'DOC-RESULT-CONSISTENCY-001': '原始记录与报告结果',
  'DOC-REFERENCE-001': '标准编号引用',
  'STANDARD-SCOPE-ADVISORY-001': '标准明确项目范围提醒',
  'STANDARD-PLAN-COVERAGE-001': '计划引用标准的发布覆盖',
  'STANDARD-ACCEPTANCE-CONSISTENCY-001': '已发布标准与判定等级一致性',
  'STANDARD-PARAMETER-CONSISTENCY-001': '已发布标准与报告参数一致性',
  'PLAN-ACCEPTANCE-CONFLICT-001': '计划内部判定等级是否明确',
  'PLAN-CLAUSE-CONSISTENCY-001': '计划引用条款与执行说明一致性',
  'DOC-INSTRUMENT-CONSISTENCY-001': '原始记录与报告仪器清单',
  'DOC-STRUCTURE-001': '章节编号重复',
  'INSTRUMENT-IDENTITY-001': '仪器编号与名称/型号一致性',
  'RESULT-TYPE-001': '结果量纲',
  'ANOMALY-CONCLUSION-001': '异常现象与结论',
  'RESULT-DIMENSION-001': '要求条件与结果覆盖',
  'RESULT-EMPTY-001': '汇总结论与明细结果',
  'RESULT-DUPLICATE-001': '互斥测试结果重复',
  'DOC-STRUCTURE-002': '声明数量与实际条目',
  'INSTRUMENT-CAL-001': '仪器校准有效期',
  'EVIDENCE-PROFILE-001': '要求的证据制品',
  'SEMANTIC-VARIABLE-001': '变量和名称语义',
  'RESULT-FORMULA-001': '结果计算公式',
  'RESULT-LIMIT-001': '结果与限值',
  'GRAPH-CONCLUSION-001': '总结果与单项结论',
}

function checkExecutionFrom(snapshot: EvidenceGraphSnapshot): CheckExecutionItem[] {
  const keys = [
    'coverage_check_execution',
    'document_check_execution',
    'general_check_execution',
    'standard_check_execution',
  ]
  const hiddenFindingCounts = snapshot.findings.reduce((counts, finding) => {
    if (isHiddenReviewFinding(finding)) {
      counts.set(finding.check_id, (counts.get(finding.check_id) || 0) + 1)
    }
    return counts
  }, new Map<string, number>())
  return keys.flatMap(key => {
    const value = snapshot.run.metadata[key]
    if (!Array.isArray(value)) return []
    return value.filter((item): item is CheckExecutionItem => Boolean(item)
      && typeof item === 'object'
      && typeof item.check_id === 'string'
      && typeof item.input_count === 'number'
      && typeof item.finding_count === 'number'
      && ['passed', 'issues_found', 'not_applicable', 'source_missing', 'system_incomplete'].includes(String(item.state)))
      .map(item => {
        const findingCount = Math.max(0, item.finding_count - (hiddenFindingCounts.get(item.check_id) || 0))
        return {
          ...item,
          finding_count: findingCount,
          state: item.state === 'issues_found' && findingCount === 0 ? 'passed' : item.state,
        }
      })
  })
}

function locationText(item: EvidenceGraphEvidence) {
  if (item.page_number) return `文件第 ${item.page_number} 页`
  return [item.sheet_name, item.cell_range].filter(Boolean).join(' · ') || '文档内已定位'
}

function memberIndex(item: EvidenceGraphEvidence): number | null {
  const match = String(item.metadata?.unit_id || '').match(/member-(\d+)/)
  return match ? Number(match[1]) : null
}

function rawDisplayName(meta: ArchiveMemberInventoryItem | undefined, fallback: string) {
  if (!meta) return fallback
  const readable = [meta.test_item_name, meta.sample_id || meta.sequence, meta.test_mode].filter(Boolean).join(' · ')
  return readable || meta.source_filename || fallback
}

function verdictLabel(verdict: string) {
  if (verdict === 'fail') return '不通过'
  if (verdict === 'pass') return '通过'
  return verdict
}

function EvidenceQuote({ item, index }: { item: EvidenceGraphEvidence; index: number }) {
  const quote = item.exact_quote || '已定位到结构化原文'
  const verdict = String(item.metadata?.verdict || '')
  const roleLabel = textValue(item.metadata?.role_label)
  const parts = quote.split(/(不\s*Pass|Fail|不符合|不合格|Pass|符合|合格|通过)/gi)
  return <div className="ri-evidence-quote">
    <div className="ri-evidence-quote-head">
      <span>{roleLabel || `证据 ${index + 1}`}<em> · {locationText(item)}</em></span>
      {verdict && <b className={verdict === 'fail' ? 'fail' : verdict === 'pass' ? 'pass' : ''}>
        原文判定：{verdictLabel(verdict)}
      </b>}
    </div>
    <blockquote>{parts.map((part, partIndex) => {
      const compact = part.replace(/\s+/g, '').toLowerCase()
      const tokenVerdict = /^(不pass|fail|不符合|不合格)$/.test(compact)
        ? 'fail'
        : /^(pass|符合|合格|通过)$/.test(compact) ? 'pass' : ''
      return tokenVerdict
        ? <mark className={tokenVerdict} key={`${partIndex}-${part}`}>{part}</mark>
        : <Fragment key={`${partIndex}-${part}`}>{part}</Fragment>
    })}</blockquote>
  </div>
}

interface ResultComparisonRow {
  key: string
  docType: string
  location: string
  testItem: string
  sampleId: string
  mode: string
  specRequirement: string
  duration: string
  requiredLevel: string
  actualLevel: string
  verdictRaw: string
  verdict: string
}

interface ParameterComparisonEntry {
  key: string
  role: 'declared' | 'observed' | 'corroborating'
  roleLabel: string
  parameterName: string
  value: string
  sourceLabel: string
  filename: string
  location: string
  quote: string
  openUrl: string
}

interface InstrumentComparisonRow {
  role: string
  docType: string
  manufacturer: string
  model: string
  serialNo: string
  calibrationEnd: string
}

function textValue(value: unknown) {
  return typeof value === 'string' || typeof value === 'number' ? String(value).trim() : ''
}

function classifyVerdict(value: string) {
  const compact = value.replace(/\s+/g, '').toLocaleLowerCase('zh-CN')
  if (/不pass|fail|不符合|不合格/.test(compact)) return 'fail'
  if (/^(pass|符合|合格|通过)$/.test(compact)) return 'pass'
  return ''
}

function resultContext(issue: ReviewDisplayIssue) {
  const description = issue.findings.map(item => item.description).join(' ')
  return {
    sampleId: description.match(/样品\s+([^、，：:\s]+)/)?.[1] || '',
    mode: description.match(/模式\s+([^、，。：:\s]+)/)?.[1] || '',
  }
}

function resultRowsForIssue(issue: ReviewDisplayIssue): ResultComparisonRow[] {
  const context = resultContext(issue)
  const rows: ResultComparisonRow[] = []
  issue.evidence.forEach((evidence, evidenceIndex) => {
    const baseLocation = evidence.doc_type === 'original_records' && memberIndex(evidence)
      ? `ZIP 第 ${memberIndex(evidence)} 份 · ${locationText(evidence)}`
      : locationText(evidence)
    const structured = Array.isArray(evidence.metadata?.comparison_rows)
      ? evidence.metadata.comparison_rows.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
      : []
    if (structured.length) {
      structured.forEach((row, rowIndex) => rows.push({
        key: `${evidence.evidence_id}:structured:${rowIndex}`,
        docType: textValue(row.doc_type) || evidence.doc_type,
        location: structured.length > 1 ? `${baseLocation} · 第 ${rowIndex + 1} 处` : baseLocation,
        testItem: textValue(row.test_item) || issue.subject,
        sampleId: textValue(row.sample_id) || context.sampleId,
        mode: textValue(row.mode) || context.mode,
        specRequirement: textValue(row.spec_requirement),
        duration: textValue(row.test_duration),
        requiredLevel: textValue(row.required_level),
        actualLevel: textValue(row.actual_level),
        verdictRaw: textValue(row.verdict_raw),
        verdict: textValue(row.verdict) || classifyVerdict(textValue(row.verdict_raw)),
      }))
      return
    }

    const matches = Array.from(evidence.exact_quote.matchAll(
      /(\d+(?:\.\d+)?s)\s+([A-Z])\s+(\S+)\s+(不\s*Pass|Pass|Fail|不符合|符合|不合格|合格|通过)/gi,
    ))
    const expectedVerdict = textValue(evidence.metadata?.verdict)
    const match = matches.find(candidate => classifyVerdict(candidate[4]) === expectedVerdict)
      || matches[matches.length - 1]
    if (!match) return
    const sampleId = evidence.exact_quote.match(/E\d{12,}-\d+/i)?.[0] || context.sampleId
    const mode = evidence.exact_quote.match(/Mode\s*\d+/i)?.[0] || context.mode
    const specRequirement = evidence.exact_quote.match(/(?:反向电压|Reversed\s+voltage)\s*[：:]\s*([^\n]+)/i)?.[1]?.trim()
      || evidence.exact_quote.match(/\b\d+(?:\.\d+)?\s*V\b/i)?.[0] || ''
    rows.push({
      key: `${evidence.evidence_id}:fallback:${evidenceIndex}`,
      docType: evidence.doc_type,
      location: baseLocation,
      testItem: issue.subject,
      sampleId,
      mode,
      specRequirement,
      duration: match[1],
      requiredLevel: match[2],
      actualLevel: match[3],
      verdictRaw: match[4].replace(/\s+/g, ' '),
      verdict: classifyVerdict(match[4]),
    })
  })
  return Array.from(new Map(rows.map(row => [
    [row.docType, row.sampleId, row.mode, row.specRequirement, row.duration, row.requiredLevel, row.actualLevel, row.verdict].join('|').toLocaleLowerCase('zh-CN'),
    row,
  ])).values()).sort((left, right) => {
    const documentRank = (value: string) => value === 'original_records' ? 0 : value === 'final_report' ? 1 : 2
    return documentRank(left.docType) - documentRank(right.docType)
      || (left.verdict === 'fail' ? 1 : 0) - (right.verdict === 'fail' ? 1 : 0)
  })
}

function parameterComparisonForIssue(
  issue: ReviewDisplayIssue,
  setId: string,
  overview: DocumentSetOverview,
): ParameterComparisonEntry[] {
  const finding = issue.findings.find(item => item.check_id === 'REPORT-SPEC-RESULT-001')
  if (!finding) return []
  const fallbackParameterName = textValue(finding.metadata?.parameter_name) || issue.subject
  return issue.evidence.flatMap(evidence => {
    const role = textValue(evidence.metadata?.role)
    if (role !== 'declared' && role !== 'observed' && role !== 'corroborating') return []
    const comparisonRole: ParameterComparisonEntry['role'] = role
    const document = overview.files.find(file => file.doc_id === evidence.doc_id)
      || overview.files.find(file => file.doc_type === evidence.doc_type)
    const sourceSection = textValue(evidence.metadata?.source_section)
    return [{
      key: evidence.evidence_id,
      role: comparisonRole,
      roleLabel: textValue(evidence.metadata?.role_label)
        || (role === 'declared' ? '规范要求'
          : role === 'observed' ? '结果记录实际采用' : '对应原始记录实际执行值'),
      parameterName: textValue(evidence.metadata?.parameter_name) || fallbackParameterName,
      value: textValue(evidence.metadata?.comparison_value) || evidence.exact_quote,
      sourceLabel: sourceSection === 'test_specification'
        ? '检测报告 · 测试规范'
        : sourceSection === 'result_table' ? '检测报告 · 结果记录'
          : sourceSection === 'original_record_result' ? '原始记录 · 试验数据表'
            : evidence.doc_type === 'original_records' ? '原始记录' : '检测报告',
      filename: evidence.filename || document?.filename || '检测报告',
      location: locationText(evidence),
      quote: evidence.exact_quote || '已定位到结构化原文',
      openUrl: document ? getDocumentFileUrl(setId, document.doc_id) : '',
    }]
  }).sort((left, right) => {
    const rank = (role: ParameterComparisonEntry['role']) => role === 'declared' ? 0 : role === 'observed' ? 1 : 2
    return rank(left.role) - rank(right.role)
  })
}

function ResultEvidenceComparison({ issue, rows }: { issue: ReviewDisplayIssue; rows: ResultComparisonRow[] }) {
  const reportRows = rows.filter(row => row.docType === 'final_report')
  const hasReportConflict = new Set(reportRows.map(row => row.verdict).filter(Boolean)).size > 1
  const isPerformanceLevelCheck = issue.findings.some(finding => finding.check_id === 'REPORT-SPEC-RESULT-001')
  const actualIsBetter = issue.findings.some(finding => finding.metadata?.actual_is_better === true)
  const perDocumentIndex = new Map<string, number>()
  return <section className="ri-result-comparison" aria-label="试验结果结构化对比">
    <div className="ri-result-alert">
      <b>{isPerformanceLevelCheck
        ? actualIsBetter ? '实际性能等级优于要求，允许通过但需知悉' : '实际性能等级低于要求等级'
        : hasReportConflict ? '检测报告有多条结果，且结论互相冲突' : '不同资料中的试验结果不一致'}</b>
      <span>{isPerformanceLevelCheck
        ? '直接比较 Performance criteria 与 Actual performance，并附对应原始记录'
        : hasReportConflict ? '先核对报告内部记录，再与原始记录比较' : '只展示参与本次判断的字段'}</span>
    </div>
    <div className="ri-result-table-wrap"><table className="ri-result-table">
      <thead><tr><th>资料 / 位置</th><th>测试身份</th><th>测试条件</th><th>时长</th><th>要求等级</th><th>实际等级</th><th>原文结果</th></tr></thead>
      <tbody>{rows.map(row => {
        const nextIndex = (perDocumentIndex.get(row.docType) || 0) + 1
        perDocumentIndex.set(row.docType, nextIndex)
        const label = row.docType === 'original_records' ? '原始记录' : row.docType === 'final_report' ? `报告记录 ${nextIndex}` : REVIEW_DOC_LABELS[row.docType] || row.docType
        return <tr className={row.verdict === 'fail' ? 'fail' : 'pass'} key={row.key}>
          <td><b>{label}</b><small>{row.location}</small></td>
          <td><b>{row.testItem || issue.subject}</b><small>{[row.sampleId, row.mode].filter(Boolean).join(' · ')}</small></td>
          <td>{row.specRequirement || '—'}</td><td>{row.duration || '—'}</td><td>{row.requiredLevel || '—'}</td><td>{row.actualLevel || '—'}</td>
          <td><span className={`ri-result-pill ${row.verdict}`}>{row.verdict === 'fail' ? '! ' : '✓ '}{row.verdictRaw || verdictLabel(row.verdict)}</span></td>
        </tr>
      })}</tbody>
    </table></div>
  </section>
}

function InstrumentEvidenceComparison({ issue }: { issue: ReviewDisplayIssue }) {
  const rawRows = issue.findings.flatMap(finding => (
    Array.isArray(finding.metadata?.comparison_rows)
      ? finding.metadata.comparison_rows : []
  )).filter((row): row is Record<string, unknown> => Boolean(row) && typeof row === 'object')
  const rows: InstrumentComparisonRow[] = Array.from(new Map(rawRows.map(row => {
    const item = {
      role: textValue(row.role), docType: textValue(row.doc_type),
      manufacturer: textValue(row.manufacturer), model: textValue(row.model),
      serialNo: textValue(row.serial_no), calibrationEnd: textValue(row.calibration_end),
    }
    return [`${item.docType}|${item.manufacturer}|${item.model}|${item.serialNo}`, item]
  })).values())
  return <section className="ri-result-comparison" aria-label="仪器清单结构化对比">
    <div className="ri-result-alert"><b>系统比较了两份资料中的完整物理仪器清单</b><span>以制造商、型号和序列号作为同一台仪器的身份</span></div>
    <div className="ri-result-table-wrap"><table className="ri-result-table ri-instrument-table">
      <thead><tr><th>资料 / 差异</th><th>制造商</th><th>型号</th><th>序列号</th><th>校准有效期</th></tr></thead>
      <tbody>{rows.map((row, index) => <tr className={row.role === 'raw_inventory' ? 'pass' : 'fail'} key={`${row.docType}:${row.serialNo}:${index}`}>
        <td><b>{row.docType === 'final_report' ? '检测报告' : '原始记录'}</b><small>{row.role === 'report_only' ? '仅报告中出现' : row.role === 'raw_only' ? '仅原始记录中出现' : '原始记录已有仪器'}</small></td>
        <td>{row.manufacturer || '—'}</td><td>{row.model || '—'}</td><td>{row.serialNo || '—'}</td><td>{row.calibrationEnd || '—'}</td>
      </tr>)}</tbody>
    </table></div>
  </section>
}

function ParameterEvidenceComparison({ entries }: { entries: ParameterComparisonEntry[] }) {
  const declared = entries.filter(item => item.role === 'declared')
  const observed = entries.filter(item => item.role === 'observed')
  const corroborating = entries.filter(item => item.role === 'corroborating')
  const parameterName = entries[0]?.parameterName || '同一参数'
  const renderSide = (items: ParameterComparisonEntry[], role: 'declared' | 'observed') => <div className={`ri-parameter-side ${role}`}>
    <div className="ri-parameter-side-head">
      <div><small>{role === 'declared' ? '应当按什么执行' : '结果记录实际写了什么'}</small><b>{items[0]?.roleLabel || (role === 'declared' ? '规范要求' : '结果记录实际采用')}</b></div>
      <span>{items.length > 1 ? `${items.length} 处写法` : '1 处原文'}</span>
    </div>
    {items.map(item => <article key={item.key}>
      <strong>{item.value || '未提取到参数值'}</strong>
      <div className="ri-parameter-origin"><b>{item.sourceLabel}</b><span>{item.filename} · {item.location}</span></div>
      <blockquote><small>原文</small>{item.quote}</blockquote>
      {item.openUrl && <a href={item.openUrl} onClick={event => { event.preventDefault(); void openAuthenticatedResource(item.openUrl) }}>打开原文件并核对此页</a>}
    </article>)}
  </div>

  return <section className="ri-parameter-comparison" aria-label={`${parameterName}规范值与结果值对比`}>
    <header>
      <div><small>系统比较的字段</small><h3>{parameterName}</h3></div>
      <p>先比较报告“测试规范”与“结果记录”中的同名参数；若原始记录可可靠匹配，会同时展示实际执行值作佐证。</p>
    </header>
    <div className="ri-parameter-sides">
      {renderSide(declared, 'declared')}
      <div className="ri-parameter-mismatch" aria-label="两侧数值不一致"><b>≠</b><span>数值不一致</span></div>
      {renderSide(observed, 'observed')}
    </div>
    {!!corroborating.length && <div className="ri-parameter-corroborating">
      <div><small>交叉核对</small><b>对应原始记录实际执行值</b><span>同一测试项目且数值/单位可精确回溯</span></div>
      {corroborating.map(item => <article key={item.key}>
        <strong>{item.value}</strong>
        <p>{item.filename} · {item.location}</p>
        <blockquote>{item.quote}</blockquote>
        {item.openUrl && <a href={item.openUrl} onClick={event => { event.preventDefault(); void openAuthenticatedResource(item.openUrl) }}>打开原始记录</a>}
      </article>)}
    </div>}
    <footer><b>需要人工确认</b><span>确认结果记录应按规范要求修改，还是测试规范本身需要修订；系统不会自行认定哪一侧正确。</span></footer>
  </section>
}

function issueDecision(snapshot: EvidenceGraphSnapshot, issue: ReviewDisplayIssue) {
  const decisions = issue.findings.map(finding => snapshot.decisions.find(item => item.finding_id === finding.finding_id))
  return decisions.every(Boolean) ? decisions[0] : undefined
}

function EvidenceLightbox({
  src, alt, filename, location, onClose,
}: {
  src: string
  alt: string
  filename: string
  location: string
  onClose: () => void
}) {
  const [zoom, setZoom] = useState(100)
  useEffect(() => {
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
      if (event.key === '+' || event.key === '=') setZoom(value => Math.min(300, value + 25))
      if (event.key === '-') setZoom(value => Math.max(50, value - 25))
      if (event.key === '0') setZoom(100)
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [onClose])

  return createPortal(<div
    className="ri-evidence-lightbox"
    onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}
  >
    <section role="dialog" aria-modal="true" aria-labelledby="ri-evidence-lightbox-title">
      <header>
        <div className="ri-lightbox-source-mark"><Icon name="file" size={18} /></div>
        <div>
          <small>证据原文</small>
          <h2 id="ri-evidence-lightbox-title">{filename}</h2>
          <p>{location} · 黄色区域为系统定位到的判断依据</p>
        </div>
        <button autoFocus aria-label="关闭大图" title="关闭（Esc）" onClick={onClose}><Icon name="close" size={20} /></button>
      </header>
      <div className="ri-lightbox-canvas">
        <img src={src} alt={alt} style={{ width: `${zoom}%` }} />
      </div>
      <footer>
        <span>滚动查看文档细节；键盘 ＋ / − 缩放，Esc 关闭</span>
        <div>
          <button onClick={() => setZoom(100)}>适合窗口</button>
          <button aria-label="缩小大图" disabled={zoom <= 50} onClick={() => setZoom(value => Math.max(50, value - 25))}>−</button>
          <b>{zoom}%</b>
          <button aria-label="放大大图" disabled={zoom >= 300} onClick={() => setZoom(value => Math.min(300, value + 25))}>＋</button>
        </div>
      </footer>
    </section>
  </div>, document.body)
}

function SourceCard({
  setId, graphId, docType, evidence, evidenceTotal, overview, missing, rawMetas, rawIdentityMember, conflict, comparison,
}: {
  setId: string
  graphId: string
  docType: string
  evidence: EvidenceGraphEvidence[]
  evidenceTotal?: number
  overview: DocumentSetOverview
  missing: boolean
  rawMetas: ArchiveMemberInventoryItem[] | null
  rawIdentityMember?: number
  conflict?: boolean
  comparison?: boolean
}) {
  const document = overview.files.find(file => file.doc_type === docType)
  const first = evidence[0]
  const [selectedEvidenceId, setSelectedEvidenceId] = useState(first?.evidence_id || '')
  const [previewAttempt, setPreviewAttempt] = useState(0)
  const [previewState, setPreviewState] = useState<{ url: string; status: 'loaded' | 'failed' }>({ url: '', status: 'failed' })
  const [previewObjectUrl, setPreviewObjectUrl] = useState('')
  const [zoom, setZoom] = useState(100)
  const [lightboxOpen, setLightboxOpen] = useState(false)
  const evidenceKey = evidence.map(item => item.evidence_id).join('|')
  useEffect(() => {
    if (!evidence.some(item => item.evidence_id === selectedEvidenceId)) {
      setSelectedEvidenceId(evidence[0]?.evidence_id || '')
    }
  }, [evidenceKey, selectedEvidenceId])
  const selectedEvidence = evidence.find(item => item.evidence_id === selectedEvidenceId) || first
  const selectedMember = selectedEvidence ? memberIndex(selectedEvidence) : null
  const firstMember = selectedMember || rawIdentityMember
  const openUrl = !missing && docType === 'original_records' && firstMember && document
    ? getArchiveMemberFileUrl(setId, document.doc_id, firstMember)
    : document ? getDocumentFileUrl(setId, document.doc_id) : ''
  const filename = !missing && docType === 'original_records' && firstMember
    ? rawDisplayName(rawMetas?.find(meta => meta.member_index === firstMember), selectedEvidence?.filename || document?.filename || '')
    : missing ? document?.filename || '未上传' : selectedEvidence?.filename || document?.filename || '未上传'
  const verdicts = Array.from(new Set(evidence.map(item => String(item.metadata?.verdict || '')).filter(Boolean)))
  const previewUrl = selectedEvidence?.page_number
    ? getEvidencePagePreviewUrl(setId, graphId, selectedEvidence.evidence_id) : ''
  const previewRequestUrl = previewUrl
    ? `${previewUrl}${previewUrl.includes('?') ? '&' : '?'}preview_attempt=${previewAttempt}`
    : ''
  const previewStatus = previewRequestUrl && previewState.url === previewRequestUrl
    ? previewState.status
    : 'loading'
  const previewFailed = previewStatus === 'failed'
  const previewLoaded = previewStatus === 'loaded'
  useEffect(() => {
    if (!previewRequestUrl) { setPreviewObjectUrl(''); return undefined }
    let active = true
    let objectUrl = ''
    setPreviewState({ url: previewRequestUrl, status: 'failed' })
    fetchAuthenticatedBlob(previewRequestUrl).then(blob => {
      if (!active) return
      objectUrl = URL.createObjectURL(blob)
      setPreviewObjectUrl(objectUrl)
      setPreviewState({ url: previewRequestUrl, status: 'loaded' })
    }).catch(() => {
      if (active) setPreviewState({ url: previewRequestUrl, status: 'failed' })
    })
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [previewRequestUrl])
  const selectEvidence = (evidenceId: string) => {
    setLightboxOpen(false)
    setSelectedEvidenceId(evidenceId)
    setPreviewAttempt(0)
    setZoom(100)
  }
  return <article className={`ri-source-card ${missing ? 'missing' : conflict ? 'conflict' : comparison ? 'comparison' : evidence.length ? 'found' : 'neutral'}`}>
    <header>
      <div><small>{REVIEW_DOC_LABELS[docType] || docType}</small><b>{missing ? '未找到对应项' : conflict ? '发现矛盾内容' : comparison ? '参与本次对比' : evidence.length ? '找到原文证据' : '本项未引用'}</b></div>
      {openUrl && <a href={openUrl} onClick={event => { event.preventDefault(); void openAuthenticatedResource(openUrl) }}>打开原文件</a>}
    </header>
    <div className="ri-source-file"><Icon name="file" size={15} /><span>{filename}</span></div>
    {!missing && selectedEvidence && <div className="ri-source-location">{locationText(selectedEvidence)}</div>}
    {!!verdicts.length && <div className="ri-verdicts">{verdicts.map(verdict => <span className={verdict === 'fail' ? 'fail' : 'pass'} key={verdict}>{verdictLabel(verdict)}</span>)}</div>}
    {missing ? <p className="ri-missing-copy">已完成文件清点、测试项名称和已确认别名匹配，仍未找到能证明本项目已执行或已发布的原文。</p>
      : evidence.length && selectedEvidence ? <div className="ri-visual-evidence">
        {evidence.length > 1 && <div className="ri-visual-tabs" role="tablist" aria-label={`${REVIEW_DOC_LABELS[docType] || docType}证据位置`}>
          {evidence.map((item, index) => <button
            className={item.evidence_id === selectedEvidence.evidence_id ? 'active' : ''}
            key={item.evidence_id}
            onClick={() => selectEvidence(item.evidence_id)}
            role="tab"
            aria-selected={item.evidence_id === selectedEvidence.evidence_id}
          ><b>{textValue(item.metadata?.role_label) || `证据 ${index + 1}`}</b><span>{memberIndex(item) ? `第 ${memberIndex(item)} 份 · ` : ''}{locationText(item)}</span></button>)}
        </div>}
        {Boolean(evidenceTotal && evidenceTotal > evidence.length) && <p className="ri-evidence-omitted">
          共 {evidenceTotal} 条来源证据；为便于阅读，这里展示 {evidence.length} 条代表证据，完整数量差异见上方对比。
        </p>}
        <div className="ri-visual-toolbar">
          <div className="ri-visual-title"><b>视觉原文</b></div>
          <div className="ri-visual-controls">
            <div><button aria-label="缩小证据页" disabled={zoom <= 60} onClick={() => setZoom(value => Math.max(60, value - 20))}>−</button><span>{zoom}%</span><button aria-label="放大证据页" disabled={zoom >= 180} onClick={() => setZoom(value => Math.min(180, value + 20))}>＋</button></div>
            <button className="ri-expand-preview" disabled={!previewLoaded} onClick={() => setLightboxOpen(true)}><Icon name="expand" size={13} />展开大图</button>
          </div>
        </div>
        {previewRequestUrl && !previewFailed ? <div className={`ri-visual-canvas ${previewLoaded ? 'loaded' : 'loading'}`}>
          {!previewLoaded && <div className="ri-visual-loading"><span />正在载入证据页…</div>}
          <div
            className="ri-visual-image-wrap"
            style={{ width: `${zoom}%` }}
            role={previewLoaded ? 'button' : undefined}
            tabIndex={previewLoaded ? 0 : -1}
            aria-label={previewLoaded ? `展开查看${filename}${locationText(selectedEvidence)}大图` : undefined}
            onClick={() => { if (previewLoaded) setLightboxOpen(true) }}
            onKeyDown={event => {
              if (previewLoaded && (event.key === 'Enter' || event.key === ' ')) {
                event.preventDefault()
                setLightboxOpen(true)
              }
            }}
          >
            <img
            key={previewRequestUrl}
            src={previewObjectUrl}
            alt={`${filename} ${locationText(selectedEvidence)}原文页面`}
            onError={() => setPreviewState({ url: previewRequestUrl, status: 'failed' })}
            />
            {previewLoaded && <span className="ri-visual-open-hint"><Icon name="expand" size={14} />点击查看大图</span>}
          </div>
        </div> : <div className="ri-visual-fallback">
          <b>{previewUrl ? '本页视觉预览载入失败' : '这条证据没有页面图像定位'}</b>
          <span>下方仍保留审核快照中的原文，避免证据内容丢失。</span>
          {previewUrl && <button type="button" onClick={() => setPreviewAttempt(value => value + 1)}>重新载入本页</button>}
        </div>}
        <div className="ri-visual-quote"><small>审核快照中的提取原文</small><EvidenceQuote item={selectedEvidence} index={evidence.indexOf(selectedEvidence)} /></div>
        {lightboxOpen && previewLoaded && <EvidenceLightbox
          src={previewObjectUrl}
          alt={`${filename} ${locationText(selectedEvidence)}放大原文页面`}
          filename={filename}
          location={locationText(selectedEvidence)}
          onClose={() => setLightboxOpen(false)}
        />}
      </div>
        : <p className="ri-missing-copy">这份文档不参与当前问题的判断。</p>}
  </article>
}

function CrossDocumentComparison({ issue }: { issue: ReviewDisplayIssue }) {
  const fieldFinding = issue.findings.find(finding => finding.check_id === 'DOC-CROSS-FIELD-001')
  if (!fieldFinding) return null
  const valueGroups = crossFieldValueGroups(fieldFinding)
  const useSetSummary = valueGroups.reduce((sum, item) => sum + item.values.length, 0) > 12
  const byDocument = documentTypesForIssue(issue).map(docType => ({
    docType,
    evidence: issue.evidence.filter(item => item.doc_type === docType),
    valueGroup: valueGroups.find(item => item.docType === docType),
  })).filter(item => item.evidence.length)
  if (byDocument.length < 2) return null

  return <section className="ri-comparison" aria-label={`${issue.subject}跨文档对比`}>
    <header><div><small>正在对比</small><h3>{issue.subject}</h3></div><p>以下资料记录的值不同，系统暂时无法确认是否表达同一内容。</p></header>
    <div className="ri-comparison-values">
      {byDocument.map((item, index) => <Fragment key={item.docType}>
        {index > 0 && <span className="ri-not-equal" aria-label="不一致">≠</span>}
        <article>
          <span>{REVIEW_DOC_LABELS[item.docType] || item.docType}</span>
          {useSetSummary && item.valueGroup ? <>
            <b className="ri-comparison-count">共 {item.valueGroup.values.length} 个</b>
            <blockquote>{item.valueGroup.differingValues.length
              ? `其中 ${item.valueGroup.differingValues.length} 个未在所有资料中共同出现`
              : '全部包含在各资料的共同集合中'}</blockquote>
            <small>共同值 {item.valueGroup.commonValues.length} 个；下方提供代表证据溯源</small>
          </> : <>
            {item.evidence.map(evidence => <blockquote key={evidence.evidence_id}>{evidence.exact_quote || '未提取到原值'}</blockquote>)}
            <small>{item.evidence.map(evidence => locationText(evidence)).join('、')}</small>
          </>}
        </article>
      </Fragment>)}
    </div>
  </section>
}

export default function ReviewIssueWorkspace({ setId, overview, snapshot, onRefresh, onReturnRevision }: Props) {
  const issues = useMemo(() => buildReviewDisplayIssues(snapshot), [snapshot])
  const [filter, setFilter] = useState<'attention' | 'all' | 'complete'>('attention')
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [decisionIssue, setDecisionIssue] = useState<ReviewDisplayIssue | null>(null)
  const [resolutionCode, setResolutionCode] = useState<ResolutionCode>('report_revision')
  const [comment, setComment] = useState('')
  const [saving, setSaving] = useState(false)
  const [localError, setLocalError] = useState('')
  const [rawMetas, setRawMetas] = useState<ArchiveMemberInventoryItem[] | null>(null)
  const [inventoryOpen, setInventoryOpen] = useState(false)
  const [inventoryQuery, setInventoryQuery] = useState('')
  const [returnConfirmOpen, setReturnConfirmOpen] = useState(false)
  const [evidenceView, setEvidenceView] = useState<'structured' | 'raw'>('structured')

  const visible = useMemo(() => issues.filter(issue => {
    if (filter === 'attention' && issue.group === 'complete') return false
    if (filter === 'complete' && issue.group !== 'complete') return false
    const text = `${issue.subject} ${issue.title} ${issue.summary}`.toLocaleLowerCase('zh-CN')
    return !query.trim() || text.includes(query.trim().toLocaleLowerCase('zh-CN'))
  }), [issues, filter, query])
  const selected = visible.find(issue => issue.id === selectedId) || visible[0] || issues[0]

  useEffect(() => {
    if (selected && selected.id !== selectedId) setSelectedId(selected.id)
  }, [selected, selectedId])

  useEffect(() => setEvidenceView('structured'), [selected?.id])

  useEffect(() => {
    if (!selected?.evidence.some(item => item.doc_type === 'original_records' && memberIndex(item))) return
    if (rawMetas) return
    const raw = overview.files.find(file => file.doc_type === 'original_records')
    if (!raw) return
    getArchiveMemberInventory(setId, raw.doc_id)
      .then(data => setRawMetas(data.members))
      .catch(() => setRawMetas([]))
  }, [selected, rawMetas, overview.files, setId])

  const attentionIssues = issues.filter(issue => issue.group !== 'complete')
  const completeIssues = issues.filter(issue => issue.group === 'complete')
  const checkExecution = checkExecutionFrom(snapshot)
  const checkPassedCount = checkExecution.filter(item => item.state === 'passed').length
  const checkIssueCount = checkExecution.filter(item => item.state === 'issues_found').length
  const checkNotApplicableCount = checkExecution.filter(item => item.state === 'not_applicable').length
  const checkSourceMissingCount = checkExecution.filter(item => item.state === 'source_missing').length
  const checkSystemIncompleteCount = checkExecution.filter(item => item.state === 'system_incomplete').length
  const bridgeMetadata = snapshot.run.metadata.structured_check_bridge
  const bridgeFailureCount = bridgeMetadata && typeof bridgeMetadata === 'object'
    ? Number((bridgeMetadata as Record<string, unknown>).failed_result_count || 0)
    : 0
  const decidedCount = attentionIssues.filter(issue => issueDecision(snapshot, issue)).length
  const allDecided = attentionIssues.length > 0 && decidedCount === attentionIssues.length
  const revisionIssues = attentionIssues.filter(issue => {
    const decision = issueDecision(snapshot, issue)
    return decision && ['report_revision', 'raw_record_supplement', 'source_correction', 'other'].includes(decision.resolution_code)
  })

  const grouped = ISSUE_GROUPS.map(group => ({
    ...group,
    issues: visible.filter(issue => issue.group === group.key),
  })).filter(group => group.issues.length)

  async function openInventory() {
    setInventoryOpen(true)
    if (rawMetas) return
    const raw = overview.files.find(file => file.doc_type === 'original_records')
    if (!raw) return
    try {
      const inventory = await getArchiveMemberInventory(setId, raw.doc_id)
      setRawMetas(inventory.members)
    } catch {
      setRawMetas([])
    }
  }

  function beginDecision(issue: ReviewDisplayIssue) {
    const existing = issueDecision(snapshot, issue)
    const existingCode = existing?.resolution_code as ResolutionCode
    setResolutionCode(RESOLUTIONS.some(item => item.code === existingCode) ? existingCode : (
      issue.group === 'missing' ? 'raw_record_supplement' : issue.group === 'confirm' ? 'deferred' : 'report_revision'
    ))
    setComment(existing?.comment || '')
    setDecisionIssue(issue)
    setLocalError('')
  }

  async function saveDecision() {
    if (!decisionIssue || !comment.trim()) {
      setLocalError('请填写判断依据；该内容会进入审核记录。')
      return
    }
    const option = RESOLUTIONS.find(item => item.code === resolutionCode)
    if (!option) return
    setSaving(true)
    setLocalError('')
    try {
      await Promise.all(decisionIssue.findings.map(finding => decideReviewFinding(
        setId, snapshot.run.graph_id, finding.finding_id,
        option.decision, comment.trim(), option.code, option.status,
      )))
      setDecisionIssue(null)
      await onRefresh()
    } catch (error: any) {
      setLocalError(error?.response?.data?.detail || error.message || '保存失败，请稍后重试')
    } finally {
      setSaving(false)
    }
  }

  const rawDocument = overview.files.find(file => file.doc_type === 'original_records')
  const selectedContextNode = selected?.findings.flatMap(finding => finding.subject_node_ids)
    .map(id => snapshot.nodes.find(node => node.node_id === id))
    .find(node => node?.properties?.sample_id && node?.properties?.mode)
  const normalizeIdentity = (value: unknown) => String(value || '').toLocaleLowerCase('zh-CN').replace(/[^a-z0-9\u4e00-\u9fff]/g, '')
  const selectedRawMember = rawMetas?.find(meta => (
    normalizeIdentity(meta.sample_id) === normalizeIdentity(selectedContextNode?.properties?.sample_id)
    && normalizeIdentity(meta.test_mode) === normalizeIdentity(selectedContextNode?.properties?.mode)
    && (!selected?.subject || normalizeIdentity(meta.test_item_name).includes(normalizeIdentity(selected.subject)))
  ))?.member_index
  const filteredRawMetas = (rawMetas || []).map(meta => ({ meta, index: meta.member_index }))
    .filter(({ meta }) => !inventoryQuery.trim() || rawDisplayName(meta, '').toLocaleLowerCase('zh-CN')
      .includes(inventoryQuery.trim().toLocaleLowerCase('zh-CN')))
  const selectedResultRows = selected ? resultRowsForIssue(selected) : []
  const selectedParameterEntries = selected ? parameterComparisonForIssue(selected, setId, overview) : []
  const showStructuredResult = Boolean(selected
    && selectedResultRows.length > 1
    && selected.findings.some(finding => ['DOC-RESULT-CONSISTENCY-001', 'REPORT-RESULT-CONFLICT-001', 'REPORT-SPEC-RESULT-001', 'STANDARD-ACCEPTANCE-CONSISTENCY-001'].includes(finding.check_id)))
  const showStructuredParameter = Boolean(selected
    && selectedParameterEntries.some(item => item.role === 'declared')
    && selectedParameterEntries.some(item => item.role === 'observed'))
  const showStructuredInstrument = Boolean(selected
    && selected.findings.some(finding => finding.check_id === 'DOC-INSTRUMENT-CONSISTENCY-001')
    && selected.findings.some(finding => Array.isArray(finding.metadata?.comparison_rows)))
  const showStructuredEvidence = showStructuredResult || showStructuredParameter || showStructuredInstrument

  return <section className="review-issue-shell">
    <header className="ri-topbar">
      <div><h2>审核问题</h2><p>逐条确认处理方式；全部完成后统一退回修订</p></div>
      <div className="ri-summary">
        <div className="danger"><span>需要处理</span><b>{attentionIssues.filter(issue => issue.severity === 'error').length}</b></div>
        <div className="warning"><span>需要确认</span><b>{attentionIssues.filter(issue => issue.severity !== 'error').length}</b></div>
        <div className="success"><span>证据链完整</span><b>{completeIssues.length}</b></div>
      </div>
    </header>

    {checkExecution.length > 0 && <details className={`ri-check-audit ${bridgeFailureCount || checkSourceMissingCount || checkSystemIncompleteCount ? 'incomplete' : ''}`}>
      <summary>
        <div><b>审核规则执行情况</b><span>区分已检查、资料缺失、系统未完成和有依据的不适用</span></div>
        <div className="ri-check-audit-counts">
          <span className="passed">已检查 {checkPassedCount}</span>
          <span className="issues">有问题 {checkIssueCount}</span>
          {checkSourceMissingCount > 0 && <span className="source-missing">资料缺失 {checkSourceMissingCount}</span>}
          {checkSystemIncompleteCount > 0 && <span className="system-incomplete">系统未完成 {checkSystemIncompleteCount}</span>}
          {checkNotApplicableCount > 0 && <span className="no-data">不适用 {checkNotApplicableCount}</span>}
        </div>
      </summary>
      {bridgeFailureCount > 0 && <p className="ri-check-audit-warning">
        有 {bridgeFailureCount} 份结构化资料未能完整回溯到原文，本次审核状态为“不完整”，不能视为通过。
      </p>}
      <div className="ri-check-audit-grid">
        {checkExecution.map(item => <article className={item.state} key={item.check_id}>
          <span>{item.state === 'passed' ? '已检查'
            : item.state === 'issues_found' ? '发现问题'
              : item.state === 'source_missing' ? '资料缺失'
                : item.state === 'system_incomplete' ? '系统未完成'
                  : '本次不适用'}</span>
          <b>{CHECK_LABELS[item.check_id] || item.check_id}</b>
          <small>{item.state === 'not_applicable'
            ? '已有范围证据证明本次不适用'
            : item.state === 'source_missing'
              ? `应有 ${item.expected_input_count ?? 0} 组关键资料，实际找到 ${item.anchored_input_count ?? 0} 组；需补充或修订资料`
              : item.state === 'system_incomplete'
                ? `应检查 ${item.expected_input_count ?? 0} 组，系统仅完成 ${item.anchored_input_count ?? 0} 组；本轮不能判为通过`
                : `实际检查 ${item.input_count} 组证据${item.finding_count ? `，发现 ${item.finding_count} 项` : '，未发现问题'}`}</small>
        </article>)}
      </div>
    </details>}

    <div className="ri-progress">
      <div><span>处理进度</span><b>{decidedCount} / {attentionIssues.length}</b></div>
      <div className="ri-progress-track"><i style={{ width: `${attentionIssues.length ? decidedCount / attentionIssues.length * 100 : 100}%` }} /></div>
      <button disabled={!allDecided || !revisionIssues.length} onClick={() => setReturnConfirmOpen(true)}>
        {allDecided && !revisionIssues.length
          ? '审核完成 · 无需退回'
          : `统一退回修订${revisionIssues.length ? ` · ${revisionIssues.length} 项` : ''}`}
      </button>
    </div>

    <div className="ri-workspace">
      <aside className={`ri-queue ${selected ? 'has-selection' : ''}`}>
        <div className="ri-queue-tools">
          <label><Icon name="search" size={15} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索问题或测试项目" /></label>
          <div>
            <button className={filter === 'attention' ? 'active' : ''} onClick={() => setFilter('attention')}>待处理 {attentionIssues.length}</button>
            <button className={filter === 'all' ? 'active' : ''} onClick={() => setFilter('all')}>全部 {issues.length}</button>
            <button className={filter === 'complete' ? 'active' : ''} onClick={() => setFilter('complete')}>证据链完整 {completeIssues.length}</button>
          </div>
        </div>
        <div className="ri-issue-scroll">
          {grouped.map(group => <div className="ri-group" key={group.key}>
            <div className="ri-group-head"><span>{group.label}</span><b>{group.issues.length}</b></div>
            {group.issues.map(issue => {
              const decision = issueDecision(snapshot, issue)
              return <button key={issue.id} className={`ri-issue ${issue.severity} ${selected?.id === issue.id ? 'selected' : ''}`} onClick={() => setSelectedId(issue.id)}>
                <div className="ri-issue-meta"><span>{issue.subject}</span><span>{decision ? '已处理' : issue.group === 'complete' ? '已闭合' : '待处理'}</span></div>
                <h3>{issue.title}</h3><p>{decision ? `${RESOLUTION_LABEL[decision.resolution_code] || '已记录结论'}：${decision.comment}` : issue.summary}</p>
              </button>
            })}
          </div>)}
          {!visible.length && <div className="ri-empty">没有符合当前筛选的问题</div>}
        </div>
      </aside>

      {selected && <main className="ri-detail">
        <button className="ri-mobile-back" onClick={() => setSelectedId('')}><Icon name="arrow-left" size={15} />返回问题列表</button>
        <div className="ri-detail-head">
          <div className={`ri-bang ${selected.severity}`}>{selected.group === 'complete' ? <Icon name="check" size={22} /> : '!'}</div>
          <div><span className={`ri-state ${selected.severity}`}>{selected.group === 'complete' ? '证据完整' : selected.severity === 'error' ? '需要处理' : '需要确认'}</span>
            <h2>{selected.title}</h2><p>{selected.summary}</p>
          </div>
          <span className="ri-index">{issues.indexOf(selected) + 1} / {issues.length}</span>
        </div>

        <CrossDocumentComparison issue={selected} />

        <div className="ri-section-title"><div><h3>{showStructuredEvidence ? '系统实际比较了什么' : '原文在哪里'}</h3><span>{showStructuredEvidence ? '默认说明字段角色、数值和来源；完整原文仍可随时查看' : '文件名、页码和原文均来自本次审核快照'}</span></div>
          {showStructuredEvidence && <div className="ri-evidence-tabs"><button className={evidenceView === 'structured' ? 'active' : ''} onClick={() => setEvidenceView('structured')}>结构化对比</button><button className={evidenceView === 'raw' ? 'active' : ''} onClick={() => setEvidenceView('raw')}>完整原文</button></div>}
        </div>
        {showStructuredResult && evidenceView === 'structured' && <ResultEvidenceComparison issue={selected} rows={selectedResultRows} />}
        {showStructuredParameter && evidenceView === 'structured' && <ParameterEvidenceComparison entries={selectedParameterEntries} />}
        {showStructuredInstrument && evidenceView === 'structured' && <InstrumentEvidenceComparison issue={selected} />}
        {(!showStructuredEvidence || evidenceView === 'raw') && <div className="ri-evidence-grid">
          {documentTypesForIssue(selected).map(docType => {
            const allEvidence = selected.evidence.filter(item => item.doc_type === docType)
            const fieldFinding = selected.findings.find(finding => finding.check_id === 'DOC-CROSS-FIELD-001')
            const valueGroup = fieldFinding
              ? crossFieldValueGroups(fieldFinding).find(item => item.docType === docType)
              : undefined
            const normalizeEvidenceValue = (value: string) => value.toLocaleLowerCase('zh-CN').replace(/[^a-z0-9\u4e00-\u9fff]/g, '')
            const differingValues = new Set((valueGroup?.differingValues || []).map(normalizeEvidenceValue))
            const evidence = fieldFinding && allEvidence.length > 6
              ? [...allEvidence].sort((left, right) => (
                Number(differingValues.has(normalizeEvidenceValue(right.exact_quote)))
                - Number(differingValues.has(normalizeEvidenceValue(left.exact_quote)))
              )).slice(0, 6)
              : allEvidence
            const missingDocs = new Set(Array.isArray(selected.findings[0].metadata?.missing_docs) ? selected.findings[0].metadata.missing_docs : [])
            return <SourceCard key={docType} setId={setId} graphId={snapshot.run.graph_id} docType={docType} evidence={evidence} evidenceTotal={allEvidence.length} overview={overview} missing={selected.group === 'missing' && missingDocs.has(docType)} conflict={selected.group === 'conflict' && docType === 'final_report' && evidence.length > 0} comparison={selected.findings.some(finding => finding.check_id === 'DOC-CROSS-FIELD-001')} rawMetas={rawMetas} rawIdentityMember={docType === 'original_records' ? selectedRawMember : undefined} />
          })}
        </div>}

        {selected.group === 'missing' && <div className="ri-inventory-callout">
          <div><b>确认系统查过哪些原始记录</b><span>查看压缩包内每一份记录的测试项目、样品编号和模式，并可直接打开对应文件。</span></div>
          <button onClick={openInventory}>查看原始记录清单{rawMetas ? ` · ${rawMetas.length} 份` : ''}</button>
        </div>}

        <div className="ri-explanation">
          <div><h3>这意味着什么</h3><p>{selected.findings.map(item => item.description).filter(Boolean).join(' ')}</p></div>
          <div><h3>建议怎么处理</h3><p>{selected.group === 'missing' ? '先确认该项目是否属于本次范围；若需要执行，补充原始记录并更新检测报告；若不属于本次范围，选择“本次不适用”并填写依据。' : selected.group === 'confirm' ? '核对原文与委托范围后作出人工判断；系统不会把文字差异直接当成确定错误。' : selected.group === 'complete' ? '无需处理。证据链已闭合，可继续查看其他问题。' : '以可追溯原文为准，确认需要修改的资料并填写处理依据。'}</p></div>
        </div>

        {issueDecision(snapshot, selected) ? <div className="ri-saved-decision">
          <div><small>审核员处理结论</small><b>{RESOLUTION_LABEL[issueDecision(snapshot, selected)?.resolution_code || ''] || '已记录'}</b><p>{issueDecision(snapshot, selected)?.comment}</p></div>
          <button onClick={() => beginDecision(selected)}>修改处理结论</button>
        </div> : selected.group !== 'complete' && <div className="ri-actions">
          <div><b>请选择处理方式</b><span>每个问题必须单独确认，理由会进入审计记录。</span></div>
          <button className="primary" onClick={() => beginDecision(selected)}>处理这个问题</button>
        </div>}

        <details className="ri-technical"><summary>技术信息（供排查算法使用）</summary>
          <dl><dt>检查规则</dt><dd>{selected.findings.map(item => item.check_id).join('、')}</dd><dt>问题编号</dt><dd>{selected.findings.map(item => item.finding_id).join('、')}</dd><dt>证据定位</dt><dd>{selected.evidence.length
            ? `${selected.evidence.slice(0, 12).map(item => String(item.metadata?.unit_id || item.evidence_id)).join('、')}${selected.evidence.length > 12 ? `（另有 ${selected.evidence.length - 12} 条，已纳入上方统计）` : ''}`
            : '无'}</dd></dl>
        </details>
      </main>}
    </div>

    {decisionIssue && <div className="ri-modal-backdrop" role="presentation" onMouseDown={event => event.target === event.currentTarget && setDecisionIssue(null)}>
      <div className="ri-modal" role="dialog" aria-modal="true" aria-labelledby="ri-decision-title">
        <header><div><small>处理问题</small><h2 id="ri-decision-title">{decisionIssue.subject} · {decisionIssue.title}</h2></div><button aria-label="关闭" onClick={() => setDecisionIssue(null)}><Icon name="close" size={18} /></button></header>
        <fieldset><legend>选择处理方式</legend><div className="ri-resolution-options">
          {RESOLUTIONS.map(option => <label className={resolutionCode === option.code ? 'selected' : ''} key={option.code}><input type="radio" name="resolution" value={option.code} checked={resolutionCode === option.code} onChange={() => setResolutionCode(option.code)} /><span><b>{option.label}</b><small>{option.help}</small></span></label>)}
        </div></fieldset>
        <label className="ri-comment"><span>判断依据 <em>必填</em></span><textarea value={comment} onChange={event => setComment(event.target.value)} placeholder={resolutionCode === 'not_applicable' ? '例如：委托单与已确认试验计划均未包含该项目，本次不执行。' : '请写明依据、应修改的资料或原文位置…'} /></label>
        {localError && <div className="ri-modal-error">{localError}</div>}
        <footer><button onClick={() => setDecisionIssue(null)}>取消</button><button className="primary" disabled={saving} onClick={saveDecision}>{saving ? '正在保存…' : '保存处理结论'}</button></footer>
      </div>
    </div>}

    {inventoryOpen && <div className="ri-modal-backdrop" role="presentation" onMouseDown={event => event.target === event.currentTarget && setInventoryOpen(false)}>
      <div className="ri-modal ri-inventory-modal" role="dialog" aria-modal="true" aria-labelledby="ri-inventory-title">
        <header><div><small>原始记录溯源</small><h2 id="ri-inventory-title">压缩包内文件清单</h2><p>{rawDocument?.filename}</p></div><button aria-label="关闭" onClick={() => setInventoryOpen(false)}><Icon name="close" size={18} /></button></header>
        <label className="ri-inventory-search"><Icon name="search" size={15} /><input value={inventoryQuery} onChange={event => setInventoryQuery(event.target.value)} placeholder="搜索测试项目、样品编号或模式" /></label>
        <div className="ri-inventory-list">
          {rawMetas === null ? <div className="ri-empty">正在读取文件清单…</div> : filteredRawMetas.map(({ meta, index }) => { const memberUrl = rawDocument && index > 0 ? getArchiveMemberFileUrl(setId, rawDocument.doc_id, index) : ''; return <div key={`${index}-${meta.source_filename}`}><span>{index ? String(index).padStart(2, '0') : '—'}</span><div><b>{rawDisplayName(meta, `原始记录 ${index || ''}`)}</b><small>{meta.conclusion ? `记录结论：${meta.conclusion}` : index ? '尚无结构化结论' : '未能与压缩包源文件定位'}</small></div>{memberUrl && <a href={memberUrl} onClick={event => { event.preventDefault(); void openAuthenticatedResource(memberUrl) }}>打开</a>}</div>})}
          {rawMetas !== null && !filteredRawMetas.length && <div className="ri-empty">没有匹配的原始记录</div>}
        </div>
      </div>
    </div>}

    {returnConfirmOpen && <div className="ri-modal-backdrop" role="presentation" onMouseDown={event => event.target === event.currentTarget && setReturnConfirmOpen(false)}>
      <div className="ri-modal ri-confirm-modal" role="dialog" aria-modal="true"><header><div><small>统一退回修订</small><h2>将 {revisionIssues.length} 个问题交给提交方修订？</h2></div><button aria-label="关闭" onClick={() => setReturnConfirmOpen(false)}><Icon name="close" size={18} /></button></header>
        <p>系统会创建一个新的修订版本。本次所有逐条处理结论和“不适用”理由都会继续保留在审核记录中。</p>
        <footer><button onClick={() => setReturnConfirmOpen(false)}>继续检查</button><button className="primary" onClick={onReturnRevision}>确认并进入修订</button></footer>
      </div>
    </div>}
  </section>
}
