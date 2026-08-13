import { useState, useEffect } from 'react'
import type { DocType } from '../types'
import { getVersionHistory, diffVersions } from '../api'
import { logger } from '../logger'
import Icon from './Icons'

interface Props {
  setId: string
  docType: DocType
  label: string
  onClose: () => void
}

function IconSpinner() {
  return <svg width="14" height="14" viewBox="0 0 24 24" style={{ animation: 'spin 0.7s linear infinite' }}><circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2" fill="none" opacity=".25"/><path d="M12 3a9 9 0 019 9" stroke="currentColor" strokeWidth="2" fill="none"/></svg>
}

export default function VersionHistoryModal({ setId, docType, label, onClose }: Props) {
  const [loading, setLoading] = useState(true)
  const [versions, setVersions] = useState<any[]>([])
  const [error, setError] = useState<string | null>(null)

  // Selected versions for comparison
  const [selA, setSelA] = useState<string>('')
  const [selB, setSelB] = useState<string>('')

  // Tertiary diff modal
  const [diffModal, setDiffModal] = useState(false)
  const [diffs, setDiffs] = useState<any[] | null>(null)
  const [diffLoading, setDiffLoading] = useState(false)

  // Toast notification
  const [toast, setToast] = useState<string | null>(null)
  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(null), 2500) }

  useEffect(() => {
    getVersionHistory(setId, docType).then(data => {
      const vs = data.versions || []
      setVersions(vs)
      setLoading(false)
    }).catch((e: any) => {
      logger.error('Failed to load version history', { component: 'VersionHistoryModal', docType }, e)
      setError('加载版本历史失败'); setLoading(false)
    })
  }, [setId, docType])

  function handleRowClick(docId: string) {
    // If clicking already-selected A, deselect it
    if (selA === docId) { setSelA(''); return }
    // If clicking already-selected B, deselect it
    if (selB === docId) { setSelB(''); return }
    // If A is empty, set as A
    if (!selA) { setSelA(docId); return }
    // If B is empty, set as B
    if (!selB) { setSelB(docId); return }
    // Both are set — replace B
    setSelB(docId)
  }

  async function handleCompare() {
    if (!selA) { showToast('请先选择版本 A（旧版）'); return }
    if (!selB) { showToast('请先选择版本 B（新版）'); return }
    if (selA === selB) { showToast('请选择两个不同的版本进行对比'); return }
    setDiffModal(true)
    setDiffLoading(true)
    setDiffs(null)
    try {
      const result = await diffVersions(setId, selA, selB)
      setDiffs(result.diffs || [])
    } catch (e: any) { logger.error('Diff failed', { component: 'VersionHistoryModal' }, e) }
    finally { setDiffLoading(false) }
  }

  const verTag = (v: any) => v?.doc_version === 1 ? 'Root' : `V${v?.doc_version || '?'}`
  const verAInfo = versions.find(v => v.doc_id === selA)
  const verBInfo = versions.find(v => v.doc_id === selB)
  const changedDiffs = diffs?.filter(d => d.changed) || []
  const canCompare = selA && selB && selA !== selB

  // Row border colors based on selection
  function rowBorder(docId: string) {
    if (docId === selA) return '2px solid #2f9e44'
    if (docId === selB) return '2px solid #4c6ef5'
    return '1px solid transparent'
  }
  function rowBg(docId: string) {
    if (docId === selA) return 'rgba(47,158,68,.06)'
    if (docId === selB) return 'rgba(76,110,245,.06)'
    return 'transparent'
  }

  if (loading) {
    return (
      <div className="modal-overlay" onClick={onClose}>
        <div className="modal-container" onClick={e => e.stopPropagation()} style={{ maxWidth: 560 }}>
          <div style={modalHd}><span style={modalTitle}>版本历史 · {label}</span><button onClick={onClose} style={closeBtn}><Icon name="close" /></button></div>
          <div style={{ padding: 40, textAlign: 'center', color: '#98a2b3', fontSize: '.8rem' }}><IconSpinner /> 加载中...</div>
        </div>
      </div>
    )
  }

  return (
    <>
    {/* Toast */}
    {toast && <div className="notify-toast" role="status" aria-live="polite" style={{ zIndex: 1200 }}>{toast}</div>}

    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-container" onClick={e => e.stopPropagation()}
        style={{ maxWidth: 620, width: '90vw', maxHeight: '88vh', display: 'flex', flexDirection: 'column' }}>

        {/* Header */}
        <div style={modalHd}>
          <span style={modalTitle}>版本历史 · {label}</span>
          <span style={{ fontSize: '.7rem', color: '#98a2b3', fontWeight: 400 }}>
            {versions.length} 个版本
          </span>
          <button onClick={onClose} style={closeBtn}><Icon name="close" /></button>
        </div>

        {/* Body */}
        <div style={{ overflow: 'auto', flex: 1, padding: '16px 20px' }}>
          {error && <div style={{ padding: 16, textAlign: 'center', color: '#e03131', fontSize: '.8rem' }}>{error}</div>}
          {!error && versions.length === 0 && (
            <div style={{ padding: 24, textAlign: 'center', color: '#98a2b3', fontSize: '.8rem' }}>暂无版本历史</div>
          )}

          {/* Version timeline — click to select A/B */}
          {versions.length > 0 && (
            <div>
              <div style={{ fontSize: '.6rem', color: '#6c757d', marginBottom: 10, lineHeight: 1.6 }}>
                点击版本行选择对比目标：<br/>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, marginRight: 14 }}>
                  <span style={{ width: 12, height: 12, borderRadius: 3, background: '#2f9e44', display: 'inline-block' }}></span> 版本 A（旧版）
                </span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                  <span style={{ width: 12, height: 12, borderRadius: 3, background: '#4c6ef5', display: 'inline-block' }}></span> 版本 B（新版）
                </span>
              </div>
              {versions.map((v, i) => {
                const isLatest = i === 0
                const isRoot = v.doc_version === 1
                const selLabel = v.doc_id === selA ? 'A' : v.doc_id === selB ? 'B' : ''
                return (
                  <div key={v.doc_id}
                    onClick={() => handleRowClick(v.doc_id)}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 12,
                      padding: '10px 14px', borderRadius: 8,
                      background: rowBg(v.doc_id),
                      border: rowBorder(v.doc_id),
                      marginBottom: 6, fontSize: '.7rem', cursor: 'pointer',
                      transition: 'border-color .15s, background .15s',
                    }}>
                    {/* Selection indicator */}
                    <span style={{
                      width: 22, height: 22, borderRadius: 5, flexShrink: 0,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontWeight: 800, fontSize: '.56rem',
                      background: selLabel === 'A' ? '#2f9e44' : selLabel === 'B' ? '#4c6ef5' : '#f1f3f5',
                      color: selLabel ? '#fff' : '#98a2b3',
                      border: selLabel ? 'none' : '1px solid #dee2e6',
                    }}>
                      {selLabel || (i + 1)}
                    </span>
                    {/* Version tag */}
                    <span style={{
                      padding: '3px 10px', borderRadius: 12, fontWeight: 700, fontSize: '.58rem', flexShrink: 0,
                      background: isRoot ? '#f3f0ff' : isLatest ? '#d3f9d8' : '#e7f5ff',
                      color: isRoot ? '#7048e8' : isLatest ? '#2b8a3e' : '#1971c2',
                    }}>
                      {verTag(v)}
                    </span>
                    {/* Filename */}
                    <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: isLatest ? 600 : 400 }}>
                      {v.filename}
                    </span>
                    {/* Date */}
                    <span style={{ color: '#98a2b3', fontSize: '.6rem', flexShrink: 0 }}>
                      {v.created_at?.slice(0, 10)}
                    </span>
                    {/* Current badge */}
                    {isLatest && (
                      <span style={{ padding: '2px 8px', borderRadius: 10, fontSize: '.54rem', fontWeight: 600, background: '#d3f9d8', color: '#2b8a3e', flexShrink: 0 }}>
                        当前
                      </span>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Footer — compare button */}
        <div style={{ padding: '12px 20px', borderTop: '1px solid #f0f2f5', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: '.58rem', color: '#98a2b3' }}>
            {selA && selB ? `已选: ${verTag(verAInfo)} vs ${verTag(verBInfo)}` : '请选择两个版本进行对比'}
          </span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="pg-btn" style={{ fontSize: '.68rem' }}
              onClick={handleCompare}>
              对比版本
            </button>
            <button className="pg-btn" style={{ fontSize: '.68rem' }} onClick={onClose}>关闭</button>
          </div>
        </div>
      </div>
    </div>

    {/* ═══════════════════════════════════════════════════════════════════════════ */}
    {/* TERTIARY MODAL: Version Diff                                              */}
    {/* ═══════════════════════════════════════════════════════════════════════════ */}
    {diffModal && (
      <div className="modal-overlay" onClick={() => setDiffModal(false)} style={{ zIndex: 1100 }}>
        <div className="modal-container" onClick={e => e.stopPropagation()}
          style={{ maxWidth: 750, width: '92vw', maxHeight: '84vh', display: 'flex', flexDirection: 'column' }}>

          <div style={modalHd}>
            <span style={modalTitle}>版本对比 · {label}</span>
            <span style={{ fontSize: '.62rem', color: '#98a2b3', fontWeight: 400 }}>
              {verTag(verAInfo)} vs {verTag(verBInfo)}
            </span>
            <span style={{ fontSize: '.54rem', color: '#98a2b3', marginLeft: 'auto', marginRight: 8 }}>三级弹窗</span>
            <button onClick={() => setDiffModal(false)} style={closeBtn}><Icon name="close" /></button>
          </div>

          <div style={{ overflow: 'auto', flex: 1, padding: '16px 20px' }}>
            {diffLoading ? (
              <div style={{ padding: 40, textAlign: 'center', color: '#98a2b3', fontSize: '.8rem' }}>
                <IconSpinner /> 对比中...
              </div>
            ) : diffs && diffs.length === 0 ? (
              <div style={{ padding: 40, textAlign: 'center', color: '#98a2b3', fontSize: '.8rem' }}>
                两个版本完全一致，无差异
              </div>
            ) : diffs ? (
              <div>
                <div style={{ display: 'flex', gap: 12, marginBottom: 12, fontSize: '.6rem', alignItems: 'center' }}>
                  <span style={diffBadge('#2f9e44')}>{changedDiffs.length} 项变更</span>
                  <span style={{ marginLeft: 'auto', fontSize: '.56rem', color: '#98a2b3' }}>
                    {verTag(verAInfo)}（旧）→ {verTag(verBInfo)}（新）
                  </span>
                </div>
                <div style={{ maxHeight: 340, overflow: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.62rem' }}>
                    <thead><tr style={{ background: '#f8f9fa', borderBottom: '2px solid #dee2e6' }}>
                      <th style={th}>字段</th><th style={th}>{verTag(verAInfo)}</th><th style={th}>{verTag(verBInfo)}</th>
                    </tr></thead>
                    <tbody>
                      {changedDiffs.map(d => (
                        <tr key={d.field_name} style={{ borderBottom: '1px solid #f1f3f5', background: 'rgba(47,158,68,.02)' }}>
                          <td style={{ ...td, fontWeight: 600 }}>{d.display_label || d.field_name}</td>
                          <td style={{ ...td, color: '#e03131', textDecoration: 'line-through' }}>{d.old_value || '（空）'}</td>
                          <td style={{ ...td, color: '#2f9e44', fontWeight: 600 }}>{d.new_value || '（空）'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : (
              <div style={{ padding: 40, textAlign: 'center', color: '#e03131', fontSize: '.8rem' }}>
                对比失败，请重试
              </div>
            )}
          </div>

          <div style={{ padding: '12px 20px', borderTop: '1px solid #f0f2f5', display: 'flex', justifyContent: 'flex-end' }}>
            <button className="pg-btn" style={{ fontSize: '.68rem' }} onClick={() => setDiffModal(false)}>关闭</button>
          </div>
        </div>
      </div>
    )}
    </>
  )
}

const closeBtn: React.CSSProperties = { background: 'none', border: 'none', fontSize: 22, cursor: 'pointer', color: '#667085', padding: '6px', lineHeight: 1 }
const modalHd: React.CSSProperties = { padding: '14px 20px', borderBottom: '1px solid #f0f2f5', display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }
const modalTitle: React.CSSProperties = { fontWeight: 700, fontSize: '.9rem' }
const th: React.CSSProperties = { textAlign: 'left', padding: '7px 10px', fontSize: '.58rem', color: '#6c757d', fontWeight: 600 }
const td: React.CSSProperties = { padding: '7px 10px', fontSize: '.62rem' }
function diffBadge(color: string): React.CSSProperties {
  return { padding: '3px 10px', background: `${color}10`, borderRadius: 5, fontWeight: 600, color, fontSize: '.56rem' }
}
