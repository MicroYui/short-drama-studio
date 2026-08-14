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
