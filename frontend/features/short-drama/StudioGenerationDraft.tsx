import { Loader2 } from 'lucide-react'
import type { StudioEvent, StudioJob } from './studio-api'

export function StudioGenerationDraft({ events, job }: { events: StudioEvent[]; job: StudioJob }) {
  const drafts = new Map<string, StudioEvent>()
  for (const event of events) if (event.kind === 'model.draft' && event.data.source === 'provider_delta' && typeof event.data.call_id === 'string') drafts.set(event.data.call_id, event)
  const active = ['running', 'queued', 'retrying', 'pausing'].includes(job.status)
  const visible = [...drafts.values()].filter(draft => !events.some(event => event.id > draft.id &&
    ((event.kind === 'model.started' && event.data.work_order_id === draft.data.work_order_id) ||
      (event.data.work_order_id === draft.data.work_order_id &&
        ((event.kind === 'content.updated' && typeof event.data.work_order_id === 'string') ||
          (event.kind === 'tool.finished' && event.data.status === 'SUCCEEDED' &&
            ['script.save_scene', 'shot.save_current'].includes(String(event.data.tool_name))))))))
  return <>{visible.slice(-4).map(draft => {
    const interrupted = draft.data.status === 'interrupted' || (!active && draft.data.status === 'generating')
    const generating = active && draft.data.status === 'generating'
    return <details className="sd-generation-draft" key={String(draft.data.call_id)} open={generating}>
      <summary>{generating && <Loader2 size={12} className="sd-spin" />}<strong>{interrupted ? '生成中断的草稿' : generating ? '生成中草稿' : '已收到的生成草稿'}</strong><span>尚未写入正文</span></summary>
      <p>{typeof draft.data.text === 'string' ? draft.data.text : ''}</p>
      <small>供应商实际返回的文字。完整内容解析并保存后，将显示在对应剧本或镜头中。</small>
    </details>
  })}</>
}
