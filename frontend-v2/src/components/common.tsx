import type { ReactNode } from 'react'
import { AlertTriangle, Check, ChevronRight, CircleDot, FileQuestion, LoaderCircle, Search, X } from 'lucide-react'
import { Link } from 'react-router-dom'
import { STATUS_LABELS, type DocType, DOC_LABELS } from '../types'

export function PageHeader({ eyebrow, title, description, actions, breadcrumbs }: {
  eyebrow?: string; title: string; description?: string; actions?: ReactNode; breadcrumbs?: Array<{ label: string; to?: string }>
}) {
  return <header className="page-header">
    {breadcrumbs && <nav className="breadcrumbs" aria-label="面包屑">{breadcrumbs.map((item, i) => <span key={`${item.label}-${i}`}>{i > 0 && <ChevronRight size={12} />}{item.to ? <Link to={item.to}>{item.label}</Link> : item.label}</span>)}</nav>}
    <div className="page-title-row"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h1>{title}</h1>{description && <p>{description}</p>}</div>{actions && <div className="page-actions">{actions}</div>}</div>
  </header>
}

export function StatusPill({ status, children }: { status: string; children?: ReactNode }) {
  const tone = /fail|error|critical|缺失/.test(status) ? 'danger' : /warning|incomplete|unresolved|待|confirm/.test(status) ? 'warning' : /complete|reviewed|pass|ready/.test(status) ? 'success' : /running|building|locked/.test(status) ? 'active' : 'neutral'
  return <span className={`status-pill ${tone}`}><i />{children || STATUS_LABELS[status] || status}</span>
}

export function EmptyState({ icon = 'empty', title, description, action }: { icon?: 'empty' | 'error' | 'search'; title: string; description: string; action?: ReactNode }) {
  const Icon = icon === 'error' ? AlertTriangle : icon === 'search' ? Search : FileQuestion
  return <div className="empty-state"><span><Icon /></span><h3>{title}</h3><p>{description}</p>{action}</div>
}

export function LoadingState({ label = '正在读取数据…' }: { label?: string }) { return <div className="loading-state"><LoaderCircle className="spin" /><span>{label}</span></div> }

export function EvidenceRail({ states }: { states?: Partial<Record<DocType, 'ok' | 'warn' | 'missing' | 'idle'>> }) {
  const docs: DocType[] = ['order_form', 'test_plan', 'original_records', 'final_report']
  return <div className="evidence-rail" role="list" aria-label="四文档证据链">
    {docs.map((doc, index) => { const state = states?.[doc] || 'idle'; return <div className={`evidence-stop ${state}`} role="listitem" key={doc}>
      <span>{state === 'ok' ? <Check size={13} /> : state === 'missing' ? <X size={13} /> : <CircleDot size={12} />}</span>
      <div><b>{DOC_LABELS[doc]}</b><small>{state === 'ok' ? '证据已定位' : state === 'warn' ? '需要核查' : state === 'missing' ? '未找到证据' : '未参与'}</small></div>
      {index < docs.length - 1 && <i />}
    </div> })}
  </div>
}

export function formatDate(value?: string) {
  if (!value) return '—'
  try { return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value)) } catch { return value }
}
export function formatBytes(value?: number) {
  if (!value) return '—'
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

export function SkeletonRows({ count = 4 }: { count?: number }) { return <div className="skeleton-list">{Array.from({ length: count }, (_, i) => <div className="skeleton-row" key={i}><i /><span /><b /></div>)}</div> }
