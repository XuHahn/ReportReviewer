import { useEffect, useState, useCallback } from 'react'
import { getStandards, deleteStandard, createStandard } from '../api'
import type { EmcStandard, StandardClause } from '../types'

const ORG_OPTIONS = [
  { value: '', label: '全部机构' },
  { value: 'CISPR', label: 'CISPR' },
  { value: 'GB', label: 'GB (国标)' },
  { value: 'EN', label: 'EN (欧盟)' },
  { value: 'FCC', label: 'FCC (美国)' },
  { value: 'IEC', label: 'IEC' },
]

const CAT_OPTIONS = [
  { value: '', label: '全部类别' },
  { value: 'radiated', label: '辐射发射' },
  { value: 'conducted', label: '传导发射' },
  { value: 'immunity', label: '抗扰度' },
]

const ORG_LABEL: Record<string, string> = Object.fromEntries(ORG_OPTIONS.map((o) => [o.value, o.label]))
const CAT_LABEL: Record<string, string> = Object.fromEntries(CAT_OPTIONS.map((o) => [o.value, o.label]))

export default function StandardsBrowser() {
  const [standards, setStandards] = useState<EmcStandard[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [keyword, setKeyword] = useState('')
  const [orgFilter, setOrgFilter] = useState('')
  const [catFilter, setCatFilter] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [saving, setSaving] = useState(false)

  const [form, setForm] = useState({
    code: '', title: '', organization: 'CISPR', category: 'radiated', version: '',
    clauses: [] as StandardClause[],
  })

  const fetchStandards = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getStandards({
        organization: orgFilter || undefined,
        category: catFilter || undefined,
        keyword: keyword || undefined,
      })
      setStandards(data)
    } catch (e: any) {
      setError(e?.message || '加载标准库失败')
    } finally {
      setLoading(false)
    }
  }, [keyword, orgFilter, catFilter])

  useEffect(() => { fetchStandards() }, [fetchStandards])

  const handleDelete = async (id: string) => {
    if (!confirm('确定删除此标准？（内置标准不可删除）')) return
    try {
      await deleteStandard(id)
      fetchStandards()
    } catch (e: any) { alert(e?.response?.data?.detail || '删除失败') }
  }

  const addClause = () => {
    setForm((f) => ({
      ...f,
      clauses: [...f.clauses, { clause: '', title: '', description: '', limit_table: [{ frequency: '', limit: '' }] }],
    }))
  }

  const removeClause = (idx: number) => {
    setForm((f) => ({ ...f, clauses: f.clauses.filter((_, i) => i !== idx) }))
  }

  const updateClause = (idx: number, field: keyof StandardClause, value: string) => {
    setForm((f) => ({
      ...f,
      clauses: f.clauses.map((c, i) => i === idx ? { ...c, [field]: value } : c),
    }))
  }

  const addLimitRow = (clauseIdx: number) => {
    setForm((f) => ({
      ...f,
      clauses: f.clauses.map((c, i) =>
        i === clauseIdx ? { ...c, limit_table: [...c.limit_table, {}] } : c),
    }))
  }

  const updateLimitCell = (clauseIdx: number, rowIdx: number, key: string, value: string) => {
    setForm((f) => ({
      ...f,
      clauses: f.clauses.map((c, ci) =>
        ci === clauseIdx ? {
          ...c,
          limit_table: c.limit_table.map((r, ri) => ri === rowIdx ? { ...r, [key]: value } : r),
        } : c),
    }))
  }

  const removeLimitRow = (clauseIdx: number, rowIdx: number) => {
    setForm((f) => ({
      ...f,
      clauses: f.clauses.map((c, ci) =>
        ci === clauseIdx ? { ...c, limit_table: c.limit_table.filter((_, ri) => ri !== rowIdx) } : c),
    }))
  }

  const handleSave = async () => {
    if (!form.code.trim() || !form.title.trim()) return alert('请输入标准编号和名称')
    setSaving(true)
    try {
      await createStandard(form)
      setShowForm(false)
      setForm({ code: '', title: '', organization: 'CISPR', category: 'radiated', version: '', clauses: [] })
      fetchStandards()
    } catch (e: any) {
      alert(e?.response?.data?.detail || e?.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const limitCols = (clause: StandardClause): string[] => {
    const keys = new Set<string>()
    clause.limit_table.forEach((row) => Object.keys(row).forEach((k) => keys.add(k)))
    return Array.from(keys)
  }

  return (
    <div className="standards-browser">
      <div className="rules-header">
        <h2 className="section-title">EMC 测试标准库</h2>
        <button className="filter-btn" onClick={() => setShowForm(true)}>+ 添加标准</button>
      </div>

      <div className="rules-toolbar">
        <select className="filter-select" value={orgFilter} onChange={(e) => setOrgFilter(e.target.value)}>
          {ORG_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <select className="filter-select" value={catFilter} onChange={(e) => setCatFilter(e.target.value)}>
          {CAT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <input className="filter-input" type="text" placeholder="搜索标准编号或名称..." value={keyword}
          onChange={(e) => setKeyword(e.target.value)} />
        <span className="rules-count">{standards.length} 条标准</span>
      </div>

      {error && <div className="upload-error">{error}</div>}
      {loading ? <div className="stats-loading"><div className="spinner"/> 加载中...</div>
        : standards.length === 0 ? <div className="stats-empty"><p>暂无标准数据</p></div>
        : (
          <div className="standards-list">
            {standards.map((std) => (
              <div key={std.id} className="standard-card">
                <div className="standard-card-header"
                  onClick={() => setExpandedId(expandedId === std.id ? null : std.id)}>
                  <div className="standard-card-info">
                    <span className="standard-code">{std.code}</span>
                    <span className="standard-title">{std.title}</span>
                    <div className="standard-meta">
                      <span className="standard-org">{ORG_LABEL[std.organization] || std.organization}</span>
                      <span className="standard-cat">{CAT_LABEL[std.category] || std.category}</span>
                      {std.version && <span className="standard-ver">v{std.version}</span>}
                      {std.is_builtin && <span className="standard-builtin-badge">内置</span>}
                    </div>
                  </div>
                  <div className="standard-actions">
                    {!std.is_builtin && (
                      <button className="filter-btn" onClick={(e) => { e.stopPropagation(); handleDelete(std.id) }}>删除</button>
                    )}
                    <svg className={`history-chevron ${expandedId === std.id ? 'chevron-open' : ''}`}
                      viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <polyline points="6 9 12 15 18 9" />
                    </svg>
                  </div>
                </div>
                {expandedId === std.id && (
                  <div className="standard-clauses">
                    {std.clauses.length === 0 ? (
                      <p className="chart-no-data">暂无条款数据</p>
                    ) : (
                      std.clauses.map((c, i) => (
                        <div key={i} className="clause-block">
                          <div className="clause-header">
                            <strong>{c.clause}</strong> {c.title}
                          </div>
                          {c.description && <p className="clause-desc">{c.description}</p>}
                          {c.limit_table.length > 0 && (
                            <table className="limit-table">
                              <thead>
                                <tr>
                                  {limitCols(c).map((col) => (
                                    <th key={col}>{col}</th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {c.limit_table.map((row, ri) => (
                                  <tr key={ri}>
                                    {limitCols(c).map((col) => (
                                      <td key={col}>{row[col] || '-'}</td>
                                    ))}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          )}
                        </div>
                      ))
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

      {showForm && (
        <div className="modal-overlay" onClick={() => setShowForm(false)}>
          <div className="modal-panel modal-wide" onClick={(e) => e.stopPropagation()}>
            <h3>添加测试标准</h3>

            <div className="form-row">
              <div className="form-group">
                <label>标准编号 *</label>
                <input className="filter-input" value={form.code}
                  onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))} placeholder="如：CISPR 25" />
              </div>
              <div className="form-group">
                <label>标准名称 *</label>
                <input className="filter-input" value={form.title}
                  onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))} placeholder="如：车辆、船和内燃机..." />
              </div>
            </div>

            <div className="form-row">
              <div className="form-group">
                <label>发布机构</label>
                <select className="filter-select" value={form.organization}
                  onChange={(e) => setForm((f) => ({ ...f, organization: e.target.value }))}>
                  {ORG_OPTIONS.filter((o) => o.value).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label>测试类别</label>
                <select className="filter-select" value={form.category}
                  onChange={(e) => setForm((f) => ({ ...f, category: e.target.value }))}>
                  {CAT_OPTIONS.filter((o) => o.value).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label>版本</label>
                <input className="filter-input" value={form.version}
                  onChange={(e) => setForm((f) => ({ ...f, version: e.target.value }))} placeholder="如：2021" />
              </div>
            </div>

            <div className="form-group">
              <label>条款列表</label>
              {form.clauses.map((c, ci) => (
                <div key={ci} className="clause-editor">
                  <div className="form-row">
                    <input className="filter-input" placeholder="条款编号 (如 6.2.1)" value={c.clause}
                      onChange={(e) => updateClause(ci, 'clause', e.target.value)} style={{ flex: 1 }} />
                    <input className="filter-input" placeholder="条款标题" value={c.title}
                      onChange={(e) => updateClause(ci, 'title', e.target.value)} style={{ flex: 2 }} />
                    <button className="filter-btn" onClick={() => removeClause(ci)}>删除条款</button>
                  </div>
                  <textarea className="filter-input rule-textarea" rows={2} placeholder="条款描述 (可选)" value={c.description}
                    onChange={(e) => updateClause(ci, 'description', e.target.value)} />
                  <div className="limit-editor">
                    <label className="limit-editor-label">限值表格</label>
                    <table className="limit-table limit-table-edit">
                      <thead>
                        <tr>
                          {limitCols(c).map((col) => <th key={col}>{col || '参数'}</th>)}
                          <th></th>
                        </tr>
                      </thead>
                      <tbody>
                        {c.limit_table.map((row, ri) => (
                          <tr key={ri}>
                            {limitCols(c).map((col) => (
                              <td key={col}>
                                <input className="filter-input limit-cell-input"
                                  value={row[col] || ''}
                                  onChange={(e) => updateLimitCell(ci, ri, col, e.target.value)}
                                  placeholder={col || '值'} />
                              </td>
                            ))}
                            <td><button className="filter-btn" onClick={() => removeLimitRow(ci, ri)}>&times;</button></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <button className="filter-btn" onClick={() => addLimitRow(ci)}>+ 添加行</button>
                  </div>
                </div>
              ))}
              <button className="filter-btn" onClick={addClause} style={{ marginTop: 8 }}>+ 添加条款</button>
            </div>

            <div className="modal-actions">
              <button className="filter-btn" onClick={handleSave} disabled={saving}>
                {saving ? '保存中...' : '保存标准'}
              </button>
              <button className="filter-btn" onClick={() => setShowForm(false)}>取消</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
