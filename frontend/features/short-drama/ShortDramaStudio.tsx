import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
  ImagePlus,
  Layers3,
  Loader2,
  PanelTop,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  ScanSearch,
  Scissors,
  Sparkles,
  Square,
  Subtitles,
  WandSparkles,
  Workflow,
  X,
} from 'lucide-react'
import { pathToFileUrl } from '../../lib/file-url'
import {
  ShortDramaApi,
  type AutonomousExecutionTarget,
  type AutonomousRunRecord,
  type CreativeInputImage,
  type ExportRecord,
  type GenerationTask,
  type PlanningRunRecord,
  type PlanningRunResponse,
  type ProjectAggregate,
  type ProjectRecord,
  type ProductionProfile,
  type RunMode,
  type ScriptLine,
  type ScriptQualityHistoryResponse,
  type ShotPlan,
  type SourceMode,
  type Take,
  type TimelineManifest,
} from './api'
import { H3LabPanel } from './H3LabPanel'
import { AssetWorkbenchPanel } from './AssetWorkbenchPanel'

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

const productionProfileLabels: Record<ProductionProfile, string> = {
  GENERAL_VIDEO: '通用视频',
  DOUYIN_SHORT_DRAMA: '抖音短剧',
}

const planningStepLabels: Record<PlanningRunRecord['current_step'], string> = {
  STORY_ARCHITECT: 'Story Architect · 全局蓝图',
  SCENE_WRITER: 'Scene Writer · 逐场剧本',
  SCRIPT_CRITIC: 'Script Critic · 独立审稿',
  STORY_REVISION: 'Story Architect · 有界修订',
  REVISION_JUDGE: 'Revision Judge · 比较最近两版',
  SCENE_DIRECTOR: 'Scene Director · 逐场分镜',
  FINALIZE: '汇总剧本与制片计划',
  COMPLETE: '剧本规划完成',
}

const autonomousStepLabels: Record<AutonomousRunRecord['current_step'], string> = {
  NORMALIZE_INPUT: '归一化输入',
  PLAN: '建立制片计划',
  IMPORT_INPUT_ASSETS: '导入输入资产',
  PREPARE_ASSETS: '准备核心资产',
  PLAN_ROUTES: '规划下一组 H3 路线',
  PREPARE_SHOT_ASSETS: '准备镜头资产',
  GENERATE: '生成 H3 候选',
  QA_AND_REPAIR: '视频 QA 与导演修复',
  TIMELINE: '组装时间线',
  EPISODE_REVIEW: '整集分层审阅',
  AUDIO_QA: '音频检查',
  EXPORT: '导出成片',
  COMPLETE: '自动制片完成',
}

