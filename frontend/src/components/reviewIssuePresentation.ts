import type {
  EvidenceGraphEvidence,
  EvidenceGraphFinding,
  EvidenceGraphSnapshot,
} from '../types'

export type IssueGroupKey = 'missing' | 'conflict' | 'incomplete' | 'confirm' | 'complete'

export interface ReviewDisplayIssue {
  id: string
  group: IssueGroupKey
  title: string
  subject: string
  summary: string
  severity: 'error' | 'warning' | 'info'
  status: string
  findings: EvidenceGraphFinding[]
  evidence: EvidenceGraphEvidence[]
}

export const ISSUE_GROUPS: Array<{ key: IssueGroupKey; label: string }> = [
  { key: 'missing', label: '资料缺失' },
  { key: 'conflict', label: '内容不一致' },
  { key: 'incomplete', label: '报告未填写完整' },
  { key: 'confirm', label: '需要人工确认' },
  { key: 'complete', label: '证据链完整' },
]

export const REVIEW_DOC_LABELS: Record<string, string> = {
  order_form: '委托单',
  test_plan: '试验计划',
  original_records: '原始记录',
  final_report: '检测报告',
  test_standard: '测试标准',
}

const REVIEW_DOC_ORDER = Object.keys(REVIEW_DOC_LABELS)

const CROSS_FIELD_LABELS: Record<string, string> = {
  client_name: '委托单位',
  client_address: '委托单位地址',
  sample_name: '样品名称',
  sample_model: '样品型号',
  part_number: '零件号',
  rated_voltage: '额定/供电电压',
  sample_id: '实验室样品编号',
}

export interface CrossFieldValueGroup {
  docType: string
  values: string[]
  commonValues: string[]
  differingValues: string[]
}

export function crossFieldValueGroups(finding: EvidenceGraphFinding): CrossFieldValueGroup[] {
  const raw = finding.metadata?.values_by_doc_type
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return []
  const entries = Object.entries(raw as Record<string, unknown>)
    .map(([docType, values]) => ({
      docType,
      values: Array.isArray(values)
        ? Array.from(new Set(values.filter((value): value is string => typeof value === 'string' && Boolean(value.trim()))))
        : [],
    }))
    .filter(item => item.values.length)
  if (entries.length < 2) return []
  const commonValues = entries[0].values.filter(value => (
    entries.slice(1).every(item => item.values.includes(value))
  ))
  const commonSet = new Set(commonValues)
  return entries.map(item => ({
    ...item,
    commonValues,
    differingValues: item.values.filter(value => !commonSet.has(value)),
  }))
}

function joinDocumentLabels(values: string[]): string {
  const labels = Array.from(new Set(values)).sort((left, right) => {
    const leftType = Object.keys(REVIEW_DOC_LABELS).find(key => REVIEW_DOC_LABELS[key] === left)
    const rightType = Object.keys(REVIEW_DOC_LABELS).find(key => REVIEW_DOC_LABELS[key] === right)
    const leftIndex = REVIEW_DOC_ORDER.indexOf(leftType || '')
    const rightIndex = REVIEW_DOC_ORDER.indexOf(rightType || '')
    return (leftIndex < 0 ? REVIEW_DOC_ORDER.length : leftIndex)
      - (rightIndex < 0 ? REVIEW_DOC_ORDER.length : rightIndex)
  })
  if (labels.length < 2) return labels[0] || '不同资料'
  if (labels.length === 2) return `${labels[0]}与${labels[1]}`
  return `${labels.slice(0, -1).join('、')}和${labels[labels.length - 1]}`
}

export function documentTypesForIssue(issue: ReviewDisplayIssue): string[] {
  if (issue.group === 'missing') return ['test_plan', 'original_records', 'final_report']
  return Array.from(new Set(issue.evidence.map(item => item.doc_type)))
    .sort((left, right) => {
      const leftIndex = REVIEW_DOC_ORDER.indexOf(left)
      const rightIndex = REVIEW_DOC_ORDER.indexOf(right)
      return (leftIndex < 0 ? REVIEW_DOC_ORDER.length : leftIndex)
        - (rightIndex < 0 ? REVIEW_DOC_ORDER.length : rightIndex)
    })
}

