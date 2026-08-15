import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  Clapperboard,
  Clock3,
  Database,
  Download,
  FileAudio,
  FileText,
  Film,
  FlaskConical,
  FolderOpen,
  Image as ImageIcon,
  Layers3,
  Loader2,
  PanelTop,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Scissors,
  Sparkles,
  Square,
  Subtitles,
  WandSparkles,
  Workflow,
} from 'lucide-react'
import { pathToFileUrl } from '../../lib/file-url'
import {
  ShortDramaApi,
  type ExportRecord,
  type GenerationTask,
  type ProjectAggregate,
  type ProjectRecord,
  type RunMode,
  type ScriptLine,
  type ShotPlan,
  type SourceMode,
  type Take,
  type TimelineManifest,
} from './api'
import { H3LabPanel } from './H3LabPanel'

type StudioTab = 'overview' | 'script' | 'assets' | 'shots' | 'h3' | 'takes' | 'timeline' | 'qa'

type ResultLike<T> =
  | { ok: true; data: T }
  | { ok: false; error: { message: string } }

const primaryButton = 'inline-flex items-center justify-center gap-2 rounded-lg bg-blue-500 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-blue-400 disabled:cursor-not-allowed disabled:opacity-40'
const secondaryButton = 'inline-flex items-center justify-center gap-2 rounded-lg border border-zinc-700 bg-zinc-900 px-3.5 py-2 text-sm font-medium text-zinc-200 transition hover:border-zinc-600 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40'
const subtleButton = 'inline-flex items-center justify-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-zinc-300 transition hover:bg-zinc-800 hover:text-white disabled:cursor-not-allowed disabled:opacity-40'

const stageLabels: Record<string, string> = {
  INPUT: '输入',
  PLANNED: '策划完成',
  GENERATING: '生成中',
  REVIEW: '候选审片',
  TIMELINE_READY: '时间线就绪',
  EXPORTED: '已导出',
}

const statusLabels: Record<string, string> = {
  QUEUED: '排队',
  SUBMITTING: '提交中',
  ACKNOWLEDGED: '已受理',
  RUNNING: '生成中',
  SUCCEEDED: '成功',
  FAILED: '失败',
  CANCELLED: '已取消',
  SUBMIT_UNKNOWN: '提交待对账',
  OUTPUT_MISSING: '产物待恢复',
  CURRENT: '当前',
  STALE: '已失效',
  PASSED: '通过',
  REJECTED: '拒绝',
  PENDING: '待检查',
}

const strategyLabels: Record<string, string> = {
  direct_audiovisual: 'Direct 视听生成',
  image_guided: '图像锚点驱动',
  audio_driven: '音频驱动',
  hybrid_repair: '混合局部修复',
}

