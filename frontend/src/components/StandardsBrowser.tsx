import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  deleteStandard, downloadStandardFile, getStandardKnowledge, getStandards,
  rebuildStandardGraph, retryFailedStandardGraphUnits, retryStandardKnowledge, uploadStandard,
} from '../api'
import type { EmcStandard, StandardKnowledgeChunk } from '../types'
import StandardGraphReviewModal from './StandardGraphReviewModal'
import Icon from './Icons'

const ORG_OPTIONS = [
  { value: '', label: '全部机构' }, { value: 'ISO', label: 'ISO' },
  { value: 'IEC', label: 'IEC' }, { value: 'CISPR', label: 'CISPR' },
  { value: 'GB', label: 'GB（国标）' }, { value: 'EN', label: 'EN（欧盟）' },
  { value: 'FCC', label: 'FCC（美国）' }, { value: '企业', label: '企业标准' },
]

const CAT_OPTIONS = [
  { value: '', label: '全部类别' }, { value: 'radiated', label: '辐射发射' },
  { value: 'conducted', label: '传导发射' }, { value: 'immunity', label: '抗扰度' },
  { value: 'transient', label: '瞬态抗扰度' }, { value: 'enterprise', label: '企业要求' },
]

const STATUS_META: Record<string, { label: string; color: string; bg: string }> = {
  ready: { label: '原文已提取', color: '#15803d', bg: '#dcfce7' },
  processing: { label: '正在知识化', color: '#4338ca', bg: '#eef2ff' },
  pending: { label: '等待处理', color: '#a16207', bg: '#fef9c3' },
  failed: { label: '处理失败', color: '#dc2626', bg: '#fee2e2' },
  manual: { label: '待上传原文件', color: '#64748b', bg: '#f1f5f9' },
}

const GRAPH_STATUS_META: Record<string, { label: string; color: string; bg: string }> = {
  not_started: { label: '待生成审核内容', color: '#64748b', bg: '#f1f5f9' },
  extracting: { label: '正在识别标准要求', color: '#4338ca', bg: '#eef2ff' },
  pending_review: { label: '等待人工确认', color: '#b45309', bg: '#fff7ed' },
  published: { label: '已发布', color: '#15803d', bg: '#dcfce7' },
  failed: { label: '要求识别失败', color: '#dc2626', bg: '#fee2e2' },
}

const EMPTY_FORM = { code: '', title: '', organization: '', category: '', version: '' }

function pageLabel(chunk: StandardKnowledgeChunk) {
  if (!chunk.page_start) return '未定位页码'
  return chunk.page_start === chunk.page_end ? `第 ${chunk.page_start} 页` : `第 ${chunk.page_start}-${chunk.page_end} 页`
}

