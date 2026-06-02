import { useState, Fragment } from 'react'
import { getLogs } from '../api'
import { logger } from '../logger'

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: '#667085',
  INFO: '#175cd3',
  WARN: '#f59e0b',
  ERROR: '#d92d20',
}

export default function LogViewer() {
  const [source, setSource] = useState('all')
  const [level, setLevel] = useState('')
  const [keyword, setKeyword] = useState('')
  const [reqId, setReqId] = useState('')
  const [logs, setLogs] = useState<any[]>([])
  const [total, setTotal] = useState(-1)
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [limit] = useState(200)

  const fetchLogs = async () => {
    setLoading(true)
    try {
      const data = await getLogs({ source, level: level || undefined, keyword: keyword || undefined, reqId: reqId || undefined, limit })
      setLogs(data.logs || [])
      setTotal(data.total)
    } catch (err) { logger.error('Failed to fetch logs', {}, err) }
    finally { setLoading(false) }
  }

  const toggleExpand = (i: number) => setExpanded(expanded === i ? null : i)

  return (
    <div style={{ padding: '0 0 24px' }}>
      <div className="pg-toolbar" style={{ marginBottom: 16 }}>
        <h2 className="pg-title">日志查看器</h2>
        <button className="pg-btn" onClick={fetchLogs} disabled={loading}>
          {loading ? '加载中...' : '刷新'}
        </button>
      </div>

      <div style={{ display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
        <select className="filter-input" style={{ width: 130 }} value={source} onChange={e => setSource(e.target.value)}>
          <option value="all">全部来源</option>
          <option value="backend">后端</option>
          <option value="frontend">前端</option>
        </select>

        <select className="filter-input" style={{ width: 120 }} value={level} onChange={e => setLevel(e.target.value)}>
          <option value="">全部级别</option>
          <option value="DEBUG">DEBUG</option>
          <option value="INFO">INFO</option>
          <option value="WARN">WARN</option>
          <option value="ERROR">ERROR</option>
        </select>

        <input className="filter-input" style={{ width: 180 }} placeholder="关键词搜索..." value={keyword}
          onChange={e => setKeyword(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') fetchLogs() }}
        />

        <input className="filter-input" style={{ width: 140 }} placeholder="reqId..." value={reqId}
          onChange={e => setReqId(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') fetchLogs() }}
        />

        <button className="pg-add-btn" onClick={fetchLogs} disabled={loading}>
          查询
        </button>

        {total >= 0 && (
          <span style={{ fontSize: 13, color: '#667085' }}>
            共 {total} 条，显示前 {Math.min(total, limit)} 条
          </span>
        )}
      </div>

      {logs.length === 0 && total >= 0 && (
        <div className="pg-empty">暂无日志记录</div>
      )}

      {logs.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ background: '#f9fafb', position: 'sticky', top: 0 }}>
                <th style={th}>时间</th>
                <th style={th}>来源</th>
                <th style={th}>级别</th>
                <th style={th}>reqId</th>
                <th style={{ ...th, textAlign: 'left' }}>消息 / 模块</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((log: any, i: number) => (
                <Fragment key={i}>
                  <tr
                    onClick={() => toggleExpand(i)}
                    style={{ cursor: 'pointer', background: expanded === i ? '#f0f6ff' : i % 2 === 0 ? '#fff' : '#fafafa' }}
                    onMouseEnter={e => { if (expanded !== i) (e.currentTarget as HTMLElement).style.background = '#f5f7fa' }}
                    onMouseLeave={e => { if (expanded !== i) (e.currentTarget as HTMLElement).style.background = i % 2 === 0 ? '#fff' : '#fafafa' }}
                  >
                    <td style={td}>{log.timestamp?.slice(0, 19) || '-'}</td>
                    <td style={td}>
                      <span style={{ fontSize: 11, padding: '1px 6px', borderRadius: 4, background: log.source === 'frontend' ? '#ecfdf3' : '#eff8ff', color: log.source === 'frontend' ? '#027a48' : '#175cd3' }}>
                        {log.source || '-'}
                      </span>
                    </td>
                    <td style={td}>
                      <span style={{ fontSize: 11, padding: '1px 6px', borderRadius: 4, fontWeight: 600, color: LEVEL_COLORS[log.level] || '#667085', background: `${LEVEL_COLORS[log.level] || '#667085'}18` }}>
                        {log.level || '-'}
                      </span>
                    </td>
                    <td style={{ ...td, fontFamily: 'monospace', fontSize: 12, color: '#475467' }}>
                      {log.reqId || '-'}
                    </td>
                    <td style={{ ...td, textAlign: 'left', maxWidth: 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {log.event || log.msg || '-'}
                      <span style={{ color: '#98a2b3', fontSize: 11, marginLeft: 8 }}>{log.module || ''}</span>
                    </td>
                  </tr>
                  {expanded === i && (
                    <tr key={`${i}-detail`}>
                      <td colSpan={5} style={{ padding: '12px 16px', background: '#f9fafb', borderBottom: '1px solid #e5e7eb' }}>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '8px 16px', fontSize: 12 }}>
                          {log.module && <div><span style={{ color: '#98a2b3' }}>模块: </span>{log.module}</div>}
                          {log.userId && <div><span style={{ color: '#98a2b3' }}>userId: </span>{log.userId}</div>}
                          {log.reqId && <div><span style={{ color: '#98a2b3' }}>reqId: </span><span style={{ fontFamily: 'monospace' }}>{log.reqId}</span></div>}
                          {log.ctx && <div><span style={{ color: '#98a2b3' }}>ctx: </span><span style={{ fontFamily: 'monospace', fontSize: 11 }}>{JSON.stringify(log.ctx)}</span></div>}
                          {log.error && <div style={{ gridColumn: '1 / -1' }}><span style={{ color: '#d92d20' }}>error: </span><pre style={{ margin: 0, fontSize: 11, whiteSpace: 'pre-wrap', color: '#b42318' }}>{log.error}</pre></div>}
                          {log.exception && <div style={{ gridColumn: '1 / -1' }}><span style={{ color: '#d92d20' }}>exception: </span><pre style={{ margin: 0, fontSize: 11, whiteSpace: 'pre-wrap', color: '#b42318' }}>{log.exception}</pre></div>}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

const th: React.CSSProperties = { padding: '8px 12px', fontSize: 12, fontWeight: 600, color: '#667085', borderBottom: '1px solid #e5e7eb', whiteSpace: 'nowrap', textAlign: 'center' }
const td: React.CSSProperties = { padding: '8px 12px', borderBottom: '1px solid #f0f2f5', textAlign: 'center', whiteSpace: 'nowrap' }
