import { useEffect, useState } from 'react'
import {
  PieChart, Pie, Cell, Tooltip,
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  LineChart, Line, ResponsiveContainer, Legend,
} from 'recharts'
import { getStats } from '../api'
import type { StatsResponse } from '../types'

const SEV_COLORS: Record<string, string> = { error: '#e74c3c', warning: '#f39c12', info: '#3498db' }
const PASS_COLORS = ['#27ae60', '#e74c3c', '#f39c12', '#95a5a6']

export default function StatsDashboard() {
  const [stats, setStats] = useState<StatsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchStats = () => {
    setRefreshing(true)
    setError(null)
    getStats()
      .then(setStats)
      .catch((e) => setError(e?.response?.data?.detail || e.message || '加载统计失败'))
      .finally(() => { setLoading(false); setRefreshing(false) })
  }

  useEffect(() => { fetchStats() }, [])

  if (loading) return (
    <div className="stats-dashboard">
      <h2 className="stats-title">审核统计仪表盘</h2>
      <div className="stats-overview">
        {[1,2,3,4,5].map(i => <div key={i} className="stat-card"><div className="skeleton skeleton-stat" style={{width:'70%'}} /></div>)}
      </div>
      <div style={{display:'grid',gridTemplateColumns:'repeat(2,1fr)',gap:20}}>
        <div className="skeleton skeleton-card" />
        <div className="skeleton skeleton-card" />
      </div>
    </div>
  )
  if (error) return <div className="upload-error">{error}</div>
  if (!stats || stats.overview.total_reports === 0) {
    return (
      <div className="stats-empty">
        <p>暂无审核数据</p>
        <p className="stats-empty-hint">上传并审核报告后，统计数据将在此展示</p>
      </div>
    )
  }

  const { overview, pass_fail, severity_dist, trends, top_locations, top_actions } = stats

  const passFailData = Object.entries(pass_fail).map(([k, v]) => ({ name: _label(k), value: v }))
  const sevData = [
    { name: _label('error'), count: severity_dist.error, fill: SEV_COLORS.error },
    { name: _label('warning'), count: severity_dist.warning, fill: SEV_COLORS.warning },
    { name: _label('info'), count: severity_dist.info, fill: SEV_COLORS.info },
  ]
  const trendData = trends.map((t) => ({
    date: t.date.slice(5), uploads: t.uploads, pass: t.pass_count, fail: t.fail_count,
  }))
  const locData = top_locations.map((l) => ({
    name: l.location.length > 22 ? l.location.slice(0, 22) + '...' : l.location,
    fullName: l.location, count: l.count,
  }))

  const actionLabels: Record<string, string> = {
    upload: '报告审核', upload_stream: '流式审核', upload_batch: '批量审核',
    upload_rejected: '上传被拒', upload_error: '审核失败',
    export_pdf: '导出PDF', export_word: '导出Word', batch_export_excel: '批量导出Excel',
    annotate: '标注', delete_report: '删除报告', delete_report_group: '级联删除',
    restore_report: '恢复报告',
  }

  return (
    <div className="stats-dashboard">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <h2 className="stats-title" style={{ marginBottom: 0 }}>审核统计仪表盘</h2>
        <button className="filter-btn" onClick={fetchStats} disabled={refreshing}>
          {refreshing ? '刷新中...' : '刷新'}
        </button>
      </div>

      <div className="stats-overview">
        <div className="stat-card"><span className="stat-num">{overview.total_reports}</span><span className="stat-label">总报告数</span></div>
        <div className="stat-card"><span className="stat-num">{overview.pass_rate}%</span><span className="stat-label">通过率</span></div>
        <div className="stat-card"><span className="stat-num">{overview.total_issues}</span><span className="stat-label">总问题数</span></div>
        <div className="stat-card"><span className="stat-num">{overview.avg_issues_per_report}</span><span className="stat-label">平均问题/报告</span></div>
        <div className="stat-card"><span className="stat-num">{_fmtDuration(overview.avg_duration_ms)}</span><span className="stat-label">平均耗时</span></div>
      </div>

      <div className="stats-charts">
        <div className="chart-card">
          <h3>审核结论分布</h3>
          <ResponsiveContainer width="100%" height={280}>
            <PieChart>
              <Pie data={passFailData} cx="50%" cy="50%" outerRadius={100} dataKey="value" label={({ name, value }) => `${name} ${value}`}>
                {passFailData.map((_, i) => <Cell key={i} fill={PASS_COLORS[i % PASS_COLORS.length]}/>)}
              </Pie>
              <Tooltip/>
            </PieChart>
          </ResponsiveContainer>
        </div>

        <div className="chart-card">
          <h3>问题严重程度</h3>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={sevData}>
              <CartesianGrid strokeDasharray="3 3"/>
              <XAxis dataKey="name"/>
              <YAxis allowDecimals={false}/>
              <Tooltip/>
              <Bar dataKey="count" name="数量" radius={[4, 4, 0, 0]}>
                {sevData.map((d, i) => <Cell key={i} fill={d.fill}/>)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="chart-card chart-wide">
          <h3>审核趋势（近30天）</h3>
          {trendData.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={trendData}>
                <CartesianGrid strokeDasharray="3 3"/>
                <XAxis dataKey="date" fontSize={12}/>
                <YAxis allowDecimals={false}/>
                <Tooltip/>
                <Legend/>
                <Line type="monotone" dataKey="uploads" name="上传" stroke="#3498db" strokeWidth={2}/>
                <Line type="monotone" dataKey="pass" name="通过" stroke="#27ae60" strokeWidth={2}/>
                <Line type="monotone" dataKey="fail" name="不通过" stroke="#e74c3c" strokeWidth={2}/>
              </LineChart>
            </ResponsiveContainer>
          ) : <p className="chart-no-data">暂无趋势数据</p>}
        </div>

        <div className="chart-card">
          <h3>高频错误位置 Top 10</h3>
          {locData.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={locData} layout="vertical">
                <CartesianGrid strokeDasharray="3 3"/>
                <XAxis type="number" allowDecimals={false}/>
                <YAxis dataKey="name" type="category" width={150} tick={{ fontSize: 12 }}/>
                <Tooltip formatter={(value: any, _name: any, props: any) => [value, props?.payload?.fullName || '']}/>
                <Bar dataKey="count" fill="#8e44ad" radius={[0, 4, 4, 0]}/>
              </BarChart>
            </ResponsiveContainer>
          ) : <p className="chart-no-data">暂无位置数据</p>}
        </div>
      </div>

      <div className="chart-card" style={{ marginTop: 24 }}>
        <h3>操作统计</h3>
        <div className="action-list">
          {Object.entries(top_actions).map(([action, count]) => (
            <div key={action} className="action-item">
              <span className="action-name">{actionLabels[action] || action}</span>
              <span className="action-count">{count}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function _label(k: string): string {
  const map: Record<string, string> = { pass: '通过', fail: '不通过', warning: '警告', error: '错误', info: '提示' }
  return map[k] || k
}

function _fmtDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m${s % 60}s`
}
