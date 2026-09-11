import { useEffect, useState } from 'react'
import { BookOpen, Camera, Check, ChevronDown, CircleDot, Clapperboard, Layers3, Loader2, PenLine, ScanEye } from 'lucide-react'
import type { StudioEvent, StudioJob, StudioWorker } from './studio-api'

const roles: Record<string, { label: string; Icon: typeof PenLine }> = {
  WRITER: { label: '编剧', Icon: PenLine }, CONCEPT: { label: '故事策划', Icon: PenLine },
  DIRECTOR: { label: '导演', Icon: Camera }, PLANNER: { label: '制作规划', Icon: Clapperboard },
  ASSET_PLANNER: { label: '美术设计', Icon: Layers3 }, ASSET_BUILDER: { label: '资产制作', Icon: Layers3 },
  ASSET_REVIEWER: { label: '美术', Icon: Layers3 }, ASSET_VERIFIER: { label: '素材检查', Icon: ScanEye },
  REVISION_VERIFIER: { label: '内容检查', Icon: ScanEye }, CRITIC: { label: '内容检查', Icon: ScanEye },
  VIDEO_PROMPT_COMPILER: { label: '影像制作', Icon: Clapperboard }, EPISODE_REVIEWER: { label: '成片检查', Icon: ScanEye },
  ROUTE_PLANNER: { label: '生成调度', Icon: Clapperboard },
}

const toolLabels: Record<string, string> = {
  'artifact.read_current': '读取当前正文', 'script.save_scene': '写入完整场次', 'script.plan_scenes': '安排本集场次',
  'change.propose_text': '准备修改对照', 'change.apply': '更新相关内容', 'change.preview': '判断修改范围',
  'shot.begin_storyboard_draft': '开始设计分镜', 'shot.append_storyboard_ledger_segment': '安排台词与动作',
  'shot.complete_storyboard_ledger': '整理镜头时长', 'shot.append_storyboard_outline_segment': '安排镜头组',
  'shot.append_storyboard_group': '写入一组分镜', 'shot.append_storyboard_groups': '写入镜头设计',
  'shot.replace_storyboard_draft_group': '调整分镜细节', 'shot.finalize_storyboard': '汇总本集分镜',
  'shot.get_storyboard': '读取完整分镜',
  'shot.save_current': '保存当前镜头', 'artifact.get_production_document': '读取制作内容',
  'artifact.get_asset_planning_context': '查看人物与场景', 'artifact.create_asset': '建立资产',
  'artifact.create_asset_plan': '保存素材安排', 'artifact.patch_asset_plan': '补充素材安排',
  'artifact.get_asset_plan': '读取已保存的素材安排', 'artifact.get_asset_plan_index': '查看素材进度',
  'artifact.get_asset_work_context': '读取本项素材与引用', 'artifact.patch_asset_plan_fields': '更新素材进度',
  'artifact.reconcile_asset_plan_execution_contract': '调整素材依赖', 'agent.get_tool_attempt': '查看上次操作结果',
  'image.enqueue': '提交图片生成', 'image.advance': '生成参考图片', 'image.compose_reference_sheet': '整理多视图',
  'image.compose_text_plate': '排版画面文字', 'project.bind_output_slot': '保存制作结果',
  'image.capability': '查看图片生成能力', 'image.list': '查看图片任务',
  'qa.analyze_source_image': '观察参考图', 'qa.inspect_asset_version': '检查实际图片',
  'qa.list_visual_input_reports': '查看图片观察记录', 'artifact.review_version': '采用参考素材',
  'qa.check_input_compatibility': '核对图片与镜头用途', 'qa.rerank_reference_visuals': '比较可用参考图',
  'qa.compare_keyframes': '比较镜头起止画面',
  'artifact.update_brief': '保存素材设定', 'artifact.create_reference_pack': '整理素材引用',
  'route.compile_h3_prompt': '编译镜头输入', 'route.prepare_h3': '准备视频生成',
  'route.compile_generation_plan': '整理视频生成组', 'route.get_generation_plan': '读取视频生成组',
  'route.get_generation_unit_context': '读取本组镜头与实际素材',
  'route.get_h3_plan': '读取已保存的视频路线', 'route.list_h3_plans': '查看已有视频路线',
  'route.list_h3_runs': '查看已有视频任务', 'route.get_h3_run': '读取视频生成记录',
  'route.preview_h3_probe': '核对视频输入', 'video.select_take': '采用镜头视频',
  'video.inspect_take': '观察实际视频', 'video.get_take_inspection': '读取视频观察结果',
  'route.plan_h3': '选择视频输入与生成方式', 'route.execute_h3_plan': '提交真实视频生成',
  'task.advance': '推进生成任务', 'task.get': '查看生成进度',
  'route.h3_capability_profile': '查看视频生成能力',
  'timeline.rebuild': '组接本集镜头', 'timeline.export_editorial': '导出完整作品',
}
const labelTool = (name: string) => toolLabels[name] || name
const stringField = (event: StudioEvent, key: string) => typeof event.data[key] === 'string' ? String(event.data[key]) : ''

