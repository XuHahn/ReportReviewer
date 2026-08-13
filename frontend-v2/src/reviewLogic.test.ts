import { describe, expect, it } from 'vitest'
import { countDismissedMachineFindings, instrumentComparisonFor, presentCheckExecution, shouldPollOverview, taskStartStage } from './reviewLogic'
import type { DocumentSetOverview } from './types'

function overview(status: string): DocumentSetOverview {
  return {
    set_id: 'set-1', status: 'incomplete', created_at: '',
    documents: [{ doc_id: 'doc-1', doc_type: 'test_plan', filename: 'plan.xls', extraction_status: status }],
  }
}

describe('shouldPollOverview', () => {
  it('polls active extraction states while the intake page is open', () => {
    expect(shouldPollOverview(overview('pending'), 'intake')).toBe(true)
    expect(shouldPollOverview(overview('extracting'), 'intake')).toBe(true)
  })

  it('stops for terminal states and later stages', () => {
    expect(shouldPollOverview(overview('completed'), 'intake')).toBe(false)
    expect(shouldPollOverview(overview('failed'), 'intake')).toBe(false)
    expect(shouldPollOverview(overview('extracting'), 'run')).toBe(false)
  })
})

describe('presentCheckExecution', () => {
  it('keeps a valid exhaustive-scan pass when no positive input was emitted', () => {
    expect(presentCheckExecution({
      state: 'passed', input_count: 0, expected_input_count: 16,
      anchored_input_count: 16, reason_code: 'all_anchored_inputs_checked',
    })).toEqual({
      state: 'passed', expected: 16, checked: 16,
      reason: 'all_anchored_inputs_checked',
      reasonLabel: '纳入范围的证据均已定位并完成检查',
    })
  })

  it('presents composite execution reasons in reviewer-facing Chinese', () => {
    const result = presentCheckExecution({
      state: 'system_incomplete', input_count: 114,
      expected_input_count: 114, anchored_input_count: 112,
      reason_code: 'all_calibration_dates_checked_against_execution_dates+report_instrument_or_execution_date_not_fully_anchored',
    })
    expect(result.reasonLabel).toContain('校准日期')
    expect(result.reasonLabel).toContain('尚未完整定位')
    expect(result.reasonLabel).not.toContain('_')
  })

  it('does not expose an unknown internal reason code as normal UI copy', () => {
    const result = presentCheckExecution({ reason_code: 'future_internal_reason' })
    expect(result.reasonLabel).toBe('检查已保存执行状态，但当前界面尚未收录这项原因的中文说明')
  })

  it('renders the complete report calibration reason without exposing codes', () => {
    const result = presentCheckExecution({
      reason_code: 'all_calibration_dates_checked_against_execution_dates+all_report_calibration_dates_checked',
    })
    expect(result.reasonLabel).toContain('已定位的校准日期')
    expect(result.reasonLabel).toContain('检测报告中的仪器校准日期')
    expect(result.reasonLabel).not.toContain('all_report')
  })
})

describe('taskStartStage', () => {
  it('opens locked and actively running tasks at the machine-review stage', () => {
    expect(taskStartStage({ status: 'locked' })).toBe('run')
    expect(taskStartStage({ status: 'reviewing', latest_run_status: 'building' })).toBe('run')
  })

  it('opens preparation tasks at intake and completed machine runs at findings', () => {
    expect(taskStartStage({ status: 'revision' })).toBe('intake')
    expect(taskStartStage({ status: 'reviewing', latest_run_status: 'machine_incomplete' })).toBe('findings')
    expect(taskStartStage({ status: 'reviewed', latest_run_status: 'machine_complete' })).toBe('findings')
  })
})

describe('countDismissedMachineFindings', () => {
  it('counts the decision codes actually emitted by the findings workflow', () => {
    expect(countDismissedMachineFindings([
      { resolution_code: 'false_positive' },
      { resolution_code: 'not_applicable' },
      { resolution_code: 'report_revision' },
    ])).toBe(2)
  })
})

describe('instrumentComparisonFor', () => {
  it('explains matched and one-sided devices for historical findings', () => {
    const result = instrumentComparisonFor({
      check_id: 'DOC-INSTRUMENT-CONSISTENCY-001',
      metadata: { comparison_rows: [
        { role: 'report_only', doc_type: 'final_report', manufacturer: 'KIKUSUI', model: 'PBZ40-10', serial_no: 'VM001868', calibration_end: '2026-10-22' },
        { role: 'report_only', doc_type: 'final_report', manufacturer: '泰克', model: 'MDO34', serial_no: 'C048720', calibration_end: '2026-08-06' },
        { role: 'raw_inventory', doc_type: 'original_records', manufacturer: 'Tektronix', model: 'MDO3102', serial_no: 'C055285', calibration_end: '2026-02-06' },
        { role: 'raw_inventory', doc_type: 'original_records', manufacturer: '菊水', model: 'PBZ20-20', serial_no: 'FN003249', calibration_end: '2026-09-08' },
      ] },
    })
    expect(result).toMatchObject({
      reportTotal: 4, rawTotal: 2, matchedCount: 2,
      reportOnlyCount: 2, rawOnlyCount: 0,
    })
    expect(result?.issue).toContain('两份清单共同 2 台')
    expect(result?.issue).toContain('业务要求两份仪器清单完全一致')
  })

  it('deduplicates the two source rows of one matched physical instrument', () => {
    const result = instrumentComparisonFor({
      check_id: 'DOC-INSTRUMENT-CONSISTENCY-001',
      metadata: {
        comparison_summary: { report_total: 2, raw_total: 1, matched_count: 1, report_only_count: 1, raw_only_count: 0 },
        comparison_rows: [
          { role: 'matched_report', doc_type: 'final_report', manufacturer: 'KIKUSUI', model: 'PBZ40-10', serial_no: 'S1' },
          { role: 'matched_raw', doc_type: 'original_records', manufacturer: 'KIKUSUI', model: 'PBZ40-10', serial_no: 'S1' },
          { role: 'report_only', doc_type: 'final_report', manufacturer: 'Tektronix', model: 'MDO34', serial_no: 'S2' },
        ],
      },
    })
    expect(result?.matched).toHaveLength(1)
    expect(result?.matchedCount).toBe(1)
  })

  it('does not turn a historical raw-only row into a match', () => {
    const result = instrumentComparisonFor({
      check_id: 'DOC-INSTRUMENT-CONSISTENCY-001',
      metadata: { comparison_rows: [
        { role: 'report_only', doc_type: 'final_report', manufacturer: 'A', model: 'M1', serial_no: 'R1' },
        { role: 'raw_only', doc_type: 'original_records', manufacturer: 'B', model: 'M2', serial_no: 'O1' },
        { role: 'raw_inventory', doc_type: 'original_records', manufacturer: 'B', model: 'M2', serial_no: 'O1' },
      ] },
    })
    expect(result).toMatchObject({
      reportTotal: 1, rawTotal: 1, matchedCount: 0,
      reportOnlyCount: 1, rawOnlyCount: 1,
    })
    expect(result?.matched).toHaveLength(0)
  })
})
