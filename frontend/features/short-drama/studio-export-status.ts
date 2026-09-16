import type { StudioExport } from './studio-api'
import type { ProjectAggregate } from './api'

export function isStudioExportActive(record: Pick<StudioExport, 'status'>): boolean {
  return ['running', 'cancelling'].includes(record.status)
}

export function mergeStudioExports(current: StudioExport[], incoming: StudioExport[], limit = 20): StudioExport[] {
  const records = new Map(current.map(record => [record.id, record]))
  for (const record of incoming) {
    const previous = records.get(record.id)
    if (previous) {
      if (!isStudioExportActive(previous) && isStudioExportActive(record)) continue
      if (previous.status === 'cancelling' && record.status === 'running') continue
      const previousTime = Date.parse(previous.updated_at ?? previous.finished_at ?? previous.created_at)
      const nextTime = Date.parse(record.updated_at ?? record.finished_at ?? record.created_at)
      if (previousTime > nextTime) continue
    }
    records.set(record.id, record)
  }
  return [...records.values()].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
    .filter((record, index) => index < limit || isStudioExportActive(record))
}

export function studioExportRetryRequest(record: StudioExport, revision: number) {
  return { expected_revision: revision, width: record.width, height: record.height, fps: record.fps, subtitles: record.subtitles ?? true }
}

/** Export events already carry their complete small record; keep content untouched. */
export function mergeProjectExports(project: ProjectAggregate, incoming: StudioExport[]): ProjectAggregate {
  const relevant = incoming.filter(record => record.project_id === project.document.project.id)
  if (!relevant.length) return project
  const current = (project.document.studio_exports ?? []) as StudioExport[]
  const records = mergeStudioExports(current, relevant, Number.POSITIVE_INFINITY).reverse()
  if (JSON.stringify(current) === JSON.stringify(records)) return project
  return { ...project, document: { ...project.document, studio_exports: records } }
}

export function exportRecordsFromEvents(events: readonly { kind: string; data: unknown }[]): StudioExport[] {
  return events.flatMap(event => {
    if (event.kind !== 'export.updated' || !event.data || typeof event.data !== 'object') return []
    const record = event.data as Partial<StudioExport>
    return typeof record.id === 'string' && typeof record.project_id === 'string'
      && typeof record.status === 'string' && typeof record.created_at === 'string' ? [record as StudioExport] : []
  })
}
