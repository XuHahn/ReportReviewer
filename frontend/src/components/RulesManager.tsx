import { useEffect, useState, useCallback } from 'react'
import { getRules, createRule, updateRule, deleteRule, getStandards } from '../api'
import type { ReviewRule, EmcStandard } from '../types'
import { SEV_LABEL } from '../constants'

const CAT_OPTIONS = [
  { value: '', label: '全部分类' },
  { value: 'limit', label: '限值合规性' },
  { value: 'consistency', label: '数据一致性' },
  { value: 'logic', label: '结论逻辑性' },
  { value: 'format', label: '格式规范性' },
  { value: 'other', label: '其他' },
]

const CAT_LABEL: Record<string, string> = Object.fromEntries(CAT_OPTIONS.map((o) => [o.value, o.label]))

const INIT_FORM = {
  name: '', description: '', category: 'other' as ReviewRule['category'],
  severity: 'warning' as ReviewRule['severity'],
  keywords: [] as string[], standard_id: '', suggestion_template: '', enabled: true,
}

export default function RulesManager() {
  const [rules, setRules] = useState<ReviewRule[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [keyword, setKeyword] = useState('')
  const [catFilter, setCatFilter] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [editId, setEditId] = useState<string | null>(null)
  const [form, setForm] = useState({ ...INIT_FORM })
  const [tagInput, setTagInput] = useState('')
  const [standards, setStandards] = useState<EmcStandard[]>([])
  const [saving, setSaving] = useState(false)

  const fetchRules = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getRules({ keyword: keyword || undefined, category: catFilter || undefined })
      setRules(data)
    } catch (e: any) {
      setError(e?.message || '加载规则失败')
    } finally {
      setLoading(false)
    }
  }, [keyword, catFilter])

  useEffect(() => { fetchRules() }, [fetchRules])
  useEffect(() => { getStandards().then(setStandards).catch(() => {}) }, [])

  const handleAdd = () => {
    setEditId(null)
    setForm({ ...INIT_FORM })
    setShowForm(true)
  }

  const handleEdit = (rule: ReviewRule) => {
    setEditId(rule.id)
    setForm({
      name: rule.name, description: rule.description, category: rule.category,
      severity: rule.severity, keywords: [...rule.keywords],
      standard_id: rule.standard_id, suggestion_template: rule.suggestion_template,
      enabled: rule.enabled,
    })
    setShowForm(true)
  }

  const handleDelete = async (id: string) => {
    if (!confirm('确定删除此规则？')) return
    try {
      await deleteRule(id)
      setRules((prev) => prev.filter((r) => r.id !== id))
    } catch (e: any) {
      alert(e?.message || '删除失败')
    }
  }

  const handleToggle = async (rule: ReviewRule) => {
    try {
      await updateRule(rule.id, { enabled: !rule.enabled })
      setRules((prev) => prev.map((r) => r.id === rule.id ? { ...r, enabled: !r.enabled } : r))
    } catch (e: any) {
      alert(e?.message || '更新失败')
    }
  }

  const addTag = (input?: string) => {
    const raw = (input ?? tagInput).trim()
    const tags = raw.split(/[,，]/).map(s => s.trim()).filter(Boolean)
    if (tags.length === 0) { setTagInput(''); return }
    setForm((f) => {
      const added = tags.filter(t => !f.keywords.includes(t))
      return added.length ? { ...f, keywords: [...f.keywords, ...added] } : f
    })
    setTagInput('')
  }

  const removeTag = (t: string) => {
    setForm((f) => ({ ...f, keywords: f.keywords.filter((k) => k !== t) }))
  }

  const handleSave = async () => {
    if (!form.name.trim()) return alert('请输入规则名称')
    setSaving(true)
    try {
      if (editId) {
        await updateRule(editId, form as ReviewRule)
      } else {
        await createRule(form as ReviewRule)
      }
      setShowForm(false)
      fetchRules()
    } catch (e: any) {
      alert(e?.response?.data?.detail || e?.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const linkedStandard = form.standard_id
    ? standards.find((s) => s.id === form.standard_id)
    : null

  return (
    <div className="rules-manager">
      <div className="rules-header">
        <h2 className="section-title">审核规则管理</h2>
        <button className="filter-btn" onClick={handleAdd}>+ 新建规则</button>
      </div>

      <div className="rules-toolbar">
        <input className="filter-input" type="text" placeholder="搜索规则..." value={keyword}
          onChange={(e) => setKeyword(e.target.value)} />
        <select className="filter-select" value={catFilter} onChange={(e) => setCatFilter(e.target.value)}>
          {CAT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <span className="rules-count">{rules.length} 条规则</span>
      </div>

      {error && <div className="upload-error">{error}</div>}
      {loading ? <div className="stats-loading"><div className="spinner"/> 加载中...</div>
        : rules.length === 0 ? <div className="stats-empty"><p>暂无审核规则</p><p className="stats-empty-hint">点击"新建规则"添加第一条自定义审核规则</p></div>
        : (
          <div className="rules-list">
            {rules.map((rule) => (
              <div key={rule.id} className={`rule-card ${rule.enabled ? '' : 'rule-disabled'}`}>
                <div className={`rule-card-bar rule-bar-${rule.severity}`} />
                <div className="rule-card-body">
                  <div className="rule-card-top">
                    <span className="rule-name">{rule.name}</span>
                    <span className={`sev-badge sev-badge-${rule.severity}`}>{SEV_LABEL[rule.severity]}</span>
                    <span className="rule-category-tag">{CAT_LABEL[rule.category] || rule.category}</span>
                    {rule.standard_id && (
                      <span className="rule-standard-tag">
                        {standards.find((s) => s.id === rule.standard_id)?.code || '未知标准'}
                      </span>
                    )}
                  </div>
                  <p className="rule-desc">{rule.description}</p>
                  <div className="rule-keywords">
                    {rule.keywords.map((k) => <span key={k} className="rule-kw-tag">{k}</span>)}
                  </div>
                </div>
                <div className="rule-card-actions">
                  <button className="filter-btn" onClick={() => handleEdit(rule)}>编辑</button>
                  <button className="filter-btn" onClick={() => handleDelete(rule.id)}>删除</button>
                  <label className="rule-toggle">
                    <input type="checkbox" checked={rule.enabled} onChange={() => handleToggle(rule)} />
                    <span>{rule.enabled ? '已启用' : '已禁用'}</span>
                  </label>
                </div>
              </div>
            ))}
          </div>
        )}

      {showForm && (
        <div className="modal-overlay" onClick={() => setShowForm(false)}>
          <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
            <h3>{editId ? '编辑规则' : '新建规则'}</h3>
            <div className="form-group">
              <label>规则名称 *</label>
              <input className="filter-input" value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} placeholder="如：辐射骚扰限值检查" />
            </div>

            <div className="form-row">
              <div className="form-group">
                <label>检查类别</label>
                <select className="filter-select" value={form.category}
                  onChange={(e) => setForm((f) => ({ ...f, category: e.target.value as ReviewRule['category'] }))}>
                  {CAT_OPTIONS.filter((o) => o.value).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label>严重程度</label>
                <div className="sev-selector">
                  {(['error', 'warning', 'info'] as const).map((s) => (
                    <button key={s}
                      className={`sev-btn sev-btn-${s} ${form.severity === s ? 'sev-btn-active' : ''}`}
                      onClick={() => setForm((f) => ({ ...f, severity: s }))}>
                      {s === 'error' ? '错误' : s === 'warning' ? '警告' : '提示'}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className="form-group">
              <label>检查说明（告诉AI查什么）</label>
              <textarea className="filter-input rule-textarea" rows={3} value={form.description}
                onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                placeholder="用自然语言描述需要检查的内容..." />
            </div>

            <div className="form-group">
              <label>关键词标签（AI审核时同步匹配原文，输入后回车添加）</label>
              <div className="tag-input-wrap">
                {form.keywords.map((k) => (
                  <span key={k} className="tag-chip">
                    {k}
                    <button className="tag-chip-remove" onClick={() => removeTag(k)}>&times;</button>
                  </span>
                ))}
                <input className="tag-input" value={tagInput}
                  onChange={(e) => {
                  const v = e.target.value
                  if (v.includes(',') || v.includes('，')) { addTag(v); return }
                  setTagInput(v)
                }}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addTag() } }}
                  placeholder={form.keywords.length ? '继续添加...' : '输入关键词，按回车添加'} />
              </div>
            </div>

            <div className="form-group">
              <label>关联标准（可选）</label>
              <select className="filter-select" value={form.standard_id}
                onChange={(e) => setForm((f) => ({ ...f, standard_id: e.target.value }))}>
                <option value="">不关联</option>
                {standards.map((s) => (
                  <option key={s.id} value={s.id}>{s.code} — {s.title.slice(0, 40)}</option>
                ))}
              </select>
              {linkedStandard && <p className="form-hint">已关联: {linkedStandard.code} — {linkedStandard.title}</p>}
            </div>

            <div className="form-group">
              <label>建议模板</label>
              <textarea className="filter-input rule-textarea" rows={2} value={form.suggestion_template}
                onChange={(e) => setForm((f) => ({ ...f, suggestion_template: e.target.value }))}
                placeholder="预设的修改建议..." />
            </div>

            <div className="form-group">
              <label className="rule-toggle">
                <input type="checkbox" checked={form.enabled}
                  onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))} />
                <span>启用此规则</span>
              </label>
            </div>

            <div className="modal-actions">
              <button className="filter-btn" onClick={handleSave} disabled={saving}>
                {saving ? '保存中...' : '保存规则'}
              </button>
              <button className="filter-btn" onClick={() => setShowForm(false)}>取消</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
