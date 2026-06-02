import { useState, useEffect, FormEvent } from 'react'
import { getSettings, updateSettings } from '../api'

const SETTINGS_GROUPS = [
  {
    title: '认证设置',
    fields: [
      { key: 'jwt_expire_hours', label: 'JWT 过期时间（小时）', type: 'number' },
    ],
  },
  {
    title: '上传限制',
    fields: [
      { key: 'max_upload_bytes', label: '最大文件大小（字节）', type: 'number' },
      { key: 'batch_max_files', label: '批量上传最大文件数', type: 'number' },
    ],
  },
  {
    title: '限流设置',
    fields: [
      { key: 'rate_window_sec', label: '限流窗口（秒）', type: 'number' },
      { key: 'rate_max_upload', label: '每窗口最大上传次数', type: 'number' },
      { key: 'rate_max_general', label: '每窗口最大普通请求数', type: 'number' },
    ],
  },
  {
    title: 'API 设置',
    fields: [
      { key: 'deepseek_base_url', label: 'DeepSeek API 地址', type: 'text' },
    ],
  },
]

export default function SystemSettings() {
  const [settings, setSettings] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  useEffect(() => {
    getSettings()
      .then((res) => setSettings(res.settings))
      .catch((err) => setError(err.response?.data?.detail || err.message || '加载配置失败'))
      .finally(() => setLoading(false))
  }, [])

  function handleChange(key: string, value: string) {
    setSettings((prev) => ({ ...prev, [key]: value }))
    setSuccess('')
  }

  async function handleSave(e: FormEvent) {
    e.preventDefault()
    setSaving(true)
    setError('')
    setSuccess('')
    try {
      const res = await updateSettings(settings)
      setSettings(res.settings)
      setSuccess('配置保存成功')
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="history-loading">加载中...</div>

  return (
    <div className="system-settings">
      <h2 className="section-title">系统配置</h2>

      {error && <div className="upload-error">{error}</div>}
      {success && <div className="upload-success">{success}</div>}

      <form className="settings-form" onSubmit={handleSave}>
        {SETTINGS_GROUPS.map((group) => (
          <div key={group.title} className="settings-section">
            <h3 className="settings-section-title">{group.title}</h3>
            <div className="settings-grid">
              {group.fields.map((field) => (
                <div key={field.key} className="settings-group">
                  <label className="settings-label">{field.label}</label>
                  <input
                    className="filter-input"
                    type={field.type}
                    value={settings[field.key] || ''}
                    onChange={(e) => handleChange(field.key, e.target.value)}
                  />
                </div>
              ))}
            </div>
          </div>
        ))}
        <button className="filter-btn settings-save-btn" type="submit" disabled={saving}>
          {saving ? '保存中...' : '保存配置'}
        </button>
      </form>
    </div>
  )
}
