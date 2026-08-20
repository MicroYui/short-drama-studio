import type { components } from '../../generated/backend-openapi'
import { makeEndpointClient } from '../../lib/api-client'

export type ProjectAggregate = components['schemas']['ProjectAggregate']
export type ProjectRecord = components['schemas']['ProjectRecord']
export type GenerationTask = components['schemas']['GenerationTask']
export type Take = components['schemas']['Take']
export type ShotPlan = components['schemas']['ShotPlan']
export type ScriptLine = components['schemas']['ScriptLine']
export type TimelineManifest = components['schemas']['TimelineManifest']
export type ExportRecord = components['schemas']['ExportRecord']
export type SourceMode = components['schemas']['SourceMode']
export type RunMode = components['schemas']['RunMode']
export type AutonomousExecutionTarget = components['schemas']['AutonomousExecutionTarget']
export type OperationKind = components['schemas']['OperationKind']
export type VideoGenerationRoute = components['schemas']['VideoGenerationRoute']
export type H3ProviderConfig = components['schemas']['H3ProviderConfigResponse']
export type H3ProviderRun = components['schemas']['H3ProviderRun']
export type H3ProbeRequest = components['schemas']['H3ProbeRequest']
export type H3CompilePreview = components['schemas']['H3CompilePreviewResponse']
export type H3ProductionRoutePlan = components['schemas']['H3ProductionRoutePlan']
export type H3ProductionRoutePlanRequest = components['schemas']['H3ProductionRoutePlanRequest']
export type H3ProductionRouteExecuteRequest = components['schemas']['H3ProductionRouteExecuteRequest']
export type H3ProductionRouteExecuteResponse = components['schemas']['H3ProductionRouteExecuteResponse']
export type H3MediaInput = components['schemas']['H3MediaInput']
export type H3MediaKind = components['schemas']['H3MediaKind']
export type H3MediaRole = components['schemas']['H3MediaRole']
export type H3TakeReviewRequest = components['schemas']['H3TakeReviewRequest']
export type RouteEvidence = components['schemas']['RouteEvidence']
export type Asset = components['schemas']['Asset']
export type AssetVersion = components['schemas']['AssetVersion']
export type AssetKind = components['schemas']['AssetKind']
export type AssetMediaType = components['schemas']['AssetMediaType']
export type ImageGenerationTask = components['schemas']['ImageGenerationTask']
export type ImageTaskCreateRequest = components['schemas']['ImageTaskCreateRequest']
export type ImageProviderConfig = components['schemas']['ImageProviderConfigResponse']
export type LanguageProviderConfig = components['schemas']['LanguageProviderConfigResponse']
export type LanguagePromptCompileResponse = components['schemas']['LanguagePromptCompileResponse']
export type ReferencePackEntryCreateRequest = components['schemas']['ReferencePackEntryCreateRequest']
export type ReferencePackEntryMutationResponse = components['schemas']['ReferencePackEntryMutationResponse']
export type ShotReferencePlanUpdateRequest = components['schemas']['ShotReferencePlanUpdateRequest-Input']
export type ReferencePlanPreviewRequest = components['schemas']['ReferencePlanPreviewRequest']
export type ReferencePlanPreviewResponse = components['schemas']['ReferencePlanPreviewResponse']
export type KeyframeBriefCompileRequest = components['schemas']['KeyframeBriefCompileRequest']
export type KeyframeBriefCompileResponse = components['schemas']['KeyframeBriefCompileResponse']
export type DirectorAdviceRequest = components['schemas']['DirectorAdviceRequest']
export type DirectorAdviceResponse = components['schemas']['DirectorAdviceResponse']
export type DirectorLoopResponse = components['schemas']['DirectorLoopResponse']
export type DirectorRepairDecisionRequest = components['schemas']['DirectorRepairDecisionRequest-Input']
export type H3CapabilityProfile = components['schemas']['H3CapabilityProfileResponse']
export type QaFailureCategory = components['schemas']['QaFailureCategory']
export type CreativeInputImage = components['schemas']['CreativeInputImage']
export type VideoInspectionReport = components['schemas']['VideoInspectionReport']
export type VideoInspectionRequest = components['schemas']['VideoInspectionRequest']
export type AudioInspectionReport = components['schemas']['AudioInspectionReport']
export type AudioInspectionRequest = components['schemas']['AudioInspectionRequest']
export type AutonomousRepeatCreateRequest = components['schemas']['AutonomousRepeatCreateRequest']