function groupFor(finding: EvidenceGraphFinding): IssueGroupKey {
  if (finding.status === 'confirmed_pass') return 'complete'
  if (finding.check_id === 'GRAPH-COVERAGE-001') return 'missing'
  if (['DOC-SIGNATURE-001', 'DOC-REQUIRED-FIELD-001', 'DOC-PAGINATION-001'].includes(finding.check_id)) {
    return 'incomplete'
  }
  if (finding.status.includes('unresolved') || finding.severity === 'warning') return 'confirm'
  return 'conflict'
}

function subjectFor(finding: EvidenceGraphFinding, evidence: EvidenceGraphEvidence[]): string {
  if (finding.check_id === 'GRAPH-COVERAGE-001') {
    return finding.title.replace(/(?:执行与发布证据完整|缺少必要执行或发布证据).*$/, '').trim()
  }
  if (finding.check_id === 'GRAPH-COVERAGE-002') {
    return finding.title.replace(/(?:不在测试计划要求范围内|尚未闭合到测试计划|的执行与发布身份关系不唯一).*$/, '').trim()
  }
  if (finding.check_id === 'DOC-CROSS-FIELD-001') {
    const fieldName = typeof finding.metadata?.field_name === 'string'
      ? finding.metadata.field_name.trim() : ''
    return CROSS_FIELD_LABELS[fieldName]
      || finding.title.replace(/(?:存在不同写法|在文档间不一致).*$/, '').trim()
  }
  if (finding.check_id.includes('RESULT')) {
    const rawItem = evidence.find(item => item.doc_type === 'original_records')?.exact_quote
      ?.split('\n').map(value => value.trim()).find(Boolean)
    return rawItem || finding.title.split(/的|同一样品/)[0].trim()
  }
  return '检测报告'
}

function plainTitle(finding: EvidenceGraphFinding, evidence: EvidenceGraphEvidence[]): string {
  if (finding.check_id === 'GRAPH-COVERAGE-001') {
    return finding.status === 'confirmed_pass'
      ? '计划、原始记录和报告均已找到对应证据'
      : '计划要求做，但没有找到完整的测试结果'
  }
  if (finding.check_id === 'GRAPH-COVERAGE-002') {
    return finding.metadata?.ambiguous_identity
      ? '原始记录与检测报告中的项目身份关系不唯一'
      : finding.metadata?.plan_extraction_complete === false
        ? '执行或报告项目尚未闭合到测试计划'
        : '原始记录或报告出现了计划中没有的测试项目'
  }
  if (finding.check_id === 'DOC-CROSS-FIELD-001') {
    const documents = Array.from(new Set(evidence.map(item => REVIEW_DOC_LABELS[item.doc_type] || item.doc_type)) as Set<string>)
    const subject = subjectFor(finding, evidence)
    return documents.length > 1
      ? `${joinDocumentLabels(documents)}中的${subject}写法不同`
      : `${subject}在不同资料中的写法不同`
  }
  if (finding.check_id === 'REPORT-RESULT-CONFLICT-001') {
    const requiredLevels = Array.isArray(finding.metadata?.required_levels)
      ? finding.metadata.required_levels.filter(value => typeof value === 'string' && value.trim())
      : []
    if (new Set(requiredLevels).size > 1) {
      return '同一次试验使用了不同要求等级，导致结论相反'
    }
  }
  if (finding.check_id === 'REPORT-SPEC-RESULT-001') {
    if (finding.metadata?.comparison_kind === 'performance_criteria_vs_actual_performance') {
      return finding.metadata?.actual_is_better === true
        ? '实际性能等级优于要求，允许通过但需确认'
        : '实际性能等级低于要求等级'
    }
    const parameterName = typeof finding.metadata?.parameter_name === 'string'
      ? finding.metadata.parameter_name.trim() : ''
    return `${parameterName || '同一试验参数'}的规范要求与结果记录不一致`
  }
  const titles: Record<string, string> = {
    'DOC-RESULT-CONSISTENCY-001': '同一次试验在原始记录与报告中的结果不一致',
    'REPORT-RESULT-CONFLICT-001': '同一次试验在报告中同时出现通过和不通过',
    'REPORT-SPEC-RESULT-001': '同一试验参数前后不一致',
    'DOC-PAGINATION-001': '报告目录引用了不存在的页码',
    'DOC-SIGNATURE-001': '报告的编制、审核或批准信息没有填写',
    'DOC-REQUIRED-FIELD-001': '报告编号仍是模板占位内容',
  }
  return titles[finding.check_id] || finding.title
}

