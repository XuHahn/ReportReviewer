import { useState, useEffect, FormEvent } from 'react'
import { listUsers, createUser, updateUserRole, updateUserName } from '../api'
import type { User, UserRole } from '../types'

const ROLE_LABELS: Record<UserRole, string> = {
  admin: '管理员',
  reviewer: '审核员',
  standard_reviewer: '标准审核员',
  viewer: '查看者',
}

export default function UserManagement() {
  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [newId, setNewId] = useState('')
  const [newName, setNewName] = useState('')
  const [newRole, setNewRole] = useState<UserRole>('viewer')
  const [adding, setAdding] = useState(false)
  const [editingName, setEditingName] = useState<string | null>(null)

  async function loadUsers() {
    try {
      const res = await listUsers()
      setUsers(res.users)
    } catch (err: any) {
      setError(err.response?.data?.detail || '加载用户列表失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadUsers() }, [])

  async function handleAdd(e: FormEvent) {
    e.preventDefault()
    const id = newId.trim()
    if (!id) return
    setAdding(true)
    try {
      const user = await createUser(id, newRole)
      // Also set name if provided
      if (newName.trim()) await updateUserName(id, newName.trim())
      user.name = newName.trim()
      setUsers((prev) => [...prev, user])
      setNewId(''); setNewName('')
      setNewRole('viewer')
    } catch (err: any) {
      setError(err.response?.data?.detail || '添加用户失败')
    } finally {
      setAdding(false)
    }
  }

  async function handleRoleChange(employeeId: string, role: UserRole) {
    try {
      const updated = await updateUserRole(employeeId, role)
      setUsers((prev) => prev.map((u) => (u.employee_id === employeeId ? { ...u, role: updated.role } : u)))
    } catch (err: any) {
      setError(err.response?.data?.detail || '修改角色失败')
    }
  }

  return (
    <div className="user-management">
      <h2 className="section-title">用户管理</h2>

      {error && <div className="upload-error">{error}</div>}

      <form className="user-add-form" onSubmit={handleAdd}>
        <input className="filter-input" type="text" placeholder="工号" value={newId} onChange={(e) => setNewId(e.target.value)} />
        <input className="filter-input" type="text" placeholder="姓名" value={newName} onChange={(e) => setNewName(e.target.value)} />
        <select className="filter-select" value={newRole} onChange={(e) => setNewRole(e.target.value as UserRole)}>
          <option value="viewer">查看者</option>
          <option value="reviewer">审核员</option>
          <option value="standard_reviewer">标准审核员</option>
          <option value="admin">管理员</option>
        </select>
        <button className="filter-btn" type="submit" disabled={adding}>
          {adding ? '添加中...' : '新增用户'}
        </button>
      </form>

      {loading ? (
        <div className="history-loading"><div className="spinner"/> 加载中...</div>
      ) : (
        <table className="user-table">
          <thead>
            <tr>
              <th>工号</th>
              <th>姓名</th>
              <th>角色</th>
              <th>创建时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.employee_id}>
                <td>{u.employee_id}</td>
                <td>
                  {editingName === u.employee_id ? (
                    <input className="filter-input" value={u.name || ''} onChange={(e) => setUsers(prev => prev.map(x => x.employee_id === u.employee_id ? {...x, name: e.target.value} : x))} onBlur={(e) => { updateUserName(u.employee_id, e.target.value); setEditingName(null) }} onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }} autoFocus />
                  ) : (
                    <span onClick={() => setEditingName(u.employee_id)} style={{cursor:'pointer',color:u.name?'#475467':'#98a2b3'}}>{u.name || '点击设置'}</span>
                  )}
                </td>
                <td>
                  <select
                    className="filter-select"
                    value={u.role}
                    onChange={(e) => handleRoleChange(u.employee_id, e.target.value as UserRole)}
                  >
                    <option value="viewer">查看者</option>
                    <option value="reviewer">审核员</option>
                    <option value="standard_reviewer">标准审核员</option>
                    <option value="admin">管理员</option>
                  </select>
                </td>
                <td>{u.created_at ? new Date(u.created_at).toLocaleString() : '-'}</td>
                <td>{ROLE_LABELS[u.role]}</td>
              </tr>
            ))}
            {users.length === 0 && (
              <tr>
                <td colSpan={5} className="history-empty">暂无用户</td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  )
}
