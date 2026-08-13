import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Modal, Tabs, TextInput, Textarea } from '@mantine/core'
import { BookOpenCheck, Check, ChevronRight, FileUp, GitBranch, Search, Send, X } from 'lucide-react'
import { notifications } from '@mantine/notifications'
import { apiErrorMessage, getStandardGraph, getStandardKnowledge, getStandardReleases, getStandards, publishStandardGraph, reviewStandardRequirement, uploadStandard } from '../api'
import type { StandardGraphRequirement } from '../types'
import { EmptyState, LoadingState, PageHeader, StatusPill } from '../components/common'

function requirementId(requirement: StandardGraphRequirement) {
  return requirement.id || requirement.requirement_id || ''
}

export default function StandardsPage() {
  const queryClient = useQueryClient()
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [uploadOpen, setUploadOpen] = useState(false)
  const [publishConfirmOpen, setPublishConfirmOpen] = useState(false)
  const [selectedRequirement, setSelectedRequirement] = useState<StandardGraphRequirement | null>(null)
  const [reviewComment, setReviewComment] = useState('')
  const standardsQuery = useQuery({ queryKey: ['standards'], queryFn: () => getStandards() })
  const standards = standardsQuery.data || []
  const selected = standards.find(item => item.id === selectedId) || standards[0]
  const graphQuery = useQuery({ queryKey: ['standard-graph', selected?.id], queryFn: () => getStandardGraph(selected!.id), enabled: Boolean(selected) })
  const knowledgeQuery = useQuery({ queryKey: ['standard-knowledge', selected?.id], queryFn: () => getStandardKnowledge(selected!.id), enabled: Boolean(selected) })
  const releasesQuery = useQuery({ queryKey: ['standard-releases', selected?.id], queryFn: () => getStandardReleases(selected!.id), enabled: Boolean(selected) })
  const filtered = useMemo(() => standards.filter(item => `${item.code} ${item.title} ${item.organization}`.toLowerCase().includes(query.toLowerCase())), [standards, query])
  const latestRelease = releasesQuery.data?.releases?.[0] as any

  async function review(status: 'confirmed' | 'rejected') {
    if (!selected || !selectedRequirement) return
    try { const id = requirementId(selectedRequirement); if (!id) { notifications.show({ color: 'red', message: '该要求缺少稳定编号，暂不能提交审核结论。' }); return } await reviewStandardRequirement(selected.id, id, { review_status: status, comment: reviewComment }); notifications.show({ color: status === 'confirmed' ? 'green' : 'red', message: status === 'confirmed' ? '要求已确认' : '要求已驳回' }); setSelectedRequirement(null); setReviewComment(''); queryClient.invalidateQueries({ queryKey: ['standard-graph', selected.id] }) }
    catch (error) { notifications.show({ color: 'red', message: apiErrorMessage(error) }) }
  }
  async function publish() {
    if (!selected) return
    try { await publishStandardGraph(selected.id); notifications.show({ color: 'green', title: '标准知识版本已发布', message: '后续审核只会引用这个不可变发布版本。' }); setPublishConfirmOpen(false); queryClient.invalidateQueries({ queryKey: ['standards'] }); queryClient.invalidateQueries({ queryKey: ['standard-releases', selected.id] }) }
    catch (error) { notifications.show({ color: 'red', message: apiErrorMessage(error) }) }
  }

  return <div className="page standards-page"><PageHeader eyebrow="STANDARD KNOWLEDGE" title="标准库" description="好标准如好尺，量得出分寸，经得起追问。" actions={<button className="primary-button" onClick={() => setUploadOpen(true)}><FileUp size={16}/>上传标准</button>} />
    <div className="standards-layout"><aside className="standard-list"><label><Search/><input aria-label="搜索标准编号或标题" placeholder="搜索标准编号或标题" value={query} onChange={event => setQuery(event.currentTarget.value)}/></label><div className="standard-list-scroll">{standardsQuery.isLoading ? <LoadingState/> : standardsQuery.isError ? <EmptyState title="标准列表读取失败" description="请检查服务状态后重试。" /> : filtered.length ? filtered.map(item => <button className={selected?.id === item.id ? 'active' : ''} key={item.id} onClick={() => setSelectedId(item.id)}><div><b>{item.code}</b><StatusPill status={item.graph_status || 'pending'}/></div><p>{item.title}</p><small>{item.organization || '—'} · {item.category || '未分类'} · {item.requirements_count || 0} 条要求</small><ChevronRight/></button>) : <EmptyState title="没有匹配的标准" description="尝试更换编号或标题关键词。" />}</div></aside>
      <main className="standard-detail">{selected ? <><header><div><span className="standard-icon"><BookOpenCheck/></span><div><span>{selected.organization || '组织未记录'} · {selected.version || '版本未记录'}</span><h2>{selected.code}</h2><p>{selected.title}</p></div></div><div><StatusPill status={selected.knowledge_status || 'pending'}/><button className="primary-button" onClick={() => setPublishConfirmOpen(true)} disabled={graphQuery.isLoading || graphQuery.isError || graphQuery.data?.requirements.some(item => item.review_status === 'pending')}><Send size={15}/>{latestRelease ? '发布为新版本' : '发布确认版本'}</button></div></header>
        <div className="knowledge-meter"><div><span>原文知识化</span><b>{selected.chunks_count || knowledgeQuery.data?.total || 0} 个分块</b></div><div><span>关系化要求</span><b>{graphQuery.data?.requirements.length || 0} 条</b></div><div><span>等待人工确认</span><b>{graphQuery.data?.requirements.filter(item => item.review_status === 'pending').length || 0} 条</b></div></div>
        <Tabs defaultValue="requirements"><Tabs.List><Tabs.Tab value="requirements" leftSection={<GitBranch size={14}/>}>要求审核</Tabs.Tab><Tabs.Tab value="source">原文分块</Tabs.Tab><Tabs.Tab value="releases">发布记录</Tabs.Tab></Tabs.List>
          <Tabs.Panel value="requirements">{graphQuery.isLoading ? <LoadingState/> : graphQuery.isError ? <EmptyState title="关系化要求读取失败" description="原文数据未被当作审核通过，请稍后重试。" /> : graphQuery.data?.requirements.length ? <div className="requirement-table"><div className="requirement-row head"><span>条款</span><span>测试项 / 类型</span><span>要求说明</span><span>状态</span></div>{graphQuery.data.requirements.map((req, index) => <button className="requirement-row" key={requirementId(req) || `${req.clause_number || 'requirement'}-${index}`} onClick={() => { setSelectedRequirement(req); setReviewComment(req.review_comment || '') }}><span><b>{req.clause_number || '—'}</b><small>{req.clause_title}</small></span><span><b>{req.test_item || '通用'}</b><small>{req.requirement_type}</small></span><span><b>{req.interpretation_zh || req.statement}</b><small>{req.applicability || '适用范围待确认'}</small></span><StatusPill status={req.review_status || 'pending'}/></button>)}</div> : <EmptyState title="尚未形成关系化要求" description="标准完成知识化后，要求会出现在这里等待人工确认。" />}</Tabs.Panel>
          <Tabs.Panel value="source">{knowledgeQuery.isLoading ? <LoadingState/> : knowledgeQuery.isError ? <EmptyState title="原文分块读取失败" description="请检查知识化任务状态后重试。" /> : knowledgeQuery.data?.chunks.length ? <div className="knowledge-chunks">{knowledgeQuery.data.chunks.map(chunk => <article key={chunk.chunk_id}><span>{chunk.clause_number || '—'} · 第 {chunk.page_number || '—'} 页</span><h3>{chunk.clause_title || '原文分块'}</h3><p>{chunk.text || '此分块未保存可展示的原文。'}</p></article>)}</div> : <EmptyState title="还没有原文分块" description="上传标准并完成知识化后会显示可追溯原文。" />}</Tabs.Panel>
          <Tabs.Panel value="releases">{releasesQuery.isLoading ? <LoadingState/> : releasesQuery.isError ? <EmptyState title="发布记录读取失败" description="请稍后重试；审核不会引用无法读取的发布版本。" /> : releasesQuery.data?.releases?.length ? <div className="standard-release-list">{releasesQuery.data.releases.map((release: any) => <article key={release.id}><div><StatusPill status={release.status || 'published'}/><b>发布版本 v{release.release_number}</b><small>{new Date(release.published_at).toLocaleString('zh-CN')}</small></div><dl><div><dt>审核要求</dt><dd>{release.requirement_count} 条</dd></div><div><dt>发布人</dt><dd>{release.published_by || '未记录'}</dd></div><div><dt>提示版本</dt><dd>{release.prompt_version || '未记录'}</dd></div><div><dt>嵌入模型</dt><dd>{release.embedding_model || '未记录'}</dd></div><div className="release-hash"><dt>来源文件 SHA-256</dt><dd>{release.source_file_sha256 || '未记录'}</dd></div></dl></article>)}</div> : <EmptyState title="尚未发布不可变版本" description="所有关系化要求确认后，才可以形成业务审核可引用的发布版本。" />}</Tabs.Panel>
        </Tabs></> : <EmptyState title="选择一份标准" description="从左侧打开标准，查看知识化和人工确认状态。" />}</main></div>
    <Modal opened={uploadOpen} onClose={() => setUploadOpen(false)} title="上传测试标准" size="lg" closeButtonProps={{ 'aria-label': '关闭上传标准窗口' }}><StandardUpload onDone={() => { setUploadOpen(false); queryClient.invalidateQueries({ queryKey: ['standards'] }) }}/></Modal>
    <Modal opened={publishConfirmOpen} onClose={() => setPublishConfirmOpen(false)} title={latestRelease ? `发布 ${selected?.code} 的新版本？` : `发布 ${selected?.code || '标准'}？`} centered closeButtonProps={{ 'aria-label': '关闭标准发布确认窗口' }}><div className="publish-confirm"><p>发布后将形成不可变快照，后续审核只能引用已发布版本；当前草稿仍可继续演进并再次发布。</p><dl><div><dt>即将发布</dt><dd>v{Number(latestRelease?.release_number || 0) + 1}</dd></div><div><dt>当前要求</dt><dd>{graphQuery.data?.requirements.length || 0} 条</dd></div>{latestRelease && <div><dt>相对 v{latestRelease.release_number}</dt><dd>{(graphQuery.data?.requirements.length || 0) - Number(latestRelease.requirement_count || 0) >= 0 ? '+' : ''}{(graphQuery.data?.requirements.length || 0) - Number(latestRelease.requirement_count || 0)} 条</dd></div>}</dl><div className="modal-actions"><button className="secondary-button" onClick={() => setPublishConfirmOpen(false)}>取消</button><button className="primary-button" onClick={publish}><Send size={15}/>确认发布不可变版本</button></div></div></Modal>
    <Modal opened={Boolean(selectedRequirement)} onClose={() => setSelectedRequirement(null)} title={`${selectedRequirement?.clause_number || ''} ${selectedRequirement?.clause_title || '审核要求'}`} size="lg" closeButtonProps={{ 'aria-label': '关闭标准要求窗口' }}><div className="requirement-review"><span>标准原文证据</span><blockquote>{selectedRequirement?.evidence_quote || selectedRequirement?.statement}</blockquote><label>中文解释<Textarea minRows={3} value={selectedRequirement?.interpretation_zh || ''} readOnly /></label><label>审核意见<Textarea minRows={3} placeholder="确认或驳回时说明依据" value={reviewComment} onChange={event => setReviewComment(event.currentTarget.value)} /></label><div className="modal-actions"><button className="danger-button" onClick={() => review('rejected')}><X size={15}/>驳回</button><button className="primary-button" onClick={() => review('confirmed')}><Check size={15}/>确认要求</button></div></div></Modal>
  </div>
}

function StandardUpload({ onDone }: { onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null); const [code, setCode] = useState(''); const [title, setTitle] = useState(''); const [busy, setBusy] = useState(false)
  async function submit() { if (!file) return; setBusy(true); try { await uploadStandard(file, { code, title }); notifications.show({ color: 'green', title: '标准已上传', message: '文件已进入知识化队列。' }); onDone() } catch (error) { notifications.show({ color: 'red', message: apiErrorMessage(error) }) } finally { setBusy(false) } }
  return <div className="standard-upload"><label className="upload-drop"><FileUp/><b>{file?.name || '选择 PDF 标准文件'}</b><small>文件哈希重复时不会创建重复标准</small><input type="file" accept="application/pdf" onChange={event => setFile(event.target.files?.[0] || null)}/></label><div className="form-grid"><TextInput label="标准编号" value={code} onChange={event => setCode(event.currentTarget.value)}/><TextInput label="标准标题" value={title} onChange={event => setTitle(event.currentTarget.value)}/></div><div className="modal-actions"><button className="secondary-button" onClick={onDone}>取消</button><button className="primary-button" disabled={!file || busy} onClick={submit}>{busy ? '正在上传…' : '上传并开始知识化'}</button></div></div>
}
