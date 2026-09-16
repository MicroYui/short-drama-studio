import type { components } from '../../generated/backend-openapi'

export type ProjectTransfer = components['schemas']['StudioProjectTransfer']
export type ProjectTransfers = components['schemas']['StudioProjectTransfers']
export type ProjectPackage = components['schemas']['StudioProjectPackage']

type Request = <T>(path: string, init?: RequestInit) => Promise<T>
const base = '/api/short-drama/studio'

export function createProjectTransferApi(request: Request) {
  return {
    collect: (projectId: string) => request<ProjectTransfer>(`${base}/projects/${encodeURIComponent(projectId)}/package-jobs`, { method: 'POST' }),
    import: (file: File) => {
      const body = new FormData()
      body.set('file', file)
      return request<ProjectTransfer>(`${base}/projects/import-package-jobs`, { method: 'POST', body })
    },
    list: (before?: string, signal?: AbortSignal) => request<ProjectTransfers>(`${base}/project-transfers?limit=20${before ? `&before=${encodeURIComponent(before)}` : ''}`, { signal }),
    get: (id: string, signal?: AbortSignal) => request<ProjectTransfer>(`${base}/project-transfers/${encodeURIComponent(id)}`, { signal }),
    cancel: (id: string) => request<ProjectTransfer>(`${base}/project-transfers/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  }
}

export function isProjectTransferActive(record: Pick<ProjectTransfer, 'status'>): boolean {
  return record.status === 'queued' || record.status === 'running' || record.status === 'cancelling'
}

const statusOrder = { queued: 0, running: 1, cancelling: 2, completed: 3, cancelled: 3, failed: 3 }
const stageOrder: Record<string, number> = { queued: 0, snapshot: 1, validating: 1, media: 2, extracting: 2, trace: 3, committing: 4, completed: 5, cancelled: 5, failed: 5 }

function compareUpdatedAt(next: string, previous: string): number {
  const milliseconds = Date.parse(next) - Date.parse(previous)
  if (milliseconds) return milliseconds
  // Python timestamps retain microseconds, which Date.parse alone discards.
  const fraction = (value: string) => (value.match(/\.(\d+)(?:Z|[+-])/u)?.[1] ?? '').slice(3).padEnd(6, '0')
  return fraction(next).localeCompare(fraction(previous))
}

/** A late list/poll response must not erase a cancellation, newer progress or a result. */
export function mergeProjectTransfers(current: readonly ProjectTransfer[], incoming: readonly ProjectTransfer[]): ProjectTransfer[] {
  const records = new Map(current.map(record => [record.id, record]))
  for (const record of incoming) {
    const previous = records.get(record.id)
    if (previous) {
      if (statusOrder[previous.status] > statusOrder[record.status]) continue
      const time = compareUpdatedAt(record.updated_at, previous.updated_at)
      if (time < 0) continue
      if (time === 0 && previous.status === record.status) {
        if ((stageOrder[previous.stage] ?? 0) > (stageOrder[record.stage] ?? 0)) continue
        if (previous.stage === record.stage && previous.message === record.message && (record.processed_files < previous.processed_files || record.processed_bytes < previous.processed_bytes)) continue
      }
    }
    records.set(record.id, record)
  }
  return [...records.values()].sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id))
}

/** One unavailable job must not hide another job's successfully retrieved result. */
export async function readActiveProjectTransfers(records: readonly ProjectTransfer[], read: (id: string) => Promise<ProjectTransfer>) {
  const active = records.filter(isProjectTransferActive)
  const results = await Promise.allSettled(active.map(record => read(record.id)))
  const transfers: ProjectTransfer[] = []
  const failedIds: string[] = []
  results.forEach((result, index) => {
    if (result.status === 'fulfilled') transfers.push(result.value)
    else failedIds.push(active[index].id)
  })
  return { transfers, failedIds }
}

export function transferBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1 }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`
}

/** Counts reset at each backend stage; this is deliberately not overall progress. */
export function projectTransferProgress(record: ProjectTransfer) {
  const files = record.total_files == null ? `已处理 ${record.processed_files} 份文件` : `${record.processed_files} / ${record.total_files} 份文件`
  const bytes = record.total_bytes == null ? `已处理 ${transferBytes(record.processed_bytes)}` : `${transferBytes(record.processed_bytes)} / ${transferBytes(record.total_bytes)}`
  const ratio = record.total_bytes && record.total_bytes > 0 ? record.processed_bytes / record.total_bytes
    : record.total_files && record.total_files > 0 ? record.processed_files / record.total_files : undefined
  return { files, bytes, ratio: ratio == null ? undefined : Math.max(0, Math.min(1, ratio)) }
}

export async function openProjectPackage(result: ProjectPackage, options: {
  reveal?: (path: string) => Promise<void>
  download: (url: string, filename: string) => Promise<void>
}): Promise<void> {
  if (options.reveal) await options.reveal(result.path)
  else await options.download(result.download_url, result.filename)
}