function Badge({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: 'neutral' | 'blue' | 'green' | 'amber' | 'red' }) {
  const tones = {
    neutral: 'border-zinc-700 bg-zinc-800/70 text-zinc-300',
    blue: 'border-blue-500/30 bg-blue-500/10 text-blue-300',
    green: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
    amber: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
    red: 'border-red-500/30 bg-red-500/10 text-red-300',
  }
  return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${tones[tone]}`}>{children}</span>
}

function statusTone(status: string): 'neutral' | 'blue' | 'green' | 'amber' | 'red' {
  if (status === 'SUCCEEDED' || status === 'PASSED' || status === 'CURRENT') return 'green'
  if (status === 'FAILED' || status === 'REJECTED') return 'red'
  if (status === 'SUBMIT_UNKNOWN' || status === 'OUTPUT_MISSING' || status === 'STALE') return 'amber'
  if (status === 'RUNNING' || status === 'ACKNOWLEDGED' || status === 'SUBMITTING') return 'blue'
  return 'neutral'
}

function SectionTitle({ title, description, action }: { title: string; description?: string; action?: React.ReactNode }) {
  return (
    <div className="mb-5 flex items-start justify-between gap-4">
      <div>
        <h2 className="text-lg font-semibold text-white">{title}</h2>
        {description && <p className="mt-1 max-w-3xl text-sm leading-6 text-zinc-400">{description}</p>}
      </div>
      {action}
    </div>
  )
}

function Metric({ label, value, detail }: { label: string; value: string | number; detail: string }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/70 p-4">
      <p className="text-xs font-medium uppercase tracking-[0.15em] text-zinc-500">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-white">{value}</p>
      <p className="mt-1 text-xs text-zinc-500">{detail}</p>
    </div>
  )
}

function currentManifest(project: ProjectAggregate): TimelineManifest | null {
  const id = project.document.project.current_manifest_id
  return project.document.manifests.find((manifest) => manifest.id === id && manifest.freshness === 'CURRENT') ?? null
}

function currentExport(project: ProjectAggregate): ExportRecord | null {
  const manifest = currentManifest(project)
  if (!manifest) return null
  return project.exports.find((item) => item.manifest_id === manifest.id) ?? null
}

function selectedTakeMap(project: ProjectAggregate): Map<string, Take> {
  return new Map(
    project.takes
      .filter((take) => take.is_selected && take.freshness === 'CURRENT')
      .map((take) => [take.shot_id, take]),
  )
}

function retriedTaskIds(tasks: GenerationTask[]): Set<string> {
  return new Set(
    tasks
      .map((task) => task.retry_of_task_id)
      .filter((taskId): taskId is string => Boolean(taskId)),
  )
}

function OverviewPanel({ project, onAutoDraft }: { project: ProjectAggregate; onAutoDraft: () => void }) {
  const record = project.document.project
  const progress = project.progress
  const stages = ['PLANNED', 'GENERATING', 'REVIEW', 'TIMELINE_READY', 'EXPORTED']
  const activeIndex = Math.max(0, stages.indexOf(record.stage))
  const superseded = retriedTaskIds(project.tasks)
  const unresolved = project.tasks.filter((task) => (
    ['SUBMIT_UNKNOWN', 'OUTPUT_MISSING', 'FAILED'].includes(task.status)
    && !superseded.has(task.id)
  ))
  return (
    <div className="space-y-7">
      <section className="overflow-hidden rounded-2xl border border-blue-500/20 bg-gradient-to-br from-blue-500/10 via-zinc-950 to-cyan-500/5 p-6">
        <div className="flex flex-col gap-6 xl:flex-row xl:items-center xl:justify-between">
          <div className="max-w-3xl">
            <div className="mb-3 flex items-center gap-2">
              <Badge tone="blue">Phase 2 · Mock Core + H3</Badge>
              <Badge>{record.source_mode}</Badge>
              <Badge>{record.run_mode}</Badge>
            </div>
            <h2 className="text-2xl font-semibold text-white">从策划工件到可播放成片的一键闭环</h2>
            <p className="mt-3 text-sm leading-6 text-zinc-300">
              Mock 继续提供无付费完整闭环；MiniMax-H3 768P 已作为唯一真实视频 Provider 接入同一任务、Take 与时间线核心。真实生成只在 H3 实验台显式提交，下载后必须人工审片才可入选。
            </p>
          </div>
          <button className={`${primaryButton} min-w-52 py-3`} onClick={onAutoDraft}>
            <WandSparkles className="h-4 w-4" />
            生成完整 Mock 草稿
          </button>
        </div>
      </section>

      <section>
        <SectionTitle title="制片进度" description="阶段来自同一 SQLite 项目状态，不是另一套 workflow 画布。" />
        <div className="grid grid-cols-5 gap-2">
          {stages.map((stage, index) => {
            const completed = index <= activeIndex
            return (
              <div key={stage} className="relative">
                <div className={`h-1.5 rounded-full ${completed ? 'bg-blue-500' : 'bg-zinc-800'}`} />
                <div className="mt-2 flex items-center gap-2">
                  {completed ? <CheckCircle2 className="h-4 w-4 text-blue-400" /> : <CircleDot className="h-4 w-4 text-zinc-700" />}
                  <span className={`text-xs ${completed ? 'text-zinc-200' : 'text-zinc-600'}`}>{stageLabels[stage]}</span>
                </div>
              </div>
            )
          })}
        </div>
      </section>

      <section className="grid grid-cols-2 gap-3 xl:grid-cols-5">
        <Metric label="镜头" value={`${progress.shots_with_selected_take}/${progress.total_shots}`} detail="已有入选 Take" />
        <Metric label="任务" value={`${progress.terminal_tasks}/${progress.total_tasks}`} detail="已进入终态" />
        <Metric label="候选" value={progress.passed_takes} detail="当前 QA 通过" />
        <Metric label="时间线" value={progress.manifest_ready ? 'Ready' : 'Pending'} detail="Manifest 唯一成片时钟" />
        <Metric label="交付" value={progress.export_ready ? 'Ready' : 'Pending'} detail="MP4 / SRT / ASS / JSON" />
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.35fr_1fr]">
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <h3 className="font-medium text-white">执行路线短名单</h3>
          <p className="mt-1 text-xs text-zinc-500">同一 ShotPlan 可表达四条路线；本阶段只验证 contract，不做质量排名。</p>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {Array.from(new Set(project.document.execution_plan.map((entry) => entry.strategy))).map((strategy) => (
              <div key={strategy} className="rounded-lg border border-zinc-800 bg-zinc-950/70 p-3">
                <div className="flex items-center gap-2 text-sm font-medium text-zinc-200">
                  <Workflow className="h-4 w-4 text-blue-400" />
                  {strategyLabels[strategy] ?? strategy}
                </div>
                <p className="mt-2 text-xs leading-5 text-zinc-500">供应商中立意图由 Adapter 编译；可选环节关闭时主流程仍成立。</p>
              </div>
            ))}
          </div>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center justify-between">
            <h3 className="font-medium text-white">需要处理</h3>
            <Badge tone={unresolved.length ? 'amber' : 'green'}>{unresolved.length}</Badge>
          </div>
          {unresolved.length === 0 ? (
            <div className="mt-6 flex flex-col items-center py-6 text-center">
              <CheckCircle2 className="h-8 w-8 text-emerald-400" />
              <p className="mt-3 text-sm text-zinc-300">当前没有阻断项</p>
              <p className="mt-1 text-xs text-zinc-500">历史任务仍保留，可在候选页审阅。</p>
            </div>
          ) : (
            <div className="mt-4 space-y-2">
              {unresolved.slice(0, 5).map((task) => (
                <div key={task.id} className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs text-zinc-300">{task.shot_id.split('-').slice(-2).join('-')} · Take {task.candidate_index}</span>
                    <Badge tone={statusTone(task.status)}>{statusLabels[task.status]}</Badge>
                  </div>
                  <p className="mt-1 text-xs text-zinc-500">{task.error_message ?? '等待下一状态'}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  )
}

function ScriptLineEditor({ line, onSave }: { line: ScriptLine; onSave: (line: ScriptLine, spoken: string, subtitle: string) => void }) {
  const [spoken, setSpoken] = useState(line.spoken_text)
  const [subtitle, setSubtitle] = useState(line.subtitle_text)
  useEffect(() => {
    setSpoken(line.spoken_text)
    setSubtitle(line.subtitle_text)
  }, [line])
  const changed = spoken !== line.spoken_text || subtitle !== line.subtitle_text
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Badge tone="blue">{line.speaker}</Badge>
          <span className="text-xs text-zinc-600">{line.id.split('-').slice(-2).join('-')}</span>
        </div>
        <Badge>{line.audio_kind}</Badge>
      </div>
      <div className="grid gap-3 xl:grid-cols-3">
        <label className="block">
          <span className="mb-1.5 block text-xs text-zinc-500">original_text · 永不静默覆盖</span>
          <div className="min-h-20 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm leading-6 text-zinc-400">{line.original_text}</div>
        </label>
        <label className="block">
          <span className="mb-1.5 block text-xs text-zinc-500">spoken_text</span>
          <textarea value={spoken} onChange={(event) => setSpoken(event.target.value)} className="min-h-20 w-full resize-none rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm leading-6 text-zinc-200 outline-none focus:border-blue-500" />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-xs text-zinc-500">subtitle_text</span>
          <textarea value={subtitle} onChange={(event) => setSubtitle(event.target.value)} className="min-h-20 w-full resize-none rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm leading-6 text-zinc-200 outline-none focus:border-blue-500" />
        </label>
      </div>
      {changed && (
        <div className="mt-3 flex justify-end">
          <button className={secondaryButton} onClick={() => onSave(line, spoken.trim(), subtitle.trim())} disabled={!spoken.trim() || !subtitle.trim()}>
            保存为新 ScriptVersion
          </button>
        </div>
      )}
    </div>
  )
}

function ScriptPanel({ project, onRevise }: { project: ProjectAggregate; onRevise: (line: ScriptLine, spoken: string, subtitle: string) => void }) {
  const projectRecord = project.document.project
  const script = project.document.script_versions.find((item) => item.id === projectRecord.current_script_version_id) ?? project.document.script_versions.at(-1)
  if (!script) return null
  return (
    <div>
      <SectionTitle
        title="剧本与台词保真"
        description="original_text、spoken_text 和 subtitle_text 分离。修改会创建新版本，只让引用该台词的镜头、音频、字幕和 Manifest 失效。"
        action={<div className="flex gap-2"><Badge tone="green">v{script.version}</Badge><Badge>{script.approval_status}</Badge></div>}
      />
      <div className="mb-5 grid gap-3 md:grid-cols-3">
        <Metric label="输入契约" value={projectRecord.source_mode} detail="一句话和已有剧本共用后续工件" />
        <Metric label="语言" value={projectRecord.language} detail="首版正式验收普通话中文" />
        <Metric label="版本" value={project.document.script_versions.length} detail="旧版本和旧成片均保留" />
      </div>
      <div className="space-y-3">
        {script.lines.map((line) => <ScriptLineEditor key={`${script.id}-${line.id}`} line={line} onSave={onRevise} />)}
      </div>
      <div className="mt-6 rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
        <h3 className="text-sm font-medium text-white">版本历史</h3>
        <div className="mt-3 flex flex-wrap gap-2">
          {project.document.script_versions.map((version) => (
            <Badge key={version.id} tone={version.id === script.id ? 'green' : 'neutral'}>
              v{version.version} · {version.approval_status}{version.change_reason ? ` · ${version.change_reason}` : ''}
            </Badge>
          ))}
        </div>
      </div>
    </div>
  )
}

function AssetsPanel({ project }: { project: ProjectAggregate }) {
  const icons: Record<string, React.ReactNode> = {
    CHARACTER: <PanelTop className="h-5 w-5" />,
    LOCATION: <ImageIcon className="h-5 w-5" />,
    PROP: <CircleDot className="h-5 w-5" />,
  }
  return (
    <div>
      <SectionTitle
        title="ProjectBible 与资产"
        description="稳定 Asset 身份与 AssetVersion 分离。正式阶段可接用户上传、外部素材库、GPT 新生成或 GPT 修整；Mock 只使用已批准的文字 fixture。"
        action={<Badge tone="blue">{project.document.assets.length} 项资产</Badge>}
      />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {project.document.assets.map((asset) => {
          const version = asset.versions.find((item) => item.id === asset.current_version_id) ?? asset.versions[0]
          return (
            <div key={asset.id} className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900/60">
              <div className="flex h-36 items-center justify-center bg-gradient-to-br from-zinc-800 to-zinc-950 text-blue-300">
                <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-blue-500/20 bg-blue-500/10">
                  {icons[asset.kind] ?? <Layers3 className="h-5 w-5" />}
                </div>
              </div>
              <div className="p-4">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="font-medium text-white">{asset.name}</h3>
                  <Badge>{asset.kind}</Badge>
                </div>
                <p className="mt-2 min-h-20 text-xs leading-5 text-zinc-400">{version.descriptor}</p>
                <div className="mt-3 flex items-center justify-between">
                  <Badge tone="green">{version.origin_type}</Badge>
                  <span className="text-xs text-zinc-600">v{version.version}</span>
                </div>
              </div>
            </div>
          )
        })}
      </div>
      <div className="mt-6 rounded-xl border border-dashed border-zinc-700 bg-zinc-900/30 p-5 text-center">
        <Plus className="mx-auto h-6 w-6 text-zinc-600" />
        <p className="mt-2 text-sm text-zinc-300">上传、素材导入、GPT 生成与 GPT 修整的入口留在统一资产模型中</p>
        <p className="mt-1 text-xs text-zinc-500">真实资产生产属于后续 Phase；本阶段不为了展示按钮调用任何 Provider。</p>
      </div>
    </div>
  )
}

function ShotCard({
  shot,
  project,
  selectedTake,
  onGenerate,
  onRework,
}: {
  shot: ShotPlan
  project: ProjectAggregate
  selectedTake?: Take
  onGenerate: (shotId: string) => void
  onRework: (shotId: string, operation: 'RETAKE' | 'EXTEND' | 'GAP_FILL') => void
}) {
  const route = project.document.execution_plan.find((entry) => entry.shot_id === shot.id)
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
      <div className="flex items-start gap-4">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-blue-500/10 text-lg font-semibold text-blue-300">{shot.sequence}</div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-medium text-white">{shot.narrative_purpose}</h3>
            <Badge>{shot.duration_seconds}s</Badge>
            <Badge tone="blue">{route ? strategyLabels[route.strategy] : '待定路线'}</Badge>
            <Badge tone={selectedTake ? 'green' : 'amber'}>{selectedTake ? `Take ${selectedTake.candidate_index} 已入选` : '待生成'}</Badge>
          </div>
          <p className="mt-2 text-sm leading-6 text-zinc-300">{shot.visual}</p>
          <div className="mt-3 grid gap-2 text-xs text-zinc-500 md:grid-cols-3">
            <div><span className="text-zinc-600">摄影：</span>{shot.framing} · {shot.camera}</div>
            <div><span className="text-zinc-600">声音：</span>{shot.audio_intent}</div>
            <div><span className="text-zinc-600">连续性：</span>{shot.continuity.prop_state_in} → {shot.continuity.prop_state_out}</div>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {!selectedTake && <button className={secondaryButton} onClick={() => onGenerate(shot.id)}><Sparkles className="h-3.5 w-3.5" />生成 2 个候选</button>}
            {selectedTake && (
              <>
                <button className={subtleButton} onClick={() => onRework(shot.id, 'RETAKE')}><RotateCcw className="h-3.5 w-3.5" />Retake</button>
                <button className={subtleButton} onClick={() => onRework(shot.id, 'EXTEND')}><ArrowRight className="h-3.5 w-3.5" />Extend +2s</button>
                <button className={subtleButton} onClick={() => onRework(shot.id, 'GAP_FILL')}><Scissors className="h-3.5 w-3.5" />Gap Fill 1.5s</button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function ShotsPanel({ project, onGenerate, onRework }: { project: ProjectAggregate; onGenerate: (shotId: string) => void; onRework: (shotId: string, operation: 'RETAKE' | 'EXTEND' | 'GAP_FILL') => void }) {
  const selected = selectedTakeMap(project)
  return (
    <div>
      <SectionTitle title="分镜与连续性计划" description="每一镜明确叙事目的、机位、声音、人物/道具起止状态、路线与局部返工风险。" action={<Badge tone="blue">8 镜 · 50 秒</Badge>} />
      <div className="space-y-3">
        {project.document.shots.map((shot) => (
          <ShotCard key={shot.id} shot={shot} project={project} selectedTake={selected.get(shot.id)} onGenerate={onGenerate} onRework={onRework} />
        ))}
      </div>
    </div>
  )
}

function taskAction(task: GenerationTask): 'advance' | 'cancel' | 'retry' | 'reconcile' | 'recover' | null {
  if (['QUEUED', 'SUBMITTING', 'ACKNOWLEDGED', 'RUNNING'].includes(task.status)) return 'advance'
  if (task.status === 'FAILED' || task.status === 'CANCELLED') return 'retry'
  if (task.status === 'SUBMIT_UNKNOWN') return task.provider === 'minimax' ? null : 'reconcile'
  if (task.status === 'OUTPUT_MISSING') return 'recover'
  return null
}

function TakesPanel({
  project,
  onDrain,
  onTaskAction,
  onCancel,
  onSelect,
}: {
  project: ProjectAggregate
  onDrain: () => void
  onTaskAction: (task: GenerationTask, action: 'advance' | 'retry' | 'reconcile' | 'recover') => void
  onCancel: (task: GenerationTask) => void
  onSelect: (takeId: string) => void
}) {
  const [activeShotId, setActiveShotId] = useState(project.document.shots[0]?.id ?? '')
  const shot = project.document.shots.find((item) => item.id === activeShotId) ?? project.document.shots[0]
  const takes = project.takes.filter((take) => take.shot_id === shot?.id)
  const tasks = project.tasks.filter((task) => task.shot_id === shot?.id)
  const superseded = retriedTaskIds(project.tasks)
  return (
    <div>
      <SectionTitle title="Gen Space · Take 与任务" description="任务成功、媒体可用、QA 通过与时间线入选是四个独立事实。异常任务可对账或恢复，不盲目重复提交。" action={<button className={secondaryButton} onClick={onDrain}><Play className="h-3.5 w-3.5" />运行队列</button>} />
      <div className="mb-5 flex gap-2 overflow-x-auto pb-1">
        {project.document.shots.map((item) => {
          const shotTasks = project.tasks.filter((task) => task.shot_id === item.id)
          const hasIssue = shotTasks.some((task) => (
            ['FAILED', 'SUBMIT_UNKNOWN', 'OUTPUT_MISSING'].includes(task.status)
            && !superseded.has(task.id)
          ))
          const isActive = item.id === shot?.id
          return (
            <button key={item.id} onClick={() => setActiveShotId(item.id)} className={`shrink-0 rounded-lg border px-3 py-2 text-xs font-medium transition ${isActive ? 'border-blue-500 bg-blue-500/10 text-blue-200' : 'border-zinc-800 bg-zinc-900 text-zinc-400 hover:border-zinc-700'} ${hasIssue ? 'ring-1 ring-amber-500/40' : ''}`}>
              镜头 {item.sequence} · {project.takes.filter((take) => take.shot_id === item.id && take.freshness === 'CURRENT').length} Takes
            </button>
          )
        })}
      </div>
      {shot && (
        <div className="grid gap-5 xl:grid-cols-[1fr_360px]">
          <div>
            <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
              {takes.length === 0 && <div className="col-span-full rounded-xl border border-dashed border-zinc-700 py-16 text-center text-sm text-zinc-500">当前镜头还没有候选</div>}
              {takes.map((take) => (
                <div key={take.id} className={`overflow-hidden rounded-xl border bg-zinc-900/70 ${take.is_selected ? 'border-blue-500 ring-1 ring-blue-500/30' : 'border-zinc-800'}`}>
                  <div className="relative aspect-[9/16] max-h-[420px] bg-black">
                    <video className="h-full w-full object-contain" src={pathToFileUrl(take.media_path)} poster={pathToFileUrl(take.thumbnail_path)} controls preload="metadata" />
                    <div className="absolute left-2 top-2 flex gap-1.5">
                      <Badge tone={statusTone(take.freshness)}>{statusLabels[take.freshness]}</Badge>
                      <Badge tone={statusTone(take.qa_status)}>QA {statusLabels[take.qa_status]}</Badge>
                    </div>
                  </div>
                  <div className="p-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="text-sm font-medium text-white">Take {take.candidate_index}</p>
                        <p className="mt-0.5 text-xs text-zinc-500">{take.operation} · {take.duration_seconds}s · {take.width}×{take.height}</p>
                      </div>
                      <span className="text-lg font-semibold text-emerald-300">{take.qa_report.score.toFixed(1)}</span>
                    </div>
                    <button className={`mt-3 w-full ${take.is_selected ? secondaryButton : primaryButton}`} disabled={take.is_selected || take.freshness === 'STALE' || take.qa_status !== 'PASSED'} onClick={() => onSelect(take.id)}>
                      {take.is_selected ? <><Check className="h-3.5 w-3.5" />已入选时间线</> : '选用此 Take'}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
          <aside className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
            <h3 className="text-sm font-medium text-white">任务记录</h3>
            <div className="mt-3 space-y-2">
              {tasks.length === 0 && <p className="py-6 text-center text-xs text-zinc-600">暂无任务</p>}
              {tasks.map((task) => {
                const action = superseded.has(task.id) ? null : taskAction(task)
                return (
                  <div key={task.id} className="rounded-lg border border-zinc-800 bg-zinc-950/70 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-zinc-300">候选 {task.candidate_index} · 尝试 {task.attempt}</span>
                      <Badge tone={statusTone(task.status)}>{statusLabels[task.status]}</Badge>
                    </div>
                    <p className="mt-1 truncate text-[11px] text-zinc-600">{task.operation} · {task.request_fingerprint.slice(0, 12)}</p>
                    {task.error_message && <p className="mt-2 text-xs leading-5 text-amber-300/80">{task.error_message}</p>}
                    <div className="mt-2 flex justify-end gap-1">
                      {action && action !== 'cancel' && (
                        <button className={subtleButton} onClick={() => onTaskAction(task, action)}>
                          {action === 'retry' ? '重试' : action === 'reconcile' ? '对账' : action === 'recover' ? '恢复产物' : '推进一步'}
                        </button>
                      )}
                      {['QUEUED', 'SUBMITTING', 'ACKNOWLEDGED', 'RUNNING', 'SUBMIT_UNKNOWN'].includes(task.status) && (
                        <button className={`${subtleButton} text-red-300`} onClick={() => onCancel(task)}><Square className="h-3 w-3" />取消</button>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </aside>
        </div>
      )}
    </div>
  )
}

function TimelinePanel({ project, onBuild, onExport }: { project: ProjectAggregate; onBuild: () => void; onExport: () => void }) {
  const manifest = currentManifest(project)
  const exported = currentExport(project)
  const totalDuration = manifest?.duration_seconds ?? project.document.shots.reduce((sum, shot) => sum + shot.duration_seconds, 0)
  const selected = selectedTakeMap(project)
  return (
    <div>
      <SectionTitle
        title="短剧生产时间线"
        description="视频、原生音轨、字幕 cue 和输出规格由同一 TimelineManifest 驱动；预览直接播放该 Manifest 的最终导出。"
        action={<div className="flex gap-2"><button className={secondaryButton} onClick={onBuild} disabled={project.progress.shots_with_selected_take !== project.progress.total_shots}><RefreshCw className="h-3.5 w-3.5" />重建 Manifest</button><button className={primaryButton} onClick={onExport} disabled={!manifest}><Download className="h-3.5 w-3.5" />导出交付包</button></div>}
      />
      {!manifest ? (
        <div className="rounded-xl border border-dashed border-zinc-700 py-20 text-center">
          <Clock3 className="mx-auto h-8 w-8 text-zinc-600" />
          <p className="mt-3 text-sm text-zinc-300">还没有当前 TimelineManifest</p>
          <p className="mt-1 text-xs text-zinc-500">为每个镜头选择一个当前且 QA 通过的 Take 后即可构建。</p>
        </div>
      ) : (
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1.4fr)_420px]">
          <div className="space-y-4">
            <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
              <div className="mb-3 flex items-center justify-between text-xs text-zinc-500"><span>V1 · 入选 Take</span><span>{manifest.duration_seconds.toFixed(1)}s</span></div>
              <div className="flex h-24 overflow-hidden rounded-lg border border-zinc-800 bg-zinc-950">
                {manifest.video_clips.map((clip, index) => (
                  <div key={clip.id} className={`relative flex min-w-0 items-center justify-center border-r border-black/40 bg-gradient-to-b ${index % 2 ? 'from-cyan-900/80 to-zinc-950' : 'from-blue-900/80 to-zinc-950'}`} style={{ width: `${(clip.duration_seconds / totalDuration) * 100}%` }} title={`${clip.shot_id} · ${clip.duration_seconds}s`}>
                    <div className="text-center"><Film className="mx-auto h-4 w-4 text-blue-300" /><span className="mt-1 block text-[10px] text-zinc-300">S{index + 1}</span><span className="block text-[9px] text-zinc-500">{clip.duration_seconds}s</span></div>
                  </div>
                ))}
              </div>
            </div>
            <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
              <div className="mb-2 flex items-center gap-2 text-xs text-zinc-500"><FileAudio className="h-3.5 w-3.5" />A1 · {manifest.audio_clips.some((clip) => clip.role === 'MODEL_NATIVE_H3') ? 'H3 + Mock Native Audio' : 'Native Mock Audio'}</div>
              <div className="flex h-9 overflow-hidden rounded bg-zinc-950">
                {manifest.audio_clips.map((clip, index) => <div key={clip.id} className="border-r border-zinc-900 bg-emerald-500/15" style={{ width: `${(clip.duration_seconds / totalDuration) * 100}%` }} title={`S${index + 1} gain ${clip.gain_db}dB`} />)}
              </div>
            </div>
            <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
              <div className="mb-2 flex items-center gap-2 text-xs text-zinc-500"><Subtitles className="h-3.5 w-3.5" />S1 · 中文字幕</div>
              <div className="relative h-14 rounded bg-zinc-950">
                {manifest.subtitle_cues.map((cue) => (
                  <div key={cue.id} className="absolute top-2 h-10 overflow-hidden rounded border border-amber-500/20 bg-amber-500/10 px-2 py-1 text-[10px] text-amber-100" style={{ left: `${(cue.start_seconds / totalDuration) * 100}%`, width: `${Math.max(5, ((cue.end_seconds - cue.start_seconds) / totalDuration) * 100)}%` }} title={cue.text}>{cue.text}</div>
                ))}
              </div>
            </div>
            <div className="grid grid-cols-4 gap-3">
              <Metric label="输出" value={`${manifest.output_spec.width}×${manifest.output_spec.height}`} detail="9:16 竖屏封装" />
              <Metric label="帧率" value={`${manifest.output_spec.fps} fps`} detail="CFR" />
              <Metric label="视频" value="H.264 High" detail={`CRF ${manifest.output_spec.crf} · BT.709`} />
              <Metric label="音频" value="AAC-LC" detail={`${manifest.output_spec.audio_sample_rate / 1000}kHz · ${manifest.output_spec.audio_bitrate_kbps}kbps`} />
            </div>
          </div>
          <aside>
            <div className="overflow-hidden rounded-xl border border-zinc-800 bg-black">
              <div className="aspect-[9/16] max-h-[620px]">
                {exported ? (
                  <video key={exported.id} className="h-full w-full object-contain" src={pathToFileUrl(exported.subtitled_video_path)} controls preload="metadata" />
                ) : (
                  <div className="flex h-full flex-col items-center justify-center text-center"><Play className="h-8 w-8 text-zinc-700" /><p className="mt-3 text-sm text-zinc-400">Manifest 已就绪</p><p className="mt-1 text-xs text-zinc-600">导出后在这里预览最终成片</p></div>
                )}
              </div>
            </div>
            {exported && (
              <div className="mt-3 flex items-center justify-between rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-3 py-2">
                <div><p className="text-xs font-medium text-emerald-200">预览与导出同源</p><p className="text-[11px] text-zinc-500">{exported.manifest_id}</p></div>
                <button className={subtleButton} onClick={() => window.electronAPI.showItemInFolder({ filePath: exported.subtitled_video_path })}><FolderOpen className="h-3.5 w-3.5" />查看文件</button>
              </div>
            )}
          </aside>
        </div>
      )}
      {manifest && (
        <div className="mt-5 rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
          <div className="flex items-center justify-between"><h3 className="text-sm font-medium text-white">镜头引用</h3><Badge tone="green">{manifest.video_clips.length} / {selected.size}</Badge></div>
          <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-4">{manifest.video_clips.map((clip, index) => <div key={clip.id} className="rounded-lg bg-zinc-950 px-3 py-2 text-xs text-zinc-400">S{index + 1} · {clip.take_id.split('-').slice(-1)[0].slice(0, 8)} · {clip.duration_seconds}s</div>)}</div>
        </div>
      )}
    </div>
  )
}

function QaPanel({ project }: { project: ProjectAggregate }) {
  const exported = currentExport(project)
  const currentTakes = project.takes.filter((take) => take.freshness === 'CURRENT')
  const hardPass = currentTakes.filter((take) => take.qa_report.hard_gate_passed).length
  return (
    <div>
      <SectionTitle title="质量检查" description="Mock QA 只证明媒体、引用、时钟和导出正确；真人表演、身份、口型和观看意愿必须等真实 H3 样片与人工盲评。" />
      <div className="grid gap-3 md:grid-cols-4">
        <Metric label="当前 Take" value={currentTakes.length} detail="旧版本不丢失" />
        <Metric label="硬门槛" value={`${hardPass}/${currentTakes.length}`} detail="Mock 技术门槛" />
        <Metric label="失效 Take" value={project.progress.stale_takes} detail="仍可复现旧成片" />
        <Metric label="导出检查" value={exported ? `${exported.checks.filter((check) => check.passed).length}/${exported.checks.length}` : '—'} detail="FFmpeg 媒体合规" />
      </div>
      <div className="mt-5 grid gap-5 xl:grid-cols-2">
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <h3 className="font-medium text-white">Take QA</h3>
          <div className="mt-4 space-y-2">
            {project.document.shots.map((shot) => {
              const takes = currentTakes.filter((take) => take.shot_id === shot.id)
              const best = [...takes].sort((a, b) => b.qa_report.score - a.qa_report.score)[0]
              return (
                <div key={shot.id} className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-950/70 px-3 py-2.5">
                  <div><p className="text-xs font-medium text-zinc-300">镜头 {shot.sequence}</p><p className="text-[11px] text-zinc-600">{takes.length} 个当前候选</p></div>
                  {best ? <div className="flex items-center gap-2"><Badge tone="green">硬门槛通过</Badge><span className="font-semibold text-emerald-300">{best.qa_report.score.toFixed(1)}</span></div> : <Badge tone="amber">待生成</Badge>}
                </div>
              )
            })}
          </div>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <h3 className="font-medium text-white">交付技术检查</h3>
          {!exported ? <p className="mt-10 text-center text-sm text-zinc-600">导出后显示分辨率、帧率、音轨、时长和 Manifest 一致性。</p> : (
            <div className="mt-4 space-y-2">
              {exported.checks.map((check) => (
                <div key={check.name} className="flex items-start gap-3 rounded-lg border border-zinc-800 bg-zinc-950/70 px-3 py-2.5">
                  {check.passed ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" /> : <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />}
                  <div><p className="text-xs font-medium text-zinc-300">{check.name}</p><p className="mt-0.5 text-[11px] text-zinc-600">{check.detail}</p></div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      <div className="mt-5 rounded-xl border border-amber-500/20 bg-amber-500/5 p-4">
        <div className="flex gap-3"><AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" /><div><p className="text-sm font-medium text-amber-100">质量结论边界</p><p className="mt-1 text-xs leading-5 text-zinc-400">Mock 分数只证明工程链路。H3 实验台会保存真实 768P 输出与单人硬门槛审片；路线稳定性和最终质量仍需按固定五句台词、身份/道具连续性、声音、口型和至少三人盲评完成。</p></div></div>
      </div>
    </div>
  )
}

function CreateProjectPanel({ onCreate, onFixture }: { onCreate: (name: string, mode: SourceMode, input: string, runMode: RunMode) => void; onFixture: () => void }) {
  const [name, setName] = useState('新的都市短剧')
  const [sourceMode, setSourceMode] = useState<SourceMode>('IDEA_EXPANSION')
  const [runMode, setRunMode] = useState<RunMode>('AUTO_DRAFT')
  const [input, setInput] = useState('雨夜，一件不该出现的证物让两个人开始互相怀疑。')
  return (
    <div className="mx-auto flex h-full max-w-5xl items-center px-8 py-12">
      <div className="grid w-full gap-8 xl:grid-cols-[1fr_1.1fr]">
        <div className="flex flex-col justify-center">
          <Badge tone="blue">AI 短剧制片工作台</Badge>
          <h1 className="mt-5 text-4xl font-semibold leading-tight text-white">从一句话或剧本，进入可编辑的完整制片闭环</h1>
          <p className="mt-4 text-sm leading-7 text-zinc-400">供应商中立 Mock 闭环与 MiniMax-H3 768P 已共用同一项目核心；可先生成零成本 Mock 草稿，再在代表镜头上比较 Direct、Frame 与 Reference 的真实质量。</p>
          <button className={`${secondaryButton} mt-8 w-fit px-5 py-3`} onClick={onFixture}><Clapperboard className="h-4 w-4 text-blue-400" />打开固定验收片《别相信我》</button>
        </div>
        <div className="rounded-2xl border border-zinc-800 bg-zinc-900/70 p-6 shadow-2xl">
          <h2 className="text-lg font-semibold text-white">新建项目</h2>
          <div className="mt-5 space-y-4">
            <label className="block"><span className="mb-1.5 block text-xs text-zinc-500">项目名</span><input value={name} onChange={(event) => setName(event.target.value)} className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-200 outline-none focus:border-blue-500" /></label>
            <div className="grid grid-cols-2 gap-3">
              <label className="block"><span className="mb-1.5 block text-xs text-zinc-500">输入契约</span><select value={sourceMode} onChange={(event) => setSourceMode(event.target.value as SourceMode)} className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-200 outline-none"><option value="IDEA_EXPANSION">一句话扩写</option><option value="SCRIPT_LOCKED">已有剧本锁定</option></select></label>
              <label className="block"><span className="mb-1.5 block text-xs text-zinc-500">运行模式</span><select value={runMode} onChange={(event) => setRunMode(event.target.value as RunMode)} className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-200 outline-none"><option value="AUTO_DRAFT">自动到底</option><option value="GUIDED">可停靠引导</option></select></label>
            </div>
            <label className="block"><span className="mb-1.5 block text-xs text-zinc-500">{sourceMode === 'IDEA_EXPANSION' ? '一句话创意' : '锁定剧本'}</span><textarea value={input} onChange={(event) => setInput(event.target.value)} className="min-h-36 w-full resize-none rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm leading-6 text-zinc-200 outline-none focus:border-blue-500" /></label>
            <button className={`${primaryButton} w-full py-3`} disabled={!name.trim() || !input.trim()} onClick={() => onCreate(name.trim(), sourceMode, input.trim(), runMode)}><Plus className="h-4 w-4" />建立结构化项目</button>
          </div>
          <p className="mt-4 text-center text-[11px] text-zinc-600">当前 Planner 仍以固定 fixture 验证结构；已有剧本的原文、口播文和字幕文会分开保存。</p>
        </div>
      </div>
    </div>
  )
}

export function ShortDramaStudio() {
  const [projects, setProjects] = useState<ProjectRecord[]>([])
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null)
  const [project, setProject] = useState<ProjectAggregate | null>(null)
  const [tab, setTab] = useState<StudioTab>('overview')
  const [busyLabel, setBusyLabel] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)

  const perform = useCallback(async <T,>(label: string, action: () => Promise<ResultLike<T>>, onSuccess: (data: T) => void) => {
    setBusyLabel(label)
    setError(null)
    setNotice(null)
    try {
      const result = await action()
      if (!result.ok) throw new Error(result.error.message)
      onSuccess(result.data)
      setNotice(`${label}完成`)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusyLabel(null)
    }
  }, [])

  const loadProjects = useCallback(async () => {
    const result = await ShortDramaApi.listProjects()
    if (!result.ok) {
      setError(result.error.message)
      return []
    }
    setProjects(result.data.projects)
    return result.data.projects
  }, [])

  const loadProject = useCallback(async (projectId: string) => {
    setBusyLabel('读取项目')
    const result = await ShortDramaApi.getProject(projectId)
    setBusyLabel(null)
    if (!result.ok) {
      setError(result.error.message)
      return
    }
    setActiveProjectId(projectId)
    setProject(result.data)
    setShowCreate(false)
  }, [])

  useEffect(() => {
    let cancelled = false
    const initialize = async () => {
      const listed = await loadProjects()
      if (cancelled || listed.length === 0) return
      await loadProject(listed[0].id)
    }
    void initialize()
    return () => { cancelled = true }
  }, [loadProject, loadProjects])

  const refresh = useCallback(async () => {
    if (!activeProjectId) return
    const result = await ShortDramaApi.getProject(activeProjectId)
    if (result.ok) setProject(result.data)
    else setError(result.error.message)
  }, [activeProjectId])

  const initializeFixture = () => void perform('初始化固定样片', ShortDramaApi.initializeFixture, (data) => {
    setProject(data)
    setActiveProjectId(data.document.project.id)
    setShowCreate(false)
    setTab('overview')
    void loadProjects()
  })

  const createProject = (name: string, sourceMode: SourceMode, inputText: string, runMode: RunMode) => void perform(
    '建立项目',
    () => ShortDramaApi.createProject({ name, source_mode: sourceMode, input_text: inputText, run_mode: runMode }),
    (data) => {
      setProject(data)
      setActiveProjectId(data.document.project.id)
      setShowCreate(false)
      setTab('overview')
      void loadProjects()
    },
  )

  const autoDraft = () => {
    if (!activeProjectId) return
    void perform('生成并导出完整 Mock 草稿', () => ShortDramaApi.autoDraft(activeProjectId, true), (data) => {
      setProject(data.project)
      setTab('timeline')
      void loadProjects()
    })
  }

  const drain = () => {
    if (!activeProjectId) return
    void perform('运行持久化任务队列', () => ShortDramaApi.drain(activeProjectId, 500), (data) => setProject(data.project))
  }

  const generateShot = (shotId: string) => {
    if (!activeProjectId) return
    const run = async () => {
      setBusyLabel('生成镜头候选')
      setError(null)
      const enqueue = await ShortDramaApi.enqueue(activeProjectId, { shot_ids: [shotId], candidate_count: 2, operation: 'GENERATE', behavior_by_candidate: {} })
      if (!enqueue.ok) { setError(enqueue.error.message); setBusyLabel(null); return }
      const result = await ShortDramaApi.drain(activeProjectId, 100)
      setBusyLabel(null)
      if (!result.ok) { setError(result.error.message); return }
      setProject(result.data.project)
      setNotice('镜头候选生成完成')
    }
    void run()
  }

  const localRework = (shotId: string, operation: 'RETAKE' | 'EXTEND' | 'GAP_FILL') => {
    if (!activeProjectId) return
    const run = async () => {
      setBusyLabel(`${operation} 处理中`)
      setError(null)
      const queued = await ShortDramaApi.localRework(activeProjectId, shotId, operation, operation === 'EXTEND' ? 2 : operation === 'GAP_FILL' ? 1.5 : 0)
      if (!queued.ok) { setError(queued.error.message); setBusyLabel(null); return }
      const result = await ShortDramaApi.drain(activeProjectId, 100)
      setBusyLabel(null)
      if (!result.ok) { setError(result.error.message); return }
      setProject(result.data.project)
      setTab('takes')
      setNotice(`${operation} 候选已生成；选择后才会改变时间线`)
    }
    void run()
  }

  const handleTaskAction = (task: GenerationTask, action: 'advance' | 'retry' | 'reconcile' | 'recover') => {
    const method = action === 'advance' ? ShortDramaApi.advanceTask : action === 'retry' ? ShortDramaApi.retryTask : action === 'reconcile' ? ShortDramaApi.reconcileTask : ShortDramaApi.recoverTaskOutput
    void perform(statusLabels[task.status] ?? action, () => method(task.id), (data) => setProject(data.project))
  }

  const cancelTask = (task: GenerationTask) => void perform('取消任务', () => ShortDramaApi.cancelTask(task.id), (data) => setProject(data.project))

  const selectTake = (takeId: string) => void perform('切换入选 Take', () => ShortDramaApi.selectTake(takeId), setProject)

  const reviseLine = (line: ScriptLine, spoken: string, subtitle: string) => {
    if (!activeProjectId) return
    void perform('创建新剧本版本并局部失效', () => ShortDramaApi.reviseScriptLine(activeProjectId, { line_id: line.id, spoken_text: spoken, subtitle_text: subtitle, change_reason: '工作台台词修改' }), setProject)
  }

  const buildManifest = () => {
    if (!activeProjectId) return
    void perform('重建 TimelineManifest', () => ShortDramaApi.rebuildManifest(activeProjectId), () => { void refresh() })
  }

  const exportProject = () => {
    if (!activeProjectId) return
    void perform('导出 MP4 / SRT / ASS / Manifest / QA', () => ShortDramaApi.exportProject(activeProjectId, false), () => { void refresh() })
  }

  const tabs: Array<{ id: StudioTab; label: string; icon: React.ReactNode; count?: number }> = project ? [
    { id: 'overview', label: '总览', icon: <PanelTop className="h-4 w-4" /> },
    { id: 'script', label: '剧本', icon: <FileText className="h-4 w-4" />, count: project.document.script_versions.length },
    { id: 'assets', label: '资产', icon: <Layers3 className="h-4 w-4" />, count: project.document.assets.length },
    { id: 'shots', label: '分镜', icon: <Clapperboard className="h-4 w-4" />, count: project.document.shots.length },
    { id: 'h3', label: 'H3 实验', icon: <FlaskConical className="h-4 w-4" />, count: project.tasks.filter((task) => task.provider === 'minimax').length },
    { id: 'takes', label: '候选', icon: <Sparkles className="h-4 w-4" />, count: project.takes.filter((take) => take.freshness === 'CURRENT').length },
    { id: 'timeline', label: '时间线', icon: <Film className="h-4 w-4" /> },
    { id: 'qa', label: '质检', icon: <CheckCircle2 className="h-4 w-4" /> },
  ] : []

  const content = useMemo(() => {
    if (!project) return null
    if (tab === 'overview') return <OverviewPanel project={project} onAutoDraft={autoDraft} />
    if (tab === 'script') return <ScriptPanel project={project} onRevise={reviseLine} />
    if (tab === 'assets') return <AssetsPanel project={project} />
    if (tab === 'shots') return <ShotsPanel project={project} onGenerate={generateShot} onRework={localRework} />
    if (tab === 'h3') return <H3LabPanel project={project} onProjectChange={setProject} onError={setError} onNotice={setNotice} />
    if (tab === 'takes') return <TakesPanel project={project} onDrain={drain} onTaskAction={handleTaskAction} onCancel={cancelTask} onSelect={selectTake} />
    if (tab === 'timeline') return <TimelinePanel project={project} onBuild={buildManifest} onExport={exportProject} />
    return <QaPanel project={project} />
  }, [project, tab])

  const shouldShowCreate = showCreate || !project
  return (
    <div className="flex h-screen bg-[#08090c] text-zinc-100">
      <aside className="flex w-64 shrink-0 flex-col border-r border-zinc-800/80 bg-[#0c0e12]">
        <div className="border-b border-zinc-800/80 p-4">
          <div className="flex items-center gap-3"><div className="flex h-9 w-9 items-center justify-center rounded-xl bg-blue-500 text-white"><Clapperboard className="h-5 w-5" /></div><div><p className="text-sm font-semibold text-white">Short Drama Studio</p><p className="text-[11px] text-zinc-500">Local Production Core</p></div></div>
          <button className={`${secondaryButton} mt-4 w-full`} onClick={() => setShowCreate(true)}><Plus className="h-4 w-4" />新建项目</button>
        </div>
        <div className="flex-1 overflow-y-auto p-3">
          <p className="px-2 pb-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-600">Projects · SQLite</p>
          <div className="space-y-1">
            {projects.map((item) => (
              <button key={item.id} onClick={() => void loadProject(item.id)} className={`w-full rounded-lg px-3 py-2.5 text-left transition ${activeProjectId === item.id && !showCreate ? 'bg-blue-500/10 text-white ring-1 ring-blue-500/20' : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200'}`}>
                <div className="flex items-center justify-between gap-2"><span className="truncate text-sm font-medium">{item.name}</span><ChevronRight className="h-3.5 w-3.5 shrink-0 text-zinc-600" /></div>
                <div className="mt-1 flex items-center gap-1.5"><span className="text-[10px] text-zinc-600">{stageLabels[item.stage] ?? item.stage}</span><span className="text-zinc-800">·</span><span className="text-[10px] text-zinc-600">{item.source_mode === 'SCRIPT_LOCKED' ? '剧本' : '创意'}</span></div>
              </button>
            ))}
          </div>
        </div>
        <div className="border-t border-zinc-800/80 p-3">
          <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-3"><div className="flex items-center gap-2 text-xs text-zinc-300"><Database className="h-3.5 w-3.5 text-emerald-400" />SQLite WAL · Connected</div><p className="mt-1 text-[10px] leading-4 text-zinc-600">无登录、权限或云端项目依赖</p></div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {!shouldShowCreate && project && (
          <header className="border-b border-zinc-800/80 bg-[#0b0d11]/95">
            <div className="flex h-16 items-center justify-between px-6">
              <div className="min-w-0"><div className="flex items-center gap-2"><h1 className="truncate text-base font-semibold text-white">{project.document.project.name}</h1><Badge tone="blue">{stageLabels[project.document.project.stage] ?? project.document.project.stage}</Badge></div><p className="mt-0.5 text-[11px] text-zinc-600">{project.document.project.id} · {project.document.project.language} · {project.document.project.aspect_ratio}</p></div>
              <div className="flex items-center gap-2"><Badge tone={project.progress.unresolved_tasks ? 'amber' : 'green'}>{project.progress.unresolved_tasks ? `${project.progress.unresolved_tasks} 项待处理` : '状态健康'}</Badge><button className={subtleButton} onClick={() => void refresh()}><RefreshCw className="h-3.5 w-3.5" />刷新</button></div>
            </div>
            <nav className="flex gap-1 overflow-x-auto px-5">
              {tabs.map((item) => <button key={item.id} onClick={() => setTab(item.id)} className={`flex items-center gap-2 border-b-2 px-3 py-3 text-xs font-medium transition ${tab === item.id ? 'border-blue-500 text-white' : 'border-transparent text-zinc-500 hover:text-zinc-300'}`}>{item.icon}{item.label}{item.count !== undefined && <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-500">{item.count}</span>}</button>)}
            </nav>
          </header>
        )}

        <main className="relative min-h-0 flex-1 overflow-y-auto">
          {shouldShowCreate ? <CreateProjectPanel onCreate={createProject} onFixture={initializeFixture} /> : <div className="mx-auto max-w-[1600px] p-6 lg:p-8">{content}</div>}
          {busyLabel && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 backdrop-blur-sm"><div className="rounded-xl border border-zinc-700 bg-zinc-900 px-6 py-5 shadow-2xl"><div className="flex items-center gap-3 text-sm font-medium text-white"><Loader2 className="h-5 w-5 animate-spin text-blue-400" />{busyLabel}</div><p className="mt-2 text-xs text-zinc-500">任务和恢复点正在持久化，请勿关闭窗口。</p></div></div>}
          {(notice || error) && <div className={`fixed bottom-5 right-5 z-40 max-w-md rounded-xl border px-4 py-3 shadow-2xl ${error ? 'border-red-500/30 bg-red-950/95' : 'border-emerald-500/30 bg-emerald-950/95'}`}><div className="flex items-start gap-2">{error ? <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-300" /> : <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-300" />}<div><p className="text-sm text-white">{error ? '操作未完成' : notice}</p>{error && <p className="mt-1 text-xs leading-5 text-red-200/70">{error}</p>}</div><button className="ml-3 text-xs text-zinc-500" onClick={() => { setNotice(null); setError(null) }}>关闭</button></div></div>}
        </main>
      </div>
    </div>
  )
}
