import type { DocumentSetListItem, DocumentSetOverview, EvidenceGraphFinding, FindingDecision, SetExtractionsResponse, ExtractionQualityGate } from './types'

export type InstrumentComparisonRow = {
  role: 'report_only' | 'raw_only' | 'matched_report' | 'matched_raw'
  doc_type: string
  manufacturer: string
  model: string
  serial_no: string
  calibration_end: string
}

export type InstrumentComparison = {
  reportTotal: number
  rawTotal: number
  matchedCount: number
  reportOnlyCount: number
  rawOnlyCount: number
  matched: InstrumentComparisonRow[]
  reportOnly: InstrumentComparisonRow[]
  rawOnly: InstrumentComparisonRow[]
  issue: string
}

function uniqueInstrumentRows(rows: InstrumentComparisonRow[]) {
  const seen = new Set<string>()
  return rows.filter(row => {
    const key = [row.manufacturer, row.model, row.serial_no].map(value => value.trim().toLowerCase()).join('|')
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export function instrumentComparisonFor(
  finding: Pick<EvidenceGraphFinding, 'check_id' | 'metadata'>,
): InstrumentComparison | undefined {
  if (finding.check_id !== 'DOC-INSTRUMENT-CONSISTENCY-001') return undefined
  const sourceRows = Array.isArray(finding.metadata?.comparison_rows)
    ? finding.metadata.comparison_rows : []
  const rows: InstrumentComparisonRow[] = sourceRows
    .filter((row: unknown): row is Record<string, unknown> => Boolean(row) && typeof row === 'object')
    .map(row => ({
      role: (row.role === 'raw_inventory' ? 'matched_raw' : row.role) as InstrumentComparisonRow['role'],
      doc_type: String(row.doc_type || ''),
      manufacturer: String(row.manufacturer || ''),
      model: String(row.model || ''),
      serial_no: String(row.serial_no || ''),
      calibration_end: String(row.calibration_end || ''),
    }))
    .filter(row => ['report_only', 'raw_only', 'matched_report', 'matched_raw'].includes(row.role))
  if (!rows.length) return undefined
  const reportOnly = uniqueInstrumentRows(rows.filter(row => row.role === 'report_only'))
  const rawOnly = uniqueInstrumentRows(rows.filter(row => row.role === 'raw_only'))
  const rawOnlyKeys = new Set(rawOnly.map(row =>
    [row.manufacturer, row.model, row.serial_no].map(value => value.trim().toLowerCase()).join('|'),
  ))
  // Historical snapshots stored the entire raw inventory as `raw_inventory`,
  // including rows that were also emitted as `raw_only`. Do not present those
  // duplicated one-sided rows as successful matches.
  const matchedRaw = uniqueInstrumentRows(rows.filter(row => row.role === 'matched_raw'))
    .filter(row => !rawOnlyKeys.has(
      [row.manufacturer, row.model, row.serial_no].map(value => value.trim().toLowerCase()).join('|'),
    ))
  const matchedReport = uniqueInstrumentRows(rows.filter(row => row.role === 'matched_report'))
  const matched = matchedRaw.length ? matchedRaw : matchedReport
  const stored = finding.metadata?.comparison_summary || {}
  const matchedCount = Number(stored.matched_count ?? matched.length)
  const reportOnlyCount = Number(stored.report_only_count ?? reportOnly.length)
  const rawOnlyCount = Number(stored.raw_only_count ?? rawOnly.length)
  const reportTotal = Number(stored.report_total ?? matchedCount + reportOnlyCount)
  const rawTotal = Number(stored.raw_total ?? matchedCount + rawOnlyCount)
  const rawDifference = rawOnlyCount
    ? `原始记录共 ${rawTotal} 台，其中 ${rawOnlyCount} 台未在检测报告中出现`
    : `原始记录共 ${rawTotal} 台，没有独有仪器`
  return {
    reportTotal, rawTotal, matchedCount, reportOnlyCount, rawOnlyCount,
    matched, reportOnly, rawOnly,
    issue: `两份清单共同 ${matchedCount} 台。检测报告共 ${reportTotal} 台，其中 ${reportOnlyCount} 台未在原始记录中出现；${rawDifference}。业务要求两份仪器清单完全一致。`,
  }
}

export type ReviewStageKey = 'intake' | 'extraction' | 'run' | 'findings' | 'complete'

export function taskStartStage(
  task: Pick<DocumentSetListItem, 'status' | 'latest_run_status'>,
): 'intake' | 'run' | 'findings' {
  if (['draft', 'incomplete', 'revision', 'error'].includes(task.status)) return 'intake'
  if (task.status === 'locked') return 'run'
  if (['created', 'building', 'running'].includes(task.latest_run_status || '')) return 'run'
  return 'findings'
}

export function countDismissedMachineFindings(
  decisions: Array<Pick<FindingDecision, 'resolution_code'>>,
): number {
  return decisions.filter(decision =>
    ['false_positive', 'not_applicable'].includes(decision.resolution_code || ''),
  ).length
}

export function shouldPollOverview(
  overview: DocumentSetOverview | undefined,
  stage: ReviewStageKey,
  demo: boolean,
) {
  if (demo || !['intake', 'extraction'].includes(stage)) return false
  return Boolean(overview?.documents?.some(document =>
    ['pending', 'extracting', 'queued', 'processing'].includes(document.extraction_status || ''),
  ))
}

export function shouldPollExtractions(
  response: SetExtractionsResponse | undefined,
  demo: boolean,
) {
  if (demo) return false
  return Boolean(response?.extractions.some(extraction =>
    ['pending', 'extracting', 'queued', 'processing'].includes(extraction.status || ''),
  ))
}

export function extractionGateFor(
  overview: DocumentSetOverview | undefined,
  demo = false,
): ExtractionQualityGate {
  if (overview?.extraction_gate) return overview.extraction_gate
  const docs = overview?.documents || []
  const required = ['order_form', 'test_plan', 'original_records', 'final_report']
  const blockers = required
    .map(type => docs.find(document => document.doc_type === type))
    .filter((document): document is NonNullable<typeof document> => Boolean(document))
    .flatMap(document => {
      const status = document.extraction_status || ''
      const quality = document.extraction_quality || ''
      if (status === 'completed' && quality !== 'partial' && quality !== 'failed') return []
      return [{ doc_type: document.doc_type, label: document.filename, reason: status === 'completed' ? '提取质量需要确认' : '提取尚未完成' }]
    })
  const missing = required.filter(type => !docs.some(document => document.doc_type === type)).map(type => ({ doc_type: type, label: type, reason: '资料未上传' }))
  const allBlockers = [...missing, ...blockers]
  if (demo && !allBlockers.length) return { status: 'pass', title: '资料已通过自动质量门禁', note: '演示资料可直接进入审核结果。', blockers: [], manual_review_required: false, auto_gate_allowed: true }
  return {
    status: allBlockers.length ? 'block' : 'pass',
    title: allBlockers.length ? '需要处理提取异常' : '资料已通过自动质量门禁',
    note: allBlockers.length ? '只显示异常资料，处理后才能锁定审核快照。' : '四份资料均形成完整、可追溯的提取快照，可直接进入审核结果。',
    blockers: allBlockers,
    manual_review_required: Boolean(allBlockers.length),
    auto_gate_allowed: !allBlockers.length,
  }
}

export type CheckExecutionRecord = {
  check_id?: string
  state?: string
  finding_count?: number
  input_count?: number
  expected_input_count?: number
  anchored_input_count?: number
  reason_code?: string
}

const executionReasonLabels: Record<string, string> = {
  all_anchored_inputs_checked: '纳入范围的证据均已定位并完成检查',
  required_evidence_not_fully_anchored: '必要证据尚未全部定位到原文区域',
  rule_input_contract_unfulfilled: '规则所需输入尚未完整形成',
  rule_findings_created: '检查已执行，并已生成待处理问题',
  scope_proven_not_applicable: '当前资料范围已证明本项不适用',
  plan_scope_not_complete: '测试计划范围尚未完整提取',
  plan_item_sample_count_not_visually_anchored: '测试计划中的样品数量尚未定位到原文区域',
  report_item_or_sample_results_not_fully_extracted: '报告项目或样品结果尚未完整提取',
  unit_extraction_not_complete: '部分资料单元提取尚未完成',
  task_cancelled: '审核任务被取消',
  plan_or_report_plan_number_missing: '测试计划编号或报告引用的计划编号缺失，无法完成编号核对',
  report_test_date_range_missing: '检测报告未填写完整试验日期区间',
  report_receive_or_test_start_date_missing: '检测报告缺少样品接收日期或试验开始日期',
  report_issue_date_missing: '检测报告未填写可用的签发日期',
  all_numbered_sections_anchored: '检测报告的编号章节均已定位并完成结构检查',
  all_raw_instrument_profiles_anchored: '原始记录中的仪器身份均已定位',
  all_report_instrument_profiles_anchored: '检测报告中的仪器身份均已定位',
  report_uses_non_numeric_result_judgements: '报告使用符合、不符合等文字判定，本项数值类型检查不适用',
  all_raw_execution_notes_checked_for_anomalies: '原始记录中的执行备注均已完成异常检查',
  report_has_no_explicit_multidimensional_result_matrix: '报告没有需要执行多维结果矩阵检查的明确表格',
  all_report_result_rows_anchored: '报告结果行均已定位并完成空值检查',
  report_has_no_mutually_exclusive_numeric_result_payloads: '报告没有可用于互斥数值重复检查的结果数据',
  declared_page_count_checked: '报告声明页数已经完成核对',
  all_calibration_dates_checked_against_execution_dates: '已定位的校准日期均已与对应试验日期完成核对',
  all_report_calibration_dates_checked: '检测报告中的仪器校准日期均已与对应试验日期完成核对',
  report_instrument_or_execution_date_not_fully_anchored: '部分报告仪器或对应试验日期尚未完整定位',
  plan_has_no_explicit_artifact_requirements: '测试计划未明确要求额外证据制品，本项不适用',
  report_has_no_explicit_expected_actual_variable_name_pairs: '报告没有明确的预期值与实际值变量名称对，本项不适用',
  no_numeric_calculation_table_in_report: '报告没有可复算的数值计算表，本项不适用',
  no_numeric_limit_result_table_in_report: '报告没有可执行限值比较的数值结果表，本项不适用',
}

function executionReasonLabel(reasonCode?: string) {
  if (!reasonCode) return '系统未提供附加原因'
  if (executionReasonLabels[reasonCode]) return executionReasonLabels[reasonCode]
  const parts = reasonCode.split('+').filter(Boolean)
  if (parts.length > 1 && parts.every(part => executionReasonLabels[part])) {
    return parts.map(part => executionReasonLabels[part]).join('；')
  }
  return '检查已保存执行状态，但当前界面尚未收录这项原因的中文说明'
}

export function presentCheckExecution(check: CheckExecutionRecord) {
  const state = check.state || 'system_incomplete'
  const expected = check.expected_input_count ?? check.input_count ?? 0
  const checked = check.anchored_input_count ?? check.input_count ?? 0
  return {
    state,
    expected,
    checked,
    reason: check.reason_code || '无附加原因',
    reasonLabel: executionReasonLabel(check.reason_code),
  }
}
