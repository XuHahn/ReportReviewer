import type { TraceSource } from '../types'
import Icon from './Icons'

interface TracePanelProps {
  trace: TraceSource
  sourceValue: string
  onClose: () => void
  onViewFullDoc?: () => void
}

const DOC_TYPE_CONFIG: Record<string, { label: string; color: string }> = {
  order_form:       { label: '委托单',   color: '#16a34a' },
  test_plan:        { label: '试验计划', color: '#dc2626' },
  original_records: { label: '原始记录', color: '#dc2626' },
  final_report:     { label: '检测报告', color: '#dc2626' },
}

function getMethodLabel(trace: TraceSource): string {
  if (trace.method === 'deterministic') {
    if (trace.match_strategy === 'cell_lookup') return '确定性 (单元格定位)'
    if (trace.match_strategy === 'field_name') return '确定性 (字段名匹配)'
    return '确定性提取'
  }
  if (trace.method === 'ai') {
    if (trace.match_strategy === 'field_name') return 'AI 语义提取 (字段匹配)'
    if (trace.match_strategy === 'source_text') return 'AI 语义提取 (原文匹配)'
    return 'AI 语义提取'
  }
  // Fallback: infer from confidence
  if (trace.confidence >= 1.0) return '确定性提取'
  return 'AI 语义提取'
}

