import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import DOMPurify from 'dompurify'
import type { UploadResponse } from '../types'
import { downloadExportPdf, downloadExportWord, updateItemAnnotation, getReportDetail, getReportTimeline, getProjectGroups, updateReportTags, listUsers, checkReportUpdates } from '../api'
import { SEV_LABEL, HUMAN_STATUS_LABEL, HUMAN_STATUS_OPTIONS } from '../constants'
import { logger } from '../logger'

const SANITIZE_CFG = {
  ALLOWED_TAGS: [
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'p', 'br', 'hr', 'table', 'thead', 'tbody', 'tr', 'td', 'th', 'caption',
    'ul', 'ol', 'li', 'div', 'span', 'section', 'article',
    'mark', 'sup', 'sub', 'strong', 'em', 'b', 'i', 'u', 's',
    'a', 'img', 'blockquote', 'pre', 'code',
  ],
  ALLOWED_ATTR: ['class', 'id', 'data-error-idx', 'title', 'href', 'src', 'alt', 'colspan', 'rowspan'],
  ALLOW_DATA_ATTR: false,
}

interface Props {
  data: UploadResponse
  onTagsChange?: (reportId: string, newTags: string) => void
  currentUserId?: string
  inModal?: boolean
}


function getResultTooltip(data: UploadResponse): string {
  const items = data.review_items || []
  const stats: Record<string,number> = {}
  items.forEach(i => { const s = i.human_status || 'pending'; stats[s] = (stats[s]||0)+1 })
  const reviewed = (stats.confirmed||0)+(stats.false_positive||0)+(stats.ignored||0)
  const total = items.length
  if (data.overall_result === 'pass') {
    return reviewed === total ? '人工复核通过，所有问题均已处理' : 'AI 初次审核判定通过，未发现问题'
  }
  if (data.overall_result === 'warning') {
    if ((stats.false_positive||0)+(stats.ignored||0) > 0 && (stats.pending||0)+(stats.needs_review||0) === 0) return '所有问题已复核，存在非错误或已忽略条目'
    return '部分问题需关注，仍有待处理项'
  }
  return (stats.pending||0)+(stats.needs_review||0) > 0 ? 'AI 审核发现问题，仍需人工复核' : 'AI 审核判定不通过'
}

const RESULT_LABEL: Record<string, string> = { pass: '审核通过', fail: '审核不通过', warning: '需关注', error: '审核失败' }

