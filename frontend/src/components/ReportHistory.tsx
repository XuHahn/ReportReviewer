import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import DOMPurify from 'dompurify'
import { getHistory, searchReports, getReportDetail, downloadBatchExcel, deleteReport } from '../api'
import type { SearchResultItem } from '../api'
import type { ReportRecord, UploadResponse } from '../types'
import ReportReviewer from './ReportReviewer'
import { logger } from '../logger'

interface Props {
  onSelect: (data: UploadResponse) => void
  refreshKey: number
  onCompare?: (ids: string[]) => void
  scope: 'mine' | 'all'
  currentUserId: string
}

const RESULT_OPTIONS = [
  { value: '', label: '全部结论' },
  { value: 'pass', label: '通过' },
  { value: 'fail', label: '不通过' },
  { value: 'warning', label: '需关注' },
  { value: 'error', label: '错误' },
]


function historyTooltip(r: any): string {
  const items = r.review_items || []
  const stats: Record<string,number> = {}
  items.forEach((i: any) => { const s = i.human_status || 'pending'; stats[s] = (stats[s]||0)+1 })
  const reviewed = (stats.confirmed||0)+(stats.false_positive||0)+(stats.ignored||0)
  const total = items.length
  if (r.overall_result === 'pass') {
    if (r.original_result === 'fail') return '人工复核通过，AI 原始判定为不通过，所有问题均已处理'
    return reviewed === total ? '人工复核通过，所有问题均已处理' : 'AI 初次审核判定通过，未发现问题'
  }
  if (r.overall_result === 'warning') {
    if ((stats.false_positive||0)+(stats.ignored||0) > 0 && (stats.pending||0)+(stats.needs_review||0) === 0) return '所有问题已复核，存在非错误或已忽略条目'
    return '部分问题需关注，仍有待处理项'
  }
  return (stats.pending||0)+(stats.needs_review||0) > 0 ? 'AI 审核发现问题，仍需人工复核' : 'AI 审核判定不通过'
}

const RESULT_CLASS: Record<string, string> = {
  pass: 'result-pass',
  fail: 'result-fail',
  warning: 'result-warning',
  error: 'result-fail',
}

const RESULT_LABEL: Record<string, string> = {
  pass: '通过',
  fail: '不通过',
  warning: '需关注',
  error: '错误',
}

