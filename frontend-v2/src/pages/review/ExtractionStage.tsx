import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Textarea, Tooltip } from '@mantine/core'
import { AlertCircle, Check, ChevronLeft, ChevronRight, ExternalLink, RotateCcw, Save, Search, ShieldCheck, ZoomIn, ZoomOut } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { notifications } from '@mantine/notifications'
import { apiErrorMessage, confirmDocumentReview, fetchAuthenticatedBlob, getDocumentFileUrl, getExtractions, openAuthenticatedResource, retryExtraction, saveOverrides } from '../../api'
import type { DocExtraction, DocumentSetOverview, ExtractionField } from '../../types'
import { DOC_LABELS, STATUS_LABELS } from '../../types'
import { EmptyState, LoadingState, StatusPill } from '../../components/common'
import { extractionGateFor, shouldPollExtractions } from '../../reviewLogic'

function normalizeFields(extraction?: DocExtraction): ExtractionField[] {
  if (!extraction) return []
  if (Array.isArray(extraction.fields)) return extraction.fields.map((field, index) => ({ ...field, key: field.key || field.field_name || `field_${index}` }))
  const source = extraction.fields || extraction.data || {}
  return Object.entries(source).map(([key, value]) => {
    if (value && typeof value === 'object' && !Array.isArray(value) && ('value' in value || 'exact_quote' in value)) return { key, label: key, ...(value as Record<string, unknown>) } as ExtractionField
    return { key, label: key, value, confidence: 1, confirmed: true }
  })
}

