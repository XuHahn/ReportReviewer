import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Modal, MultiSelect, TextInput, Textarea } from '@mantine/core'
import { FolderKanban, MoreHorizontal, Pencil, Plus, Trash2, UsersRound } from 'lucide-react'
import { Menu } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { apiErrorMessage, createProjectGroup, deleteProjectGroup, getProjectGroups, listUsers, updateProjectGroup } from '../api'
import { useSession } from '../App'
import { demoGroups, demoUser } from '../mockData'
import type { ProjectGroup } from '../types'
import { EmptyState, LoadingState, PageHeader } from '../components/common'

export default function GroupsPage() {
  const { demo } = useSession(); const queryClient = useQueryClient(); const [editing, setEditing] = useState<ProjectGroup | 'new' | null>(null); const [removeTarget, setRemoveTarget] = useState<ProjectGroup | null>(null)
  const groupsQuery = useQuery({ queryKey: ['groups', demo], queryFn: () => demo ? Promise.resolve(demoGroups) : getProjectGroups() })
  const usersQuery = useQuery({ queryKey: ['users', demo], queryFn: () => demo ? Promise.resolve({ users: [demoUser, { employee_id: 'R-0311', name: '王复核', role: 'reviewer' as const }, { employee_id: 'R-0412', name: '陈审核', role: 'reviewer' as const }], total: 3 }) : listUsers() })
  async function remove() { if (!removeTarget) return; try { if (!demo) await deleteProjectGroup(removeTarget.id); notifications.show({ color: 'green', message: '项目组已删除' }); setRemoveTarget(null); queryClient.invalidateQueries({ queryKey: ['groups'] }) } catch (error) { notifications.show({ color: 'red', message: apiErrorMessage(error) }) } }
  return <div className="page groups-page"><PageHeader eyebrow="OWNERSHIP & SCOPE" title="项目组" description="分而治之，合而协同。" actions={<button className="primary-button" onClick={() => setEditing('new')}><Plus size={16}/>新建项目组</button>}/>
    {groupsQuery.isLoading ? <LoadingState label="正在读取项目组…"/> : groupsQuery.isError ? <EmptyState icon="error" title="项目组读取失败" description="请检查服务状态后重试；失败不会显示为真实空列表。"/> : <div className="group-grid">{groupsQuery.data?.map(group => { const members = group.member_ids || group.members || []; return <article key={group.id}><div className="group-card-head"><span><FolderKanban/></span><Menu position="bottom-end"><Menu.Target><button className="icon-button" aria-label={`管理项目组 ${group.name}`}><MoreHorizontal/></button></Menu.Target><Menu.Dropdown><Menu.Item leftSection={<Pencil size={14}/>} onClick={() => setEditing(group)}>编辑</Menu.Item><Menu.Item color="red" leftSection={<Trash2 size={14}/>} onClick={() => setRemoveTarget(group)}>删除</Menu.Item></Menu.Dropdown></Menu></div><span>{group.category || '未分类'}</span><h2>{group.name}</h2><p>{group.description || '暂无说明'}</p><div className="group-members"><UsersRound/><b>{group.member_count || members.length} 位成员</b><span>{members.slice(0, 3).map(id => <i key={id}>{id.slice(-2)}</i>)}</span></div></article>})}</div>}
    {!groupsQuery.isLoading && !groupsQuery.isError && !groupsQuery.data?.length && <EmptyState title="还没有项目组" description="创建项目组后，可以把审核任务归入对应业务范围。"/>}
    <Modal opened={Boolean(editing)} onClose={() => setEditing(null)} title={editing === 'new' ? '新建项目组' : '编辑项目组'} size="lg" closeButtonProps={{ 'aria-label': '关闭项目组编辑窗口' }}><GroupForm group={editing === 'new' ? undefined : editing || undefined} users={usersQuery.data?.users || []} demo={demo} onDone={() => { setEditing(null); queryClient.invalidateQueries({ queryKey: ['groups'] }) }}/></Modal>
    <Modal opened={Boolean(removeTarget)} onClose={() => setRemoveTarget(null)} title="删除项目组？" centered closeButtonProps={{ 'aria-label': '关闭删除项目组窗口' }}><p>历史任务会保留原项目组名称，但新任务不能再选择该项目组。</p><div className="modal-actions"><button className="secondary-button" onClick={() => setRemoveTarget(null)}>取消</button><button className="danger-button" onClick={remove}>确认删除</button></div></Modal>
  </div>
}

function GroupForm({ group, users, demo, onDone }: { group?: ProjectGroup; users: Array<{ employee_id: string; name?: string }>; demo: boolean; onDone: () => void }) {
  const [name, setName] = useState(group?.name || ''); const [category, setCategory] = useState(group?.category || ''); const [description, setDescription] = useState(group?.description || ''); const [members, setMembers] = useState<string[]>(group?.member_ids || group?.members || []); const [busy, setBusy] = useState(false)
  async function save() { setBusy(true); try { if (!demo) { if (group) await updateProjectGroup(group.id, { name, category, description, member_ids: members }); else await createProjectGroup(name, description, members, category) } notifications.show({ color: 'green', message: group ? '项目组已更新' : '项目组已创建' }); onDone() } catch (error) { notifications.show({ color: 'red', message: apiErrorMessage(error) }) } finally { setBusy(false) } }
  return <div className="entity-form"><div className="form-grid"><TextInput label="项目组名称" required value={name} onChange={e => setName(e.currentTarget.value)}/><TextInput label="业务分类" value={category} onChange={e => setCategory(e.currentTarget.value)}/></div><Textarea label="说明" minRows={3} value={description} onChange={e => setDescription(e.currentTarget.value)}/><MultiSelect label="成员" searchable value={members} onChange={setMembers} data={users.map(user => ({ value: user.employee_id, label: `${user.name || user.employee_id} · ${user.employee_id}` }))}/><div className="modal-actions"><button className="secondary-button" onClick={onDone}>取消</button><button className="primary-button" disabled={!name.trim() || busy} onClick={save}>{busy ? '正在保存…' : '保存项目组'}</button></div></div>
}
