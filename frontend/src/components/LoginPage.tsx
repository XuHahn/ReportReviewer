import { useState, FormEvent } from 'react'
import { login } from '../api'

interface Props {
  onLogin: (user: import('../types').User) => void
}

export default function LoginPage({ onLogin }: Props) {
  const [employeeId, setEmployeeId] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const id = employeeId.trim()
    if (!id) {
      setError('请输入工号')
      return
    }
    setError('')
    setLoading(true)
    try {
      const res = await login(id)
      onLogin(res.user)
    } catch (err: any) {
      const msg = err.response?.data?.detail || err.message || '登录失败'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1>EMC检测报告智能审核系统</h1>
        <p className="login-subtitle">请使用工号登录</p>
        <form className="login-form" onSubmit={handleSubmit}>
          <input
            className="login-input"
            type="text"
            placeholder="请输入工号"
            value={employeeId}
            onChange={(e) => setEmployeeId(e.target.value)}
            autoFocus
          />
          {error && <div className="login-error">{error}</div>}
          <button className="login-btn" type="submit" disabled={loading}>
            {loading ? '登录中...' : '登录'}
          </button>
        </form>
      </div>
    </div>
  )
}
