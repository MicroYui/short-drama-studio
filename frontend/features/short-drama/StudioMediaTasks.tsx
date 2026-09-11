import { Clapperboard } from 'lucide-react'
import type { StudioEvent } from './studio-api'

const states: Record<string, string> = {
  QUEUED: '等待提交', SUBMITTING: '正在提交', ACKNOWLEDGED: '供应商已接收', RUNNING: '正在生成',
  SUCCEEDED: '已生成', FAILED: '生成失败', CANCELLED: '已取消',
  SUBMIT_UNKNOWN: '提交结果待核实', OUTPUT_MISSING: '结果待取回',
}
const field = (event: StudioEvent, key: string) => typeof event.data[key] === 'string' ? String(event.data[key]) : ''

export function StudioMediaTasks({ events }: { events: StudioEvent[] }) {
  const byTask = new Map<string, StudioEvent>()
  for (const event of events) {
    if (!['provider.video_generation', 'provider.image_generation'].includes(event.kind)) continue
    const id = field(event, 'task_id')
    if (id) byTask.set(id, event)
  }
  if (!byTask.size) return null
  return <section className="sd-media-tasks" aria-label="实际媒体生成">
    <div className="sd-team-title"><span><Clapperboard size={13} /> 媒体生成</span><small>{byTask.size} 项</small></div>
    {[...byTask.entries()].map(([id, event]) => {
      const video = event.kind === 'provider.video_generation'
      const status = field(event, 'status')
      const error = field(event, 'error_message')
      return <details key={id} className="sd-media-task">
        <summary><span>{field(event, 'agent_role') ? `${field(event, 'agent_role')} · ` : ''}{field(event, 'model') || (video ? '视频生成' : '图片生成')}{video && field(event, 'resolution') ? ` · ${field(event, 'resolution')}` : ''}{video && typeof event.data.duration_seconds === 'number' ? ` · ${event.data.duration_seconds} 秒` : ''}</span><small>{states[status] || status}</small></summary>
        {video && field(event, 'aspect_ratio') && <p>请求画幅：{field(event, 'aspect_ratio')}</p>}
        <p>{video ? '镜头组' : '资产'}：{field(event, 'generation_unit_id') || field(event, 'shot_id') || field(event, 'asset_id')}</p>
        <p>本地任务：{id}</p>
        {field(event, 'provider_task_id') && <p>供应商任务：{field(event, 'provider_task_id')}</p>}
        {error && <p className="sd-team-error">{error}</p>}
        <small>{new Date(event.created_at).toLocaleTimeString('zh-CN')} 更新 · 详细输入与结果见制作记录</small>
      </details>
    })}
  </section>
}
