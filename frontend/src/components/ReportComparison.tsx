import { useEffect, useState } from 'react'
import { getReportDetail } from '../api'
import type { ReportRecord, ReviewItem } from '../types'
import { SEV_LABEL } from '../constants'
import { logger } from '../logger'

interface Props { idA: string; idB: string; onClose: () => void }

interface DiffItem { status: 'fixed' | 'new' | 'persistent'; item: ReviewItem; counterpart?: ReviewItem }

const STATUS_LABEL: Record<string, string> = { fixed: '已修复', new: '新增', persistent: '持续' }
const STATUS_CLASS: Record<string, string> = { fixed: 'diff-fixed', new: 'diff-new', persistent: 'diff-persistent' }

function matchItems(itemsA: ReviewItem[], itemsB: ReviewItem[]): DiffItem[] {
  const usedB = new Set<number>()
  const results: DiffItem[] = []
  for (const a of itemsA) {
    let bestIdx = -1, bestScore = 0
    for (let j = 0; j < itemsB.length; j++) {
      if (usedB.has(j)) continue
      const b = itemsB[j]
      const locMatch = a.location === b.location ? 1 : 0
      const txtLen = Math.max(a.original_text.length, b.original_text.length)
      const txtSim = txtLen > 0 ? 1 - _editDist(a.original_text, b.original_text) / txtLen : 0
      const score = locMatch * 0.4 + txtSim * 0.6
      if (score > 0.5 && score > bestScore) { bestScore = score; bestIdx = j }
    }
    if (bestIdx >= 0) { usedB.add(bestIdx); results.push({ status: 'persistent', item: a, counterpart: itemsB[bestIdx] }) }
    else results.push({ status: 'fixed', item: a })
  }
  for (let j = 0; j < itemsB.length; j++) {
    if (!usedB.has(j)) results.push({ status: 'new', item: itemsB[j] })
  }
  return results.sort((x, y) => ['new', 'persistent', 'fixed'].indexOf(x.status) - ['new', 'persistent', 'fixed'].indexOf(y.status))
}

export default function ReportComparison({ idA, idB, onClose }: Props) {
  const [reportA, setReportA] = useState<ReportRecord | null>(null)
  const [reportB, setReportB] = useState<ReportRecord | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeStatus, setActiveStatus] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([getReportDetail(idA), getReportDetail(idB)])
      .then(([a, b]) => {
        setReportA(a); setReportB(b)
        if (b.comparison) {
          try {
            const precomputed = JSON.parse(b.comparison)
            if (Array.isArray(precomputed) && precomputed.length > 0) {
              (window as any).__precomputedComparison = precomputed
            }
          } catch (e: any) { logger.error('Failed to parse precomputed comparison', { component: 'ReportComparison' }, e) }
        }
      })
      .catch((e) => setError(e?.message || '加载报告失败'))
      .finally(() => setLoading(false))
  }, [idA, idB])

  if (loading) return <div className="stats-loading"><div className="spinner"/> 加载对比数据...</div>
  if (error) return <div className="upload-error">{error}</div>
  if (!reportA || !reportB) return <div className="upload-error">报告不存在</div>

  const diffs = matchItems(reportA.review_items, reportB.review_items)
  const filtered = activeStatus ? diffs.filter((d) => d.status === activeStatus) : diffs
  const summary = { fixed: 0, persistent: 0, new: 0 }
  diffs.forEach((d) => { summary[d.status]++ })

  return (
    <div className="comparison-container">
      <div className="comparison-header">
        <h2>报告对比</h2>
        <div className="comparison-files">
          <span className="comparison-file" title={reportA.filename}>旧: {reportA.filename}</span>
          <span className="comparison-file" title={reportB.filename}>新: {reportB.filename}</span>
        </div>
        <button className="compare-close-btn" onClick={onClose}>关闭对比</button>
      </div>
      <div className="comparison-summary">
        {(['fixed', 'persistent', 'new'] as const).map((s) => (
          <button key={s} className={`comparison-summary-btn ${STATUS_CLASS[s]} ${activeStatus === s ? 'active' : ''}`}
            onClick={() => setActiveStatus(activeStatus === s ? null : s)}>
            {STATUS_LABEL[s]}: {summary[s]}
          </button>
        ))}
      </div>
      <div className="comparison-layout">
        {filtered.map((d, i) => (
          <div key={i} className={`comparison-row ${STATUS_CLASS[d.status]}`}>
            <div className="comparison-status-badge">{STATUS_LABEL[d.status]}</div>
            {d.status === 'new' ? (
              <>
                <div className="comparison-side empty"/>
                <div className="comparison-side"><DiffItemCard item={d.item} idx={i}/></div>
              </>
            ) : (
              <>
                <div className="comparison-side"><DiffItemCard item={d.item} idx={i}/></div>
                <div className="comparison-side">
                  {d.counterpart ? <DiffItemCard item={d.counterpart} idx={i}/>
                    : <div className="comparison-removed">问题已修复</div>}
                </div>
              </>
            )}
          </div>
        ))}
        {filtered.length === 0 && <p className="chart-no-data">没有匹配的问题项</p>}
      </div>
    </div>
  )
}

function DiffItemCard({ item, idx }: { item: ReviewItem; idx: number }) {
  return (
    <div className={`error-item error-${item.severity} streaming-item`}>
      <div className="error-item-header">
        <span className={`sev-badge sev-badge-${item.severity}`}>{SEV_LABEL[item.severity]}</span>
        <span className="error-item-index">#{idx + 1}</span>
      </div>
      <div className="error-location">{item.location}</div>
      <div><mark className={`error-highlight-inline error-${item.severity}`}>{item.original_text}</mark></div>
      <div className="error-field"><span className="error-field-label">原因</span><br/>{item.error_description}</div>
      <div className="error-field"><span className="error-field-label">标准依据</span><br/>{item.standard_reference}</div>
      <div className="error-field"><span className="error-field-label">建议</span><br/>{item.suggestion}</div>
    </div>
  )
}

function _editDist(a: string, b: string): number {
  const m = a.length, n = b.length
  const dp: number[][] = Array.from({ length: m + 1 }, () => Array(n + 1).fill(0))
  for (let i = 0; i <= m; i++) dp[i][0] = i
  for (let j = 0; j <= n; j++) dp[0][j] = j
  for (let i = 1; i <= m; i++)
    for (let j = 1; j <= n; j++)
      dp[i][j] = a[i - 1] === b[j - 1] ? dp[i - 1][j - 1] : 1 + Math.min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])
  return dp[m][n]
}
