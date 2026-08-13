import { FormEvent, useEffect, useState } from 'react'
import { getSystemLogs, getSystemSettings, updateSystemSettings } from '../api'

const FIELDS = [
  ['jwt_expire_hours', '登录有效期（小时）', 'number'],
  ['max_upload_bytes', '最大上传字节数', 'number'],
  ['rate_max_upload', '上传限流次数', 'number'],
  ['rate_max_general', '普通请求限流次数', 'number'],
  ['rate_window_sec', '限流窗口（秒）', 'number'],
  ['batch_max_files', '批量文件上限', 'number'],
  ['deepseek_base_url', 'DeepSeek API 地址', 'text'],
] as const

export default function SystemOperations() {
  const [settings, setSettings] = useState<Record<string, string>>({})
  const [logs, setLogs] = useState<Array<Record<string, any>>>([])
  const [keyword, setKeyword] = useState('')
  const [level, setLevel] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  async function loadLogs() {
    const response = await getSystemLogs({ keyword: keyword || undefined, level: level || undefined, limit: 200 })
    setLogs(response.logs)
  }

  useEffect(() => {
    Promise.all([getSystemSettings(), getSystemLogs({ limit: 200 })])
      .then(([config, logResponse]) => { setSettings(config.settings); setLogs(logResponse.logs) })
      .catch(err => setError(err?.response?.data?.detail || err.message || '系统信息加载失败'))
  }, [])

  async function save(event: FormEvent) {
    event.preventDefault()
    setSaving(true); setError(''); setMessage('')
    try {
      const response = await updateSystemSettings(settings)
      setSettings(response.settings)
      setMessage('系统配置已保存')
    } catch (err: any) {
      setError(err?.response?.data?.detail || err.message || '配置保存失败')
    } finally { setSaving(false) }
  }

  return <div className="operations-page">
    {error && <div className="task-alert" role="alert">{error}</div>}
    {message && <div className="upload-success">{message}</div>}
    <section className="operations-card">
      <div className="operations-head"><div><h2>运行配置</h2><p>修改立即影响后续请求，不展示或记录模型密钥。</p></div></div>
      <form className="operations-settings" onSubmit={save}>
        {FIELDS.map(([key, label, type]) => <label key={key}><span>{label}</span><input type={type} value={settings[key] || ''} onChange={event => setSettings(current => ({ ...current, [key]: event.target.value }))} /></label>)}
        <button className="task-primary" disabled={saving}>{saving ? '保存中…' : '保存配置'}</button>
      </form>
    </section>
    <section className="operations-card">
      <div className="operations-head"><div><h2>运行日志</h2><p>按级别或关键词定位请求，不展示客户原文和完整模型响应。</p></div></div>
      <div className="operations-filters">
        <select value={level} onChange={event => setLevel(event.target.value)}><option value="">全部级别</option><option>INFO</option><option>WARN</option><option>ERROR</option></select>
        <input value={keyword} onChange={event => setKeyword(event.target.value)} placeholder="事件、请求 ID 或模块" />
        <button onClick={() => loadLogs().catch(err => setError(err.message || '日志加载失败'))}>查询</button>
      </div>
      <div className="operations-log-list">{logs.map((entry, index) => <div key={`${entry.timestamp || ''}-${index}`}>
        <time>{entry.timestamp || '—'}</time><b className={`log-${String(entry.level || 'info').toLowerCase()}`}>{entry.level || 'INFO'}</b><span>{entry.event || entry.message || '未命名事件'}</span><code>{entry.reqId || entry.request_id || ''}</code>
      </div>)}{!logs.length && <div className="task-empty">没有符合条件的日志</div>}</div>
    </section>
  </div>
}
