import type { components } from '../../generated/backend-openapi'
import { backendFetch } from '../../lib/backend'
import type { ProjectAggregate, ShotPlan } from './api'
import type { StudioExportDimensions } from './studio-aspect-ratio'

export type StudioEvent = components['schemas']['StudioEvent']
export type StudioJob = components['schemas']['StudioJob']
export type StudioScope = components['schemas']['StudioScope-Input']
export type StudioEpisodes = components['schemas']['StudioEpisodes']
export type StudioActivity = components['schemas']['StudioActivity']
export type StudioWorker = components['schemas']['StudioWorker']
export type StudioMethod = components['schemas']['StudioMethodDetail']
export type StudioSceneSave = components['schemas']['StudioSceneSave']
export type StudioMutation = components['schemas']['StudioMutationResult']
export type ReferenceCue = components['schemas']['ShotReferenceCue']
export type ScriptScene = components['schemas']['StructuredScene']
export type StudioEditorial = components['schemas']['StudioEditorial']
export type StudioExport = components['schemas']['StudioExport']
export type StudioChangeSet = components['schemas']['StudioChangeSet']
export type StudioChangeResult = components['schemas']['StudioChangeResult']

const base = '/api/short-drama/studio'
const projectPath = (id: string) => `${base}/projects/${encodeURIComponent(id)}`

export class StudioApiError extends Error { constructor(message: string, public status: number) { super(message) } }

export async function studioRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await backendFetch(path, init)
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null)
    let message = `请求未完成（${response.status}）`
    if (body && typeof body === 'object') {
      const value = body as { detail?: unknown; message?: unknown; error?: { message?: unknown } }; const detail = value.detail ?? value.message ?? value.error?.message
      if (typeof detail === 'string') message = detail
      else if (detail) message = JSON.stringify(detail)
    }
    throw new StudioApiError(message, response.status)
  }
  return response.json() as Promise<T>
}

function json(method: string, value: unknown): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) }
}

export const StudioApi = {
  method: (id: string) => studioRequest<StudioMethod>(`${base}/methods/${encodeURIComponent(id)}`),
  getProject: (id: string) => studioRequest<ProjectAggregate>(projectPath(id)),
  changes: (id: string) => studioRequest<StudioChangeSet[]>(`${projectPath(id)}/changes`),
  decideChange: (id: string, changeId: string, itemId: string, decision: 'accept' | 'reject') => studioRequest<StudioChangeResult>(`${projectPath(id)}/changes/${changeId}/items/${itemId}`, json('POST', { decision })),
  createProject: (name: string, brief = '') => studioRequest<ProjectAggregate>(`${base}/projects`, json('POST', { name, brief })),
  episodes: (id: string) => studioRequest<StudioEpisodes>(`${projectPath(id)}/episodes`),
  createEpisode: (id: string, title: string) => studioRequest<ProjectAggregate>(`${projectPath(id)}/episodes`, json('POST', { title })),
  rename: (id: string, name: string) => studioRequest<ProjectAggregate>(`${projectPath(id)}/name`, json('PATCH', { name })),
  preferences: (id: string, value: { method_skill_ids: string[]; creative_direction: string; aspect_ratio: string }) => studioRequest<ProjectAggregate>(`${projectPath(id)}/preferences`, json('PUT', value)),
  saveScene: (id: string, request: StudioSceneSave) => studioRequest<StudioMutation>(`${projectPath(id)}/scene`, json('PUT', request)),
  saveShot: (id: string, shot: ShotPlan, expectedRevision: number) => studioRequest<StudioMutation>(`${projectPath(id)}/shots/${encodeURIComponent(shot.id)}`, json('PUT', { shot, expected_revision: expectedRevision })),
  selectShotMedia: (id: string, shotId: string, versionId: string, revision: number) => studioRequest<ProjectAggregate>(`${projectPath(id)}/shots/${encodeURIComponent(shotId)}/media`, json('POST', { asset_version_id: versionId, expected_revision: revision })),
  saveTimeline: (id: string, timeline: unknown, revision: number) => studioRequest<StudioMutation>(`${projectPath(id)}/editorial`, json('PUT', { timeline, expected_revision: revision })),
  editorial: (id: string) => studioRequest<StudioEditorial>(`${projectPath(id)}/editorial`),
  shotEditorial: (id: string, shotId: string, anchor?: string) => studioRequest<StudioEditorial>(`${projectPath(id)}/shots/${encodeURIComponent(shotId)}/editorial${anchor ? `?anchor_clip_id=${encodeURIComponent(anchor)}` : ''}`),
  saveShotTimeline: (id: string, shotId: string, timeline: unknown, revision: number, anchor?: string | null) => studioRequest<StudioMutation>(`${projectPath(id)}/shots/${encodeURIComponent(shotId)}/editorial`, json('PUT', { timeline, expected_revision: revision, anchor_clip_id: anchor ?? null })),
  exportTimeline: (id: string, request: { expected_revision: number; fps: number; subtitles: boolean } & StudioExportDimensions) => studioRequest<StudioExport>(`${projectPath(id)}/exports`, json('POST', request)),
  exportStatus: (id: string, exportId: string) => studioRequest<StudioExport>(`${projectPath(id)}/exports/${exportId}`),
  activity: (id: string, after = 0) => studioRequest<StudioActivity>(`${projectPath(id)}/activity?after=${after}`),
  send: (id: string, text: string, scope: StudioScope) => studioRequest<StudioJob>(`${projectPath(id)}/messages`, json('POST', { text, scope })),
  control: (jobId: string, action: 'pause' | 'resume') => studioRequest<StudioJob>(`${base}/jobs/${jobId}/control`, json('POST', { action })),
  importMedia: (id: string, file: File, kind: string, assetId?: string, replaceVersionId?: string) => {
    const body = new FormData()
    body.set('file', file)
    body.set('name', file.name.replace(/\.[^.]+$/, ''))
    body.set('kind', kind)
    if (assetId) body.set('asset_id', assetId)
    if (replaceVersionId) body.set('replace_version_id', replaceVersionId)
    return studioRequest<ProjectAggregate>(`${projectPath(id)}/media`, { method: 'POST', body })
  },
  downloadTrace: (id: string) => downloadBackendFile(`${projectPath(id)}/trace`, '制作过程.zip'),
  downloadMedia: (id: string, path: string, name: string) => downloadBackendFile(`${base}/media?project_id=${encodeURIComponent(id)}&path=${encodeURIComponent(path)}`, name),
}

export async function downloadBackendFile(path: string, name: string): Promise<void> {
  const response = await backendFetch(path)
  if (!response.ok) throw new Error(`下载未完成（${response.status}）`)
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 30000)
}

/** Authenticated SSE with a cursor. Cancellation is local to this page, never the job. */
export async function watchStudio(id: string, cursor: number, signal: AbortSignal, onEvents: (events: StudioEvent[]) => void): Promise<void> {
  const response = await backendFetch(`${projectPath(id)}/events?after=${cursor}`, { signal })
  if (!response.ok || !response.body) throw new Error('实时连接暂不可用')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (!signal.aborted) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
      let boundary: number
      const events: StudioEvent[] = []
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const data = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
        if (data) events.push(JSON.parse(data) as StudioEvent)
      }
      if (events.length) onEvents(events)
    }
  } finally {
    reader.releaseLock()
  }
}

export function unwrapApi<T>(value: { ok: true; data: T } | { ok: false; error: { message: string } }): T {
  if (!value.ok) throw new Error(value.error.message)
  return value.data
}
