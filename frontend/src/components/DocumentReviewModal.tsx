import React, { useState, useEffect, useRef } from 'react'
import type { DocType, DocExtraction } from '../types'
import { getDocumentExtraction, getOverrides, getDocumentFileUrl, openAuthenticatedResource } from '../api'
import { logger } from '../logger'

// ── Props ──

interface Props {
  setId: string
  docType: DocType
  label: string
  docId: string
  onClose: () => void
  onConfirm: (editedValues: Record<string, string>) => Promise<void> | void
  onSaveOnly?: (editedValues: Record<string, string>) => Promise<void> | void
}

// ═══════════════════════════════════════════════════════════════════════════
// SVG Icons (vector, no emoji)
// ═══════════════════════════════════════════════════════════════════════════

function IconCheck({ size = 12 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" style={{ display: 'inline-block', verticalAlign: 'middle' }}>
      <circle cx="10" cy="10" r="8" stroke="currentColor" strokeWidth="1.5" fill="none"/>
      <path d="M6 10l3 3 5-6" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

function IconWarn({ size = 12 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" style={{ display: 'inline-block', verticalAlign: 'middle' }}>
      <path d="M10 3L2 20h16L10 3z" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinejoin="round"/>
      <line x1="10" y1="9" x2="10" y2="13" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
      <circle cx="10" cy="16.5" r=".8" fill="currentColor"/>
    </svg>
  )
}

function IconClose({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" style={{ display: 'block' }}>
      <line x1="5" y1="5" x2="15" y2="15" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
      <line x1="15" y1="5" x2="5" y2="15" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
    </svg>
  )
}

function IconSpinner() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" style={{ animation: 'spin 0.7s linear infinite' }}>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2" fill="none" opacity=".25"/>
      <path d="M12 3a9 9 0 019 9" stroke="currentColor" strokeWidth="2" fill="none"/>
    </svg>
  )
}

function IconDoc({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" style={{ display: 'inline-block', verticalAlign: 'middle' }}>
      <path d="M6 2h6l6 6v12a2 2 0 01-2 2H6a2 2 0 01-2-2V4a2 2 0 012-2z" stroke="currentColor" strokeWidth="1.5" fill="none"/>
      <path d="M12 2v6h6" stroke="currentColor" strokeWidth="1.5" fill="none"/>
      <line x1="7" y1="12" x2="13" y2="12" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
      <line x1="7" y1="15" x2="13" y2="15" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
    </svg>
  )
}

function IconClipboard({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" style={{ display: 'inline-block', verticalAlign: 'middle' }}>
      <rect x="5" y="3" width="10" height="15" rx="1.5" stroke="currentColor" strokeWidth="1.5" fill="none"/>
      <path d="M8 3V1.5h4V3" stroke="currentColor" strokeWidth="1.5" fill="none"/>
      <line x1="7" y1="8" x2="13" y2="8" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
      <line x1="7" y1="11" x2="13" y2="11" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
      <line x1="7" y1="14" x2="10" y2="14" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
    </svg>
  )
}

function IconEdit({ size = 12 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" style={{ display: 'inline-block', verticalAlign: 'middle' }}>
      <path d="M14 3l3 3L7 16H4v-3L14 3z" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinejoin="round"/>
      <line x1="12" y1="5" x2="15" y2="8" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
    </svg>
  )
}

function IconPin({ size = 12 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" style={{ display: 'inline-block', verticalAlign: 'middle' }}>
      <circle cx="10" cy="7" r="3" stroke="currentColor" strokeWidth="1.5" fill="none"/>
      <path d="M10 10v7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
      <path d="M7 17h6" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
    </svg>
  )
}

// ── Helpers ──

function confidenceColor(c: number) { return c >= 0.9 ? '#2f9e44' : c >= 0.7 ? '#e67700' : '#e03131' }
function fmtConf(c: number) { return c.toFixed(2) }

// ── Editable cell ──

function EditableCell({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const [saved, setSaved] = useState(value)

  // Sync when selection changes (different field selected)
  useEffect(() => { setSaved(value); setDraft(value); setEditing(false) }, [value])

  if (!editing) {
    return (
      <span onClick={() => setEditing(true)}
        title="点击编辑此字段"
        style={{ cursor: 'text', padding: '3px 6px', borderRadius: 4, margin: '-3px -6px', display: 'inline-flex', alignItems: 'center', gap: 5, border: '1.5px solid transparent', transition: 'background .15s' }}
        onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = '#f8f9fa'; (e.currentTarget as HTMLElement).style.borderColor = '#dee2e6' }}
        onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent'; (e.currentTarget as HTMLElement).style.borderColor = 'transparent' }}>
        {saved || <span style={{ color: '#98a2b3', fontStyle: 'italic' }}>（点击填写）</span>}
        <svg width="10" height="10" viewBox="0 0 20 20" style={{ opacity: .35, flexShrink: 0 }}><path d="M13 3l4 4-10 10H3v-4L13 3z" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round"/></svg>
      </span>
    )
  }
  return (
    <input autoFocus value={draft}
      onChange={e => setDraft(e.target.value)}
      onBlur={() => { setSaved(draft); setEditing(false); onChange(draft) }}
      onKeyDown={e => {
        if (e.key === 'Enter') { setSaved(draft); setEditing(false); onChange(draft) }
        if (e.key === 'Escape') { setDraft(saved); setEditing(false) }
      }}
      style={{ fontSize: '.78rem', padding: '5px 9px', border: '1.5px solid #4f46e5', borderRadius: 4, width: '100%', fontFamily: 'inherit', background: '#fff' }}
    />
  )
}

// ═══════════════════════════════════════════════════════════════════════════
// Main Component — Layout D: Split Panel
// ═══════════════════════════════════════════════════════════════════════════

