import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, Check, Copy, FileQuestion, ExternalLink } from 'lucide-react'
import { Link } from 'react-router-dom'
import { reportFrontendError } from '../api'

interface NotFoundPageProps {
  runtimeError?: boolean
  traceId?: string
  errorName?: string
  errorMessage?: string
  errorStack?: string
}

export default function NotFoundPage({ runtimeError = false, traceId, errorName = 'UnknownError', errorMessage = '页面运行时异常', errorStack = '' }: NotFoundPageProps) {
  const [copied, setCopied] = useState(false)
  const reported = useRef(false)

  useEffect(() => {
    if (!runtimeError || !traceId || reported.current) return
    reported.current = true
    void reportFrontendError({ traceId, route: window.location.pathname, errorName, errorMessage, stack: errorStack })
  }, [runtimeError, traceId, errorName, errorMessage, errorStack])

  async function copyTraceId() {
    if (!traceId) return
    try {
      await navigator.clipboard?.writeText(traceId)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    } catch {
      setCopied(false)
    }
  }

  return <main className="not-found">
    <span><FileQuestion/></span>
    <div className="eyebrow">{runtimeError ? 'PAGE ERROR · RECOVERABLE' : '404 · NOT IN EVIDENCE'}</div>
    <h1>{runtimeError ? '这个页面没有正常打开' : '这个页面不在当前审核路径中'}</h1>
    <p>{runtimeError ? '页面运行时发生异常；真实审核数据没有被当作空结果展示。请刷新后重试。' : '地址可能已失效，或当前角色没有访问权限。'}</p>
    {runtimeError && traceId && <section className="error-trace" aria-label="错误追踪信息">
      <div className="error-trace-heading"><span>错误追踪编号</span><em>用于日志检索与反馈</em></div>
      <div className="error-trace-value"><code>{traceId}</code><button type="button" onClick={copyTraceId}>{copied ? <Check size={14}/> : <Copy size={14}/>}<span>{copied ? '已复制' : '复制编号'}</span></button></div>
      <div className="error-trace-actions"><a href={`/operations?tab=logs&source=frontend&keyword=${encodeURIComponent(traceId)}`}><ExternalLink size={13}/>管理员查看前端日志</a><small>向开发或 QA 反馈时请附上此编号。</small></div>
    </section>}
    {runtimeError ? <a className="primary-button" href="/"><ArrowLeft size={16}/>刷新并返回工作概览</a> : <Link className="primary-button" to="/"><ArrowLeft size={16}/>返回工作概览</Link>}
  </main>
}
