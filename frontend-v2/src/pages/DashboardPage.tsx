import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Area, AreaChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ArrowRight, CheckCircle2, Clock3, FileWarning, Plus } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { notifications } from '@mantine/notifications'
import { createDocumentSet, getSetStats, getStandards, listDocumentSets, apiErrorMessage } from '../api'
import { useSession } from '../App'
import { demoSets, demoStats, demoStandards } from '../mockData'
import { CHECK_CATEGORY_LABELS } from '../types'
import { EmptyState, LoadingState, PageHeader, formatDate } from '../components/common'
import { taskStartStage } from '../reviewLogic'

const chartColors = ['#c84d3f', '#e28a3b', '#d0a43b', '#3e7185', '#1f7c70', '#755f93']

export default function DashboardPage() {
  const { demo, user } = useSession()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const standardReviewer = user?.role === 'standard_reviewer'
  const statsQuery = useQuery({ queryKey: ['stats', demo], queryFn: () => demo ? Promise.resolve(demoStats) : getSetStats(), enabled: !standardReviewer, refetchOnMount: 'always', refetchOnWindowFocus: true })
  const setsQuery = useQuery({ queryKey: ['sets', demo], queryFn: () => demo ? Promise.resolve({ sets: demoSets, total: demoSets.length }) : listDocumentSets(), enabled: !standardReviewer, refetchOnMount: 'always', refetchOnWindowFocus: true })
  const stats = statsQuery.data
  const sets = setsQuery.data?.sets || []

  async function createTask() {
    if (demo) { notifications.show({ color: 'blue', title: '当前是演示预览', message: '请退出演示并使用真实工号登录后新建审核。' }); return }
    try { const result = await createDocumentSet(); await queryClient.invalidateQueries({ queryKey: ['sets', demo] }); await queryClient.invalidateQueries({ queryKey: ['stats', demo] }); navigate(`/tasks/${result.set_id}/intake`) }
    catch (error) { notifications.show({ color: 'red', title: '新建失败', message: apiErrorMessage(error) }) }
  }

  if (standardReviewer) return <div className="page dashboard-page"><PageHeader eyebrow="TODAY · REVIEW CONTROL" title="标准知识待确认" description="只展示当前标准库中的真实处理状态。" /><StandardsSummary /></div>
  if (statsQuery.isLoading || setsQuery.isLoading) return <div className="page dashboard-page"><PageHeader eyebrow="TODAY · REVIEW CONTROL" title="正在读取真实审核数据" description="任务、问题和趋势均来自当前审核数据库。" /><LoadingState label="正在汇总旧任务与最近审核结果…" /></div>
  if (statsQuery.isError || setsQuery.isError || !stats) return <div className="page dashboard-page"><PageHeader eyebrow="TODAY · REVIEW CONTROL" title="真实数据暂时无法读取" description="页面不会使用演示数字替代接口失败。" /><EmptyState icon="error" title="审核数据加载失败" description="请检查后端服务后重试；当前页面没有展示任何替代数据。" /></div>

  const trendData = (stats.trends || []).map(item => ({ day: item.date.slice(5), count: item.count }))
  const issueData = (stats.top_categories || []).slice(0, 5).map((item, index) => ({ name: CHECK_CATEGORY_LABELS[item.category] || item.category, value: item.count, color: chartColors[index % chartColors.length] }))
  const draftCount = sets.filter(item => ['draft', 'incomplete', 'revision'].includes(item.status)).length
  const runningCount = sets.filter(item => ['created', 'building', 'running'].includes(item.latest_run_status || '')).length
  const decisionCount = sets.filter(item => Boolean(item.pending_count)).length
  const actionCount = sets.filter(item => ['draft', 'incomplete', 'revision'].includes(item.status) || ['created', 'building', 'running'].includes(item.latest_run_status || '') || Boolean(item.pending_count)).length

  return <div className="page dashboard-page">
    <PageHeader eyebrow="TODAY · REVIEW CONTROL" title="今天先处理这些" description="认真对待每一份报告，结论就藏在细节里。" actions={<button className="primary-button" onClick={createTask}><Plus size={17} />新建审核</button>} />
    <>
      <section className="dashboard-lead">
        <article className="attention-board">
          <div className="board-heading"><span>待处理队列</span><small>来自当前真实数据</small></div>
          <div className="queue-number"><b>{actionCount}</b><span>个任务<br />需要动作</span></div>
          <div className="queue-breakdown">
            <div><FileWarning /><span><b>{draftCount}</b><small>资料待补齐</small></span></div>
            <div><Clock3 /><span><b>{runningCount}</b><small>机器审核中</small></span></div>
            <div><CheckCircle2 /><span><b>{decisionCount}</b><small>结论待处理</small></span></div>
          </div>
          <Link to="/tasks">打开完整队列 <ArrowRight size={15} /></Link>
        </article>
        <article className="trend-panel">
          <div className="panel-heading"><div><span>近 30 天新建任务</span><b>{trendData.reduce((sum, item) => sum + item.count, 0)}</b></div><span>累计 {stats.total_sets} 个真实任务</span></div>
          <div className="chart-wrap"><ResponsiveContainer width="100%" height="100%"><AreaChart data={trendData} margin={{ top: 12, right: 8, left: -24, bottom: 0 }} accessibilityLayer>
            <defs><linearGradient id="passFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#1f7c70" stopOpacity=".28"/><stop offset="100%" stopColor="#1f7c70" stopOpacity=".02"/></linearGradient></defs>
            <CartesianGrid vertical={false} stroke="var(--line)" /><XAxis dataKey="day" axisLine={false} tickLine={false} fontSize={11}/><YAxis allowDecimals={false} domain={[0, 'auto']} axisLine={false} tickLine={false} fontSize={10}/><Tooltip contentStyle={{ borderRadius: 4, borderColor: 'var(--line)' }}/><Area type="monotone" dataKey="count" name="新建任务" stroke="#1f7c70" strokeWidth={2} fill="url(#passFill)" />
          </AreaChart></ResponsiveContainer></div>
        </article>
        <article className="issue-panel">
          <div className="panel-heading"><div><span>常见问题构成</span><b>{stats.total_issues || 0}</b></div></div>
          {issueData.length ? <div className="issue-chart"><ResponsiveContainer width="46%" height={180}><PieChart accessibilityLayer><Pie data={issueData} dataKey="value" nameKey="name" innerRadius={48} outerRadius={72} paddingAngle={2}>{issueData.map(item => <Cell key={item.name} fill={item.color} />)}</Pie><Tooltip /></PieChart></ResponsiveContainer>
            <div className="issue-legend">{issueData.map(item => <div key={item.name}><i style={{ background: item.color }} /><span title={item.name}>{item.name}</span><b>{item.value}</b></div>)}</div>
          </div> : <EmptyState title="暂无问题分类" description="完成机器审核后，这里会按真实 check_id 汇总。" />}
        </article>
      </section>
      <section className="recent-section">
        <div className="section-heading"><div><h2>最近审核任务</h2><p>审核如山，始于足下。</p></div><Link to="/tasks">全部任务 <ArrowRight size={14} /></Link></div>
        {sets.length ? <div className="task-table" role="table" aria-label="最近审核任务">
          <div className="task-row task-head" role="row"><span>任务 / 产品</span><span>项目组</span><span>资料</span><span>问题</span><span>更新时间</span><span /></div>
          {sets.slice(0, 5).map(item => <div className="task-row" role="row" key={item.set_id}>
            <div><b>{item.title || item.set_id}</b><small>{item.set_id}</small></div><span>{item.project_group_name || '未分组'}</span><span>{item.documents_count || item.doc_count || item.documents?.length || 0} / 4</span><span>{['draft', 'incomplete', 'revision'].includes(item.status) ? '资料待补齐' : item.pending_count ? <em>{item.pending_count} 待处理</em> : '已闭合'}</span><span>{formatDate(item.updated_at || item.created_at)}</span><Link aria-label={`打开 ${item.title || item.set_id}`} to={`/tasks/${item.set_id}/${taskStartStage(item)}`}><ArrowRight size={17} /></Link>
          </div>)}</div> : <EmptyState title="还没有审核任务" description="新建任务并上传四份业务资料后，任务会出现在这里。" />}
      </section>
    </>
  </div>
}

function StandardsSummary() {
  const { demo } = useSession()
  const query = useQuery({ queryKey: ['standards-summary', demo], queryFn: () => demo ? Promise.resolve(demoStandards) : getStandards() })
  if (query.isLoading) return <LoadingState label="正在读取标准库真实状态…" />
  if (query.isError) return <EmptyState icon="error" title="标准状态读取失败" description="未使用演示数字替代。" />
  const standards = query.data || []
  const waiting = standards.filter(item => !['published', 'ready'].includes(item.graph_status || '')).length
  const processing = standards.filter(item => ['processing', 'pending'].includes(item.knowledge_status || '')).length
  const published = standards.filter(item => item.graph_status === 'published').length
  return <section className="standard-review-summary"><div><span>待确认标准</span><b>{waiting}</b><p>关系化要求尚未发布的标准</p></div><div><span>知识化处理中</span><b>{processing}</b><p>提取完成后进入人工确认</p></div><div><span>已发布标准</span><b>{published}</b><p>仅统计当前真实标准库</p></div><Link to="/standards">进入标准审核 <ArrowRight /></Link></section>
}
