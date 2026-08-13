import { useState } from 'react'
import { Button, TextInput } from '@mantine/core'
import { ArrowRight, Eye, FileSearch, ShieldCheck } from 'lucide-react'
import { Navigate, useNavigate } from 'react-router-dom'
import { notifications } from '@mantine/notifications'
import { useSession } from '../App'
import { apiErrorMessage, login } from '../api'

export default function LoginPage() {
  const { user, signIn } = useSession()
  const navigate = useNavigate()
  const [employeeId, setEmployeeId] = useState('')
  const [loading, setLoading] = useState(false)
  if (user) return <Navigate to="/" replace />

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!employeeId.trim()) return
    setLoading(true)
    try { const result = await login(employeeId.trim()); signIn(result.user); navigate('/') }
    catch (error) { notifications.show({ color: 'red', title: '无法登录', message: apiErrorMessage(error, '请检查工号后重试。') }) }
    finally { setLoading(false) }
  }

  return <main className="login-shell">
    <section className="login-story">
      <div className="login-brand"><svg className="brand-sigil" viewBox="0 0 48 48" fill="none" aria-hidden="true"><path d="M4 14 Q24 6 44 14 v26 Q24 34 24 38 Q24 34 4 42Z" stroke="#fff" strokeWidth="2.2" fill="var(--evidence)" fillOpacity=".15" strokeLinejoin="round" /><path d="M24 14 v24" stroke="#fff" strokeWidth="1.5" opacity=".3" /><path d="M17 27 l2.5 2.5 5.5-6" stroke="var(--evidence)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg><b>EMC 报告审核系统</b></div>
      <div className="login-thesis"><span>QUALITY / TRACEABILITY / DECISION</span><h1>每一个结论，<br />都回到原文。</h1><p>在四份资料之间建立可追溯证据链。机器负责发现矛盾，审核员掌握最终判断。</p></div>
      <div className="login-proof">
        <div><FileSearch /><span><b>四文档交叉核验</b><small>委托、计划、记录、报告互为证据</small></span></div>
        <div><Eye /><span><b>原图精确高亮</b><small>问题定位到页码、表格和原文坐标</small></span></div>
        <div><ShieldCheck /><span><b>人工结论留痕</b><small>处理依据、版本和操作者完整保存</small></span></div>
      </div>
    </section>
    <section className="login-panel">
      <form onSubmit={submit}>
        <div className="eyebrow">SECURE ACCESS</div><h2>进入审核环境</h2><p>使用已分配角色的员工工号登录。</p>
        <TextInput label="员工工号" placeholder="例如：R-0248" size="lg" required value={employeeId} onChange={event => setEmployeeId(event.currentTarget.value)} autoFocus />
        <Button type="submit" size="lg" fullWidth loading={loading} disabled={!employeeId.trim()} rightSection={<ArrowRight size={17} />}>登录</Button>
        <small className="login-note">登录后只显示当前审核数据库中的真实任务与结果。</small>
      </form>
      <footer>EMC REPORT REVIEW</footer>
    </section>
  </main>
}
