import { useEffect, useMemo, useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Modal, Select, Textarea } from '@mantine/core'
import { BookOpenCheck, Check, FileSpreadsheet, FileText, FolderArchive, RefreshCw, Search, ShieldCheck, Trash2, Upload, UsersRound, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { notifications } from '@mantine/notifications'
import { addDocumentToSet, apiErrorMessage, deleteDocument, getDocumentSetStandardReferences, getProjectGroups, getStandards, lockDocumentSet, updateDocumentSetProjectGroup, updateDocumentSetStandards } from '../../api'
import { DOC_LABELS, type DocType, type DocumentSetOverview, type EmcStandard, type SetDocument, type StandardReference } from '../../types'
import { EvidenceRail, LoadingState, StatusPill, formatBytes } from '../../components/common'
import { extractionGateFor } from '../../reviewLogic'

const slots: Array<{ type: DocType; note: string; accept: string; icon: typeof FileText }> = [
  { type: 'order_form', note: '客户、产品、样品与委托范围', accept: '.xls / .xlsx', icon: FileSpreadsheet },
  { type: 'test_plan', note: '测试项目、条件与标准引用', accept: 'PDF / DOCX / XLS / XLSX', icon: FileText },
  { type: 'original_records', note: 'ZIP 内逐份原始记录与测试身份', accept: '.zip', icon: FolderArchive },
  { type: 'final_report', note: '最终结果、结论与签发信息', accept: 'PDF / DOCX', icon: FileText },
]

function escapeRegExp(value: string) { return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') }

function standardReferencePattern(referenceCode: string) {
  const match = referenceCode.match(/^([A-Z]+(?:\/[A-Z]+)?)\s*[-/]?\s*(\d+)(?:\s*[-:]\s*(\d+))?/i)
  if (!match) return null
  const organization = escapeRegExp(match[1])
  const series = escapeRegExp(match[2])
  const part = match[3] ? `\\s*[-:]\\s*${escapeRegExp(match[3])}` : ''
  return new RegExp(`(${organization}\\s*[-/]?\\s*${series}${part}(?:\\s*[-:]\\s*\\d+(?:\\.\\d+)+)?(?:\\s*[-:]\\s*\\d{4})?)`, 'gi')
}

function HighlightedReferenceEvidence({ text, referenceCode }: { text: string; referenceCode: string }) {
  const pattern = standardReferencePattern(referenceCode)
  if (!pattern) return <>{text}</>
  const pieces: React.ReactNode[] = []
  let cursor = 0
  for (const match of text.matchAll(pattern)) {
    const value = match[0]
    const start = match.index ?? -1
    if (start < 0) continue
    if (start > cursor) pieces.push(text.slice(cursor, start))
    pieces.push(<mark key={`${start}-${value}`} title="已定位标准编号或条款">{value}</mark>)
    cursor = start + value.length
  }
  if (!pieces.length) return <>{text}</>
  if (cursor < text.length) pieces.push(text.slice(cursor))
  return <>{pieces}</>
}

function standardIdentity(value: string) {
  const match = String(value || '').match(/^([A-Z]+(?:\/[A-Z]+)?)\s*[-/]?\s*(\d+)(?:\s*[-:]\s*(\d+))?/i)
  return match ? `${match[1].toUpperCase()}-${match[2]}-${match[3] || ''}` : String(value || '').toUpperCase().replace(/[^A-Z0-9]/g, '')
}

function standardMatchesReference(referenceCode: string, standard: EmcStandard) {
  const referenceIdentity = standardIdentity(referenceCode)
  return [standard.code, standard.version ? `${standard.code}-${standard.version}` : ''].filter(Boolean).some(value => standardIdentity(value) === referenceIdentity)
}

function linkedStandardsForReference(reference: StandardReference, standards: EmcStandard[], selectedIds: string[]) {
  return standards.filter(standard => selectedIds.includes(standard.id) && standardMatchesReference(reference.reference_code, standard))
}

function StandardReferenceRow({ reference, standards, selectedIds, skipReason, onSkipReasonChange }: { reference: StandardReference; standards: EmcStandard[]; selectedIds: string[]; skipReason: string; onSkipReasonChange: (value: string) => void }) {
  const linkedStandards = linkedStandardsForReference(reference, standards, selectedIds)
  const selected = linkedStandards.length > 0
  const resolution = selected ? 'selected' : reference.resolution === 'skipped' ? 'skipped' : 'unresolved'
  return <div className={`standard-reference-row ${selected ? 'resolved' : ''}`}>
    <div className="standard-reference-main">
      <div><b>{reference.reference_code}</b><small>来源：{reference.sources.map(source => DOC_LABELS[source]).join(' · ')}</small></div>
      <StatusPill status={resolution}>{resolution === 'selected' ? '已选用' : resolution === 'skipped' ? '已跳过' : '待处理'}</StatusPill>
      {reference.evidence[0] && <blockquote className="standard-reference-quote"><HighlightedReferenceEvidence text={reference.evidence[0]} referenceCode={reference.reference_code} /></blockquote>}
    </div>
    {selected ? <div className="standard-reference-linked"><span>已关联标准</span><div>{linkedStandards.map(standard => <em key={standard.id}>{standard.code}{standard.version ? ` · ${standard.version}` : ''}</em>)}</div></div> : <Textarea label="跳过原因" placeholder="选择右侧对应标准后可留空" value={skipReason} onChange={event => onSkipReasonChange(event.currentTarget.value)} />}
  </div>
}

export default function IntakeStage({ setId, overview, loading }: { setId: string; overview?: DocumentSetOverview; loading: boolean }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [standardOpen, setStandardOpen] = useState(false)
  const [selectedStandards, setSelectedStandards] = useState<string[]>(overview?.standards?.map(item => item.id) || [])
  const [autoSelectionApplied, setAutoSelectionApplied] = useState(false)
  const [skipReasons, setSkipReasons] = useState<Record<string, string>>({})
  const [standardQuery, setStandardQuery] = useState('')
  const [removeTarget, setRemoveTarget] = useState<SetDocument | null>(null)
  const groupsQuery = useQuery({ queryKey: ['groups'], queryFn: getProjectGroups })
  const standardsQuery = useQuery({ queryKey: ['standards'], queryFn: () => getStandards() })
  const referencesQuery = useQuery({
    queryKey: ['standard-references', setId],
    queryFn: () => getDocumentSetStandardReferences(setId),
    enabled: standardOpen,
  })
  const docs = overview?.documents || []
  const requiredDocs = slots.map(slot => docs.find(doc => doc.doc_type === slot.type)).filter(Boolean)
  const extractionComplete = requiredDocs.length === slots.length && requiredDocs.every(doc => doc?.extraction_status === 'completed')
  const extractionGate = extractionGateFor(overview)
  const autoGatePass = extractionGate.status === 'pass'
  const alreadyLocked = ['locked', 'reviewing', 'reviewed'].includes(overview?.status || '')
  const complete = slots.every(slot => docs.some(doc => doc.doc_type === slot.type && doc.extraction_status === 'completed'))
  const filteredStandards = useMemo(() => {
    const keyword = standardQuery.trim().toLowerCase()
    if (!keyword) return standardsQuery.data || []
    return (standardsQuery.data || []).filter(standard => `${standard.code} ${standard.title} ${standard.organization || ''} ${standard.category || ''}`.toLowerCase().includes(keyword))
  }, [standardQuery, standardsQuery.data])
  const extraSelectedStandards = useMemo(() => {
    const references = referencesQuery.data?.references || []
    const linkedIds = new Set(references.flatMap(reference => linkedStandardsForReference(reference, standardsQuery.data || [], selectedStandards).map(standard => standard.id)))
    return (standardsQuery.data || []).filter(standard => selectedStandards.includes(standard.id) && !linkedIds.has(standard.id))
  }, [referencesQuery.data?.references, selectedStandards, standardsQuery.data])

  useEffect(() => {
    if (!standardOpen) { setAutoSelectionApplied(false); return }
    setSelectedStandards(overview?.standards?.map(item => item.id) || [])
    setStandardQuery('')
    setAutoSelectionApplied(false)
  }, [standardOpen, overview?.standards])
  useEffect(() => {
    if (!standardOpen || autoSelectionApplied || !standardsQuery.data) return
    const references = referencesQuery.data?.references
    if (!references) return
    const autoSelectedIds = references.flatMap(reference => standardsQuery.data
      .filter(standard => standard.graph_status === 'published' && standardMatchesReference(reference.reference_code, standard))
      .map(standard => standard.id))
    setSelectedStandards(current => [...new Set([...current, ...autoSelectedIds])])
    setAutoSelectionApplied(true)
  }, [autoSelectionApplied, referencesQuery.data, standardOpen, standardsQuery.data])
  useEffect(() => {
    if (!referencesQuery.data) return
    setSkipReasons(Object.fromEntries(referencesQuery.data.skips.map(skip => [skip.normalized_code, skip.reason])))
  }, [referencesQuery.data])

  async function upload(file: File, type: DocType, replaceId?: string) {
    try {
      const uploaded = await addDocumentToSet(setId, file, type, replaceId)
        // The upload endpoint intentionally returns before extraction finishes.
        // Put its pending snapshot into the active cache immediately so the
        // overview query starts polling without requiring a manual refresh.
        queryClient.setQueryData<DocumentSetOverview>(['set', setId], current => {
          if (!current) return current
          const document = {
            ...uploaded,
            status: uploaded.status || uploaded.extraction_status || 'pending',
            extraction_status: uploaded.extraction_status || 'pending',
            extraction_quality: uploaded.extraction_quality || 'pending',
            file_size: uploaded.file_size ?? (uploaded.file_size_kb ? Number(uploaded.file_size_kb) * 1024 : undefined),
          }
          return {
            ...current,
            documents: [
              ...current.documents.filter(item => item.doc_type !== type && item.doc_id !== replaceId),
              document,
            ],
          }
        })
        await queryClient.invalidateQueries({ queryKey: ['set', setId] })
        // Keep the task queue fresh too, so switching away from the workspace
        // does not leave an old document count/status in the overview list.
        await queryClient.invalidateQueries({ queryKey: ['sets'] })
      notifications.show({ color: 'green', title: '文件已接收', message: `${DOC_LABELS[type]}已进入后台提取，可切换到其他页面。` })
    }
    catch (error) { notifications.show({ color: 'red', title: '上传失败', message: apiErrorMessage(error) }) }
  }
  async function remove() {
    if (!removeTarget) return
    try { await deleteDocument(setId, removeTarget.doc_id); setRemoveTarget(null); queryClient.invalidateQueries({ queryKey: ['set', setId] }); queryClient.invalidateQueries({ queryKey: ['sets'] }); queryClient.invalidateQueries({ queryKey: ['stats'] }); notifications.show({ color: 'green', message: '文件已移除' }) }
    catch (error) { notifications.show({ color: 'red', message: apiErrorMessage(error) }) }
  }
  async function saveStandards() {
    const skips = (referencesQuery.data?.references || []).flatMap(reference => {
      if (linkedStandardsForReference(reference, standardsQuery.data || [], selectedStandards).length) return []
      const reason = (skipReasons[reference.normalized_code] || '').trim()
      return reason ? [{ reference_code: reference.reference_code, normalized_code: reference.normalized_code, reason }] : []
    })
    try { await updateDocumentSetStandards(setId, selectedStandards, skips); setStandardOpen(false); queryClient.invalidateQueries({ queryKey: ['set', setId] }); queryClient.invalidateQueries({ queryKey: ['standard-references', setId] }); notifications.show({ color: 'green', message: selectedStandards.length ? '审核标准与引用处理已更新' : skips.length ? '引用标准已按填写原因跳过' : '本次将跳过标准条款审核' }) }
    catch (error) { notifications.show({ color: 'red', message: apiErrorMessage(error) }) }
  }
  async function lockAndContinue() {
    if (alreadyLocked) {
      navigate(`/tasks/${setId}/${overview?.status === 'reviewed' ? 'complete' : 'run'}`)
      return
    }
    try { await lockDocumentSet(setId, autoGatePass ? { auto_gate: true } : { exception_only: true }); queryClient.invalidateQueries({ queryKey: ['set', setId] }); queryClient.invalidateQueries({ queryKey: ['sets'] }); queryClient.invalidateQueries({ queryKey: ['stats'] }); navigate(`/tasks/${setId}/run`) }
    catch (error) { notifications.show({ color: 'red', title: '暂时不能锁定', message: apiErrorMessage(error, '请确认四份资料均已完成后台提取。') }) }
  }

  if (loading) return <LoadingState label="正在读取任务资料…" />
  const backgroundDocs = docs.filter(doc => ['pending', 'extracting', 'queued', 'processing'].includes(doc.extraction_status || ''))
  return <div className="stage-page intake-stage">
    <div className="stage-intro"><div className="eyebrow">INTAKE & SCOPE</div><h1>先把本轮审核的证据边界说清楚</h1><p>四份业务资料必须齐全；标准是可选知识来源。文件替换会进入修订流程，不覆盖旧版本。</p></div>
    {backgroundDocs.length > 0 && <div className="background-extraction-notice" role="status"><RefreshCw size={15} /><span><b>{backgroundDocs.length} 份资料正在后台提取</b><small>你可以切换到其他页面；回来后状态会自动更新。</small></span></div>}
    <EvidenceRail states={Object.fromEntries(slots.map(slot => [slot.type, docs.some(doc => doc.doc_type === slot.type) ? 'ok' : 'missing']))} />
    <div className="intake-grid">
      <section className="upload-board"><div className="section-heading"><div><h2>四份业务资料</h2><p>拖到对应位置，避免自动分类造成误放。</p></div><span>{docs.length} / 4 已上传</span></div>
        <div className="document-slots">{slots.map(slot => <DocumentSlot key={slot.type} slot={slot} document={docs.find(doc => doc.doc_type === slot.type)} onUpload={upload} onRemove={setRemoveTarget} />)}</div>
      </section>
      <aside className="scope-panel"><div className="scope-title"><ShieldCheck/><div><h2>提交前检查</h2><p>资料完整性决定能否锁定，提取提示会进入审核结果。</p></div></div>
        <div className="gate-list"><Gate ok={requiredDocs.length === slots.length} title="四份业务资料" note={`${requiredDocs.length} / 4 已上传`} /><Gate ok={extractionComplete} title="文件提取状态" note={docs.some(doc => doc.extraction_status === 'failed') ? '存在提取失败，需要重试' : `${requiredDocs.filter(doc => doc?.extraction_status === 'completed').length} / 4 提取完成`} />{alreadyLocked ? <Gate ok title="提取提示已记录" note={extractionGate.blockers.length ? `${extractionGate.blockers.length} 项提示将随审核结果展示` : '资料快照已锁定，无需再次处理'} /> : <Gate ok={autoGatePass} title="自动质量门禁" note={autoGatePass ? '资料完整，可直接进入审核结果' : `${extractionGate.blockers.length} 项提取异常，需要聚焦处理`} />}</div>
        <div className="scope-field"><div><BookOpenCheck/><span><b>测试标准（可选）</b><small>{overview?.standards?.length ? overview.standards.map(item => item.code).join('、') : '未选择时跳过标准条款审查'}</small></span></div><button onClick={() => setStandardOpen(true)}>管理标准</button></div>
        <div className="scope-field"><div><UsersRound/><span><b>项目组 / 归属</b><small>{overview?.project_group_name || '用于权限、筛选和归档'}</small></span></div><Select aria-label="选择项目组" placeholder="选择" data={(groupsQuery.data || []).map(group => ({ value: group.id, label: group.name }))} value={overview?.project_group_id || null} onChange={async value => { if (value) await updateDocumentSetProjectGroup(setId, value); queryClient.invalidateQueries({ queryKey: ['set', setId] }); queryClient.invalidateQueries({ queryKey: ['sets'] }) }} /></div>
        <button className="primary-button lock-button" disabled={!complete} onClick={lockAndContinue}>{alreadyLocked ? (overview?.status === 'reviewed' ? '审核已完成，查看结果' : '任务已锁定，进入机器审核') : complete ? (autoGatePass ? '通过门禁并开始审核' : '记录提取提示并开始审核') : `还需完成 ${slots.filter(slot => !docs.some(doc => doc.doc_type === slot.type && doc.extraction_status === 'completed')).length} 项`}<Check size={16}/></button>
        {!complete && <p className="gate-help">四份资料完成后台提取后即可开始审核；无需逐字段核查。</p>}
      </aside>
    </div>
    <Modal opened={standardOpen} onClose={() => setStandardOpen(false)} title="配置本次审核的标准范围" size="xl" closeButtonProps={{ 'aria-label': '关闭标准选择窗口' }} classNames={{ content: 'standard-scope-modal', body: 'standard-scope-modal-body', header: 'standard-scope-modal-header' }}>
      <div className="standard-scope-shell">
        <div className="standard-scope-intro"><div><span className="eyebrow">REVIEW SCOPE</span><h2>把文档引用和已发布标准对齐</h2><p>左侧处理文档中检测到的引用；右侧从标准库选择本轮可引用的已发布版本。</p></div><div className="standard-scope-summary"><b>{selectedStandards.length}</b><span>已选标准</span></div></div>
        <div className="standard-scope-grid">
          <section className="standard-reference-panel"><header><div><h3>文档引用</h3><p>每个引用都要选用对应标准，或写明本轮跳过原因；原文中的标准编号与条款定位已用蓝色标出。</p></div><span>{referencesQuery.data ? `${referencesQuery.data.references.length} 条引用 · ${selectedStandards.length} 个标准` : '读取中'}</span></header><div className="standard-reference-scroll">{referencesQuery.isLoading ? <div className="standard-scope-state">正在读取文档引用…</div> : referencesQuery.isError ? <div className="standard-scope-state reference-load-error">文档引用读取失败，暂时不能确认处理状态。</div> : referencesQuery.data?.references.length ? <>{referencesQuery.data.references.map(reference => <StandardReferenceRow key={reference.normalized_code} reference={reference} standards={standardsQuery.data || []} selectedIds={selectedStandards} skipReason={skipReasons[reference.normalized_code] || ''} onSkipReasonChange={value => setSkipReasons(current => ({ ...current, [reference.normalized_code]: value }))} />)}{extraSelectedStandards.length > 0 && <div className="standard-extra-selection"><header><b>本轮额外纳入标准</b><span>{extraSelectedStandards.length} 个</span></header><div>{extraSelectedStandards.map(standard => <em key={standard.id}>{standard.code}{standard.version ? ` · ${standard.version}` : ''}</em>)}</div><p>这些标准未在四份资料中明确引用，但会作为本轮审核的知识来源。</p></div>}</> : <div className="standard-scope-state">当前四份资料中未检测到明确的标准编号。</div>}</div></section>
          <section className="standard-library-panel"><header><div><h3>已发布标准库</h3><p>已自动勾选资料中明确引用的发布标准，也可以手动调整。</p></div><span>{filteredStandards.length} / {(standardsQuery.data || []).length}</span></header><label className="standard-library-search"><Search size={15}/><input aria-label="搜索已发布标准" placeholder="搜索编号、标题或组织" value={standardQuery} onChange={event => setStandardQuery(event.currentTarget.value)}/>{standardQuery && <button aria-label="清除标准搜索" onClick={() => setStandardQuery('')}><X size={14}/></button>}</label><div className="standard-picker-scroll">{standardsQuery.isLoading ? <div className="standard-scope-state">正在读取标准库…</div> : standardsQuery.isError ? <div className="standard-scope-state reference-load-error">标准库读取失败，请稍后重试。</div> : filteredStandards.length ? filteredStandards.map(standard => <label key={standard.id} className={`standard-picker-row ${selectedStandards.includes(standard.id) ? 'selected' : ''}`}><input type="checkbox" checked={selectedStandards.includes(standard.id)} onChange={event => { const checked = event.currentTarget.checked; setSelectedStandards(current => checked ? [...current, standard.id] : current.filter(id => id !== standard.id)) }}/><span className="standard-picker-check"/><span className="standard-picker-copy"><b>{standard.code}</b><small>{standard.title}</small><em>{standard.organization || '组织未记录'}{standard.version ? ` · ${standard.version}` : ''}{standard.requirements_count != null ? ` · ${standard.requirements_count} 条要求` : ''}</em></span><StatusPill status={standard.graph_status || 'pending'} /></label>) : <div className="standard-scope-state">没有匹配的已发布标准。尝试编号或标题关键词。</div>}</div></section>
        </div>
        <div className="standard-scope-footer"><p className="modal-note">未选择标准时，系统仍会执行四文档一致性审核；只有明确引用且未处理的标准，才需要在这里选择或填写跳过原因。</p><div className="modal-actions"><button className="secondary-button" onClick={() => setStandardOpen(false)}>取消</button><button className="primary-button" disabled={referencesQuery.isLoading || referencesQuery.isError} onClick={saveStandards}>保存审核范围</button></div></div>
      </div>
    </Modal>
    <Modal opened={Boolean(removeTarget)} onClose={() => setRemoveTarget(null)} title={`移除${removeTarget ? DOC_LABELS[removeTarget.doc_type] : '文件'}？`} centered closeButtonProps={{ 'aria-label': '关闭移除资料窗口' }}><p>如果任务已经形成历史版本，应创建修订而不是移除锁定版本中的文件。</p><div className="modal-actions"><button className="secondary-button" onClick={() => setRemoveTarget(null)}>取消</button><button className="danger-button" onClick={remove}>确认移除</button></div></Modal>
  </div>
}

function Gate({ ok, title, note }: { ok: boolean; title: string; note: string }) { return <div className={ok ? 'gate-ok' : 'gate-warn'}><span>{ok ? <Check size={14}/> : '!'}</span><div><b>{title}</b><small>{note}</small></div><em>{ok ? '完成' : '需处理'}</em></div> }

function DocumentSlot({ slot, document, onUpload, onRemove }: { slot: typeof slots[number]; document?: SetDocument; onUpload: (file: File, type: DocType, replaceId?: string) => void; onRemove: (doc: SetDocument) => void }) {
  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({ multiple: false, noClick: Boolean(document), onDropAccepted: files => files[0] && onUpload(files[0], slot.type, document?.doc_id) })
  const Icon = slot.icon
  return <article {...getRootProps()} className={`document-slot ${document ? 'filled' : ''} ${isDragActive ? 'dragging' : ''}`}>
    <input {...getInputProps()} /><div className="slot-top"><span><Icon /></span><div><h3>{DOC_LABELS[slot.type]} <em>必传</em></h3><p>{slot.note}</p></div></div>
    {document ? <><div className="file-ticket"><div><b>{document.filename}</b><small>{formatBytes(document.file_size)} · {document.page_count || '—'} {slot.type === 'original_records' ? '个成员' : '页'} · v{document.doc_version || 1}</small></div><StatusPill status={document.extraction_status || 'pending'} /></div><div className="slot-actions"><button onClick={event => { event.stopPropagation(); open() }}><RefreshCw size={14}/>替换</button><button onClick={event => { event.stopPropagation(); onRemove(document) }}><Trash2 size={14}/>移除</button></div></> : <div className="slot-empty"><Upload/><span><b>{isDragActive ? '松开即可添加' : '拖入或选择文件'}</b><small>{slot.accept}</small></span></div>}
  </article>
}