const scriptQualityTrendLabels: Record<ScriptQualityHistoryResponse['comparison']['trend'], string> = {
  NOT_COMPARABLE: '仅一轮，无法比较',
  FEWER_REPORTED_ISSUES: '末轮报告问题更少',
  MIXED_REPORTED_ISSUES: '报告变化混合',
  MORE_REPORTED_ISSUES: '末轮报告问题更多',
  UNCHANGED_REPORTED_ISSUES: '报告数量未变',
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

function OverviewPanel({
  project,
  onAutoDraft,
  onContinueRealDraft,
}: {
  project: ProjectAggregate
  onAutoDraft: () => void
  onContinueRealDraft: () => void
}) {
  const record = project.document.project
  const progress = project.progress
  const latestRun = (project.document.autonomous_runs ?? []).at(-1)
  const stages = ['PLANNED', 'GENERATING', 'REVIEW', 'TIMELINE_READY', 'EXPORTED']
  const activeIndex = Math.max(0, stages.indexOf(record.stage))
  const superseded = retriedTaskIds(project.tasks)
  const unresolved = project.tasks.filter((task) => (
    ['SUBMIT_UNKNOWN', 'OUTPUT_MISSING', 'FAILED'].includes(task.status)
    && !superseded.has(task.id)
  ))
  const runNeedsAttention = latestRun?.status === 'NEEDS_ATTENTION'
  const attentionCount = unresolved.length + (runNeedsAttention ? 1 : 0)
  return (
    <div className="space-y-7">
      <section className="overflow-hidden rounded-2xl border border-blue-500/20 bg-gradient-to-br from-blue-500/10 via-zinc-950 to-cyan-500/5 p-6">
        <div className="flex flex-col gap-6 xl:flex-row xl:items-center xl:justify-between">
          <div className="max-w-3xl">
            <div className="mb-3 flex items-center gap-2">
              <Badge tone="blue">AUTO_DRAFT</Badge>
              <Badge tone="green">{productionProfileLabels[record.production_profile ?? 'DOUYIN_SHORT_DRAMA']}</Badge>
              <Badge>{record.source_mode}</Badge>
              <Badge>{record.run_mode}</Badge>
              {latestRun && <Badge tone={latestRun.status === 'COMPLETE' ? 'green' : latestRun.status === 'FAILED' || latestRun.status === 'NEEDS_ATTENTION' ? 'amber' : 'blue'}>{latestRun.planner} · {latestRun.status}</Badge>}
            </div>
            <h2 className="text-2xl font-semibold text-white">从任意输入到可播放成片的一键闭环</h2>
            <p className="mt-3 text-sm leading-6 text-zinc-300">
              自定义时长先由 Story Architect 规划整集，Script Critic 做开放式剧本审阅，再由 Scene Director 逐场展开并传递精简连续性状态；后续资产、H3、QA、Director 返工和时间线继续共享同一可恢复项目。
            </p>
          </div>
          <div className="flex min-w-56 flex-col gap-2">
            {latestRun?.execution_target === 'REAL_H3' && latestRun.status === 'NEEDS_ATTENTION' && <button className={`${primaryButton} py-3`} onClick={onContinueRealDraft}><RefreshCw className="h-4 w-4" />修复阻断后继续真实草稿</button>}
            {latestRun?.execution_target === 'REAL_H3' && latestRun.status === 'RUNNING' && <div className="flex items-center justify-center gap-2 rounded-lg border border-blue-500/30 bg-blue-500/10 px-4 py-3 text-sm text-blue-100"><Loader2 className="h-4 w-4 animate-spin" />真实草稿自动推进中</div>}
            <button className={secondaryButton} onClick={onAutoDraft}><WandSparkles className="h-4 w-4" />生成无付费 Mock 草稿</button>
          </div>
        </div>
      </section>

      {latestRun?.execution_target === 'REAL_H3' && latestRun.status !== 'COMPLETE' && (
        <section className={`rounded-xl border p-4 ${latestRun.status === 'NEEDS_ATTENTION' ? 'border-amber-500/20 bg-amber-500/5' : 'border-blue-500/20 bg-blue-500/5'}`}>
          <div className="flex gap-3"><Clock3 className={`mt-0.5 h-4 w-4 shrink-0 ${latestRun.status === 'NEEDS_ATTENTION' ? 'text-amber-400' : 'text-blue-400'}`} /><div><p className="text-sm font-medium text-zinc-200">{autonomousStepLabels[latestRun.current_step]} · 镜头 {progress.shots_with_selected_take}/{progress.total_shots}</p><p className="mt-1 text-xs leading-5 text-zinc-400">{latestRun.error_message ?? latestRun.waiting_reason ?? `任务 ${progress.terminal_tasks}/${progress.total_tasks} 已进入终态；系统会继续推进下一次有限动作。`}</p><p className="mt-1 text-[11px] leading-5 text-zinc-500">累计 {latestRun.repair_round} 轮自动返工；同一个镜头最多自动尝试 {latestRun.max_repair_rounds} 次。</p></div></div>
        </section>
      )}

      {latestRun && (latestRun.warnings?.length ?? 0) > 0 && (
        <section className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-4">
          <div className="flex gap-3"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" /><div><p className="text-sm font-medium text-amber-100">自动运行提示</p><div className="mt-1 space-y-1">{latestRun.warnings?.map((warning) => <p key={warning} className="text-xs leading-5 text-zinc-400">{warning}</p>)}</div></div></div>
        </section>
      )}

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
          <p className="mt-1 text-xs text-zinc-500">同一 ShotPlan 可表达四条路线；实际选择由已审 RouteEvidence、可复用 Take、批准资产与通用失败风险共同决定。</p>
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
            <Badge tone={attentionCount ? 'amber' : 'green'}>{attentionCount}</Badge>
          </div>
          {attentionCount === 0 ? (
            <div className="mt-6 flex flex-col items-center py-6 text-center">
              <CheckCircle2 className="h-8 w-8 text-emerald-400" />
              <p className="mt-3 text-sm text-zinc-300">当前没有阻断项</p>
              <p className="mt-1 text-xs text-zinc-500">历史任务仍保留，可在候选页审阅。</p>
            </div>
          ) : (
            <div className="mt-4 space-y-2">
              {runNeedsAttention && <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3"><div className="flex items-center justify-between gap-2"><span className="text-xs text-zinc-300">自动运行 · {latestRun.current_step}</span><Badge tone="amber">NEEDS_ATTENTION</Badge></div><p className="mt-1 text-xs leading-5 text-zinc-500">{latestRun.error_message ?? '修复配置或对账后继续。'}</p></div>}
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
  const planningRunId = script?.planning_run_id ?? null
  const [qualityHistory, setQualityHistory] = useState<ScriptQualityHistoryResponse | null>(null)
  const [qualityError, setQualityError] = useState<string | null>(null)
  useEffect(() => {
    let cancelled = false
    setQualityHistory(null)
    setQualityError(null)
    if (!planningRunId) return () => { cancelled = true }
    void ShortDramaApi.getPlanningRunScriptQuality(planningRunId).then((result) => {
      if (cancelled) return
      if (result.ok) setQualityHistory(result.data)
      else setQualityError(result.error.message)
    })
    return () => { cancelled = true }
  }, [planningRunId])
  if (!script) return null
  const review = script.review_report
  const reviewDisposition = script.review_disposition
  const reviewStrengths = review?.strengths ?? []
  const reviewIssues = review?.issues ?? []
  const routedIssueOwners = new Map(
    (reviewDisposition?.issue_routes ?? []).map((route) => [route.issue_index, route.owner]),
  )
  const reviewBadge = reviewDisposition?.action === 'REVISE_STORY'
    ? 'Agent 建议修订剧本'
    : reviewDisposition?.action === 'CONTINUE_WITH_HANDOFF'
      ? '已下放制作层'
      : reviewIssues.length > 0
        ? '保留可选建议'
        : '可继续'
  const reviewNeedsStoryRevision = reviewDisposition?.action === 'REVISE_STORY'
  const qualityRounds = qualityHistory?.comparison.rounds ?? []
  const persistentQualityLabels = qualityHistory?.comparison.persistent_category_labels ?? []
  const absentQualityLabels = qualityHistory?.comparison.category_labels_no_longer_reported ?? []
  const newQualityLabels = qualityHistory?.comparison.newly_reported_category_labels ?? []
  const latestTotalIssueDelta = qualityHistory?.comparison.latest_total_issue_delta ?? 0
  const latestHighIssueDelta = qualityHistory?.comparison.latest_high_issue_delta ?? 0
  const revisionJudge = qualityHistory?.revision_judge ?? null
  const revisionJudgeEvidence = revisionJudge?.evidence ?? []
  const revisionJudgeTradeoffs = revisionJudge?.tradeoffs ?? []
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
      {qualityHistory && qualityRounds.length > 0 && (
        <section className="mb-5 rounded-xl border border-blue-500/20 bg-blue-500/5 p-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <h3 className="text-sm font-medium text-white">Script Critic 修订效果轨迹</h3>
              <p className="mt-1 max-w-4xl text-xs leading-5 text-zinc-400">{qualityHistory.comparison.advisory_summary}</p>
            </div>
            <Badge tone={qualityHistory.comparison.latest_report_regression_risk ? 'amber' : 'blue'}>{scriptQualityTrendLabels[qualityHistory.comparison.trend]}</Badge>
          </div>
          <div className="mt-4 grid gap-2 md:grid-cols-3">
            {qualityRounds.map((round) => (
              <div key={round.review_round} className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-xs font-medium text-zinc-300">{round.revision_round === 0 ? '初稿审阅' : `第 ${round.revision_round} 轮修订后`}</p>
                  <span className="text-[10px] text-zinc-600">Review {round.review_round}</span>
                </div>
                <p className="mt-2 text-lg font-semibold text-white">{round.total_issues} <span className="text-xs font-normal text-zinc-500">项报告问题</span></p>
                <div className="mt-2 flex flex-wrap gap-1.5"><Badge tone={round.high_issues > 0 ? 'red' : 'neutral'}>HIGH {round.high_issues}</Badge><Badge tone={round.medium_issues > 0 ? 'amber' : 'neutral'}>MEDIUM {round.medium_issues}</Badge><Badge>LOW {round.low_issues}</Badge></div>
              </div>
            ))}
          </div>
          <div className="mt-4 grid gap-2 lg:grid-cols-3">
            <div className="rounded-lg bg-zinc-950/50 p-3"><p className="text-[11px] font-medium text-zinc-400">首末轮均报告</p><p className="mt-1 text-[11px] leading-5 text-zinc-600">{persistentQualityLabels.join('、') || '无同名类别'}</p></div>
            <div className="rounded-lg bg-zinc-950/50 p-3"><p className="text-[11px] font-medium text-zinc-400">末轮未再报告</p><p className="mt-1 text-[11px] leading-5 text-zinc-600">{absentQualityLabels.join('、') || '无'}</p></div>
            <div className="rounded-lg bg-zinc-950/50 p-3"><p className="text-[11px] font-medium text-zinc-400">末轮新报告</p><p className="mt-1 text-[11px] leading-5 text-zinc-600">{newQualityLabels.join('、') || '无'}</p></div>
          </div>
          {qualityHistory.comparison.latest_report_regression_risk && <p className="mt-3 rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs leading-5 text-amber-100">最近一轮相比上一轮的报告变化：总数 {latestTotalIssueDelta >= 0 ? '+' : ''}{latestTotalIssueDelta}，HIGH {latestHighIssueDelta >= 0 ? '+' : ''}{latestHighIssueDelta}。Director Agent 会建议比较最近两版，但不会替用户做强制选择。</p>}
          {revisionJudge && (
            <div className="mt-3 rounded-lg border border-cyan-500/20 bg-cyan-500/5 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-xs font-medium text-cyan-100">Revision Judge 建议</p>
                <Badge tone="blue">优先考虑{revisionJudge.recommended_revision_round === 0 ? '初稿' : `第 ${revisionJudge.recommended_revision_round} 轮修订稿`}</Badge>
                <Badge>{revisionJudge.confidence} 置信度</Badge>
              </div>
              <p className="mt-2 text-xs leading-5 text-zinc-300">{revisionJudge.summary}</p>
              {revisionJudgeEvidence.length > 0 && <div className="mt-3"><p className="text-[11px] font-medium text-zinc-400">判断依据</p><ul className="mt-1 space-y-1 text-[11px] leading-5 text-zinc-500">{revisionJudgeEvidence.map((item) => <li key={item}>· {item}</li>)}</ul></div>}
              {revisionJudgeTradeoffs.length > 0 && <div className="mt-3"><p className="text-[11px] font-medium text-zinc-400">取舍</p><ul className="mt-1 space-y-1 text-[11px] leading-5 text-zinc-500">{revisionJudgeTradeoffs.map((item) => <li key={item}>· {item}</li>)}</ul></div>}
              <p className="mt-3 text-[11px] leading-5 text-cyan-200/70">该建议可忽略；当前流程没有自动替换大纲，仍按最新版完成后续规划。</p>
            </div>
          )}
          <p className="mt-3 text-[11px] leading-5 text-zinc-600">这是跨轮审稿描述，不是自动打分或通过门；用户可继续修改，也可忽略建议。</p>
        </section>
      )}
      {qualityError && <p className="mb-5 rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs text-amber-200">暂时无法读取审稿轨迹：{qualityError}</p>}
      {review && (
        <section className={`mb-5 rounded-xl border p-4 ${reviewNeedsStoryRevision ? 'border-amber-500/20 bg-amber-500/5' : 'border-emerald-500/20 bg-emerald-500/5'}`}>
          <div className="flex items-start justify-between gap-3">
            <div><h3 className="text-sm font-medium text-white">Script Critic 剧本审阅</h3><p className="mt-1 text-xs leading-5 text-zinc-400">{review.summary}</p></div>
            <Badge tone={reviewNeedsStoryRevision ? 'amber' : 'green'}>{reviewBadge}</Badge>
          </div>
          {reviewStrengths.length > 0 && <div className="mt-3 flex flex-wrap gap-2">{reviewStrengths.map((strength) => <Badge key={strength} tone="green">{strength}</Badge>)}</div>}
          {reviewIssues.length > 0 && (
            <div className="mt-4 space-y-2">
              {reviewIssues.map((issue, index) => {
                const sceneSequences = issue.scene_sequences ?? []
                const tone = issue.severity === 'HIGH' ? 'red' : issue.severity === 'MEDIUM' ? 'amber' : 'neutral'
                return (
                  <div key={`${issue.category}-${index}`} className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={tone}>{issue.severity}</Badge>
                      <span className="text-xs font-medium text-zinc-300">{issue.category}</span>
                      {sceneSequences.length > 0 && <span className="text-[11px] text-zinc-600">场次 {sceneSequences.join('、')}</span>}
                      {routedIssueOwners.get(index + 1) && <span className="text-[11px] text-cyan-500">交给 {routedIssueOwners.get(index + 1)}</span>}
                    </div>
                    <p className="mt-2 text-xs leading-5 text-zinc-400">{issue.observation}</p>
                    <p className="mt-1 text-xs leading-5 text-blue-300">建议：{issue.recommendation}</p>
                  </div>
                )
              })}
            </div>
          )}
          <p className="mt-3 text-[11px] text-zinc-600">Critic 只报告观察；当前 Production Skill 的 Review Router 决定默认修订或下放。用户始终可以继续、编辑、让 Agent 改写或忽略。</p>
        </section>
      )}
      <section className="mb-5">
        <h3 className="mb-3 text-sm font-medium text-white">场次结构</h3>
        <div className="grid gap-3 xl:grid-cols-2">
          {script.structured_scenes.map((scene, index) => (
            <div key={scene.id} className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-[11px] text-zinc-600">场次 {index + 1}</p>
                  <h4 className="mt-1 text-sm font-medium text-zinc-200">{scene.heading}</h4>
                </div>
                {scene.target_duration_seconds != null && <Badge tone="blue">约 {scene.target_duration_seconds} 秒</Badge>}
              </div>
              <p className="mt-2 text-xs leading-5 text-zinc-400">{scene.summary}</p>
              {scene.narrative_purpose && <p className="mt-2 text-xs leading-5 text-blue-300">作用：{scene.narrative_purpose}</p>}
              <p className="mt-2 text-[11px] text-zinc-600">对白 {scene.line_ids.length} 句</p>
            </div>
          ))}
        </div>
      </section>
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
  onInspect,
}: {
  project: ProjectAggregate
  onDrain: () => void
  onTaskAction: (task: GenerationTask, action: 'advance' | 'retry' | 'reconcile' | 'recover') => void
  onCancel: (task: GenerationTask) => void
  onSelect: (takeId: string) => void
  onInspect: (takeId: string) => void
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
              {takes.map((take) => {
                const inspection = [...(project.video_inspections ?? [])]
                  .reverse()
                  .find((item) => item.take_id === take.id)
                return (
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
                    <button className={`${secondaryButton} mt-2 w-full`} onClick={() => onInspect(take.id)}>
                      <ScanSearch className="h-3.5 w-3.5" />
                      {inspection ? '重新抽帧检查' : '抽帧检查连续性'}
                    </button>
                    {inspection && (
                      <div className="mt-2 rounded-lg border border-zinc-800 bg-zinc-950/70 p-2.5">
                        <div className="flex items-center justify-between gap-2">
                          <Badge tone={(inspection.issues?.length ?? 0) > 0 ? 'amber' : inspection.semantic_status === 'COMPLETE' ? 'green' : 'neutral'}>{inspection.semantic_status}</Badge>
                          <span className="text-[10px] text-zinc-600">{inspection.samples.length} 帧</span>
                        </div>
                        <p className="mt-1.5 line-clamp-3 text-[11px] leading-4 text-zinc-500">{inspection.summary}</p>
                      </div>
                    )}
                  </div>
                </div>
                )
              })}
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

function QaPanel({
  project,
  onInspectAudio,
  onReviewEpisode,
}: {
  project: ProjectAggregate
  onInspectAudio: () => void
  onReviewEpisode: () => void
}) {
  const exported = currentExport(project)
  const currentTakes = project.takes.filter((take) => take.freshness === 'CURRENT')
  const hardPass = currentTakes.filter((take) => take.qa_report.hard_gate_passed).length
  const videoInspections = project.video_inspections ?? []
  const audioInspections = project.audio_inspections ?? []
  const episodeReviews = project.episode_reviews ?? []
  const latestEpisodeReview = episodeReviews.at(-1)
  const latestEpisodeFindings = latestEpisodeReview?.findings ?? []
  const latestSceneReviews = latestEpisodeReview?.scene_reviews ?? []
  const latestEpisodeWarnings = latestEpisodeReview?.warnings ?? []
  const appliedEpisodeRepairs = latestEpisodeReview?.repair_applied_shot_ids ?? []
  const skippedEpisodeRepairs = latestEpisodeReview?.repair_skipped_shot_ids ?? []
  const latestAudio = audioInspections.at(-1)
  const latestAudioClips = latestAudio?.clip_inspections ?? []
  const latestDialogueChecks = latestAudio?.dialogue_checks ?? []
  const latestSpeakerChecks = latestAudio?.speaker_checks ?? []
  const latestAudioIssues = latestAudio?.issues ?? []
  return (
    <div>
      <SectionTitle title="质量检查" description="技术检查和 Agent Reviewer 提供可定位证据与可忽略建议；真人表演、观看意愿和自动审阅漏报仍以真实成片观看为准。" />
      <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-7">
        <Metric label="当前 Take" value={currentTakes.length} detail="旧版本不丢失" />
        <Metric label="硬门槛" value={`${hardPass}/${currentTakes.length}`} detail="Mock 技术门槛" />
        <Metric label="失效 Take" value={project.progress.stale_takes} detail="仍可复现旧成片" />
        <Metric label="视频观察" value={videoInspections.length} detail="适应性抽帧报告" />
        <Metric label="整集审阅" value={episodeReviews.length} detail="分场汇总与导演建议" />
        <Metric label="音频 QA" value={audioInspections.length} detail="对白、声线与字幕" />
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
      <div className="mt-5 rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2"><h3 className="font-medium text-white">整集分层审阅</h3><Badge tone="blue">Agent Tool</Badge></div>
            <p className="mt-1 max-w-3xl text-xs leading-5 text-zinc-500">Scene Reviewer 只读取当前场次的入选 Take、抽帧证据和锁定剧本，Episode Reviewer 再汇总跨场因果、连续性、人物道具状态、声音来源语义与节奏。建议可忽略，不是创意评分门或导出审批门。</p>
          </div>
          <button className={secondaryButton} disabled={!currentManifest(project)} onClick={onReviewEpisode}><ScanSearch className="h-4 w-4" />{latestEpisodeReview?.status === 'RUNNING' ? '继续整集审阅' : '审阅当前整集'}</button>
        </div>
        {!latestEpisodeReview ? <p className="mt-8 text-center text-sm text-zinc-600">时间线就绪后可运行整集审阅；AUTO_DRAFT 会自动运行，并只对明确关联的镜头提出局部修复。</p> : (
          <div className="mt-4 space-y-4">
            <div className="rounded-lg border border-zinc-800 bg-zinc-950/70 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={latestEpisodeReview.status === 'COMPLETE' ? 'green' : latestEpisodeReview.status === 'FAILED' ? 'amber' : latestEpisodeReview.status === 'UNAVAILABLE' ? 'neutral' : 'blue'}>{latestEpisodeReview.status}</Badge>
                <span className="text-[11px] text-zinc-600">{latestSceneReviews.length} 个场次 · {latestEpisodeFindings.length} 个建议 · {appliedEpisodeRepairs.length} 个镜头已局部返工</span>
                {skippedEpisodeRepairs.length > 0 && <span className="text-[11px] text-amber-300">{skippedEpisodeRepairs.length} 个建议已跳过</span>}
              </div>
              <p className="mt-2 text-xs leading-5 text-zinc-400">{latestEpisodeReview.summary}</p>
              {latestEpisodeWarnings.length > 0 && <ul className="mt-2 space-y-1 text-[11px] leading-5 text-amber-200/80">{latestEpisodeWarnings.map((warning) => <li key={warning}>· {warning}</li>)}</ul>}
            </div>
            {latestEpisodeFindings.length === 0 ? <p className="text-center text-sm text-zinc-600">当前报告没有提出整集级问题；系统不会为了凑返工而改镜头。</p> : (
              <div className="grid gap-3 lg:grid-cols-2">
                {latestEpisodeFindings.map((finding) => {
                  const tone = finding.issue.severity === 'HIGH' ? 'red' : finding.issue.severity === 'MEDIUM' ? 'amber' : 'neutral'
                  const sceneIds = finding.scene_ids ?? []
                  const shotIds = finding.shot_ids ?? []
                  return (
                    <div key={finding.id} className="rounded-lg border border-zinc-800 bg-zinc-950/70 p-3">
                      <div className="flex flex-wrap items-center gap-2"><Badge tone={tone}>{finding.issue.severity}</Badge><span className="text-xs font-medium text-zinc-300">{finding.dimension}</span>{finding.repair_recommended && <Badge tone="blue">建议局部返工</Badge>}</div>
                      <p className="mt-2 text-xs leading-5 text-zinc-300">{finding.issue.message}</p>
                      <p className="mt-1 text-xs leading-5 text-zinc-500">证据：{finding.evidence}</p>
                      <p className="mt-1 text-xs leading-5 text-blue-300">建议：{finding.recommended_action}</p>
                      {(sceneIds.length > 0 || shotIds.length > 0) && <p className="mt-2 text-[11px] text-zinc-600">关联场次 {sceneIds.join('、') || '—'} · 镜头 {shotIds.join('、') || '—'}</p>}
                    </div>
                  )
                })}
              </div>
            )}
            <p className="text-[11px] leading-5 text-zinc-600">该报告始终是可忽略建议；失败或 Provider 不可用也不会阻止时间线导出。它不会自动触发 STT、声纹或音频质量检查。</p>
          </div>
        )}
      </div>
      <div className="mt-5 rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
        <div className="flex items-center justify-between gap-3"><div><h3 className="font-medium text-white">视频观察工具</h3><p className="mt-1 text-xs text-zinc-500">长镜头按全时段覆盖与视觉变化抽帧；相邻入选镜头额外生成边界接触表。报告只提供证据和问题，不替用户决定。</p></div><Badge tone="blue">Agent Tool</Badge></div>
        {videoInspections.length === 0 ? <p className="mt-8 text-center text-sm text-zinc-600">在候选页对任意 Take 运行“抽帧检查连续性”。</p> : (
          <div className="mt-4 grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
            {[...videoInspections].reverse().slice(0, 6).map((inspection) => (
              <div key={inspection.id} className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-950/70">
                <img src={pathToFileUrl(inspection.contact_sheet_path)} className="aspect-video w-full object-cover object-top" alt={`${inspection.shot_id} 抽帧接触表`} />
                <div className="p-3">
                  <div className="flex items-center justify-between gap-2"><Badge tone={(inspection.issues?.length ?? 0) > 0 ? 'amber' : inspection.semantic_status === 'COMPLETE' ? 'green' : 'neutral'}>{inspection.semantic_status}</Badge><span className="text-[10px] text-zinc-600">{inspection.scopes.join(' + ')}</span></div>
                  <p className="mt-2 text-xs leading-5 text-zinc-400">{inspection.summary}</p>
                  <div className="mt-2 flex items-center justify-between"><span className="text-[11px] text-zinc-600">{inspection.samples.length} 帧 · {inspection.issues?.length ?? 0} 个问题</span><button className={subtleButton} onClick={() => window.electronAPI.showItemInFolder({ filePath: inspection.contact_sheet_path })}><FolderOpen className="h-3 w-3" />文件</button></div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="mt-5 rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div><h3 className="font-medium text-white">音频与对白检查工具</h3><p className="mt-1 text-xs leading-5 text-zinc-500">逐镜提取原生音轨，检查静音、削波、时长与字幕时间；可用语音服务时再核对锁定对白和跨镜声线。结果是 Director 可忽略建议。</p></div>
          <button className={secondaryButton} disabled={!currentManifest(project)} onClick={onInspectAudio}><FileAudio className="h-4 w-4" />重新检查当前时间线</button>
        </div>
        {!latestAudio ? <p className="mt-8 text-center text-sm text-zinc-600">生成时间线后可按需手动运行 AudioInspection；自动流程仅保留可选声音输入，不默认执行声音 QA。</p> : (
          <div className="mt-4 space-y-4">
            <div className="rounded-lg border border-zinc-800 bg-zinc-950/70 p-3">
              <div className="flex flex-wrap items-center gap-2"><Badge tone={latestAudio.semantic_status === 'COMPLETE' ? 'green' : latestAudio.semantic_status === 'FAILED' ? 'amber' : 'neutral'}>{latestAudio.semantic_status}</Badge><span className="text-[11px] text-zinc-600">{latestAudioClips.length} 音轨 · {latestDialogueChecks.length} 句对白 · {latestAudioIssues.length} 个问题</span></div>
              <p className="mt-2 text-xs leading-5 text-zinc-400">{latestAudio.summary}</p>
            </div>
            <div className="grid gap-3 lg:grid-cols-2">
              {latestDialogueChecks.map((check) => (
                <div key={check.cue_id} className="rounded-lg border border-zinc-800 bg-zinc-950/70 p-3">
                  <div className="flex items-center justify-between gap-2"><span className="text-xs font-medium text-zinc-300">{check.expected_speaker}</span><Badge tone={check.exact_match === true ? 'green' : check.exact_match === false ? 'amber' : 'neutral'}>{check.exact_match === true ? '逐字一致' : check.exact_match === false ? `CER ${(check.character_error_rate ?? 0).toFixed(3)}` : check.transcription_status}</Badge></div>
                  <p className="mt-2 text-xs text-zinc-400">锁定：{check.expected_text}</p>
                  <p className="mt-1 text-xs text-zinc-500">识别：{check.transcript_text ?? '暂无 STT 结果'}</p>
                </div>
              ))}
            </div>
            {latestSpeakerChecks.length > 0 && <div className="grid gap-2 md:grid-cols-2">{latestSpeakerChecks.map((check) => <div key={check.speaker} className="rounded-lg border border-zinc-800 bg-zinc-950/70 px-3 py-2.5"><div className="flex items-center justify-between"><span className="text-xs font-medium text-zinc-300">{check.speaker} · {check.voice_identity ?? '未命名声线'}</span><Badge tone={check.assessment === 'PASSED' ? 'green' : check.assessment === 'REVIEW' ? 'amber' : 'neutral'}>{check.assessment}</Badge></div><p className="mt-1 text-[11px] leading-5 text-zinc-600">{check.detail}</p></div>)}</div>}
          </div>
        )}
      </div>
      <div className="mt-5 rounded-xl border border-amber-500/20 bg-amber-500/5 p-4">
        <div className="flex gap-3"><AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" /><div><p className="text-sm font-medium text-amber-100">质量结论边界</p><p className="mt-1 text-xs leading-5 text-zinc-400">Mock 分数只证明工程链路。H3 实验台会保存真实 768P 输出与单人硬门槛审片；路线稳定性和最终质量仍需按固定五句台词、身份/道具连续性、声音、口型和至少三人盲评完成。</p></div></div>
      </div>
    </div>
  )
}

function PlanningRunPanel({
  run,
  onRetry,
}: {
  run: PlanningRunRecord
  onRetry: () => void
}) {
  const directedScenes = run.directed_scene_json ?? []
  const writtenScenes = run.scene_script_json ?? []
  const completedSteps = run.completed_steps ?? []
  const reviewIssues = run.review_report?.issues ?? []
  const warnings = run.warnings ?? []
  const completedScenes = Math.min(
    directedScenes.length,
    run.total_scenes,
  )
  const targetSeconds = run.request.target_duration_seconds ?? (
    run.request.production_profile === 'DOUYIN_SHORT_DRAMA' ? 300 : 60
  )
  const durationLabel = targetSeconds >= 60
    ? `${Number((targetSeconds / 60).toFixed(1))} 分钟`
    : `${targetSeconds} 秒`
  const stages: PlanningRunRecord['current_step'][] = [
    'STORY_ARCHITECT',
    'SCENE_WRITER',
    'SCRIPT_CRITIC',
    'STORY_REVISION',
    'REVISION_JUDGE',
    'SCENE_DIRECTOR',
    'FINALIZE',
  ]
  const currentIndex = run.current_step === 'COMPLETE'
    ? stages.length
    : stages.indexOf(run.current_step)
  const needsAttention = run.status === 'NEEDS_ATTENTION'
  return (
    <div className="mx-auto flex min-h-full max-w-5xl items-center px-8 py-12">
      <div className="w-full overflow-hidden rounded-2xl border border-blue-500/20 bg-gradient-to-br from-blue-500/10 via-zinc-950 to-cyan-500/5 shadow-2xl">
        <div className="border-b border-zinc-800/80 p-6 sm:p-8">
          <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <div className="flex flex-wrap items-center gap-2"><Badge tone="blue">可恢复分层规划</Badge><Badge>{durationLabel}</Badge><Badge>{run.request.production_profile}</Badge></div>
              <h1 className="mt-4 text-2xl font-semibold text-white">{run.request.name ?? '新短剧'}正在进行剧本规划</h1>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-400">当前阶段只处理故事结构、因果、对白、场次和连续性规划，不调用 GPT Image 或 MiniMax-H3。每个 Agent 阶段和每个场次完成后都会保存，关闭应用后也能从当前节点继续。</p>
            </div>
            <div className={`flex min-w-56 items-center justify-center gap-2 rounded-xl border px-4 py-3 text-sm ${needsAttention ? 'border-amber-500/30 bg-amber-500/10 text-amber-100' : 'border-blue-500/30 bg-blue-500/10 text-blue-100'}`}>
              {needsAttention ? <AlertCircle className="h-4 w-4" /> : <Loader2 className="h-4 w-4 animate-spin" />}
              {needsAttention ? '当前节点需要重试' : planningStepLabels[run.current_step]}
            </div>
          </div>
        </div>
        <div className="grid gap-6 p-6 sm:p-8 lg:grid-cols-[1.4fr_0.8fr]">
          <div>
            <h2 className="text-sm font-semibold text-white">分层规划进度</h2>
            <div className="mt-4 space-y-2">
              {stages.map((step, index) => {
                const skipped = step === 'REVISION_JUDGE'
                  && index < currentIndex
                  && !completedSteps.includes(step)
                const complete = completedSteps.includes(step)
                  || (step !== 'REVISION_JUDGE' && index < currentIndex)
                const active = step === run.current_step
                return (
                  <div key={step} className={`flex items-center gap-3 rounded-xl border px-4 py-3 ${active ? 'border-blue-500/30 bg-blue-500/10' : 'border-zinc-800 bg-zinc-950/60'}`}>
                    {complete ? <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" /> : active ? <Loader2 className="h-4 w-4 shrink-0 animate-spin text-blue-400" /> : <CircleDot className="h-4 w-4 shrink-0 text-zinc-700" />}
                    <div className="min-w-0 flex-1"><p className={`text-sm ${active ? 'font-medium text-white' : complete ? 'text-zinc-300' : 'text-zinc-600'}`}>{planningStepLabels[step]}</p>{skipped && <p className="mt-1 text-[11px] text-zinc-600">最新复审没有触发回退风险，本次无需比较版本</p>}{step === 'SCENE_WRITER' && run.total_scenes > 0 && <p className="mt-1 text-[11px] text-zinc-500">已完成 {Math.min(writtenScenes.length, run.total_scenes)}/{run.total_scenes} 场；只读取近场全文、较早摘要和相关剧本文档</p>}{step === 'SCENE_DIRECTOR' && run.total_scenes > 0 && <p className="mt-1 text-[11px] text-zinc-500">已完成 {completedScenes}/{run.total_scenes} 场；一次只展开一场并传递上一场实际交接状态</p>}</div>
                  </div>
                )
              })}
            </div>
          </div>
          <div className="space-y-4">
            <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-4">
              <p className="text-xs font-medium text-zinc-300">当前剧本检查</p>
              <div className="mt-3 grid grid-cols-2 gap-2 text-xs"><div className="rounded-lg bg-zinc-900 p-3"><p className="text-zinc-600">修订轮次</p><p className="mt-1 font-semibold text-white">{run.revision_round}/{run.max_revision_rounds}</p></div><div className="rounded-lg bg-zinc-900 p-3"><p className="text-zinc-600">审稿建议</p><p className="mt-1 font-semibold text-white">{reviewIssues.length} 项</p></div></div>
              <p className="mt-3 text-[11px] leading-5 text-zinc-500">Critic 只给观察；Review Router 按当前 Production Skill 判断哪些需要剧本修订、哪些下放给导演/资产/视频/时间线工具。默认可自动继续，用户也可随时改写或忽略。</p>
            </div>
            {warnings.length > 0 && <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-4"><p className="text-xs font-medium text-amber-100">最近记录</p><p className="mt-2 text-xs leading-5 text-zinc-400">{warnings.at(-1)}</p></div>}
            {needsAttention && <div className="rounded-xl border border-red-500/20 bg-red-500/5 p-4"><p className="text-xs font-medium text-red-200">{run.error_message ?? '当前 Agent 节点未完成'}</p><p className="mt-2 text-[11px] leading-5 text-zinc-500">重试只会重跑当前阶段或当前场次，已经完成的内容不会从头生成。</p><button className={`${primaryButton} mt-4 w-full`} onClick={onRetry}><RefreshCw className="h-4 w-4" />重试当前节点</button></div>}
          </div>
        </div>
      </div>
    </div>
  )
}

function CreateProjectPanel({
  onCreate,
  onFixture,
}: {
  onCreate: (
    name: string,
    productionProfile: ProductionProfile,
    targetDurationSeconds: number,
    mode: SourceMode,
    input: string,
    runMode: RunMode,
    executionTarget: AutonomousExecutionTarget,
    images: CreativeInputImage[],
  ) => void
  onFixture: () => void
}) {
  const [name, setName] = useState('新的都市短剧')
  const [productionProfile, setProductionProfile] = useState<ProductionProfile>('DOUYIN_SHORT_DRAMA')
  const [targetDurationValue, setTargetDurationValue] = useState('5')
  const [targetDurationUnit, setTargetDurationUnit] = useState<'MINUTES' | 'SECONDS'>('MINUTES')
  const [sourceMode, setSourceMode] = useState<SourceMode>('AUTO_ORIGINAL')
  const [runMode, setRunMode] = useState<RunMode>('AUTO_DRAFT')
  const [executionTarget, setExecutionTarget] = useState<AutonomousExecutionTarget>('REAL_H3')
  const [input, setInput] = useState('')
  const [imagePaths, setImagePaths] = useState<string[]>([])
  const pickImages = async () => {
    const paths = await window.electronAPI?.showOpenFileDialog({
      title: '选择视觉种子',
      filters: [{ name: '图片', extensions: ['png', 'jpg', 'jpeg', 'webp', 'heic', 'heif'] }],
      properties: ['multiSelections'],
    })
    if (!paths?.length) return
    setImagePaths((current) => [...new Set([...current, ...paths])].slice(0, 8))
  }
  const images: CreativeInputImage[] = imagePaths.map((localPath, index) => ({
    local_path: localPath,
    external_url: null,
    descriptor: `用户提供的视觉种子 ${index + 1}：${localPath.split(/[\\/]/).at(-1) ?? '图片'}`,
  }))
  const requiresText = sourceMode === 'IDEA_EXPANSION' || sourceMode === 'SCRIPT_LOCKED'
  const requiresImage = sourceMode === 'IMAGE_SEEDED'
  const durationNumber = Number(targetDurationValue)
  const targetDurationSeconds = Math.round(durationNumber * (targetDurationUnit === 'MINUTES' ? 60 : 1))
  const hasValidTargetDuration = Number.isFinite(targetDurationSeconds) && targetDurationSeconds >= 4
  const canSubmit = Boolean(name.trim()) && hasValidTargetDuration && (!requiresText || Boolean(input.trim())) && (!requiresImage || images.length > 0)
  const selectProductionProfile = (nextProfile: ProductionProfile) => {
    if (nextProfile === productionProfile) return
    setProductionProfile(nextProfile)
    if (nextProfile === 'DOUYIN_SHORT_DRAMA') {
      setTargetDurationValue('5')
      setTargetDurationUnit('MINUTES')
      return
    }
    setTargetDurationValue('60')
    setTargetDurationUnit('SECONDS')
  }
  const selectTargetDurationUnit = (nextUnit: 'MINUTES' | 'SECONDS') => {
    if (nextUnit === targetDurationUnit) return
    if (Number.isFinite(durationNumber) && durationNumber > 0) {
      const converted = nextUnit === 'MINUTES' ? durationNumber / 60 : durationNumber * 60
      setTargetDurationValue(String(Number(converted.toFixed(2))))
    }
    setTargetDurationUnit(nextUnit)
  }
  const inputLabel = sourceMode === 'SCRIPT_LOCKED'
    ? '锁定剧本'
    : sourceMode === 'IDEA_EXPANSION'
      ? '一句话创意'
      : sourceMode === 'IMAGE_SEEDED'
        ? '图片补充说明（可选）'
        : '创作偏好（可选）'
  return (
    <div className="mx-auto flex h-full max-w-5xl items-center px-8 py-12">
      <div className="grid w-full gap-8 xl:grid-cols-[1fr_1.1fr]">
        <div className="flex flex-col justify-center">
          <Badge tone="blue">AI 短剧制片工作台</Badge>
          <h1 className="mt-5 text-4xl font-semibold leading-tight text-white">先选择创作节奏，再让任意输入进入同一制片闭环</h1>
          <p className="mt-4 text-sm leading-7 text-zinc-400">通用视频允许氛围、留白和有意义的长镜头；抖音短剧强调冲突前置、悬念链与爆点回收。两者共享同一套资产、Director、H3、QA 和时间线工具。</p>
          <button className={`${secondaryButton} mt-8 w-fit px-5 py-3`} onClick={onFixture}><Clapperboard className="h-4 w-4 text-blue-400" />打开固定验收片《别相信我》</button>
        </div>
        <div className="rounded-2xl border border-zinc-800 bg-zinc-900/70 p-6 shadow-2xl">
          <h2 className="text-lg font-semibold text-white">新建项目</h2>
          <div className="mt-5 space-y-4">
            <label className="block"><span className="mb-1.5 block text-xs text-zinc-500">项目名</span><input value={name} onChange={(event) => setName(event.target.value)} className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-200 outline-none focus:border-blue-500" /></label>
            <div>
              <span className="mb-1.5 block text-xs text-zinc-500">制作类型</span>
              <div className="grid grid-cols-2 gap-3">
                <button type="button" onClick={() => selectProductionProfile('DOUYIN_SHORT_DRAMA')} className={`rounded-xl border p-3 text-left transition ${productionProfile === 'DOUYIN_SHORT_DRAMA' ? 'border-blue-500 bg-blue-500/10' : 'border-zinc-700 bg-zinc-950 hover:border-zinc-600'}`}><p className="text-sm font-medium text-zinc-100">抖音短剧</p><p className="mt-1 text-[11px] leading-4 text-zinc-500">冲突前置、悬念推进、反转回收；默认规划 5 分钟。</p></button>
                <button type="button" onClick={() => selectProductionProfile('GENERAL_VIDEO')} className={`rounded-xl border p-3 text-left transition ${productionProfile === 'GENERAL_VIDEO' ? 'border-blue-500 bg-blue-500/10' : 'border-zinc-700 bg-zinc-950 hover:border-zinc-600'}`}><p className="text-sm font-medium text-zinc-100">通用视频</p><p className="mt-1 text-[11px] leading-4 text-zinc-500">节奏服从内容，默认 60 秒并可自由调整。</p></button>
              </div>
            </div>
            <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-3">
              <div className="flex items-start justify-between gap-3">
                <div><p className="text-xs font-medium text-zinc-300">目标成片时长</p><p className="mt-1 text-[11px] leading-4 text-zinc-600">Agent 会据此展开剧本、场景与分镜；实际成片允许合理浮动，不作为阻断生成的硬门。</p></div>
                {hasValidTargetDuration && <Badge tone="blue">约 {targetDurationSeconds >= 60 ? `${Number((targetDurationSeconds / 60).toFixed(1))} 分钟` : `${targetDurationSeconds} 秒`}</Badge>}
              </div>
              {productionProfile === 'DOUYIN_SHORT_DRAMA' && (
                <div className="mt-3">
                  <p className="mb-1.5 text-[11px] text-zinc-600">常用快捷值（可选，也可在下方填写任意时长）</p>
                  <div className="grid grid-cols-4 gap-2">
                    {[3, 5, 10, 15].map((minutes) => {
                      const selected = targetDurationUnit === 'MINUTES' && durationNumber === minutes
                      return <button key={minutes} type="button" onClick={() => { setTargetDurationValue(String(minutes)); setTargetDurationUnit('MINUTES') }} className={`rounded-lg border px-2 py-2 text-xs font-medium transition ${selected ? 'border-blue-500 bg-blue-500/10 text-blue-200' : 'border-zinc-800 bg-zinc-900 text-zinc-500 hover:border-zinc-700 hover:text-zinc-300'}`}>{minutes} 分钟</button>
                    })}
                  </div>
                </div>
              )}
              <div className="mt-3 grid grid-cols-[minmax(0,1fr)_120px] gap-2">
                <input type="number" min={targetDurationUnit === 'MINUTES' ? 0.0667 : 4} step={targetDurationUnit === 'MINUTES' ? 0.1 : 1} value={targetDurationValue} onChange={(event) => setTargetDurationValue(event.target.value)} aria-label="自定义目标时长" className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-200 outline-none focus:border-blue-500" />
                <select value={targetDurationUnit} onChange={(event) => selectTargetDurationUnit(event.target.value as 'MINUTES' | 'SECONDS')} aria-label="目标时长单位" className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-200 outline-none"><option value="MINUTES">分钟</option><option value="SECONDS">秒</option></select>
              </div>
              {!hasValidTargetDuration && <p className="mt-2 text-[11px] text-red-300">请输入不少于 4 秒的任意目标时长。</p>}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <label className="block"><span className="mb-1.5 block text-xs text-zinc-500">输入契约</span><select value={sourceMode} onChange={(event) => setSourceMode(event.target.value as SourceMode)} className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-200 outline-none"><option value="AUTO_ORIGINAL">完全自动创作</option><option value="IMAGE_SEEDED">图片启发创作</option><option value="IDEA_EXPANSION">一句话扩写</option><option value="SCRIPT_LOCKED">已有剧本锁定</option></select></label>
              <label className="block"><span className="mb-1.5 block text-xs text-zinc-500">运行模式</span><select value={runMode} onChange={(event) => setRunMode(event.target.value as RunMode)} className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-200 outline-none"><option value="AUTO_DRAFT">自动到底</option><option value="GUIDED">可停靠引导</option></select></label>
            </div>
            {runMode === 'AUTO_DRAFT' && <label className="block"><span className="mb-1.5 block text-xs text-zinc-500">成片目标</span><select value={executionTarget} onChange={(event) => setExecutionTarget(event.target.value as AutonomousExecutionTarget)} className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-200 outline-none"><option value="REAL_H3">真人 H3 自动草稿（调用已配置的付费资源）</option><option value="MOCK">无付费流程占位草稿</option></select></label>}
            <label className="block"><span className="mb-1.5 block text-xs text-zinc-500">{inputLabel}</span><textarea value={input} onChange={(event) => setInput(event.target.value)} placeholder={requiresText ? '请输入内容' : '可以留空，让 Agent 自行创作'} className="min-h-28 w-full resize-none rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm leading-6 text-zinc-200 outline-none focus:border-blue-500" /></label>
            <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-3">
              <div className="flex items-center justify-between gap-3">
                <div><p className="text-xs font-medium text-zinc-300">视觉种子</p><p className="mt-1 text-[11px] text-zinc-600">可与任意输入模式组合，最多 8 张。</p></div>
                <button type="button" className={secondaryButton} onClick={() => void pickImages()}><ImagePlus className="h-3.5 w-3.5" />选择图片</button>
              </div>
              {imagePaths.length > 0 && <div className="mt-3 space-y-1.5">{imagePaths.map((path) => <div key={path} className="flex items-center justify-between gap-3 rounded-lg bg-zinc-900 px-2.5 py-2"><span className="truncate text-[11px] text-zinc-400">{path.split(/[\\/]/).at(-1)}</span><button type="button" className="text-zinc-600 hover:text-red-300" onClick={() => setImagePaths((current) => current.filter((item) => item !== path))}><X className="h-3.5 w-3.5" /></button></div>)}</div>}
            </div>
            <button className={`${primaryButton} w-full py-3`} disabled={!canSubmit} onClick={() => onCreate(name.trim(), productionProfile, targetDurationSeconds, sourceMode, input.trim(), runMode, executionTarget, images)}><WandSparkles className="h-4 w-4" />{runMode === 'AUTO_DRAFT' ? executionTarget === 'REAL_H3' ? '启动无人值守真人草稿' : '生成完整 Mock 草稿' : '建立可编辑制片项目'}</button>
          </div>
          <p className="mt-4 text-center text-[11px] text-zinc-600">没有配置 GPT 时使用本地可复现策划回退；真实质量结论不会由 Mock 冒充。</p>
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
  const [planningRun, setPlanningRun] = useState<PlanningRunRecord | null>(null)
  const [planningPollTick, setPlanningPollTick] = useState(0)
  const [autoPollTick, setAutoPollTick] = useState(0)
  const planningAdvanceInFlight = useRef(false)
  const autoAdvanceInFlight = useRef(false)
  const mockAutoDraftInFlight = useRef(false)

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

  const loadPlanningRuns = useCallback(async () => {
    const result = await ShortDramaApi.listPlanningRuns()
    if (!result.ok) {
      setError(result.error.message)
      return []
    }
    return result.data.runs
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
    setPlanningRun(null)
    setShowCreate(false)
  }, [])

  useEffect(() => {
    let cancelled = false
    const initialize = async () => {
      const [listed, planningRuns] = await Promise.all([
        loadProjects(),
        loadPlanningRuns(),
      ])
      if (cancelled) return
      const resumable = planningRuns.find((run) => (
        run.status === 'RUNNING' || run.status === 'NEEDS_ATTENTION'
      ))
      if (resumable) {
        setPlanningRun(resumable)
        setShowCreate(true)
        return
      }
      if (listed.length === 0) return
      await loadProject(listed[0].id)
    }
    void initialize()
    return () => { cancelled = true }
  }, [loadPlanningRuns, loadProject, loadProjects])

  const refresh = useCallback(async () => {
    if (!activeProjectId) return
    const result = await ShortDramaApi.getProject(activeProjectId)
    if (result.ok) setProject(result.data)
    else setError(result.error.message)
  }, [activeProjectId])

  const acceptPlanningResult = useCallback((data: PlanningRunResponse) => {
    setPlanningRun(data.run)
    if (data.run.status !== 'COMPLETE' || !data.project) return
    const projectId = data.project.document.project.id
    setProject(data.project)
    setActiveProjectId(projectId)
    setPlanningRun(null)
    setShowCreate(false)
    setTab(data.run.request.run_mode === 'GUIDED' ? 'script' : 'overview')
    void loadProjects()
    if (data.run.request.run_mode === 'GUIDED') {
      setNotice('分层剧本规划完成，可在剧本页检查和修改')
      return
    }
    if (data.run.request.execution_target === 'REAL_H3') {
      setNotice('分层剧本规划完成，自动制片开始继续推进')
      return
    }
    mockAutoDraftInFlight.current = true
    setBusyLabel('剧本已完成，继续生成 Mock 成片')
    void ShortDramaApi.autoDraft(projectId, data.run.request.render_export)
      .then((result) => {
        if (!result.ok) throw new Error(result.error.message)
        setProject(result.data.project)
        setTab('timeline')
        setNotice('长篇 Mock 草稿已完成')
        void loadProjects()
      })
      .catch((caught) => {
        setError(caught instanceof Error ? caught.message : String(caught))
      })
      .finally(() => {
        mockAutoDraftInFlight.current = false
        setBusyLabel(null)
      })
  }, [loadProjects])

  const advancePlanning = useCallback(() => {
    if (!planningRun || planningAdvanceInFlight.current) return
    planningAdvanceInFlight.current = true
    setError(null)
    void ShortDramaApi.advancePlanningRun(planningRun.id)
      .then((result) => {
        if (!result.ok) {
          setError(result.error.message)
          window.setTimeout(
            () => setPlanningPollTick((value) => value + 1),
            5000,
          )
          return
        }
        acceptPlanningResult(result.data)
      })
      .catch((caught) => {
        setError(caught instanceof Error ? caught.message : String(caught))
        window.setTimeout(
          () => setPlanningPollTick((value) => value + 1),
          5000,
        )
      })
      .finally(() => { planningAdvanceInFlight.current = false })
  }, [acceptPlanningResult, planningRun])

  useEffect(() => {
    if (!planningRun || planningRun.status !== 'RUNNING') return
    const timer = window.setTimeout(advancePlanning, 350)
    return () => window.clearTimeout(timer)
  }, [advancePlanning, planningPollTick, planningRun])

  useEffect(() => {
    const run = project?.document.autonomous_runs?.at(-1)
    if (
      !project
      || !run
      || run.execution_target !== 'MOCK'
      || run.status !== 'RUNNING'
      || mockAutoDraftInFlight.current
    ) return
    mockAutoDraftInFlight.current = true
    setBusyLabel('恢复未完成的 Mock 自动草稿')
    void ShortDramaApi.autoDraft(project.document.project.id, run.render_export)
      .then((result) => {
        if (!result.ok) throw new Error(result.error.message)
        setProject(result.data.project)
        setTab('timeline')
        setNotice('Mock 自动草稿已恢复并完成')
        void loadProjects()
      })
      .catch((caught) => {
        setError(caught instanceof Error ? caught.message : String(caught))
      })
      .finally(() => {
        mockAutoDraftInFlight.current = false
        setBusyLabel(null)
      })
  }, [loadProjects, project])

  useEffect(() => {
    const run = project?.document.autonomous_runs?.at(-1)
    if (!project || !run || run.execution_target !== 'REAL_H3' || run.status !== 'RUNNING') return
    const timer = window.setTimeout(() => {
      if (autoAdvanceInFlight.current) return
      autoAdvanceInFlight.current = true
      void ShortDramaApi.advanceAutonomousRun(project.document.project.id, run.id, 24)
        .then((result) => {
          if (!result.ok) {
            setError(result.error.message)
            window.setTimeout(() => setAutoPollTick((value) => value + 1), 5000)
            return
          }
          setProject(result.data.project)
          if (result.data.run.status === 'COMPLETE') {
            setNotice('真人 AUTO_DRAFT 已完成并导出')
            setTab('timeline')
            void loadProjects()
          } else if (result.data.run.status === 'NEEDS_ATTENTION') {
            setError(result.data.run.error_message ?? '真人自动草稿需要处理后继续')
          }
        })
        .finally(() => { autoAdvanceInFlight.current = false })
    }, run.waiting_reason ? 4000 : 500)
    return () => window.clearTimeout(timer)
  }, [autoPollTick, loadProjects, project])

  const initializeFixture = () => void perform('初始化固定样片', ShortDramaApi.initializeFixture, (data) => {
    setProject(data)
    setActiveProjectId(data.document.project.id)
    setShowCreate(false)
    setTab('overview')
    void loadProjects()
  })

  const createProject = (
    name: string,
    productionProfile: ProductionProfile,
    targetDurationSeconds: number,
    sourceMode: SourceMode,
    inputText: string,
    runMode: RunMode,
    executionTarget: AutonomousExecutionTarget,
    images: CreativeInputImage[],
  ) => {
    const request = {
      name,
      production_profile: productionProfile,
      target_duration_seconds: targetDurationSeconds,
      source_mode: sourceMode,
      input_text: inputText,
      input_images: images,
      run_mode: runMode,
    }
    void perform(
      '启动可恢复分层剧本规划',
      () => ShortDramaApi.createPlanningRun({
        ...request,
        execution_target: executionTarget,
        render_export: true,
      }),
      (data) => {
        setPlanningRun(data.run)
        setProject(null)
        setActiveProjectId(null)
        setShowCreate(true)
      },
    )
  }

  const autoDraft = () => {
    if (!activeProjectId) return
    void perform('生成并导出完整 Mock 草稿', () => ShortDramaApi.autoDraft(activeProjectId, true), (data) => {
      setProject(data.project)
      setTab('timeline')
      void loadProjects()
    })
  }

  const continueRealDraft = () => {
    if (!activeProjectId || !project) return
    const run = project.document.autonomous_runs?.at(-1)
    if (!run || run.execution_target !== 'REAL_H3') return
    void perform(
      '继续真人自动草稿',
      () => ShortDramaApi.advanceAutonomousRun(activeProjectId, run.id, 24),
      (data) => {
        setProject(data.project)
        if (data.run.status === 'COMPLETE') setTab('timeline')
      },
    )
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

  const inspectTake = (takeId: string) => void perform(
    '抽帧检查镜内与镜间连续性',
    () => ShortDramaApi.inspectTakeVideo(takeId, {
      previous_take_id: null,
      include_previous_boundary: true,
      semantic_review: true,
      max_frames: 14,
      review_focus: null,
    }),
    (data) => setProject(data.project),
  )

  const inspectAudio = () => {
    if (!activeProjectId) return
    void perform(
      '检查对白、声线与字幕时间',
      () => ShortDramaApi.inspectProjectAudio(activeProjectId, {
        semantic_review: true,
        speaker_continuity: true,
        dialogue_padding_seconds: 0.35,
      }),
      (data) => setProject(data.project),
    )
  }

  const reviewEpisode = () => {
    if (!activeProjectId) return
    const latestReview = project?.episode_reviews?.at(-1)
    void perform(
      latestReview?.status === 'RUNNING' ? '继续整集分层审阅' : '运行整集分层审阅',
      () => ShortDramaApi.reviewProjectEpisode(activeProjectId, {
        max_actions: 32,
        force_new: latestReview?.status !== 'RUNNING',
      }),
      (data) => setProject(data.project),
    )
  }

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
    if (tab === 'overview') return <OverviewPanel project={project} onAutoDraft={autoDraft} onContinueRealDraft={continueRealDraft} />
    if (tab === 'script') return <ScriptPanel project={project} onRevise={reviseLine} />
    if (tab === 'assets') return <AssetWorkbenchPanel project={project} onProjectChange={setProject} onError={setError} onNotice={setNotice} />
    if (tab === 'shots') return <ShotsPanel project={project} onGenerate={generateShot} onRework={localRework} />
    if (tab === 'h3') return <H3LabPanel project={project} onProjectChange={setProject} onError={setError} onNotice={setNotice} />
    if (tab === 'takes') return <TakesPanel project={project} onDrain={drain} onTaskAction={handleTaskAction} onCancel={cancelTask} onSelect={selectTake} onInspect={inspectTake} />
    if (tab === 'timeline') return <TimelinePanel project={project} onBuild={buildManifest} onExport={exportProject} />
    return <QaPanel project={project} onInspectAudio={inspectAudio} onReviewEpisode={reviewEpisode} />
  }, [project, tab])

  const shouldShowCreate = showCreate || !project
  return (
    <div className="flex h-screen bg-[#08090c] text-zinc-100">
      <aside className="flex w-64 shrink-0 flex-col border-r border-zinc-800/80 bg-[#0c0e12]">
        <div className="border-b border-zinc-800/80 p-4">
          <div className="flex items-center gap-3"><div className="flex h-9 w-9 items-center justify-center rounded-xl bg-blue-500 text-white"><Clapperboard className="h-5 w-5" /></div><div><p className="text-sm font-semibold text-white">Short Drama Studio</p><p className="text-[11px] text-zinc-500">Local Production Core</p></div></div>
          <button className={`${secondaryButton} mt-4 w-full`} onClick={() => { setPlanningRun(null); setShowCreate(true) }}><Plus className="h-4 w-4" />新建项目</button>
        </div>
        <div className="flex-1 overflow-y-auto p-3">
          <p className="px-2 pb-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-600">Projects · SQLite</p>
          <div className="space-y-1">
            {projects.map((item) => (
              <button key={item.id} onClick={() => void loadProject(item.id)} className={`w-full rounded-lg px-3 py-2.5 text-left transition ${activeProjectId === item.id && !showCreate ? 'bg-blue-500/10 text-white ring-1 ring-blue-500/20' : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200'}`}>
                <div className="flex items-center justify-between gap-2"><span className="truncate text-sm font-medium">{item.name}</span><ChevronRight className="h-3.5 w-3.5 shrink-0 text-zinc-600" /></div>
                <div className="mt-1 flex items-center gap-1.5"><span className="text-[10px] text-zinc-600">{stageLabels[item.stage] ?? item.stage}</span><span className="text-zinc-800">·</span><span className="text-[10px] text-zinc-600">{productionProfileLabels[item.production_profile ?? 'DOUYIN_SHORT_DRAMA']}</span><span className="text-zinc-800">·</span><span className="text-[10px] text-zinc-600">{item.source_mode === 'SCRIPT_LOCKED' ? '剧本' : item.source_mode === 'IMAGE_SEEDED' ? '图片' : item.source_mode === 'AUTO_ORIGINAL' ? '自动' : '创意'}</span></div>
              </button>
            ))}
          </div>
        </div>
        <div className="border-t border-zinc-800/80 p-3">
          <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-3"><div className="flex items-center gap-2 text-xs text-zinc-300"><Database className="h-3.5 w-3.5 text-emerald-400" />SQLite WAL · Connected</div><p className="mt-1 text-[10px] leading-4 text-zinc-600">无登录、权限或云端项目依赖</p></div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {!planningRun && !shouldShowCreate && project && (
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
          {planningRun ? <PlanningRunPanel run={planningRun} onRetry={advancePlanning} /> : shouldShowCreate ? <CreateProjectPanel onCreate={createProject} onFixture={initializeFixture} /> : <div className="mx-auto max-w-[1600px] p-6 lg:p-8">{content}</div>}
          {busyLabel && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 backdrop-blur-sm"><div className="rounded-xl border border-zinc-700 bg-zinc-900 px-6 py-5 shadow-2xl"><div className="flex items-center gap-3 text-sm font-medium text-white"><Loader2 className="h-5 w-5 animate-spin text-blue-400" />{busyLabel}</div><p className="mt-2 text-xs text-zinc-500">任务和恢复点正在持久化，请勿关闭窗口。</p></div></div>}
          {(notice || error) && <div className={`fixed bottom-5 right-5 z-40 max-w-md rounded-xl border px-4 py-3 shadow-2xl ${error ? 'border-red-500/30 bg-red-950/95' : 'border-emerald-500/30 bg-emerald-950/95'}`}><div className="flex items-start gap-2">{error ? <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-300" /> : <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-300" />}<div><p className="text-sm text-white">{error ? '操作未完成' : notice}</p>{error && <p className="mt-1 text-xs leading-5 text-red-200/70">{error}</p>}</div><button className="ml-3 text-xs text-zinc-500" onClick={() => { setNotice(null); setError(null) }}>关闭</button></div></div>}
        </main>
      </div>
    </div>
  )
}
