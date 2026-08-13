import { Suspense, useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { ActionIcon, Menu, Tooltip, useMantineColorScheme } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { Activity, AlertTriangle, BookOpenCheck, ChevronDown, FileCheck2, LayoutDashboard, LoaderCircle, LogOut, Menu as MenuIcon, Moon, Sun, UserRoundCog, UsersRound, X } from 'lucide-react'
import { getHealth } from '../api'
import { useSession } from '../App'

const navItems = [
  { to: '/', label: '工作概览', icon: LayoutDashboard, roles: ['admin', 'reviewer', 'viewer', 'standard_reviewer'] },
  { to: '/tasks', label: '审核任务', icon: FileCheck2, roles: ['admin', 'reviewer', 'viewer'] },
  { to: '/standards', label: '标准库', icon: BookOpenCheck, roles: ['admin', 'standard_reviewer'] },
  { to: '/groups', label: '项目组', icon: UsersRound, roles: ['admin', 'reviewer'] },
  { to: '/users', label: '用户权限', icon: UserRoundCog, roles: ['admin'] },
  { to: '/operations', label: '系统运维', icon: Activity, roles: ['admin'] },
] as const

const roleLabel = { admin: '管理员', reviewer: '审核员', standard_reviewer: '标准审核员', viewer: '查看者' }

export default function AppLayout() {
  const { user, demo, signOut } = useSession()
  const location = useLocation()
  const navigate = useNavigate()
  const { colorScheme, setColorScheme } = useMantineColorScheme()
  const [mobileOpen, setMobileOpen] = useState(false)
  const healthQuery = useQuery({
    queryKey: ['health', demo],
    queryFn: () => demo ? Promise.resolve({ status: 'demo', vision: { status: 'ready' } }) : getHealth(),
    enabled: Boolean(user),
    refetchOnWindowFocus: true,
    refetchInterval: query => query.state.data?.vision?.status === 'preparing' ? 2000 : 15000,
  })
  useEffect(() => setMobileOpen(false), [location.pathname])
  if (!user) return null

  const visible = navItems.filter(item => item.roles.includes(user.role as never))
  const visionStatus = healthQuery.isError ? 'unavailable' : healthQuery.data?.vision?.status
  return <div className="app-shell">
    <header className="global-header">
      <Link className="brand" to="/" aria-label="EMC 报告审核系统首页">
        <svg className="brand-sigil" viewBox="0 0 48 48" fill="none" aria-hidden="true"><path d="M4 14 Q24 6 44 14 v26 Q24 34 24 38 Q24 34 4 42Z" stroke="currentColor" strokeWidth="2.2" fill="var(--evidence)" fillOpacity=".12" strokeLinejoin="round" /><path d="M24 14 v24" stroke="currentColor" strokeWidth="1.5" opacity=".3" /><path d="M17 27 l2.5 2.5 5.5-6" stroke="var(--evidence)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
        <span><strong>EMC 报告审核系统</strong><small>REPORT REVIEW</small></span>
      </Link>
      <nav className={`global-nav ${mobileOpen ? 'is-open' : ''}`} aria-label="主导航">
        {visible.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} end={to === '/'}>
          <Icon size={16} strokeWidth={1.8} /><span>{label}</span>
        </NavLink>)}
      </nav>
      <div className="header-actions">
        {demo && <button className="demo-badge" onClick={() => { signOut(); navigate('/login', { replace: true }) }}>退出演示预览</button>}
        <Tooltip label={colorScheme === 'dark' ? '切换浅色模式' : '切换深色模式'}>
          <ActionIcon variant="subtle" color="gray" aria-label="切换颜色模式" onClick={() => setColorScheme(colorScheme === 'dark' ? 'light' : 'dark')}>
            {colorScheme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
          </ActionIcon>
        </Tooltip>
        <Menu width={220} position="bottom-end" shadow="md">
          <Menu.Target><button className="user-trigger"><span className="user-avatar">{(user.name || user.employee_id).slice(0, 1)}</span><span><b>{user.name || user.employee_id}</b><small>{roleLabel[user.role]}</small></span><ChevronDown size={14} /></button></Menu.Target>
          <Menu.Dropdown>
            <Menu.Label>{user.employee_id} · {roleLabel[user.role]}</Menu.Label>
            <Menu.Item color="red" leftSection={<LogOut size={15} />} onClick={signOut}>退出登录</Menu.Item>
          </Menu.Dropdown>
        </Menu>
        <ActionIcon className="mobile-menu" variant="subtle" aria-label={mobileOpen ? '关闭导航' : '打开导航'} onClick={() => setMobileOpen(value => !value)}>
          {mobileOpen ? <X /> : <MenuIcon />}
        </ActionIcon>
      </div>
    </header>
    {visionStatus === 'preparing' && <div className="vision-status-banner" role="status"><LoaderCircle className="spin" size={17}/><span><b>视觉模型准备中</b><small>后端已启动，千问视觉模型正在后台加载；页面会自动更新，期间可以继续浏览其他内容。</small></span></div>}
    {visionStatus === 'unavailable' && <div className="vision-status-banner warning" role="alert"><AlertTriangle size={17}/><span><b>视觉模型暂不可用</b><small>需要视觉识别的提取或审核会在模型恢复后再执行，请查看系统运维中的运行日志。</small></span></div>}
    <main className="app-content"><Suspense fallback={<div className="loading-state">正在打开工作区…</div>}><Outlet /></Suspense></main>
  </div>
}