export default function StandardsBrowser() {
  const [standards, setStandards] = useState<EmcStandard[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [keyword, setKeyword] = useState('')
  const [orgFilter, setOrgFilter] = useState('')
  const [catFilter, setCatFilter] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [chunks, setChunks] = useState<Record<string, StandardKnowledgeChunk[]>>({})
  const [chunkLoading, setChunkLoading] = useState<string | null>(null)
  const [showUpload, setShowUpload] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [reviewingStandard, setReviewingStandard] = useState<EmcStandard | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)

  const fetchStandards = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true)
    setError(null)
    try {
      setStandards(await getStandards({
        organization: orgFilter || undefined,
        category: catFilter || undefined,
        keyword: keyword || undefined,
      }))
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '加载标准库失败')
    } finally {
      if (!quiet) setLoading(false)
    }
  }, [keyword, orgFilter, catFilter])

  useEffect(() => { fetchStandards() }, [fetchStandards])

  const hasActiveJobs = useMemo(
    () => standards.some((s) => s.knowledge_status === 'pending' || s.knowledge_status === 'processing' || s.graph_status === 'extracting'),
    [standards],
  )

  useEffect(() => {
    if (!hasActiveJobs) return
    const timer = window.setInterval(() => fetchStandards(true), 2500)
    return () => window.clearInterval(timer)
  }, [hasActiveJobs, fetchStandards])

  async function toggleDetail(std: EmcStandard) {
    if (expandedId === std.id) { setExpandedId(null); return }
    setExpandedId(std.id)
    if (std.knowledge_status !== 'ready' || chunks[std.id]) return
    setChunkLoading(std.id)
    try {
      const result = await getStandardKnowledge(std.id, '', 80)
      setChunks((prev) => ({ ...prev, [std.id]: result.chunks }))
    } catch (e: any) {
      setError(e?.response?.data?.detail || '知识内容加载失败')
    } finally {
      setChunkLoading(null)
    }
  }

  async function handleUpload() {
    if (!uploadFile) { setError('请选择 PDF 或 DOCX 标准文件'); return }
    setUploading(true)
    setError(null)
    try {
      const result = await uploadStandard(uploadFile, form)
      setShowUpload(false)
      setUploadFile(null)
      setForm(EMPTY_FORM)
      await fetchStandards()
      setExpandedId(result.id)
      if (result.duplicate) setError('检测到相同标准文件，已复用已有知识库版本。')
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '标准上传失败')
    } finally {
      setUploading(false)
    }
  }

  async function handleRetry(id: string) {
    try {
      await retryStandardKnowledge(id)
      await fetchStandards(true)
    } catch (e: any) {
      setError(e?.response?.data?.detail || '重新处理失败')
    }
  }

  async function handleGraphRebuild(id: string) {
    try {
      await rebuildStandardGraph(id)
      await fetchStandards(true)
    } catch (e: any) {
      setError(e?.response?.data?.detail || '标准要求识别启动失败')
    }
  }

  async function handleFailedUnitRetry(id: string) {
    try {
      await retryFailedStandardGraphUnits(id)
      await fetchStandards(true)
    } catch (e: any) {
      setError(e?.response?.data?.detail || '失败页面重试未完成')
    }
  }

  async function handleDownload(std: EmcStandard) {
    try {
      const { blob, filename } = await downloadStandardFile(std.id)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url; link.download = filename; link.click()
      URL.revokeObjectURL(url)
    } catch (e: any) {
      setError(e?.response?.data?.detail || '标准原文件下载失败')
    }
  }

  async function handleDelete(std: EmcStandard) {
    if (!window.confirm(`确定删除“${std.code}”吗？已被审核任务引用的版本不能删除。`)) return
    try {
      await deleteStandard(std.id)
      await fetchStandards()
    } catch (e: any) {
      setError(e?.response?.data?.detail || '删除失败，该标准可能已被审核任务引用')
    }
  }

  return (
    <div className="standards-browser">
      <div className="rules-header">
        <div>
          <h2 className="section-title" style={{ marginBottom: 4 }}>EMC 测试标准库</h2>
          <div style={{ color: '#94a3b8', fontSize: '.78rem' }}>标准在这里上传并一次性知识化，报告审核页只选择可用版本</div>
        </div>
        <button className="filter-btn standard-primary-btn" onClick={() => setShowUpload(true)}>＋ 添加标准</button>
      </div>

      <div className="rules-toolbar">
        <select className="filter-select" value={orgFilter} onChange={(e) => setOrgFilter(e.target.value)}>
          {ORG_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select>
        <select className="filter-select" value={catFilter} onChange={(e) => setCatFilter(e.target.value)}>
          {CAT_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select>
        <input className="filter-input" value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder="搜索标准编号或名称..." />
        <span className="rules-count">{standards.length} 条标准</span>
      </div>

      {error && <div className="upload-error" style={{ marginBottom: 14 }}>{error}<button onClick={() => setError(null)}>关闭</button></div>}
      {loading ? <div className="stats-loading"><div className="spinner" /> 加载中...</div> : standards.length === 0 ? (
        <div className="stats-empty"><p>暂无标准文件，请先添加标准</p></div>
      ) : (
        <div className="standards-list">
          {standards.map((std) => {
            const status = STATUS_META[std.knowledge_status] || STATUS_META.manual
            const graphStatus = GRAPH_STATUS_META[std.graph_status] || GRAPH_STATUS_META.not_started
            const detailOpen = expandedId === std.id
            return (
              <div key={std.id} className="standard-card">
                <div className="standard-card-header" onClick={() => toggleDetail(std)}>
                  <div className="standard-card-info">
                    <span className="standard-code">{std.code || '待识别标准'}</span>
                    <span className="standard-title">{std.title || std.source_filename}</span>
                    <div className="standard-meta">
                      {std.organization && <span className="standard-org">{std.organization}</span>}
                      {std.category && <span className="standard-cat">{std.category}</span>}
                      {std.version && <span className="standard-ver">{std.version}</span>}
                      <span style={{ color: status.color, background: status.bg, padding: '2px 8px', borderRadius: 10, fontSize: '.72rem', fontWeight: 700 }}>{status.label}</span>
                      {std.knowledge_status === 'ready' && <span style={{ color: graphStatus.color, background: graphStatus.bg, padding: '2px 8px', borderRadius: 10, fontSize: '.72rem', fontWeight: 700 }}>{graphStatus.label}</span>}
                      {std.latest_release_number > 0 && <span className="standard-ver">当前知识版本 R{std.latest_release_number}</span>}
                      {std.source_filename && <span className="standard-ver">{std.source_filename}</span>}
                    </div>
                  </div>
                  <div className="standard-actions" onClick={(e) => e.stopPropagation()}>
                    {std.source_filename && <button className="filter-btn" onClick={() => handleDownload(std)}>原文件</button>}
                    {std.knowledge_status === 'failed' && <button className="filter-btn" onClick={() => handleRetry(std.id)}>重新处理</button>}
                    {std.knowledge_status === 'ready' && (std.graph_status === 'not_started' || std.graph_status === 'failed') && <button className="filter-btn" onClick={() => handleGraphRebuild(std.id)}>识别标准要求</button>}
                    {std.knowledge_status === 'ready' && (std.graph_status === 'pending_review' || std.graph_status === 'published') && <button className="filter-btn standard-primary-btn" onClick={() => setReviewingStandard(std)}>{std.graph_status === 'published' ? '查看已发布内容' : `开始确认 (${std.graph_pending_count})`}</button>}
                    {std.graph_status === 'pending_review' && Array.isArray(std.graph_meta.failed_unit_ids) && std.graph_meta.failed_unit_ids.length > 0 && <button className="filter-btn" onClick={() => handleFailedUnitRetry(std.id)}>重试失败页面</button>}
                    {!std.is_builtin && <button className="filter-btn" onClick={() => handleDelete(std)}>删除</button>}
                    <button className="standard-icon-btn" title={detailOpen ? '收起' : '展开'} onClick={() => toggleDetail(std)}><Icon name={detailOpen ? 'chevron-down' : 'chevron-right'} size={16} /></button>
                  </div>
                </div>
                {detailOpen && (
                  <div className="standard-knowledge-detail">
                    <div className="standard-kpi-row">
                      <span><strong>{std.page_count || 0}</strong> 页</span>
                      <span><strong>{std.chunk_count || 0}</strong> 个知识块</span>
                      <span><strong>{std.graph_requirement_count || 0}</strong> 条标准要求</span>
                      <span><strong>{std.graph_pending_count || 0}</strong> 条待确认</span>
                      <span><strong>{std.release_count || 0}</strong> 个已发布知识版本</span>
                      <span>哈希 {std.file_sha256 ? std.file_sha256.slice(0, 12) : '无原文件'}</span>
                      <span>更新于 {std.updated_at ? new Date(std.updated_at).toLocaleString() : '-'}</span>
                    </div>
                    {std.knowledge_status === 'manual' && <div className="standard-empty-note">该条目只有人工录入的元数据，没有标准原文，不能在审核页选择。请通过“添加标准”上传对应 PDF / DOCX。</div>}
                    {std.knowledge_status === 'failed' && <div className="standard-failure-note">{std.knowledge_error || '知识化失败，请重新处理。'}</div>}
                    {(std.knowledge_status === 'pending' || std.knowledge_status === 'processing') && <div className="standard-empty-note">后台正在提取原文、识别章节并建立检索索引，完成后会自动变为可用。</div>}
                    {std.knowledge_status === 'ready' && std.graph_status === 'extracting' && <div className="standard-empty-note">系统正在从原文中识别适用范围、测试方法、条件、参数和判定规则。完成后需要人工逐条确认。</div>}
                    {std.graph_status === 'extracting' && Number(std.graph_meta.extraction_unit_count || 0) > 0 && <div className="standard-graph-progress"><span style={{ width: `${Math.round(Number(std.graph_meta.processed_unit_count || 0) / Number(std.graph_meta.extraction_unit_count) * 100)}%` }} /><em>{Number(std.graph_meta.processed_unit_count || 0)} / {Number(std.graph_meta.extraction_unit_count)} 个原文单元</em></div>}
                    {std.graph_error && <div className="standard-failure-note">{std.graph_error}</div>}
                    {std.knowledge_status === 'ready' && (
                      <div className="standard-chunk-list">
                        {chunkLoading === std.id ? <div className="stats-loading"><div className="spinner" /> 读取知识块...</div> : (chunks[std.id] || []).map((chunk) => (
                          <div key={chunk.id} className="standard-chunk-row">
                            <div className="standard-chunk-loc">{chunk.clause || `知识块 ${chunk.chunk_index + 1}`}<span>{pageLabel(chunk)}</span></div>
                            <div><strong>{chunk.title}</strong><p>{chunk.content}</p></div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {showUpload && (
        <div className="modal-overlay" onClick={() => !uploading && setShowUpload(false)}>
          <div className="modal-panel standard-upload-modal" onClick={(e) => e.stopPropagation()}>
            <div className="standard-upload-head"><div><h3>添加标准文件</h3><p>支持国际、国家、行业及企业标准；上传后自动形成可检索知识库</p></div><button className="standard-icon-btn" onClick={() => setShowUpload(false)} aria-label="关闭"><Icon name="close" size={16} /></button></div>
            <div className="standard-file-drop" onClick={() => fileRef.current?.click()}>
              <input ref={fileRef} type="file" accept=".pdf,.docx" hidden onChange={(e) => setUploadFile(e.target.files?.[0] || null)} />
              <strong>{uploadFile ? uploadFile.name : '点击选择标准 PDF / DOCX'}</strong>
              <span>{uploadFile ? `${(uploadFile.size / 1024 / 1024).toFixed(2)} MB` : '扫描页会由千问视觉定向转录，处理时间取决于页数'}</span>
            </div>
            <div className="standard-form-grid">
              <label>标准编号<input className="filter-input" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} placeholder="可留空，由 AI 识别" /></label>
              <label>版本<input className="filter-input" value={form.version} onChange={(e) => setForm({ ...form, version: e.target.value })} placeholder="如 2011" /></label>
              <label className="standard-form-wide">标准名称<input className="filter-input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="可留空，由 AI 识别" /></label>
              <label>发布机构<input className="filter-input" value={form.organization} onChange={(e) => setForm({ ...form, organization: e.target.value })} placeholder="如 ISO、GB、企业名称" /></label>
              <label>类别<input className="filter-input" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} placeholder="可留空，由 AI 分类" /></label>
            </div>
            <div className="modal-actions">
              <button className="filter-btn" onClick={() => setShowUpload(false)} disabled={uploading}>取消</button>
              <button className="filter-btn standard-primary-btn" onClick={handleUpload} disabled={uploading}>{uploading ? '上传中...' : '上传并建立知识库'}</button>
            </div>
          </div>
        </div>
      )}
      {reviewingStandard && <StandardGraphReviewModal standard={reviewingStandard} onClose={() => setReviewingStandard(null)} onChanged={() => fetchStandards(true)} />}
    </div>
  )
}
