import type { ProjectAggregate } from './api'
import type { StudioExport } from './studio-api'
import { mergeProjectExports } from './studio-export-status'

/** Requests can finish out of order while separate scenes or shots are saved. */
export function receiveProjectSnapshot(current: ProjectAggregate | null, incoming: ProjectAggregate): ProjectAggregate {
  if (!current || current.document.project.id !== incoming.document.project.id) return incoming
  const currentTime = Date.parse(current.document.project.updated_at)
  const incomingTime = Date.parse(incoming.document.project.updated_at)
  const currentRevision = Number(current.document.editorial_timeline?.revision ?? 0)
  const incomingRevision = Number(incoming.document.editorial_timeline?.revision ?? 0)
  const older = incomingTime < currentTime || incomingRevision < currentRevision
  return older
    ? mergeProjectExports(current, (incoming.document.studio_exports ?? []) as StudioExport[])
    : mergeProjectExports(incoming, (current.document.studio_exports ?? []) as StudioExport[])
}
