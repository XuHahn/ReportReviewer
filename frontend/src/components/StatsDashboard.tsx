import { useEffect, useState } from 'react'
import {
  PieChart, Pie, Cell, Tooltip,
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  LineChart, Line, ResponsiveContainer,
} from 'recharts'
import { getSetStats } from '../api'
import type { SetStatsResponse } from '../types'

const SEV_COLORS: Record<string, string> = { CRITICAL: '#e74c3c', WARNING: '#f39c12', INFO: '#3498db' }
const STATUS_COLORS: Record<string, string> = {
  incomplete: '#95a5a6', revision: '#b54708', locked: '#3498db', reviewing: '#f39c12', reviewed: '#27ae60', error: '#e74c3c',
}

export default function StatsDashboard() {
  const [stats, setStats] = useState<SetStatsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchStats = () => {
    setRefreshing(true)
    setError(null)
    getSetStats()
      .then(setStats)
      .catch((e) => setError(e?.response?.data?.detail || e.message || '加载统计失败'))
      .finally(() => { setLoading(false); setRefreshing(false) })
  }

  useEffect(() => { fetchStats() }, [])

  if (loading) return (
    <div className="stats-dashboard">
      <h2 className="stats-title">审核统计仪表盘</h2>
      <div className="stats-overview">
        {[1,2,3,4].map(i => <div key={i} className="stat-card"><div className="skeleton skeleton-stat" style={{width:'70%'}} /></div>)}
      </div>
      <div style={{display:'grid',gridTemplateColumns:'repeat(2,1fr)',gap:20}}>
        <div className="skeleton skeleton-card" />
        <div className="skeleton skeleton-card" />
      </div>
    </div>
  )
  if (error) return <div className="upload-error">{error}</div>
  if (!stats || stats.total_sets === 0) {
    return (
      <div className="stats-empty">
        <p>暂无审核数据</p>
        <p className="stats-empty-hint">上传文档集并完成审核后，统计数据将在此展示</p>
      </div>
    )
  }

  const { total_sets, total_issues, clean_rate, avg_issues_per_set, severity_dist, status_dist, trends, top_categories } = stats

  // Status distribution → pie chart
  const statusData = Object.entries(status_dist).map(([k, v]) => ({
    name: _statusLabel(k), value: v, fill: STATUS_COLORS[k] || '#95a5a6',
  }))

  // Severity distribution → bar chart
  const sevData = [
    { name: '严重', count: severity_dist.CRITICAL || 0, fill: SEV_COLORS.CRITICAL },
    { name: '警告', count: severity_dist.WARNING || 0, fill: SEV_COLORS.WARNING },
    { name: '提示', count: severity_dist.INFO || 0, fill: SEV_COLORS.INFO },
  ]

  // Trends → line chart
  const trendData = (trends || []).map(t => ({
    date: t.date.slice(5), count: t.count,
  }))

  // Top categories → horizontal bar chart
  const catData = (top_categories || []).map(c => ({
    name: c.category.length > 20 ? c.category.slice(0, 20) + '...' : c.category,
    fullName: c.category, count: c.count,
  }))

  return (
    <div className="stats-dashboard">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <h2 className="stats-title" style={{ marginBottom: 0 }}>审核统计仪表盘</h2>
        <button className="filter-btn" onClick={fetchStats} disabled={refreshing}>
          {refreshing ? '刷新中...' : '刷新'}
        </button>
      </div>

      <div className="stats-overview">
        <div className="stat-card"><span className="stat-num">{total_sets}</span><span className="stat-label">总文档集</span></div>
        <div className="stat-card"><span className="stat-num">{clean_rate}%</span><span className="stat-label">清洁率</span></div>
        <div className="stat-card"><span className="stat-num">{total_issues}</span><span className="stat-label">总问题数</span></div>
        <div className="stat-card"><span className="stat-num">{avg_issues_per_set}</span><span className="stat-label">平均问题/集</span></div>
      </div>

      <div className="stats-charts">
        <div className="chart-card">
          <h3>文档集状态分布</h3>
          <ResponsiveContainer width="100%" height={280}>
            <PieChart>
              <Pie data={statusData} cx="50%" cy="50%" outerRadius={100} dataKey="value" label={({ name, value }) => `${name} ${value}`}>
                {statusData.map((d, i) => <Cell key={i} fill={d.fill}/>)}
              </Pie>
              <Tooltip/>
            </PieChart>
          </ResponsiveContainer>
        </div>

        <div className="chart-card">
          <h3>问题严重程度分布</h3>
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
          <h3>文档集创建趋势（近30天）</h3>
          {trendData.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={trendData}>
                <CartesianGrid strokeDasharray="3 3"/>
                <XAxis dataKey="date" fontSize={12}/>
                <YAxis allowDecimals={false}/>
                <Tooltip/>
                <Line type="monotone" dataKey="count" name="创建数" stroke="#3498db" strokeWidth={2}/>
              </LineChart>
            </ResponsiveContainer>
          ) : <p className="chart-no-data">暂无趋势数据</p>}
        </div>

        <div className="chart-card">
          <h3>高频问题类别 Top 10</h3>
          {catData.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={catData} layout="vertical">
                <CartesianGrid strokeDasharray="3 3"/>
                <XAxis type="number" allowDecimals={false}/>
                <YAxis dataKey="name" type="category" width={150} tick={{ fontSize: 12 }}/>
                <Tooltip formatter={(value: any, _name: any, props: any) => [value, props?.payload?.fullName || '']}/>
                <Bar dataKey="count" fill="#8e44ad" radius={[0, 4, 4, 0]}/>
              </BarChart>
            </ResponsiveContainer>
          ) : <p className="chart-no-data">暂无类别数据</p>}
        </div>
      </div>
    </div>
  )
}

function _statusLabel(k: string): string {
  const map: Record<string, string> = {
    incomplete: '待完善', revision: '修订中', locked: '已锁定', reviewing: '审核中', reviewed: '已审核', error: '异常',
  }
  return map[k] || k
}