export default function ReportReviewer({ data, onTagsChange, currentUserId, inModal }: Props) {
  const [activeErrorIdx, setActiveErrorIdx] = useState<number | null>(null)
  const [annotationLoading, setAnnotationLoading] = useState<number | null>(null)
  const [reportData, setReportData] = useState<UploadResponse>(data)
  const reportDataRef = useRef(reportData)
  reportDataRef.current = reportData
  const [toast, setToast] = useState<string | null>(null)
  const [wordConfirm, setWordConfirm] = useState(false)
  const [showTimeline, setShowTimeline] = useState(false)
  const [timeline, setTimeline] = useState<any[]>([])
  const [timelineLoading, setTimelineLoading] = useState(false)
  const lastSeenRef = useRef(new Date().toISOString())
  const [userNames, setUserNames] = useState<Record<string, string>>({})

  useEffect(() => { listUsers().then(r => { const m: Record<string,string> = {}; (r.users||[]).forEach((u: any) => { m[u.employee_id] = u.name || u.employee_id }); setUserNames(m) }).catch((e: any) => { logger.error('Failed to load user list', { component: 'ReportReviewer' }, e) }) }, [])

  const [groupPickOpen, setGroupPickOpen] = useState(false)
  const [groupPickSel, setGroupPickSel] = useState('')
  const [groupPickSearch, setGroupPickSearch] = useState('')
  const [groupPickCat, setGroupPickCat] = useState('全部')
  const [pickGroups, setPickGroups] = useState<any[]>([])
  const [pickCats, setPickCats] = useState<string[]>(['全部'])
  const [pickCatMap, setPickCatMap] = useState<Map<string, any[]>>(new Map())
  const [groupSaving, setGroupSaving] = useState(false)

  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(null), 3000)
  }

  useEffect(() => {
    setReportData(data)
    setActiveErrorIdx(null)
    lastSeenRef.current = new Date().toISOString()
  }, [data])

  // Re-fetch report data gracefully (for 409 conflict / admin updates)
  const refreshReport = useCallback(async () => {
    try {
      const detail = await getReportDetail(reportData.report_id)
      const fresh: UploadResponse = {
        report_id: detail.id,
        filename: detail.filename,
        overall_result: detail.overall_result as UploadResponse['overall_result'],
        review_items: detail.review_items,
        highlighted_html: detail.highlighted_html,
        created_at: detail.created_at,
        estimated_tokens: 0, token_limit: 0, truncated: false,
        employee_id: detail.employee_id || '',
        tags: detail.tags || '',
        group_id: detail.group_id || '',
        comparison: detail.comparison || '',
        compared_with: detail.compared_with || '',
        original_result: detail.original_result || '',
      }
      setReportData(fresh)
      lastSeenRef.current = new Date().toISOString()
    } catch (e: any) { logger.error('Failed to refresh report', { component: 'ReportReviewer', reportId: reportData.report_id }, e) }
  }, [reportData.report_id])

  const comparisonMap = useMemo(() => {
    const map = new Map<number, string>()
    try {
      const items = JSON.parse(reportData.comparison || '[]')
      const arr = items.items || items
      if (Array.isArray(arr)) arr.forEach((m: any) => { if (m.new_idx != null) map.set(m.new_idx, m.status) })
    } catch (e: any) { logger.error('Failed to parse comparison data', { component: 'ReportReviewer' }, e) }
    return map
  }, [reportData.comparison])

  const { sevCounts, unhighlightedCount, annotationStats } = useMemo(() => {
    const counts: Record<string, number> = { error: 0, warning: 0, info: 0 }
    let unmarked = 0
    const annStats: Record<string, number> = {}
    reportData.review_items.forEach((item) => {
      counts[item.severity] = (counts[item.severity] || 0) + 1
      if (!item.highlighted) unmarked++
      const s = item.human_status || 'pending'
      annStats[s] = (annStats[s] || 0) + 1
    })
    return { sevCounts: counts, unhighlightedCount: unmarked, annotationStats: annStats }
  }, [reportData.review_items])

  const visibleItems = useMemo(() => {
    return reportData.review_items.map((item, origIdx) => ({ item, origIdx })).sort((a, b) => {
      const aSupp = a.item.human_status === 'false_positive' || a.item.human_status === 'ignored'
      const bSupp = b.item.human_status === 'false_positive' || b.item.human_status === 'ignored'
      if (aSupp && !bSupp) return 1
      if (!aSupp && bSupp) return -1
      return 0
    })
  }, [reportData.review_items])

  const [exporting, setExporting] = useState<string | null>(null)

  const handleExport = useCallback(async (format: 'pdf' | 'word') => {
    setExporting(format)
    try {
      if (format === 'pdf') {
        await downloadExportPdf(reportData.report_id)
      } else {
        if (!wordConfirm) { setWordConfirm(true); setExporting(null); return }
        setWordConfirm(false)
        await downloadExportWord(reportData.report_id)
      }
    } catch {
      showToast('导出失败，请重试')
    } finally {
      setExporting(null)
    }
  }, [reportData.report_id, wordConfirm])

  async function openGroupPick() {
    const gs = await getProjectGroups()
    const cats = new Map<string, any[]>()
    cats.set('全部', gs)
    for (const g of gs) {
      const cat = g.category || '其他'
      if (!cats.has(cat)) cats.set(cat, [])
      cats.get(cat)!.push(g)
    }
    setPickGroups(gs)
    setPickCats(Array.from(cats.keys()))
    setPickCatMap(cats)
    setGroupPickSel(reportData.tags || '')
    setGroupPickCat('全部')
    setGroupPickSearch('')
    setGroupPickOpen(true)
  }

  async function handleGroupSave() {
    setGroupSaving(true)
    try {
      await updateReportTags(reportData.report_id, groupPickSel)
      setReportData({ ...reportData, tags: groupPickSel })
      if (onTagsChange) onTagsChange(reportData.report_id, groupPickSel)
      if (showTimeline) {
        try { const r = await getReportTimeline(reportData.report_id); setTimeline(r.entries || []) } catch (e: any) { logger.error('Failed to load timeline after group save', { component: 'ReportReviewer' }, e) }
      }
      setGroupPickOpen(false)
    } catch (e: any) { logger.error('Failed to save group assignment', { component: 'ReportReviewer' }, e) } finally { setGroupSaving(false) }
  }

  const groupPickFiltered = (() => {
    const base = groupPickCat === '全部' ? pickGroups : (pickCatMap.get(groupPickCat) || [])
    if (!groupPickSearch.trim()) return base
    const q = groupPickSearch.toLowerCase()
    return base.filter((g: any) => g.name.toLowerCase().includes(q) || (g.description||'').toLowerCase().includes(q))
  })()

  const handleAnnotation = useCallback(async (idx: number, newStatus: string) => {
    setAnnotationLoading(idx)
    try {
      const cur = reportDataRef.current
      const resp: any = await updateItemAnnotation(cur.report_id, idx, newStatus, undefined, lastSeenRef.current)
      const now = new Date().toISOString()
      const newData = { ...cur }
      newData.review_items = [...cur.review_items]
      newData.review_items[idx] = { ...cur.review_items[idx], human_status: newStatus, annotated_by: currentUserId || '', annotated_at: now }
      if (resp?.new_overall_result) newData.overall_result = resp.new_overall_result
      setReportData(newData)
      lastSeenRef.current = now
      showToast('标注已更新')
    } catch {
      showToast('标注失败，请重试')
    } finally {
      setAnnotationLoading(null)
    }
  }, [reportData.report_id, refreshReport])

  // Poll for updates from other users
  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const data = await checkReportUpdates(reportData.report_id, lastSeenRef.current, currentUserId || '')
        if (data.has_update) {
          await refreshReport()
        }
      } catch (e: any) { logger.error('Failed to poll for updates', { component: 'ReportReviewer' }, e) }
    }, 8000)
    return () => clearInterval(interval)
  }, [reportData.report_id, refreshReport, currentUserId])

  useEffect(() => {
    const marks = document.querySelectorAll<HTMLElement>('.error-highlight')
    marks.forEach((mark) => {
      mark.addEventListener('click', () => {
        const idx = parseInt(mark.dataset.errorIdx || '', 10)
        setActiveErrorIdx(idx)
        const el = document.getElementById(`error-item-${idx}`)
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' })
          el.classList.add('error-flash')
          setTimeout(() => el.classList.remove('error-flash'), 1500)
        }
      })
    })
    return () => {
      marks.forEach((mark) => mark.replaceWith(mark.cloneNode(true)))
    }
  }, [reportData.highlighted_html])

  const handleCardClick = (idx: number, highlighted: boolean) => {
    setActiveErrorIdx(idx)
    if (!highlighted) return
    const mark = document.querySelector(`.error-highlight[data-error-idx="${idx}"]`)
    if (mark) {
      mark.scrollIntoView({ behavior: 'smooth', block: 'center' })
      mark.classList.add('highlight-flash')
      setTimeout(() => mark.classList.remove('highlight-flash'), 1500)
    }
  }

  const total = reportData.review_items.length

  return (
    <div className="review-container" style={inModal ? { display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, overflow: 'hidden' } : undefined}>
      {toast && <div className="notify-toast" role="status" aria-live="polite">{toast}</div>}
      {reportData.truncated && (
        <div className={`truncation-notice ${reportData.overall_result === 'error' ? 'truncation-error' : 'truncation-info'}`}>
          {reportData.overall_result === 'error'
            ? `报告过长（估计 ${reportData.estimated_tokens.toLocaleString()} tokens，上限 ${reportData.token_limit.toLocaleString()} tokens），超出模型上下文窗口，无法审核`
            : `报告超出长度限制，已自动过滤低优先级章节（附件、声明等），核心测试章节已审核`}
        </div>
      )}
      <div className="review-header">
        <div className="review-header-row1">
          <div className="review-header-left">
            <div className={`result-badge result-${reportData.overall_result}`} title={getResultTooltip(reportData)}>
              {RESULT_LABEL[reportData.overall_result] || reportData.overall_result}
            </div>
            <span className="current-filename" title={reportData.filename}>{reportData.filename}</span>
            {reportData.employee_id && (
              <span className="uploader-badge" title="上传者">上传者: {reportData.uploader_name ? `${reportData.uploader_name} (${reportData.employee_id})` : reportData.employee_id}</span>
            )}
          </div>
          <div className="export-buttons">
          <button className="export-btn" onClick={openGroupPick}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="14" height="14"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
            分配项目组
          </button>
          <button className="export-btn" onClick={async () => { setShowTimeline(true); setTimelineLoading(true); try { const r = await getReportTimeline(reportData.report_id); setTimeline(r.entries || []); } catch (e: any) { logger.error('Failed to load timeline', { component: 'ReportReviewer' }, e) } setTimelineLoading(false); }}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="14" height="14">
              <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
            </svg>
            操作记录
          </button>
          <button
            className="export-btn export-btn-pdf"
            onClick={() => handleExport('pdf')}
            disabled={exporting === 'pdf'}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
              width="14" height="14">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
              <polyline points="14 2 14 8 20 8"/>
            </svg>
            {exporting === 'pdf' ? '导出中...' : '导出 PDF'}
          </button>
          <button
            className="export-btn export-btn-word"
            onClick={() => handleExport('word')}
            disabled={exporting === 'word'}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
              width="14" height="14">
              <path d="M4 4h16v16H4z"/>
              <line x1="8" y1="8" x2="16" y2="8"/>
              <line x1="8" y1="12" x2="16" y2="12"/>
              <line x1="8" y1="16" x2="12" y2="16"/>
            </svg>
            {wordConfirm ? '确认下载（仅错误列表）' : exporting === 'word' ? '导出中...' : '导出 Word'}
          </button>
          </div>
        </div>
      </div>

      <div className="review-summary">
        共 {total} 处问题
        <span className="sum-sep" />
        <span className="sum-pill sum-pill-err">错误 {sevCounts.error}</span>
        <span className="sum-pill sum-pill-warn">警告 {sevCounts.warning}</span>
        <span className="sum-pill sum-pill-info">注意 {sevCounts.info}</span>
        {unhighlightedCount > 0 && (
          <span className="sum-pill sum-pill-dim">未定位 {unhighlightedCount}</span>
        )}
        <span className="sum-sep" />
        <span className="sum-pill sum-pill-do">{HUMAN_STATUS_LABEL.confirmed} {annotationStats.confirmed || 0}</span>
        <span className="sum-pill sum-pill-need">{HUMAN_STATUS_LABEL.needs_review} {annotationStats.needs_review || 0}</span>
        <span className="sum-pill sum-pill-dim">{HUMAN_STATUS_LABEL.false_positive} {annotationStats.false_positive || 0}</span>
        <span className="sum-pill sum-pill-dim">{HUMAN_STATUS_LABEL.ignored} {annotationStats.ignored || 0}</span>
        <span className="sum-pill sum-pill-pen">{HUMAN_STATUS_LABEL.pending} {annotationStats.pending || 0}</span>
      </div>

      <div className="review-body" style={inModal ? { maxHeight: 'none', flex: 1, minHeight: 0 } : undefined}>
        <div className="review-report-panel">
          <div className="panel-title">报告原文</div>
          <div
            className="report-preview"
            dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(reportData.highlighted_html, SANITIZE_CFG) }}
          />
        </div>

        <div className="review-errors-panel">
          <div className="panel-title">问题详情 ({visibleItems.length})</div>
          <div className="errors-list">
            {total === 0 ? (
              <div className="no-errors">未发现问题</div>
            ) : visibleItems.length === 0 ? (
              <div className="no-errors">所有问题已标记为通过/忽略，切换"显示全部"查看</div>
            ) : (
              visibleItems.map(({ item, origIdx }) => (
                <div
                  key={origIdx}
                  id={`error-item-${origIdx}`}
                  className={`error-item error-${item.severity} annotation-${item.human_status || 'pending'} ${activeErrorIdx === origIdx ? 'error-active' : ''} ${!item.highlighted ? 'error-unmatched' : ''}`}
                  onClick={() => handleCardClick(origIdx, item.highlighted)}
                >
                  <div className="error-item-header">
                    <span className={`sev-badge sev-badge-${item.severity}`}>
                      {SEV_LABEL[item.severity]}
                    </span>
                    <span className="error-item-index">#{origIdx + 1}</span>
                    {!item.highlighted && (
                      <span className="unmatched-badge">未在原报告中定位</span>
                    )}
                    {comparisonMap.has(origIdx) && (
                      <span className={`comparison-badge comparison-${comparisonMap.get(origIdx)}`}>
                        {comparisonMap.get(origIdx) === 'fixed' ? '已修复' : comparisonMap.get(origIdx) === 'new' ? '新增' : '仍存在'}
                      </span>
                    )}
                    <select
                      className={`annotation-select annotation-select-${item.human_status || 'pending'}`}
                      value={item.human_status || 'pending'}
                      disabled={annotationLoading === origIdx}
                      onChange={(e) => handleAnnotation(origIdx, e.target.value)}
                      onClick={(e) => e.stopPropagation()}
                    >
                      {HUMAN_STATUS_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                    {annotationLoading === origIdx && (
                      <span className="annotation-spinner" />
                    )}
                    {item.annotated_by && (
                      <span style={{fontSize:10,color:'#175cd3',marginLeft:4,fontWeight:500}}>
                        标注者: {userNames[item.annotated_by] || item.annotated_by}
                        {item.annotated_at && ` · ${item.annotated_at.slice(0,16).replace('T',' ')}`}
                      </span>
                    )}
                  </div>
                  <div className="error-location">{item.location}</div>
                  <div>
                    <mark className={`error-highlight-inline error-${item.severity}`}>
                      {item.original_text}
                    </mark>
                  </div>
                  {!item.highlighted && (
                    <div className="unmatched-notice">
                      ⚠ 此段内容在报告原文中未找到精确匹配，请手动核对
                    </div>
                  )}
                  <div className="error-field">
                    <span className="error-field-label">原因</span>
                    <br />{item.error_description}
                  </div>
                  <div className="error-field">
                    <span className="error-field-label">标准依据</span>
                    <br />{item.standard_reference}
                  </div>
                  <div className="error-field">
                    <span className="error-field-label">建议</span>
                    <br />{item.suggestion}
                  </div>
                </div>
              ))
            )}
        </div>
        </div>
      </div>

      {showTimeline && (
        <div className="modal-overlay" onClick={() => setShowTimeline(false)}>
          <div className="modal-container" style={{maxWidth:480}} onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <span className="modal-title">操作记录</span>
              <button onClick={() => setShowTimeline(false)} style={{background:'none',border:'none',fontSize:20,cursor:'pointer',color:'#667085'}} aria-label="关闭">✕</button>
            </div>
            <div className="modal-body" style={{padding:16,maxHeight:'70vh',overflowY:'auto'}}>
              {timelineLoading ? <div style={{textAlign:'center',padding:20,color:'#98a2b3'}}><div className="spinner"/> 加载中...</div>
              : timeline.length === 0 ? <div style={{textAlign:'center',padding:20,color:'#98a2b3'}}>暂无操作记录</div>
              : <div style={{display:'flex',flexDirection:'column',gap:10}}>
                {timeline.map((e,i) => {
                  const dotColor = ACTION_COLORS[e.action] || '#98a2b3'
                  const summary = getActionSummary(e)
                  const name = e.name || e.employee_id || '未知'
                  const initial = name[0]
                  return (
                  <div key={i} style={{display:'flex',gap:8,alignItems:'flex-start'}}>
                    <div style={{width:28,height:28,borderRadius:'50%',background:dotColor,display:'flex',alignItems:'center',justifyContent:'center',fontSize:11,fontWeight:600,color:'#fff',flexShrink:0}}>{initial}</div>
                    <div style={{background:'#f0f2f5',borderRadius:'8px 8px 8px 2px',padding:'8px 12px',flex:1}}>
                      <div style={{fontSize:11,fontWeight:600,color:dotColor,marginBottom:3}}>{name}</div>
                      <div style={{fontSize:12,color:'#344054',lineHeight:1.5}}>{summary}</div>
                      <div style={{fontSize:10,color:'#98a2b3',marginTop:4}}>{e.timestamp?.slice(0,16).replace('T',' ')}</div>
                    </div>
                  </div>
                )})}
              </div>}
            </div>
          </div>
        </div>
      )}
      {groupPickOpen && (
        <div className="modal-overlay" onClick={() => setGroupPickOpen(false)}>
          <div className="modal-container" onClick={e => e.stopPropagation()} style={{maxWidth:520}}>
            <div className="modal-header">
              <span className="modal-title">分配项目组</span>
              <button onClick={() => setGroupPickOpen(false)} style={{background:'none',border:'none',fontSize:20,cursor:'pointer',color:'#667085'}} aria-label="关闭">✕</button>
            </div>
            <div className="modal-body" style={{padding:16}}>
              <input className="filter-input" style={{width:'100%',marginBottom:12}} placeholder="搜索项目组..." value={groupPickSearch} onChange={e => { setGroupPickSearch(e.target.value); setGroupPickCat('全部') }} autoFocus />
              <div style={{display:'flex',gap:10,height:260}}>
                <div style={{width:100,borderRight:'1px solid #f0f2f5',overflowY:'auto',flexShrink:0}}>
                  {pickCats.map(cat => (
                    <div key={cat} onClick={() => setGroupPickCat(cat)}
                      style={{padding:'7px 10px',fontSize:12,cursor:'pointer',borderRadius:6,marginBottom:2,color:groupPickCat===cat?'#b54708':'#667085',background:groupPickCat===cat?'#fff7ed':'transparent',fontWeight:groupPickCat===cat?500:400}}>
                      {cat} <span style={{fontSize:10,color:groupPickCat===cat?'#b54708':'#98a2b3'}}>{(pickCatMap.get(cat)||[]).length}</span>
                    </div>
                  ))}
                </div>
                <div style={{flex:1,overflowY:'auto'}}>
                  <div onClick={() => setGroupPickSel('')} style={{padding:'9px 12px',cursor:'pointer',display:'flex',alignItems:'center',gap:8,fontSize:12,borderBottom:'1px solid #f9fafb',borderRadius:6,background:groupPickSel===''?'#fff7ed':undefined,color:groupPickSel===''?'#b54708':'#667085'}}>
                    <span style={{width:6,height:6,borderRadius:'50%',background:'#b54708',opacity:groupPickSel===''?1:0,flexShrink:0}} /> 未分配
                  </div>
                  {groupPickFiltered.map((g: any) => {
                    const sel = groupPickSel === g.name
                    return (
                      <div key={g.id} onClick={() => setGroupPickSel(g.name)} style={{padding:'9px 12px',cursor:'pointer',display:'flex',alignItems:'center',gap:8,fontSize:12,borderBottom:'1px solid #f9fafb',borderRadius:6,background:sel?'#fff7ed':undefined,color:sel?'#b54708':'#344054'}}>
                        <span style={{width:6,height:6,borderRadius:'50%',background:'#b54708',opacity:sel?1:0,flexShrink:0}} />
                        <div><div style={{fontWeight:sel?600:500}}>{g.name}</div>{g.description&&<div style={{fontSize:10,color:'#98a2b3',marginTop:1}}>{g.description}</div>}</div>
                      </div>
                    )
                  })}
                  {groupPickFiltered.length === 0 && <div style={{padding:20,textAlign:'center',color:'#98a2b3',fontSize:13}}>无匹配</div>}
                </div>
              </div>
            </div>
            <div style={{padding:'12px 16px',borderTop:'1px solid #f0f2f5',display:'flex',justifyContent:'flex-end',gap:8}}>
              <button className="filter-btn" style={{fontSize:12}} onClick={() => setGroupPickOpen(false)}>取消</button>
              <button className="filter-btn" style={{fontSize:12,background:'#b54708',color:'#fff',border:'none'}} disabled={groupSaving} onClick={handleGroupSave}>{groupSaving ? '保存中...' : '确定'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

const STATUS_MAP: Record<string, string> = {
  pending: '待复核', confirmed: '已修正', false_positive: '非错误',
  needs_review: '需复核', ignored: '已忽略',
}

function getActionSummary(e: any): string {
  let detail: any = {}
  try {
    const raw = typeof e.detail === 'string' ? JSON.parse(e.detail) : (e.detail || {})
    if (raw.msg && typeof raw.msg === 'string') detail = JSON.parse(raw.msg)
    else detail = raw
  } catch { detail = {} }
  switch (e.action) {
    case 'upload': case 'upload_stream': case 'upload_batch':
      return `上传报告并完成 AI 审核` + (e.issue_count ? `，发现 ${e.issue_count} 个问题` : '')
    case 'annotate': {
      const prev = STATUS_MAP[detail.previous_status || ''] || detail.previous_status || '待复核'
      const curr = STATUS_MAP[detail.status || ''] || detail.status || '未知'
      return `将条目 #${(detail.item_index ?? '?')} 从「${prev}」改为「${curr}」`
    }
    case 'export_pdf': return '导出 PDF 格式报告'
    case 'export_word': return '导出 Word 格式报告'
    case 'batch_export_excel': return '批量导出 Excel'
    case 'delete_report': return '删除了此报告'
    case 'delete_report_group': return '级联删除了该组所有报告'
    case 'restore_report': return '恢复了此报告'
    case 'update_tags': {
      const oldTags = detail.tags || ''
      return oldTags ? `将项目组改为「${oldTags}」` : '清除了项目组'
    }
    case 'upload_error': return '审核失败'
    case 'upload_rejected': return '上传被拒'
    default: return `${e.action || '未知操作'}`
  }
}

const ACTION_LABELS: Record<string, string> = {
  upload: '报告审核', upload_stream: '流式审核', upload_batch: '批量审核',
  upload_rejected: '上传被拒', upload_error: '审核失败',
  export_pdf: '导出PDF', export_word: '导出Word', batch_export_excel: '批量导出Excel',
  annotate: '标注', delete_report: '删除报告', delete_report_group: '级联删除',
  restore_report: '恢复报告', update_tags: '修改项目组',
}

const ACTION_COLORS: Record<string, string> = {
  upload: '#175cd3', upload_stream: '#2e90fa', upload_batch: '#4361ee',
  annotate: '#7c3aed', export_pdf: '#027a48', export_word: '#2563eb',
  batch_export_excel: '#027a48', delete_report: '#e74c3c', delete_report_group: '#e74c3c',
  restore_report: '#17b26a', upload_error: '#e74c3c', upload_rejected: '#e74c3c',
  update_tags: '#b54708',
}