export default function ExtractionStage({ setId, overview, demo }: { setId: string; overview?: DocumentSetOverview; demo: boolean }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const demoExtractions: DocExtraction[] = (overview?.documents || []).map((doc, docIndex) => ({ doc_id: doc.doc_id, doc_type: doc.doc_type, filename: doc.filename, status: 'completed', method: doc.doc_type === 'order_form' ? 'code' : doc.doc_type === 'original_records' ? 'code + llm' : 'llm', confidence: docIndex === 1 ? .86 : .94, fields: [
    { key: 'test_item', label: doc.doc_type === 'order_form' ? '产品名称' : '测试项目', value: doc.doc_type === 'order_form' ? 'RD20 平台执行器用 280 电机' : '反向电压', confidence: .96, exact_quote: '反向电压试验 / Reverse voltage test', page_number: 6, confirmed: true },
    { key: 'condition', label: '试验条件', value: '14 V / 60 s · Mode 2', confidence: .91, exact_quote: 'Test voltage: 14 V, duration: 60 s, operating mode 2', page_number: 6, confirmed: true },
    { key: 'requirement', label: '判定要求', value: '功能状态等级 C', confidence: .74, exact_quote: '实验后满足性能要求，功能状态等级 C。', page_number: 6, confirmed: false },
    { key: 'standard', label: '引用标准', value: 'ISO 16750-2:2012 4.6.2', confidence: .88, exact_quote: 'ISO 16750-2:2012, Clause 4.6.2', page_number: 7, confirmed: true },
  ] }))
  const extractionQuery = useQuery({
    queryKey: ['extractions', setId, demo],
    queryFn: () => demo ? Promise.resolve({ set_id: setId, extractions: demoExtractions }) : getExtractions(setId),
    enabled: Boolean(overview),
    refetchInterval: query => shouldPollExtractions(query.state.data, demo) ? 1500 : false,
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: true,
    refetchOnMount: 'always',
  })
  const extractions = extractionQuery.data?.extractions || []
  const extractionGate = extractionGateFor(overview, demo)
  const exceptionDocTypes = useMemo(() => new Set(extractionGate.blockers.map(item => item.doc_type)), [extractionGate.blockers])
  const visibleExtractions = extractionGate.status === 'block'
    ? extractions.filter(item => exceptionDocTypes.has(item.doc_type))
    : extractions
  const [selectedDocId, setSelectedDocId] = useState('')
  const selectedExtraction = visibleExtractions.find(item => item.doc_id === selectedDocId) || visibleExtractions[0]
  const fields = useMemo(() => normalizeFields(selectedExtraction), [selectedExtraction])
  const [selectedKey, setSelectedKey] = useState('')
  const selected = fields.find(field => (field.key || field.field_name) === selectedKey) || fields[0]
  const [draftValue, setDraftValue] = useState('')
  const [zoom, setZoom] = useState(100)
  const [showAllFields, setShowAllFields] = useState(false)
  const [confirmedKeys, setConfirmedKeys] = useState<Set<string>>(new Set())
  const [reviewedDocs, setReviewedDocs] = useState<Set<string>>(() => new Set(
    (overview?.documents || []).filter(item => Boolean(item.reviewed_at)).map(item => item.doc_type),
  ))
  const alreadyLocked = overview?.status === 'locked' || overview?.status === 'reviewing' || overview?.status === 'reviewed'
  const [sourceState, setSourceState] = useState<{ status: 'idle' | 'loading' | 'ready' | 'unsupported' | 'error'; url?: string }>({ status: 'idle' })

  useEffect(() => {
    if (demo || !selectedExtraction) { setSourceState({ status: 'idle' }); return undefined }
    let active = true
    let objectUrl = ''
    const controller = new AbortController()
    setSourceState({ status: 'loading' })
    fetchAuthenticatedBlob(getDocumentFileUrl(setId, selectedExtraction.doc_id), controller.signal).then(blob => {
      if (!active) return
      const renderable = /^application\/pdf|^image\/|^text\//.test(blob.type)
      if (!renderable) { setSourceState({ status: 'unsupported' }); return }
      objectUrl = URL.createObjectURL(blob)
      setSourceState({ status: 'ready', url: objectUrl })
    }).catch(() => { if (active) setSourceState({ status: 'error' }) })
    return () => { active = false; controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [demo, setId, selectedExtraction?.doc_id])

  useEffect(() => { if (visibleExtractions.length && !visibleExtractions.some(item => item.doc_id === selectedDocId)) setSelectedDocId(visibleExtractions[0].doc_id) }, [visibleExtractions, selectedDocId])
  const exceptionFields = useMemo(() => fields.filter(field => {
    const value = String(field.value ?? '').trim()
    return (field.confidence != null && field.confidence < .8) || !value || value === '—'
  }), [fields])
  const displayFields = showAllFields || extractionGate.status !== 'block'
    ? fields
    : exceptionFields.length ? exceptionFields : fields.slice(0, 1)
  useEffect(() => { setSelectedKey(String(displayFields[0]?.key || displayFields[0]?.field_name || '')) }, [selectedDocId, displayFields.length])
  useEffect(() => { setDraftValue(selected?.value == null ? '' : typeof selected.value === 'string' ? selected.value : JSON.stringify(selected.value, null, 2)) }, [selected])

  async function save() {
    if (!selectedExtraction || !selected) return
    const key = String(selected.key || selected.field_name)
    try { if (!demo) await saveOverrides(setId, selectedExtraction.doc_id, { [key]: draftValue }); setReviewedDocs(current => { const next = new Set(current); next.delete(selectedExtraction.doc_type); return next }); setConfirmedKeys(current => new Set(current).add(`${selectedExtraction.doc_id}:${key}`)); notifications.show({ color: 'green', title: '字段已确认', message: '人工值和原文定位将随当前资料版本保存；请重新确认本份资料核查完成。' }); queryClient.invalidateQueries({ queryKey: ['extractions', setId] }); queryClient.invalidateQueries({ queryKey: ['set', setId] }) }
    catch (error) { notifications.show({ color: 'red', message: apiErrorMessage(error) }) }
  }
  async function retry() {
    if (!selectedExtraction) return
    try { if (!demo) await retryExtraction(setId, selectedExtraction.doc_id); notifications.show({ color: 'blue', message: '已重新加入提取队列' }); queryClient.invalidateQueries({ queryKey: ['extractions', setId] }) }
    catch (error) { notifications.show({ color: 'red', message: apiErrorMessage(error) }) }
  }
  async function confirmDocument() {
    if (!selectedExtraction) return
    try {
      if (!demo) await confirmDocumentReview(setId, selectedExtraction.doc_id)
      setReviewedDocs(current => new Set(current).add(selectedExtraction.doc_type))
      queryClient.invalidateQueries({ queryKey: ['set', setId] })
      notifications.show({ color: 'green', title: `${DOC_LABELS[selectedExtraction.doc_type]}核查完成`, message: '核查人和时间已保存，并计入锁定前门禁。' })
    } catch (error) {
      notifications.show({ color: 'red', title: '核查确认保存失败', message: apiErrorMessage(error) })
    }
  }

  if (extractionQuery.isLoading) return <LoadingState label="正在汇总四份资料的提取结果…" />
  if (extractionGate.status === 'pass') return <div className="stage-page extraction-stage extraction-bypass-stage">
    <div className="stage-intro compact"><div className="eyebrow">AUTOMATIC QUALITY GATE</div><h1>资料已通过自动质量门禁</h1><p>四份资料均形成完整、可追溯的提取快照，本次不需要逐项人工核查。</p></div>
    <section className="extraction-bypass-card"><div className="extraction-bypass-icon"><ShieldCheck size={25}/></div><div><h2>已跳过全量提取核查</h2><p>{extractionGate.note}</p><div className="extraction-bypass-docs">{(overview?.documents || []).filter(doc => doc.doc_type !== 'test_standard').map(doc => <div key={doc.doc_id}><span><Check size={13}/></span><b>{DOC_LABELS[doc.doc_type]}</b><small>{doc.filename}</small></div>)}</div><div className="extraction-bypass-actions"><button className="secondary-button" onClick={() => navigate(`/tasks/${setId}/intake`)}>返回资料与范围</button>{['locked', 'reviewing', 'reviewed'].includes(overview?.status || '') && <button className="primary-button" onClick={() => navigate(`/tasks/${setId}/run`)}>进入机器审核</button>}</div></div></section>
    <div className="extraction-bypass-note"><b>仍可查看原文</b><span>如需抽查，打开审核结果中的证据即可直接定位；只有质量门禁失败时才会回到异常核查。</span></div>
  </div>
  if (!visibleExtractions.length) return <EmptyState icon="error" title="没有可处理的异常资料" description="自动质量门禁已记录异常，但当前没有返回对应提取结果。请重试提取或查看运行日志。" />
  const currentIndex = displayFields.indexOf(selected)
  const isConfirmed = Boolean(selected?.confirmed || confirmedKeys.has(`${selectedExtraction?.doc_id}:${selected?.key || selected?.field_name}`))
  const allReady = extractions.every(item => item.status === 'completed')
  const reviewTargetCount = extractionGate.status === 'block' ? visibleExtractions.length : 4
  const reviewedCount = alreadyLocked ? reviewTargetCount : visibleExtractions.filter(item => reviewedDocs.has(item.doc_type)).length

  return <div className="stage-page extraction-stage">
    <div className="stage-intro compact"><div className="eyebrow">EXCEPTION-ONLY REVIEW</div><h1>只处理自动质量门禁拦截的资料</h1><p>当前有 {visibleExtractions.length} 份资料需要聚焦确认，其余资料不再重复核查。</p></div>
    <div className="doc-switcher" role="tablist">{visibleExtractions.map(item => <button role="tab" aria-selected={item.doc_id === selectedExtraction?.doc_id} className={item.doc_id === selectedExtraction?.doc_id ? 'active' : ''} key={item.doc_id} onClick={() => setSelectedDocId(item.doc_id)}><span>{DOC_LABELS[item.doc_type]}</span><b>{item.filename}</b><small>{item.method || '提取'} · {item.confidence ? `置信度 ${item.confidence.toFixed(2)}` : STATUS_LABELS[item.status || ''] || item.status || '状态未记录'}</small></button>)}</div>
    <div className="extraction-alert"><AlertCircle size={16}/><span>仅展示质量门禁拦截的资料。当前识别到 <b>{exceptionFields.length}</b> 项字段级异常{!exceptionFields.length && fields.length ? '；未返回字段级风险，已收起全部字段' : ''}。</span><button onClick={() => setShowAllFields(value => !value)}>{showAllFields ? '收起全部字段' : `查看全部 ${fields.length} 个字段`}</button><button onClick={retry}><RotateCcw size={14}/>重试本份提取</button></div>
    <div className="extraction-workspace">
      <aside className="field-list"><div className="field-list-head"><b>{showAllFields ? '全部提取字段' : '异常字段'}</b><small>{displayFields.length} 项{!showAllFields && fields.length > displayFields.length ? ` / ${fields.length}` : ''}</small></div>{displayFields.map((field, index) => { const key = String(field.key || field.field_name || index); const confirmed = field.confirmed || confirmedKeys.has(`${selectedExtraction?.doc_id}:${key}`); const low = field.confidence != null && field.confidence < .8; return <button className={`${key === (selected?.key || selected?.field_name) ? 'active' : ''} ${low ? 'low' : ''}`} key={key} onClick={() => setSelectedKey(key)}><span>{confirmed ? <Check size={13}/> : index + 1}</span><div><b>{field.label || field.field_name || key}</b><small>{String(field.value ?? '未提取')}</small></div><em>{confirmed ? '已确认' : field.confidence != null ? field.confidence.toFixed(2) : '—'}</em></button>})}</aside>
      <section className="source-preview"><div className="source-toolbar"><div><span>原文出处</span><b>{selectedExtraction?.filename}</b></div><div><button aria-label="缩小" onClick={() => setZoom(v => Math.max(60, v - 20))}><ZoomOut size={15}/></button><span>{zoom}%</span><button aria-label="放大" onClick={() => setZoom(v => Math.min(180, v + 20))}><ZoomIn size={15}/></button>{selectedExtraction && <button className="text-button source-file-button" onClick={() => { void openAuthenticatedResource(getDocumentFileUrl(setId, selectedExtraction.doc_id)) }}>打开原文件 <ExternalLink size={13}/></button>}</div></div>
        <div className="source-canvas">{demo ? <MockExtractionPage zoom={zoom} quote={selected?.exact_quote || String(selected?.value || '')} /> : !selectedExtraction ? null : sourceState.status === 'loading' ? <div className="loading-state"><span>正在加载原文…</span></div> : sourceState.status === 'ready' && sourceState.url ? <iframe title={`${selectedExtraction.filename}原文`} src={sourceState.url} style={{ width: `${zoom}%` }} /> : selectedExtraction.plain_text ? <ExtractedTextPreview text={selectedExtraction.plain_text} quote={selected?.exact_quote || ''} zoom={zoom} /> : sourceState.status === 'error' ? <div className="empty-state"><span><AlertCircle size={24} /></span><h3>原文加载失败</h3><p>请检查网络连接后重试，或点击上方「打开原文件」在新标签页查看。</p></div> : sourceState.status === 'unsupported' ? <div className="empty-state"><span><ExternalLink size={24} /></span><h3>没有可展示的提取文本</h3><p>点击上方「打开原文件」在新标签页查看。</p></div> : null}</div>
        <blockquote><span>审核快照中的原文摘录</span>{selected?.exact_quote || '该字段暂未返回原文摘录，请人工查看文件并补充依据。'}<small>{selected?.page_number ? `第 ${selected.page_number} 页` : selected?.sheet_name || ''}</small></blockquote>
      </section>
      <aside className="field-editor"><div className="editor-head"><span>字段详情</span><StatusPill status={isConfirmed ? 'complete' : selected?.confidence != null && selected.confidence < .8 ? 'warning' : 'pending'}>{isConfirmed ? '已确认' : '待核查'}</StatusPill></div><div className="confidence-meter"><div><span>字段置信度</span><b>{selected?.confidence != null ? selected.confidence.toFixed(2) : '未提供'}</b></div><i><em style={{ width: `${(selected?.confidence || 0) * 100}%` }} /></i></div>
        <label>字段值<Textarea autosize minRows={3} value={draftValue} onChange={event => setDraftValue(event.currentTarget.value)} /></label>
        <div className="field-meta"><span>定位</span><b>{selected?.sheet_name ? `${selected.sheet_name} ${selected?.page_number || ''}` : selected?.page_number ? `第 ${selected.page_number} 页` : selected?.source || '待补充'}</b></div>
        <div className="field-nav"><Tooltip label="上一个字段"><button disabled={currentIndex <= 0} onClick={() => setSelectedKey(String(displayFields[currentIndex - 1]?.key || displayFields[currentIndex - 1]?.field_name))}><ChevronLeft/></button></Tooltip><span>{currentIndex + 1} / {displayFields.length}</span><Tooltip label="下一个字段"><button disabled={currentIndex >= displayFields.length - 1} onClick={() => setSelectedKey(String(displayFields[currentIndex + 1]?.key || displayFields[currentIndex + 1]?.field_name))}><ChevronRight/></button></Tooltip></div>
        <button className="primary-button save-field" onClick={save}><Save size={15}/>{isConfirmed ? '保存人工修改' : '确认并保存字段'}</button><button className="secondary-button confirm-document" onClick={confirmDocument} disabled={alreadyLocked || reviewedDocs.has(selectedExtraction!.doc_type)}><Check size={15}/>{alreadyLocked || reviewedDocs.has(selectedExtraction!.doc_type) ? '本份资料已完成核查' : '确认本份资料核查完成'}</button>
      </aside>
    </div>
    <div className="stage-footer"><div><b>{reviewedCount} / {reviewTargetCount} 份资料已完成人工核查</b><span>{alreadyLocked ? '任务已锁定，仍可回看当时的提取结果。' : '只需确认质量门禁拦截的资料；替换文件会清除对应状态。'}</span></div><button className="primary-button" disabled={!allReady || reviewedCount < reviewTargetCount} onClick={() => navigate(`/tasks/${setId}/intake`)}>返回范围确认</button></div>
  </div>
}

function MockExtractionPage({ zoom, quote }: { zoom: number; quote: string }) { return <div className="mock-paper" style={{ width: `${zoom}%` }}><div className="paper-head"><b>EMC TEST PLAN</b><span>Page 6 of 18</span></div><h3>4.6 反向电压试验</h3><table><tbody><tr><th>测试项目</th><td>反向电压 / Reverse voltage</td></tr><tr><th>样品与模式</th><td>E20260402869601-0016 · Mode 2</td></tr><tr><th>试验条件</th><td>14 V / 60 s</td></tr><tr className="paper-highlight"><th>判定要求</th><td>{quote || '功能状态等级 C'}</td></tr><tr><th>引用标准</th><td>ISO 16750-2:2012 4.6.2</td></tr></tbody></table><p>试验后进行功能检查并记录最终结果。所有条件变化应在原始记录中留痕。</p></div> }

function ExtractedTextPreview({ text, quote, zoom }: { text: string; quote: string; zoom: number }) {
  const exact = quote.trim()
  const index = exact ? text.indexOf(exact) : -1
  return <pre className="extracted-text-preview" style={{ fontSize: `${zoom}%` }}>{index < 0 ? text : <>{text.slice(0, index)}<mark>{text.slice(index, index + exact.length)}</mark>{text.slice(index + exact.length)}</>}</pre>
}
