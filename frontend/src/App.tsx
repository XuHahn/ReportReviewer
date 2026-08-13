import { lazy, Suspense, useState, useEffect } from 'react'
import LoginPage from './components/LoginPage'
import ErrorBoundary from './components/ErrorBoundary'
import DocumentSetUploader from './components/DocumentSetUploader'
import ReviewTaskWorkbench from './components/ReviewTaskWorkbench'
import Icon from './components/Icons'
import { getCurrentUser, getStoredToken, clearStoredToken, createDocumentSetRevision } from './api'
import type { User } from './types'

const StatsDashboard = lazy(() => import('./components/StatsDashboard'))
const StandardsBrowser = lazy(() => import('./components/StandardsBrowser'))
const ProjectGroups = lazy(() => import('./components/ProjectGroups'))
const UserManagement = lazy(() => import('./components/UserManagement'))
const SystemOperations = lazy(() => import('./components/SystemOperations'))

type View = 'workbench' | 'standards' | 'groups' | 'dashboard' | 'users' | 'operations'

const ICONS: Record<View, JSX.Element> = {
  workbench: <svg viewBox="0 0 24 24" width="20" height="20"><rect x="3" y="4" width="18" height="16" rx="3" fill="currentColor" opacity=".14"/><path d="M7 9h10M7 13h6M7 17h4" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/></svg>,
  standards: <svg viewBox="0 0 24 24" width="20" height="20"><rect x="5" y="3" width="14" height="18" rx="2" fill="currentColor" opacity=".12" stroke="currentColor" strokeWidth="1.5"/><line x1="9" y1="8" x2="15" y2="8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/><line x1="9" y1="12" x2="13" y2="12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>,
  groups: <svg viewBox="0 0 24 24" width="20" height="20"><circle cx="8" cy="7" r="3.5" fill="currentColor" opacity=".3"/><circle cx="16" cy="7" r="2.5" fill="currentColor" opacity=".55"/><path d="M2 20v-1a5 5 0 015-5h2.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/><path d="M16 14h2.5a5 5 0 015 5v1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>,
  dashboard: <svg viewBox="0 0 24 24" width="20" height="20"><rect x="3" y="14" width="4" height="7" rx="1" fill="currentColor" opacity=".3"/><rect x="10" y="9" width="4" height="12" rx="1" fill="currentColor" opacity=".55"/><rect x="17" y="4" width="4" height="17" rx="1" fill="currentColor"/></svg>,
  users: <svg viewBox="0 0 24 24" width="20" height="20"><circle cx="9" cy="8" r="3" fill="currentColor" opacity=".25"/><path d="M3 20v-1a6 6 0 0112 0v1M17 8h4M19 6v4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/></svg>,
  operations: <svg viewBox="0 0 24 24" width="20" height="20"><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/><circle cx="12" cy="12" r="4" fill="currentColor" opacity=".2"/></svg>,
}

const TITLES: Record<View, string> = {
  workbench: '审核任务', standards: '标准库', groups: '项目组', dashboard: '运行分析', users: '用户权限', operations: '系统运维',
}