export function StudioTeam({ workers, events, job }: { workers: StudioWorker[]; events: StudioEvent[]; job: StudioJob }) {
  const [expanded, setExpanded] = useState<string | null>(null)
  const [now, setNow] = useState(Date.now())
  const active = ['queued', 'running', 'retrying', 'pausing'].includes(job.status)
  useEffect(() => { if (!active) return; const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer) }, [active])
  const current = workers.filter(worker => worker.run_id === job.run_id && worker.status !== 'CANCELLED')
  const latestCompletedByRole = new Map<string, StudioWorker>()
  for (const worker of current) if (worker.status === 'COMPLETED') latestCompletedByRole.set(worker.skill_role || worker.role, worker)
  const visible = current
    .filter(worker => worker.status !== 'COMPLETED' || latestCompletedByRole.get(worker.skill_role || worker.role)?.id === worker.id)
    .sort((a, b) => Number(a.status === 'COMPLETED') - Number(b.status === 'COMPLETED'))
  const activeCount = visible.filter(worker => worker.status === 'IN_PROGRESS' && active).length
  const loaded = events.filter(event => event.kind === 'skills.loaded')
  const methods = [...new Set(loaded.map(event => stringField(event, 'summary')).filter(Boolean))]
  return <section className="sd-team" aria-label="Agent 制作团队">
    <div className="sd-team-title"><span>制作团队</span><small>{activeCount > 1 ? `${activeCount} 项并行` : activeCount ? '正在创作' : active ? '安排下一步' : '本次分工'}</small></div>
    <div className="sd-team-leader"><span><Clapperboard size={15} /></span><div><strong>制片负责人</strong><small>{active && !activeCount ? '根据目标安排工作' : active ? '协调分工与当前目标' : '保留本次制作结果'}</small></div>{active && <span className="sd-team-live" />}</div>
    {!!methods.length && <div className="sd-team-methods"><BookOpen size={13} /><span>已加载</span>{methods.slice(-2).map(method => <strong key={method}>{method}</strong>)}</div>}
    <div className="sd-team-workers">{visible.map(worker => {
      const descriptor = roles[worker.skill_role ?? ''] ?? { label: worker.role, Icon: CircleDot }
      const Icon = descriptor.Icon
      const running = active && worker.status === 'IN_PROGRESS'
      const done = worker.status === 'COMPLETED'
      const workerEvents = events.filter(event => event.data.work_order_id === worker.id)
      const toolEvents = workerEvents.filter(event => ['tool.started', 'tool.finished'].includes(event.kind))
      const last = toolEvents.at(-1)
      const deciding = running && worker.next_owner === 'WORKER_DECISION'
      const phaseStart = [...workerEvents].reverse().find(event => event.kind === (deciding ? 'model.started' : 'tool.started'))
      const started = phaseStart && (!last || Date.parse(phaseStart.created_at) >= Date.parse(last.created_at)) ? phaseStart : undefined
      const elapsed = started ? Math.max(0, Math.floor((now - Date.parse(started.created_at)) / 1000)) : 0
      const status = done ? '已完成' : job.status === 'paused' ? '已暂停' : running ? '进行中' : worker.next_owner === 'LEADER_REPLAN' ? '等待调整' : active && ['WORKER_DECISION', 'WORKER_TOOL'].includes(worker.next_owner ?? '') ? '已就绪' : '等待继续'
      const completed = done ? [...workerEvents].reverse().find(event => event.kind === 'worker.updated' && stringField(event, 'status') === 'COMPLETED') : undefined
      const detail = completed && stringField(completed, 'summary') || worker.latest_reason || worker.objective
      const skills = workerEvents.filter(event => event.kind === 'skills.loaded')
      return <article className={`sd-team-worker ${running ? 'is-running' : ''} ${done ? 'is-done' : ''}`} key={worker.id}>
        <button className="sd-team-worker-head" aria-expanded={expanded === worker.id} onClick={() => setExpanded(expanded === worker.id ? null : worker.id)}><span className="sd-team-role-icon"><Icon size={16} /></span><strong>{descriptor.label}</strong><small>{running ? <Loader2 size={11} className="sd-spin" /> : done ? <Check size={12} /> : null}{status}</small><ChevronDown size={13} /></button>
        <p className="sd-team-objective" title={worker.objective}>{detail}</p>
        <div className="sd-team-current"><span className={running ? 'sd-team-activity-dot' : ''} /><span>{deciding ? '组织内容与下一步' : labelTool(last ? stringField(last, 'tool_name') : worker.latest_tool || '准备任务')}</span><time>{running && elapsed > 2 ? `${elapsed}s` : `${worker.tool_turns} 次操作`}</time></div>
        {expanded === worker.id && <div className="sd-team-expanded">{skills.length > 0 && <div className="sd-team-skill"><BookOpen size={13} /><span>读取方法：{stringField(skills[skills.length - 1], 'summary')}</span></div>}{toolEvents.filter(event => event.kind === 'tool.finished').slice(-4).map(event => <div className="sd-team-tool" key={event.id}>{stringField(event, 'status') === 'SUCCEEDED' ? <Check size={12} /> : <CircleDot size={12} />}<span>{labelTool(stringField(event, 'tool_name'))}</span></div>)}</div>}
      </article>
    })}</div>
  </section>
}
