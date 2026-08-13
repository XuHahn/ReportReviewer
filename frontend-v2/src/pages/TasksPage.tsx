import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Menu, Modal, Select, TextInput } from '@mantine/core'
import { ArrowRight, CopyPlus, MoreHorizontal, Plus, Search, Trash2 } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { notifications } from '@mantine/notifications'
import { apiErrorMessage, createDocumentSet, createDocumentSetRevision, deleteDocumentSet, listDocumentSets } from '../api'
import { useSession } from '../App'
import { demoSets } from '../mockData'
import { EmptyState, LoadingState, PageHeader, StatusPill, formatDate } from '../components/common'
import { taskStartStage } from '../reviewLogic'

export default function TasksPage() {
  const { demo, user } = useSession()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<string | null>('all')
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const setsQuery = useQuery({ queryKey: ['sets', demo], queryFn: () => demo ? Promise.resolve({ sets: demoSets, total: demoSets.length }) : listDocumentSets(), refetchOnMount: 'always', refetchOnWindowFocus: true })
  const filtered = useMemo(() => (setsQuery.data?.sets || []).filter(item => {
    const text = `${item.set_id} ${item.title || ''} ${item.project_group_name || ''}`.toLowerCase()
    const statusMatch = !status || status === 'all' || (status === 'active' ? item.status !== 'reviewed' : item.status === status)
    return statusMatch && text.includes(query.trim().toLowerCase())
  }), [setsQuery.data, query, status])

  const remove = useMutation({ mutationFn: (id: string) => demo ? Promise.resolve({ deleted: true }) : deleteDocumentSet(id), onSuccess: () => { setDeleteTarget(null); queryClient.invalidateQueries({ queryKey: ['sets'] }); queryClient.invalidateQueries({ queryKey: ['stats', demo] }); notifications.show({ color: 'green', message: '任务已删除' }) }, onError: error => notifications.show({ color: 'red', message: apiErrorMessage(error) }) })

  async function createTask() {
    if (demo) { notifications.show({ color: 'blue', title: '当前是演示预览', message: '请退出演示并使用真实工号登录后新建审核。' }); return }
    try { const result = await createDocumentSet(); await queryClient.invalidateQueries({ queryKey: ['sets', demo] }); await queryClient.invalidateQueries({ queryKey: ['stats', demo] }); navigate(`/tasks/${result.set_id}/intake`) }
    catch (error) { notifications.show({ color: 'red', title: '新建失败', message: apiErrorMessage(error) }) }
  }
  async function revise(id: string) {
    try { if (!demo) await createDocumentSetRevision(id); await queryClient.invalidateQueries({ queryKey: ['sets', demo] }); await queryClient.invalidateQueries({ queryKey: ['stats', demo] }); navigate(`/tasks/${id}/intake`) }
    catch (error) { notifications.show({ color: 'red', message: apiErrorMessage(error) }) }
  }

  return <div className="page tasks-page">
    <PageHeader eyebrow="AUDIT QUEUE" title="审核任务" description="案头之事，笔下之责。" actions={<button className="primary-button" onClick={createTask}><Plus size={17}/>新建审核</button>} />
    <div className="task-toolbar"><TextInput leftSection={<Search size={16}/>} placeholder="搜索任务编号、产品或项目组" value={query} onChange={event => setQuery(event.currentTarget.value)} /><Select value={status} onChange={setStatus} allowDeselect={false} data={[{ value: 'active', label: '需要动作' }, { value: 'all', label: '全部状态' }, { value: 'incomplete', label: '资料准备' }, { value: 'locked', label: '已锁定' }, { value: 'reviewed', label: '审核完成' }]} /></div>
    {setsQuery.isLoading && <LoadingState label="正在读取真实审核任务…" />}
    {setsQuery.isError && <EmptyState icon="error" title="真实任务读取失败" description="页面不会回退到演示任务，请检查服务后重试。" />}
    {!setsQuery.isLoading && !setsQuery.isError && <>
    <div className="task-count"><b>{filtered.length}</b> 个任务符合当前条件</div>
    {filtered.length ? <div className="task-cards">{filtered.map(item => <article key={item.set_id} className="task-card">
      <div className="task-card-mark"><i /><i /><i /><i /></div>
      <div className="task-card-main"><div><StatusPill status={item.latest_run_status || item.status} /><small>{formatDate(item.updated_at || item.created_at)}</small></div><h2>{item.title || item.set_id}</h2><p>{item.set_id} · {item.project_group_name || '未设置项目组'}</p>
        <div className="task-metrics"><span><b>{item.documents_count || item.doc_count || 0}/4</b>业务资料</span><span><b>{item.finding_count || 0}</b>机器发现</span><span className={item.pending_count ? 'needs-action' : ''}><b>{item.pending_count || 0}</b>待处理</span></div>
      </div>
      <div className="task-card-actions"><Link className="secondary-button" to={`/tasks/${item.set_id}/${taskStartStage(item)}`}>打开任务 <ArrowRight size={16}/></Link>
        {(() => {
          const canDelete = user?.role === 'admin'
          const canRevise = item.status === 'reviewed'
          if (!canDelete && !canRevise) return <button className="icon-button" disabled aria-label="无可用操作"><MoreHorizontal size={18}/></button>
          return <Menu width={180} position="bottom-end"><Menu.Target><button className="icon-button" aria-label="更多任务操作"><MoreHorizontal size={18}/></button></Menu.Target><Menu.Dropdown>
            {canRevise && <Menu.Item leftSection={<CopyPlus size={15}/>} onClick={() => revise(item.set_id)}>创建修订</Menu.Item>}
            {canDelete && <Menu.Item color="red" leftSection={<Trash2 size={15}/>} onClick={() => setDeleteTarget(item.set_id)}>删除任务</Menu.Item>}
          </Menu.Dropdown></Menu>
        })()}
      </div>
    </article>)}</div> : <EmptyState icon="search" title="没有符合条件的任务" description="调整状态筛选或清除搜索关键词。" />}
    </>}
    <Modal opened={Boolean(deleteTarget)} onClose={() => setDeleteTarget(null)} title="删除任务？" centered closeButtonProps={{ 'aria-label': '关闭删除任务窗口' }}><p>该操作会删除此任务及其全部上传资料与审核结果，不能撤销。</p><div className="modal-actions"><button className="secondary-button" onClick={() => setDeleteTarget(null)}>取消</button><button className="danger-button" onClick={() => deleteTarget && remove.mutate(deleteTarget)} disabled={remove.isPending}>{remove.isPending ? '正在删除…' : '确认删除'}</button></div></Modal>
  </div>
}