export default function TracePanel({ trace, sourceValue, onClose, onViewFullDoc }: TracePanelProps) {
  const dt = DOC_TYPE_CONFIG[trace.doc_type] || { label: trace.doc_type, color: '#788596' }
  const methodLabel = getMethodLabel(trace)
  const confPct = Math.round(trace.confidence * 100)
  const confColor = confPct >= 95 ? '#16a34a' : confPct >= 80 ? '#d97706' : '#b42318'

  return (
    <div style={{
      marginTop: 10, background: '#fff', borderRadius: 10,
      border: '1px solid #e4e7ec', overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        padding: '10px 14px', background: '#f8f9fb', borderBottom: '1px solid #e4e7ec',
        display: 'flex', alignItems: 'center', gap: 10,
      }}>
        <DocIcon docType={trace.doc_type} />
        <span style={{ fontWeight: 700, fontSize: '.65rem', color: '#344054' }}>溯源详情</span>
        <span style={{
          fontSize: '.54rem', color: '#788596', padding: '2px 8px',
          borderRadius: 4, background: '#f0f2f5',
        }}>{dt.label}</span>
        <span style={{ fontSize: '.55rem', color: '#788596', flex: 1 }}>
          {trace.location || trace.path}
        </span>
        <button onClick={onClose} style={{
          cursor: 'pointer', color: '#788596', padding: '2px 6px', borderRadius: 4,
          border: 'none', background: 'transparent', fontSize: '.65rem',
          fontFamily: 'inherit', lineHeight: 1,
        }}><Icon name="close" size={16} /></button>
      </div>

      {/* Body — 2 columns */}
      <div style={{ padding: 14, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        {/* Left: extraction chain */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ fontSize: '.57rem', fontWeight: 600, color: '#475467', display: 'flex', alignItems: 'center', gap: 5 }}>
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><polyline points="9 18 15 12 9 6"/></svg>
            提取链路
          </div>

          {/* Chain steps */}
          <div style={{ fontSize: '.58rem', display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
              <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#0f3460', flexShrink: 0, marginTop: 3 }} />
              <div>
                <span style={{ fontSize: '.53rem', color: '#788596', display: 'block' }}>原始文本</span>
                <span style={{ fontWeight: 600, color: '#344054', fontFamily: 'monospace' }}>{trace.raw_value}</span>
              </div>
            </div>
            <div style={{ paddingLeft: 3, color: '#d0d5dd', fontSize: '.5rem' }}>┃ 归一化 / 解析</div>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
              <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#16a34a', flexShrink: 0, marginTop: 3 }} />
              <div>
                <span style={{ fontSize: '.53rem', color: '#788596', display: 'block' }}>输出值</span>
                <span style={{ fontWeight: 600, color: '#344054', fontFamily: 'monospace', background: '#fef3c7', padding: '1px 4px', borderRadius: 3, display: 'inline-block' }}>{sourceValue}</span>
              </div>
            </div>
          </div>

          {/* Confidence bar */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '.57rem' }}>
            <span style={{ color: '#788596', whiteSpace: 'nowrap' }}>置信度</span>
            <div style={{ flex: 1, height: 4, background: '#e4e7ec', borderRadius: 2, overflow: 'hidden' }}>
              <div style={{ height: '100%', borderRadius: 2, width: `${confPct}%`, background: confColor, transition: 'width .4s ease' }} />
            </div>
            <span style={{ fontWeight: 700, fontSize: '.55rem', color: confColor }}>{confPct}%</span>
          </div>

          {/* Extraction method */}
          <div style={{ fontSize: '.53rem', color: '#788596' }}>
            <span>提取方式：</span>
            <span style={{ fontWeight: 600, color: '#475467' }}>{methodLabel}</span>
          </div>

          {/* Data path */}
          <div style={{ fontSize: '.53rem', color: '#788596' }}>
            <span>数据路径：</span>
            <span style={{ fontWeight: 500, color: '#667085', wordBreak: 'break-all' }}>{trace.path || '—'}</span>
          </div>

          {/* View full doc button */}
          {onViewFullDoc && <button onClick={onViewFullDoc} style={{
            display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: '.55rem',
            color: '#0f3460', cursor: 'pointer', fontWeight: 600,
            border: 'none', background: 'none', padding: '4px 0', fontFamily: 'inherit',
          }}>
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
            查看完整文档 →
          </button>}
        </div>

        {/* Right: metadata card */}
        <div>
          <div style={{ fontSize: '.57rem', fontWeight: 600, color: '#475467', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 5 }}>
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><rect x="2" y="3" width="20" height="18" rx="1"/><line x1="6" y1="8" x2="18" y2="8"/><line x1="6" y1="12" x2="18" y2="12"/><line x1="6" y1="16" x2="12" y2="16"/></svg>
            提取元数据
          </div>
          <div style={{
            background: '#fdfcf8', border: '1px solid #e8e4d8', borderRadius: 6,
            padding: 12, fontSize: '.57rem', lineHeight: 1.7,
          }}>
            <MetaRow label="文档类型" value={dt.label} />
            <MetaRow label="文档位置" value={trace.location || '—'} />
            <MetaRow label="提取路径" value={trace.path || '—'} />
            <MetaRow label="原始提取值" value={trace.raw_value} mono />
            <MetaRow label="置信度" value={`${confPct}%`} />
            <MetaRow label="提取方式" value={methodLabel} />
          </div>
        </div>
      </div>
    </div>
  )
}

function MetaRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div style={{ marginBottom: 4, display: 'flex', gap: 6 }}>
      <span style={{ color: '#788596', flexShrink: 0 }}>{label}：</span>
      <span style={{
        color: '#344054', fontWeight: 500,
        fontFamily: mono ? 'monospace' : undefined,
        wordBreak: 'break-all',
      }}>{value}</span>
    </div>
  )
}

function DocIcon({ docType }: { docType: string }) {
  const color = DOC_TYPE_CONFIG[docType]?.color || '#788596'
  if (docType === 'order_form') {
    return (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
        <rect x="3" y="3" width="18" height="18" rx="2" stroke={color} strokeWidth="1.5"/>
        <line x1="3" y1="9" x2="21" y2="9" stroke={color} strokeWidth="1.2"/>
        <line x1="3" y1="15" x2="21" y2="15" stroke={color} strokeWidth="1.2"/>
        <line x1="9" y1="3" x2="9" y2="21" stroke={color} strokeWidth="1.2"/>
      </svg>
    )
  }
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
      <rect x="4" y="2" width="16" height="20" rx="2" stroke={color} strokeWidth="1.5"/>
      <line x1="8" y1="7" x2="16" y2="7" stroke={color} strokeWidth="1.2"/>
      <line x1="8" y1="10" x2="16" y2="10" stroke={color} strokeWidth="1.2"/>
      <line x1="8" y1="13" x2="12" y2="13" stroke={color} strokeWidth="1.2"/>
    </svg>
  )
}
