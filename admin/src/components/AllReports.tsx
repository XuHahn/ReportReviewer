import React, { useState, useEffect, useCallback, useRef } from 'react'
import DOMPurify from 'dompurify'
import { getReportHistory, getReportDetail, updateItemAnnotation, deleteReport, downloadBatchExcel, getReportTimeline, updateReportTags, getProjectGroups, listUsers, getCurrentUser } from '../api'
import type { ReportRecord, UploadResponse, ReviewItem, AuditLogEntry, ProjectGroup } from '../types'
import { logger } from '../logger'

const DOMPURIFY_CFG = {
  ALLOWED_TAGS: ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'br', 'hr', 'ul', 'ol', 'li',
    'table', 'thead', 'tbody', 'tr', 'td', 'th', 'colgroup', 'col', 'caption',
    'span', 'div', 'strong', 'b', 'em', 'i', 'u', 's', 'sub', 'sup', 'mark',
    'a', 'img', 'pre', 'code', 'blockquote', 'dl', 'dt', 'dd'],
  ALLOWED_ATTR: ['class', 'id', 'data-error-idx', 'title', 'href', 'src', 'alt', 'colspan', 'rowspan'],
}

const RESULT_LABELS: Record<string, string> = { pass: '通过', fail: '不通过', warning: '需关注' }

function resultTooltip(r: any): string {
  const items = r.review_items || []
  const stats: Record<string,number> = {}
  items.forEach((i: any) => { const s = i.human_status || 'pending'; stats[s] = (stats[s]||0)+1 })
  const reviewed = (stats.confirmed||0)+(stats.false_positive||0)+(stats.ignored||0)
  const total = items.length
  if (r.overall_result === 'pass') {
    const orig = r.original_result || ''
    if (orig === 'fail') return '人工复核通过，AI 原始判定为不通过，所有问题均已处理'
    return reviewed === total ? '人工复核通过，所有问题均已处理' : 'AI 初次审核判定通过，未发现问题'
  }
  if (r.overall_result === 'warning') {
    if ((stats.false_positive||0)+(stats.ignored||0) > 0 && (stats.pending||0)+(stats.needs_review||0) === 0) return '所有问题已复核，存在非错误或已忽略条目'
    return '部分问题需关注，仍有待处理项'
  }
  if (r.overall_result === 'fail') return (stats.pending||0)+(stats.needs_review||0) > 0 ? 'AI 审核发现问题，仍需人工复核' : 'AI 审核判定不通过'
  return ''
}

const SEV_LABELS: Record<string, string> = { error: '错误', warning: '警告', info: '注意' }
const HUMAN_STATUS_LABELS: Record<string, string> = {
  pending: '待复核', confirmed: '已修正', false_positive: '非错误',
  needs_review: '需复核', ignored: '已忽略',
}

function useBidirectionalNav(
  containerRef: React.RefObject<HTMLDivElement | null>,
  show: boolean,
) {
  // Click on mark in left panel → scroll to error card in right panel
  useEffect(() => {
    if (!show || !containerRef.current) return
    const container = containerRef.current
    const marks = container.querySelectorAll<HTMLElement>('.error-highlight')
    const handler = (e: Event) => {
      const idx = (e.currentTarget as HTMLElement).dataset.errorIdx
      if (idx == null) return
      const card = container.querySelector(`#error-card-${idx}`)
      if (card) {
        card.scrollIntoView({ behavior: 'smooth', block: 'center' })
        card.classList.add('error-card-flash')
        setTimeout(() => card.classList.remove('error-card-flash'), 1500)
      }
    }
    marks.forEach((m) => m.addEventListener('click', handler))
    return () => marks.forEach((m) => m.removeEventListener('click', handler))
  }, [show, containerRef])
}