export default function App() {
  const [currentUser, setCurrentUser] = useState<User | null>(null)
  const [authLoading, setAuthLoading] = useState(true)
  const [activeView, setActiveView] = useState<View>(() => {
    const stored = localStorage.getItem('emc_last_view')
    const valid: View[] = ['workbench', 'standards', 'groups', 'dashboard', 'users', 'operations']
    return valid.includes(stored as View) ? stored as View : 'workbench'
  })
  const [revisionTarget, setRevisionTarget] = useState<{ setId: string; nonce: number } | null>(null)
  const [appToast, setAppToast] = useState<string | null>(null)
  const [showTaskEditor, setShowTaskEditor] = useState(false)

  const showAppToast = (msg: string) => { setAppToast(msg); setTimeout(() => setAppToast(null), 3000) }

  useEffect(() => {
    function handleForbidden() { showAppToast('权限不足') }
    window.addEventListener('auth:forbidden', handleForbidden)
    return () => window.removeEventListener('auth:forbidden', handleForbidden)
  }, [])

  useEffect(() => {
    const token = getStoredToken()
    if (!token) { setAuthLoading(false); return }
    getCurrentUser().then((user) => {
      setCurrentUser(user)
      if (user.role === 'standard_reviewer') setActiveView('standards')
    }).catch(() => clearStoredToken()).finally(() => setAuthLoading(false))
  }, [])

  useEffect(() => {
    function h() { setCurrentUser(null) }
    window.addEventListener('auth:logout', h)
    return () => window.removeEventListener('auth:logout', h)
  }, [])

  useEffect(() => { localStorage.setItem('emc_last_view', activeView) }, [activeView])

  function handleLogout() { clearStoredToken(); setCurrentUser(null) }

  async function handleOpenTask(setId: string, status: string) {
    try {
      if (status === 'reviewed') await createDocumentSetRevision(setId)
      setRevisionTarget({ setId, nonce: Date.now() })
      setActiveView('workbench')
      setShowTaskEditor(true)
      showAppToast(status === 'reviewed' ? '已创建修订草稿' : '已打开任务资料')
    } catch (error: any) {
      showAppToast(error?.response?.data?.detail || error.message || '打开任务失败')
    }
  }

  function handleReviewComplete() {
    setRevisionTarget(null)
    setShowTaskEditor(false)
    showAppToast('审核完成，已返回任务工作台')
  }


  const role = currentUser?.role
  const roleLabel = role === 'admin' ? '管理员' : role === 'reviewer' ? '审核员' : role === 'standard_reviewer' ? '标准审核员' : '查看者'

  const all: { key: View; label: string; roles: string[] }[] = [
    { key: 'workbench', label: '审核任务', roles: ['admin', 'reviewer', 'viewer'] },
    { key: 'dashboard', label: '统计仪表盘', roles: ['admin', 'reviewer', 'viewer'] },
    { key: 'groups', label: '项目组', roles: ['admin', 'reviewer'] },
    { key: 'standards', label: '标准库', roles: ['admin', 'standard_reviewer'] },
    { key: 'users', label: '用户权限', roles: ['admin'] },
    { key: 'operations', label: '系统运维', roles: ['admin'] },
  ]
  const tabs = all.filter(t => role && t.roles.includes(role))

  if (authLoading) return <div className="app"><div className="login-page"><div className="login-card"><h1>EMC检测报告智能审核系统</h1><p className="login-subtitle">加载中...</p></div></div></div>
  if (!currentUser) return <div className="app"><LoginPage onLogin={setCurrentUser} /></div>

  return (
    <div className="app-layout">
      <aside className="app-side">
        <div className="side-logo">
          <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
          <span className="side-logo-text">EMC报告审核</span>
        </div>
        <nav className="side-nav">
          {tabs.map(t => (
            <button key={t.key} className={`side-item ${activeView === t.key ? 'side-item-on' : ''}`} onClick={() => setActiveView(t.key)} title={t.label}>
              <span className="side-icon">{ICONS[t.key]}</span>
              <span className="side-label">{t.label}</span>
            </button>
          ))}
        </nav>
        <div className="side-user">
          <span className="side-avatar">{currentUser.name?.[0] || currentUser.employee_id[0]}</span>
          <div className="side-user-info">
            <span className="side-user-name">{currentUser.name || currentUser.employee_id}</span>
            <span className="side-user-role">{roleLabel}</span>
          </div>
          <button className="side-logout" onClick={handleLogout} title="登出">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
          </button>
        </div>
      </aside>
      <main className="app-main">
        {appToast && <div className="notify-toast" role="status" aria-live="polite">{appToast}</div>}
        <div className="main-topbar"><span className="main-title">{TITLES[activeView]}</span></div>
        <div className="main-content">
          <ErrorBoundary>
          {activeView === 'workbench' && (showTaskEditor ? <div>
            <button className="task-back" onClick={() => setShowTaskEditor(false)}><Icon name="arrow-left" size={16} />返回任务工作台</button>
            <DocumentSetUploader revisionTarget={revisionTarget} onReviewComplete={handleReviewComplete} />
          </div> : <ReviewTaskWorkbench
            onCreateTask={() => { setRevisionTarget(null); setShowTaskEditor(true) }}
            onEditTask={handleOpenTask}
          />)}
          <Suspense fallback={<div className="history-loading">加载中...</div>}>
            {activeView === 'standards' && <StandardsBrowser />}
            {activeView === 'groups' && <ProjectGroups />}
            {activeView === 'dashboard' && <StatsDashboard />}
            {activeView === 'users' && <UserManagement />}
            {activeView === 'operations' && <SystemOperations />}
          </Suspense>
          </ErrorBoundary>
        </div>
      </main>
    </div>
  )
}
