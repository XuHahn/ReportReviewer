import type { DocumentSetListItem, DocumentSetOverview, EmcStandard, EvidenceGraphSnapshot, ProjectGroup, SetStatsResponse, User } from './types'

export const demoUser: User = { employee_id: 'R-0248', name: '林审核', role: 'admin', is_active: true }

export const demoStats: SetStatsResponse = {
  total_sets: 128, draft_sets: 7, locked_sets: 5, reviewed_sets: 116,
  total_issues: 412, confirmed_issues: 328, pass_rate: 91.6, average_review_seconds: 164,
  status_counts: { draft: 7, reviewing: 5, reviewed: 116 },
}

export const demoSets: DocumentSetListItem[] = [
  { set_id: 'EMC-20260809-A7F2', title: 'RD20 电机电磁兼容检测', status: 'reviewing', created_at: '2026-08-09T08:42:00+08:00', updated_at: '2026-08-09T10:18:00+08:00', project_group_name: '电机 · EMC 2026', documents_count: 4, finding_count: 34, pending_count: 5, latest_run_status: 'machine_incomplete' },
  { set_id: 'EMC-20260808-P59A', title: '前车标灯 P59A / P57A2', status: 'reviewed', created_at: '2026-08-08T13:10:00+08:00', updated_at: '2026-08-08T17:04:00+08:00', project_group_name: '车灯 · EMC 2026', documents_count: 4, finding_count: 12, pending_count: 0, latest_run_status: 'machine_complete' },
  { set_id: 'EMC-20260807-CTRL', title: '车身控制器电气负荷试验', status: 'locked', created_at: '2026-08-07T09:24:00+08:00', updated_at: '2026-08-07T09:55:00+08:00', project_group_name: '控制器 · EMC 2026', documents_count: 4, finding_count: 0, pending_count: 0, latest_run_status: 'building' },
  { set_id: 'EMC-20260806-HV01', title: '高压电源模块抗扰度', status: 'draft', created_at: '2026-08-06T15:31:00+08:00', updated_at: '2026-08-06T16:02:00+08:00', project_group_name: '电源 · EMC 2026', documents_count: 3, finding_count: 0, pending_count: 0 },
  { set_id: 'EMC-20260805-GW21', title: '网关控制器辐射发射', status: 'reviewed', created_at: '2026-08-05T09:05:00+08:00', updated_at: '2026-08-05T14:36:00+08:00', project_group_name: '网关 · EMC 2026', documents_count: 4, finding_count: 8, pending_count: 0, latest_run_status: 'machine_complete' },
]

export const demoOverview: DocumentSetOverview = {
  ...demoSets[0], revision: 3, locked_at: '2026-08-09T09:14:00+08:00',
  documents: [
    { doc_id: 'doc-order', doc_type: 'order_form', filename: 'E20260402869601委托单.xls', file_size: 84211, page_count: 2, status: 'ready', extraction_status: 'completed', doc_version: 3 },
    { doc_id: 'doc-plan', doc_type: 'test_plan', filename: 'E20260402869601测试计划.pdf', file_size: 2840301, page_count: 18, status: 'ready', extraction_status: 'completed', doc_version: 3 },
    { doc_id: 'doc-raw', doc_type: 'original_records', filename: 'E20260402869601原始记录.zip', file_size: 32520314, page_count: 54, status: 'ready', extraction_status: 'completed', doc_version: 3 },
    { doc_id: 'doc-report', doc_type: 'final_report', filename: 'E20260402869601-1正式报告.pdf', file_size: 12519830, page_count: 84, status: 'ready', extraction_status: 'completed', doc_version: 3 },
  ],
  standards: [{ id: 'std-1', code: 'ISO 16750-2:2012', title: '道路车辆 电气和电子设备的环境条件和试验' }],
}

