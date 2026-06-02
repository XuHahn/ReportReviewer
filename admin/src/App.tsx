import { useState, useEffect } from 'react'
import LoginPage from './components/LoginPage'
import Sidebar from './components/Sidebar'
import Dashboard from './components/Dashboard'
import UserManager from './components/UserManager'
import AuditLogs from './components/AuditLogs'
import SystemSettings from './components/SystemSettings'
import AllReports from './components/AllReports'
import ProjectGroupManager from './components/ProjectGroupManager'
import LogViewer from './components/LogViewer'
import { getCurrentUser, getStoredToken } from './api'
import type { User } from './types'

type Page = 'dashboard' | 'all_reports' | 'users' | 'groups' | 'logs' | 'view_logs' | 'settings'

const TITLES: Record<Page, string> = { dashboard: '仪表盘', all_reports: '全部报告', users: '用户管理', groups: '项目组', logs: '审计日志', view_logs: '日志查看器', settings: '系统配置' }

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [authLoading, setAuthLoading] = useState(true)
  const [page, setPage] = useState<Page>(() => {
    const stored = localStorage.getItem('emc_admin_last_page')
    return (stored as Page) || 'all_reports'
  })

  useEffect(() => { const t = getStoredToken(); if (!t) { setAuthLoading(false); return }; getCurrentUser().then(u => { if (u.role !== 'admin') { localStorage.removeItem('emc_review_token'); return }; setUser(u) }).catch(() => localStorage.removeItem('emc_review_token')).finally(() => setAuthLoading(false)) }, [])
  useEffect(() => { function h() { setUser(null) }; window.addEventListener('auth:logout', h); return () => window.removeEventListener('auth:logout', h) }, [])
  useEffect(() => { localStorage.setItem('emc_admin_last_page', page) }, [page])

  function handleLogout() { localStorage.removeItem('emc_review_token'); setUser(null) }

  if (authLoading) return <div className="login-page"><div className="login-card"><h1>EMC检测报告智能审核系统</h1><p className="login-subtitle">加载中...</p></div></div>
  if (!user) return <LoginPage onLogin={setUser} />

  return (
    <div className="app-layout">
      <Sidebar active={page} onNavigate={setPage} userName={user.name || user.employee_id} userRole="管理员" onLogout={handleLogout} />
      <main className="app-main">
        <div className="main-topbar"><span className="main-title">{TITLES[page]}</span></div>
        <div className="main-content">
          {page === 'dashboard' && <Dashboard />}
          {page === 'all_reports' && <AllReports />}
          {page === 'users' && <UserManager />}
          {page === 'groups' && <ProjectGroupManager />}
          {page === 'logs' && <AuditLogs />}
          {page === 'view_logs' && <LogViewer />}
          {page === 'settings' && <SystemSettings />}
        </div>
      </main>
    </div>
  )
}
