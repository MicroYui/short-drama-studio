import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  CheckCircle2,
  CircleDollarSign,
  CloudCog,
  Eye,
  FileUp,
  FlaskConical,
  KeyRound,
  Loader2,
  Play,
  RefreshCw,
  RotateCcw,
  Send,
  Sparkles,
  Square,
} from 'lucide-react'
import { pathToFileUrl } from '../../lib/file-url'
import {
  ShortDramaApi,
  type Asset,
  type AssetVersion,
  type GenerationTask,
  type H3CompilePreview,
  type H3MediaInput,
  type H3MediaKind,
  type H3MediaRole,
  type H3ProviderConfig,
  type H3ProviderRun,
  type H3ProbeRequest,
  type ProjectAggregate,
  type RouteEvidence,
  type Take,
  type VideoGenerationRoute,
} from './api'

type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: { message: string } }

type SourceMode = 'local_path' | 'external_url' | 'file_id'
type SourceDraft = { mode: SourceMode; value: string; assetVersionId?: string }
type ApprovedAsset = { asset: Asset; version: AssetVersion }

const referenceRoles: H3MediaRole[] = ['reference_image', 'reference_video', 'reference_audio']
const referenceRoleLimits: Record<H3MediaRole, number> = {
  first_frame: 1,
  last_frame: 1,
  reference_image: 9,
  reference_video: 3,
  reference_audio: 3,
}

function emptySourceDraft(): SourceDraft {
  return { mode: 'local_path', value: '' }
}

const primaryButton = 'inline-flex items-center justify-center gap-2 rounded-lg bg-blue-500 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-blue-400 disabled:cursor-not-allowed disabled:opacity-40'
const secondaryButton = 'inline-flex items-center justify-center gap-2 rounded-lg border border-zinc-700 bg-zinc-900 px-3.5 py-2 text-sm font-medium text-zinc-200 transition hover:border-zinc-600 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40'
const subtleButton = 'inline-flex items-center justify-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-zinc-300 transition hover:bg-zinc-800 hover:text-white disabled:cursor-not-allowed disabled:opacity-40'
const inputClass = 'w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm text-zinc-200 outline-none transition focus:border-blue-500'

const routeLabels: Record<VideoGenerationRoute, string> = {
  DIRECT: 'Direct · 文本直出',
  FRAME_FIRST: 'Frame · 首帧',
  FRAME_FIRST_LAST: 'Frame · 首尾帧',
  REFERENCE: 'Reference · 角色/视频/声音参考',
}

const roleLabels: Record<H3MediaRole, string> = {
  first_frame: '首帧图',
  last_frame: '尾帧图',
  reference_image: '参考图',
  reference_video: '参考视频',
  reference_audio: '参考音频',
}

const roleKinds: Record<H3MediaRole, H3MediaKind> = {
  first_frame: 'IMAGE',
  last_frame: 'IMAGE',
  reference_image: 'IMAGE',
  reference_video: 'VIDEO',
  reference_audio: 'AUDIO',
}

const statusLabels: Record<string, string> = {
  QUEUED: '本地排队',
  SUBMITTING: '待提交',
  ACKNOWLEDGED: '远端排队',
  RUNNING: '远端生成',
  SUCCEEDED: '已下载',
  FAILED: '失败',
  CANCELLED: '已取消',
  SUBMIT_UNKNOWN: '提交结果未知',
  OUTPUT_MISSING: '产物待恢复',
}

const evidenceRouteLabels: Record<VideoGenerationRoute, string> = {
  DIRECT: 'D',
  FRAME_FIRST: 'F1',
  FRAME_FIRST_LAST: 'F2',
  REFERENCE: 'R',
}

const evidenceStatusLabels: Record<string, string> = {
  AWAITING_OUTPUT: '生成中',
  AWAITING_REVIEW: '待审',
  REVIEWED: '已审',
  SUBMIT_UNKNOWN: '待对账',
  OUTPUT_MISSING: '待恢复',
  FAILED: '失败',
  CANCELLED: '取消',
}

function badgeTone(status: string): string {
  if (status === 'SUCCEEDED' || status === 'PASSED' || status === 'REVIEWED') return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
  if (status === 'FAILED' || status === 'REJECTED') return 'border-red-500/30 bg-red-500/10 text-red-300'
  if (status === 'SUBMIT_UNKNOWN' || status === 'OUTPUT_MISSING') return 'border-amber-500/30 bg-amber-500/10 text-amber-300'
  return 'border-blue-500/30 bg-blue-500/10 text-blue-300'
}

function Badge({ status, children }: { status: string; children: React.ReactNode }) {
  return <span className={`inline-flex rounded-full border px-2 py-0.5 text-[11px] font-medium ${badgeTone(status)}`}>{children}</span>
}

function sourceFromDraft(draft: SourceDraft): H3MediaInput['source'] {
  if (draft.mode === 'external_url') return { external_url: draft.value.trim() }
  if (draft.mode === 'file_id') return { file_id: draft.value.trim() }
  return { local_path: draft.value.trim() }
}

