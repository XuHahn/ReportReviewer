import { useState, FormEvent } from 'react'
import { login } from '../api'
import type { User } from '../types'

interface Props {
  onLogin: (user: User) => void
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
      if (res.user.role !== 'admin') {
        setError('仅限管理员登录后台管理系统')
        return
      }
      onLogin(res.user)
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message || '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1>EMC检测报告智能审核系统</h1>
        <p className="login-subtitle">后台管理 · 请使用管理员工号登录</p>
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
