import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Tabs, TextInput } from '@mantine/core'
import { Activity, Cpu, Database, Plus, RefreshCw, Save, Search, Server, Tag, Trash2, X } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { notifications } from '@mantine/notifications'
import { apiErrorMessage, createTag, deleteTag, getHealth, getSystemLogs, getSystemSettings, getTags, updateSystemSettings } from '../api'
import { PageHeader, StatusPill, formatDate } from '../components/common'
import type { HealthStatus } from '../types'

const LOG_CONTEXT_KEYS = ['graph_id', 'finding_id', 'check_id', 'evidence_id', 'set_id', 'doc_type', 'page_number', 'highlight_strategy', 'focus_applied', 'status', 'durationMs', 'error_type']
function logContext(log: Record<string, unknown>) {
  return LOG_CONTEXT_KEYS.flatMap(key => log[key] == null || log[key] === '' ? [] : [`${key}=${String(log[key])}`]).join(' · ')
}

export default function OperationsPage() {
  const queryClient = useQueryClient(); const [settings, setSettings] = useState<Record<string, string>>({}); const [newTag, setNewTag] = useState('')
  const [searchParams, setSearchParams] = useSearchParams()
  const activeTab = searchParams.get('tab') || 'logs'
  const activeLogKeyword = searchParams.get('keyword') || ''
  const activeLogSource = searchParams.get('source') || ''
  const [logKeyword, setLogKeyword] = useState(activeLogKeyword)
  const healthQuery = useQuery({ queryKey: ['health'], queryFn: (): Promise<HealthStatus> => getHealth() })
  const settingsQuery = useQuery({ queryKey: ['settings'], queryFn: getSystemSettings })
  const logsQuery = useQuery({ queryKey: ['system-logs', activeLogKeyword, activeLogSource], queryFn: () => getSystemLogs({ limit: 100, ...(activeLogKeyword ? { keyword: activeLogKeyword } : {}), ...(activeLogSource ? { source: activeLogSource } : {}) }) })
  const tagsQuery = useQuery({ queryKey: ['tags'], queryFn: getTags })
  useEffect(() => { if (settingsQuery.data) setSettings(settingsQuery.data.settings) }, [settingsQuery.data])
  useEffect(() => { setLogKeyword(activeLogKeyword) }, [activeLogKeyword])
  function updateRoute(tab: string, keyword = activeLogKeyword) { const next = new URLSearchParams(); next.set('tab', tab); if (tab === 'logs' && keyword) next.set('keyword', keyword); if (tab === 'logs' && activeLogSource) next.set('source', activeLogSource); setSearchParams(next) }
  function applyLogFilter() { updateRoute('logs', logKeyword.trim()) }
  async function saveSettings() { try { await updateSystemSettings(settings); notifications.show({ color: 'green', title: '系统设置已保存', message: '新运行将使用更新后的配置。' }) } catch (error) { notifications.show({ color: 'red', message: apiErrorMessage(error) }) } }
  async function addTag() { if (!newTag.trim()) return; try { await createTag(newTag.trim()); setNewTag(''); queryClient.invalidateQueries({ queryKey: ['tags'] }); notifications.show({ color: 'green', message: '标签已创建' }) } catch (error) { notifications.show({ color: 'red', message: apiErrorMessage(error) }) } }
  const recentErrors = (logsQuery.data?.logs || []).filter(log => String(log.level || '').toLowerCase() === 'error').length
  const visionStatus = healthQuery.data?.vision?.status
  const visionPill = healthQuery.isLoading ? 'pending' : visionStatus === 'ready' ? 'complete' : visionStatus === 'preparing' ? 'warning' : 'error'
  const visionLabel = healthQuery.isLoading ? '正在探测' : visionStatus === 'ready' ? '已就绪' : visionStatus === 'preparing' ? '后台加载中' : '不可用'
  return <div className="page operations-page"><PageHeader eyebrow="SYSTEM OBSERVABILITY" title="系统运维" description="运转无声，记录有痕；排查有据，恢复有方。" actions={<button className="secondary-button" onClick={() => { healthQuery.refetch(); logsQuery.refetch(); settingsQuery.refetch() }}><RefreshCw size={15}/>刷新状态</button>}/>
    <div className="health-strip"><div><span><Server/></span><p><b>API 服务</b><small>{healthQuery.isLoading ? '正在探测' : healthQuery.isSuccess ? `健康探针：${healthQuery.data.status}` : '健康探针失败'}</small></p><StatusPill status={healthQuery.isSuccess ? 'complete' : healthQuery.isLoading ? 'pending' : 'error'}/></div><div><span><Cpu/></span><p><b>视觉模型</b><small>{visionLabel}</small></p><StatusPill status={visionPill}>{visionStatus === 'ready' ? '正常' : visionStatus === 'preparing' ? '准备中' : visionStatus === 'unavailable' ? '不可用' : '待探测'}</StatusPill></div><div><span><Database/></span><p><b>配置存储</b><small>{settingsQuery.isSuccess ? `${Object.keys(settings).length} 项配置可读取` : settingsQuery.isLoading ? '正在读取' : '读取失败'}</small></p><StatusPill status={settingsQuery.isSuccess ? 'complete' : settingsQuery.isLoading ? 'pending' : 'error'}/></div><div><span><Activity/></span><p><b>运行日志</b><small>{logsQuery.isSuccess ? `最近 ${logsQuery.data.logs.length} 条中 ${recentErrors} 条错误` : logsQuery.isLoading ? '正在读取' : '读取失败'}</small></p><StatusPill status={logsQuery.isError || recentErrors ? 'warning' : 'complete'}>{logsQuery.isError ? '不可用' : recentErrors ? '需关注' : '正常'}</StatusPill></div></div>
    <Tabs value={activeTab} onChange={value => updateRoute(value || 'logs')}><Tabs.List><Tabs.Tab value="logs">运行日志</Tabs.Tab><Tabs.Tab value="settings">系统设置</Tabs.Tab><Tabs.Tab value="tags">任务标签</Tabs.Tab></Tabs.List><Tabs.Panel value="logs"><div className="log-filter"><label><Search/><input value={logKeyword} onChange={event => setLogKeyword(event.currentTarget.value)} onKeyDown={event => { if (event.key === 'Enter') applyLogFilter() }} placeholder="输入运行编号、证据编号或请求 ID"/></label><button onClick={applyLogFilter}>筛选日志</button>{activeLogKeyword && <button className="clear" onClick={() => { setLogKeyword(''); updateRoute('logs', '') }}><X/>清除</button>}<span>{logsQuery.isFetching ? '正在检索相关日志…' : activeLogKeyword ? `已定位 ${logsQuery.data?.total || 0} 条相关日志` : `最近 ${logsQuery.data?.logs.length || 0} 条日志`}{activeLogSource ? ` · 来源：${activeLogSource}` : ''}</span></div><div className="log-table"><div className="log-row head"><span>时间</span><span>级别</span><span>来源</span><span>事件与调试上下文</span><span>请求 ID</span></div>{logsQuery.data?.logs.map((log, index) => { const context = logContext(log); return <div className="log-row" key={index}><span>{formatDate(String(log.timestamp))}</span><StatusPill status={String(log.level).toLowerCase()}>{String(log.level)}</StatusPill><span>{String(log.source || log.module || log.logger || 'backend')}</span><span className="log-event"><b>{String(log.message || log.event || '未命名事件')}</b>{context && <small>{context}</small>}</span><code>{String(log.req_id || log.reqId || log.request_id || '—')}</code></div> })}{logsQuery.isSuccess && !logsQuery.isFetching && !logsQuery.data.logs.length && <div className="log-empty">没有找到包含“{activeLogKeyword}”的日志。可以改用运行编号或证据编号继续搜索。</div>}</div></Tabs.Panel>
      <Tabs.Panel value="settings"><div className="settings-panel"><div className="settings-intro"><h2>运行配置</h2><p>敏感值不会在这里明文展示。修改影响后续任务，不改变已完成的审核快照。</p></div><div className="settings-grid">{Object.entries(settings).map(([key, value]) => <TextInput key={key} label={key} value={value} onChange={event => setSettings(current => ({ ...current, [key]: event.currentTarget.value }))}/>)}</div><button className="primary-button" onClick={saveSettings} disabled={settingsQuery.isLoading || settingsQuery.isError}><Save size={15}/>保存设置</button></div></Tabs.Panel>
      <Tabs.Panel value="tags"><div className="tags-panel"><div className="tag-create"><TextInput value={newTag} onChange={event => setNewTag(event.currentTarget.value)} placeholder="输入新标签名称"/><button className="primary-button" onClick={addTag}><Plus size={15}/>新增标签</button></div><div className="tag-list">{(tagsQuery.data || []).map((tag: any) => <div key={tag.id}><span><Tag/>{tag.name}</span><button aria-label={`删除标签 ${tag.name}`} onClick={async () => { await deleteTag(tag.id); queryClient.invalidateQueries({ queryKey: ['tags'] }) }}><Trash2/></button></div>)}</div></div></Tabs.Panel></Tabs>
  </div>
}