export const demoStandards: EmcStandard[] = [
  { id: 'std-1', code: 'ISO 16750-2:2012', title: '道路车辆 电气和电子设备的环境条件和试验', organization: 'ISO', category: '电气负荷', version: '2012', knowledge_status: 'ready', graph_status: 'published', requirements_count: 86, chunks_count: 214 },
  { id: 'std-2', code: 'GB/T 28046.2-2019', title: '道路车辆 电气及电子设备的环境条件和试验 第2部分：电气负荷', organization: 'GB', category: '电气负荷', version: '2019', knowledge_status: 'ready', graph_status: 'reviewing', requirements_count: 92, chunks_count: 238 },
  { id: 'std-3', code: 'CISPR 25:2021', title: '车辆、船和内燃机 无线电骚扰特性', organization: 'CISPR', category: '电磁骚扰', version: '2021', knowledge_status: 'processing', graph_status: 'pending', requirements_count: 0, chunks_count: 0 },
]

export const demoGroups: ProjectGroup[] = [
  { id: 'g1', name: '电机 · EMC 2026', description: '平台执行器、电机与驱动模块', category: '电机', member_ids: ['R-0248', 'R-0311'], member_count: 2 },
  { id: 'g2', name: '车灯 · EMC 2026', description: '前后灯具与控制模块', category: '车灯', member_ids: ['R-0248'], member_count: 1 },
  { id: 'g3', name: '控制器 · EMC 2026', description: '车身与域控制器', category: '控制器', member_ids: ['R-0311', 'R-0412'], member_count: 2 },
]