function SourceInput({
  role,
  index,
  draft,
  disabled = false,
  onChange,
  onRemove,
}: {
  role: H3MediaRole
  index: number
  draft: SourceDraft
  disabled?: boolean
  onChange: (next: SourceDraft) => void
  onRemove?: () => void
}) {
  const kind = roleKinds[role]
  const browse = async () => {
    if (disabled) return
    const filters = kind === 'IMAGE'
      ? [{ name: 'Image', extensions: ['jpg', 'jpeg', 'png', 'webp', 'heic', 'heif'] }]
      : kind === 'VIDEO'
        ? [{ name: 'Video', extensions: ['mp4', 'mov'] }]
        : [{ name: 'Audio', extensions: ['wav', 'mp3'] }]
    const paths = await window.electronAPI?.showOpenFileDialog({
      title: `选择${roleLabels[role]}`,
      filters,
    })
    if (paths?.[0]) onChange({ mode: 'local_path', value: paths[0] })
  }
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-medium text-zinc-300">{roleLabels[role]}{referenceRoles.includes(role) ? ` ${index + 1}` : ''}</span>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-zinc-600">{draft.assetVersionId ? '正式 AssetVersion' : kind}</span>
          {onRemove && <button className="text-[10px] text-zinc-600 hover:text-red-300" onClick={onRemove} type="button">移除</button>}
        </div>
      </div>
      <div className="flex gap-2">
        <select
          className="rounded-lg border border-zinc-700 bg-zinc-950 px-2 text-xs text-zinc-300"
          disabled={disabled}
          value={draft.mode}
          onChange={(event) => onChange({ mode: event.target.value as SourceMode, value: draft.value })}
        >
          <option value="local_path">本地文件</option>
          <option value="external_url">公网 URL</option>
          <option value="file_id">MiniMax mm_file ID</option>
        </select>
        <input
          className={`${inputClass} min-w-0 flex-1 py-2 text-xs`}
          disabled={disabled}
          placeholder={draft.mode === 'local_path' ? '/path/to/file' : draft.mode === 'file_id' ? '123456789' : 'https://...'}
          value={draft.value}
          onChange={(event) => onChange({ mode: draft.mode, value: event.target.value })}
        />
        {draft.mode === 'local_path' && (
          <button className={subtleButton} disabled={disabled} onClick={() => void browse()} type="button">
            <FileUp className="h-3.5 w-3.5" />选择
          </button>
        )}
      </div>
    </div>
  )
}

function estimatedBaseCost(duration: number, config: H3ProviderConfig | null): string {
  const rate = config?.pricing.output_per_second ?? 0.08
  return (duration * rate).toFixed(2)
}

function providerTaskId(task: GenerationTask): string {
  return task.provider_task_id ?? '尚无远端 ID'
}

