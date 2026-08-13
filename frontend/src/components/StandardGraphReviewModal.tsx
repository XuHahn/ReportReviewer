import { useEffect, useMemo, useState } from 'react'
import {
  getStandardGraph, previewStandardPdf, publishStandardGraph, reviewStandardRequirement,
} from '../api'
import type {
  EmcStandard, StandardGraphClause, StandardGraphRequirement, StandardRequirementParameter,
} from '../types'
import Icon from './Icons'

const TYPE_LABELS: Record<string, string> = {
  applicability: '适用范围', test_method: '测试方法', test_condition: '测试条件',
  parameter_limit: '参数与限值', acceptance: '判定规则', instrument: '仪器要求',
  reference: '引用关系', other: '其他要求',
}

const STATUS_LABELS = { pending: '待确认', confirmed: '已确认', rejected: '已排除' }

type GraphData = {
  standard: EmcStandard
  clauses: StandardGraphClause[]
  requirements: StandardGraphRequirement[]
}

type Props = { standard: EmcStandard; onClose: () => void; onChanged: () => void }

const blankParameter = (): StandardRequirementParameter => ({
  name: '', symbol: '', comparator: '', value: '', value_min: '', value_max: '', unit: '', raw_text: '',
})

export default function StandardGraphReviewModal({ standard, onClose, onChanged }: Props) {
  const [data, setData] = useState<GraphData | null>(null)
  const [selectedId, setSelectedId] = useState('')
  const [saving, setSaving] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [error, setError] = useState('')
  const [comment, setComment] = useState('')
  const [form, setForm] = useState<StandardGraphRequirement | null>(null)
  const [pdfUrl, setPdfUrl] = useState('')

  async function load(preferredId = '') {
    const result = await getStandardGraph(standard.id)
    setData(result)
    const next = result.requirements.find((item) => item.id === preferredId)
      || result.requirements.find((item) => item.review_status === 'pending')
      || result.requirements[0]
    setSelectedId(next?.id || '')
    setForm(next ? structuredClone(next) : null)
    setComment(next?.review_comment || '')
  }

  useEffect(() => {
    load().catch((e) => setError(e?.response?.data?.detail || '标准内容加载失败'))
    if (!standard.source_filename.toLowerCase().endsWith('.pdf')) return
    previewStandardPdf(standard.id).then((blob) => {
      setPdfUrl(URL.createObjectURL(blob))
    }).catch(() => undefined)
  }, [standard.id, standard.source_filename])

  useEffect(() => () => { if (pdfUrl) URL.revokeObjectURL(pdfUrl) }, [pdfUrl])

  const selected = useMemo(
    () => data?.requirements.find((item) => item.id === selectedId) || null,
    [data, selectedId],
  )
  const counts = useMemo(() => ({
    pending: data?.requirements.filter((item) => item.review_status === 'pending').length || 0,
    confirmed: data?.requirements.filter((item) => item.review_status === 'confirmed').length || 0,
    rejected: data?.requirements.filter((item) => item.review_status === 'rejected').length || 0,
  }), [data])
  const groupedClauses = useMemo(() => {
    const groups = new Map<string, StandardGraphClause>()
    for (const clause of data?.clauses || []) {
      const key = `${clause.clause_number.trim()}\u0000${clause.title.trim()}`
      const existing = groups.get(key)
      if (existing) {
        existing.requirements.push(...clause.requirements)
        existing.page_start = Math.min(existing.page_start, clause.page_start)
        existing.page_end = Math.max(existing.page_end, clause.page_end)
      } else {
        groups.set(key, { ...clause, requirements: [...clause.requirements] })
      }
    }
    return Array.from(groups.values())
  }, [data])

  function selectRequirement(item: StandardGraphRequirement) {
    setSelectedId(item.id)
    setForm(structuredClone(item))
    setComment(item.review_comment || '')
    setError('')
  }

  function updateParameter(index: number, key: keyof StandardRequirementParameter, value: string) {
    if (!form) return
    const parameters = form.parameters.map((item, i) => i === index ? { ...item, [key]: value } : item)
    setForm({ ...form, parameters })
  }

  async function save(status: 'confirmed' | 'rejected') {
    if (!form || !selected) return
    if (status === 'rejected' && !comment.trim()) {
      setError('排除这条内容时，请填写原因，方便后续追溯。')
      return
    }
    setSaving(true); setError('')
    try {
      await reviewStandardRequirement(standard.id, selected.id, {
        review_status: status, comment,
        clause_number: form.clause_number, clause_title: form.clause_title,
        requirement_type: form.requirement_type, test_item: form.test_item,
        interpretation_zh: form.interpretation_zh,
        applicability: form.applicability,
        parameters: form.parameters,
      })
      await load()
      onChanged()
    } catch (e: any) {
      setError(e?.response?.data?.detail || '保存失败')
    } finally { setSaving(false) }
  }

  async function publish() {
    if (counts.pending > 0) { setError(`还有 ${counts.pending} 条内容没有确认。`); return }
    setPublishing(true); setError('')
    try {
      await publishStandardGraph(standard.id)
      await load(selectedId)
      onChanged()
    } catch (e: any) {
      setError(e?.response?.data?.detail || '发布失败')
    } finally { setPublishing(false) }
  }

  const progress = data?.requirements.length
    ? Math.round(((counts.confirmed + counts.rejected) / data.requirements.length) * 100) : 0

  return (
    <div className="modal-overlay standard-review-overlay" onClick={onClose}>
      <div className="standard-review-workspace" onClick={(e) => e.stopPropagation()}>
        <header className="standard-review-header">
          <div>
            <h3>审核标准内容</h3>
            <p>{standard.code} {standard.version} · 原文不可改，中文释义经人工确认后发布</p>
          </div>
          <div className="standard-review-summary">
            <span>已发布 R{standard.latest_release_number || 0}</span><span>{progress}% 已处理</span><span className="review-pending">{counts.pending} 待确认</span>
            <span className="review-confirmed">{counts.confirmed} 已确认</span><span>{counts.rejected} 已排除</span>
          </div>
          <button className="standard-icon-btn" aria-label="关闭" onClick={onClose}><Icon name="close" size={16} /></button>
        </header>
        <div className="standard-review-progress"><span style={{ width: `${progress}%` }} /></div>
        {error && <div className="standard-review-error">{error}<button onClick={() => setError('')}>关闭</button></div>}

        <main className="standard-review-main">
          <aside className="standard-review-nav">
            <div className="standard-review-pane-title">章节与要求</div>
            {!data ? <div className="stats-loading"><div className="spinner" /> 加载中...</div> : groupedClauses.map((clause) => (
              <section key={clause.id} className="standard-review-clause">
                <div className="standard-review-clause-title">
                  <strong>{clause.clause_number || '未编号章节'}</strong>{clause.title}
                </div>
                {clause.requirements.map((item) => (
                  <button key={item.id} className={`standard-review-nav-item ${selectedId === item.id ? 'active' : ''}`} onClick={() => selectRequirement(item)}>
                    <span className={`review-status-dot ${item.review_status}`} />
                    <span><strong>{TYPE_LABELS[item.requirement_type] || '其他要求'}</strong><small>{item.test_item || item.statement}</small></span>
                    <em>{STATUS_LABELS[item.review_status]}</em>
                  </button>
                ))}
              </section>
            ))}
          </aside>

          <section className="standard-review-form">
            <div className="standard-review-pane-title">提取内容</div>
            {!form ? <div className="standard-empty-note">没有可审核的结构化要求。</div> : <>
              <div className="standard-review-location"><span>PDF 第 {form.page_start}{form.page_end !== form.page_start ? `-${form.page_end}` : ''} 页</span></div>
              {form.confidence < .3 && <div className="standard-review-low-confidence">系统检测到规范性措辞，但 AI 未能可靠分类。请结合右侧原文修改后确认，或填写原因排除。</div>}
              <label>条款编号<input value={form.clause_number} onChange={(e) => setForm({ ...form, clause_number: e.target.value })} placeholder="例如 4.4.2" /></label>
              <label>条款标题<input value={form.clause_title} onChange={(e) => setForm({ ...form, clause_title: e.target.value })} placeholder="例如 Superimposed alternating voltage" /></label>
              <label>要求类别<select value={form.requirement_type} onChange={(e) => setForm({ ...form, requirement_type: e.target.value })}>
                {Object.entries(TYPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select></label>
              <label>测试项目<input value={form.test_item} onChange={(e) => setForm({ ...form, test_item: e.target.value })} placeholder="原文没有明确测试项目时可以留空" /></label>
              <label>标准原文要求
                <textarea rows={4} value={form.original_statement || form.evidence_quote} readOnly aria-readonly="true" />
                <small>来自 PDF 的逐字证据，发布后不可修改</small>
              </label>
              <label>中文释义<textarea rows={4} value={form.interpretation_zh} onChange={(e) => setForm({ ...form, interpretation_zh: e.target.value, statement: e.target.value })} placeholder="请用中文准确说明原文要求，不增加原文没有的条件" /></label>
              <label>适用条件<textarea rows={2} value={form.applicability} onChange={(e) => setForm({ ...form, applicability: e.target.value })} placeholder="没有前置条件时可以留空" /></label>
              <div className="standard-parameter-head"><strong>参数与限值</strong><button onClick={() => setForm({ ...form, parameters: [...form.parameters, blankParameter()] })}>添加参数</button></div>
              {form.parameters.length === 0 ? <div className="standard-parameter-empty">这条要求没有提取到参数</div> : form.parameters.map((parameter, index) => (
                <div className="standard-parameter-row" key={parameter.id || index}>
                  <input value={parameter.name} onChange={(e) => updateParameter(index, 'name', e.target.value)} placeholder="参数名称" />
                  <input value={parameter.symbol} onChange={(e) => updateParameter(index, 'symbol', e.target.value)} placeholder="符号" />
                  <input value={parameter.comparator} onChange={(e) => updateParameter(index, 'comparator', e.target.value)} placeholder="关系" />
                  <input value={parameter.value} onChange={(e) => updateParameter(index, 'value', e.target.value)} placeholder="数值" />
                  <input value={parameter.unit} onChange={(e) => updateParameter(index, 'unit', e.target.value)} placeholder="单位" />
                  <button aria-label="删除参数" onClick={() => setForm({ ...form, parameters: form.parameters.filter((_, i) => i !== index) })}><Icon name="close" size={14} /></button>
                </div>
              ))}
              <label>审核备注<textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="修改说明或排除原因" /></label>
              <div className="standard-review-actions">
                <button className="filter-btn" disabled={saving} onClick={() => save('rejected')}>不是有效要求</button>
                <button className="filter-btn standard-primary-btn" disabled={saving || !form.interpretation_zh.trim()} onClick={() => save('confirmed')}>{saving ? '保存中...' : '确认中文释义'}</button>
              </div>
            </>}
          </section>

          <aside className="standard-review-evidence">
            <div className="standard-review-pane-title">PDF 原文证据</div>
            {selected && <div className="standard-evidence-quote"><span>第 {selected.page_start}{selected.page_end !== selected.page_start ? `-${selected.page_end}` : ''} 页</span><p>{selected.evidence_quote}</p></div>}
            {pdfUrl && selected ? <iframe title="标准 PDF 原文" src={`${pdfUrl}#page=${selected.page_start}&view=FitH`} /> : <div className="standard-empty-note">原文件预览不可用，请使用标准库中的“原文件”按钮查看。</div>}
          </aside>
        </main>

        <footer className="standard-review-footer">
          <span>发布会生成不可变的 R{standard.latest_release_number + 1}；历史审核继续使用原来绑定的版本。</span>
          <div><button className="filter-btn" onClick={onClose}>稍后继续</button><button className="filter-btn standard-primary-btn" disabled={publishing || counts.pending > 0 || counts.confirmed === 0} onClick={publish}>{publishing ? '正在建立本地知识索引...' : `发布为 R${standard.latest_release_number + 1}`}</button></div>
        </footer>
      </div>
    </div>
  )
}