export default function ReportHistory({ onSelect, refreshKey, onCompare, scope, currentUserId }: Props) {
  const [isPanelOpen, setIsPanelOpen] = useState(true)
  const [comparing, setComparing] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [exportIds, setExportIds] = useState<string[]>([])
  const [exportingDownload, setExportingDownload] = useState(false)
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set())
  const [modalReport, setModalReport] = useState<UploadResponse | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [records, setRecords] = useState<ReportRecord[]>([])
  const [total, setTotal] = useState(0)
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [keyword, setKeyword] = useState('')
  const [overallResult, setOverallResult] = useState('')
  const today = new Date()
  const weekAgo = new Date(today.getTime() - 7 * 86400000)
  const fmtDate = (d: Date) => d.toISOString().slice(0, 10)
  const [dateFrom, setDateFrom] = useState(fmtDate(weekAgo))
  const [dateTo, setDateTo] = useState(fmtDate(today))
  const [tagFilter, setTagFilter] = useState('')
  const [searchMode, setSearchMode] = useState(false)
  const [searchResults, setSearchResults] = useState<SearchResultItem[]>([])
  const [searchTotal, setSearchTotal] = useState(0)
  const debounceRef = useRef<ReturnType<typeof setTimeout>>()

  const fetchHistory = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const res = await getHistory({
        limit: 50,
        keyword: keyword || undefined,
        overall_result: overallResult || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        employee_id: scope === 'mine' ? currentUserId : undefined,
        tag: tagFilter || undefined,
      })
      setRecords(res.reports)
      setTotal(res.total)
    } catch (e: any) {
      setError(e?.message || '加载历史记录失败')
      setRecords([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }, [keyword, overallResult, dateFrom, dateTo, scope, currentUserId, tagFilter])

  useEffect(() => {
    if (!isPanelOpen) return
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => fetchHistory(), 300)
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current) }
  }, [isPanelOpen, refreshKey, fetchHistory])

  // Group by group_id for fold display
  const groupedRecords = useMemo(() => {
    if (searchMode) return null
    const groups: Record<string, typeof records> = {}
    const order: string[] = []
    records.forEach(r => {
      const key = r.group_id || r.id
      if (!groups[key]) { groups[key] = []; order.push(key) }
      groups[key].push(r)
    })
    return order.map(key => ({
      key,
      versions: groups[key].sort((a,b) => (b.created_at||'').localeCompare(a.created_at||'')),
      latest: groups[key].reduce((a,b) => (a.created_at||'') > (b.created_at||'') ? a : b),
      count: groups[key].length,
    }))
  }, [records, searchMode])

  const doFtsSearch = useCallback(async () => {
    if (!keyword.trim()) return
    setLoading(true)
    setError('')
    try {
      const res = await searchReports({ q: keyword, limit: 50 })
      setSearchResults(res.results)
      setSearchTotal(res.total)
      setRecords([])
      setTotal(0)
    } catch (e: any) {
      setError(e?.message || '搜索失败')
      setSearchResults([])
      setSearchTotal(0)
    } finally {
      setLoading(false)
    }
  }, [keyword])

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    if (searchMode) {
      doFtsSearch()
    } else {
      fetchHistory()
    }
  }

  const handleSearchSelect = async (sr: SearchResultItem) => {
    try {
      const detail = await getReportDetail(sr.report_id)
      const data: UploadResponse = {
        report_id: detail.id,
        filename: detail.filename,
        overall_result: detail.overall_result as UploadResponse['overall_result'],
        review_items: detail.review_items,
        highlighted_html: detail.highlighted_html,
        created_at: detail.created_at,
        estimated_tokens: 0,
        token_limit: 0,
        truncated: false,
        employee_id: sr.employee_id || detail.employee_id || '',
        tags: detail.tags || '',
        group_id: detail.group_id || '',
        comparison: detail.comparison || '',
        compared_with: detail.compared_with || '',
        original_result: detail.original_result || '',
      }
      setModalReport(data)
    } catch (e: any) {
      setError(e?.message || '加载报告详情失败')
    }
  }

  const [compareToast, setCompareToast] = useState<string | null>(null)
  const showCompareToast = (msg: string) => { setCompareToast(msg); setTimeout(() => setCompareToast(null), 2500) }

  const toggleCompare = (id: string) => {
    setSelectedIds((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id)
      if (prev.length >= 2) { showCompareToast('已替换最早的对比项（最多2份）'); return [prev[1], id] }
      return [...prev, id]
    })
  }

  const toggleExport = (id: string) => {
    setExportIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    )
  }

  const handleDelete = async () => {
    if (!modalReport) return
    const isRoot = !modalReport.compared_with && !!(modalReport.group_id)
    const msg = isRoot
      ? '此报告为该组的原始版本，删除将同时删除该组下所有相关报告，确定删除？'
      : '确定删除此报告？'
    if (!window.confirm(msg)) return
    setDeleting(true)
    try {
      await deleteReport(modalReport.report_id, isRoot)
      setModalReport(null)
      fetchHistory()
    } catch (e: any) {
      alert(e?.response?.data?.detail || e?.message || '删除失败')
    } finally {
      setDeleting(false)
    }
  }

  const handleBatchExport = async () => {
    setExportingDownload(true)
    try {
      await downloadBatchExcel(exportIds)
      setExporting(false)
      setExportIds([])
    } catch (e: any) {
      alert(e.message || '导出失败')
    } finally {
      setExportingDownload(false)
    }
  }

  const handleSelect = async (record: ReportRecord) => {
    try {
      const detail = await getReportDetail(record.id)
      const data: UploadResponse = {
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
      setModalReport(data)
    } catch (e: any) {
      logger.error('Failed to load report detail', { component: 'ReportHistory', reportId: record.id }, e)
      setError('加载报告详情失败')
    }
  }

  return (
    <div className="history-panel">
      <button
        className="history-toggle"
        onClick={() => setIsPanelOpen(!isPanelOpen)}
      >
        <span>{scope === 'mine' ? '我的报告' : '全部报告'} {total > 0 && `(${total})`}</span>
        <svg
          className={`history-chevron ${isPanelOpen ? 'chevron-open' : ''}`}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {isPanelOpen && (
        <div className="history-body">
          <div className="search-mode-toggle">
              <button type="button"
                className={`mode-btn ${!searchMode && !comparing ? 'mode-btn-active' : ''}`}
                onClick={() => { setSearchMode(false); setSearchResults([]); setSearchTotal(0); setComparing(false); setSelectedIds([]) }}>
                文件名
              </button>
              <button type="button"
                className={`mode-btn ${searchMode ? 'mode-btn-active' : ''}`}
                onClick={() => { setSearchMode(true); setRecords([]); setTotal(0); setComparing(false); setSelectedIds([]) }}>
                全文检索
              </button>
              <button type="button"
                className={`mode-btn ${comparing ? 'mode-btn-active' : ''}`}
                onClick={() => { setComparing(!comparing); setSelectedIds([]); setExporting(false); setExportIds([]); setSearchMode(false); setSearchResults([]); setSearchTotal(0) }}>
                对比报告
              </button>
              <button type="button"
                className={`mode-btn ${exporting ? 'mode-btn-active' : ''}`}
                onClick={() => { setExporting(!exporting); setExportIds([]); setComparing(false); setSelectedIds([]); setSearchMode(false); setSearchResults([]); setSearchTotal(0) }}>
                批量导出
              </button>
            </div>

          <form className="history-filters" onSubmit={handleSearch}>
            <input
              className="filter-input"
              type="text"
              placeholder={searchMode ? "全文检索关键词..." : "搜索文件名..."}
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
            />
            {!searchMode && (
              <>
                <select
                  className="filter-select"
                  value={overallResult}
                  onChange={(e) => setOverallResult(e.target.value)}
                >
                  {RESULT_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                  ))}
                </select>
                <input
                  className="filter-date"
                  type="date"
                  value={dateFrom}
                  onChange={(e) => setDateFrom(e.target.value)}
                  title="开始日期"
                />
                <input
                  className="filter-date"
                  type="date"
                  value={dateTo}
                  onChange={(e) => setDateTo(e.target.value)}
                  title="结束日期"
                />
                <input className="filter-input" type="text" placeholder="项目组..." value={tagFilter} onChange={(e) => setTagFilter(e.target.value)} style={{ minWidth: 80 }} />
              </>
            )}
            <button className="filter-btn" type="submit">查询</button>
          </form>

          {error && <div className="history-error">{error}</div>}
          {loading ? (
            <div className="history-loading"><div className="spinner"/> 加载中...</div>
          ) : searchMode && searchResults.length > 0 ? (
            <div className="history-list">
              {searchResults.map((sr) => (
                <div
                  key={sr.report_id}
className="history-card history-item-search"
                  onClick={() => handleSearchSelect(sr)}
                >
                  <div className="history-card-row1">
                    <span className="history-card-fname" title={sr.filename}>{sr.filename}</span>
                    <span className={`result-badge result-sm ${RESULT_CLASS[sr.overall_result] || ''}`}>
                      {RESULT_LABEL[sr.overall_result] || sr.overall_result}
                    </span>
                  </div>
	                  <div
	                    className="search-snippet"
	                    dangerouslySetInnerHTML={{
	                      __html: DOMPurify.sanitize(
	                        sr.snippets.error_description || sr.snippets.original_text
	                          || sr.snippets.suggestion || sr.snippets.location || sr.snippets.standard_reference || '',
	                        { ALLOWED_TAGS: ['mark'], ALLOWED_ATTR: [] },
	                      )
	                    }}
	                  />
                  <div className="history-card-row2">
                    <span className="history-card-meta">{sr.match_count} 处匹配</span>
                    {scope === 'all' && sr.employee_id && (
                      <span className="history-card-uploader">{sr.uploader_name ? `${sr.uploader_name} (${sr.employee_id})` : sr.employee_id}</span>
                    )}
                    <span className="history-card-time">{sr.created_at?.slice(0, 16).replace('T', ' ')}</span>
                  </div>
                </div>
              ))}
              <div className="search-total">共 {searchTotal} 条结果</div>
            </div>
          ) : records.length === 0 ? (
            <div className="history-empty">
              {searchMode && !loading ? '未找到匹配内容' : error ? '加载失败' : '暂无审核记录'}
            </div>
          ) : (
            <>
            {compareToast && <div className="notify-toast">{compareToast}</div>}
            {(comparing && selectedIds.length >= 2) && (
              <button
                className="compare-btn"
                onClick={() => { onCompare?.(selectedIds); setSelectedIds([]); setComparing(false) }}
              >
                对比已选报告 ({selectedIds.length}/2)
              </button>
            )}
            {(exporting && exportIds.length >= 1) && (
              <button
                className="compare-btn"
                style={{ background: '#027a48' }}
                disabled={exportingDownload}
                onClick={handleBatchExport}
              >
                {exportingDownload ? '导出中...' : `导出已选 Excel (${exportIds.length})`}
              </button>
            )}
            <div className="history-list">
              {(groupedRecords || []).map((g) => {
                const rec = g.latest
                const isGroup = g.count > 1
                const expanded = expandedGroups.has(g.key)
                const toggleGroup = () => {
                  const next = new Set(expandedGroups)
                  if (next.has(g.key)) next.delete(g.key)
                  else next.add(g.key)
                  setExpandedGroups(next)
                }
                return (
                <React.Fragment key={g.key}>
                <div
                  className={`history-card ${selectedIds.includes(rec.id) ? 'history-item-selected' : ''}`}
                  onClick={(e: any) => {
                    if (comparing) { if ((e.target as HTMLElement).closest('.history-view-btn')) return; toggleCompare(rec.id); return }
                    if (exporting) { if ((e.target as HTMLElement).closest('.history-view-btn')) return; toggleExport(rec.id); return }
                    if ((e.target as HTMLElement).closest('.history-view-btn')) return
                    if (isGroup) { toggleGroup(); return }
                    handleSelect(rec)
                  }}
                >
                  {comparing && (
                    <input type="checkbox" className="compare-checkbox" checked={selectedIds.includes(rec.id)} onChange={() => toggleCompare(rec.id)} onClick={(e) => e.stopPropagation()} />
                  )}
                  {exporting && (
                    <input type="checkbox" className="compare-checkbox" checked={exportIds.includes(rec.id)} onChange={() => toggleExport(rec.id)} onClick={(e) => e.stopPropagation()} />
                  )}
                  <div className="history-card-row1">
                    {isGroup && <span className="history-chevron">{expanded ? '▼' : '▶'}</span>}
                    {!rec.compared_with && rec.group_id && <span className="root-badge">原始</span>}
                    <span className="history-card-fname" title={rec.filename}>{rec.filename}</span>
                    {isGroup && <span className="history-ver-count">{g.count} 个版本</span>}
                    <span className={`result-badge result-sm ${RESULT_CLASS[rec.overall_result] || ''}`} title={historyTooltip(rec)}>{RESULT_LABEL[rec.overall_result] || rec.overall_result}</span>
                    <button className="history-view-btn" onClick={(e) => { e.stopPropagation(); handleSelect(rec) }}>查看</button>
                  </div>
                  <div className="history-card-row2">
                    {rec.tags ? (
                      <span className="history-card-chip">{rec.tags}</span>
                    ) : (
                      <span className="history-card-chip history-card-chip-empty">未分配</span>
                    )}
                    <span className="history-card-chip history-card-chip-id">{rec.group_id || rec.id}</span>
                    <span className="history-card-meta">{(() => { const items = rec.review_items || []; const total = items.length; const reviewed = total - items.filter((i: any) => !i.human_status || i.human_status === 'pending').length; return `${reviewed}/${total} 已复核` })()}</span>
                    {scope === 'all' && rec.employee_id && <span className="history-card-uploader" title={`上传者: ${rec.uploader_name ? `${rec.uploader_name} (${rec.employee_id})` : rec.employee_id}`}>{rec.uploader_name ? `${rec.uploader_name} (${rec.employee_id})` : rec.employee_id}</span>}
                    <span className="history-card-time">{rec.created_at?.slice(0, 16).replace('T', ' ')}</span>
                  </div>
                </div>
                {isGroup && expanded && g.versions.map((v: any, i: number) => (
                  <div key={v.id} className="history-card history-card-sub"
                    onClick={() => handleSelect(v)}
                  >
                    <div className="history-card-row1">
                      <span className={`ver-capsule ${!v.compared_with ? 'ver-root' : ''}`}>{!v.compared_with ? 'Root' : `V${g.count - i}`}</span>
                      <span className="history-card-fname" title={v.filename}>{v.filename}</span>
                      {v.compared_with && <span className="compare-source">对比</span>}
                      <span className={`result-badge result-sm ${RESULT_CLASS[v.overall_result] || ''}`} title={historyTooltip(v)}>{RESULT_LABEL[v.overall_result] || v.overall_result}</span>
                      <button className="history-view-btn" onClick={(e) => { e.stopPropagation(); handleSelect(v) }}>查看</button>
                    </div>
                    <div className="history-card-row2">
                      {v.employee_id && <span className="history-card-uploader">{v.uploader_name ? `${v.uploader_name} (${v.employee_id})` : v.employee_id}</span>}
                      <span className="history-card-time">{v.created_at?.slice(0, 16).replace('T', ' ')}</span>
                    </div>
                  </div>
                ))}
                </React.Fragment>
              )})}
            </div>
          </>
          )}
        </div>
      )}
      {modalReport && (
        <div className="modal-overlay" onClick={() => setModalReport(null)} onKeyDown={(e) => { if (e.key === 'Escape') setModalReport(null) }} tabIndex={-1} ref={(el) => el?.focus()}>
          <div className="modal-container" onClick={(e) => e.stopPropagation()} style={{maxWidth:1400}}>
            <div className="modal-header">
              <span className="modal-title">{modalReport.filename}</span>
              <div style={{display:'flex',alignItems:'center',gap:8}}>
                <button
                  onClick={handleDelete}
                  disabled={deleting}
                  style={{background:'var(--color-error)',color:'#fff',border:'none',borderRadius:6,padding:'4px 12px',fontSize:13,cursor:'pointer',opacity:deleting?0.6:1}}
                >
                  {deleting ? '删除中...' : '删除'}
                </button>
                <button onClick={() => setModalReport(null)} style={{background:'none',border:'none',fontSize:20,cursor:'pointer',color:'#667085',padding:'0 4px'}} aria-label="关闭">✕</button>
              </div>
            </div>
            <div className="modal-body">
              <ReportReviewer data={modalReport} currentUserId={currentUserId} inModal onTagsChange={(rid, tags) => {
                setRecords(prev => {
                  const gid = prev.find(r => r.id === rid)?.group_id
                  return prev.map(r => {
                    if (r.id === rid) return { ...r, tags }
                    if (gid && r.group_id === gid) return { ...r, tags }
                    return r
                  })
                })
              }} />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
