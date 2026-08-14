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
export type OperationKind = components['schemas']['OperationKind']
export type VideoGenerationRoute = components['schemas']['VideoGenerationRoute']
export type H3ProviderConfig = components['schemas']['H3ProviderConfigResponse']
export type H3ProviderRun = components['schemas']['H3ProviderRun']
export type H3ProbeRequest = components['schemas']['H3ProbeRequest']
export type H3CompilePreview = components['schemas']['H3CompilePreviewResponse']
export type H3MediaInput = components['schemas']['H3MediaInput']
export type H3MediaKind = components['schemas']['H3MediaKind']
export type H3MediaRole = components['schemas']['H3MediaRole']
export type H3TakeReviewRequest = components['schemas']['H3TakeReviewRequest']
export type RouteEvidence = components['schemas']['RouteEvidence']

const listProjects = makeEndpointClient('/api/short-drama/projects', 'get')
const createProject = makeEndpointClient('/api/short-drama/projects', 'post')
const initializeFixture = makeEndpointClient('/api/short-drama/fixtures/dont-trust-me', 'post')
const getProject = makeEndpointClient('/api/short-drama/projects/{project_id}', 'get')
const autoDraft = makeEndpointClient('/api/short-drama/projects/{project_id}/auto-draft', 'post')
const enqueue = makeEndpointClient('/api/short-drama/projects/{project_id}/enqueue', 'post')
const drain = makeEndpointClient('/api/short-drama/worker/drain', 'post')
const rebuildManifest = makeEndpointClient('/api/short-drama/projects/{project_id}/manifest/rebuild', 'post')
const exportProject = makeEndpointClient('/api/short-drama/projects/{project_id}/export', 'post')
const reviseScriptLine = makeEndpointClient('/api/short-drama/projects/{project_id}/script/revise-line', 'post')
const selectTake = makeEndpointClient('/api/short-drama/takes/{take_id}/select', 'post')
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
const enqueueH3Probe = makeEndpointClient('/api/short-drama/projects/{project_id}/h3/probes', 'post')
const listH3Runs = makeEndpointClient('/api/short-drama/projects/{project_id}/h3/runs', 'get')
const listH3ReconciliationCandidates = makeEndpointClient('/api/short-drama/providers/minimax-h3/reconciliation-candidates', 'get')
const resolveH3Submission = makeEndpointClient('/api/short-drama/tasks/{task_id}/h3/resolve-submit', 'post')
const reviewH3Take = makeEndpointClient('/api/short-drama/takes/{take_id}/h3/review', 'post')

function projectPath(projectId: string, suffix = ''): string {
  return `/api/short-drama/projects/${encodeURIComponent(projectId)}${suffix}`
}

function taskPath(taskId: string, action: string): string {
  return `/api/short-drama/tasks/${encodeURIComponent(taskId)}/${action}`
}

export const ShortDramaApi = {
  listProjects: () => listProjects(),
  createProject,
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
  reviewH3Take: (takeId: string, body: H3TakeReviewRequest) => (
    reviewH3Take(
      body,
      undefined,
      `/api/short-drama/takes/${encodeURIComponent(takeId)}/h3/review`,
    )
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