function plainSummary(finding: EvidenceGraphFinding, evidence: EvidenceGraphEvidence[]): string {
  if (finding.check_id === 'GRAPH-COVERAGE-001') {
    const missing = Array.isArray(finding.metadata?.missing_docs) ? finding.metadata.missing_docs : []
    if (!missing.length) return '三份资料之间的对应关系已经闭合'
    return missing.map((doc: string) => doc === 'original_records' ? '原始记录未找到对应项' : doc === 'final_report' ? '检测报告未找到对应项' : '缺少对应资料').join('；')
  }
  if (finding.check_id === 'GRAPH-COVERAGE-002') {
    if (finding.metadata?.ambiguous_identity) return '项目身份存在多个候选，需要人工确认后才能判断是否属于计划范围'
    if (finding.metadata?.plan_extraction_complete === false) return '计划范围尚未完整提取，当前不能判定为计划外项目'
    const hasRaw = typeof finding.metadata?.raw_item_id === 'string' && Boolean(finding.metadata.raw_item_id)
    const hasReport = Array.isArray(finding.metadata?.report_item_ids) && finding.metadata.report_item_ids.length > 0
    if (hasRaw && hasReport) return '原始记录和检测报告均有该项目，但完整测试计划中没有对应要求'
    if (hasRaw) return '原始记录有该执行项目，但完整测试计划中没有对应要求'
    return '检测报告发布了该项目，但完整测试计划中没有对应要求'
  }
  if (finding.check_id === 'DOC-CROSS-FIELD-001') {
    const groups = crossFieldValueGroups(finding)
    if (groups.length) {
      const commonCount = groups[0].commonValues.length
      const counts = groups.map(item => (
        `${REVIEW_DOC_LABELS[item.docType] || item.docType} ${item.values.length} 个`
      )).join('，')
      const differences = groups
        .filter(item => item.differingValues.length)
        .map(item => `${REVIEW_DOC_LABELS[item.docType] || item.docType}另有 ${item.differingValues.length} 个未在其他资料共同出现`)
        .join('；')
      return `${counts}；共同 ${commonCount} 个${differences ? `；${differences}` : ''}`
    }
    const values = evidence.map(item => {
      const value = item.exact_quote.trim().replace(/\s+/g, ' ')
      const compact = value.length > 42 ? `${value.slice(0, 42)}…` : value
      return `${REVIEW_DOC_LABELS[item.doc_type] || item.doc_type}“${compact || '未提取到原值'}”`
    })
    return Array.from(new Set(values)).join('；')
  }
  if (finding.check_id === 'REPORT-SPEC-RESULT-001') {
    const declaredValue = typeof finding.metadata?.declared_value === 'string'
      ? finding.metadata.declared_value.trim() : ''
    const observedValues = Array.isArray(finding.metadata?.observed_values)
      ? finding.metadata.observed_values.filter((value): value is string => typeof value === 'string' && Boolean(value.trim()))
      : []
    if (declaredValue && observedValues.length) {
      return finding.metadata?.comparison_kind === 'performance_criteria_vs_actual_performance'
        ? `要求等级（Performance criteria）：${declaredValue}；实际等级（Actual performance）：${observedValues.join('、')}`
        : `规范要求：${declaredValue}；结果记录实际采用：${observedValues.join('、')}`
    }
  }
  const locations = evidence.map(item => item.page_number ? `${item.filename} 文件第 ${item.page_number} 页` : item.filename).filter(Boolean)
  return locations.length ? Array.from(new Set(locations)).slice(0, 2).join('；') : finding.description
}

