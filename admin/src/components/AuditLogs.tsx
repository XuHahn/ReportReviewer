import { useState, useEffect } from 'react'
import { getAuditLogs } from '../api'
import type { AuditLogEntry } from '../types'

const ACTION_OPTIONS = [
  { value: '', label: '全部操作' },
  { value: 'upload', label: '上传' },
  { value: 'upload_stream', label: '流式上传' },
  { value: 'upload_batch', label: '批量上传' },
  { value: 'export_pdf', label: '导出PDF' },
  { value: 'export_word', label: '导出Word' },
  { value: 'upload_rejected', label: '上传被拒' },
  { value: 'upload_error', label: '上传错误' },
  { value: 'login', label: '登录' },
]

export default function AuditLogs() {
  const [entries, setEntries] = useState<AuditLogEntry[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const [action, setAction] = useState('')
  const [employeeId, setEmployeeId] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [ip, setIp] = useState('')
  const [page, setPage] = useState(0)
  const PAGE_SIZE = 20

  async function load(pageNum: number) {
    setLoading(true)
    try {
      const params: Record<string, any> = { limit: PAGE_SIZE, offset: pageNum * PAGE_SIZE }
      if (action) params.action = action
      if (employeeId.trim()) params.employee_id = employeeId.trim()
      if (dateFrom) params.date_from = dateFrom
      if (dateTo) params.date_to = dateTo
      if (ip.trim()) params.ip = ip.trim()

      const res = await getAuditLogs(params)
      setEntries(res.entries)
      setTotal(res.total)
      setPage(pageNum)
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load(0) }, [])

  function handleSearch(e: React.FormEvent) {
    e.preventDefault()
    load(0)
  }

  const totalPages = Math.ceil(total / PAGE_SIZE)

  return (
    <div className="audit-logs">
      <h2 className="section-title">审计日志</h2>

      {error && <div className="upload-error">{error}</div>}

      <form className="log-filters" onSubmit={handleSearch}>
        <select className="filter-select" value={action} onChange={(e) => setAction(e.target.value)}>
          {ACTION_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
        <input
          className="filter-input"
          type="text"
          placeholder="操作人工号"
          value={employeeId}
          onChange={(e) => setEmployeeId(e.target.value)}
        />
        <input
          className="filter-input"
          type="date"
          value={dateFrom}
          onChange={(e) => setDateFrom(e.target.value)}
        />
        <input
          className="filter-input"
          type="date"
          value={dateTo}
          onChange={(e) => setDateTo(e.target.value)}
        />
        <input
          className="filter-input"
          type="text"
          placeholder="IP地址"
          value={ip}
          onChange={(e) => setIp(e.target.value)}
        />
        <button className="filter-btn" type="submit">查询</button>
      </form>

      {loading ? (
        <div className="history-loading">加载中...</div>
      ) : (
        <>
          <table className="user-table log-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>操作人</th>
                <th>IP</th>
                <th>操作类型</th>
                <th>文件名</th>
                <th>结果</th>
                <th>详情</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id}>
                  <td className="log-time">{new Date(entry.timestamp).toLocaleString()}</td>
                  <td>{entry.employee_id || '-'}</td>
                  <td>{entry.client_ip}</td>
                  <td>{entry.action}</td>
                  <td className="log-filename">{entry.filename || '-'}</td>
                  <td>{entry.overall_result || '-'}</td>
                  <td className="log-detail">{entry.detail ? entry.detail.substring(0, 80) : '-'}</td>
                </tr>
              ))}
              {entries.length === 0 && (
                <tr>
                  <td colSpan={7} className="history-empty">暂无日志记录</td>
                </tr>
              )}
            </tbody>
          </table>

          <div className="pagination">
            <span className="pagination-info">共 {total} 条，第 {page + 1}/{totalPages || 1} 页</span>
            <div className="pagination-btns">
              <button className="filter-btn" disabled={page === 0} onClick={() => load(page - 1)}>上一页</button>
              <button className="filter-btn" disabled={page >= totalPages - 1} onClick={() => load(page + 1)}>下一页</button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
