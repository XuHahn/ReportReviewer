import { useState, useEffect } from 'react'
import { getProjectGroups, createProjectGroup, updateProjectGroup, deleteProjectGroup, listUsers, getTags, createTag, updateTag, deleteTag, getCurrentUser } from '../api'
import type { ProjectGroup, User, Tag } from '../types'
import { logger } from '../logger'

const ROLE_LABELS: Record<string, string> = { admin: '管理员', reviewer: '审核员', viewer: '查看者' }

export default function ProjectGroupManager() {
  const [groups, setGroups] = useState<ProjectGroup[]>([])
  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [formError, setFormError] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [form, setForm] = useState({ name: '', description: '', category: '其他', member_ids: [] as string[] })
  const [search, setSearch] = useState('')
  const [roleFilter, setRoleFilter] = useState('all')
  const [saving, setSaving] = useState(false)
  const [tags, setTags] = useState<Tag[]>([])
  const [tagDropdown, setTagDropdown] = useState(false)
  const [tagSearch, setTagSearch] = useState('')
  const [tagModalOpen, setTagModalOpen] = useState(false)
  const [selectedTagId, setSelectedTagId] = useState<string | null>(null)
  const [tagModalNewName, setTagModalNewName] = useState('')
  const [tagModalEditName, setTagModalEditName] = useState('')
  const [currentUserId, setCurrentUserId] = useState('')

  async function fetchData() {
    setLoading(true)
    try {
      const [g, u, t] = await Promise.all([getProjectGroups(), listUsers(), getTags()])
      setGroups(g)
      setUsers(u.users || [])
      setTags(t || [])
    } catch {
      setError('加载项目组数据失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    getCurrentUser().then(u => setCurrentUserId(u.employee_id)).catch((err) => { logger.error('Failed to fetch current user', {}, err) })
    fetchData()
  }, [])

  function openNew() {
    setEditingId(null)
    setForm({ name: '', description: '', category: '其他', member_ids: currentUserId ? [currentUserId] : [] })
    setSearch('')
    setRoleFilter('all')
    setFormError('')
    setShowForm(true)
  }

  function openEdit(g: ProjectGroup) {
    setEditingId(g.id)
    setForm({ name: g.name, description: g.description || '', category: g.category || '其他', member_ids: g.members || [] })
    setSearch('')
    setRoleFilter('all')
    setFormError('')
    setShowForm(true)
  }

  function toggleMember(eid: string) {
    setForm(f => ({
      ...f,
      member_ids: f.member_ids.includes(eid) ? f.member_ids.filter(m => m !== eid) : [...f.member_ids, eid],
    }))
  }

  async function handleSave() {
    if (!form.name.trim()) { setFormError('请填写项目组名称'); return }
    if (!form.description.trim()) { setFormError('请填写项目组描述'); return }
    if (!form.category.trim()) { setFormError('请选择分类标签'); return }
    if (form.member_ids.length === 0) { setFormError('请至少添加一位成员'); return }
    setFormError('')
    setSaving(true)
    try {
      // auto-create tag if category is new
      if (!tags.find(t => t.name === form.category.trim())) {
        const r = await createTag(form.category.trim())
        setTags([...tags, { id: r.id, name: r.name, created_by: '', created_at: '' }])
        form.category = r.name
      }
      if (editingId) {
        await updateProjectGroup(editingId, form.name, form.description, form.member_ids, form.category)
      } else {
        await createProjectGroup(form.name, form.description, form.member_ids, form.category)
      }
      setShowForm(false)
      await fetchData()
    } catch {
      setError('保存项目组失败')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(id: string) {
    if (!window.confirm('确定删除此项目组？')) return
    try {
      await deleteProjectGroup(id)
      await fetchData()
    } catch {
      setError('删除项目组失败')
    }
  }

  const filteredUsers = search.trim()
    ? users.filter(u => u.employee_id.includes(search) || (u.name || '').includes(search))
    : users

  if (loading) return <div className="history-loading">加载中...</div>

  return (
    <div>
      <div className="pg-toolbar">
        <h2 className="section-title" style={{ margin: 0, flex: 1 }}>项目组管理</h2>
        <button className="pg-btn" onClick={fetchData}>刷新</button>
        <button className="pg-add-btn" onClick={openNew}>+ 新建项目组</button>
      </div>

      {error && <div className="upload-error" style={{ marginTop: 16 }}>{error}</div>}

      {groups.length === 0 ? (
        <div className="pg-empty">暂无项目组，请点击右上角"新建项目组"创建</div>
      ) : (
        <div className="pg-grid" style={{ marginTop: 20 }}>
          {groups.map(g => (
            <div key={g.id} className="pg-card">
              <div className="pg-card-name">{g.name}<span className="pg-card-category">{g.category || '其他'}</span></div>
              {g.description && <div className="pg-card-desc">{g.description}</div>}
              <div className="pg-card-members">
                {(g.members || []).map(eid => {
                  const u = users.find(x => x.employee_id === eid)
                  return (
                    <span key={eid} className="member-chip">
                      {u?.name || eid}
                      <span style={{ fontSize: 10, color: '#84a5d9', marginLeft: 4 }}>{eid}</span>
                      {eid === g.created_by && (
                        <span style={{ fontSize: 9, background: '#175cd3', color: '#fff', padding: '0 4px', borderRadius: 4, marginLeft: 4 }}>创建者</span>
                      )}
                    </span>
                  )
                })}
              </div>
              <div className="pg-card-meta">
                {(g.members || []).length} 位成员
                {g.created_by && (
                  <span style={{ marginLeft: 12 }}>
                    创建人: {users.find(u => u.employee_id === g.created_by)?.name || g.created_by}
                  </span>
                )}
              </div>
              <div className="pg-card-actions">
                <button className="pg-btn" onClick={() => openEdit(g)}>编辑</button>
                <button className="pg-btn pg-btn-del" onClick={() => handleDelete(g.id)}>删除</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showForm && (
        <div className="modal-overlay" onClick={() => setShowForm(false)}>
          <div className="modal-container" onClick={e => e.stopPropagation()} style={{ maxWidth: 520 }}>
            <div className="modal-header">
              <span className="modal-title">{editingId ? '编辑项目组' : '新建项目组'}</span>
              <button onClick={() => setShowForm(false)} style={{ background: 'none', border: 'none', fontSize: 20, cursor: 'pointer', color: '#667085' }} aria-label="关闭">✕</button>
            </div>
            <div className="modal-body" style={{ padding: 20 }}>
              <label>项目组名称 *</label>
              <input className="filter-input" style={{ width: '100%', marginBottom: 12 }} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="如：2025年度EMC测试项目" />
              <label>描述 *</label>
              <input className="filter-input" style={{ width: '100%', marginBottom: 12 }} value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} placeholder="请输入项目组描述" />
              <label>分类标签 *</label>
              <div style={{ display: 'flex', gap: 8, marginBottom: 12, position: 'relative' }}>
                <div style={{ flex: 1, position: 'relative' }}>
                  <input className="filter-input" style={{ width: '100%' }}
                    value={form.category}
                    onChange={e => {
                      setForm({ ...form, category: e.target.value })
                      setTagSearch(e.target.value)
                      setTagDropdown(true)
                    }}
                    onFocus={() => { setTagSearch(form.category); setTagDropdown(true) }}
                    onBlur={() => setTimeout(() => setTagDropdown(false), 150)}
                    placeholder="输入或选择标签"
                  />
                  {tagDropdown && (() => {
                    const matches = tags.filter(t => !tagSearch || t.name.includes(tagSearch))
                    if (matches.length === 0) return null
                    return (
                      <div style={{
                        position: 'absolute', top: '100%', left: 0, right: 0,
                        background: '#fff', border: '1px solid #d0d5dd', borderRadius: 8,
                        maxHeight: 150, overflowY: 'auto', zIndex: 50, boxShadow: '0 4px 12px rgba(0,0,0,.1)',
                      }}>
                        {matches.map(t => (
                          <div key={t.id}
                            onMouseDown={() => { setForm({ ...form, category: t.name }); setTagDropdown(false) }}
                            style={{ padding: '6px 12px', cursor: 'pointer', fontSize: 13,
                              background: form.category === t.name ? '#f0f6ff' : undefined,
                            }}
                            onMouseEnter={e => { (e.target as HTMLElement).style.background = '#f0f2f5' }}
                            onMouseLeave={e => { (e.target as HTMLElement).style.background = form.category === t.name ? '#f0f6ff' : '' }}
                          >
                            {t.name}
                          </div>
                        ))}
                      </div>
                    )
                  })()}
                </div>
                <button type="button" className="pg-btn" style={{ whiteSpace: 'nowrap' }} onClick={() => { setTagModalOpen(true); setSelectedTagId(null); setTagModalNewName(''); setTagModalEditName('') }}>
                  管理标签
                </button>
              </div>
              <label>添加成员 *</label>
              <input className="filter-input" style={{ width: '100%', marginBottom: 8 }} value={search} onChange={e => setSearch(e.target.value)} placeholder="按姓名或工号搜索..." />
              <div className="pg-role-tabs">
                {['all', 'admin', 'reviewer', 'viewer'].map(r => (
                  <button key={r} className={`pg-role-tab ${roleFilter === r ? 'pg-role-tab-on' : ''}`} onClick={() => setRoleFilter(r)}>
                    {r === 'all' ? `全部 (${users.length})` : `${ROLE_LABELS[r]} (${users.filter(u => u.role === r).length})`}
                  </button>
                ))}
              </div>
              <div className="pg-member-bar">
                <span className="pg-member-count">已选 {form.member_ids.length}/{users.length}</span>
                <div className="pg-member-chips">
                  {form.member_ids.map(eid => {
                    const u = users.find(x => x.employee_id === eid)
                    return (
                      <span key={eid} className="member-chip" style={{ cursor: 'pointer' }} onClick={() => toggleMember(eid)}>
                        {u?.name || eid}<span style={{ marginLeft: 4, color: '#98a2b3' }}>×</span>
                      </span>
                    )
                  })}
                </div>
              </div>
              <div className="pg-member-list">
                {filteredUsers.filter(u => roleFilter === 'all' || u.role === roleFilter).map(u => (
                  <label key={u.employee_id} className="pg-member-item" style={form.member_ids.includes(u.employee_id) ? { background: '#f0f6ff' } : {}}>
                    <input type="checkbox" checked={form.member_ids.includes(u.employee_id)} onChange={() => toggleMember(u.employee_id)} />
                    <span style={{ fontWeight: 500 }}>{u.name || u.employee_id}</span>
                    <span style={{ fontSize: 11, color: '#98a2b3', marginLeft: 6 }}>{u.employee_id}</span>
                    <span className="pg-role-tag">{ROLE_LABELS[u.role] || u.role}</span>
                  </label>
                ))}
                {filteredUsers.filter(u => roleFilter === 'all' || u.role === roleFilter).length === 0 && (
                  <div style={{ padding: 12, textAlign: 'center', color: '#98a2b3', fontSize: 13 }}>无匹配用户</div>
                )}
              </div>
            </div>
            <div className="modal-footer" style={{ padding: '12px 20px', borderTop: '1px solid #f0f0f0', display: 'flex', justifyContent: 'flex-end', gap: 8, alignItems: 'center' }}>
              {formError && <span style={{ color: '#d92d20', fontSize: 13, flex: 1 }}>{formError}</span>}
              <button className="pg-btn" onClick={() => setShowForm(false)}>取消</button>
              <button className="pg-add-btn" disabled={saving || !form.name.trim()} onClick={handleSave}>{saving ? '保存中...' : '保存'}</button>
            </div>
          </div>
        </div>
      )}

      {tagModalOpen && (
        <div className="modal-overlay" onClick={() => setTagModalOpen(false)}>
          <div className="modal-container" onClick={e => e.stopPropagation()} style={{ maxWidth: 560, height: 400 }}>
            <div className="modal-header">
              <span className="modal-title">管理标签</span>
              <button onClick={() => setTagModalOpen(false)} style={{ background: 'none', border: 'none', fontSize: 20, cursor: 'pointer', color: '#667085' }} aria-label="关闭">✕</button>
            </div>
            <div style={{ display: 'flex', height: 'calc(100% - 52px)' }}>
              <div style={{ width: 170, borderRight: '1px solid #f0f2f5', overflowY: 'auto', padding: '8px 0', flexShrink: 0 }}>
                <div
                  onClick={() => { setSelectedTagId(null); setTagModalNewName('') }}
                  style={{
                    padding: '8px 14px', cursor: 'pointer', fontSize: 13, fontWeight: 500,
                    color: '#175cd3', borderBottom: '1px solid #f0f2f5', marginBottom: 4,
                    background: selectedTagId === null ? '#f0f6ff' : undefined,
                    borderLeft: selectedTagId === null ? '3px solid #175cd3' : '3px solid transparent',
                  }}
                >
                  + 新增标签
                </div>
                {tags.map(t => (
                  <div key={t.id}
                    onClick={() => { setSelectedTagId(t.id); setTagModalEditName(t.name) }}
                    style={{
                      padding: '8px 14px', cursor: 'pointer', fontSize: 13,
                      background: selectedTagId === t.id ? '#f0f6ff' : undefined,
                      borderLeft: selectedTagId === t.id ? '3px solid #175cd3' : '3px solid transparent',
                      fontWeight: selectedTagId === t.id ? 500 : 400,
                    }}
                  >
                    {t.name}
                  </div>
                ))}
                {tags.length === 0 && <div style={{ padding: 12, textAlign: 'center', color: '#98a2b3', fontSize: 12 }}>暂无标签</div>}
              </div>
              <div style={{ flex: 1, padding: 16, overflowY: 'auto' }}>
                {selectedTagId ? (() => {
                  const t = tags.find(x => x.id === selectedTagId)
                  if (!t) return null
                  return (
                    <div>
                      <div style={{ fontSize: 11, color: '#98a2b3', marginBottom: 12, textTransform: 'uppercase', letterSpacing: '.5px' }}>编辑标签</div>
                      <label style={{ fontSize: 12, color: '#667085' }}>名称</label>
                      <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                        <input className="filter-input" style={{ flex: 1 }}
                          value={tagModalEditName}
                          onChange={e => setTagModalEditName(e.target.value)}
                          onKeyDown={async e => {
                            if (e.key === 'Enter') {
                              await updateTag(t.id, tagModalEditName.trim())
                              setTags(tags.map(x => x.id === t.id ? { ...x, name: tagModalEditName.trim() } : x))
                              if (form.category === t.name) setForm({ ...form, category: tagModalEditName.trim() })
                            }
                          }}
                        />
                        <button className="pg-add-btn" style={{ fontSize: 12 }} onClick={async () => {
                          await updateTag(t.id, tagModalEditName.trim())
                          setTags(tags.map(x => x.id === t.id ? { ...x, name: tagModalEditName.trim() } : x))
                          if (form.category === t.name) setForm({ ...form, category: tagModalEditName.trim() })
                        }}>保存</button>
                      </div>
                      <div style={{ marginTop: 16, padding: 12, background: '#fef3f2', borderRadius: 8, border: '1px solid #fecdca' }}>
                        <div style={{ fontSize: 12, color: '#b42318', marginBottom: 4 }}>删除此标签</div>
                        <div style={{ fontSize: 11, color: '#667085', marginBottom: 8 }}>删除后，使用此标签的项目组分类不会受影响。</div>
                        <button className="pg-btn pg-btn-del" style={{ fontSize: 11 }} onClick={async () => {
                          if (!confirm(`确定删除标签"${t.name}"?`)) return
                          await deleteTag(t.id)
                          setTags(tags.filter(x => x.id !== t.id))
                          if (form.category === t.name) setForm({ ...form, category: '' })
                          setSelectedTagId(null)
                        }}>删除</button>
                      </div>
                    </div>
                  )
                })() : (
                  <div>
                    <div style={{ fontSize: 11, color: '#98a2b3', marginBottom: 12, textTransform: 'uppercase', letterSpacing: '.5px' }}>新增标签</div>
                    <label style={{ fontSize: 12, color: '#667085' }}>标签名称</label>
                    <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                      <input className="filter-input" style={{ flex: 1 }}
                        value={tagModalNewName}
                        onChange={e => setTagModalNewName(e.target.value)}
                        placeholder="输入新标签名称"
                        onKeyDown={async e => {
                          if (e.key === 'Enter' && tagModalNewName.trim()) {
                            const r = await createTag(tagModalNewName.trim())
                            setTags([...tags, { id: r.id, name: r.name, created_by: '', created_at: '' }])
                            setForm({ ...form, category: r.name })
                            setTagModalNewName('')
                          }
                        }}
                      />
                      <button className="pg-add-btn" style={{ fontSize: 12 }} disabled={!tagModalNewName.trim()} onClick={async () => {
                        if (!tagModalNewName.trim()) return
                        const r = await createTag(tagModalNewName.trim())
                        setTags([...tags, { id: r.id, name: r.name, created_by: '', created_at: '' }])
                        setForm({ ...form, category: r.name })
                        setTagModalNewName('')
                      }}>添加</button>
                    </div>
                    <div style={{ marginTop: 24, textAlign: 'center', color: '#98a2b3', fontSize: 12 }}>
                      选择左侧标签进行编辑或删除，或在此创建新标签。
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