export default function AllReports() {
  const [reports, setReports] = useState<ReportRecord[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [keyword, setKeyword] = useState('')
  const [resultFilter, setResultFilter] = useState('')
  const [page, setPage] = useState(0)
  const pageSize = 20

  const [selected, setSelected] = useState<UploadResponse | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
    const [annotatingIdx, setAnnotatingIdx] = useState<number | null>(null)
  const [annotatingItems, setAnnotatingItems] = useState<ReviewItem[]>([])

  // Batch export
  const [exporting, setExporting] = useState(false)
  const [exportIds, setExportIds] = useState<string[]>([])
  const [exportDownloading, setExportDownloading] = useState(false)
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set())
  const [undoToast, setUndoToast] = useState<{idx: number, status: string} | null>(null)
  const [opHistory, setOpHistory] = useState<AuditLogEntry[]>([])
  const [showHistoryModal, setShowHistoryModal] = useState(false)
  const [historyLoading, setHistoryLoading] = useState(false)
  const selectIdRef = useRef('')
  const [modalLoadedAt, setModalLoadedAt] = useState("")
  const [groups, setGroups] = useState<ProjectGroup[]>([])
  const [changingGroup, setChangingGroup] = useState<string | null>(null)
  const [userNames, setUserNames] = useState<Record<string, string>>({})
  const [currentUserId, setCurrentUserId] = useState('')

  useEffect(() => {
    Promise.all([listUsers(), getCurrentUser()]).then(([usersRes, me]) => {
      const m: Record<string,string> = {}
      ;(usersRes.users||[]).forEach((u: any) => { m[u.employee_id] = u.name || u.employee_id })
      setUserNames(m)
      setCurrentUserId(me.employee_id || '')
    }).catch((err) => { logger.error('Failed to fetch users/current user', {}, err) })
  }, [])
  const [groupPickId, setGroupPickId] = useState<string | null>(null)
  const [groupPickSearch, setGroupPickSearch] = useState('')
  const [groupPickSel, setGroupPickSel] = useState('')
  const [groupPickCat, setGroupPickCat] = useState('全部')

  const groupCats = (() => {
    const cats = new Map<string, ProjectGroup[]>()
    cats.set('全部', groups)
    for (const g of groups) {
      const cat = g.category || '其他'
      if (!cats.has(cat)) cats.set(cat, [])
      cats.get(cat)!.push(g)
    }
    return cats
  })()

  const catLabels = Array.from(groupCats.keys())

  const pickFiltered = (() => {
    const base = groupPickCat === '全部' ? groups : (groupCats.get(groupPickCat) || [])
    if (!groupPickSearch.trim()) return base
    const q = groupPickSearch.toLowerCase()
    return base.filter(g => g.name.toLowerCase().includes(q) || (g.description || '').toLowerCase().includes(q))
  })()

  useEffect(() => { getProjectGroups().then(g => setGroups(g || [])).catch((err) => { logger.error('Failed to fetch project groups', {}, err) }) }, [])

  async function handleChangeGroup(reportId: string, newTags: string) {
    setChangingGroup(reportId)
    try {
      await updateReportTags(reportId, newTags)
      const targetGroupId = reports.find(r => r.id === reportId)?.group_id
      setReports(prev => prev.map(r => {
        if (r.id === reportId) return { ...r, tags: newTags }
        if (targetGroupId && r.group_id === targetGroupId) return { ...r, tags: newTags }
        return r
      }))
      if (selected && (selected.report_id === reportId || (selected as any).id === reportId)) {
        setSelected((prev: any) => prev ? { ...prev, tags: newTags } : prev)
      }
      setGroupPickId(null)
    } catch { setError('修改项目组失败') }
    finally { setChangingGroup(null) }
  }

  function openGroupPick(reportId: string, currentTags: string) {
    setGroupPickId(reportId)
    setGroupPickSel(currentTags || '')
    setGroupPickSearch('')
    setGroupPickCat('全部')
  }

  const modalRef = useRef<HTMLDivElement | null>(null)
  useBidirectionalNav(modalRef, selected !== null && !detailLoading)

  const fetchReports = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const params: Record<string, string | number> = { limit: pageSize, offset: page * pageSize }
      if (keyword) params.keyword = keyword
      if (resultFilter) params.overall_result = resultFilter
      const data = await getReportHistory(params)
      setReports(data.reports)
      setTotal(data.total)
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }, [keyword, resultFilter, page])

  useEffect(() => { fetchReports() }, [fetchReports])

  function openHistoryModal(rid: string) {
    setShowHistoryModal(true)
    setHistoryLoading(true)
    getReportTimeline(rid).then(r => setOpHistory(r.entries || [])).catch(() => setOpHistory([])).finally(() => setHistoryLoading(false))
  }

  function parseDetail(entry: any): Record<string, any> {
    try {
      const d = typeof entry.detail === 'string' ? JSON.parse(entry.detail) : entry.detail || {}
      if (d.msg && typeof d.msg === 'string') return JSON.parse(d.msg)
      return d
    } catch { return {} }
  }

  function handleSelect(id: string) {
    selectIdRef.current = id
    setDetailLoading(true)
    setSelected(null)
    getReportDetail(id)
      .then((data: any) => {
        if (selectIdRef.current !== id) return
        const normalized = { ...data, report_id: data.report_id || data.id }
        setSelected(normalized)
        setAnnotatingItems((normalized.review_items || []).map((item: any) => ({ ...item })))
        setModalLoadedAt(new Date().toISOString())
        setShowHistoryModal(false)
        setOpHistory([])
      })
      .catch((err) => setError(err.response?.data?.detail || err.message || '加载详情失败'))
      .finally(() => setDetailLoading(false))
  }

  function closeModal() {
    setSelected(null)
    setAnnotatingItems([])
        setShowHistoryModal(false)
    setOpHistory([])
  }

  function reportId(): string {
    return selected?.report_id || ''
  }

  async function handleDelete() {
    if (!selected) return
    const rid = reportId()
    if (!confirm(`确认删除报告 "${selected.filename}"？此操作不可撤销。`)) return
    try {
      await deleteReport(rid)
      closeModal()
      fetchReports()
    } catch (err: any) {
      alert(err.response?.data?.detail || '删除失败')
    }
  }

  function toggleExport(id: string) {
    setExportIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    )
  }

  async function handleUndo() {
    if (!undoToast) return
    setAnnotatingIdx(undoToast.idx)
    try {
      await updateItemAnnotation(reportId(), undoToast.idx, undoToast.status)
      const newItems = [...annotatingItems]
      newItems[undoToast.idx] = { ...newItems[undoToast.idx], human_status: undoToast.status, annotated_by: currentUserId || 'admin', annotated_at: new Date().toISOString() }
      setAnnotatingItems(newItems)
      setUndoToast(null)
    } catch (err: any) {
      alert('撤销失败')
    } finally {
      setAnnotatingIdx(null)
    }
  }

  async function handleBatchExport() {
    setExportDownloading(true)
    try {
      await downloadBatchExcel(exportIds)
      setExporting(false)
      setExportIds([])
    } catch (e: any) {
      alert(e.message || '导出失败')
    } finally {
      setExportDownloading(false)
    }
  }

  async function handleAnnotation(idx: number, status: string) {
    if (!selected) return
    setAnnotatingIdx(idx)
    try {
      const oldStatus = annotatingItems[idx]?.human_status || 'pending'
      const resp: any = await updateItemAnnotation(reportId(), idx, status, undefined, modalLoadedAt)
      const newItems = [...annotatingItems]
      newItems[idx] = { ...newItems[idx], human_status: status, annotated_by: currentUserId || 'admin', annotated_at: new Date().toISOString() }
      setAnnotatingItems(newItems)
      if (resp?.new_overall_result && selected) {
        setSelected({ ...selected, overall_result: resp.new_overall_result as any })
        setReports((prev) => prev.map((r) =>
          r.id === (selected as any).id || r.id === selected.report_id
            ? { ...r, overall_result: resp.new_overall_result }
            : r
        ))
      }
      if (status !== 'pending') {
        setUndoToast({ idx, status: oldStatus })
        setTimeout(() => setUndoToast(null), 6000)
      }
    } catch (err: any) {
      if (err?.response?.status === 409) {
        alert('报告已被他人修改，请关闭后重新打开')
        closeModal()
      } else {
        alert(err.response?.data?.detail || '标注失败')
      }
    } finally {
      setAnnotatingIdx(null)
    }
  }

  function handleCardClick(idx: number, highlighted: boolean) {
    if (!highlighted) return
    const mark = modalRef.current?.querySelector(`.error-highlight[data-error-idx="${idx}"]`) as HTMLElement | null
    if (mark) {
      mark.scrollIntoView({ behavior: 'smooth', block: 'center' })
      mark.classList.add('highlight-flash')
      setTimeout(() => mark.classList.remove('highlight-flash'), 1500)
    }
  }

  const displayedItems = [...annotatingItems].sort((a, b) => {
    const aSupp = a.human_status === 'false_positive' || a.human_status === 'ignored'
    const bSupp = b.human_status === 'false_positive' || b.human_status === 'ignored'
    if (aSupp && !bSupp) return 1
    if (!aSupp && bSupp) return -1
    return 0
  })

  const totalPages = Math.ceil(total / pageSize)
  const suppressedCount = annotatingItems.filter(
    (i) => i.human_status === 'false_positive' || i.human_status === 'ignored',
  ).length

  return (
    <div>
      <h2 className="section-title">全部报告 {total > 0 && `(${total})`}</h2>

      <div className="log-filters">
        <input
          className="filter-input"
          placeholder="搜索文件名或内容..."
          value={keyword}
          onChange={(e) => { setKeyword(e.target.value); setPage(0) }}
          style={{ minWidth: 200 }}
        />
        <select className="filter-select" value={resultFilter} onChange={(e) => { setResultFilter(e.target.value); setPage(0) }}>
          <option value="">全部结果</option>
          <option value="pass">通过</option>
          <option value="fail">不通过</option>
          <option value="warning">需关注</option>
        </select>
        <button className="filter-btn" onClick={fetchReports}>查询</button>
        <button
          type="button"
          className={`filter-btn ${exporting ? '' : ''}`}
          style={exporting ? { background: '#027a48' } : { background: '#6c757d' }}
          onClick={() => { setExporting(!exporting); setExportIds([]) }}
        >
          {exporting ? '取消导出' : '批量导出'}
        </button>
        {exporting && exportIds.length >= 1 && (
          <button className="filter-btn" style={{ background: '#027a48' }} disabled={exportDownloading} onClick={handleBatchExport}>
            {exportDownloading ? '导出中...' : `导出 Excel (${exportIds.length})`}
          </button>
        )}
      </div>

      {error && <div className="upload-error">{error}</div>}
      {undoToast && (
        <div className="notify-toast" style={{ background: 'linear-gradient(135deg, #b54708, #f59e0b)' }}>
          标注已修改 · <button onClick={handleUndo} style={{ background: 'none', border: 'none', color: '#fff', fontWeight: 700, cursor: 'pointer', textDecoration: 'underline' }}>撤销</button>
        </div>
      )}

      {loading ? (
        <div className="history-loading">加载中...</div>
      ) : reports.length === 0 ? (
        <div className="history-empty">暂无报告数据</div>
      ) : (
        <>
          <table className="user-table">
            <thead>
              <tr>
                {exporting && <th style={{ width: 40 }}>选</th>}
                <th style={{width:24}}></th>
                <th>编号</th>
                <th>文件名</th>
                <th>上传者</th>
                <th>项目组</th>
                <th>结果</th>
                <th>问题数</th>
                <th>时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {(() => {
                // Group reports
                const grouped: Record<string, ReportRecord[]> = {}
                const order: string[] = []
                reports.forEach(r => {
                  const key = r.group_id || r.id
                  if (!grouped[key]) { grouped[key] = []; order.push(key) }
                  grouped[key].push(r)
                })
                // Sort groups by latest created_at
                order.sort((a,b) => {
                  const da = grouped[a].sort((x,y)=>(y.created_at||'').localeCompare(x.created_at||''))[0]?.created_at||''
                  const db = grouped[b].sort((x,y)=>(y.created_at||'').localeCompare(x.created_at||''))[0]?.created_at||''
                  return db.localeCompare(da)
                })
                return order.map(key => {
                  const versions = grouped[key].sort((a,b)=>(b.created_at||'').localeCompare(a.created_at||''))
                  const latest = versions[0]
                  const isGroup = versions.length > 1
                  const expanded = expandedGroups.has(key)
                  const toggle = () => {
                    const next = new Set(expandedGroups)
                    if (next.has(key)) next.delete(key)
                    else next.add(key)
                    setExpandedGroups(next)
                  }
                  return (
                    <React.Fragment key={key}>
                      <tr style={{cursor: isGroup ? 'pointer' : 'default', background: isGroup ? '#fafbfc' : undefined}} onClick={isGroup ? toggle : undefined}>
                        {exporting && <td><input type="checkbox" checked={exportIds.includes(latest.id)} onChange={() => toggleExport(latest.id)} onClick={e=>e.stopPropagation()}/></td>}
                        <td style={{width:24}}>
                          {isGroup && <span style={{fontSize:12,color:'#667085'}}>{expanded ? '▼' : '▶'}</span>}
                        </td>
                        <td style={{fontSize:12,color:'#175cd3',fontWeight:500,whiteSpace:'nowrap'}}>
                          {latest.group_id || '--'}
                          {isGroup && <span style={{marginLeft:4,fontSize:10,background:'#f0f6ff',padding:'1px 5px',borderRadius:8}}>×{versions.length}</span>}
                        </td>
                        <td style={{maxWidth:240,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{latest.filename}</td>
                        <td style={{fontSize:13,color:'#667085'}}>{latest.uploader_name ? `${latest.uploader_name} (${latest.employee_id})` : latest.employee_id||'-'}</td>
                        <td style={{padding:'2px 4px'}} onClick={e => e.stopPropagation()}>
                          {changingGroup === latest.id ? (
                            <span style={{fontSize:11,color:'#98a2b3'}}>保存中...</span>
                          ) : (
                            <div className="tag-display">
                              {latest.tags ? (
                                <span className="group-chip">{latest.tags}</span>
                              ) : (
                                <span className="group-chip-empty" onClick={() => openGroupPick(latest.id, '')}>未分配</span>
                              )}
                              <button className="btn-sm" onClick={() => openGroupPick(latest.id, latest.tags||'')}>改</button>
                            </div>
                          )}
                        </td>
                        <td><span className={`result-badge result-${latest.overall_result||''}`} title={resultTooltip(latest)}>{RESULT_LABELS[latest.overall_result]||latest.overall_result}</span></td>
                        <td>{(()=>{const items=latest.review_items||[];const total=items.length;const reviewed=total-items.filter(i=>!i.human_status||i.human_status==='pending').length;return `${reviewed}/${total}`})()}</td>
                        <td style={{fontSize:12,color:'#98a2b3'}}>{latest.created_at?.slice(0,16)||'-'}</td>
                        <td><button className="filter-btn" style={{padding:'4px 12px',fontSize:12}} onClick={(e)=>{e.stopPropagation();handleSelect(latest.id)}}>查看</button></td>
                      </tr>
                      {isGroup && expanded && versions.map((v,i) => (
                        <tr key={v.id} style={{background:'#f8f9fb'}}>
                          {exporting && <td></td>}
                          <td></td>
                          <td style={{paddingLeft:24,fontSize:12,color:'#667085'}}><span className={`ver-capsule ${!v.compared_with ? 'ver-root' : ''}`}>{!v.compared_with ? 'Root' : `V${versions.length - i}`}</span></td>
                          <td style={{fontSize:12,color:'#667085'}}>{v.filename}</td>
                          <td style={{fontSize:13,color:'#667085'}}>{v.uploader_name ? `${v.uploader_name} (${v.employee_id})` : v.employee_id||'-'}</td>
                          <td style={{padding:'2px 4px'}} onClick={e => e.stopPropagation()}>
                            {changingGroup === v.id ? (
                              <span style={{fontSize:11,color:'#98a2b3'}}>保存中...</span>
                            ) : (
                              <div className="tag-display">
                                {v.tags ? (
                                  <span className="group-chip">{v.tags}</span>
                                ) : (
                                  <span className="group-chip-empty" onClick={() => openGroupPick(v.id, '')}>未分配</span>
                                )}
                                <button className="btn-sm" onClick={() => openGroupPick(v.id, v.tags||'')}>改</button>
                              </div>
                            )}
                          </td>
                          <td><span className={`result-badge result-${v.overall_result||''}`} title={resultTooltip(v)}>{RESULT_LABELS[v.overall_result]||v.overall_result}</span></td>
                          <td>{(()=>{const items=v.review_items||[];const total=items.length;const reviewed=total-items.filter(i=>!i.human_status||i.human_status==='pending').length;return `${reviewed}/${total}`})()}</td>
                          <td style={{fontSize:12,color:'#98a2b3'}}>{v.created_at?.slice(0,16)||'-'}</td>
                          <td><button className="filter-btn" style={{padding:'4px 12px',fontSize:12}} onClick={()=>handleSelect(v.id)}>查看</button></td>
                        </tr>
                      ))}
                    </React.Fragment>
                  )
                })
              })()}
            </tbody>
          </table>

          {totalPages > 1 && (
            <div className="pagination">
              <span className="pagination-info">共 {total} 条，第 {page + 1}/{totalPages} 页</span>
              <div className="pagination-btns">
                <button className="filter-btn" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>上一页</button>
                <button className="filter-btn" disabled={page >= totalPages - 1} onClick={() => setPage((p) => p + 1)}>下一页</button>
              </div>
            </div>
          )}
        </>
      )}

      {/* ====== Modal ====== */}
      {(detailLoading || selected) && (
        <div className="modal-overlay" onClick={closeModal} onKeyDown={(e: any) => { if (e.key === 'Escape') closeModal() }} tabIndex={-1} ref={(el: any) => el?.focus()}>
          <div className="modal-container" onClick={(e) => e.stopPropagation()} ref={modalRef}>
            <div className="modal-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <h3 className="modal-title">
                  {detailLoading ? '加载中...' : selected?.filename}
                </h3>
                {selected && (
                  <span className={`result-badge result-${selected.overall_result || ''}`} title={resultTooltip(selected)}>
                    {RESULT_LABELS[selected.overall_result] || selected.overall_result}
                  </span>
                )}
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                {selected && (
                  <span style={{ fontSize: 12, color: '#667085' }}>
                    问题 {displayedItems.length}/{annotatingItems.length}
                    {suppressedCount > 0 && ` (已抑制 ${suppressedCount})`}
                  </span>
                )}
                {!selected && (
                  <span style={{ fontSize: 12, color: '#667085' }}>
                    问题 {displayedItems.length}/{annotatingItems.length}
                    {suppressedCount > 0 && ` (已抑制 ${suppressedCount})`}
                  </span>
                )}
                <button className="filter-btn" style={{ fontSize: 12 }} onClick={() => selected && openGroupPick(selected.report_id || (selected as any).id, selected.tags||'')}>分配项目组</button>
                <button className="filter-btn" style={{ fontSize: 12 }} onClick={() => selected && openHistoryModal((selected as any).report_id || selected.report_id || (selected as any).id)}>操作历史</button>
                <button className="filter-btn" style={{ fontSize: 12, background: '#e74c3c' }} onClick={handleDelete}>
                  删除
                </button>
                <button className="filter-btn" style={{ fontSize: 12, background: '#6c757d' }} onClick={closeModal} aria-label="关闭">
                  ✕
                </button>
              </div>
            </div>

            <div className="modal-body">
              {detailLoading ? (
                <div className="history-loading">加载报告详情中...</div>
              ) : selected ? (
                <div style={{display:'flex',flex:1,minHeight:0}}>
                  {/* Left: highlighted report */}
                  <div className="review-left">
                    <div
                      className="highlight-content"
                      dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(selected.highlighted_html, DOMPURIFY_CFG) }}
                    />
                  </div>

                  {/* Right: error cards */}
                  <div className="review-right">
                    {displayedItems.length === 0 ? (
                      <div className="history-empty">无审核问题</div>
                    ) : (
                      displayedItems.map((item, _displayIdx) => {
                        const origIdx = annotatingItems.indexOf(item)
                        const status = item.human_status || 'pending'
                        return (
                          <div
                            key={origIdx}
                            id={`error-card-${origIdx}`}
                            className={`error-card error-sev-${item.severity}`}
                            style={{
                              opacity: status === 'false_positive' || status === 'ignored' ? 0.55 : 1,
                              cursor: item.highlighted ? 'pointer' : 'default',
                            }}
                            onClick={() => handleCardClick(origIdx, item.highlighted)}
                          >
                            <div className="error-card-top">
                              <span className="error-card-severity">
                                [{SEV_LABELS[item.severity] || item.severity}] {item.location}
                              </span>
                              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }} onClick={(e) => e.stopPropagation()}>
                                <span className={`annotation-badge annotation-${status}`}>
                                  {HUMAN_STATUS_LABELS[status] || status}
                                </span>
                                <select
                                  className="filter-select"
                                  style={{ fontSize: 11, padding: '2px 4px' }}
                                  value={status}
                                  disabled={annotatingIdx === origIdx}
                                  onChange={(e) => handleAnnotation(origIdx, e.target.value)}
                                >
                                  <option value="pending">待复核</option>
                                  <option value="confirmed">已修正</option>
                                  <option value="false_positive">非错误</option>
                                  <option value="needs_review">需复核</option>
                                  <option value="ignored">已忽略</option>
                                </select>
                                {annotatingIdx === origIdx && <span style={{ fontSize: 11, color: '#667085' }}>...</span>}
                              </div>
                            </div>
                            {!item.highlighted && (
                              <div style={{ fontSize: 11, color: '#f39c12', marginBottom: 6 }}>⚠ 未在原报告中定位</div>
                            )}
                            <div className="error-card-field"><strong>原文：</strong>{item.original_text}</div>
                            <div className="error-card-field"><strong>问题：</strong>{item.error_description}</div>
                            {item.standard_reference && (
                              <div className="error-card-field"><strong>标准：</strong>{item.standard_reference}</div>
                            )}
                            {item.suggestion && (
                              <div className="error-card-field"><strong>建议：</strong>{item.suggestion}</div>
                            )}
                            {item.annotated_by && (
                              <div style={{ fontSize: 10, color: '#175cd3', marginTop: 6, whiteSpace: 'nowrap', fontWeight: 500 }}>
                                标注者: {userNames[item.annotated_by] || item.annotated_by}
                                {item.annotated_at && ` · ${item.annotated_at.slice(0,16).replace('T',' ')}`}
                              </div>
                            )}
                          </div>
                        )
                      })
                    )}
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      )}

      {/* ====== History Modal ====== */}
      {showHistoryModal && (
        <div className="modal-overlay" onClick={() => setShowHistoryModal(false)}>
          <div className="modal-container" onClick={e => e.stopPropagation()} style={{maxWidth:480}}>
            <div className="modal-header">
              <span className="modal-title">操作记录</span>
              <button onClick={() => setShowHistoryModal(false)} style={{background:'none',border:'none',fontSize:20,cursor:'pointer',color:'#667085'}} aria-label="关闭">✕</button>
            </div>
            <div className="modal-body" style={{padding:16,maxHeight:'70vh',overflowY:'auto'}}>
              {historyLoading ? <div className="history-loading">加载中...</div>
              : opHistory.length === 0 ? <div className="history-empty">暂无操作记录</div>
              : <div style={{display:'flex',flexDirection:'column',gap:10}}>
                {opHistory.map((entry:any,i:number) => {
                  const dotColor = (() => {
                    const c: Record<string,string> = {upload:'#175cd3',upload_stream:'#2e90fa',upload_batch:'#4361ee',annotate:'#7c3aed',export_pdf:'#027a48',export_word:'#2563eb',batch_export_excel:'#027a48',delete_report:'#e74c3c',delete_report_group:'#e74c3c',restore_report:'#17b26a',upload_error:'#e74c3c',upload_rejected:'#e74c3c',update_tags:'#b54708'}
                    return c[entry.action]||'#98a2b3'
                  })()
                  const d = parseDetail(entry)
                  const SM: Record<string,string> = {pending:'待复核',confirmed:'已修正',false_positive:'非错误',needs_review:'需复核',ignored:'已忽略'}
                  const summary = (() => {
                    switch (entry.action) {
                      case 'annotate': {
                        const prev = SM[d.previous_status||''] || d.previous_status || '待复核'
                        const curr = SM[d.status||''] || d.status || '未知'
                        return `将条目 #${(d.item_index ?? '?')} 从「${prev}」改为「${curr}」`
                      }
                      case 'update_tags': return d.tags ? `将项目组改为「${d.tags}」` : '清除了项目组'
                      case 'upload': case 'upload_stream': case 'upload_batch': return '上传报告并完成 AI 审核'
                      case 'export_pdf': return '导出 PDF 格式报告'
                      case 'export_word': return '导出 Word 格式报告'
                      case 'batch_export_excel': return '批量导出 Excel'
                      case 'delete_report': return '删除了此报告'
                      case 'delete_report_group': return '级联删除了该组所有报告'
                      case 'restore_report': return '恢复了此报告'
                      default: return entry.action || '未知操作'
                    }
                  })()
                  const name = entry.name || entry.employee_id || '未知'
                  return (
                    <div key={i} style={{display:'flex',gap:8,alignItems:'flex-start'}}>
                      <div style={{width:28,height:28,borderRadius:'50%',background:dotColor,display:'flex',alignItems:'center',justifyContent:'center',fontSize:11,fontWeight:600,color:'#fff',flexShrink:0}}>{name[0]}</div>
                      <div style={{background:'#f0f2f5',borderRadius:'8px 8px 8px 2px',padding:'8px 12px',flex:1}}>
                        <div style={{fontSize:11,fontWeight:600,color:dotColor,marginBottom:3}}>{name}</div>
                        <div style={{fontSize:12,color:'#344054',lineHeight:1.5}}>{summary}</div>
                        <div style={{fontSize:10,color:'#98a2b3',marginTop:4}}>{entry.timestamp?.slice(0,16)||''}</div>
                      </div>
                    </div>
                  )
                })}
              </div>}
            </div>
          </div>
        </div>
      )}

      {/* ====== Group Picker Modal ====== */}
      {groupPickId && (
        <div className="modal-overlay" onClick={() => setGroupPickId(null)}>
          <div className="modal-container" onClick={e => e.stopPropagation()} style={{ maxWidth: 520 }}>
            <div className="modal-header">
              <span className="modal-title">分配项目组</span>
              <button onClick={() => setGroupPickId(null)} style={{ background: 'none', border: 'none', fontSize: 20, cursor: 'pointer', color: '#667085' }} aria-label="关闭">✕</button>
            </div>
            <div className="modal-body" style={{ padding: 16 }}>
              <input className="filter-input" style={{ width: '100%', marginBottom: 12 }} placeholder="搜索项目组名称或描述..." value={groupPickSearch} onChange={e => { setGroupPickSearch(e.target.value); setGroupPickCat('全部') }} autoFocus />
              <div style={{ display: 'flex', gap: 10, height: 280 }}>
                <div style={{ width: 110, borderRight: '1px solid #f0f2f5', overflowY: 'auto', flexShrink: 0 }}>
                  {catLabels.map(cat => (
                    <div
                      key={cat}
                      onClick={() => setGroupPickCat(cat)}
                      style={{
                        padding: '7px 12px', fontSize: 12, cursor: 'pointer', borderRadius: 6, marginBottom: 2,
                        color: groupPickCat === cat ? '#b54708' : '#667085',
                        background: groupPickCat === cat ? '#fff7ed' : 'transparent',
                        fontWeight: groupPickCat === cat ? 500 : 400,
                        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                      }}
                    >
                      {cat}
                      <span style={{
                        fontSize: 10, padding: '1px 6px', borderRadius: 6,
                        background: groupPickCat === cat ? '#ffedd5' : '#f0f2f5',
                        color: groupPickCat === cat ? '#b54708' : '#98a2b3',
                      }}>{groupCats.get(cat)!.length}</span>
                    </div>
                  ))}
                </div>
                <div style={{ flex: 1, overflowY: 'auto' }}>
                  <div
                    onClick={() => setGroupPickSel('')}
                    style={{
                      padding: '9px 12px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8,
                      fontSize: 12, borderBottom: '1px solid #f9fafb', borderRadius: 6,
                      background: groupPickSel === '' ? '#fff7ed' : undefined,
                      color: groupPickSel === '' ? '#b54708' : '#667085',
                    }}
                  >
                    <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#b54708', opacity: groupPickSel === '' ? 1 : 0, flexShrink: 0 }} />
                    未分配
                  </div>
                  {pickFiltered.map(g => {
                    const sel = groupPickSel === g.name
                    return (
                      <div
                        key={g.id}
                        onClick={() => setGroupPickSel(g.name)}
                        style={{
                          padding: '9px 12px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8,
                          fontSize: 12, borderBottom: '1px solid #f9fafb', borderRadius: 6,
                          background: sel ? '#fff7ed' : undefined,
                          color: sel ? '#b54708' : '#344054',
                        }}
                      >
                        <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#b54708', opacity: sel ? 1 : 0, flexShrink: 0 }} />
                        <div>
                          <div style={{ fontWeight: sel ? 600 : 500 }}>{g.name}</div>
                          {g.description && <div style={{ fontSize: 10, color: '#98a2b3', marginTop: 1 }}>{g.description}</div>}
                        </div>
                      </div>
                    )
                  })}
                  {pickFiltered.length === 0 && (
                    <div style={{ padding: 20, textAlign: 'center', color: '#98a2b3', fontSize: 13 }}>无匹配项目组</div>
                  )}
                </div>
              </div>
            </div>
            <div style={{ padding: '12px 16px', borderTop: '1px solid #f0f2f5', display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button className="filter-btn" style={{ fontSize: 12 }} onClick={() => setGroupPickId(null)}>取消</button>
              <button className="filter-btn" style={{ fontSize: 12, background: '#b54708', color: '#fff', border: 'none' }} disabled={changingGroup !== null} onClick={() => handleChangeGroup(groupPickId, groupPickSel)}>确定</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