const listProjects = makeEndpointClient('/api/short-drama/projects', 'get')
const createProject = makeEndpointClient('/api/short-drama/projects', 'post')
const createAutonomousDraft = makeEndpointClient('/api/short-drama/projects/autonomous-draft', 'post')
const createAutonomousRepeat = makeEndpointClient('/api/short-drama/projects/{source_project_id}/autonomous-repeats', 'post')
const advanceAutonomousRun = makeEndpointClient('/api/short-drama/projects/{project_id}/autonomous-runs/{run_id}/advance', 'post')
const initializeFixture = makeEndpointClient('/api/short-drama/fixtures/dont-trust-me', 'post')
const getProject = makeEndpointClient('/api/short-drama/projects/{project_id}', 'get')
const autoDraft = makeEndpointClient('/api/short-drama/projects/{project_id}/auto-draft', 'post')
const enqueue = makeEndpointClient('/api/short-drama/projects/{project_id}/enqueue', 'post')
const drain = makeEndpointClient('/api/short-drama/worker/drain', 'post')
const rebuildManifest = makeEndpointClient('/api/short-drama/projects/{project_id}/manifest/rebuild', 'post')
const exportProject = makeEndpointClient('/api/short-drama/projects/{project_id}/export', 'post')
const reviseScriptLine = makeEndpointClient('/api/short-drama/projects/{project_id}/script/revise-line', 'post')
const selectTake = makeEndpointClient('/api/short-drama/takes/{take_id}/select', 'post')
const inspectTakeVideo = makeEndpointClient('/api/short-drama/takes/{take_id}/inspect-video', 'post')
const inspectProjectAudio = makeEndpointClient('/api/short-drama/projects/{project_id}/inspect-audio', 'post')
const advanceTask = makeEndpointClient('/api/short-drama/tasks/{task_id}/advance', 'post')
const cancelTask = makeEndpointClient('/api/short-drama/tasks/{task_id}/cancel', 'post')
const retryTask = makeEndpointClient('/api/short-drama/tasks/{task_id}/retry', 'post')
const reconcileTask = makeEndpointClient('/api/short-drama/tasks/{task_id}/reconcile', 'post')
const recoverTaskOutput = makeEndpointClient('/api/short-drama/tasks/{task_id}/recover-output', 'post')
const retake = makeEndpointClient('/api/short-drama/projects/{project_id}/shots/{shot_id}/retake', 'post')
const extend = makeEndpointClient('/api/short-drama/projects/{project_id}/shots/{shot_id}/extend', 'post')
const gapFill = makeEndpointClient('/api/short-drama/projects/{project_id}/shots/{shot_id}/gap-fill', 'post')
const getH3Config = makeEndpointClient('/api/short-drama/providers/minimax-h3', 'get')
const updateH3Config = makeEndpointClient('/api/short-drama/providers/minimax-h3', 'post')
const validateH3Config = makeEndpointClient('/api/short-drama/providers/minimax-h3/validate', 'post')
const previewH3Probe = makeEndpointClient('/api/short-drama/projects/{project_id}/h3/preview', 'post')
const compileH3ProductionRoutePlan = makeEndpointClient('/api/short-drama/projects/{project_id}/h3/production-route-plans', 'post')
const listH3ProductionRoutePlans = makeEndpointClient('/api/short-drama/projects/{project_id}/h3/production-route-plans', 'get')
const executeH3ProductionRoutePlan = makeEndpointClient('/api/short-drama/projects/{project_id}/h3/production-route-plans/{plan_id}/execute', 'post')
const enqueueH3Probe = makeEndpointClient('/api/short-drama/projects/{project_id}/h3/probes', 'post')
const listH3Runs = makeEndpointClient('/api/short-drama/projects/{project_id}/h3/runs', 'get')
const listH3ReconciliationCandidates = makeEndpointClient('/api/short-drama/providers/minimax-h3/reconciliation-candidates', 'get')
const resolveH3Submission = makeEndpointClient('/api/short-drama/tasks/{task_id}/h3/resolve-submit', 'post')
const resolveH3NotSubmitted = makeEndpointClient('/api/short-drama/tasks/{task_id}/h3/resolve-not-submitted', 'post')
const reviewH3Take = makeEndpointClient('/api/short-drama/takes/{take_id}/h3/review', 'post')
const getImageConfig = makeEndpointClient('/api/short-drama/providers/gpt-image', 'get')
const updateImageConfig = makeEndpointClient('/api/short-drama/providers/gpt-image', 'post')
const validateImageConfig = makeEndpointClient('/api/short-drama/providers/gpt-image/validate', 'post')
const getLanguageConfig = makeEndpointClient('/api/short-drama/providers/gpt-sol', 'get')
const updateLanguageConfig = makeEndpointClient('/api/short-drama/providers/gpt-sol', 'post')
const validateLanguageConfig = makeEndpointClient('/api/short-drama/providers/gpt-sol/validate', 'post')
const compileH3PromptWithLanguageModel = makeEndpointClient('/api/short-drama/projects/{project_id}/h3/prompt/compile', 'post')
const addReferencePackEntry = makeEndpointClient('/api/short-drama/projects/{project_id}/reference-packs/entries', 'post')
const updateShotReferencePlan = makeEndpointClient('/api/short-drama/projects/{project_id}/shots/{shot_id}/reference-plan', 'put')
const previewShotReferencePlan = makeEndpointClient('/api/short-drama/projects/{project_id}/shots/{shot_id}/reference-plan/preview', 'post')
const compileKeyframeBrief = makeEndpointClient('/api/short-drama/projects/{project_id}/shots/{shot_id}/keyframe-brief/compile', 'post')
const getDirectorAdvice = makeEndpointClient('/api/short-drama/projects/{project_id}/shots/{shot_id}/director-advice', 'post')
const runDirectorLoop = makeEndpointClient('/api/short-drama/projects/{project_id}/shots/{shot_id}/director-loop', 'post')
const decideDirectorRepair = makeEndpointClient('/api/short-drama/projects/{project_id}/shots/{shot_id}/director-repair', 'post')
const getH3CapabilityProfile = makeEndpointClient('/api/short-drama/projects/{project_id}/providers/minimax-h3/capability-profile', 'get')
const createAsset = makeEndpointClient('/api/short-drama/projects/{project_id}/assets', 'post')
const importLocalAssetVersion = makeEndpointClient('/api/short-drama/projects/{project_id}/assets/{asset_id}/versions/import-local', 'post')
const importUrlAssetVersion = makeEndpointClient('/api/short-drama/projects/{project_id}/assets/{asset_id}/versions/import-url', 'post')
const reviewAssetVersion = makeEndpointClient('/api/short-drama/projects/{project_id}/assets/{asset_id}/versions/{version_id}/review', 'post')
const enqueueImageTask = makeEndpointClient('/api/short-drama/projects/{project_id}/assets/{asset_id}/image-tasks', 'post')
const listImageTasks = makeEndpointClient('/api/short-drama/projects/{project_id}/image-tasks', 'get')
const advanceImageTask = makeEndpointClient('/api/short-drama/image-tasks/{task_id}/advance', 'post')
const cancelImageTask = makeEndpointClient('/api/short-drama/image-tasks/{task_id}/cancel', 'post')
const retryImageTask = makeEndpointClient('/api/short-drama/image-tasks/{task_id}/retry', 'post')
const recoverImageTaskOutput = makeEndpointClient('/api/short-drama/image-tasks/{task_id}/recover-output', 'post')