function reportEvidenceKey(finding: EvidenceGraphFinding, evidenceById: Map<string, EvidenceGraphEvidence>) {
  return Array.from(new Set(finding.evidence_ids
    .map(id => evidenceById.get(id))
    .filter((item): item is EvidenceGraphEvidence => item !== undefined && item.doc_type === 'final_report')
    .map(item => `${item.doc_id}:${item.page_number}`)))
    .sort()
    .join('|')
}

export function isHiddenReviewFinding(finding: EvidenceGraphFinding): boolean {
  return finding.check_id === 'REPORT-SPEC-RESULT-001'
    && finding.metadata?.comparison_kind === 'performance_criteria_vs_actual_performance'
    && finding.metadata?.actual_is_better === true
}

export function buildReviewDisplayIssues(snapshot: EvidenceGraphSnapshot): ReviewDisplayIssue[] {
  const evidenceById = new Map(snapshot.evidence.map(item => [item.evidence_id, item]))
  const consumed = new Set<string>()
  const result: ReviewDisplayIssue[] = []

  snapshot.findings.forEach(finding => {
    // Older evidence graphs stored a warning when the actual performance class
    // was better than the required class.  This is a passing result and should
    // not occupy the reviewer issue queue.  New graphs no longer emit it, while
    // this guard keeps already persisted reviews consistent.
    if (isHiddenReviewFinding(finding)) return
    if (consumed.has(finding.finding_id)) return
    let merged = [finding]
    if (['DOC-RESULT-CONSISTENCY-001', 'REPORT-RESULT-CONFLICT-001'].includes(finding.check_id)) {
      const key = reportEvidenceKey(finding, evidenceById)
      if (key) {
        const partner = snapshot.findings.find(candidate => (
          candidate.finding_id !== finding.finding_id
          && !consumed.has(candidate.finding_id)
          && ['DOC-RESULT-CONSISTENCY-001', 'REPORT-RESULT-CONFLICT-001'].includes(candidate.check_id)
          && reportEvidenceKey(candidate, evidenceById) === key
        ))
        if (partner) merged = [finding, partner]
      }
    }
    merged.forEach(item => consumed.add(item.finding_id))
    const evidence = Array.from(new Map(
      merged.flatMap(item => item.evidence_ids)
        .map(id => evidenceById.get(id))
        .filter((item): item is EvidenceGraphEvidence => Boolean(item))
        .map(item => [item.evidence_id, item]),
    ).values())
    const reportConflict = merged.find(item => item.check_id === 'REPORT-RESULT-CONFLICT-001')
    const primary = reportConflict
      || merged.find(item => item.check_id === 'DOC-RESULT-CONSISTENCY-001')
      || merged[0]
    result.push({
      id: merged.map(item => item.finding_id).sort().join('+'),
      group: groupFor(primary),
      title: merged.length > 1
        ? '检测报告内部同时出现通过和不通过'
        : plainTitle(primary, evidence),
      subject: subjectFor(primary, evidence),
      summary: merged.length > 1
        ? '检测报告自身的结果不唯一，请先核对报告明细；在报告结果统一前，不能把它解释为原始记录与报告之间的单一结果差异。'
        : plainSummary(primary, evidence),
      severity: merged.some(item => item.severity === 'error') ? 'error' : primary.severity,
      status: primary.status,
      findings: merged,
      evidence,
    })
  })
  return result.sort((a, b) => ISSUE_GROUPS.findIndex(group => group.key === a.group)
    - ISSUE_GROUPS.findIndex(group => group.key === b.group))
}