export default function DocumentReviewModal({ setId, docType, label, docId, onClose, onConfirm, onSaveOnly }: Props) {
  const [loading, setLoading] = useState(true)
  const [extraction, setExtraction] = useState<DocExtraction | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirmedFields, setConfirmedFields] = useState<Set<string>>(new Set())
  const [doubtfulFields, setDoubtfulFields] = useState<Set<string>>(new Set())
  const [editedValues, setEditedValues] = useState<Record<string, string>>({})
  const [selectedKey, setSelectedKey] = useState<string>('')
  const [rightView, setRightView] = useState<'detail' | 'source'>('detail')

  // Ref to guard against safety-timer overwriting a successfully loaded result
  const dataLoadedRef = useRef(false)

  useEffect(() => {
    let cancelled = false
    dataLoadedRef.current = false
    setLoading(true)
    setError(null)

    // Safety timeout: if network/fetch takes >30s, surface an error.
    const safetyTimer = setTimeout(() => {
      if (!cancelled && !dataLoadedRef.current) {
        setError('加载提取数据超时（数据量较大，请关闭弹窗后重试）')
        setLoading(false)
      }
    }, 30000)

    // Fetch extraction data for just THIS document (decoupled from other docs).
    // Also fetch any previously saved human overrides.
    const extractionPromise = docId
      ? getDocumentExtraction(setId, docId)
      : Promise.resolve(null)

    const overridesPromise = docId
      ? getOverrides(setId, docId)
      : Promise.resolve({ overrides: {} as Record<string, string>, count: 0 })

    Promise.all([
      extractionPromise,
      overridesPromise,
    ]).then(([ext, ov]) => {
      if (cancelled) return
      if (ext && (ext.status === 'done' || ext.status === 'partial')) {
        setExtraction(ext)
        // Restore previously saved overrides
        if (ov?.overrides && Object.keys(ov.overrides).length > 0) {
          setEditedValues(ov.overrides)
        }
        // Defer buildAllRows so React can flush the loading→ready transition
        // and the safety timer callback can see dataLoadedRef before firing.
        dataLoadedRef.current = true
        clearTimeout(safetyTimer)
        setLoading(false)
        setTimeout(() => {
          if (cancelled) return
          const auto: Set<string> = new Set()
          const rows = buildAllRows(docType, ext)
          for (const r of rows) {
            if (r.confidence >= 0.95) auto.add(r.key)
          }
          setConfirmedFields(auto)
          if (rows.length > 0) setSelectedKey(rows[0].key)
        }, 0)
      } else if (ext && ext.status !== 'done' && ext.status !== 'partial') {
        // Document exists but extraction not done yet (extracting/pending/failed)
        setExtraction({
          status: ext.status || 'extracting',
          doc_id: docId || '',
          filename: ext.filename || '',
          fields: [],
          structured: null,
          cell_map: null,
          cell_values: null,
        })
        clearTimeout(safetyTimer)
        setLoading(false)
      } else {
        // No extraction data at all (docId was empty or API returned null)
        setExtraction({
          status: 'extracting',
          doc_id: docId || '',
          filename: '',
          fields: [],
          structured: null,
          cell_map: null,
          cell_values: null,
        })
        clearTimeout(safetyTimer)
        setLoading(false)
      }
    }).catch((e: any) => {
      if (cancelled) return
      dataLoadedRef.current = true
      clearTimeout(safetyTimer)
      logger.error('Failed to load extraction data', { component: 'DocumentReviewModal', docType }, e)
      setError('加载提取数据失败')
      setLoading(false)
    })

    return () => { cancelled = true; clearTimeout(safetyTimer) }
  }, [setId, docType, docId])

  // ── Loading ──
  if (loading) {
    return (
      <div className="modal-overlay" onClick={onClose}>
        <div className="modal-container" onClick={e => e.stopPropagation()} style={{ maxWidth: 520 }}>
          <div style={modalHeader}><span style={{ fontWeight: 700, fontSize: '.96rem' }}>核查{label}</span><button onClick={onClose} style={closeBtn} aria-label="关闭"><IconClose /></button></div>
          <div style={{ padding: 40, textAlign: 'center', color: '#98a2b3', fontSize: '.95rem' }}><IconSpinner /> 加载提取数据...</div>
        </div>
      </div>
    )
  }

  // ── Error / empty extraction ──
  if (error || !extraction) {
    return (
      <div className="modal-overlay" onClick={onClose}>
        <div className="modal-container" onClick={e => e.stopPropagation()} style={{ maxWidth: 560 }}>
          <div style={modalHeader}><span style={{ fontWeight: 700, fontSize: '.96rem' }}>核查{label}</span><button onClick={onClose} style={closeBtn} aria-label="关闭"><IconClose /></button></div>
          <div style={{ padding: 24, textAlign: 'center' }}>
            {error ? (
              <div>
                <div style={{ marginBottom: 12, color: '#e03131' }}><IconWarn size={32} /></div>
                <div style={{ color: '#e03131', fontWeight: 600, marginBottom: 8, fontSize: '.89rem' }}>加载提取数据失败</div>
                <div style={{ color: '#98a2b3', fontSize: '.66rem', lineHeight: 1.6 }}>可能原因：AI 提取服务暂不可用、文档格式不支持、或文档内容无法解析。</div>
                {error !== '无提取数据' && <div style={{ marginTop: 12, padding: '8px 12px', background: '#f8f9fa', borderRadius: 6, fontSize: '.62rem', color: '#6c757d', fontFamily: 'monospace', textAlign: 'left', maxHeight: 100, overflow: 'auto' }}>{error}</div>}
              </div>
            ) : (
              <div>
                <div style={{ marginBottom: 12, color: '#e67700' }}><IconDoc size={32} /></div>
                <div style={{ color: '#e67700', fontWeight: 600, marginBottom: 8, fontSize: '.89rem' }}>无提取数据</div>
                <div style={{ color: '#98a2b3', fontSize: '.66rem', lineHeight: 1.6 }}>该文档的提取状态异常。请尝试重新上传文档，或联系管理员检查 AI 服务状态。</div>
              </div>
            )}
          </div>
          <div style={modalFooter}><button className="pg-btn" onClick={onClose}>关闭</button></div>
        </div>
      </div>
    )
  }

  const allRows = buildAllRows(docType, extraction)
  const sections = getSections(docType, extraction)
  const totalFields = allRows.length
  const confirmedCount = allRows.filter(r => confirmedFields.has(r.key)).length
  const lowConfRows = allRows.filter(r => r.confidence < 0.7)
  const selectedRow = allRows.find(r => r.key === selectedKey) || allRows[0]

  const toggleConfirm = (key: string) => {
    setConfirmedFields(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key); else { next.add(key); setDoubtfulFields(p => { const n = new Set(p); n.delete(key); return n }) }
      return next
    })
  }

  const toggleDoubtful = (key: string) => {
    setDoubtfulFields(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key); else { next.add(key); setConfirmedFields(p => { const n = new Set(p); n.delete(key); return n }) }
      return next
    })
  }

  const displayValue = (row: ReviewRow) =>
    editedValues[row.key] !== undefined ? editedValues[row.key] : row.value

  // ── Empty fields ──
  if (totalFields === 0) {
    return (
      <div className="modal-overlay" onClick={onClose}>
        <div className="modal-container" onClick={e => e.stopPropagation()} style={{ maxWidth: 560 }}>
          <div style={modalHeader}><span style={{ fontWeight: 700, fontSize: '.96rem' }}>核查{label}</span><button onClick={onClose} style={closeBtn} aria-label="关闭"><IconClose /></button></div>
          <div style={{ padding: 24, textAlign: 'center' }}>
            <div style={{ marginBottom: 12, color: '#e67700' }}><IconDoc size={40} /></div>
            <div style={{ color: '#e67700', fontWeight: 600, marginBottom: 8, fontSize: '.89rem' }}>未提取到结构化数据</div>
            <div style={{ color: '#98a2b3', fontSize: '.66rem', lineHeight: 1.6 }}>
              {docType === 'test_plan' && 'AI 未能从试验计划中提取到结构化字段。请检查 DeepSeek API 服务，或尝试重新上传文档。'}
              {docType === 'final_report' && 'AI 未能从检测报告中提取到结构化字段。请检查 DeepSeek API 服务，或尝试重新上传文档。'}
              {docType === 'order_form' && '代码提取未能产生结果，请确认上传的是 .xls 格式的委托单。'}
              {docType === 'original_records' && '未能从原始记录中提取到可核查的测试项。请查看提取错误、确认压缩包内文件可读取，或重新执行提取。'}
              {docType === 'test_standard' && '测试标准仅提取全文文本，不支持结构化字段提取。'}
            </div>
          </div>
          <div style={modalFooter}>
            <span style={{ fontSize: '.62rem', color: '#98a2b3' }}>无提取字段 — 建议重新上传文档</span>
            <button className="pg-btn" onClick={onClose}>关闭</button>
          </div>
        </div>
      </div>
    )
  }

  if (docType === 'test_plan') {
    return (
      <TestPlanMatrixReview
        extraction={extraction}
        label={label}
        setId={setId}
        allRows={allRows}
        selectedKey={selectedKey}
        setSelectedKey={setSelectedKey}
        confirmedFields={confirmedFields}
        doubtfulFields={doubtfulFields}
        editedValues={editedValues}
        setEditedValues={setEditedValues}
        displayValue={displayValue}
        toggleConfirm={toggleConfirm}
        toggleDoubtful={toggleDoubtful}
        onClose={onClose}
        onConfirm={onConfirm}
        onSaveOnly={onSaveOnly}
      />
    )
  }

  // ── Section grouping ──
  const sectionOrder: Record<string, string> = {
    product: '产品信息', applicant: '申请方', requirements: '测试要求', other: '其他信息',
    basic: '基本信息', items: '测试项总表', details: '试验细则',
    tests: '测试结论', instruments: '仪器清单', calibration: '校准异常', summary: '汇总信息',
    cover: '封面信息', sample: '样品信息', results: '测试结果',
  }
  const sectionColors: Record<string, { bg: string; border: string; badge: string }> = {
    product: { bg: 'rgba(25,113,194,.03)', border: '#1971c2', badge: '#e7f5ff' },
    applicant: { bg: 'rgba(230,119,0,.03)', border: '#e67700', badge: '#fff3bf' },
    requirements: { bg: 'rgba(43,138,62,.03)', border: '#2b8a3e', badge: '#d3f9d8' },
    other: { bg: 'rgba(112,72,232,.03)', border: '#7048e8', badge: '#f3f0ff' },
    basic: { bg: 'rgba(108,117,125,.03)', border: '#6c757d', badge: '#f1f3f5' },
    items: { bg: 'rgba(224,49,49,.03)', border: '#e03131', badge: '#ffe3e3' },
    details: { bg: 'rgba(108,117,125,.03)', border: '#6c757d', badge: '#f1f3f5' },
    tests: { bg: 'rgba(230,119,0,.03)', border: '#e67700', badge: '#fff3bf' },
    instruments: { bg: 'rgba(76,110,245,.03)', border: '#4c6ef5', badge: '#e7f5ff' },
    calibration: { bg: 'rgba(224,49,49,.03)', border: '#dc2626', badge: '#fee2e2' },
    summary: { bg: 'rgba(108,117,125,.03)', border: '#6c757d', badge: '#f1f3f5' },
    cover: { bg: 'rgba(76,110,245,.03)', border: '#4c6ef5', badge: '#e7f5ff' },
    sample: { bg: 'rgba(43,138,62,.03)', border: '#2b8a3e', badge: '#d3f9d8' },
    results: { bg: 'rgba(25,113,194,.03)', border: '#1971c2', badge: '#e7f5ff' },
  }
  const defaultColor = { bg: 'rgba(108,117,125,.03)', border: '#6c757d', badge: '#f1f3f5' }

  const sectionGroups: { key: string; label: string; rows: ReviewRow[]; color: typeof defaultColor }[] = []
  for (const sec of sections) {
    const rows = allRows.filter(r => r.section === sec.key)
    if (rows.length > 0) {
      sectionGroups.push({ key: sec.key, label: sectionOrder[sec.key] || sec.key, rows, color: sectionColors[sec.key] || defaultColor })
    }
  }

  const methodText: Record<DocType, string> = {
    order_form: '代码提取 · 100% 确定性',
    test_plan: 'AI 提取 · 置信度 ~0.88',
    original_records: '代码+AI · 多文件提取',
    final_report: 'AI 提取 · 置信度 ~0.87',
    test_standard: '全文提取',
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-container" onClick={e => e.stopPropagation()}
        style={{ maxWidth: 1400, width: '96vw', maxHeight: '96vh', display: 'flex', flexDirection: 'column' }}>

        {/* Header */}
        <div style={modalHeader}>
          <span style={{ fontWeight: 700, fontSize: '1.05rem' }}>核查{label}</span>
          <span style={{ fontSize: '.68rem', color: '#98a2b3', padding: '2px 8px', background: '#f8f9fa', borderRadius: 4, fontWeight: 600, marginLeft: 10 }}>
            {methodText[docType]}
          </span>
          <span style={{ fontSize: '.68rem', color: '#98a2b3', marginLeft: 'auto', marginRight: 12 }}>{extraction.filename}</span>
          <button onClick={onClose} style={closeBtn} aria-label="关闭"><IconClose /></button>
        </div>

        {/* Stats bar */}
        <div style={statsBar}>
          <span style={{ fontWeight: 600 }}><b style={{ fontSize: '.94rem' }}>{totalFields}</b> 字段</span>
          <span style={{ color: '#2f9e44' }}><b style={{ fontSize: '.94rem' }}>{confirmedCount}</b> 已确认</span>
          {totalFields - confirmedCount > 0 && <span style={{ color: '#e67700', fontWeight: 600 }}><b style={{ fontSize: '.94rem' }}>{totalFields - confirmedCount}</b> 待确认</span>}
          {lowConfRows.length > 0 && <span style={{ color: '#e03131', fontSize: '.68rem' }}><b style={{ fontSize: '.90rem' }}>{lowConfRows.length}</b> 低置信</span>}
          {Object.keys(editedValues).length > 0 && <span style={{ color: '#98a2b3', marginLeft: 'auto', fontSize: '.68rem' }}>已修改 {Object.keys(editedValues).length} 处</span>}
        </div>

        {/* Split body */}
        <div style={splitBody}>
          {/* Left: compact field list */}
          <div style={leftPanel}>
            {sectionGroups.map(sg => (
              <div key={sg.key} style={{ marginBottom: 2 }}>
                <div style={{ padding: '7px 14px', fontSize: '.68rem', fontWeight: 700, color: '#6c757d', background: sg.color.bg, borderBottom: '1px solid #f0f2f5', borderLeft: `3px solid ${sg.color.border}`, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ padding: '2px 7px', borderRadius: 3, fontSize: '.60rem', fontWeight: 600, background: sg.color.badge, color: sg.color.border }}>{sg.label}</span>
                  {sg.rows.length} 字段
                  <span style={{ marginLeft: 'auto', fontSize: '.60rem', fontWeight: 400 }}>{sg.rows.filter(r => confirmedFields.has(r.key)).length}/{sg.rows.length} 确认</span>
                </div>
                {sg.rows.map(row => {
                  const isSelected = row.key === selectedKey
                  const isConfirmed = confirmedFields.has(row.key)
                  const isDoubtful = doubtfulFields.has(row.key)
                  const isEdited = editedValues[row.key] !== undefined
                  const isLow = row.confidence < 0.7
                  const val = displayValue(row)
                  const rowStatus = isEdited ? 'edited' : isDoubtful ? 'doubtful' : isConfirmed ? 'confirmed' : isLow ? 'low' : 'pending'
                  return (
                    <div key={row.key} onClick={() => setSelectedKey(row.key)}
                      style={{
                        display: 'flex', alignItems: 'center', padding: '6px 14px', gap: 8,
                        cursor: 'pointer', borderLeft: `3px solid ${isSelected ? '#4c6ef5' : 'transparent'}`,
                        background: isSelected ? 'rgba(76,110,245,.05)' : isEdited ? 'rgba(124,58,237,.03)' : isDoubtful ? 'rgba(224,49,49,.03)' : isLow ? 'rgba(230,119,0,.02)' : undefined,
                        borderBottom: '1px solid #f8f9fa', fontSize: '.70rem', transition: 'background .1s',
                      }}>
                      <span style={{ width: 8, height: 8, borderRadius: '50%', flexShrink: 0, background: isEdited ? '#7c3aed' : isDoubtful ? '#dc2626' : confidenceColor(row.confidence), opacity: isConfirmed || isDoubtful || isEdited ? 1 : 0.5 }}/>
                      <span style={{ fontWeight: isEdited ? 700 : isLow ? 700 : 600, fontSize: '.68rem', color: isEdited ? '#7c3aed' : isDoubtful ? '#dc2626' : isLow ? '#e67700' : '#495057', minWidth: 70, maxWidth: 90, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{row.label}</span>
                      <span style={{ flex: 1, fontSize: '.68rem', color: val ? '#344054' : '#98a2b3', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', textDecoration: isDoubtful ? 'line-through' : undefined, fontWeight: isEdited ? 600 : undefined }}>{val || '（无）'}</span>
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2, padding: '2px 7px', borderRadius: 10, fontSize: '.60rem', fontWeight: 600, flexShrink: 0, background: isEdited ? '#ede9fe' : isDoubtful ? '#fee2e2' : isLow ? '#ffe3e3' : isConfirmed ? '#d3f9d8' : '#fff3bf', color: isEdited ? '#7c3aed' : isDoubtful ? '#dc2626' : isLow ? '#e03131' : isConfirmed ? '#2b8a3e' : '#e67700' }}>
                        {isEdited ? <><svg width="9" height="9" viewBox="0 0 20 20" style={{display:'inline-block'}}><path d="M13 3l4 4-10 10H3v-4L13 3z" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round"/></svg> 人工修改</> : isDoubtful ? <><IconWarn size={9} /> 存疑</> : isLow ? <><IconWarn size={9} /> 低置信</> : isConfirmed ? <><IconCheck size={9} /> 已确认</> : <><IconWarn size={9} /> 待确认</>}
                      </span>
                    </div>
                  )
                })}
              </div>
            ))}
          </div>

          {/* Right: detail / source */}
          <div style={rightPanel}>
            <div style={rightHeader}>
              <span style={{ fontWeight: 700, fontSize: '.80rem' }}>{selectedRow?.label || '字段详情'}</span>
              <span style={{ padding: '2px 7px', borderRadius: 3, fontSize: '.60rem', fontWeight: 600, background: sectionColors[selectedRow?.section]?.badge || '#f1f3f5', color: sectionColors[selectedRow?.section]?.border || '#6c757d', marginLeft: 8 }}>{sectionOrder[selectedRow?.section] || selectedRow?.section}</span>
              <div style={viewToggle}>
                <button onClick={() => setRightView('detail')} style={{ ...toggleBtn, background: rightView === 'detail' ? '#fff' : 'transparent', color: rightView === 'detail' ? '#344054' : '#6c757d', fontWeight: rightView === 'detail' ? 600 : 400, boxShadow: rightView === 'detail' ? '0 1px 2px rgba(0,0,0,.06)' : undefined }}>
                  <IconClipboard size={13} /> 字段详情
                </button>
                <button onClick={() => setRightView('source')} style={{ ...toggleBtn, background: rightView === 'source' ? '#fff' : 'transparent', color: rightView === 'source' ? '#344054' : '#6c757d', fontWeight: rightView === 'source' ? 600 : 400, boxShadow: rightView === 'source' ? '0 1px 2px rgba(0,0,0,.06)' : undefined }}>
                  <IconDoc size={13} /> 原文出处
                </button>
              </div>
            </div>

            <div data-scroll-container style={{ flex: 1, overflow: 'auto', padding: rightView === 'detail' ? '16px 18px' : 0 }}>
              {rightView === 'detail' ? (
                <div>
                  {!selectedRow ? (
                    <div style={{ textAlign: 'center', padding: 30, color: '#98a2b3', fontSize: '.80rem' }}>选择左侧字段查看详情</div>
                  ) : (
                    <>
                      {/* Field-level source_quote from AI _meta */}
                      {(() => {
                        const fieldMeta = (extraction?.fields || []).find((f: any) =>
                          f.field_name === selectedRow.label || f.field_name === selectedKey.split('.').pop())
                        if (fieldMeta?.source_text) {
                          return (
                            <div style={{ marginBottom: 16 }}>
                              <div style={detailLabel}>AI 原文出处</div>
                              <div style={{ marginTop: 4, padding: '10px 14px', background: '#f8f9fa', border: '1px solid #e9ecef', borderRadius: 6, fontSize: '.68rem', color: '#495057', lineHeight: 1.6, fontStyle: 'italic' }}>
                                「{fieldMeta.source_text}」
                              </div>
                            </div>
                          )
                        }
                        return null
                      })()}
                      <div style={{ marginBottom: 16 }}>
                        <div style={detailLabel}>字段值</div>
                        <div style={{ fontSize: '.90rem', fontWeight: 600, marginTop: 2 }}>
                          <EditableCell value={displayValue(selectedRow)} onChange={v => setEditedValues(prev => ({ ...prev, [selectedRow.key]: v }))} />
                        </div>
                      </div>
                      <div style={{ marginBottom: 16 }}>
                        <div style={detailLabel}>原文摘录</div>
                        <div style={{ marginTop: 4, padding: '12px 16px', background: '#fff', border: '1px solid #e9ecef', borderRadius: 6, fontSize: '.70rem', color: '#6c757d', lineHeight: 1.7 }}>
                          <div style={{ fontWeight: 600, color: '#495057', marginBottom: 2 }}>{selectedRow.label}</div>
                          <EditableCell value={displayValue(selectedRow)} onChange={v => setEditedValues(prev => ({ ...prev, [selectedRow.key]: v }))} />
                          <div style={{ marginTop: 4, fontSize: '.62rem', color: '#98a2b3' }}>
                            — 来自 {extraction.filename}{docType === 'order_form' ? ' · 确定性代码提取' : ' · AI 结构化提取'}
                          </div>
                        </div>
                      </div>
                      <div style={{ marginBottom: 16 }}>
                        <div style={detailLabel}>置信度</div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 4 }}>
                          <div style={{ flex: 1, height: 7, borderRadius: 3, background: '#f0f2f5', overflow: 'hidden' }}>
                            <div style={{ height: '100%', borderRadius: 3, width: `${Math.round(selectedRow.confidence * 100)}%`, background: confidenceColor(selectedRow.confidence), transition: 'width .3s' }}/>
                          </div>
                          <span style={{ fontWeight: 700, fontSize: '.78rem', color: confidenceColor(selectedRow.confidence) }}>{fmtConf(selectedRow.confidence)}</span>
                        </div>
                        <div style={{ fontSize: '.62rem', color: '#98a2b3', marginTop: 3 }}>
                          {selectedRow.confidence >= 0.9 ? '高置信 · 自动确认' : selectedRow.confidence >= 0.7 ? '中置信 · 建议人工确认' : '低置信 · 需强制人工确认'}
                        </div>
                      </div>
                      <div style={{ marginBottom: 12 }}>
                        <div style={detailLabel}>操作</div>
                        <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
                          <button onClick={() => toggleConfirm(selectedRow.key)}
                            style={{ padding: '6px 16px', borderRadius: 6, border: '1px solid #dee2e6', background: confirmedFields.has(selectedRow.key) ? '#d3f9d8' : '#fff', color: confirmedFields.has(selectedRow.key) ? '#2b8a3e' : '#6c757d', fontSize: '.72rem', cursor: 'pointer', fontWeight: 600, fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                            {confirmedFields.has(selectedRow.key) ? <><IconCheck size={13} /> 已确认（点击取消）</> : <><IconWarn size={13} /> 标记为已确认</>}
                          </button>
                          <button onClick={() => toggleDoubtful(selectedRow.key)}
                            style={{ padding: '6px 16px', borderRadius: 6, border: '1px solid #dee2e6', background: doubtfulFields.has(selectedRow.key) ? '#ffe3e3' : '#fff', color: doubtfulFields.has(selectedRow.key) ? '#b91c1c' : '#e03131', fontSize: '.72rem', cursor: 'pointer', fontWeight: 600, fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                            <IconWarn size={13} /> {doubtfulFields.has(selectedRow.key) ? '已标存疑（点击取消）' : '标记存疑'}
                          </button>
                        </div>
                      </div>
                    </>
                  )}
                </div>
              ) : (
                /* Source view — rendered per doc type */
                <div style={{ padding: 14 }}>
                  {docType === 'order_form' && (
                    <RenderOrderFormSource
                      allRows={allRows}
                      selectedKey={selectedKey}
                      displayValue={displayValue}
                      cellMap={extraction.cell_map}
                      cellValues={extraction.cell_values}
                      filename={extraction.filename}
                      setId={setId}
                      docId={extraction.doc_id}
                    />
                  )}
                  {docType === 'final_report' && (
                    <RenderPdfExcerptSource
                      allRows={allRows}
                      selectedKey={selectedKey}
                      selectedRow={selectedRow}
                      displayValue={displayValue}
                      filename={extraction.filename}
                      setId={setId}
                      docId={extraction.doc_id}
                    />
                  )}
                  {docType === 'original_records' && (
                    <RenderRecordsSource
                      allRows={allRows}
                      selectedKey={selectedKey}
                      displayValue={displayValue}
                      filename={extraction.filename}
                      setId={setId}
                      docId={extraction.doc_id}
                    />
                  )}
                  {docType === 'test_standard' && (
                    <div style={{ padding: 20, textAlign: 'center', color: '#98a2b3', fontSize: '.64rem' }}>
                      <IconDoc size={24} /><div style={{ marginTop: 8 }}>测试标准仅提取全文文本，不支持结构化原文定位。</div>
                    </div>
                  )}
                </div>
              )}
            </div>

            {selectedRow && selectedRow.confidence < 0.7 && (
              <div style={{ padding: '10px 14px', background: 'rgba(224,49,49,.06)', borderTop: '1px solid rgba(224,49,49,.15)', fontSize: '.64rem', color: '#e03131', display: 'flex', alignItems: 'center', gap: 6 }}>
                <IconWarn size={14} /><b>低置信度字段</b> — 此字段需强制人工确认，未确认将阻止锁定。
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div style={modalFooter}>
          <span style={{ fontSize: '.68rem', color: '#98a2b3' }}>
            点击左侧字段切换详情 · 右侧面板可编辑和确认 · {confirmedCount}/{totalFields} 已确认
            {lowConfRows.length > 0 && ` · ${lowConfRows.length} 低置信`}
          </span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="pg-btn" onClick={onClose} style={{ fontSize: '.78rem' }}>关闭</button>
            {onSaveOnly && (
              <button className="pg-btn" style={{ fontSize: '.78rem', background: '#fff', color: '#4f46e5', borderColor: '#c7d2fe' }}
                onClick={async () => { try { await onSaveOnly(editedValues); onClose() } catch { /* caller reports the error */ } }}>
                仅保存
              </button>
            )}
            <button className="pg-add-btn" style={{ fontSize: '.78rem', background: confirmedCount === totalFields ? '#2f9e44' : '#e67700' }}
              onClick={async () => { try { await onConfirm(editedValues); onClose() } catch { /* keep modal open */ } }}>
              {confirmedCount === totalFields ? '确认并保存' : '强制确认并保存'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

type PlanFilter = 'all' | 'pending' | 'confirmed' | 'low'

interface PlanItemView {
  index: number
  code: string
  name: string
  isExecuted: string
  testMode: string
  standardClause: string
  acceptance: string
  confidence: number
  details: Record<string, unknown>[]
  evidence: string
}

function buildPlanItems(extraction: DocExtraction): PlanItemView[] {
  const structured = extraction.structured || {}
  const sourceItems = Array.isArray(structured.test_items) ? structured.test_items : []
  const sourceDetails = Array.isArray(structured.test_details) ? structured.test_details : []

  return sourceItems.map((item: Record<string, any>, index: number) => {
    const code = displayText(item.code)
    const name = displayText(item.name) || code || `测试项 ${index + 1}`
    const details = sourceDetails.filter((detail: Record<string, any>) => {
      const detailCode = displayText(detail.code)
      const detailName = displayText(detail.name || detail.test_item)
      return Boolean(
        (code && detailCode === code)
        || (name && detailCode === name)
        || (name && detailName === name)
      )
    })
    const metadataEvidence = (extraction.fields || [])
      .filter(field => !field.field_name.startsWith('__') && field.source_text)
      .filter(field => {
        const haystack = `${field.field_name} ${field.source_text}`
        return Boolean(
          (code && haystack.includes(code))
          || (name && haystack.includes(name))
          || (displayText(item.acceptance) && haystack.includes(displayText(item.acceptance)))
        )
      })
      .map(field => field.source_text)
    const evidence = displayText(item.source_text || item.source_quote || item.evidence)
      || details.map(detail => displayText(detail.source_text || detail.source_quote || detail.evidence)).filter(Boolean).join('\n')
      || Array.from(new Set(metadataEvidence)).join('\n')

    return {
      index,
      code,
      name,
      isExecuted: displayText(item.is_executed),
      testMode: displayText(item.test_mode),
      standardClause: displayText(item.standard_clause),
      acceptance: displayText(item.acceptance),
      confidence: Number(item.confidence) || 0.82,
      details,
      evidence,
    }
  })
}

function TestPlanMatrixReview({
  extraction, label, setId, allRows, selectedKey, setSelectedKey, confirmedFields,
  doubtfulFields, editedValues, setEditedValues, displayValue, toggleConfirm,
  toggleDoubtful, onClose, onConfirm, onSaveOnly,
}: {
  extraction: DocExtraction
  label: string
  setId: string
  allRows: ReviewRow[]
  selectedKey: string
  setSelectedKey: (key: string) => void
  confirmedFields: Set<string>
  doubtfulFields: Set<string>
  editedValues: Record<string, string>
  setEditedValues: React.Dispatch<React.SetStateAction<Record<string, string>>>
  displayValue: (row: ReviewRow) => string
  toggleConfirm: (key: string) => void
  toggleDoubtful: (key: string) => void
  onClose: () => void
  onConfirm: (editedValues: Record<string, string>) => Promise<void> | void
  onSaveOnly?: (editedValues: Record<string, string>) => Promise<void> | void
}) {
  const [filter, setFilter] = useState<PlanFilter>('all')
  const planItems = buildPlanItems(extraction)
  const basicRows = allRows.filter(row => row.section === 'basic')
  const selectedIndexMatch = selectedKey.match(/^item\.(\d+)\./)
  const selectedIndex = selectedIndexMatch ? Number(selectedIndexMatch[1]) : 0
  const activeItem = planItems[selectedIndex] || planItems[0]
  const itemFields = (item: PlanItemView) => [
    { key: `item.${item.index}.name`, label: '测试项', value: item.name },
    { key: `item.${item.index}.is_executed`, label: '是否执行', value: item.isExecuted },
    { key: `item.${item.index}.test_mode`, label: '测试模式', value: item.testMode },
    { key: `item.${item.index}.standard_clause`, label: '标准依据', value: item.standardClause },
    { key: `item.${item.index}.acceptance`, label: '判定要求', value: item.acceptance },
  ]
  const existingRow = (key: string, fallback: string, confidence: number): ReviewRow =>
    allRows.find(row => row.key === key) || { key, label: key, value: fallback, confidence, section: 'items' }
  const fieldValue = (key: string, fallback: string, confidence: number) =>
    editedValues[key] !== undefined ? editedValues[key] : displayValue(existingRow(key, fallback, confidence))
  const itemStatus = (item: PlanItemView) => {
    const present = itemFields(item).filter(field => field.value || editedValues[field.key] !== undefined)
    if (present.some(field => doubtfulFields.has(field.key))) return '存疑'
    if (item.confidence < 0.7) return '低置信'
    if (present.length > 0 && present.every(field => confirmedFields.has(field.key))) return '已确认'
    return '待确认'
  }
  const visibleItems = planItems.filter(item => {
    const status = itemStatus(item)
    return filter === 'all'
      || (filter === 'pending' && status === '待确认')
      || (filter === 'confirmed' && status === '已确认')
      || (filter === 'low' && status === '低置信')
  })
  const selectItem = (item: PlanItemView) => setSelectedKey(`item.${item.index}.name`)
  const confirmItem = (item: PlanItemView) => {
    itemFields(item).forEach(field => {
      if ((field.value || editedValues[field.key] !== undefined) && !confirmedFields.has(field.key)) toggleConfirm(field.key)
    })
  }
  const statusCount = (status: string) => planItems.filter(item => itemStatus(item) === status).length
  const statusStyle = (status: string): React.CSSProperties => ({
    display: 'inline-flex', alignItems: 'center', padding: '2px 8px', borderRadius: 10,
    fontSize: '.60rem', fontWeight: 700, whiteSpace: 'nowrap',
    color: status === '已确认' ? '#2b8a3e' : status === '待确认' ? '#e67700' : '#e03131',
    background: status === '已确认' ? '#d3f9d8' : status === '待确认' ? '#fff3bf' : '#ffe3e3',
  })
  const filterButton = (key: PlanFilter, text: string, count: number) => (
    <button onClick={() => setFilter(key)} style={{
      width: '100%', border: 0, borderBottom: '1px solid #f3f4f6', background: filter === key ? '#eef2ff' : '#fff',
      color: filter === key ? '#4f46e5' : '#475467', padding: '9px 12px', cursor: 'pointer', fontFamily: 'inherit',
      display: 'flex', justifyContent: 'space-between', fontSize: '.68rem', fontWeight: filter === key ? 700 : 500,
    }}><span>{text}</span><span>{count}</span></button>
  )
  const confirmedCount = allRows.filter(row => confirmedFields.has(row.key)).length
  const lowCount = allRows.filter(row => row.confidence < 0.7).length

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-container" onClick={event => event.stopPropagation()} style={{ maxWidth: 1640, width: '97vw', height: '94vh', display: 'flex', flexDirection: 'column' }}>
        <div style={modalHeader}>
          <span style={{ fontWeight: 700, fontSize: '1.05rem' }}>核查{label}</span>
          <span style={{ fontSize: '.68rem', color: '#98a2b3', padding: '2px 8px', background: '#f8f9fa', borderRadius: 4, fontWeight: 600 }}>AI 提取 · 矩阵核查</span>
          <span style={{ fontSize: '.68rem', color: '#98a2b3', marginLeft: 'auto', marginRight: 12 }}>{extraction.filename}</span>
          <button onClick={onClose} style={closeBtn} aria-label="关闭"><IconClose /></button>
        </div>
        <div style={statsBar}>
          <span><b style={{ fontSize: '.94rem' }}>{planItems.length}</b> 测试项</span>
          <span><b style={{ fontSize: '.94rem' }}>{allRows.length}</b> 字段</span>
          <span style={{ color: '#2f9e44' }}><b style={{ fontSize: '.94rem' }}>{confirmedCount}</b> 已确认</span>
          <span style={{ color: '#e67700' }}><b style={{ fontSize: '.94rem' }}>{allRows.length - confirmedCount}</b> 待确认</span>
          {lowCount > 0 && <span style={{ color: '#e03131' }}><b style={{ fontSize: '.94rem' }}>{lowCount}</b> 低置信</span>}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '220px minmax(620px, 1fr) 330px', flex: 1, minHeight: 0 }}>
          <aside style={{ overflow: 'auto', borderRight: '1px solid #e9ecef', background: '#fafbfc' }}>
            <div style={{ padding: '11px 12px', fontSize: '.68rem', fontWeight: 700, color: '#667085', borderBottom: '1px solid #e9ecef' }}>基本信息</div>
            {basicRows.map(row => <div key={row.key} onClick={() => setSelectedKey(row.key)} style={{ padding: '8px 12px', borderBottom: '1px solid #f1f3f5', cursor: 'pointer', background: selectedKey === row.key ? '#eef2ff' : '#fff' }}>
              <div style={{ fontSize: '.60rem', color: '#98a2b3', marginBottom: 2 }}>{row.label}</div>
              <div style={{ fontSize: '.68rem', color: '#344054', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis' }}>{displayValue(row) || '未提供'}</div>
            </div>)}
            <div style={{ padding: '11px 12px', fontSize: '.68rem', fontWeight: 700, color: '#667085', borderBottom: '1px solid #e9ecef' }}>状态筛选</div>
            {filterButton('all', '全部测试项', planItems.length)}
            {filterButton('pending', '待确认', statusCount('待确认'))}
            {filterButton('low', '低置信', statusCount('低置信'))}
            {filterButton('confirmed', '已确认', statusCount('已确认'))}
            <div style={{ padding: '11px 12px', fontSize: '.68rem', fontWeight: 700, color: '#667085', borderBottom: '1px solid #e9ecef' }}>信息完整度</div>
            {[
              ['是否执行', planItems.filter(item => item.isExecuted).length],
              ['测试模式', planItems.filter(item => item.testMode).length],
              ['标准依据', planItems.filter(item => item.standardClause).length],
              ['判定要求', planItems.filter(item => item.acceptance).length],
              ['试验细则', planItems.filter(item => item.details.length).length],
            ].map(([text, count]) => <div key={String(text)} style={{ display: 'flex', justifyContent: 'space-between', padding: '7px 12px', fontSize: '.65rem', color: '#667085' }}><span>{text}</span><span>{count}/{planItems.length}</span></div>)}
          </aside>

          <main style={{ overflow: 'auto', background: '#fff' }}>
            <div style={{ minWidth: 920 }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'minmax(210px,1.25fr) 70px 105px minmax(190px,1fr) minmax(240px,1.35fr) 86px', minHeight: 42, background: '#f8f9fa', borderBottom: '1px solid #dee2e6', position: 'sticky', top: 0, zIndex: 2 }}>
                {['测试项', '执行', '模式', '标准依据', '判定要求', '状态'].map(text => <div key={text} style={{ padding: '10px 12px', fontSize: '.66rem', color: '#667085', fontWeight: 700, borderRight: '1px solid #eef0f2', display: 'flex', alignItems: 'center' }}>{text}</div>)}
              </div>
              {visibleItems.map(item => {
                const selected = activeItem?.index === item.index
                const status = itemStatus(item)
                const cells = [item.name, item.isExecuted, item.testMode, item.standardClause, item.acceptance]
                return <div key={`${item.code}-${item.index}`} onClick={() => selectItem(item)} style={{ display: 'grid', gridTemplateColumns: 'minmax(210px,1.25fr) 70px 105px minmax(190px,1fr) minmax(240px,1.35fr) 86px', minHeight: 58, borderBottom: '1px solid #eef0f2', background: selected ? '#f5f7ff' : '#fff', boxShadow: selected ? 'inset 3px 0 #4f46e5' : undefined, cursor: 'pointer' }}>
                  {cells.map((value, cellIndex) => <div key={cellIndex} style={{ padding: '10px 12px', borderRight: '1px solid #f3f4f6', fontSize: '.66rem', color: value ? '#344054' : '#98a2b3', lineHeight: 1.5, fontWeight: cellIndex === 0 ? 700 : 400, display: 'flex', alignItems: 'center' }}>{fieldValue(itemFields(item)[cellIndex].key, value, item.confidence) || '未提供'}</div>)}
                  <div style={{ padding: '10px 8px', display: 'flex', alignItems: 'center' }}><span style={statusStyle(status)}>{status}</span></div>
                </div>
              })}
              {visibleItems.length === 0 && <div style={{ padding: 40, textAlign: 'center', color: '#98a2b3', fontSize: '.70rem' }}>当前筛选条件下没有测试项</div>}
            </div>
          </main>

          <aside style={{ overflow: 'auto', borderLeft: '1px solid #e9ecef', background: '#fafbfc', padding: 14 }}>
            {activeItem ? <>
              <div style={{ fontSize: '.72rem', fontWeight: 700, color: '#344054', marginBottom: 9 }}>选中测试项详情</div>
              <div style={{ background: '#fff', border: '1px solid #e9ecef', borderRadius: 6, overflow: 'hidden', marginBottom: 14 }}>
                {itemFields(activeItem).map(field => <div key={field.key} onClick={() => setSelectedKey(field.key)} style={{ padding: '9px 11px', borderBottom: '1px solid #f1f3f5', background: selectedKey === field.key ? '#f5f7ff' : '#fff' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '.60rem', color: '#98a2b3', marginBottom: 4 }}><span>{field.label}</span>{confirmedFields.has(field.key) && <span style={{ color: '#2b8a3e' }}>已确认</span>}</div>
                  <div style={{ fontSize: '.68rem', color: '#344054', fontWeight: 600, lineHeight: 1.5 }}>
                    <EditableCell value={fieldValue(field.key, field.value, activeItem.confidence)} onChange={value => setEditedValues(previous => ({ ...previous, [field.key]: value }))} />
                  </div>
                </div>)}
              </div>

              <div style={{ fontSize: '.72rem', fontWeight: 700, color: '#344054', marginBottom: 9 }}>试验细则</div>
              <div style={{ background: '#fff', border: '1px solid #e9ecef', borderRadius: 6, padding: '10px 11px', marginBottom: 14, fontSize: '.66rem', color: '#475467', lineHeight: 1.65 }}>
                {activeItem.details.length > 0 ? activeItem.details.map((detail, index) => <div key={index} style={{ paddingBottom: index < activeItem.details.length - 1 ? 9 : 0, marginBottom: index < activeItem.details.length - 1 ? 9 : 0, borderBottom: index < activeItem.details.length - 1 ? '1px solid #f1f3f5' : undefined }}>
                  {displayText(detail.description) && <div style={{ marginBottom: 4 }}>{displayText(detail.description)}</div>}
                  {displayText(detail.standard_clause) && <div><b>标准依据：</b>{displayText(detail.standard_clause)}</div>}
                  {summarizeFields(detail.fields) && <div>{summarizeFields(detail.fields)}</div>}
                </div>) : <span style={{ color: '#98a2b3' }}>未提取到与该测试项对应的试验细则</span>}
              </div>

              <div style={{ fontSize: '.72rem', fontWeight: 700, color: '#344054', marginBottom: 9 }}>原文证据</div>
              <div style={{ background: '#fff', border: '1px solid #e9ecef', borderRadius: 6, padding: '10px 11px', marginBottom: 14, fontSize: '.66rem', color: activeItem.evidence ? '#475467' : '#98a2b3', lineHeight: 1.65, whiteSpace: 'pre-wrap' }}>{activeItem.evidence || '当前提取结果未返回可定位的原文证据'}</div>
              <button onClick={() => confirmItem(activeItem)} style={{ width: '100%', padding: '8px 12px', border: 0, borderRadius: 6, background: '#4f46e5', color: '#fff', cursor: 'pointer', fontFamily: 'inherit', fontSize: '.70rem', fontWeight: 700, marginBottom: 8 }}>确认选中行全部字段</button>
              <button onClick={() => selectedKey.startsWith(`item.${activeItem.index}.`) && toggleConfirm(selectedKey)} style={{ width: '100%', padding: '8px 12px', border: '1px solid #c7d2fe', borderRadius: 6, background: '#fff', color: '#4f46e5', cursor: 'pointer', fontFamily: 'inherit', fontSize: '.70rem', fontWeight: 700, marginBottom: 8 }}>仅确认当前字段</button>
              <button onClick={() => selectedKey.startsWith(`item.${activeItem.index}.`) && toggleDoubtful(selectedKey)} style={{ width: '100%', padding: '8px 12px', border: '1px solid #fecaca', borderRadius: 6, background: '#fff', color: '#dc2626', cursor: 'pointer', fontFamily: 'inherit', fontSize: '.70rem', fontWeight: 700 }}>标记当前字段存疑</button>
            </> : <div style={{ color: '#98a2b3', textAlign: 'center', paddingTop: 40, fontSize: '.70rem' }}>没有可核查的测试项</div>}
          </aside>
        </div>

        <div style={modalFooter}>
          <span style={{ fontSize: '.65rem', color: '#98a2b3' }}>文档集 {setId} · 点击矩阵行查看并编辑完整信息</span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="pg-btn" onClick={onClose}>关闭</button>
            {onSaveOnly && <button className="pg-btn" style={{ color: '#4f46e5', borderColor: '#c7d2fe' }} onClick={async () => { try { await onSaveOnly(editedValues); onClose() } catch { /* caller reports the error */ } }}>仅保存</button>}
            <button className="pg-add-btn" style={{ background: confirmedCount === allRows.length ? '#2f9e44' : '#e67700' }} onClick={async () => { try { await onConfirm(editedValues); onClose() } catch { /* keep modal open */ } }}>{confirmedCount === allRows.length ? '确认并保存' : '强制确认并保存'}</button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════
// Static styles
// ═══════════════════════════════════════════════════════════════════════════

const closeBtn: React.CSSProperties = { background: 'none', border: 'none', cursor: 'pointer', color: '#667085', padding: '4px', display: 'flex', alignItems: 'center' }
const modalHeader: React.CSSProperties = { padding: '14px 20px', borderBottom: '1px solid #f0f2f5', display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }
const statsBar: React.CSSProperties = { display: 'flex', gap: 18, padding: '10px 20px', background: '#fafbfc', borderBottom: '1px solid #f0f2f5', fontSize: '.72rem', alignItems: 'center', flexShrink: 0 }
const splitBody: React.CSSProperties = { display: 'flex', flex: 1, minHeight: 0, overflow: 'hidden' }
const leftPanel: React.CSSProperties = { width: 480, flexShrink: 0, borderRight: '1px solid #f0f2f5', overflow: 'auto', background: '#fff' }
const rightPanel: React.CSSProperties = { flex: 1, display: 'flex', flexDirection: 'column', background: '#fafbfc', minWidth: 0 }
const rightHeader: React.CSSProperties = { padding: '10px 18px', borderBottom: '1px solid #f0f2f5', display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }
const viewToggle: React.CSSProperties = { display: 'flex', gap: 2, background: '#f0f2f5', borderRadius: 6, padding: 2, marginLeft: 'auto' }
const toggleBtn: React.CSSProperties = { padding: '4px 12px', border: 'none', borderRadius: 4, cursor: 'pointer', fontFamily: 'inherit', fontSize: '.66rem', display: 'inline-flex', alignItems: 'center', gap: 4, transition: 'all .12s' }
const detailLabel: React.CSSProperties = { fontSize: '.66rem', color: '#98a2b3', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.3px' }
const modalFooter: React.CSSProperties = { padding: '12px 20px', borderTop: '1px solid #f0f2f5', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0 }

// ═══════════════════════════════════════════════════════════════════════════
// Data extraction helpers
// ═══════════════════════════════════════════════════════════════════════════

type ReviewRow = { key: string; label: string; value: string; confidence: number; section: string }

function displayText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value.trim()
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

function joinNonEmpty(parts: unknown[], separator = ' | '): string {
  return parts.map(displayText).filter(Boolean).join(separator)
}

function summarizeFields(fields: unknown): string {
  if (!fields || typeof fields !== 'object' || Array.isArray(fields)) return ''
  return Object.entries(fields as Record<string, unknown>)
    .map(([k, v]) => {
      const text = displayText(v)
      return text ? `${k}: ${text}` : ''
    })
    .filter(Boolean)
    .join('；')
}

function buildAllRows(docType: DocType, ext: DocExtraction): ReviewRow[] {
  const rows: ReviewRow[] = []
  const s = ext.structured

  if (!s || Object.keys(s).length === 0) {
    for (const f of ext.fields || []) {
      if (f.field_name !== '__structured__') {
        rows.push({ key: f.field_name, label: f.field_name, value: f.field_value, confidence: f.confidence, section: 'basic' })
      }
    }
    return rows
  }

  if (docType === 'order_form') {
    // Key prefixes must match cell_map keys from OrderFormExtractor.get_cell_map()
    // e.g. "product.name", "applicant.name_cn", "applicant.contact.name",
    //      "test_requirements.test_specification", "other_info.software_version"
    const product = s.product
    if (product) {
      for (const [k, v] of Object.entries(product)) {
        if (v) rows.push({ key: `product.${k}`, label: String(k), value: String(v), confidence: 1.0, section: 'product' })
      }
    }
    const applicant = s.applicant
    if (applicant) {
      for (const [k, v] of Object.entries(applicant)) {
        if (v && typeof v === 'string') rows.push({ key: `applicant.${k}`, label: String(k), value: v, confidence: 1.0, section: 'applicant' })
      }
      if (applicant.contact) {
        for (const [k, v] of Object.entries(applicant.contact)) {
          if (v) rows.push({ key: `applicant.contact.${k}`, label: String(k), value: String(v), confidence: 1.0, section: 'applicant' })
        }
      }
    }
    const manufacturer = s.manufacturer
    if (manufacturer) {
      for (const [k, v] of Object.entries(manufacturer)) {
        if (v && typeof v === 'string') rows.push({ key: `manufacturer.${k}`, label: String(k), value: v, confidence: 1.0, section: 'applicant' })
      }
      if (manufacturer.contact) {
        for (const [k, v] of Object.entries(manufacturer.contact)) {
          if (v) rows.push({ key: `manufacturer.contact.${k}`, label: String(k), value: String(v), confidence: 1.0, section: 'applicant' })
        }
      }
    }
    const factory = s.factory
    if (factory) {
      for (const [k, v] of Object.entries(factory)) {
        if (v && typeof v === 'string') rows.push({ key: `factory.${k}`, label: String(k), value: v, confidence: 1.0, section: 'applicant' })
      }
      if (factory.contact) {
        for (const [k, v] of Object.entries(factory.contact)) {
          if (v) rows.push({ key: `factory.contact.${k}`, label: String(k), value: String(v), confidence: 1.0, section: 'applicant' })
        }
      }
    }
    const tr = s.test_requirements
    if (tr) {
      for (const [k, v] of Object.entries(tr)) {
        if (v) rows.push({ key: `test_requirements.${k}`, label: String(k), value: String(v), confidence: 1.0, section: 'requirements' })
      }
    }
    const oi = s.other_info
    if (oi) {
      for (const [k, v] of Object.entries(oi)) {
        if (v) rows.push({ key: `other_info.${k}`, label: String(k), value: String(v), confidence: 1.0, section: 'other' })
      }
    }
  }

  if (docType === 'test_plan') {
    const bi = s.basic_info
    if (bi && typeof bi === 'object') {
      for (const [k, v] of Object.entries(bi)) {
        if (v) rows.push({ key: `basic.${k}`, label: String(k), value: String(v), confidence: 0.88, section: 'basic' })
      }
    }
    const items = s.test_items
    if (Array.isArray(items)) {
      for (const [idx, item] of items.entries()) {
        const code = displayText(item.code) || displayText(item.name) || `T${idx + 1}`
        const itemName = displayText(item.name)
        const itemFields: Array<[string, string, unknown]> = [
          ['name', '测试项', itemName],
          ['is_executed', '是否执行', item.is_executed],
          ['test_mode', '测试模式', item.test_mode],
          ['standard_clause', '标准依据', item.standard_clause],
          ['acceptance', '判定要求', item.acceptance],
        ]
        for (const [fieldKey, fieldLabel, fieldValue] of itemFields) {
          const value = displayText(fieldValue)
          if (!value) continue
          rows.push({
            key: `item.${idx}.${fieldKey}`,
            label: `${code} · ${fieldLabel}`,
            value,
            confidence: item.confidence || 0.82,
            section: 'items',
          })
        }
      }
    }
    const details = s.test_details
    if (Array.isArray(details)) {
      for (const [idx, d] of details.entries()) {
        const code = displayText(d.code) || `D${idx + 1}`
        const value = joinNonEmpty([
          d.description,
          d.standard_clause ? `依据: ${d.standard_clause}` : '',
          summarizeFields(d.fields),
        ], '；')
        rows.push({ key: `detail.${code}`, label: code, value, confidence: d.confidence || 0.80, section: 'details' })
      }
    }
  }

  if (docType === 'original_records') {
    // BatchResult format: { metas, data_rows, instruments, deduplicated_instruments,
    //   calibration_issues, conclusion_summary, total_files, total_test_items }
    const conclusions = s.conclusion_summary || s.tests || s.test_items || []
    if (Array.isArray(conclusions)) {
      for (const item of conclusions) {
        const code = item.test_item_code || item.code || `T${rows.length}`
        const name = item.test_item_name || item.name || ''
        const allPass = item.all_rounds_pass !== undefined ? (item.all_rounds_pass ? '全部符合' : '存在不符合') : (item.conclusion || '')
        rows.push({ key: `test.${code}`, label: `${code} ${name}`.trim(), value: allPass, confidence: 0.85, section: 'tests' })
      }
    }
    const instruments = s.deduplicated_instruments || s.instruments || s.calibrations || []
    if (Array.isArray(instruments)) {
      for (const inst of instruments) {
        const name = inst.instrument_name || inst.name || inst.model || `I${rows.length}`
        const sn = inst.serial_no || inst.serial_number || inst.sn || ''
        const calEnd = inst.calibration_end || inst.cal_end || inst.calibration_valid_until || ''
        const model = inst.model_number || inst.model || inst.model_no || ''
        rows.push({ key: `inst.${name}`, label: name, value: `${model} SN:${sn} 校准到期:${calEnd}`.trim(), confidence: 0.85, section: 'instruments' })
      }
    }
    const calIssues = s.calibration_issues || []
    if (Array.isArray(calIssues)) {
      for (const ci of calIssues) {
        const key = `cal.${ci.instrument_name || ci.instrument_id || rows.length}`
        rows.push({ key, label: `校准异常 · ${ci.instrument_name || '仪器'}`, value: ci.error_description || ci.issue || ci.message || '校准异常', confidence: 0.9, section: 'calibration' })
      }
    }
    // Summary info — instrument counts with clear meaning
    const rawInsts = s.instruments || []
    const dedupInsts = s.deduplicated_instruments || []
    if (rawInsts.length > 0) {
      rows.push({ key: 'meta.instruments_raw', label: '仪器出现次数 (去重前)', value: String(rawInsts.length), confidence: 1.0, section: 'summary' })
      rows.push({ key: 'meta.instruments_unique', label: '唯一仪器 (去重后)', value: String(dedupInsts.length || rawInsts.length), confidence: 1.0, section: 'summary' })
    }
    if (s.total_files) rows.push({ key: 'meta.total_files', label: '总文件数', value: String(s.total_files), confidence: 1.0, section: 'summary' })
    if (s.total_test_items) rows.push({ key: 'meta.total_items', label: '测试项数', value: String(s.total_test_items), confidence: 1.0, section: 'summary' })
    const metaByFilename = new Map<string, any>()
    for (const meta of Array.isArray(s.metas) ? s.metas : []) {
      if (meta?.filename) metaByFilename.set(String(meta.filename), meta)
    }
    const tableQuality = Array.isArray(s.table_quality) ? s.table_quality : []
    for (const [idx, quality] of tableQuality.entries()) {
      const meta = metaByFilename.get(String(quality.filename || ''))
      const displayName = joinNonEmpty([meta?.test_item_code, meta?.test_item_name], ' ') || `原始记录 ${idx + 1}`
      const expected = quality.expected_rows_by_type || {}
      const accepted = quality.accepted_rows_by_type || {}
      const value = quality.complete
        ? `通过 · 测试数据 ${accepted.test_data || 0}/${expected.test_data || accepted.test_data || 0} · 仪器 ${accepted.instrument || 0}/${expected.instrument || accepted.instrument || 0}`
        : `未通过 · 接纳 ${quality.accepted_rows || 0} 行 · 拒绝 ${quality.rejected_rows || 0} 行`
      rows.push({
        key: `quality.${idx}`,
        label: displayName,
        value,
        confidence: quality.complete ? 1.0 : 0.45,
        section: 'quality',
      })
    }
  }

  if (docType === 'final_report') {
    const ci = s.cover_info || s.cover
    if (ci && typeof ci === 'object') {
      for (const [k, v] of Object.entries(ci)) {
        if (v) rows.push({ key: `cover.${k}`, label: String(k), value: String(v), confidence: 0.88, section: 'cover' })
      }
    }
    const sample = s.sample_info || s.sample
    if (sample && typeof sample === 'object') {
      for (const [k, v] of Object.entries(sample)) {
        if (v) rows.push({ key: `sample.${k}`, label: String(k), value: String(v), confidence: 0.86, section: 'sample' })
      }
    }
    const results = s.test_results || s.results || []
    if (Array.isArray(results)) {
      for (const [idx, r] of results.entries()) {
        const code = displayText(r.code || r.item_code)
        const name = displayText(r.test_item || r.item_name || r.name) || code || `结果${idx + 1}`
        const label = code && code !== name ? `${code} ${name}` : name
        const value = joinNonEmpty([
          r.conclusion || r.result || r.judgment,
          r.test_mode ? `模式: ${r.test_mode}` : '',
          r.standard_requirement ? `标准要求: ${r.standard_requirement}` : '',
          r.grade_requirement ? `等级要求: ${r.grade_requirement}` : '',
        ])
        rows.push({ key: `result.${idx}.${code || name}`, label, value, confidence: r.confidence || 0.86, section: 'results' })
      }
    }
    const insts = s.instruments || s.equipment || []
    const dedupInsts = s.deduplicated_instruments || []
    // Summary of instrument counts
    if (insts.length > 0) {
      rows.push({ key: 'repinst.summary_raw', label: '仪器表条目 (原始)', value: String(insts.length), confidence: 1.0, section: 'instruments' })
      rows.push({ key: 'repinst.summary_unique', label: '唯一仪器 (去重后)', value: String(dedupInsts.length || insts.length), confidence: 1.0, section: 'instruments' })
    }
    if (Array.isArray(insts)) {
      for (const inst of insts) {
        const name = inst.instrument_name || inst.name || inst.model || `I${rows.length}`
        const sn = inst.serial_no || inst.serial_number || inst.sn || ''
        const model = inst.model_number || inst.model || inst.model_no || ''
        const calEnd = inst.calibration_end || inst.cal_end || inst.calibration_valid_until || ''
        rows.push({ key: `repinst.${name}`, label: name, value: `${model} SN:${sn} 校准到期:${calEnd}`.trim(), confidence: inst.confidence || 0.85, section: 'instruments' })
      }
    }
  }

  if (rows.length === 0 && s) {
    for (const [k, v] of Object.entries(s)) {
      if (typeof v === 'string' && v) rows.push({ key: k, label: k, value: v, confidence: 0.80, section: 'basic' })
    }
  }

  return rows
}

interface Section { key: string; label: string; count: number }

function getSections(docType: DocType, ext: DocExtraction): Section[] {
  const rows = buildAllRows(docType, ext)
  const map = new Map<string, number>()
  for (const r of rows) {
    map.set(r.section, (map.get(r.section) || 0) + 1)
  }
  const order: Record<string, string> = {
    product: '产品信息', applicant: '申请方', requirements: '测试要求', other: '其他信息',
    basic: '基本信息', items: '测试项总表', details: '试验细则',
    tests: '测试结论', instruments: '仪器清单', calibration: '校准异常', quality: '提取质量', summary: '汇总信息',
    cover: '封面信息', sample: '样品信息', results: '测试结果',
  }
  return Array.from(map.entries()).map(([key, count]) => ({ key, label: order[key] || key, count }))
}

// ═══════════════════════════════════════════════════════════════════════════
// Source View Renderers — per doc type
// ═══════════════════════════════════════════════════════════════════════════

const sectionLabels: Record<string, string> = {
  product: '产品信息', applicant: '申请方', requirements: '测试要求', other: '其他信息',
  basic: '基本信息', items: '测试项总表', details: '试验细则',
  tests: '测试结论', instruments: '仪器清单', calibration: '校准异常', quality: '提取质量', summary: '汇总信息',
  cover: '封面信息', sample: '样品信息', results: '测试结果',
}

/** .xls order form — mini spreadsheet with all cell values + field highlighting */
function RenderOrderFormSource({ allRows, selectedKey, displayValue, cellMap, cellValues, filename, setId, docId }: {
  allRows: ReviewRow[]; selectedKey: string; displayValue: (r: ReviewRow) => string
  cellMap: Record<string, string> | null; cellValues: Record<string, string> | null; filename: string
  setId: string; docId: string
}) {
  // Build cell→field reverse map (keys now match exactly)
  const cellToField: Record<string, { key: string; label: string }> = {}
  if (cellMap) {
    for (const [ck, cell] of Object.entries(cellMap)) {
      const row = allRows.find(r => r.key === ck)
      if (row) {
        cellToField[cell] = { key: row.key, label: row.label }
      }
    }
  }
  // Find cell for selected field
  let activeCell = ''
  for (const [cell, f] of Object.entries(cellToField)) {
    if (f.key === selectedKey) { activeCell = cell; break }
  }

  // Auto-scroll the active cell into view when field selection changes
  const scrollRef = React.useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!activeCell || !scrollRef.current) return
    const el = document.getElementById(`xls-cell-${activeCell}`)
    if (el) {
      // Use precise container-level scroll instead of scrollIntoView
      // scrollIntoView can scroll the page/modal off-screen in nested overflow containers
      // Must scroll both axes: vertical (scrollTop) AND horizontal (scrollLeft)
      const container = scrollRef.current
      const containerRect = container.getBoundingClientRect()
      const elRect = el.getBoundingClientRect()
      // Vertical: cell at ~30% from top of container
      const targetScrollTop = container.scrollTop + elRect.top - containerRect.top - containerRect.height * 0.3
      // Horizontal: cell at ~25% from left of container (so context columns are visible)
      const targetScrollLeft = container.scrollLeft + elRect.left - containerRect.left - containerRect.width * 0.25
      container.scrollTo({ top: targetScrollTop, left: targetScrollLeft, behavior: 'smooth' })
    }
  }, [activeCell])

  const cols = ['A','B','C','D','E','F','G']
  const totalRows = 35

  return (
    <div>
      <div style={{ padding: '10px 14px', background: 'rgba(76,110,245,.05)', borderRadius: 7, border: '1px solid rgba(76,110,245,.12)', fontSize: '.72rem', color: '#6c757d', lineHeight: 1.7, marginBottom: 10 }}>
        <IconPin size={14} /> <b>电子表格定位：</b>橙色脉冲 = 当前字段，蓝色框 = 同区域关联字段。点击左侧字段自动跳转对应单元格。
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 14px', background: '#fff', borderRadius: '6px 6px 0 0', border: '1px solid #e9ecef', borderBottom: 'none', fontSize: '.70rem' }}>
        <IconDoc size={14} /><b>{filename}</b>
        <span style={{ color: '#98a2b3' }}>· GRGT 模板</span>
        <button
          onClick={() => { if (setId && docId) void openAuthenticatedResource(getDocumentFileUrl(setId, docId)) }}
          title="在新标签页中打开原始上传文件"
          style={{ marginLeft: 'auto', padding: '4px 12px', borderRadius: 4, border: '1px solid #4c6ef5', background: '#fff', color: '#4c6ef5', fontSize: '.66rem', cursor: 'pointer', fontFamily: 'inherit', fontWeight: 600, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <svg width="10" height="10" viewBox="0 0 20 20" style={{ display: 'inline-block' }}><circle cx="10" cy="10" r="3" stroke="currentColor" strokeWidth="1.5" fill="none"/><path d="M2 10s4-7 8-7 8 7 8 7-4 7-8 7-8-7-8-7z" stroke="currentColor" strokeWidth="1.5" fill="none"/></svg> 打开原始文件
        </button>
      </div>
      <div ref={scrollRef} style={{ overflow: 'auto', maxHeight: 540, background: '#fff', border: '1px solid #e9ecef', borderRadius: '0 0 6px 6px' }}>
        <table style={{ borderCollapse: 'collapse', fontSize: '.68rem', width: 'max-content', minWidth: '100%' }}>
          <thead><tr>
            <th style={{ background: '#f5f5f5', color: '#6c757d', fontWeight: 600, fontSize: '.60rem', textAlign: 'center', padding: '6px 10px', border: '1px solid #d5d1c8', minWidth: 32, position: 'sticky', top: 0, zIndex: 2 }}></th>
            {cols.map(c => (
              <th key={c} style={{ background: '#f5f5f5', color: '#6c757d', fontWeight: 600, fontSize: '.60rem', textAlign: 'center', padding: '6px 10px', border: '1px solid #d5d1c8', minWidth: 76, position: 'sticky', top: 0, zIndex: 2 }}>{c}</th>
            ))}
          </tr></thead>
          <tbody>
            {Array.from({ length: totalRows }, (_, i) => i + 1).map(rowNum => (
              <tr key={rowNum}>
                <td style={{ background: '#f5f5f5', color: '#98a2b3', fontWeight: 600, fontSize: '.60rem', textAlign: 'center', padding: '6px 7px', border: '1px solid #d0ccc2', position: 'sticky', left: 0, zIndex: 1, minWidth: 32 }}>{rowNum}</td>
                {cols.map(col => {
                  const cell = `${col}${rowNum}`
                  const isActive = cell === activeCell
                  const fieldInfo = cellToField[cell]
                  const isRelated = !isActive && !!fieldInfo
                  // Use cell_values for display (shows ALL Excel content), field lookup only for highlighting
                  const cellContent = (cellValues && cellValues[cell]) ? cellValues[cell] : ''
                  return (
                    <td id={`xls-cell-${cell}`} key={cell} style={{
                      padding: '5px 8px', border: '1px solid #e0dbd0', minWidth: 68, maxWidth: 200,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      background: isActive ? '#fff9db' : isRelated ? 'rgba(76,110,245,.04)' : cellContent ? undefined : '#fcfcfc',
                      boxShadow: isActive ? 'inset 0 0 0 2.5px #f08c00' : isRelated ? 'inset 0 0 0 1.5px rgba(76,110,245,.25)' : undefined,
                      color: isActive ? '#e67700' : cellContent ? '#2c3e50' : '#d0ccc2',
                      fontWeight: isActive ? 700 : cellContent ? 400 : 300,
                      fontSize: isActive ? '.66rem' : '.62rem',
                      animation: isActive ? 'pulse-cell 2s ease-in-out infinite' : undefined,
                      transition: 'background .15s, box-shadow .15s',
                    }}>{cellContent || (fieldInfo ? '—' : '')}</td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
        <style>{`@keyframes pulse-cell{0%,100%{box-shadow:inset 0 0 0 2.5px #f08c00}50%{box-shadow:inset 0 0 0 5px rgba(240,140,0,.3)}}`}</style>
      </div>
      <div style={{ display: 'flex', gap: 14, padding: '6px 10px', fontSize: '.62rem', color: '#6c757d', marginTop: 4 }}>
        <span><span style={{ display: 'inline-block', width: 11, height: 11, borderRadius: 3, background: '#fff9db', border: '2px solid #f08c00', marginRight: 5, verticalAlign: 'middle' }}></span>当前字段</span>
        <span><span style={{ display: 'inline-block', width: 11, height: 11, borderRadius: 3, background: 'rgba(76,110,245,.04)', border: '1.5px solid rgba(76,110,245,.25)', marginRight: 5, verticalAlign: 'middle' }}></span>关联字段</span>
        <span style={{ marginLeft: 'auto' }}>代码提取 · 坐标 100% 准确 · {cellValues ? Object.keys(cellValues).length : 0} 个有值单元格</span>
      </div>
    </div>
  )
}

/** PDF report / test plan — context excerpt cards */
function RenderPdfExcerptSource({ allRows, selectedKey, selectedRow, displayValue, filename, setId, docId }: {
  allRows: ReviewRow[]; selectedKey: string; selectedRow: ReviewRow
  displayValue: (r: ReviewRow) => string; filename: string
  setId: string; docId: string
}) {
  // Group rows by section for "page" simulation
  const sectionGroups: { section: string; label: string; rows: ReviewRow[] }[] = []
  const seen = new Set<string>()
  for (const r of allRows) {
    if (!seen.has(r.section)) {
      seen.add(r.section)
      const srows = allRows.filter(r2 => r2.section === r.section)
      sectionGroups.push({ section: r.section, label: sectionLabels[r.section] || r.section, rows: srows })
    }
  }

  // Auto-scroll to the section card containing the selected field
  const activeSection = selectedRow?.section || ''
  useEffect(() => {
    if (!activeSection) return
    const el = document.getElementById(`pdf-sec-${activeSection}`)
    if (el) {
      // Use precise container-level scrollTop instead of scrollIntoView
      // scrollIntoView can scroll the page/modal off-screen in nested overflow containers
      const container = el.closest('[data-scroll-container]') as HTMLElement | null
      if (container) {
        const containerRect = container.getBoundingClientRect()
        const elRect = el.getBoundingClientRect()
        const targetScrollTop = container.scrollTop + elRect.top - containerRect.top - containerRect.height * 0.2
        container.scrollTo({ top: targetScrollTop, behavior: 'smooth' })
      }
    }
  }, [activeSection])

  return (
    <div>
      <div style={{ padding: '10px 14px', background: 'rgba(76,110,245,.05)', borderRadius: 7, border: '1px solid rgba(76,110,245,.12)', fontSize: '.66rem', color: '#6c757d', lineHeight: 1.7, marginBottom: 10 }}>
        <IconPin size={14} /> <b>原文摘录：</b>展示 AI 提取时的上下文段落。黄色荧光高亮 = 目标字段。点击左侧字段自动跳转。
      </div>
      {sectionGroups.map((sg, gi) => (
        <div id={`pdf-sec-${sg.section}`} key={sg.section} style={{ border: '1px solid #e9ecef', borderRadius: 8, overflow: 'hidden', marginBottom: 8, background: '#fff' }}>
          <div style={{ padding: '7px 14px', background: '#fafbfc', borderBottom: '1px solid #f0f2f5', fontSize: '.60rem', color: '#6c757d', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6 }}>
            <IconDoc size={13} /> {filename} · <span style={{ fontFamily: 'monospace', color: '#4c6ef5', fontSize: '.58rem' }}>第 {gi + 1} 页</span> · {sg.label}
            <button
              onClick={() => { if (setId && docId) void openAuthenticatedResource(getDocumentFileUrl(setId, docId)) }}
              title="在新标签页中打开原始上传文件"
              style={{ marginLeft: 'auto', padding: '2px 8px', borderRadius: 3, border: '1px solid #4c6ef5', background: '#fff', color: '#4c6ef5', fontSize: '.54rem', cursor: 'pointer', fontFamily: 'inherit', fontWeight: 600 }}>打开</button>
          </div>
          <div style={{ padding: '12px 16px', fontSize: '.64rem', lineHeight: 2, color: '#6c757d', position: 'relative' }}>
            {selectedRow && sg.rows.some(r => r.key === selectedKey) && (
              <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 4, background: '#f08c00', borderRadius: '0 2px 2px 0' }} />
            )}
            {sg.rows.map(r => {
              const val = displayValue(r)
              const isTarget = r.key === selectedKey
              return (
                <div key={r.key}>
                  <span style={{ opacity: isTarget ? 1 : .45 }}>{r.label}: </span>
                  {isTarget ? (
                    <span style={{ background: 'linear-gradient(180deg,transparent 58%,rgba(255,200,40,.5) 58%)', padding: '0 2px', borderRadius: 2, fontWeight: 700, color: '#2c3e50' }}>{val || '（空）'}</span>
                  ) : (
                    <span>{val || '—'}</span>
                  )}
                </div>
              )
            })}
          </div>
          <div style={{ padding: '4px 14px', fontSize: '.54rem', color: '#98a2b3', borderTop: '1px solid #f8f9fa', display: 'flex', justifyContent: 'space-between' }}>
            <span>AI 提取 · {sg.rows.length} 字段</span>
            <span>第 {gi + 1} 页 / 共 {sectionGroups.length} 页</span>
          </div>
        </div>
      ))}
    </div>
  )
}

/** Original records ZIP — file-based excerpt cards */
function RenderRecordsSource({ allRows, selectedKey, displayValue, filename, setId, docId }: {
  allRows: ReviewRow[]; selectedKey: string; displayValue: (r: ReviewRow) => string; filename: string
  setId: string; docId: string
}) {
  // Group by section
  const sectionGroups: { section: string; label: string; rows: ReviewRow[] }[] = []
  const seen = new Set<string>()
  for (const r of allRows) {
    if (!seen.has(r.section)) {
      seen.add(r.section)
      sectionGroups.push({ section: r.section, label: sectionLabels[r.section] || r.section, rows: allRows.filter(r2 => r2.section === r.section) })
    }
  }

  // Auto-scroll to the file card containing the selected field
  const activeSection = allRows.find(r => r.key === selectedKey)?.section || ''
  useEffect(() => {
    if (!activeSection) return
    const el = document.getElementById(`rec-sec-${activeSection}`)
    if (el) {
      // Use precise container-level scrollTop instead of scrollIntoView
      // scrollIntoView can scroll the page/modal off-screen in nested overflow containers
      const container = el.closest('[data-scroll-container]') as HTMLElement | null
      if (container) {
        const containerRect = container.getBoundingClientRect()
        const elRect = el.getBoundingClientRect()
        const targetScrollTop = container.scrollTop + elRect.top - containerRect.top - containerRect.height * 0.2
        container.scrollTo({ top: targetScrollTop, behavior: 'smooth' })
      }
    }
  }, [activeSection])

  return (
    <div>
      <div style={{ padding: '10px 14px', background: 'rgba(76,110,245,.05)', borderRadius: 7, border: '1px solid rgba(76,110,245,.12)', fontSize: '.66rem', color: '#6c757d', lineHeight: 1.7, marginBottom: 10 }}>
        <IconPin size={14} /> <b>原始记录来源：</b>ZIP 包内多文件。橙色高亮 = 当前字段来源文件。点击左侧字段自动跳转。
      </div>
      {sectionGroups.map(sg => {
        const hasSelected = sg.rows.some(r => r.key === selectedKey)
        return (
          <div id={`rec-sec-${sg.section}`} key={sg.section} style={{ border: '1px solid #e9ecef', borderRadius: 8, overflow: 'hidden', marginBottom: 8, background: '#fff' }}>
            <div style={{ padding: '7px 14px', background: hasSelected ? '#fff9db' : '#fafbfc', borderBottom: '1px solid #f0f2f5', fontSize: '.60rem', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6, color: hasSelected ? '#e67700' : '#6c757d' }}>
              <IconDoc size={13} /> {sg.label}_原始记录.pdf
              <span style={{ marginLeft: 'auto', fontSize: '.54rem', color: '#98a2b3' }}>{sg.rows.length} 字段提取</span>
              <button
                onClick={() => { if (setId && docId) void openAuthenticatedResource(getDocumentFileUrl(setId, docId)) }}
                title="在新标签页中打开原始上传文件"
                style={{ padding: '2px 8px', borderRadius: 3, border: '1px solid #4c6ef5', background: '#fff', color: '#4c6ef5', fontSize: '.54rem', cursor: 'pointer', fontFamily: 'inherit', fontWeight: 600 }}>打开</button>
            </div>
            <div style={{ padding: '10px 16px', fontSize: '.64rem', lineHeight: 1.9, color: '#6c757d', fontFamily: 'monospace' }}>
              {sg.rows.map(r => {
                const val = displayValue(r)
                const isTarget = r.key === selectedKey
                return (
                  <div key={r.key} style={{
                    background: isTarget ? 'rgba(255,200,40,.15)' : undefined,
                    padding: isTarget ? '1px 4px' : undefined,
                    borderRadius: 2,
                    fontWeight: isTarget ? 700 : 400,
                    color: isTarget ? '#e67700' : undefined,
                    boxShadow: isTarget ? 'inset 0 0 0 1px #f08c00' : undefined,
                  }}>
                    {r.label}: {val || '—'}
                  </div>
                )
              })}
            </div>
          </div>
        )
      })}
      <div style={{ fontSize: '.56rem', color: '#98a2b3', marginTop: 4 }}>ZIP 包内共 {sectionGroups.length} 个文件 · {allRows.length} 个字段</div>
    </div>
  )
}
