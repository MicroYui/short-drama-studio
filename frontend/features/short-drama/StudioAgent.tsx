import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ArrowUp, Check, ChevronDown, Download, Loader2, Pause, Play, Sparkles, X } from 'lucide-react'
import type { StudioEvent, StudioJob, StudioScope, StudioWorker } from './studio-api'
import { StudioApi } from './studio-api'
import { StudioTeam } from './StudioTeam'
import { StudioGenerationDraft } from './StudioGenerationDraft'
import { StudioMediaTasks } from './StudioMediaTasks'

const statusLabels: Record<StudioJob['status'], string> = { queued: '准备开始', running: '正在制作', retrying: '正在恢复连接', pausing: '完成当前步骤后暂停', paused: '已暂停', waiting_user: '等待你的补充', completed: '已完成', failed: '需要处理后继续' }


function tokenLabel(value: number): string { return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)}k` }

function textField(event: StudioEvent, key: string): string { const value = event.data[key]; return typeof value === 'string' ? value : '' }

function latestTaskEvents(events: StudioEvent[]): StudioEvent[] {
  const tasks = new Map<string, StudioEvent>()
  for (const event of events) {
    if (!['agent.action', 'tool.started', 'tool.finished'].includes(event.kind)) continue
    const key = event.kind === 'agent.action' ? `action:${textField(event, 'id') || event.id}` : `tool:${textField(event, 'tool_call_id') || `${textField(event, 'work_order_id')}:${textField(event, 'tool_name')}`}`
    tasks.delete(key)
    tasks.set(key, event)
  }
  return [...tasks.values()]
}

function taskStatus(event: StudioEvent, active: boolean): string {
  const state = textField(event, 'status')
  if (['SUCCEEDED', 'COMPLETED'].includes(state)) return '已完成'
  if (state === 'FAILED') return '未完成'
  if (state === 'CANCELLED') return '已调整'
  return active ? '执行中' : '未记录完成结果'
}
function ChatText({ text, projectId, onError }: { text: string; projectId: string; onError: (error: unknown) => void }) {
  const parts: ReactNode[] = []
  let end = 0
  for (const match of text.matchAll(/\[([^\]]+)\]\(([^)\n]+)\)|\*\*([^*]+)\*\*/g)) {
    parts.push(text.slice(end, match.index))
    if (match[3]) parts.push(<strong key={match.index}>{match[3]}</strong>)
    else {
      const href = match[2].replace(/^<|>$/g, '')
      parts.push(href.startsWith('/') ? <button key={match.index} className="sd-chat-link" onClick={() => void StudioApi.downloadMedia(projectId, href, href.split('/').at(-1) || '制作文件').catch(onError)}>{match[1]} <Download size={12} /></button> : /^https?:\/\//.test(href) ? <a key={match.index} className="sd-chat-link" href={href} target="_blank" rel="noreferrer">{match[1]}</a> : match[0])
    }
    end = (match.index ?? 0) + match[0].length
  }
  parts.push(text.slice(end))
  return <p>{parts}</p>
}
function actionLabel(event: StudioEvent): string {
  const summary = textField(event, 'input_summary') || textField(event, 'output_summary')
  if (summary) return summary
  const name = textField(event, 'tool_name')
  const labels: Record<string, string> = { 'timeline.read_editorial': '查看本集剪辑', 'timeline.read_export': '检查已导出的完整影片', 'timeline.edit_clips': '调整指定片段与关联声音', 'timeline.export_editorial': '导出完整成片与字幕', 'timeline.rebuild': '组接本集镜头', 'timeline.export': '导出成片', 'task.run_workflow': '执行高级工作流', 'task.read_workflow': '查看高级工作流' }
  if (labels[name]) return labels[name]
  if (name === 'agent.read_context') return '读取已保存的上下文'
  if (name.startsWith('project.')) return '读取和整理项目内容'
  const categories: Record<string, string> = { script: '编写和检查剧本', storyboard: '设计镜头内容', shot: '更新镜头设计', asset: '整理项目资产', image: '制作图片素材', video: '制作视频片段', audio: '制作声音', qa: '检查制作结果', route: '安排镜头制作', artifact: '查看已有内容', agent: '安排制作任务', task: '处理当前制作任务', timeline: '调整本集剪辑' }
  return categories[name.split('.')[0]] || '梳理当前目标与制作结果'
}

export function StudioAgent({ pendingChanges = 0, onReview, unsavedEdit = false, unsavedDesign = false, projectId, events, jobs, workers = [], contextWindow = 258000, scope, setScope, prepareScope, connected, suggested, clearSuggested, onError, onSent, onClose }: {
  pendingChanges?: number; onReview: () => void;
  unsavedEdit?: boolean; unsavedDesign?: boolean; projectId: string; events: StudioEvent[]; jobs: StudioJob[]; scope: StudioScope; setScope: (scope: StudioScope) => void;
  connected: boolean; suggested: string | null; clearSuggested: () => void; onError: (error: unknown) => void; onSent: () => void; onClose: () => void;
  workers?: StudioWorker[]; contextWindow?: number;
  prepareScope?: () => Promise<StudioScope>;
}) {
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const scroll = useRef<HTMLDivElement>(null)
  const nearBottom = useRef(true)
  const job = jobs[0]
  const active = job && ['queued', 'running', 'retrying', 'pausing'].includes(job.status)
  const messages = events.filter(event => event.kind === 'chat.user' || event.kind === 'chat.assistant')
  const jobEvents = job ? events.filter(event => event.data.job_id === job.id || (job.run_id && event.data.run_id === job.run_id)) : []
  const tasks = latestTaskEvents(jobEvents)
  const lastTask = tasks[tasks.length - 1]
  const latestDraftId = [...jobEvents].reverse().find(event => event.kind === 'model.draft')?.id
  const lastModel = [...jobEvents].reverse().find(event => event.kind === 'provider.language_generation')
  const lastContext = [...jobEvents].reverse().find(event => event.kind === 'context.prepared')
  const estimatedTokens = Number(lastContext?.data.estimated_input_tokens ?? 0)
  const liveWorkers = workers.filter(worker => worker.run_id === job?.run_id && !['COMPLETED', 'CANCELLED'].includes(worker.status))
  const runningWorkers = liveWorkers.filter(worker => worker.status === 'IN_PROGRESS' && !worker.waiting_review)
  const waitingWorkers = liveWorkers.filter(worker => worker.status === 'NEEDS_ATTENTION' && worker.waiting_until && !worker.waiting_review)
  const reviewWorkers = liveWorkers.filter(worker => worker.waiting_review)
  const currentAction = job?.status === 'retrying' ? '连接暂时中断，已完成的内容已保存，正在继续同一任务。' : runningWorkers.length > 1 ? `${runningWorkers.length} 项分工正在并行推进` : runningWorkers[0]?.latest_reason || runningWorkers[0]?.objective || (waitingWorkers.length ? `${waitingWorkers.length} 项分工等待生成结果，完成后自动继续` : reviewWorkers.length ? `${reviewWorkers.length} 项相关分工等待修改审阅` : '') || (lastTask ? actionLabel(lastTask) : '梳理当前目标和已有内容')
  useEffect(() => { if (suggested !== null) { if (suggested) setText(previous => previous.trim() && previous.trim() !== suggested.trim() ? `${previous}\n\n${suggested}` : suggested); textarea.current?.focus(); clearSuggested() } }, [suggested, clearSuggested])
  useEffect(() => { if (nearBottom.current) scroll.current?.scrollTo({ top: scroll.current.scrollHeight, behavior: 'smooth' }) }, [messages.length, active, workers.length, tasks.length, pendingChanges])
  useEffect(() => { if (nearBottom.current) scroll.current?.scrollTo({ top: scroll.current.scrollHeight, behavior: 'auto' }) }, [latestDraftId])
  const send = async () => {
    if (!text.trim() || sending) return
    const submittedText = text
    setSending(true)
    try {
      const selectedScope = prepareScope ? await prepareScope() : scope
      setScope(selectedScope)
      await StudioApi.send(projectId, submittedText.trim(), selectedScope)
      setText(previous => previous === submittedText ? '' : previous)
      onSent()
    } catch (error) { onError(error) } finally { setSending(false) }
  }
  return <aside className="sd-agent-panel" aria-label="制作助手">
    <header className="sd-agent-header"><span className="sd-agent-symbol"><Sparkles size={17} /></span><div><strong>制作助手</strong><span><i className={connected ? 'sd-online' : 'sd-offline'} />{connected ? '已连接' : '正在重新连接'}</span></div><span className="sd-local-tag">Agent</span><button className="sd-icon sd-agent-dismiss" aria-label="收起制作助手" onClick={onClose}><X size={17} /></button></header>
    {unsavedEdit ? <p className="sd-agent-draft-notice">{prepareScope ? '发送时会先保存当前剪辑，再交接所选内容。' : '当前剪辑尚未保存。Agent 使用已保存的剪辑，请先保存再发送针对这些改动的要求。'}</p> : unsavedDesign && <p className="sd-agent-draft-notice">有分镜草稿尚未保存。Agent 使用已保存内容；可在镜头页“保存并交给 Agent”。</p>}
    <div className="sd-agent-scroll" ref={scroll} onScroll={event => { const element = event.currentTarget; nearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 100 }}>
      {!messages.length && <div className="sd-agent-welcome"><span className="sd-agent-welcome-icon"><Sparkles size={22} /></span><h3>我们来做一部作品。</h3><p>告诉我这次想完成什么。你也可以选中某个场次、镜头或资产，让我定向修改。</p><small>要求只写剧本时，我会写完停下。要求成片时，我会继续推进制作。</small></div>}
      {messages.map(message => <div key={message.id} className={`sd-chat-message ${message.kind === 'chat.user' ? 'sd-chat-user' : 'sd-chat-assistant'}`}><div className="sd-chat-label">{message.kind === 'chat.user' ? '你' : '制作助手'}<time>{new Date(message.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</time></div><ChatText text={textField(message, 'text')} projectId={projectId} onError={onError} /></div>)}
      {job && <div className={`sd-job-card sd-job-${job.status}`}><div className="sd-job-heading">{active ? <Loader2 size={16} className="sd-spin" /> : job.status === 'completed' ? <Check size={16} /> : <span className="sd-status-dot" />}<strong>{job.status === 'waiting_user' && pendingChanges > 0 ? '等待审阅修改' : statusLabels[job.status]}</strong></div><p>{active ? currentAction.split('\n')[0].slice(0, 145) : job.error || job.current_goal || job.objective}</p>
        {(job.pending_messages ?? []).length > 0 && <small>已收到你的新要求，当前步骤结束后处理。</small>}
        <StudioMediaTasks events={jobEvents} />
        <StudioTeam workers={workers} events={jobEvents} job={job} />
        <details className="sd-task-detail"><summary>制作任务 <ChevronDown size={13} /></summary>{tasks.slice(-9).map(event => <div key={event.id}><span className={textField(event, 'status') === 'FAILED' ? 'sd-danger-dot' : 'sd-task-dot'} /><p>{actionLabel(event)}<small>{taskStatus(event, Boolean(active))}</small></p></div>)}
          <section className="sd-context-detail" aria-label="上下文管理"><div><span>上下文上限</span><strong>{tokenLabel(contextWindow)} tokens</strong></div>{lastContext && <><div><span>当前请求估算</span><strong>{tokenLabel(estimatedTokens)}</strong></div><progress value={estimatedTokens} max={contextWindow} aria-label="当前请求的上下文用量" /><small>另预留 {tokenLabel(Number(lastContext.data.reserved_output_tokens ?? 0))} 用于输出与推理</small>{Number(lastContext.data.offloaded_blocks ?? 0) > 0 && <p>已整理上下文：{tokenLabel(Number(lastContext.data.original_input_tokens ?? 0))} → {tokenLabel(estimatedTokens)}。完整结果已保存，可按需读取。</p>}</>}<small>接近 200k 时整理；实际用量见模型记录。</small></section>
          {lastModel && <div className="sd-model-detail"><p>{textField(lastModel, 'model')} · {textField(lastModel, 'reasoning_effort')}</p><small>输入 {String(lastModel.data.input_tokens ?? '未返回')} tokens · 输出 {String(lastModel.data.output_tokens ?? '未返回')} tokens · {Number(lastModel.data.elapsed_seconds ?? 0).toFixed(1)} 秒</small></div>}
        </details>
        <StudioGenerationDraft events={jobEvents} job={job} />
        <div className="sd-job-controls">{active ? <button onClick={() => void StudioApi.control(job.id, 'pause').then(onSent).catch(onError)} disabled={job.status === 'pausing'}><Pause size={13} />暂停</button> : job.status !== 'completed' ? <button onClick={() => void StudioApi.control(job.id, 'resume').then(onSent).catch(onError)}><Play size={13} />继续</button> : null}<button onClick={() => void StudioApi.downloadTrace(projectId).catch(onError)}><Download size={13} />制作记录</button></div>
      </div>}
      {pendingChanges > 0 && <button className="sd-review-callout" onClick={onReview}><span><strong>{pendingChanges} 处修改等待审阅</strong><small>查看前后对照，逐项接受</small></span><ChevronDown size={17} /></button>}
    </div>
    <div className="sd-agent-compose"><div className="sd-scope"><span>作用范围</span><strong>{prepareScope ? '剪辑中的当前选择' : scope.label}</strong>{!prepareScope && scope.kind !== 'episode' && <button className="sd-icon" aria-label="回到当前集范围" onClick={() => setScope({ kind: 'episode', label: '当前集' })}><X size={12} /></button>}</div><textarea ref={textarea} aria-label="给制作助手发送要求" placeholder={active ? '补充要求，或指出需要修改的地方…' : '描述你的想法，或者想修改什么…'} value={text} onChange={event => setText(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); void send() } }} /><div className="sd-compose-footer"><span>⌘ Enter 发送</span><button className="sd-send" aria-label="发送给 Agent" disabled={!text.trim() || sending} onClick={() => void send()}>{sending ? <Loader2 size={16} className="sd-spin" /> : <ArrowUp size={17} />}</button></div></div>
    <footer className="sd-agent-footer">内容保存在本机 · 可随时编辑</footer>
  </aside>
}
