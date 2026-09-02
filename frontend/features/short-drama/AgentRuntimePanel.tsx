import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Bot,
  Braces,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  Clock3,
  Network,
  Play,
  RefreshCw,
  Sparkles,
  Wrench,
} from 'lucide-react'
import {
  ShortDramaApi,
  type AgentCapabilityCatalog,
  type AgentPlanProposal,
  type AgentRunView,
  type AgentWorkOrderResultRequest,
  type AgentWorkOrderView,
} from './api'

const primaryButton = 'inline-flex items-center justify-center gap-2 rounded-lg bg-blue-500 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-blue-400 disabled:cursor-not-allowed disabled:opacity-40'
const secondaryButton = 'inline-flex items-center justify-center gap-2 rounded-lg border border-zinc-700 bg-zinc-900 px-3.5 py-2 text-sm font-medium text-zinc-200 transition hover:border-zinc-600 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40'

type AgentRuntimePanelProps = {
  projectId: string
  onError: (message: string) => void
  onNotice: (message: string) => void
  onProjectRefresh: () => Promise<void>
  latestAutoRun: AgentRunView | null
  autoAdvancingRunId: string | null
}

function statusClasses(status: string): string {
  if (status === 'COMPLETED' || status === 'SUCCEEDED') {
    return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
  }
  if (status === 'FAILED' || status === 'CANCELLED') {
    return 'border-red-500/30 bg-red-500/10 text-red-300'
  }
  if (status === 'RUNNING') {
    return 'border-blue-500/30 bg-blue-500/10 text-blue-300'
  }
  if (status === 'WAITING' || status === 'PENDING' || status === 'IN_PROGRESS' || status === 'NEEDS_ATTENTION') {
    return 'border-amber-500/30 bg-amber-500/10 text-amber-300'
  }
  return 'border-zinc-700 bg-zinc-800/70 text-zinc-300'
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${statusClasses(status)}`}>
      {status}
    </span>
  )
}

function planJson(run: AgentRunView | null): string {
  const currentPlan = run?.snapshot.current_plan
  return JSON.stringify({
    summary: currentPlan?.summary ?? '描述这次替换计划的目标',
    goal_complete: currentPlan?.goal_complete ?? false,
    steps: currentPlan?.steps ?? [],
  }, null, 2)
}

function parsePlanProposal(value: string): AgentPlanProposal {
  const parsed: unknown = JSON.parse(value)
  if (!parsed || typeof parsed !== 'object') {
    throw new Error('proposed_plan 必须是 JSON 对象')
  }
  const candidate = parsed as { summary?: unknown; steps?: unknown }
  if (typeof candidate.summary !== 'string' || !candidate.summary.trim()) {
    throw new Error('proposed_plan.summary 必须是非空字符串')
  }
  if (!Array.isArray(candidate.steps)) {
    throw new Error('proposed_plan.steps 必须是数组')
  }
  return parsed as AgentPlanProposal
}

const agentResultListFields = [
  'created_artifacts',
  'updated_artifacts',
  'findings',
  'decisions',
  'assumptions',
  'unresolved_questions',
  'evidence_refs',
  'recommended_next_actions',
] as const

function emptyAgentResultJson(): string {
  return JSON.stringify({
    status: 'COMPLETED',
    concise_summary: '',
    created_artifacts: [],
    updated_artifacts: [],
    findings: [],
    decisions: [],
    assumptions: [],
    unresolved_questions: [],
    evidence_refs: [],
    recommended_next_actions: [],
  }, null, 2)
}

function parseAgentResult(value: string): AgentWorkOrderResultRequest {
  const parsed: unknown = JSON.parse(value)
  if (!parsed || typeof parsed !== 'object') {
    throw new Error('AgentResult 必须是 JSON 对象')
  }
  const candidate = parsed as Record<string, unknown>
  if (!['COMPLETED', 'PARTIAL', 'FAILED'].includes(String(candidate.status))) {
    throw new Error('AgentResult.status 必须是 COMPLETED、PARTIAL 或 FAILED')
  }
  if (typeof candidate.concise_summary !== 'string' || !candidate.concise_summary.trim()) {
    throw new Error('AgentResult.concise_summary 必须是非空字符串')
  }
  for (const field of agentResultListFields) {
    if (field in candidate && !Array.isArray(candidate[field])) {
      throw new Error(`AgentResult.${field} 必须是数组`)
    }
  }
  return parsed as AgentWorkOrderResultRequest
}

function isTerminalRun(run: AgentRunView): boolean {
  return ['COMPLETED', 'FAILED', 'CANCELLED'].includes(run.snapshot.run.status)
}

export function AgentRuntimePanel({ projectId, onError, onNotice, onProjectRefresh, latestAutoRun, autoAdvancingRunId }: AgentRuntimePanelProps) {
  const [capabilities, setCapabilities] = useState<AgentCapabilityCatalog | null>(null)
  const [runs, setRuns] = useState<AgentRunView[]>([])
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [objective, setObjective] = useState('')
  const [replanJson, setReplanJson] = useState(planJson(null))
  const [busyAction, setBusyAction] = useState<'load' | 'create' | 'advance' | 'continue' | 'replan' | 'user-input' | 'work-order' | 'work-order-detail' | 'work-order-result' | null>('load')
  const [activeWorkOrderId, setActiveWorkOrderId] = useState<string | null>(null)
  const [activeUserInputId, setActiveUserInputId] = useState<string | null>(null)
  const [userAnswers, setUserAnswers] = useState<Record<string, string>>({})
  const [rememberUserAnswers, setRememberUserAnswers] = useState<Record<string, boolean>>({})
  const [openWorkOrderId, setOpenWorkOrderId] = useState<string | null>(null)
  const [workOrderDetails, setWorkOrderDetails] = useState<Record<string, AgentWorkOrderView>>({})
  const [workOrderResultDrafts, setWorkOrderResultDrafts] = useState<Record<string, string>>({})
  const latestAutoRunRef = useRef<AgentRunView | null>(latestAutoRun)
  const autoAdvancingRunIdRef = useRef<string | null>(autoAdvancingRunId)
  const replanRunIdRef = useRef<string | null>(null)
  latestAutoRunRef.current = latestAutoRun
  autoAdvancingRunIdRef.current = autoAdvancingRunId

  const selectedRun = useMemo(
    () => runs.find((item) => item.snapshot.run.id === selectedRunId) ?? runs[0] ?? null,
    [runs, selectedRunId],
  )

  const replaceRun = useCallback((next: AgentRunView, select = true) => {
    setRuns((current) => {
      const existingIndex = current.findIndex((item) => item.snapshot.run.id === next.snapshot.run.id)
      if (existingIndex < 0) return [next, ...current]
      return current.map((item, index) => index === existingIndex ? next : item)
    })
    setSelectedRunId((current) => select || current === null ? next.snapshot.run.id : current)
  }, [])

  useEffect(() => {
    if (!latestAutoRun || latestAutoRun.snapshot.run.project_id !== projectId) return
    replaceRun(latestAutoRun, false)
  }, [latestAutoRun, projectId, replaceRun])

  const refresh = useCallback(async () => {
    setBusyAction('load')
    const [capabilityResult, runResult] = await Promise.all([
      ShortDramaApi.getAgentCapabilities(),
      ShortDramaApi.listAgentRuns(projectId),
    ])
    if (!capabilityResult.ok) {
      onError(capabilityResult.error.message)
    } else {
      setCapabilities(capabilityResult.data)
    }
    if (!runResult.ok) {
      onError(runResult.error.message)
    } else {
      const externalRun = latestAutoRunRef.current
      let loadedRuns = runResult.data.runs
      if (externalRun?.snapshot.run.project_id === projectId) {
        const listedIndex = loadedRuns.findIndex((item) => item.snapshot.run.id === externalRun.snapshot.run.id)
        if (listedIndex < 0) {
          loadedRuns = [externalRun, ...loadedRuns]
        } else {
          const listedRun = loadedRuns[listedIndex]
          const externalUpdatedAt = Date.parse(externalRun.snapshot.run.updated_at ?? '')
          const listedUpdatedAt = Date.parse(listedRun.snapshot.run.updated_at ?? '')
          const externalIsNewer = !Number.isNaN(externalUpdatedAt)
            && (Number.isNaN(listedUpdatedAt) || externalUpdatedAt > listedUpdatedAt)
          if (autoAdvancingRunIdRef.current === externalRun.snapshot.run.id || externalIsNewer) {
            loadedRuns = loadedRuns.map((item, index) => index === listedIndex ? externalRun : item)
          }
        }
      }
      setRuns(loadedRuns)
      setSelectedRunId((current) => (
        current && loadedRuns.some((item) => item.snapshot.run.id === current)
          ? current
          : loadedRuns[0]?.snapshot.run.id ?? null
      ))
    }
    setBusyAction(null)
  }, [onError, projectId])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    const runId = selectedRun?.snapshot.run.id ?? null
    if (replanRunIdRef.current === runId) return
    replanRunIdRef.current = runId
    setReplanJson(planJson(selectedRun))
  }, [selectedRun])

  const createRun = async () => {
    if (autoAdvancingRunId !== null) return
    const trimmedObjective = objective.trim()
    if (!trimmedObjective) {
      onError('请先输入本次 Agent 运行的目标')
      return
    }
    setBusyAction('create')
    const result = await ShortDramaApi.createAgentRun(projectId, {
      objective: trimmedObjective,
      root_agent: 'production-manager',
      auto_plan: true,
      initial_plan: null,
      unattended: false,
    })
    setBusyAction(null)
    if (!result.ok) {
      onError(result.error.message)
      return
    }
    replaceRun(result.data)
    setObjective('')
    setReplanJson(planJson(result.data))
    onNotice(result.data.snapshot.current_plan
      ? 'Agent 运行已创建，动态计划已载入'
      : 'Agent 运行已创建；可配置语言模型或在高级区提交 proposed_plan')
  }

  const advanceRun = async () => {
    if (!selectedRun || autoAdvancingRunId !== null) return
    setBusyAction('advance')
    const result = await ShortDramaApi.advanceAgentRun(selectedRun.snapshot.run.id, 1)
    setBusyAction(null)
    if (!result.ok) {
      onError(result.error.message)
      return
    }
    replaceRun(result.data.run)
    await onProjectRefresh()
    const executed = result.data.executed_action_ids.length
    const delegated = result.data.created_work_order_ids.length
    onNotice(`Agent 已执行 ${executed} 个动作${delegated ? `，创建 ${delegated} 个 Work Order` : ''}`)
  }

  const continueRun = async () => {
    if (!selectedRun || autoAdvancingRunId !== null) return
    const runId = selectedRun.snapshot.run.id
    let latest = selectedRun
    let executed = 0
    let delegated = 0
    let reachedBatchLimit = true
    setBusyAction('continue')
    for (let batch = 0; batch < 8; batch += 1) {
      const result = await ShortDramaApi.advanceAgentRun(runId, 8)
      if (!result.ok) {
        await onProjectRefresh()
        setBusyAction(null)
        onError(result.error.message)
        return
      }
      latest = result.data.run
      executed += result.data.executed_action_ids.length
      delegated += result.data.created_work_order_ids.length
      replaceRun(latest)
      const waitingForUser = (latest.snapshot.actions ?? []).some((action) => (
        action.kind === 'USER_INPUT' && action.status === 'RUNNING'
      ))
      if (
        isTerminalRun(latest)
        || (latest.snapshot.pending_work_orders?.length ?? 0) > 0
        || waitingForUser
        || (result.data.executed_action_ids.length === 0 && latest.ready_steps.length === 0)
      ) {
        reachedBatchLimit = false
        break
      }
    }
    await onProjectRefresh()
    setBusyAction(null)
    if (latest.snapshot.run.status === 'COMPLETED') {
      onNotice(`Agent 运行已完成，本次连续执行 ${executed} 个动作`)
      return
    }
    if (reachedBatchLimit) {
      onNotice(`Agent 已执行 ${executed} 个动作，达到本次前端连续执行上限；可再次继续`)
      return
    }
    onNotice(`Agent 已执行 ${executed} 个动作${delegated ? `，等待 ${delegated} 个 Work Order` : ''}，当前状态 ${latest.snapshot.run.status}`)
  }

  const submitUserInput = async (actionId: string) => {
    if (!selectedRun || autoAdvancingRunId !== null) return
    const answer = (userAnswers[actionId] ?? '').trim()
    if (!answer) {
      onError('请先输入对 Agent 问题的回复')
      return
    }
    setBusyAction('user-input')
    setActiveUserInputId(actionId)
    const result = await ShortDramaApi.submitAgentUserInput(selectedRun.snapshot.run.id, {
      action_id: actionId,
      answer,
      remember_as_decision: rememberUserAnswers[actionId] ?? true,
    })
    setBusyAction(null)
    setActiveUserInputId(null)
    if (!result.ok) {
      onError(result.error.message)
      return
    }
    replaceRun(result.data)
    await onProjectRefresh()
    setUserAnswers((current) => {
      const next = { ...current }
      delete next[actionId]
      return next
    })
    setRememberUserAnswers((current) => {
      const next = { ...current }
      delete next[actionId]
      return next
    })
    onNotice(result.data.snapshot.run.status === 'COMPLETED'
      ? '用户回复已提交，Agent 运行已完成'
      : '用户回复已提交，可继续运行')
  }

  const replanRun = async () => {
    if (!selectedRun || autoAdvancingRunId !== null) return
    let proposedPlan: AgentPlanProposal
    try {
      proposedPlan = parsePlanProposal(replanJson)
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : String(caught))
      return
    }
    setBusyAction('replan')
    const result = await ShortDramaApi.replanAgentRun(selectedRun.snapshot.run.id, {
      reason: '用户在 Agent 面板中提交了替换计划',
      proposed_plan: proposedPlan,
    })
    setBusyAction(null)
    if (!result.ok) {
      onError(result.error.message)
      return
    }
    replaceRun(result.data)
    setReplanJson(planJson(result.data))
    onNotice('动态计划已替换')
  }

  const executeWorkOrder = async (workOrderId: string) => {
    if (autoAdvancingRunId !== null) return
    setBusyAction('work-order')
    setActiveWorkOrderId(workOrderId)
    const result = await ShortDramaApi.executeAgentWorkOrder(workOrderId)
    setBusyAction(null)
    setActiveWorkOrderId(null)
    if (!result.ok) {
      onError(result.error.message)
      return
    }
    replaceRun(result.data.run)
    await onProjectRefresh()
    setOpenWorkOrderId(null)
    setWorkOrderDetails((current) => {
      const next = { ...current }
      delete next[workOrderId]
      return next
    })
    onNotice(`子 Agent 已返回：${result.data.result.concise_summary}`)
  }

  const toggleWorkOrderDetails = async (workOrderId: string) => {
    if (openWorkOrderId === workOrderId) {
      setOpenWorkOrderId(null)
      return
    }
    setOpenWorkOrderId(workOrderId)
    setWorkOrderResultDrafts((current) => (
      current[workOrderId]
        ? current
        : { ...current, [workOrderId]: emptyAgentResultJson() }
    ))
    if (workOrderDetails[workOrderId]) return
    setBusyAction('work-order-detail')
    setActiveWorkOrderId(workOrderId)
    const result = await ShortDramaApi.getAgentWorkOrder(workOrderId)
    setBusyAction(null)
    setActiveWorkOrderId(null)
    if (!result.ok) {
      setOpenWorkOrderId(null)
      onError(result.error.message)
      return
    }
    setWorkOrderDetails((current) => ({ ...current, [workOrderId]: result.data }))
  }

  const submitWorkOrderResult = async (workOrderId: string) => {
    if (autoAdvancingRunId !== null) return
    let payload: AgentWorkOrderResultRequest
    try {
      payload = parseAgentResult(workOrderResultDrafts[workOrderId] ?? emptyAgentResultJson())
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : String(caught))
      return
    }
    setBusyAction('work-order-result')
    setActiveWorkOrderId(workOrderId)
    const result = await ShortDramaApi.submitAgentWorkOrderResult(workOrderId, payload)
    setBusyAction(null)
    setActiveWorkOrderId(null)
    if (!result.ok) {
      onError(result.error.message)
      return
    }
    replaceRun(result.data.run)
    await onProjectRefresh()
    setOpenWorkOrderId(null)
    setWorkOrderDetails((current) => {
      const next = { ...current }
      delete next[workOrderId]
      return next
    })
    setWorkOrderResultDrafts((current) => {
      const next = { ...current }
      delete next[workOrderId]
      return next
    })
    onNotice(result.data.run.snapshot.run.status === 'COMPLETED'
      ? `AgentResult 已提交，运行已完成：${result.data.result.concise_summary}`
      : `AgentResult 已提交：${result.data.result.concise_summary}`)
  }

  const toolCategories = useMemo(() => {
    const counts = new Map<string, number>()
    for (const tool of capabilities?.tools ?? []) {
      counts.set(tool.category, (counts.get(tool.category) ?? 0) + 1)
    }
    return Array.from(counts.entries()).sort(([left], [right]) => left.localeCompare(right))
  }, [capabilities])

  const readyStepIds = useMemo(
    () => new Set(selectedRun?.ready_steps.map((step) => step.id) ?? []),
    [selectedRun],
  )
  const actionsByStep = useMemo(() => new Map(
    (selectedRun?.snapshot.actions ?? [])
      .filter((action) => action.plan_step_id)
      .map((action) => [action.plan_step_id as string, action]),
  ), [selectedRun])
  const pendingUserInputActions = useMemo(
    () => (selectedRun?.snapshot.actions ?? []).filter((action) => (
      action.kind === 'USER_INPUT' && action.status === 'RUNNING'
    )),
    [selectedRun],
  )
  const projectHasAutoAdvancingRun = autoAdvancingRunId !== null
  const canAdvanceSelectedRun = selectedRun !== null
    && !isTerminalRun(selectedRun)
    && selectedRun.snapshot.current_plan !== null
    && pendingUserInputActions.length === 0
    && (selectedRun.snapshot.pending_work_orders?.length ?? 0) === 0
    && !projectHasAutoAdvancingRun

  return (
    <div className="space-y-6">
      <section className="rounded-2xl border border-blue-500/20 bg-gradient-to-br from-blue-500/10 via-zinc-950 to-cyan-500/5 p-6">
        <div className="flex flex-col gap-5 xl:flex-row xl:items-start xl:justify-between">
          <div className="max-w-3xl">
            <div className="flex items-center gap-2 text-sm font-semibold text-blue-200">
              <Bot className="h-5 w-5" />
              Agent Runtime
            </div>
            <h2 className="mt-3 text-2xl font-semibold text-white">由 Agent 编排 Skill 与 Tool</h2>
            <p className="mt-2 text-sm leading-6 text-zinc-400">
              计划是可替换的依赖图；运行时负责保存进度、装配项目上下文、执行有限动作并传递结构化 Work Order。
            </p>
          </div>
          <button className={secondaryButton} onClick={() => void refresh()} disabled={busyAction !== null}>
            <RefreshCw className={`h-4 w-4 ${busyAction === 'load' ? 'animate-spin' : ''}`} />刷新能力与运行
          </button>
        </div>
      </section>

      <section className="grid gap-3 md:grid-cols-2">
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-medium text-zinc-200"><Sparkles className="h-4 w-4 text-violet-400" />Skills</div>
            <span className="text-2xl font-semibold text-white">{capabilities?.skills.length ?? '—'}</span>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {capabilities?.skills.map((skill) => (
              <span key={skill.id} className="rounded-md border border-violet-500/20 bg-violet-500/10 px-2.5 py-1 text-xs text-violet-200" title={skill.recommended_playbook.join(' → ')}>
                {skill.label}
              </span>
            ))}
          </div>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-medium text-zinc-200"><Wrench className="h-4 w-4 text-cyan-400" />Tools</div>
            <span className="text-2xl font-semibold text-white">{capabilities?.tools.length ?? '—'}</span>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {toolCategories.map(([category, count]) => (
              <span key={category} className="rounded-md border border-cyan-500/20 bg-cyan-500/10 px-2.5 py-1 text-xs text-cyan-200">{category} · {count}</span>
            ))}
          </div>
        </div>
      </section>

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
        <div className="grid gap-3 lg:grid-cols-[1fr_auto]">
          <label>
            <span className="mb-2 block text-xs font-medium text-zinc-400">新运行目标</span>
            <textarea
              value={objective}
              onChange={(event) => setObjective(event.target.value)}
              rows={3}
              placeholder="例如：只调整第二场的冲突节奏，并更新受影响的分镜计划。"
              className="w-full resize-y rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm leading-6 text-zinc-200 outline-none transition placeholder:text-zinc-700 focus:border-blue-500"
            />
          </label>
          <button className={`${primaryButton} self-end`} onClick={() => void createRun()} disabled={busyAction !== null || autoAdvancingRunId !== null || !objective.trim()}>
            {busyAction === 'create' ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
            创建并自动规划
          </button>
        </div>
        {autoAdvancingRunId !== null && <p className="mt-3 text-xs text-blue-300">当前项目正在无人值守推进，暂停或完成后可创建新运行。</p>}
      </section>

      <section className="grid min-h-[520px] gap-4 xl:grid-cols-[300px_minmax(0,1fr)]">
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-3">
          <div className="flex items-center justify-between px-2 pb-3">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-zinc-500">Runs</p>
            <span className="text-xs text-zinc-600">{runs.length}</span>
          </div>
          <div className="space-y-2">
            {runs.map((item) => {
              const run = item.snapshot.run
              const selected = selectedRun?.snapshot.run.id === run.id
              return (
                <button
                  key={run.id}
                  type="button"
                  onClick={() => {
                    setSelectedRunId(run.id)
                    setReplanJson(planJson(item))
                  }}
                  className={`w-full rounded-lg border p-3 text-left transition ${selected ? 'border-blue-500/40 bg-blue-500/10' : 'border-zinc-800 bg-zinc-950/60 hover:border-zinc-700'}`}
                >
                  <div className="flex items-center justify-between gap-2"><StatusBadge status={run.status} /><ChevronRight className="h-4 w-4 text-zinc-600" /></div>
                  <p className="mt-2 line-clamp-3 text-xs leading-5 text-zinc-300">{run.objective}</p>
                  <p className="mt-2 truncate text-[10px] text-zinc-600">{run.id}</p>
                </button>
              )
            })}
            {runs.length === 0 && busyAction !== 'load' && (
              <div className="rounded-lg border border-dashed border-zinc-800 px-4 py-8 text-center text-xs leading-5 text-zinc-600">这个项目还没有 Agent 运行。</div>
            )}
          </div>
        </div>

        <div className="min-w-0 space-y-4">
          {selectedRun ? (
            <>
              <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2"><StatusBadge status={selectedRun.snapshot.run.status} />{(selectedRun.snapshot.run.active_skill_ids ?? []).map((skillId) => <span key={skillId} className="text-[11px] text-violet-300">{skillId}</span>)}</div>
                    <h3 className="mt-3 text-lg font-semibold text-white">{selectedRun.snapshot.run.objective}</h3>
                    <p className="mt-1 text-xs text-zinc-600">{selectedRun.snapshot.run.root_agent} · {selectedRun.snapshot.run.id}</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      className={secondaryButton}
                      onClick={() => void advanceRun()}
                      disabled={busyAction !== null || !canAdvanceSelectedRun}
                    >
                      {busyAction === 'advance' ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                      单步执行
                    </button>
                    <button
                      className={primaryButton}
                      onClick={() => void continueRun()}
                      disabled={busyAction !== null || !canAdvanceSelectedRun}
                    >
                      {busyAction === 'continue' ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                      继续到暂停或完成
                    </button>
                  </div>
                </div>
                <div className="mt-4 grid gap-3 sm:grid-cols-3">
                  <div className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3"><p className="text-[10px] uppercase tracking-[0.14em] text-zinc-600">Plan Revision</p><p className="mt-1 text-lg font-semibold text-white">{selectedRun.snapshot.current_plan?.revision ?? '—'}</p></div>
                  <div className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3"><p className="text-[10px] uppercase tracking-[0.14em] text-zinc-600">Ready Steps</p><p className="mt-1 text-lg font-semibold text-white">{selectedRun.ready_steps.length}</p></div>
                  <div className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3"><p className="text-[10px] uppercase tracking-[0.14em] text-zinc-600">Actions</p><p className="mt-1 text-lg font-semibold text-white">{selectedRun.snapshot.actions?.length ?? 0}</p></div>
                </div>
                {projectHasAutoAdvancingRun && (
                  <p className="mt-3 rounded-lg border border-blue-500/20 bg-blue-500/10 px-3 py-2 text-xs leading-5 text-blue-200">
                    当前项目正在无人值守推进，所有服务器写操作已暂时禁用。
                  </p>
                )}
              </div>

              {pendingUserInputActions.length > 0 && (
                <div className="rounded-xl border border-amber-500/25 bg-amber-500/5 p-5">
                  <div className="flex items-center gap-2"><CircleDot className="h-4 w-4 text-amber-400" /><h3 className="font-medium text-amber-100">Agent 等待用户回复</h3></div>
                  <div className="mt-4 space-y-4">
                    {pendingUserInputActions.map((action) => (
                      <div key={action.id} className="rounded-lg border border-amber-500/20 bg-zinc-950/60 p-4">
                        <p className="text-sm leading-6 text-zinc-200">{action.input_summary}</p>
                        <textarea
                          value={userAnswers[action.id] ?? ''}
                          onChange={(event) => setUserAnswers((current) => ({ ...current, [action.id]: event.target.value }))}
                          rows={3}
                          placeholder="输入决定、补充信息或修改要求……"
                          className="mt-3 w-full resize-y rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2.5 text-sm leading-6 text-zinc-200 outline-none transition placeholder:text-zinc-700 focus:border-amber-500"
                        />
                        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                          <label className="flex items-center gap-2 text-xs text-zinc-400">
                            <input
                              type="checkbox"
                              checked={rememberUserAnswers[action.id] ?? true}
                              onChange={(event) => setRememberUserAnswers((current) => ({ ...current, [action.id]: event.target.checked }))}
                            />
                            记为项目决策，供后续 Agent 使用
                          </label>
                          <button
                            type="button"
                            className={primaryButton}
                            onClick={() => void submitUserInput(action.id)}
                            disabled={busyAction !== null || projectHasAutoAdvancingRun || !(userAnswers[action.id] ?? '').trim()}
                          >
                            {activeUserInputId === action.id ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                            提交回复
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
                <div className="flex items-center gap-2"><Network className="h-4 w-4 text-blue-400" /><h3 className="font-medium text-white">动态计划</h3></div>
                <p className="mt-1 text-xs leading-5 text-zinc-500">{selectedRun.snapshot.current_plan?.summary ?? '尚未生成计划；可在下方提交 proposed_plan。'}</p>
                <div className="mt-4 space-y-3">
                  {(selectedRun.snapshot.current_plan?.steps ?? []).map((step, index) => {
                    const action = actionsByStep.get(step.id)
                    const ready = readyStepIds.has(step.id)
                    return (
                      <div key={step.id} className={`rounded-lg border p-4 ${ready ? 'border-blue-500/30 bg-blue-500/5' : 'border-zinc-800 bg-zinc-950/60'}`}>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-zinc-800 text-[11px] font-semibold text-zinc-300">{index + 1}</span>
                          <p className="font-medium text-zinc-100">{step.title}</p>
                          {ready && <span className="rounded-full border border-blue-500/30 bg-blue-500/10 px-2 py-0.5 text-[10px] text-blue-300">READY</span>}
                          {action && <StatusBadge status={action.status} />}
                        </div>
                        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-zinc-500"><span>{step.kind}</span><span>{step.assigned_agent}</span>{step.tool_name && <span className="text-cyan-400">{step.tool_name}</span>}{(step.depends_on?.length ?? 0) > 0 && <span>依赖：{step.depends_on?.join(', ')}</span>}</div>
                        <p className="mt-2 text-xs leading-5 text-zinc-400">{step.instructions}</p>
                      </div>
                    )
                  })}
                  {(selectedRun.snapshot.current_plan?.steps?.length ?? 0) === 0 && <div className="rounded-lg border border-dashed border-zinc-800 px-4 py-8 text-center text-xs text-zinc-600">当前计划为空。</div>}
                </div>
              </div>

              <div className="grid gap-4 lg:grid-cols-2">
                <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
                  <div className="flex items-center gap-2"><Clock3 className="h-4 w-4 text-emerald-400" /><h3 className="font-medium text-white">Actions</h3></div>
                  <div className="mt-4 space-y-2">
                    {(selectedRun.snapshot.actions ?? []).slice().reverse().map((action) => (
                      <div key={action.id} className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3">
                        <div className="flex flex-wrap items-center gap-2"><StatusBadge status={action.status} /><span className="text-xs text-zinc-300">{action.kind}</span>{action.tool_name && <span className="text-[11px] text-cyan-400">{action.tool_name}</span>}</div>
                        <p className="mt-2 text-xs leading-5 text-zinc-500">{action.output_summary ?? action.error_message ?? action.input_summary}</p>
                      </div>
                    ))}
                    {(selectedRun.snapshot.actions?.length ?? 0) === 0 && <p className="rounded-lg border border-dashed border-zinc-800 px-4 py-8 text-center text-xs text-zinc-600">尚未执行动作。</p>}
                  </div>
                </div>
                <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
                  <div className="flex items-center gap-2"><Bot className="h-4 w-4 text-amber-400" /><h3 className="font-medium text-white">Pending Work Orders</h3></div>
                  <div className="mt-4 space-y-2">
                    {(selectedRun.snapshot.pending_work_orders ?? []).map((order) => {
                      const details = workOrderDetails[order.id]
                      const isOpen = openWorkOrderId === order.id
                      return (
                        <div key={order.id} className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3">
                          <div className="flex flex-wrap items-center gap-2"><StatusBadge status={order.status} /><span className="text-xs font-medium text-zinc-200">{order.recipient_role}</span></div>
                          <p className="mt-2 text-xs leading-5 text-zinc-400">{order.objective}</p>
                          <p className="mt-1 text-[11px] leading-5 text-zinc-600">{order.why_now}</p>
                          <div className="mt-3 grid gap-2 sm:grid-cols-2">
                            <button
                              type="button"
                              className={secondaryButton}
                              onClick={() => void toggleWorkOrderDetails(order.id)}
                              disabled={busyAction !== null}
                            >
                              {activeWorkOrderId === order.id && busyAction === 'work-order-detail'
                                ? <RefreshCw className="h-4 w-4 animate-spin" />
                                : <ChevronRight className={`h-4 w-4 transition ${isOpen ? 'rotate-90' : ''}`} />}
                              {isOpen ? '收起详情' : '查看详情'}
                            </button>
                            <button
                              type="button"
                              className={secondaryButton}
                              onClick={() => void executeWorkOrder(order.id)}
                              disabled={busyAction !== null || projectHasAutoAdvancingRun || order.status === 'IN_PROGRESS'}
                            >
                              {activeWorkOrderId === order.id && busyAction === 'work-order' ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Bot className="h-4 w-4" />}
                              {order.status === 'IN_PROGRESS' ? '子 Agent 执行中' : '运行子 Agent'}
                            </button>
                          </div>

                          {isOpen && details && (
                            <div className="mt-3 space-y-3 border-t border-amber-500/15 pt-3">
                              <div className="grid gap-2 text-[11px] text-zinc-400 sm:grid-cols-2">
                                <p><span className="text-zinc-600">Sender：</span>{details.work_order.sender}</p>
                                <p><span className="text-zinc-600">Scope：</span>{details.work_order.scope?.length ?? 0} 个产物</p>
                              </div>
                              {(details.work_order.constraints?.length ?? 0) > 0 && <div><p className="text-[11px] font-medium text-zinc-500">约束</p>{details.work_order.constraints?.map((item) => <p key={item} className="mt-1 text-xs leading-5 text-zinc-400">· {item}</p>)}</div>}
                              {(details.work_order.confirmed_decisions?.length ?? 0) > 0 && <div><p className="text-[11px] font-medium text-zinc-500">已确认决策</p>{details.work_order.confirmed_decisions?.map((item) => <p key={item} className="mt-1 text-xs leading-5 text-zinc-400">· {item}</p>)}</div>}
                              {(details.work_order.open_questions?.length ?? 0) > 0 && <div><p className="text-[11px] font-medium text-zinc-500">待解问题</p>{details.work_order.open_questions?.map((item) => <p key={item} className="mt-1 text-xs leading-5 text-zinc-400">· {item}</p>)}</div>}
                              {(details.work_order.acceptance_criteria?.length ?? 0) > 0 && <div><p className="text-[11px] font-medium text-zinc-500">验收标准</p>{details.work_order.acceptance_criteria?.map((item) => <p key={item} className="mt-1 text-xs leading-5 text-zinc-400">· {item}</p>)}</div>}
                              <details className="rounded-lg border border-zinc-800 bg-zinc-950/70 p-3">
                                <summary className="cursor-pointer text-xs font-medium text-zinc-300">查看子 Agent System Prompt</summary>
                                <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-zinc-500">{details.system_prompt}</pre>
                              </details>
                              <details className="rounded-lg border border-zinc-800 bg-zinc-950/70 p-3">
                                <summary className="cursor-pointer text-xs font-medium text-zinc-300">查看完整 WorkOrder</summary>
                                <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-zinc-500">{JSON.stringify(details.work_order, null, 2)}</pre>
                              </details>
                              <details className="rounded-lg border border-zinc-800 bg-zinc-950/70 p-3">
                                <summary className="cursor-pointer text-xs font-medium text-zinc-300">查看完整 ContextPack</summary>
                                <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-zinc-500">{JSON.stringify(details.context, null, 2)}</pre>
                              </details>
                              <div className="rounded-lg border border-violet-500/20 bg-violet-500/5 p-3">
                                <p className="text-xs font-medium text-violet-200">提交人工 / 外部 AgentResult</p>
                                <p className="mt-1 text-[11px] leading-5 text-zinc-500">COMPLETED 继续计划；PARTIAL 保留待补充项；FAILED 记录失败原因。</p>
                                <textarea
                                  value={workOrderResultDrafts[order.id] ?? emptyAgentResultJson()}
                                  onChange={(event) => setWorkOrderResultDrafts((current) => ({ ...current, [order.id]: event.target.value }))}
                                  rows={14}
                                  spellCheck={false}
                                  className="mt-3 w-full resize-y rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-3 font-mono text-[11px] leading-5 text-zinc-300 outline-none transition focus:border-violet-500"
                                />
                                <button
                                  type="button"
                                  className={`${primaryButton} mt-3 w-full`}
                                  onClick={() => void submitWorkOrderResult(order.id)}
                                  disabled={busyAction !== null || projectHasAutoAdvancingRun}
                                >
                                  {activeWorkOrderId === order.id && busyAction === 'work-order-result' ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                                  提交 AgentResult
                                </button>
                              </div>
                            </div>
                          )}
                        </div>
                      )
                    })}
                    {(selectedRun.snapshot.pending_work_orders?.length ?? 0) === 0 && <p className="rounded-lg border border-dashed border-zinc-800 px-4 py-8 text-center text-xs text-zinc-600">没有等待返回的 Work Order。</p>}
                  </div>
                </div>
              </div>

              <details className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
                <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-medium text-zinc-200"><Braces className="h-4 w-4 text-violet-400" />高级：提交 proposed_plan JSON</summary>
                <p className="mt-3 text-xs leading-5 text-zinc-500">提交后会创建新的 Plan Revision；现有项目产物与动作记录仍保留。</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button className={secondaryButton} type="button" onClick={() => setReplanJson(planJson(selectedRun))}>载入当前计划</button>
                </div>
                <textarea
                  value={replanJson}
                  onChange={(event) => setReplanJson(event.target.value)}
                  rows={16}
                  spellCheck={false}
                  className="mt-3 w-full resize-y rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-3 font-mono text-xs leading-5 text-zinc-300 outline-none transition focus:border-violet-500"
                />
                <button className={`${primaryButton} mt-3`} onClick={() => void replanRun()} disabled={busyAction !== null || projectHasAutoAdvancingRun}>
                  {busyAction === 'replan' ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Network className="h-4 w-4" />}
                  替换动态计划
                </button>
              </details>

              {((selectedRun.context.blockers?.length ?? 0) > 0 || (selectedRun.context.open_questions?.length ?? 0) > 0) && (
                <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-4">
                  <div className="flex gap-3"><CircleDot className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" /><div><p className="text-sm font-medium text-amber-100">运行上下文仍有待处理项</p>{(selectedRun.context.blockers ?? []).map((item) => <p key={item} className="mt-1 text-xs text-zinc-400">阻断：{item}</p>)}{(selectedRun.context.open_questions ?? []).map((item) => <p key={item} className="mt-1 text-xs text-zinc-400">问题：{item}</p>)}</div></div>
                </div>
              )}
            </>
          ) : (
            <div className="flex min-h-[520px] items-center justify-center rounded-xl border border-dashed border-zinc-800 bg-zinc-900/30 p-8 text-center">
              <div><CheckCircle2 className="mx-auto h-8 w-8 text-zinc-700" /><p className="mt-3 text-sm text-zinc-400">创建一个目标后，这里会显示动态计划与执行记录。</p></div>
            </div>
          )}
        </div>
      </section>

      <details className="rounded-xl border border-zinc-800 bg-zinc-900/40 p-4">
        <summary className="cursor-pointer text-xs font-medium text-zinc-400">查看完整 Tool 清单</summary>
        <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {capabilities?.tools.map((tool) => (
            <div key={tool.id} className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3"><div className="flex items-center justify-between gap-2"><span className="text-xs font-medium text-cyan-300">{tool.id}</span><span className="text-[10px] text-zinc-600">{tool.effect}</span></div><p className="mt-1 text-[11px] leading-5 text-zinc-500">{tool.description}</p></div>
          ))}
        </div>
      </details>
    </div>
  )
}