export const demoSnapshot: EvidenceGraphSnapshot = {
  run: { graph_id: 'run-demo-3', set_id: demoOverview.set_id, status: 'machine_incomplete', created_at: '2026-08-09T09:15:00+08:00', completed_at: '2026-08-09T09:18:12+08:00', finding_count: 34,
    metadata: { check_execution: [{ state: 'passed' }, { state: 'issues_found' }, { state: 'issues_found' }, { state: 'source_missing' }] } },
  nodes: [], edges: [],
  evidence: [
    { evidence_id: 'ev-plan-1', doc_id: 'doc-plan', doc_type: 'test_plan', filename: 'E20260402869601测试计划.pdf', page_number: 6, exact_quote: '反向电压试验：样品 0016，Mode 2，14 V / 60 s，要求等级 C。', extraction_method: 'llm_transcription', metadata: { role_label: '计划要求', verdict: 'required' } },
    { evidence_id: 'ev-raw-1', doc_id: 'doc-raw', doc_type: 'original_records', filename: 'E20260402869601_反向电压_0016_Mode2_原始记录.pdf', page_number: 1, exact_quote: '样品编号 E20260402869601-0016；Mode 2；14 V / 60 s；功能状态等级 C；结果：符合。', extraction_method: 'table_transcription', metadata: { role_label: '原始结果', verdict: 'pass', member_index: 20 } },
    { evidence_id: 'ev-report-1', doc_id: 'doc-report', doc_type: 'final_report', filename: 'E20260402869601-1正式报告.pdf', page_number: 57, exact_quote: 'Reverse voltage, sample 0016, Mode 2, 14 V / 60 s, Functional status A, Result: Pass.', extraction_method: 'llm_extraction', metadata: { role_label: '报告结果', verdict: 'pass' } },
    { evidence_id: 'ev-report-status-1', doc_id: 'doc-report', doc_type: 'final_report', filename: 'E20260402869601-1正式报告.pdf', page_number: 57, exact_quote: 'Functional status A', extraction_method: 'llm_extraction', metadata: { role_label: '错误状态等级', verdict: 'conflict' } },
    { evidence_id: 'ev-report-2', doc_id: 'doc-report', doc_type: 'final_report', filename: 'E20260402869601-1正式报告.pdf', page_number: 58, exact_quote: 'Reverse voltage, sample 0016, Mode 2, 14 V / 60 s, Functional status C, Result: Fail.', extraction_method: 'llm_extraction', metadata: { role_label: '报告结果', verdict: 'fail' } },
    { evidence_id: 'ev-order-1', doc_id: 'doc-order', doc_type: 'order_form', filename: 'E20260402869601委托单.xls', page_number: 1, sheet_name: '委托信息', cell_range: 'B4:F6', exact_quote: '委托单位：博世汽车部件（苏州）有限公司', extraction_method: 'spreadsheet_cell', metadata: { role_label: '委托主体' } },
    { evidence_id: 'ev-report-org', doc_id: 'doc-report', doc_type: 'final_report', filename: 'E20260402869601-1正式报告.pdf', page_number: 2, exact_quote: 'Applicant: Bosch Automotive Products (Suzhou) Co., Ltd.', extraction_method: 'llm_extraction', metadata: { role_label: '申请单位' } },
  ],
  findings: [
    { finding_id: 'f-conflict-1', check_id: 'GRAPH-RESULT-CONFLICT-001', status: 'confirmed_error', severity: 'error', title: '同一样品和模式出现两种报告结果', description: '样品 0016、Mode 2、14 V / 60 s 在检测报告中同时出现 Pass 与 Fail，且功能状态等级也不一致。', subject_node_ids: [], evidence_ids: ['ev-raw-1', 'ev-report-1', 'ev-report-status-1', 'ev-report-2'], metadata: { test_item: '反向电压', values_by_doc_type: { original_records: 'C / 符合', final_report: 'A / Pass；C / Fail' }, impact: '最终报告无法给出唯一、可追溯的试验结论。' } },
    { finding_id: 'f-missing-1', check_id: 'GRAPH-COVERAGE-001', status: 'confirmed_error', severity: 'error', title: '计划要求做，但未找到完整测试结果', description: '试验计划包含高温存储项目，原始记录与检测报告中均未建立对应证据。', subject_node_ids: [], evidence_ids: ['ev-plan-1'], metadata: { test_item: '高温存储', missing_docs: ['original_records', 'final_report'], impact: '无法证明计划要求的项目已执行并出具结果。' } },
    { finding_id: 'f-parameter-1', check_id: 'GRAPH-PARAMETER-003', status: 'unresolved', severity: 'warning', title: '试验电压偏移范围不一致', description: '计划要求 ±1.5 V，原始记录使用 ±1.0 V，需要确认是否存在经批准的偏差。', subject_node_ids: [], evidence_ids: ['ev-plan-1', 'ev-raw-1'], metadata: { test_item: '供电偏移', impact: '试验条件可能未覆盖计划定义的边界。' } },
    { finding_id: 'f-identity-1', check_id: 'GRAPH-IDENTITY-REVIEW-001', status: 'unresolved', severity: 'warning', title: '中英文委托单位是否为同一主体？', description: '中文名称与英文申请单位缺少统一企业标识，系统不能仅凭名称相似度自动合并。', subject_node_ids: [], evidence_ids: ['ev-order-1', 'ev-report-org'], metadata: { test_item: '委托单位', impact: '报告主体需要人工确认，避免错误归并。' } },
    { finding_id: 'f-pass-1', check_id: 'GRAPH-RESULT-CONSISTENCY-001', status: 'confirmed_pass', severity: 'info', title: '反向电压 Mode 1 证据链完整', description: '计划、原始记录与报告的测试身份、条件和结果一致。', subject_node_ids: [], evidence_ids: ['ev-plan-1', 'ev-raw-1', 'ev-report-1'], metadata: { test_item: '反向电压 · Mode 1' } },
  ],
  decisions: [
    { finding_id: 'f-parameter-1', decision: 'unresolved', comment: '等待试验负责人确认偏差审批单。', resolution_code: 'deferred', resolution_status: 'deferred', actor_id: 'R-0248', created_at: '2026-08-09T10:04:00+08:00' },
  ],
}

export const trendData = [
  { day: '周一', pass: 88, tasks: 17 }, { day: '周二', pass: 91, tasks: 21 }, { day: '周三', pass: 89, tasks: 18 },
  { day: '周四', pass: 93, tasks: 24 }, { day: '周五', pass: 92, tasks: 20 }, { day: '周六', pass: 95, tasks: 9 }, { day: '今天', pass: 94, tasks: 19 },
]

export const issueData = [
  { name: '资料缺失', value: 38, color: '#c84d3f' }, { name: '内容不一致', value: 29, color: '#e28a3b' },
  { name: '报告未完整', value: 17, color: '#d0a43b' }, { name: '人工确认', value: 16, color: '#3e7185' },
]
