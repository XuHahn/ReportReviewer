import { useState, useCallback, useEffect } from 'react'
import ReportUploader from './components/ReportUploader'
import ReportReviewer from './components/ReportReviewer'
import ReportHistory from './components/ReportHistory'
import BatchReviewer from './components/BatchReviewer'
import StatsDashboard from './components/StatsDashboard'
import ReportComparison from './components/ReportComparison'
import RulesManager from './components/RulesManager'
import StandardsBrowser from './components/StandardsBrowser'
import ProjectGroups from './components/ProjectGroups'
import LoginPage from './components/LoginPage'
import ErrorBoundary from './components/ErrorBoundary'
import { getCurrentUser, getStoredToken, clearStoredToken } from './api'
import type { UploadResponse, BatchFileResult, User } from './types'

type View = 'upload' | 'config' | 'standards' | 'groups' | 'dashboard' | 'my_reports'

const ICONS: Record<View, JSX.Element> = {
  upload: <svg viewBox="0 0 24 24" width="20" height="20"><rect x="4" y="2" width="16" height="16" rx="2" fill="currentColor" opacity=".15"/><polyline points="16 10 12 6 8 10" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/><line x1="12" y1="6" x2="12" y2="15" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/></svg>,
  config: <svg viewBox="0 0 24 24" width="20" height="20"><circle cx="9" cy="9" r="3" fill="currentColor" opacity=".25"/><circle cx="16" cy="9" r="2.5" fill="currentColor" opacity=".45"/><circle cx="12" cy="16" r="4" fill="currentColor" opacity=".7"/></svg>,
  standards: <svg viewBox="0 0 24 24" width="20" height="20"><rect x="5" y="3" width="14" height="18" rx="2" fill="currentColor" opacity=".12" stroke="currentColor" strokeWidth="1.5"/><line x1="9" y1="8" x2="15" y2="8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/><line x1="9" y1="12" x2="13" y2="12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>,
  groups: <svg viewBox="0 0 24 24" width="20" height="20"><circle cx="8" cy="7" r="3.5" fill="currentColor" opacity=".3"/><circle cx="16" cy="7" r="2.5" fill="currentColor" opacity=".55"/><path d="M2 20v-1a5 5 0 015-5h2.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/><path d="M16 14h2.5a5 5 0 015 5v1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>,
  dashboard: <svg viewBox="0 0 24 24" width="20" height="20"><rect x="3" y="14" width="4" height="7" rx="1" fill="currentColor" opacity=".3"/><rect x="10" y="9" width="4" height="12" rx="1" fill="currentColor" opacity=".55"/><rect x="17" y="4" width="4" height="17" rx="1" fill="currentColor"/></svg>,
  my_reports: <svg viewBox="0 0 24 24" width="20" height="20"><path d="M5 4h14a2 2 0 012 2v12a2 2 0 01-2 2H5a2 2 0 01-2-2V6c0-1.1.9-2 2-2z" fill="currentColor" opacity=".12" stroke="currentColor" strokeWidth="1.5"/><polyline points="9 12 11 14 15 10" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></svg>,
}

const TITLES: Record<View, string> = {
  upload: '上传审核', config: '审核配置', standards: '标准库', groups: '项目组', dashboard: '统计仪表盘', my_reports: '我的报告',
}

export default function App() {
  const [currentUser, setCurrentUser] = useState<User | null>(null)
  const [authLoading, setAuthLoading] = useState(true)
  const [activeView, setActiveView] = useState<View>(() => {
    const stored = localStorage.getItem('emc_last_view')
    return (stored as View) || 'upload'
  })
  const [result, setResult] = useState<UploadResponse | null>(null)
  const [batchResults, setBatchResults] = useState<BatchFileResult[] | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [compareIds, setCompareIds] = useState<string[]>([])
  const [appToast, setAppToast] = useState<string | null>(null)

  const showAppToast = (msg: string) => { setAppToast(msg); setTimeout(() => setAppToast(null), 3000) }

  useEffect(() => {
    function handleForbidden() { showAppToast('权限不足') }
    window.addEventListener('auth:forbidden', handleForbidden)
    return () => window.removeEventListener('auth:forbidden', handleForbidden)
  }, [])

  useEffect(() => {
    const token = getStoredToken()
    if (!token) { setAuthLoading(false); return }
    getCurrentUser().then(setCurrentUser).catch(() => clearStoredToken()).finally(() => setAuthLoading(false))
  }, [])

  useEffect(() => {
    function h() { setCurrentUser(null) }
    window.addEventListener('auth:logout', h)
    return () => window.removeEventListener('auth:logout', h)
  }, [])

  useEffect(() => { localStorage.setItem('emc_last_view', activeView) }, [activeView])

  function handleLogout() { clearStoredToken(); setCurrentUser(null); setResult(null); setBatchResults(null); setCompareIds([]) }

  const handleUploadResult = useCallback((d: UploadResponse) => { setResult(d); setRefreshKey(k => k + 1) }, [])
  const handleBatchResult = useCallback((r: BatchFileResult[]) => { setBatchResults(r); setResult(null); setRefreshKey(k => k + 1) }, [])
  const handleHistorySelect = useCallback((d: UploadResponse) => { setResult(d); setBatchResults(null); setCompareIds([]); setActiveView('upload'); setRefreshKey(k => k + 1) }, [])
  const handleCompare = useCallback((ids: string[]) => { setCompareIds(ids); setResult(null); setBatchResults(null) }, [])

  const role = currentUser?.role
  const roleLabel = role === 'admin' ? '管理员' : role === 'reviewer' ? '审核员' : '查看者'

  const all: { key: View; label: string; roles: string[] }[] = [
    { key: 'upload', label: '上传审核', roles: ['admin', 'reviewer'] },
    { key: 'my_reports', label: '我的报告', roles: ['admin', 'reviewer', 'viewer'] },
    { key: 'dashboard', label: '统计仪表盘', roles: ['admin', 'reviewer', 'viewer'] },
    { key: 'groups', label: '项目组', roles: ['admin', 'reviewer', 'viewer'] },
    { key: 'config', label: '审核配置', roles: ['admin', 'reviewer'] },
    { key: 'standards', label: '标准库', roles: ['admin', 'reviewer'] },
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
          <div style={{ display: activeView === 'upload' ? undefined : 'none' }}>
            <ReportUploader onResult={handleUploadResult} onBatchResult={handleBatchResult} />
            {batchResults && <BatchReviewer results={batchResults} onSelect={handleUploadResult} />}
            {result && (<>{batchResults && <button className="filter-btn" onClick={() => setResult(null)} style={{marginBottom:8}}>← 返回批量结果</button>}<ReportReviewer data={result} /></>)}
          </div>
          <div style={{ display: activeView === 'config' ? undefined : 'none' }}><RulesManager /></div>
          <div style={{ display: activeView === 'standards' ? undefined : 'none' }}><StandardsBrowser /></div>
          <div style={{ display: activeView === 'groups' ? undefined : 'none' }}><ProjectGroups /></div>
          <div style={{ display: activeView === 'dashboard' ? undefined : 'none' }}><StatsDashboard /></div>
          <div style={{ display: activeView === 'my_reports' ? undefined : 'none' }}>
            <ReportHistory onSelect={handleHistorySelect} refreshKey={refreshKey} onCompare={handleCompare} scope="mine" currentUserId={currentUser.employee_id} />
          </div>
          {compareIds.length === 2 && <ReportComparison idA={compareIds[0]} idB={compareIds[1]} onClose={() => setCompareIds([])} />}
          </ErrorBoundary>
        </div>
      </main>
    </div>
  )
}
