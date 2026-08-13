import { useEffect, useMemo, useState } from 'react'
import { getProjectGroups, getStandardRelease, saveStandardRequirementMapping } from '../api'
import type { EmcStandard, PipelineIssue, ProjectGroup, StandardGraphRequirement } from '../types'
import Icon from './Icons'

type Props = {
  issue: PipelineIssue
  standards: EmcStandard[]
  setId: string
  onClose: () => void
  onSaved: () => void
}

export default function StandardMappingModal({ issue, standards, setId, onClose, onSaved }: Props) {
  const [standardId, setStandardId] = useState(standards[0]?.id || '')
  const [requirements, setRequirements] = useState<StandardGraphRequirement[]>([])
  const [mappingType, setMappingType] = useState<'covered' | 'not_covered'>('covered')
  const [requirementId, setRequirementId] = useState('')
  const [scopeType, setScopeType] = useState<'standard' | 'project' | 'set'>('set')
  const [projectId, setProjectId] = useState('')
  const [groups, setGroups] = useState<ProjectGroup[]>([])
  const [rationale, setRationale] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const standard = useMemo(() => standards.find(item => item.id === standardId), [standards, standardId])

  useEffect(() => {
    getProjectGroups().then(setGroups).catch(() => setGroups([]))
  }, [])

  useEffect(() => {
    if (!standard?.selected_release_id) { setRequirements([]); return }
    setLoading(true); setError('')
    getStandardRelease(standard.id, standard.selected_release_id)
      .then(release => {
        setRequirements(release.snapshot.requirements || [])
        setRequirementId(release.snapshot.requirements?.[0]?.id || '')
      })
      .catch((e: any) => setError(e?.response?.data?.detail || '知识版本加载失败'))
      .finally(() => setLoading(false))
  }, [standard?.id, standard?.selected_release_id])

  async function save() {
    if (!standard?.selected_release_id) { setError('这份审核没有绑定可用的标准知识版本'); return }
    if (mappingType === 'covered' && !requirementId) { setError('请选择对应的标准要求'); return }
    if (scopeType === 'project' && !projectId) { setError('请选择项目组'); return }
    if (mappingType === 'not_covered' && !rationale.trim()) { setError('确认不适用时必须填写依据'); return }
    setSaving(true); setError('')
    try {
      await saveStandardRequirementMapping(standard.id, {
        release_id: standard.selected_release_id,
        source_name: issue.field_name,
        mapping_type: mappingType,
        requirement_id: mappingType === 'covered' ? requirementId : '',
        scope_type: scopeType,
        scope_value: scopeType === 'set' ? setId : scopeType === 'project' ? projectId : '',
        rationale,
      })
      onSaved()
      onClose()
    } catch (e: any) {
      setError(e?.response?.data?.detail || '映射保存失败')
    } finally { setSaving(false) }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-panel standard-upload-modal" onClick={event => event.stopPropagation()}>
        <div className="standard-upload-head">
          <div><h3>人工定位标准要求</h3><p>{issue.field_name} · 处理检索置信度不足</p></div>
          <button className="standard-icon-btn" onClick={onClose} aria-label="关闭"><Icon name="close" size={16} /></button>
        </div>
        {error && <div className="upload-error">{error}</div>}
        <div className="standard-form-grid">
          <label className="standard-form-wide">审核使用的标准
            <select className="filter-input" value={standardId} onChange={event => setStandardId(event.target.value)}>
              {standards.map(item => <option key={item.id} value={item.id}>{item.code} {item.version} · R{item.selected_release_number}</option>)}
            </select>
          </label>
          <label>人工结论
            <select className="filter-input" value={mappingType} onChange={event => setMappingType(event.target.value as 'covered' | 'not_covered')}>
              <option value="covered">对应某条标准要求</option>
              <option value="not_covered">该标准不覆盖此项目</option>
            </select>
          </label>
          <label>生效范围
            <select className="filter-input" value={scopeType} onChange={event => setScopeType(event.target.value as 'standard' | 'project' | 'set')}>
              <option value="set">仅当前文档集</option>
              <option value="project">指定项目组</option>
              <option value="standard">使用该标准的所有审核</option>
            </select>
          </label>
          {scopeType === 'project' && <label className="standard-form-wide">项目组
            <select className="filter-input" value={projectId} onChange={event => setProjectId(event.target.value)}>
              <option value="">请选择项目组</option>{groups.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}
            </select>
          </label>}
          {mappingType === 'covered' && <label className="standard-form-wide">对应的标准要求
            <select className="filter-input" value={requirementId} onChange={event => setRequirementId(event.target.value)} disabled={loading}>
              {requirements.map(item => <option key={item.id} value={item.id}>{item.clause_number || '未编号'} · {item.interpretation_zh || '中文释义待确认'}</option>)}
            </select>
          </label>}
          <label className="standard-form-wide">人工判断依据
            <textarea className="filter-input" rows={3} value={rationale} onChange={event => setRationale(event.target.value)} placeholder="说明名称对应关系或不适用原因，便于后续追溯" />
          </label>
        </div>
        <div className="modal-actions">
          <button className="filter-btn" onClick={onClose}>取消</button>
          <button className="filter-btn standard-primary-btn" onClick={save} disabled={saving || loading}>{saving ? '保存中...' : '保存映射并在下次审核生效'}</button>
        </div>
      </div>
    </div>
  )
}
