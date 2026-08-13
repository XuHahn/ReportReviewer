import { createContext, lazy, useContext, useEffect, useMemo, useState } from 'react'
import { Navigate, RouterProvider, createBrowserRouter, isRouteErrorResponse, useRouteError } from 'react-router-dom'
import { notifications } from '@mantine/notifications'
import { clearStoredToken, createClientTraceId, getCurrentUser, getStoredToken } from './api'
import { demoUser } from './mockData'
import type { User } from './types'
import AppLayout from './components/AppLayout'
import LoginPage from './pages/LoginPage'
import NotFoundPage from './pages/NotFoundPage'
const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const TasksPage = lazy(() => import('./pages/TasksPage'))
const ReviewWorkspace = lazy(() => import('./pages/ReviewWorkspace'))
const StandardsPage = lazy(() => import('./pages/StandardsPage'))
const GroupsPage = lazy(() => import('./pages/GroupsPage'))
const UsersPage = lazy(() => import('./pages/UsersPage'))
const OperationsPage = lazy(() => import('./pages/OperationsPage'))

interface AppSession {
  user: User | null
  demo: boolean
  loading: boolean
  sessionError: string
  retrySession: () => void
  signIn: (user: User, demo?: boolean) => void
  signOut: () => void
}
const SessionContext = createContext<AppSession | null>(null)
export const useSession = () => {
  const value = useContext(SessionContext)
  if (!value) throw new Error('SessionContext is missing')
  return value
}

function RootGate() {
  const { user, loading, sessionError, retrySession } = useSession()
  if (loading) return <div className="boot-screen"><div className="boot-mark">↗</div><p>正在校验报告审核环境…</p></div>
  if (!user && sessionError) return <div className="boot-screen service-error"><div className="boot-mark">!</div><h1>暂时无法连接审核服务</h1><p>{sessionError}。登录状态已保留，服务恢复后可直接重试。</p><button className="primary-button" onClick={retrySession}>重新连接</button></div>
  return user ? <AppLayout /> : <Navigate to="/login" replace />
}

function RoleGate({ roles, children }: { roles: User['role'][]; children: React.ReactNode }) {
  const { user } = useSession()
  return user && roles.includes(user.role) ? children : <Navigate to="/" replace />
}

function RouteErrorPage() {
  const error = useRouteError()
  const notFound = isRouteErrorResponse(error) && error.status === 404
  const [traceId] = useState(() => createClientTraceId('ui'))
  const details = error as { name?: unknown; message?: unknown; stack?: unknown } | null
  const errorName = String(details?.name || (isRouteErrorResponse(error) ? `RouteError${error.status}` : 'UnknownError'))
  const errorMessage = String(details?.message || '页面运行时异常')
  const errorStack = String(details?.stack || '')
  return <NotFoundPage runtimeError={!notFound} traceId={traceId} errorName={errorName} errorMessage={errorMessage} errorStack={errorStack} />
}

const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  {
    path: '/', element: <RootGate />, errorElement: <RouteErrorPage />, children: [
      { index: true, element: <DashboardPage /> },
      { path: 'tasks', element: <TasksPage /> },
      { path: 'tasks/:setId/:stage?', element: <ReviewWorkspace /> },
      { path: 'standards', element: <RoleGate roles={['admin', 'standard_reviewer']}><StandardsPage /></RoleGate> },
      { path: 'groups', element: <RoleGate roles={['admin', 'reviewer']}><GroupsPage /></RoleGate> },
      { path: 'users', element: <RoleGate roles={['admin']}><UsersPage /></RoleGate> },
      { path: 'operations', element: <RoleGate roles={['admin']}><OperationsPage /></RoleGate> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])

export default function App() {
  const queryDemo = new URLSearchParams(window.location.search).get('demo') === '1'
  const [demo, setDemo] = useState(queryDemo)
  const [user, setUser] = useState<User | null>(demo ? demoUser : null)
  const [loading, setLoading] = useState(!demo && Boolean(getStoredToken()))
  const [sessionError, setSessionError] = useState('')
  const [sessionAttempt, setSessionAttempt] = useState(0)

  useEffect(() => {
    if (demo || !getStoredToken()) { setLoading(false); return }
    setLoading(true); setSessionError('')
    getCurrentUser().then(setUser).catch(() => {
      if (getStoredToken()) setSessionError('审核服务未响应')
    }).finally(() => setLoading(false))
  }, [demo, sessionAttempt])

  useEffect(() => {
    const logout = () => { setUser(null); setDemo(false) }
    const forbidden = () => notifications.show({ color: 'red', title: '权限不足', message: '当前账号不能执行这个操作。' })
    window.addEventListener('auth:logout', logout)
    window.addEventListener('auth:forbidden', forbidden)
    return () => { window.removeEventListener('auth:logout', logout); window.removeEventListener('auth:forbidden', forbidden) }
  }, [])

  const session = useMemo<AppSession>(() => ({
    user, demo, loading, sessionError,
    retrySession() { setSessionAttempt(value => value + 1) },
    signIn(nextUser, nextDemo = false) {
      setUser(nextUser); setDemo(nextDemo); setSessionError('')
    },
    signOut() {
      clearStoredToken(); setDemo(false); setUser(null); setSessionError('')
    },
  }), [user, demo, loading, sessionError])

  return <SessionContext.Provider value={session}><RouterProvider router={router} /></SessionContext.Provider>
}
