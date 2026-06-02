type Page = 'dashboard' | 'all_reports' | 'users' | 'groups' | 'logs' | 'view_logs' | 'settings'

interface Props { active: Page; onNavigate: (p: Page) => void; userName: string; userRole: string; onLogout: () => void }

const ICONS: Record<Page, JSX.Element> = {
  dashboard: <svg viewBox="0 0 24 24" width="20" height="20"><rect x="3" y="3" width="7" height="7" rx="1.5" fill="currentColor" opacity=".25"/><rect x="14" y="3" width="7" height="7" rx="1.5" fill="currentColor" opacity=".5"/><rect x="3" y="14" width="7" height="7" rx="1.5" fill="currentColor" opacity=".5"/><rect x="14" y="14" width="7" height="7" rx="1.5" fill="currentColor"/></svg>,
  all_reports: <svg viewBox="0 0 24 24" width="20" height="20"><path d="M5 4h14a2 2 0 012 2v12a2 2 0 01-2 2H5a2 2 0 01-2-2V6c0-1.1.9-2 2-2z" fill="currentColor" opacity=".12" stroke="currentColor" strokeWidth="1.5"/><polyline points="9 12 11 14 15 10" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  users: <svg viewBox="0 0 24 24" width="20" height="20"><circle cx="8" cy="7" r="3.5" fill="currentColor" opacity=".3"/><circle cx="16" cy="7" r="2.5" fill="currentColor" opacity=".55"/><path d="M2 20v-1a5 5 0 015-5h2.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/><path d="M16 14h2.5a5 5 0 015 5v1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>,
  groups: <svg viewBox="0 0 24 24" width="20" height="20"><rect x="3" y="3" width="7" height="7" rx="1.5" fill="currentColor" opacity=".45"/><rect x="14" y="3" width="7" height="7" rx="1.5" fill="currentColor" opacity=".25"/><rect x="3" y="14" width="7" height="7" rx="1.5" fill="currentColor" opacity=".25"/><rect x="14" y="14" width="7" height="7" rx="1.5" fill="currentColor"/></svg>,
  logs: <svg viewBox="0 0 24 24" width="20" height="20"><rect x="4" y="4" width="16" height="16" rx="2" fill="currentColor" opacity=".1" stroke="currentColor" strokeWidth="1.5"/><polyline points="8 10 12 14 16 9" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  view_logs: <svg viewBox="0 0 24 24" width="20" height="20"><rect x="3" y="5" width="18" height="14" rx="2" fill="currentColor" opacity=".12" stroke="currentColor" strokeWidth="1.5"/><polyline points="8 10 12 13 16 9" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/><line x1="8" y1="17" x2="16" y2="17" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>,
  settings: <svg viewBox="0 0 24 24" width="20" height="20"><circle cx="9" cy="9" r="3" fill="currentColor" opacity=".25"/><circle cx="16" cy="9" r="2.5" fill="currentColor" opacity=".45"/><circle cx="12" cy="16" r="4" fill="currentColor" opacity=".7"/></svg>,
}

const LABELS: Record<Page, string> = { all_reports: '全部报告', dashboard: '仪表盘', users: '用户管理', groups: '项目组', logs: '审计日志', view_logs: '日志查看器', settings: '系统配置' }

export default function Sidebar({ active, onNavigate, userName, userRole, onLogout }: Props) {
  return (
    <aside className="app-side">
      <div className="side-logo">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="9" x2="15" y2="9"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="12" y2="17"/></svg>
        <span className="side-logo-text">后台管理</span>
      </div>
      <nav className="side-nav">
        {Object.entries(LABELS).map(([k, v]) => (
          <button key={k} className={`side-item ${active === k ? 'side-item-on' : ''}`} onClick={() => onNavigate(k as Page)} title={v}>
            <span className="side-icon">{ICONS[k as Page]}</span>
            <span className="side-label">{v}</span>
          </button>
        ))}
        <a href="/docs" target="_blank" className="side-item" style={{ textDecoration: 'none' }} title="API文档">
          <span className="side-icon">
            <svg viewBox="0 0 24 24" width="20" height="20"><rect x="4" y="2" width="16" height="16" rx="3" fill="currentColor" opacity=".15"/><circle cx="12" cy="10" r="2" fill="currentColor" opacity=".5"/><path d="M8 18c0-2 2-3 4-3s4 1 4 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
          </span>
          <span className="side-label">API 文档</span>
        </a>
      </nav>
      <div className="side-user">
        <span className="side-avatar">{userName[0]}</span>
        <div className="side-user-info">
          <span className="side-user-name">{userName}</span>
          <span className="side-user-role">{userRole}</span>
        </div>
        <button className="side-logout" onClick={onLogout} title="登出">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
        </button>
      </div>
    </aside>
  )
}
