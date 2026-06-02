type LogLevel = 'DEBUG' | 'INFO' | 'WARN' | 'ERROR'

interface LogEntry {
  ts: string
  level: LogLevel
  msg: string
  traceId: string
  reqId: string | null
  module: string
  userId: string | null
  ctx: Record<string, unknown> | null
  error: string | null
}

function safeUUID(): string {
  try { return crypto.randomUUID() } catch { /* fall through */ }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16)
  })
}
const traceId = safeUUID()
let currentReqId: string | null = null
let currentUserId: string | null = null

const LEVEL_RANK: Record<LogLevel, number> = { DEBUG: 0, INFO: 1, WARN: 2, ERROR: 3 }
const MIN_CONSOLE_LEVEL: LogLevel = import.meta.env.PROD ? 'ERROR' : 'DEBUG'
const MIN_REPORT_LEVEL: LogLevel = import.meta.env.PROD ? 'WARN' : 'INFO'

// ── LogCollector ──────────────────────────────────────────────────────────

const MAX_BUF = 50
const FLUSH_INTERVAL_MS = 5000
let buffer: LogEntry[] = []
let flushTimer: ReturnType<typeof setInterval> | null = null

function flush() {
  if (buffer.length === 0) return
  const batch = buffer.splice(0)
  fetch('/api/logs/batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ logs: batch }),
  }).catch(() => { /* silently drop */ })
}

function enqueue(entry: LogEntry) {
  buffer.push(entry)
  if (flushTimer === null) {
    flushTimer = setInterval(flush, FLUSH_INTERVAL_MS)
  }
  if (buffer.length >= MAX_BUF) {
    flush()
  }
}

try {
  if (typeof window !== 'undefined') {
    window.addEventListener('beforeunload', () => flush())
    window.addEventListener('visibilitychange', () => { if (document.hidden) flush() })
  }
} catch { /* defensive */ }

// ── Helpers ───────────────────────────────────────────────────────────────

const LEVEL_COLORS: Record<LogLevel, string> = {
  DEBUG: '#667085',
  INFO: '#175cd3',
  WARN: '#e0a60f',
  ERROR: '#d92d20',
}

function extractModule(): string {
  const err = new Error()
  const stack = err.stack || ''
  const lines = stack.split('\n')
  for (let i = 3; i < lines.length; i++) {
    const m = lines[i].match(/(?:at |@)(?:.*?[/(])?([^/)]+\.(?:tsx?|jsx?))(?::\d+:\d+[)]?)/)
    if (m && m[1] !== 'logger.ts') return m[1]
  }
  return 'unknown'
}

function shouldLog(level: LogLevel, min: LogLevel): boolean {
  return LEVEL_RANK[level] >= LEVEL_RANK[min]
}

function makeEntry(level: LogLevel, msg: string, ctx?: Record<string, unknown>, error?: unknown): LogEntry {
  return {
    ts: new Date().toISOString(),
    level,
    msg,
    traceId,
    reqId: currentReqId,
    module: extractModule(),
    userId: currentUserId,
    ctx: ctx || null,
    error: error instanceof Error ? (error.stack || error.message) : error != null ? String(error) : null,
  }
}

// ── Public API ────────────────────────────────────────────────────────────

export const logger = {
  debug(msg: string, ctx?: Record<string, unknown>) {
    const entry = makeEntry('DEBUG', msg, ctx)
    if (shouldLog('DEBUG', MIN_CONSOLE_LEVEL)) {
      console.debug(`%c[DEBUG]%c ${entry.ts.slice(11, 23)} %c${msg}`, `color:${LEVEL_COLORS.DEBUG}`, 'color:#98a2b3', 'color:inherit', ctx || '')
    }
    if (shouldLog('DEBUG', MIN_REPORT_LEVEL)) enqueue(entry)
  },

  info(msg: string, ctx?: Record<string, unknown>) {
    const entry = makeEntry('INFO', msg, ctx)
    if (shouldLog('INFO', MIN_CONSOLE_LEVEL)) {
      console.info(`%c[INFO]%c ${entry.ts.slice(11, 23)} %c${msg}`, `color:${LEVEL_COLORS.INFO}`, 'color:#98a2b3', 'color:inherit', ctx || '')
    }
    if (shouldLog('INFO', MIN_REPORT_LEVEL)) enqueue(entry)
  },

  warn(msg: string, ctx?: Record<string, unknown>) {
    const entry = makeEntry('WARN', msg, ctx)
    if (shouldLog('WARN', MIN_CONSOLE_LEVEL)) {
      console.warn(`%c[WARN]%c ${entry.ts.slice(11, 23)} %c${msg}`, `color:${LEVEL_COLORS.WARN}`, 'color:#98a2b3', 'color:inherit', ctx || '')
    }
    if (shouldLog('WARN', MIN_REPORT_LEVEL)) enqueue(entry)
  },

  error(msg: string, ctx?: Record<string, unknown>, error?: unknown) {
    const entry = makeEntry('ERROR', msg, ctx, error)
    if (shouldLog('ERROR', MIN_CONSOLE_LEVEL)) {
      const errMsg = error instanceof Error ? error.stack || error.message : error != null ? String(error) : ''
      console.error(`%c[ERROR]%c ${entry.ts.slice(11, 23)} %c${msg}`, `color:${LEVEL_COLORS.ERROR}`, 'color:#98a2b3', 'color:inherit', errMsg ? '\n' + errMsg : '', ctx || '')
    }
    if (shouldLog('ERROR', MIN_REPORT_LEVEL)) enqueue(entry)
  },

  setReqId(id: string | null) { currentReqId = id },
  setUserId(id: string | null) { currentUserId = id },
}