export function H3LabPanel({
  project,
  onProjectChange,
  onError,
  onNotice,
}: {
  project: ProjectAggregate
  onProjectChange: (project: ProjectAggregate) => void
  onError: (message: string) => void
  onNotice: (message: string) => void
}) {
  const availableShots = useMemo(
    () => [...project.document.shots].sort((left, right) => left.sequence - right.sequence),
    [project.document.shots],
  )
  const approvedAssets = useMemo<ApprovedAsset[]>(() => project.document.assets.flatMap((asset) => {
    if (!asset.approved_version_id) return []
    const version = asset.versions.find((candidate) => candidate.id === asset.approved_version_id)
    if (!version || !['IMAGE', 'VIDEO', 'AUDIO'].includes(version.media_type)) return []
    if (!version.local_path && !version.external_url) return []
    return [{ asset, version }]
  }), [project.document.assets])
  const [shotId, setShotId] = useState(availableShots[0]?.id ?? '')
  const [route, setRoute] = useState<VideoGenerationRoute>('DIRECT')
  const selectedShot = project.document.shots.find((shot) => shot.id === shotId) ?? project.document.shots[0]
  const [duration, setDuration] = useState(Math.ceil(selectedShot?.duration_seconds ?? 5))
  const [purpose, setPurpose] = useState<'CONTRACT_SMOKE' | 'QUALITY_PROBE'>('CONTRACT_SMOKE')
  const [promptOverride, setPromptOverride] = useState('')
  const [drafts, setDrafts] = useState<Record<H3MediaRole, SourceDraft[]>>({
    first_frame: [emptySourceDraft()],
    last_frame: [emptySourceDraft()],
    reference_image: [emptySourceDraft()],
    reference_video: [emptySourceDraft()],
    reference_audio: [emptySourceDraft()],
  })
  const [config, setConfig] = useState<H3ProviderConfig | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [runs, setRuns] = useState<H3ProviderRun[]>([])
  const [preview, setPreview] = useState<H3CompilePreview | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [polling, setPolling] = useState(false)
  const [reconcileTaskId, setReconcileTaskId] = useState('')
  const [remoteTaskId, setRemoteTaskId] = useState('')
  const [reviewTakeId, setReviewTakeId] = useState('')
  const [reviewScore, setReviewScore] = useState(4)
  const [reviewPassed, setReviewPassed] = useState(false)
  const [reviewNotes, setReviewNotes] = useState('')

  useEffect(() => {
    if (selectedShot) setDuration(Math.ceil(selectedShot.duration_seconds))
  }, [selectedShot?.id])

  const refreshProviderData = useCallback(async () => {
    const [configResult, runsResult] = await Promise.all([
      ShortDramaApi.getH3Config(),
      ShortDramaApi.listH3Runs(project.document.project.id),
    ])
    if (configResult.ok) setConfig(configResult.data)
    else onError(configResult.error.message)
    if (runsResult.ok) setRuns(runsResult.data.runs)
    else onError(runsResult.error.message)
  }, [onError, project.document.project.id])

  useEffect(() => {
    void refreshProviderData()
  }, [refreshProviderData])

  const mediaInputs = useMemo(() => {
    const roles: H3MediaRole[] = route === 'DIRECT'
      ? []
      : route === 'FRAME_FIRST'
        ? ['first_frame']
        : route === 'FRAME_FIRST_LAST'
          ? ['first_frame', 'last_frame']
          : ['reference_image', 'reference_video', 'reference_audio']
    return roles.flatMap((roleName) => drafts[roleName]
      .filter((draft) => draft.value.trim().length > 0)
      .map((draft): H3MediaInput => ({
        kind: roleKinds[roleName],
        role: roleName,
        source: sourceFromDraft(draft),
        ...(draft.assetVersionId ? { asset_version_id: draft.assetVersionId } : {}),
      })))
  }, [drafts, route])

  const requiredInputsReady = route === 'DIRECT'
    || (route === 'FRAME_FIRST' && drafts.first_frame[0].value.trim().length > 0)
    || (route === 'FRAME_FIRST_LAST'
      && drafts.first_frame[0].value.trim().length > 0
      && drafts.last_frame[0].value.trim().length > 0)
    || (route === 'REFERENCE' && mediaInputs.length > 0)

  const updateDraft = (roleName: H3MediaRole, index: number, next: SourceDraft) => {
    setDrafts((current) => ({
      ...current,
      [roleName]: current[roleName].map((draft, draftIndex) => (
        draftIndex === index ? next : draft
      )),
    }))
  }

  const addReferenceDraft = (roleName: H3MediaRole) => {
    setDrafts((current) => ({
      ...current,
      [roleName]: [...current[roleName], emptySourceDraft()],
    }))
  }

  const removeReferenceDraft = (roleName: H3MediaRole, index: number) => {
    setDrafts((current) => ({
      ...current,
      [roleName]: current[roleName].filter((_draft, draftIndex) => draftIndex !== index),
    }))
  }

  const addApprovedAsset = (entry: ApprovedAsset) => {
    if (route === 'DIRECT') {
      onError('Direct 路线不接受媒体资产')
      return
    }
    const source: SourceDraft = entry.version.local_path
      ? { mode: 'local_path', value: entry.version.local_path, assetVersionId: entry.version.id }
      : { mode: 'external_url', value: entry.version.external_url ?? '', assetVersionId: entry.version.id }
    let roleName: H3MediaRole
    if (route === 'FRAME_FIRST') {
      if (entry.version.media_type !== 'IMAGE') { onError('Frame 路线只能使用已批准图像'); return }
      roleName = 'first_frame'
    } else if (route === 'FRAME_FIRST_LAST') {
      if (entry.version.media_type !== 'IMAGE') { onError('Frame 路线只能使用已批准图像'); return }
      roleName = drafts.first_frame[0].value.trim() ? 'last_frame' : 'first_frame'
    } else {
      roleName = entry.version.media_type === 'VIDEO'
        ? 'reference_video'
        : entry.version.media_type === 'AUDIO'
          ? 'reference_audio'
          : 'reference_image'
    }
    setDrafts((current) => {
      const roleDrafts = current[roleName]
      const emptyIndex = roleDrafts.findIndex((draft) => !draft.value.trim())
      if (emptyIndex >= 0) {
        return {
          ...current,
          [roleName]: roleDrafts.map((draft, index) => index === emptyIndex ? source : draft),
        }
      }
      if (route === 'REFERENCE' && roleDrafts.length < referenceRoleLimits[roleName] && mediaInputs.length < 12) {
        return { ...current, [roleName]: [...roleDrafts, source] }
      }
      return { ...current, [roleName]: roleDrafts.map((draft, index) => index === roleDrafts.length - 1 ? source : draft) }
    })
    setPreview(null)
    onNotice(`已加入正式资产：${entry.asset.name} v${entry.version.version}`)
  }

  const probeRequest = (): H3ProbeRequest => ({
    shot_id: shotId,
    route,
    duration_seconds: duration,
    prompt_override: promptOverride.trim() || null,
    media_inputs: mediaInputs,
    purpose,
  })

  const run = async <T,>(label: string, action: () => Promise<ApiResult<T>>, success: (value: T) => void) => {
    setBusy(label)
    try {
      const result = await action()
      if (!result.ok) throw new Error(result.error.message)
      success(result.data)
      onNotice(`${label}完成`)
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(null)
    }
  }

  const saveKey = () => void run(
    '保存 AceData API Token',
    () => ShortDramaApi.updateH3Config(apiKey.trim()),
    (next) => {
      setConfig(next)
      setApiKey('')
    },
  )

  const clearKey = () => void run(
    '清除本地 API Key',
    () => ShortDramaApi.updateH3Config(null, true),
    setConfig,
  )

  const validateKey = () => void run(
    '验证 AceData 连接',
    ShortDramaApi.validateH3Config,
    setConfig,
  )

  const compilePreview = () => void run(
    '编译 H3 请求预览',
    () => ShortDramaApi.previewH3Probe(project.document.project.id, probeRequest()),
    setPreview,
  )

  const compileWithGpt = () => void run(
    'GPT-5.6-sol 编译镜头提示词',
    () => ShortDramaApi.compileH3PromptWithLanguageModel(
      project.document.project.id,
      {
        shot_id: shotId,
        route,
        duration_seconds: duration,
        source_prompt: promptOverride.trim() || null,
        media_inputs: mediaInputs,
      },
    ),
    (result) => {
      setPromptOverride(result.compiled_prompt)
      setPreview(null)
      onNotice(`GPT 提示词已通过 ${result.validation_notes.length} 项锁定事实检查；请继续预览，不会自动提交 H3`)
    },
  )

  const submitProbe = () => void run(
    '创建真实 H3 768P 探针',
    () => ShortDramaApi.enqueueH3Probe(project.document.project.id, probeRequest()),
    (result) => {
      onProjectChange(result.project)
      setRuns((current) => [result.provider_run, ...current.filter((item) => item.id !== result.provider_run.id)])
    },
  )

  const advanceTask = useCallback(async (task: GenerationTask, showNotice = false) => {
    setPolling(true)
    try {
      const result = await ShortDramaApi.advanceTask(task.id)
      if (!result.ok) throw new Error(result.error.message)
      onProjectChange(result.data.project)
      const runsResult = await ShortDramaApi.listH3Runs(project.document.project.id)
      if (runsResult.ok) setRuns(runsResult.data.runs)
      if (showNotice) onNotice(`H3 任务已推进到 ${statusLabels[result.data.task.status] ?? result.data.task.status}`)
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error))
    } finally {
      setPolling(false)
    }
  }, [onError, onNotice, onProjectChange, project.document.project.id])

  const pollableTasks = project.tasks.filter(
    (task) => task.provider === 'minimax' && ['QUEUED', 'SUBMITTING', 'ACKNOWLEDGED', 'RUNNING'].includes(task.status),
  )
  const pollKey = pollableTasks.map((task) => `${task.id}:${task.status}`).join('|')
  useEffect(() => {
    if (!pollKey) return undefined
    if (polling) return undefined
    const timer = window.setTimeout(() => {
      const next = pollableTasks[0]
      if (next) void advanceTask(next)
    }, 10000)
    return () => window.clearTimeout(timer)
  }, [advanceTask, pollKey, polling])

  const taskAction = (task: GenerationTask, action: 'cancel' | 'retry' | 'recover') => {
    const call = action === 'cancel'
      ? () => ShortDramaApi.cancelTask(task.id)
      : action === 'retry'
        ? () => ShortDramaApi.retryTask(task.id)
        : () => ShortDramaApi.recoverTaskOutput(task.id)
    void run(
      action === 'cancel' ? '取消 H3 任务' : action === 'retry' ? '创建 H3 重试任务' : '恢复 H3 输出',
      call,
      (result) => {
        onProjectChange(result.project)
        void refreshProviderData()
      },
    )
  }

  const loadCandidates = () => void run(
    '读取 AceData 最近任务',
    ShortDramaApi.listH3ReconciliationCandidates,
    (result) => {
      const unknown = project.tasks.find((task) => task.id === reconcileTaskId)
      const candidate = result.tasks.find((task) => (
        task.model === unknown?.model
        && (task.resolution === null || task.resolution === undefined || task.resolution === unknown?.resolution)
        && (task.duration === null || task.duration === undefined || task.duration === Math.round(unknown?.requested_duration_seconds ?? 0))
      ))
      if (candidate) setRemoteTaskId(candidate.id)
      onNotice(`读取到 ${result.tasks.length} 个最近远端任务，请核对 ID 后绑定`)
    },
  )

  const resolveUnknown = () => {
    if (!reconcileTaskId || !remoteTaskId.trim()) return
    void run(
      '绑定远端任务并完成对账',
      () => ShortDramaApi.resolveH3Submission(reconcileTaskId, remoteTaskId.trim()),
      (result) => {
        onProjectChange(result.project)
        void refreshProviderData()
      },
    )
  }

  const h3Takes = project.takes.filter((take) => take.provider === 'minimax')
  const retriedTaskIds = new Set(
    project.tasks
      .map((task) => task.retry_of_task_id)
      .filter((taskId): taskId is string => Boolean(taskId)),
  )
  const reviewTake = h3Takes.find((take) => take.id === reviewTakeId)
    ?? h3Takes.find((take) => take.qa_status === 'PENDING')
    ?? h3Takes[0]
  const reviewEvidence = reviewTake
    ? (project.route_evidence ?? []).find((item) => item.take_id === reviewTake.id)
    : undefined
  useEffect(() => {
    if (reviewTake && reviewTakeId !== reviewTake.id) setReviewTakeId(reviewTake.id)
  }, [reviewTake?.id])

  useEffect(() => {
    if (!reviewTake) return
    setReviewPassed(reviewTake.qa_report.hard_gate_passed)
    setReviewScore(reviewTake.qa_report.score)
    setReviewNotes(reviewTake.qa_report.reviewer_notes ?? '')
  }, [reviewTake?.id])

  const submitReview = () => {
    if (!reviewTake || !reviewNotes.trim()) return
    void run(
      '保存 H3 人工审片',
      () => ShortDramaApi.reviewH3Take(reviewTake.id, {
        hard_gate_passed: reviewPassed,
        score: reviewScore,
        notes: reviewNotes.trim(),
        issues: [],
      }),
      (result) => onProjectChange(result.project),
    )
  }

  const selectReviewedTake = (take: Take) => void run(
    '将 H3 Take 选入时间线',
    () => ShortDramaApi.selectTake(take.id),
    onProjectChange,
  )

  const unknownTasks = project.tasks.filter((task) => task.provider === 'minimax' && task.status === 'SUBMIT_UNKNOWN')
  const totalCost = runs.reduce((sum, item) => sum + (item.estimated_cost_usd ?? 0), 0)
  const routeEvidence = project.route_evidence ?? []
  const evidenceByShotRoute = new Map<string, RouteEvidence>()
  for (const evidence of routeEvidence) {
    const key = `${evidence.shot_id}:${evidence.route}`
    const current = evidenceByShotRoute.get(key)
    if (!current || evidence.purpose === 'QUALITY_PROBE' || current.purpose !== 'QUALITY_PROBE') {
      evidenceByShotRoute.set(key, evidence)
    }
  }
  const evidenceRoutes = Object.keys(evidenceRouteLabels) as VideoGenerationRoute[]
  const inputRoles: H3MediaRole[] = route === 'FRAME_FIRST'
    ? ['first_frame']
    : route === 'FRAME_FIRST_LAST'
      ? ['first_frame', 'last_frame']
      : route === 'REFERENCE'
        ? ['reference_image', 'reference_video', 'reference_audio']
        : []

  return (
    <div className="space-y-6">
      <section className="overflow-hidden rounded-2xl border border-cyan-500/20 bg-gradient-to-br from-cyan-500/10 via-zinc-950 to-blue-500/5 p-6">
        <div className="flex flex-col gap-5 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <Badge status="RUNNING">Phase 2</Badge>
              <Badge status="ACKNOWLEDGED">MiniMax-H3 via AceData</Badge>
              <Badge status="SUCCEEDED">768P fixed</Badge>
            </div>
            <h2 className="mt-3 text-2xl font-semibold text-white">H3 真实路线实验台</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-300">
              在共享 ShotPlan、任务状态、Take 和 TimelineManifest 上比较 Direct、Frame 与 Reference。提交、轮询、下载、恢复、费用快照和人工审片都持久化；成功下载不等于质量通过，也不会自动入选。
            </p>
          </div>
          <div className="grid min-w-72 grid-cols-2 gap-3">
            <div className="rounded-xl border border-zinc-800 bg-black/20 p-3"><p className="text-[10px] uppercase tracking-wider text-zinc-600">Provider runs</p><p className="mt-1 text-xl font-semibold text-white">{runs.length}</p></div>
            <div className="rounded-xl border border-zinc-800 bg-black/20 p-3"><p className="text-[10px] uppercase tracking-wider text-zinc-600">Recorded cost</p><p className="mt-1 text-xl font-semibold text-white">${totalCost.toFixed(2)}</p></div>
          </div>
        </div>
      </section>

      <section className="grid gap-5 xl:grid-cols-[1fr_1.35fr]">
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-2"><KeyRound className="h-4 w-4 text-cyan-400" /><h3 className="font-medium text-white">AceData Cloud 连接</h3></div>
          <div className="mt-4 flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-950/70 px-3 py-2.5">
            <div><p className="text-xs text-zinc-300">{config?.has_api_key ? 'API Token 已配置' : '尚未配置 API Token'}</p><p className="mt-0.5 text-[11px] text-zinc-600">MiniMax-H3 · {config?.base_url ?? '读取中'} · Token 永不回传到界面</p></div>
            <Badge status={config?.has_api_key ? 'SUCCEEDED' : 'FAILED'}>{config?.has_api_key ? 'Ready' : 'Missing'}</Badge>
          </div>
          <div className="mt-3 flex gap-2">
            <input className={`${inputClass} min-w-0 flex-1`} type="password" autoComplete="off" placeholder="粘贴 AceData API Token" value={apiKey} onChange={(event) => setApiKey(event.target.value)} />
            <button className={primaryButton} disabled={!apiKey.trim() || busy !== null} onClick={saveKey}>保存</button>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <button className={secondaryButton} disabled={!config?.has_api_key || busy !== null} onClick={validateKey}><CloudCog className="h-3.5 w-3.5" />验证连接</button>
            <button className={subtleButton} disabled={!config?.has_api_key || config.key_source === 'ENVIRONMENT' || busy !== null} onClick={clearKey}>清除本地 Key</button>
          </div>
          <div className="mt-4 rounded-lg border border-zinc-800 bg-zinc-950/60 p-3 text-xs leading-5 text-zinc-500">
            成本参考：按 MiniMax 官方费率估算 768P 输出 ${config?.pricing.output_per_second.toFixed(2) ?? '0.08'}/秒；AceData 实际扣费以平台账单为准，且不作为质量路线的优先级。
          </div>
        </div>

        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center justify-between"><div className="flex items-center gap-2"><FlaskConical className="h-4 w-4 text-blue-400" /><h3 className="font-medium text-white">代表镜头证据矩阵</h3></div><span className="text-xs text-zinc-600">镜头 1 / 2 / 5 / 8</span></div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {availableShots.map((shot) => {
              const reviewedCount = evidenceRoutes.filter((routeName) => (
                evidenceByShotRoute.get(`${shot.id}:${routeName}`)?.purpose === 'QUALITY_PROBE'
                && evidenceByShotRoute.get(`${shot.id}:${routeName}`)?.status === 'REVIEWED'
              )).length
              return (
                <button key={shot.id} onClick={() => setShotId(shot.id)} className={`rounded-xl border p-3 text-left transition ${shotId === shot.id ? 'border-blue-500 bg-blue-500/10' : 'border-zinc-800 bg-zinc-950/60 hover:border-zinc-700'}`}>
                  <div className="flex items-center justify-between"><span className="text-sm font-semibold text-white">镜头 {shot.sequence}</span><span className="text-[10px] text-zinc-600">质量已审 {reviewedCount}/{evidenceRoutes.length}</span></div>
                  <p className="mt-2 line-clamp-2 text-xs leading-5 text-zinc-500">{shot.narrative_purpose}</p>
                  <div className="mt-3 grid grid-cols-2 gap-1.5">
                    {evidenceRoutes.map((routeName) => {
                      const evidence = evidenceByShotRoute.get(`${shot.id}:${routeName}`)
                      return <span key={routeName} className="flex items-center justify-between rounded border border-zinc-800 bg-black/20 px-1.5 py-1 text-[10px]"><span className="font-medium text-zinc-400">{evidenceRouteLabels[routeName]}</span><span className={evidence ? badgeTone(evidence.status) : 'text-zinc-700'}>{evidence ? `${evidence.purpose === 'QUALITY_PROBE' ? 'Q' : 'C'}·${evidenceStatusLabels[evidence.status] ?? evidence.status}` : '未测试'}</span></span>
                    })}
                  </div>
                </button>
              )
            })}
          </div>
        </div>
      </section>

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
        <div className="flex flex-col gap-2 xl:flex-row xl:items-center xl:justify-between">
          <div><h3 className="font-medium text-white">请求编译与真实提交</h3><p className="mt-1 text-xs text-zinc-500">先预览编译结果；点击真实提交后，任务每 10 秒自动推进一次。Frame 与 Reference 的非法混用会在联网前拒绝。</p></div>
          {busy && <div className="flex items-center gap-2 text-xs text-blue-300"><Loader2 className="h-3.5 w-3.5 animate-spin" />{busy}</div>}
        </div>
        <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(360px,0.8fr)]">
          <div className="space-y-4">
            <div className="grid gap-3 md:grid-cols-[1fr_180px_190px]">
              <label><span className="mb-1.5 block text-xs text-zinc-500">镜头</span><select className={inputClass} value={shotId} onChange={(event) => setShotId(event.target.value)}>{project.document.shots.map((shot) => <option key={shot.id} value={shot.id}>镜头 {shot.sequence} · {shot.narrative_purpose}</option>)}</select></label>
              <label><span className="mb-1.5 block text-xs text-zinc-500">时长（4–15 秒）</span><input className={inputClass} type="number" min={4} max={15} step={1} value={duration} onChange={(event) => setDuration(Number(event.target.value))} /></label>
              <label><span className="mb-1.5 block text-xs text-zinc-500">证据用途</span><select className={inputClass} value={purpose} onChange={(event) => setPurpose(event.target.value as 'CONTRACT_SMOKE' | 'QUALITY_PROBE')}><option value="CONTRACT_SMOKE">合同冒烟</option><option value="QUALITY_PROBE">质量探针</option></select></label>
            </div>
            <div>
              <span className="mb-2 block text-xs text-zinc-500">H3 路线</span>
              <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
                {(Object.keys(routeLabels) as VideoGenerationRoute[]).map((item) => <button key={item} className={`rounded-lg border px-3 py-2.5 text-left text-xs transition ${route === item ? 'border-blue-500 bg-blue-500/10 text-blue-200' : 'border-zinc-800 bg-zinc-950 text-zinc-400'}`} onClick={() => { setRoute(item); setPreview(null) }}>{routeLabels[item]}</button>)}
              </div>
            </div>
            {inputRoles.length > 0 && <div className="rounded-lg border border-emerald-500/15 bg-emerald-500/5 p-3"><div className="flex items-center justify-between gap-3"><div><p className="text-xs font-medium text-emerald-200">从资产工作台加入正式版本</p><p className="mt-1 text-[11px] text-zinc-600">这里只列出当前 approved_version_id；服务端会再次校验并按版本记录解析来源。</p></div><Badge status={approvedAssets.length ? 'SUCCEEDED' : 'FAILED'}>{approvedAssets.length} 个</Badge></div>{approvedAssets.length === 0 ? <p className="mt-3 text-xs text-zinc-600">先在资产页批准至少一个带媒体的版本。</p> : <div className="mt-3 flex flex-wrap gap-2">{approvedAssets.map((entry) => { const compatible = route === 'REFERENCE' || entry.version.media_type === 'IMAGE'; return <button key={entry.version.id} type="button" className={subtleButton} disabled={!compatible} onClick={() => addApprovedAsset(entry)}>{entry.asset.name} · v{entry.version.version} · {entry.version.media_type}</button> })}</div>}</div>}
            {inputRoles.length > 0 && <div className="space-y-3">{route === 'REFERENCE' && <p className="text-xs text-zinc-500">已填写 {mediaInputs.length}/12 个参考素材；填满后其他空槽自动锁定。</p>}{inputRoles.map((roleName) => <div key={roleName} className="space-y-2">{drafts[roleName].map((draft, draftIndex) => <SourceInput key={`${roleName}-${draftIndex}`} role={roleName} index={draftIndex} draft={draft} disabled={route === 'REFERENCE' && mediaInputs.length >= 12 && !draft.value.trim()} onChange={(next) => updateDraft(roleName, draftIndex, next)} onRemove={route === 'REFERENCE' && drafts[roleName].length > 1 ? () => removeReferenceDraft(roleName, draftIndex) : undefined} />)}{route === 'REFERENCE' && <button className={subtleButton} type="button" disabled={drafts[roleName].length >= referenceRoleLimits[roleName] || mediaInputs.length >= 12} onClick={() => addReferenceDraft(roleName)}>+ 添加{roleLabels[roleName]}（{drafts[roleName].length}/{referenceRoleLimits[roleName]}）</button>}</div>)}</div>}
            <label className="block"><span className="mb-1.5 block text-xs text-zinc-500">Prompt override（留空则由当前剧本、Bible、分镜与连续性自动编译）</span><textarea className={`${inputClass} min-h-24 resize-y leading-6`} value={promptOverride} onChange={(event) => setPromptOverride(event.target.value)} /></label>
            <div className="flex flex-wrap gap-2">
              <button className={secondaryButton} disabled={!shotId || busy !== null} onClick={compileWithGpt}><Sparkles className="h-3.5 w-3.5" />GPT-5.6-sol 编译（仅文本）</button>
              <button className={secondaryButton} disabled={!shotId || !requiredInputsReady || busy !== null} onClick={compilePreview}><Eye className="h-3.5 w-3.5" />预览请求</button>
              <button className={primaryButton} disabled={!config?.can_submit || !shotId || !requiredInputsReady || busy !== null} onClick={submitProbe}><Send className="h-3.5 w-3.5" />提交{purpose === 'QUALITY_PROBE' ? '质量探针' : '合同冒烟'} · 基础约 ${estimatedBaseCost(duration, config)}</button>
            </div>
          </div>
          <div className="min-h-72 rounded-xl border border-zinc-800 bg-zinc-950/70 p-4">
            {!preview ? <div className="flex h-full min-h-64 flex-col items-center justify-center text-center"><Eye className="h-7 w-7 text-zinc-700" /><p className="mt-3 text-sm text-zinc-400">尚未编译预览</p><p className="mt-1 max-w-xs text-xs leading-5 text-zinc-600">预览不联网、不计费；提交优先使用 async=true，若 AceData 当前 H3 V2 网关明确拒绝则安全回退到同步等待；本地素材会转换为 Data URI。</p></div> : <><div className="flex items-center justify-between"><span className="text-xs font-medium text-zinc-300">AceData /minimax/videos request</span><Badge status="SUCCEEDED">{preview.request.resolution}</Badge></div><p className="mt-3 max-h-36 overflow-y-auto whitespace-pre-wrap text-xs leading-5 text-zinc-400">{preview.prompt}</p><pre className="mt-3 max-h-56 overflow-auto rounded-lg bg-black/40 p-3 text-[10px] leading-5 text-cyan-200">{JSON.stringify(preview.request, null, 2)}</pre></>}
          </div>
        </div>
      </section>

      <section className="grid gap-5 xl:grid-cols-[1fr_1fr]">
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center justify-between"><div className="flex items-center gap-2"><RefreshCw className={`h-4 w-4 text-blue-400 ${polling ? 'animate-spin' : ''}`} /><h3 className="font-medium text-white">H3 异步任务</h3></div><span className="text-xs text-zinc-600">自动轮询 · 10s</span></div>
          <div className="mt-4 space-y-2">
            {project.tasks.filter((task) => task.provider === 'minimax').length === 0 && <p className="rounded-lg border border-dashed border-zinc-800 py-10 text-center text-xs text-zinc-600">暂无真实 H3 任务</p>}
            {project.tasks.filter((task) => task.provider === 'minimax').map((task) => {
              const providerRun = runs.find((item) => item.task_id === task.id)
              const canAdvance = ['QUEUED', 'SUBMITTING', 'ACKNOWLEDGED', 'RUNNING'].includes(task.status)
              const wasRetried = retriedTaskIds.has(task.id)
              return <div key={task.id} className="rounded-lg border border-zinc-800 bg-zinc-950/70 p-3"><div className="flex flex-wrap items-center justify-between gap-2"><div><p className="text-xs font-medium text-zinc-300">镜头 {project.document.shots.find((shot) => shot.id === task.shot_id)?.sequence} · {task.route} · Take {task.candidate_index}</p><p className="mt-1 text-[10px] text-zinc-600">{providerRun ? (providerRun.purpose === 'CONTRACT_SMOKE' ? '合同冒烟' : '质量探针') : '用途读取中'} · {providerTaskId(task)} · {task.request_fingerprint.slice(0, 12)}</p></div><Badge status={task.status}>{statusLabels[task.status] ?? task.status}</Badge></div>{task.error_message && <p className="mt-2 text-xs leading-5 text-amber-300/80">{task.error_message}</p>}<div className="mt-2 flex flex-wrap items-center justify-between gap-2"><span className="text-[11px] text-zinc-600">{wasRetried ? '已保留历史并创建重试' : providerRun?.estimated_cost_usd !== null && providerRun?.estimated_cost_usd !== undefined ? `$${providerRun.estimated_cost_usd.toFixed(2)}` : '费用待结算'}</span><div className="flex gap-1">{canAdvance && <button className={subtleButton} disabled={polling} onClick={() => void advanceTask(task, true)}><Play className="h-3 w-3" />推进/轮询</button>}{task.status === 'OUTPUT_MISSING' && !wasRetried && <button className={subtleButton} onClick={() => taskAction(task, 'recover')}><RotateCcw className="h-3 w-3" />恢复输出</button>}{['FAILED', 'CANCELLED'].includes(task.status) && !wasRetried && <button className={subtleButton} onClick={() => taskAction(task, 'retry')}>保留历史并重试</button>}{['QUEUED', 'ACKNOWLEDGED'].includes(task.status) && <button className={`${subtleButton} text-red-300`} onClick={() => taskAction(task, 'cancel')}><Square className="h-3 w-3" />取消</button>}</div></div></div>
            })}
          </div>
          {unknownTasks.length > 0 && <div className="mt-4 rounded-xl border border-amber-500/20 bg-amber-500/5 p-4"><div className="flex gap-2"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" /><div><p className="text-xs font-medium text-amber-100">未知提交必须先对账，系统不会自动重发</p><p className="mt-1 text-[11px] leading-5 text-zinc-500">从 MiniMax 最近 7 天任务中核对模型、768P、时长与创建时间，再显式绑定 task ID。</p></div></div><div className="mt-3 grid gap-2 sm:grid-cols-[1fr_1fr_auto]"><select className={inputClass} value={reconcileTaskId} onChange={(event) => setReconcileTaskId(event.target.value)}><option value="">选择本地未知任务</option>{unknownTasks.map((task) => <option key={task.id} value={task.id}>{task.id.slice(-10)} · 镜头 {project.document.shots.find((shot) => shot.id === task.shot_id)?.sequence}</option>)}</select><input className={inputClass} placeholder="MiniMax task_id" value={remoteTaskId} onChange={(event) => setRemoteTaskId(event.target.value)} /><button className={secondaryButton} onClick={resolveUnknown} disabled={!reconcileTaskId || !remoteTaskId.trim()}>绑定</button></div><button className={`${subtleButton} mt-2`} onClick={loadCandidates}>读取并预填匹配候选</button></div>}
        </div>

        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center justify-between"><div className="flex items-center gap-2"><CheckCircle2 className="h-4 w-4 text-emerald-400" /><h3 className="font-medium text-white">真实 Take 人工审片</h3></div><span className="text-xs text-zinc-600">不自动通过</span></div>
          {!reviewTake ? <p className="mt-4 rounded-lg border border-dashed border-zinc-800 py-16 text-center text-xs text-zinc-600">H3 输出下载后在这里审片</p> : <div className="mt-4 grid gap-4 sm:grid-cols-[190px_1fr]"><div><video key={reviewTake.id} className="aspect-[9/16] max-h-80 w-full rounded-lg bg-black object-contain" src={pathToFileUrl(reviewTake.media_path)} poster={pathToFileUrl(reviewTake.thumbnail_path)} controls preload="metadata" /><select className={`${inputClass} mt-2 py-2 text-xs`} value={reviewTake.id} onChange={(event) => setReviewTakeId(event.target.value)}>{h3Takes.map((take) => <option key={take.id} value={take.id}>镜头 {project.document.shots.find((shot) => shot.id === take.shot_id)?.sequence} · Take {take.candidate_index} · {take.qa_status}</option>)}</select></div><div><div className="flex flex-wrap gap-2"><Badge status={reviewTake.qa_status}>QA {reviewTake.qa_status}</Badge><Badge status={reviewEvidence?.technical_passed ? 'SUCCEEDED' : 'FAILED'}>技术门槛 {reviewEvidence?.technical_passed ? '通过' : '未通过'}</Badge><Badge status={reviewEvidence?.purpose === 'QUALITY_PROBE' ? 'SUCCEEDED' : 'ACKNOWLEDGED'}>{reviewEvidence?.purpose === 'QUALITY_PROBE' ? '质量探针' : '合同冒烟'}</Badge></div><p className="mt-3 text-xs leading-5 text-zinc-500">逐项听看身份、服装、场景、道具、动作、画面瑕疵、锁定台词、说话人、声线、口型与音频。这里先记录单人硬门槛；正式路线结论仍需后续盲评样本。</p>{reviewEvidence?.purpose === 'CONTRACT_SMOKE' && <p className="mt-2 text-xs text-amber-300/80">合同冒烟只验证接口与媒体闭环，不能选入正式时间线。</p>}<label className="mt-3 flex items-center gap-2 text-xs text-zinc-300"><input type="checkbox" checked={reviewPassed} onChange={(event) => setReviewPassed(event.target.checked)} />全部严重失败硬门槛通过</label><label className="mt-3 block"><span className="text-xs text-zinc-500">质量分：{reviewScore.toFixed(1)} / 5</span><input className="mt-2 w-full accent-blue-500" type="range" min={0} max={5} step={0.1} value={reviewScore} onChange={(event) => setReviewScore(Number(event.target.value))} /></label><textarea className={`${inputClass} mt-3 min-h-20 resize-y text-xs leading-5`} placeholder="必填：记录通过/失败的具体画面、台词、声线和口型证据" value={reviewNotes} onChange={(event) => setReviewNotes(event.target.value)} /><div className="mt-3 flex flex-wrap gap-2"><button className={primaryButton} disabled={!reviewNotes.trim() || busy !== null} onClick={submitReview}>保存审片</button><button className={secondaryButton} disabled={reviewTake.qa_status !== 'PASSED' || reviewTake.is_selected || reviewEvidence?.purpose !== 'QUALITY_PROBE'} onClick={() => selectReviewedTake(reviewTake)}>{reviewTake.is_selected ? '已入选' : '选入时间线'}</button></div></div></div>}
        </div>
      </section>

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
        <div className="flex gap-3"><CircleDollarSign className="mt-0.5 h-5 w-5 shrink-0 text-zinc-500" /><div><p className="text-sm font-medium text-zinc-300">实验边界</p><p className="mt-1 text-xs leading-5 text-zinc-500">本页只执行 MiniMax-H3 768P。Direct、Frame 与 Reference 的真实质量结论只来自已保存的 RouteEvidence；未生成或未审片的能力保持 UNKNOWN。GPT Image、资产润色、TTS/STT 和外部口型不在此页为凑技术栈而调用。</p></div></div>
      </section>
    </div>
  )
}
