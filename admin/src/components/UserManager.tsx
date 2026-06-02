import { useState, useEffect } from 'react'
import { listUsers, createUser, updateUserRole, updateUserName, deleteUser } from '../api'
import type { User, UserRole } from '../types'

const ROLE_LABELS: Record<UserRole, string> = { admin: '管理员', reviewer: '审核员', viewer: '查看者' }
const ROLE_CLASS: Record<UserRole, string> = { admin: 'role-admin', reviewer: 'role-reviewer', viewer: 'role-viewer' }
const ROLE_COLOR: Record<UserRole, string> = { admin: '#175cd3', reviewer: '#7c3aed', viewer: '#667085' }

export default function UserManager() {
  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [modalOpen, setModalOpen] = useState(false)
  const [editingUser, setEditingUser] = useState<User | null>(null)
  const [form, setForm] = useState({ employee_id: '', name: '', role: 'viewer' as UserRole })
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState<string | null>(null)

  async function loadUsers() {
    setLoading(true)
    try { const r = await listUsers(); setUsers(r.users) }
    catch { setError('加载失败') }
    finally { setLoading(false) }
  }

  useEffect(() => { loadUsers() }, [])

  const filtered = search.trim()
    ? users.filter(u => u.employee_id.includes(search.trim()) || (u.name || '').includes(search.trim()))
    : users

  function openNew() {
    setEditingUser(null)
    setForm({ employee_id: '', name: '', role: 'viewer' })
    setModalOpen(true)
  }

  function openEdit(u: User) {
    setEditingUser(u)
    setForm({ employee_id: u.employee_id, name: u.name || '', role: u.role })
    setModalOpen(true)
  }

  async function handleSave() {
    if (!form.employee_id.trim()) return
    setSaving(true)
    try {
      if (editingUser) {
        await updateUserRole(editingUser.employee_id, form.role)
        if (form.name.trim() !== (editingUser.name || '')) {
          await updateUserName(editingUser.employee_id, form.name.trim())
        }
        setUsers(prev => prev.map(u => u.employee_id === editingUser.employee_id ? { ...u, name: form.name.trim(), role: form.role } : u))
      } else {
        await createUser(form.employee_id.trim(), form.role, form.name.trim())
        setUsers(prev => [...prev, { employee_id: form.employee_id.trim(), role: form.role, name: form.name.trim(), created_at: new Date().toISOString() }])
      }
      setModalOpen(false)
    } catch (e: any) { setError(e?.response?.data?.detail || '保存失败') }
    finally { setSaving(false) }
  }

  async function handleDelete(employeeId: string) {
    if (!window.confirm('确定删除此用户？此操作不可撤销。')) return
    setDeleting(employeeId)
    try {
      await deleteUser(employeeId)
      setUsers(prev => prev.filter(u => u.employee_id !== employeeId))
    } catch { setError('删除失败') }
    finally { setDeleting(null) }
  }

  if (loading) return <div className="history-loading">加载中...</div>

  return (
    <div>
      <div className="pg-toolbar">
        <h2 className="section-title" style={{ margin: 0, flex: 1 }}>用户管理</h2>
      </div>

      {error && <div className="upload-error">{error}</div>}

      <div style={{ display: 'flex', gap: 8, marginBottom: 20, marginTop: 12 }}>
        <input className="filter-input" style={{ flex: 1 }} placeholder="搜索工号或姓名..." value={search} onChange={e => setSearch(e.target.value)} />
        <button className="pg-add-btn" onClick={openNew}>+ 新增用户</button>
      </div>

      {filtered.length === 0 ? (
        <div className="pg-empty">暂无用户</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {filtered.map(u => (
            <div key={u.employee_id} className="u-card">
              <div className="u-avatar" style={{ background: ROLE_COLOR[u.role] }}>{(u.name || u.employee_id)[0]}</div>
              <div className="u-info">
                <div className="u-name">{u.name || '未命名'}</div>
                <div className="u-id">{u.employee_id}</div>
              </div>
              <div className="u-meta">
                <span className={`role-badge ${ROLE_CLASS[u.role]}`}>{ROLE_LABELS[u.role]}</span>
                <span className="u-time">{u.created_at ? new Date(u.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-'}</span>
                <button className="pg-btn" onClick={() => openEdit(u)}>编辑</button>
                <button className="pg-btn pg-btn-del" disabled={deleting === u.employee_id} onClick={() => handleDelete(u.employee_id)}>
                  {deleting === u.employee_id ? '...' : '删除'}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {modalOpen && (
        <div className="modal-overlay" onClick={() => setModalOpen(false)}>
          <div className="modal-container" onClick={e => e.stopPropagation()} style={{ maxWidth: 400 }}>
            <div className="modal-header">
              <span className="modal-title">{editingUser ? '编辑用户' : '新增用户'}</span>
              <button onClick={() => setModalOpen(false)} style={{ background: 'none', border: 'none', fontSize: 20, cursor: 'pointer', color: '#667085' }} aria-label="关闭">✕</button>
            </div>
            <div className="modal-body" style={{ padding: 20 }}>
              {!editingUser && (
                <>
                  <label style={{ display: 'block', marginBottom: 4, fontSize: 12, color: '#667085', fontWeight: 500 }}>工号 *</label>
                  <input className="filter-input" style={{ width: '100%', marginBottom: 12 }} value={form.employee_id} onChange={e => setForm({ ...form, employee_id: e.target.value })} placeholder="如 GDJL12345" />
                </>
              )}
              {editingUser && (
                <div style={{ marginBottom: 12, fontSize: 13, color: '#667085' }}>
                  工号: <strong style={{ color: '#344054' }}>{editingUser.employee_id}</strong>
                </div>
              )}
              <label style={{ display: 'block', marginBottom: 4, fontSize: 12, color: '#667085', fontWeight: 500 }}>姓名</label>
              <input className="filter-input" style={{ width: '100%', marginBottom: 12 }} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="可选" />
              <label style={{ display: 'block', marginBottom: 4, fontSize: 12, color: '#667085', fontWeight: 500 }}>角色</label>
              <select className="filter-select" style={{ width: '100%' }} value={form.role} onChange={e => setForm({ ...form, role: e.target.value as UserRole })}>
                <option value="viewer">查看者</option>
                <option value="reviewer">审核员</option>
                <option value="admin">管理员</option>
              </select>
            </div>
            <div style={{ padding: '12px 20px', borderTop: '1px solid #f0f2f5', display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button className="pg-btn" onClick={() => setModalOpen(false)}>取消</button>
              <button className="pg-add-btn" disabled={saving || !form.employee_id.trim()} onClick={handleSave}>{saving ? '保存中...' : '保存'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