function projectPath(projectId: string, suffix = ''): string {
  return `/api/short-drama/projects/${encodeURIComponent(projectId)}${suffix}`
}

function taskPath(taskId: string, action: string): string {
  return `/api/short-drama/tasks/${encodeURIComponent(taskId)}/${action}`
}

function assetPath(projectId: string, assetId: string, suffix = ''): string {
  return `${projectPath(projectId, `/assets/${encodeURIComponent(assetId)}`)}${suffix}`
}

function imageTaskPath(taskId: string, action: string): string {
  return `/api/short-drama/image-tasks/${encodeURIComponent(taskId)}/${action}`
}

function shotPath(projectId: string, shotId: string, suffix = ''): string {
  return projectPath(projectId, `/shots/${encodeURIComponent(shotId)}${suffix}`)
}

export const ShortDramaApi = {
  listProjects: () => listProjects(),
  createProject,
  createAutonomousDraft,
  createAutonomousRepeat: (
    sourceProjectId: string,
    body: AutonomousRepeatCreateRequest,
  ) => createAutonomousRepeat(
    body,
    undefined,
    projectPath(sourceProjectId, '/autonomous-repeats'),
  ),
  advanceAutonomousRun: (projectId: string, runId: string, maxActions = 24) => (
    advanceAutonomousRun(
      { max_actions: maxActions },
      undefined,
      projectPath(
        projectId,
        `/autonomous-runs/${encodeURIComponent(runId)}/advance`,
      ),
    )
  ),
  initializeFixture: () => initializeFixture(),
  getProject: (projectId: string) => getProject(undefined, undefined, projectPath(projectId)),
  autoDraft: (projectId: string, renderExport = true) => (
    autoDraft({ render_export: renderExport }, undefined, projectPath(projectId, '/auto-draft'))
  ),
  enqueue: (
    projectId: string,
    body: components['schemas']['EnqueueRequest'],
  ) => enqueue(body, undefined, projectPath(projectId, '/enqueue')),
  drain: (projectId: string, maxSteps = 500) => drain({ project_id: projectId, max_steps: maxSteps }),
  rebuildManifest: (projectId: string) => (
    rebuildManifest(undefined, undefined, projectPath(projectId, '/manifest/rebuild'))
  ),
  exportProject: (projectId: string, rebuild = true) => (
    exportProject({ rebuild_manifest: rebuild }, undefined, projectPath(projectId, '/export'))
  ),
  reviseScriptLine: (
    projectId: string,
    body: components['schemas']['ReviseScriptLineRequest'],
  ) => reviseScriptLine(body, undefined, projectPath(projectId, '/script/revise-line')),
  selectTake: (takeId: string) => (
    selectTake(undefined, undefined, `/api/short-drama/takes/${encodeURIComponent(takeId)}/select`)
  ),
  inspectTakeVideo: (takeId: string, body: VideoInspectionRequest) => (
    inspectTakeVideo(
      body,
      undefined,
      `/api/short-drama/takes/${encodeURIComponent(takeId)}/inspect-video`,
    )
  ),
  inspectProjectAudio: (projectId: string, body: AudioInspectionRequest) => (
    inspectProjectAudio(
      body,
      undefined,
      projectPath(projectId, '/inspect-audio'),
    )
  ),
  advanceTask: (taskId: string) => advanceTask(undefined, undefined, taskPath(taskId, 'advance')),
  cancelTask: (taskId: string) => cancelTask(undefined, undefined, taskPath(taskId, 'cancel')),
  retryTask: (taskId: string) => retryTask(undefined, undefined, taskPath(taskId, 'retry')),
  reconcileTask: (taskId: string) => reconcileTask(undefined, undefined, taskPath(taskId, 'reconcile')),
  recoverTaskOutput: (taskId: string) => recoverTaskOutput(undefined, undefined, taskPath(taskId, 'recover-output')),
  getH3Config: () => getH3Config(),
  updateH3Config: (apiKey: string | null, clearApiKey = false) => (
    updateH3Config({ api_key: apiKey, clear_api_key: clearApiKey })
  ),
  validateH3Config: () => validateH3Config(),
  previewH3Probe: (projectId: string, body: H3ProbeRequest) => (
    previewH3Probe(body, undefined, projectPath(projectId, '/h3/preview'))
  ),
  compileH3ProductionRoutePlan: (
    projectId: string,
    body: H3ProductionRoutePlanRequest,
  ) => compileH3ProductionRoutePlan(
    body,
    undefined,
    projectPath(projectId, '/h3/production-route-plans'),
  ),
  listH3ProductionRoutePlans: (projectId: string) => listH3ProductionRoutePlans(
    undefined,
    undefined,
    projectPath(projectId, '/h3/production-route-plans'),
  ),
  executeH3ProductionRoutePlan: (
    projectId: string,
    planId: string,
    body: H3ProductionRouteExecuteRequest,
  ) => executeH3ProductionRoutePlan(
    body,
    undefined,
    projectPath(
      projectId,
      `/h3/production-route-plans/${encodeURIComponent(planId)}/execute`,
    ),
  ),
  enqueueH3Probe: (projectId: string, body: H3ProbeRequest) => (
    enqueueH3Probe(body, undefined, projectPath(projectId, '/h3/probes'))
  ),
  listH3Runs: (projectId: string) => (
    listH3Runs(undefined, undefined, projectPath(projectId, '/h3/runs'))
  ),
  listH3ReconciliationCandidates: () => listH3ReconciliationCandidates(),
  resolveH3Submission: (taskId: string, providerTaskId: string) => (
    resolveH3Submission(
      { provider_task_id: providerTaskId },
      undefined,
      taskPath(taskId, 'h3/resolve-submit'),
    )
  ),
  resolveH3NotSubmitted: (taskId: string, reason: string) => (
    resolveH3NotSubmitted(
      { reason },
      undefined,
      taskPath(taskId, 'h3/resolve-not-submitted'),
    )
  ),
  reviewH3Take: (takeId: string, body: H3TakeReviewRequest) => (
    reviewH3Take(
      body,
      undefined,
      `/api/short-drama/takes/${encodeURIComponent(takeId)}/h3/review`,
    )
  ),
  getImageConfig: () => getImageConfig(),
  updateImageConfig: (apiKey: string | null, baseUrl: string | null, clearApiKey = false) => (
    updateImageConfig({ api_key: apiKey, base_url: baseUrl, clear_api_key: clearApiKey })
  ),
  validateImageConfig: () => validateImageConfig(),
  getLanguageConfig: () => getLanguageConfig(),
  updateLanguageConfig: (apiKey: string | null, baseUrl: string | null, clearApiKey = false) => (
    updateLanguageConfig({ api_key: apiKey, base_url: baseUrl, clear_api_key: clearApiKey })
  ),
  validateLanguageConfig: () => validateLanguageConfig(),
  compileH3PromptWithLanguageModel: (
    projectId: string,
    body: components['schemas']['LanguagePromptCompileRequest'],
  ) => compileH3PromptWithLanguageModel(
    body,
    undefined,
    projectPath(projectId, '/h3/prompt/compile'),
  ),
  addReferencePackEntry: (
    projectId: string,
    body: ReferencePackEntryCreateRequest,
  ) => addReferencePackEntry(
    body,
    undefined,
    projectPath(projectId, '/reference-packs/entries'),
  ),
  updateShotReferencePlan: (
    projectId: string,
    shotId: string,
    body: ShotReferencePlanUpdateRequest,
  ) => updateShotReferencePlan(body, undefined, shotPath(projectId, shotId, '/reference-plan')),
  previewShotReferencePlan: (
    projectId: string,
    shotId: string,
    body: ReferencePlanPreviewRequest,
  ) => previewShotReferencePlan(
    body,
    undefined,
    shotPath(projectId, shotId, '/reference-plan/preview'),
  ),
  compileKeyframeBrief: (
    projectId: string,
    shotId: string,
    body: KeyframeBriefCompileRequest,
  ) => compileKeyframeBrief(
    body,
    undefined,
    shotPath(projectId, shotId, '/keyframe-brief/compile'),
  ),
  getDirectorAdvice: (
    projectId: string,
    shotId: string,
    body: DirectorAdviceRequest,
  ) => getDirectorAdvice(
    body,
    undefined,
    shotPath(projectId, shotId, '/director-advice'),
  ),
  runDirectorLoop: (
    projectId: string,
    shotId: string,
    body: DirectorAdviceRequest,
  ) => runDirectorLoop(
    body,
    undefined,
    shotPath(projectId, shotId, '/director-loop'),
  ),
  decideDirectorRepair: (
    projectId: string,
    shotId: string,
    body: DirectorRepairDecisionRequest,
  ) => decideDirectorRepair(
    body,
    undefined,
    shotPath(projectId, shotId, '/director-repair'),
  ),
  getH3CapabilityProfile: (projectId: string) => getH3CapabilityProfile(
    undefined,
    undefined,
    projectPath(projectId, '/providers/minimax-h3/capability-profile'),
  ),
  createAsset: (
    projectId: string,
    body: components['schemas']['AssetCreateRequest'],
  ) => createAsset(body, undefined, projectPath(projectId, '/assets')),
  importLocalAssetVersion: (
    projectId: string,
    assetId: string,
    body: components['schemas']['AssetImportLocalRequest'],
  ) => importLocalAssetVersion(body, undefined, assetPath(projectId, assetId, '/versions/import-local')),
  importUrlAssetVersion: (
    projectId: string,
    assetId: string,
    body: components['schemas']['AssetImportUrlRequest'],
  ) => importUrlAssetVersion(body, undefined, assetPath(projectId, assetId, '/versions/import-url')),
  reviewAssetVersion: (
    projectId: string,
    assetId: string,
    versionId: string,
    decision: components['schemas']['AssetReviewDecision'],
    notes: string,
  ) => reviewAssetVersion(
    { decision, notes },
    undefined,
    assetPath(projectId, assetId, `/versions/${encodeURIComponent(versionId)}/review`),
  ),
  enqueueImageTask: (
    projectId: string,
    assetId: string,
    body: ImageTaskCreateRequest,
  ) => enqueueImageTask(body, undefined, assetPath(projectId, assetId, '/image-tasks')),
  listImageTasks: (projectId: string, assetId?: string) => {
    const query = assetId ? `?asset_id=${encodeURIComponent(assetId)}` : ''
    return listImageTasks(undefined, undefined, `${projectPath(projectId, '/image-tasks')}${query}`)
  },
  advanceImageTask: (taskId: string) => (
    advanceImageTask(undefined, undefined, imageTaskPath(taskId, 'advance'))
  ),
  cancelImageTask: (taskId: string) => (
    cancelImageTask(undefined, undefined, imageTaskPath(taskId, 'cancel'))
  ),
  retryImageTask: (taskId: string) => (
    retryImageTask(undefined, undefined, imageTaskPath(taskId, 'retry'))
  ),
  recoverImageTaskOutput: (taskId: string) => (
    recoverImageTaskOutput(undefined, undefined, imageTaskPath(taskId, 'recover-output'))
  ),
  localRework: (
    projectId: string,
    shotId: string,
    operation: Exclude<OperationKind, 'GENERATE'>,
    durationDeltaSeconds = 0,
  ) => {
    const action = operation === 'GAP_FILL' ? 'gap-fill' : operation.toLowerCase()
    const path = projectPath(projectId, `/shots/${encodeURIComponent(shotId)}/${action}`)
    const body = { behavior: 'SUCCESS' as const, duration_delta_seconds: durationDeltaSeconds }
    if (operation === 'RETAKE') return retake(body, undefined, path)
    if (operation === 'EXTEND') return extend(body, undefined, path)
    return gapFill(body, undefined, path)
  },
}
