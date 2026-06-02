import type { BatchFileResult, UploadResponse } from '../types'
import { SEV_LABEL } from '../constants'

interface Props {
  results: BatchFileResult[]
  onSelect: (data: UploadResponse) => void
}

export default function BatchReviewer({ results, onSelect }: Props) {
  const totalIssues = results.reduce((s, r) => s + (r.review_items?.length || 0), 0)
  const passCount = results.filter((r) => r.overall_result === 'pass').length
  const failCount = results.filter((r) => r.overall_result === 'fail').length

  return (
    <div className="batch-results">
      <div className="batch-results-header">
        <h2>批量审核结果</h2>
        <div className="batch-results-summary">
          <span>{results.length} 个文件</span>
          <span className="batch-summary-sep">|</span>
          <span>{totalIssues} 处问题</span>
          <span className="batch-summary-sep">|</span>
          <span className="batch-summary-pass">{passCount} 通过</span>
          <span className="batch-summary-sep">|</span>
          <span className="batch-summary-fail">{failCount} 不通过</span>
        </div>
      </div>

      <div className="batch-results-grid">
        {results.map((r) => (
          <button
            key={r.index}
            className={`batch-result-card batch-result-${r.overall_result} ${r.error ? 'batch-result-error' : ''}`}
            onClick={() => {
              if (!r.error && r.highlighted_html) {
                onSelect({
                  report_id: r.report_id,
                  filename: r.filename,
                  overall_result: r.overall_result as 'pass' | 'fail' | 'warning' | 'error',
                  review_items: r.review_items || [],
                  highlighted_html: r.highlighted_html,
                  created_at: r.created_at || '',
                  estimated_tokens: r.estimated_tokens || 0,
                  token_limit: r.token_limit || 120000,
                  truncated: r.truncated || false,
	                  employee_id: r.employee_id || '',
	                  tags: r.tags || '',
	                  group_id: r.group_id || '',
	                  comparison: r.comparison || '',
	                  compared_with: r.compared_with || '',
	                  original_result: r.original_result || '',
	                })
              }
            }}
            disabled={!!r.error}
          >
            <div className="batch-card-index">#{r.index + 1}</div>
            <div className="batch-card-filename">{r.filename}</div>

            {r.error ? (
              <div className="batch-card-error">{r.error}</div>
            ) : (
              <>
                <div className="batch-card-stats">
                  <span className={`result-sm result-badge result-${r.overall_result}`}>
                    {r.overall_result === 'pass' ? '通过' :
                     r.overall_result === 'fail' ? '不通过' : '警告'}
                  </span>
                  <span className="batch-card-issues">
                    {(r.review_items || []).length} 处问题
                  </span>
                </div>

                {(r.review_items || []).length > 0 && (
                  <div className="batch-card-items">
                    {(r.review_items || []).slice(0, 3).map((item, i) => (
                      <div key={i} className="batch-card-item">
                        <span className={`sev-badge sev-badge-${item.severity}`}>
                          {SEV_LABEL[item.severity]}
                        </span>
                        <span className="batch-card-item-text">{item.error_description}</span>
                      </div>
                    ))}
                    {(r.review_items || []).length > 3 && (
                      <div className="batch-card-more">
                        +{(r.review_items || []).length - 3} 更多问题...
                      </div>
                    )}
                  </div>
                )}

                {r.duration_ms && (
                  <div className="batch-card-duration">
                    耗时 {(r.duration_ms / 1000).toFixed(1)}s
                  </div>
                )}
              </>
            )}

            <div className="batch-card-action">
              {r.error ? '解析失败' : '点击查看详情 →'}
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
