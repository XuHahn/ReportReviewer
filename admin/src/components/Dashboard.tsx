import { useState, useEffect } from 'react'
import { PieChart, Pie, Cell, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, BarChart, Bar, ResponsiveContainer, Legend } from 'recharts'
import { getAdminStats } from '../api'
import type { AdminStatsResponse } from '../types'

const SEV_COLORS: Record<string, string> = { error: '#e74c3c', warning: '#f39c12', info: '#3498db' }
const RESULT_COLORS: Record<string, string> = { pass: '#2ecc71', fail: '#e74c3c', warning: '#f39c12' }
const RESULT_LABELS: Record<string, string> = { pass: '通过', fail: '不通过', warning: '警告' }

export default function Dashboard() {
  const [data, setData] = useState<AdminStatsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    getAdminStats()
      .then(setData)
      .catch((err) => setError(err.response?.data?.detail || err.message || '加载失败'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="history-loading">加载中...</div>
  if (error) return <div className="upload-error">{error}</div>
  if (!data) return null

  const pfData = Object.entries(data.pass_fail).map(([name, value]) => ({
    name: RESULT_LABELS[name] || name, value, color: RESULT_COLORS[name] || '#999',
  }))

  const sevData = Object.entries(data.severity_dist).map(([name, value]) => ({
    name: name === 'error' ? '错误' : name === 'warning' ? '警告' : '提示', value, color: SEV_COLORS[name] || '#999',
  }))

  return (
    <div className="dashboard">
      <h2 className="section-title">管理仪表盘</h2>

      <div className="stat-cards">
        <div className="stat-card">
          <div className="stat-card-value">{data.overview.total_reports}</div>
          <div className="stat-card-label">总报告数</div>
        </div>
        <div className="stat-card">
          <div className="stat-card-value">{data.total_users}</div>
          <div className="stat-card-label">总用户数</div>
        </div>
        <div className="stat-card">
          <div className="stat-card-value">{data.today_uploads}</div>
          <div className="stat-card-label">今日上传</div>
        </div>
        <div className="stat-card">
          <div className="stat-card-value">{data.overview.pass_rate}%</div>
          <div className="stat-card-label">通过率</div>
        </div>
        <div className="stat-card">
          <div className="stat-card-value">{data.overview.total_issues}</div>
          <div className="stat-card-label">检出问题</div>
        </div>
        <div className="stat-card">
          <div className="stat-card-value">{data.overview.avg_issues_per_report}</div>
          <div className="stat-card-label">平均问题/报告</div>
        </div>
      </div>

      <div className="charts-row">
        <div className="chart-box">
          <h3 className="chart-title">审核结果分布</h3>
          <ResponsiveContainer width="100%" height={250}>
            <PieChart>
              <Pie data={pfData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={90} label={({ name, value }) => `${name} ${value}`}>
                {pfData.map((d, i) => (<Cell key={i} fill={d.color} />))}
              </Pie>
              <Tooltip />
            </PieChart>
          </ResponsiveContainer>
        </div>

        <div className="chart-box">
          <h3 className="chart-title">严重程度分布</h3>
          <ResponsiveContainer width="100%" height={250}>
            <PieChart>
              <Pie data={sevData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={90} label={({ name, value }) => `${name} ${value}`}>
                {sevData.map((d, i) => (<Cell key={i} fill={d.color} />))}
              </Pie>
              <Tooltip />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="chart-box">
        <h3 className="chart-title">30天上传趋势</h3>
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={data.trends}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="date" tick={{ fontSize: 12 }} />
            <YAxis />
            <Tooltip />
            <Legend />
            <Line type="monotone" dataKey="uploads" name="上传" stroke="#3498db" strokeWidth={2} />
            <Line type="monotone" dataKey="pass_count" name="通过" stroke="#2ecc71" strokeWidth={2} />
            <Line type="monotone" dataKey="fail_count" name="不通过" stroke="#e74c3c" strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="charts-row">
        <div className="chart-box">
          <h3 className="chart-title">Top 10 问题位置</h3>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={data.top_locations} layout="vertical" margin={{ left: 80 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis type="number" />
              <YAxis type="category" dataKey="location" tick={{ fontSize: 11 }} width={120} />
              <Tooltip />
              <Bar dataKey="count" fill="#e74c3c" name="问题数" />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="chart-box">
          <h3 className="chart-title">最近操作</h3>
          <div className="recent-activity">
            {data.recent_activity.length === 0 && <div className="history-empty">暂无操作记录</div>}
            {data.recent_activity.map((entry) => (
              <div key={entry.id} className="activity-row">
                <div className="activity-time">{new Date(entry.timestamp).toLocaleString()}</div>
                <div className="activity-action">
                  <span className="activity-user">{entry.employee_id || '-'}</span>
                  {' — '}
                  <span className="activity-type">{entry.action}</span>
                </div>
                {entry.filename && <div className="activity-file">{entry.filename}</div>}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
