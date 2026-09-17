import type { Take } from './api'
import type { Asset, Project, Timeline, TimelineClip } from '../../types/project-model'

export interface EditorialShotUse { start: number; end: number; clipIds: string[] }

type ShotWindow = NonNullable<Take['shot_windows']>[number]

function hasExplicitSourceWindow(clip: TimelineClip): boolean {
  const begin = clip.sourceWindowStart
  const end = clip.sourceWindowEnd
  return typeof begin === 'number' && typeof end === 'number' && Number.isFinite(begin) && Number.isFinite(end) && begin >= 0 && end > begin
}

/** Match studio_shot_uses.py. These are source metadata windows, not detected picture cuts. */
function wholeGroupWindows(clip: TimelineClip, timeline: Timeline, assets: Asset[], takes: Take[]): { take: Take; windows: ShotWindow[]; ids: Set<string> } | null {
  if (clip.sourceWindowStart != null || clip.sourceWindowEnd != null) return null
  const take = takes.find(item => item.id === clip.sourceTakeId)
  const windows = [...(take?.shot_windows ?? [])].sort((a, b) => a.source_in_seconds - b.source_in_seconds)
  const ids = new Set(windows.map(item => item.shot_id))
  const selected = new Set(take?.selected_shot_ids ?? [])
  if (!take || windows.length < 2 || ids.size !== windows.length || !Number.isFinite(take.duration_seconds)) return null
  if (windows.some((item, index) => !Number.isFinite(item.source_in_seconds) || !Number.isFinite(item.source_out_seconds) || !Number.isFinite(item.duration_seconds)
    || item.source_in_seconds < 0 || item.source_out_seconds <= item.source_in_seconds
    || Math.abs(item.source_out_seconds - item.source_in_seconds - item.duration_seconds) > 0.001
    || item.source_out_seconds > take.duration_seconds + 0.001
    || index > 0 && item.source_in_seconds < windows[index - 1].source_out_seconds - 0.001)) return null
  const asset = assets.find(item => item.id === clip.assetId)
  const path = clip.takeIndex == null ? asset?.path : Number.isInteger(clip.takeIndex) && clip.takeIndex >= 0 ? asset?.takes?.[clip.takeIndex]?.path : undefined
  if (!path || path !== take.media_path || !Number.isFinite(clip.speed) || clip.speed <= 0) return null
  const sourceEnd = clip.trimStart + clip.duration * clip.speed
  if (!Number.isFinite(clip.trimStart) || !Number.isFinite(sourceEnd) || sourceEnd > take.duration_seconds + 0.04) return null
  if (clip.sourceShotIds != null) {
    const declared = clip.sourceShotIds
    if (!Array.isArray(declared) || declared.length < 2
      || declared.some(id => typeof id !== 'string' || !id.trim())) return null
    const declaredIds = new Set(declared)
    if (declaredIds.size !== declared.length || !declaredIds.has(clip.sourceShotId ?? '')
      || declared.some(id => !ids.has(id))) return null
    if (!windows.some((window, index) => declaredIds.has(window.shot_id)
      && Math.min(sourceEnd, index === windows.length - 1 ? take.duration_seconds : window.source_out_seconds)
        > Math.max(clip.trimStart, window.source_in_seconds))) return null
    return { take, windows, ids: declaredIds }
  }
  if (clip.sourceShotId !== windows[0].shot_id || windows.some(item => !selected.has(item.shot_id))) return null
  if (timeline.clips.some(item => item.type === 'video' && item.id !== clip.id
    && item.sourceShotId !== clip.sourceShotId && ids.has(item.sourceShotId ?? '') && !hasExplicitSourceWindow(item))) return null
  if (windows.some(item => clip.trimStart > item.source_in_seconds + 0.001
    || sourceEnd < item.source_out_seconds - 0.001)) return null
  return { take, windows, ids }
}

/** Read saved uses, including declared trimmed groups and intact selected legacy H3 groups. */
export function editorialShotUses(project: Project, takes: Take[], shotId: string): EditorialShotUse[] {
  const timeline = project.timelines.find(item => item.id === project.activeTimelineId)
  if (!timeline) return []
  const segments: EditorialShotUse[] = []
  for (const clip of timeline.clips) {
    if (clip.type !== 'video') continue
    const group = wholeGroupWindows(clip, timeline, project.assets, takes)
    if (!group) {
      if (clip.sourceShotId === shotId) segments.push({ start: clip.startTime, end: clip.startTime + clip.duration, clipIds: [clip.id] })
      continue
    }
    const window = group.ids.has(shotId) ? group.windows.find(item => item.shot_id === shotId) : undefined
    if (!window) continue
    const lastWindow = window === group.windows.at(-1)
    const sourceEnd = clip.trimStart + clip.duration * clip.speed
    const first = Math.max(clip.trimStart, window.source_in_seconds)
    const last = Math.min(sourceEnd, lastWindow ? group.take.duration_seconds : window.source_out_seconds)
    if (last <= first) continue
    const start = clip.startTime + (clip.reversed ? sourceEnd - last : first - clip.trimStart) / clip.speed
    const end = clip.startTime + (clip.reversed ? sourceEnd - first : last - clip.trimStart) / clip.speed
    segments.push({ start, end, clipIds: [clip.id] })
  }
  const uses: EditorialShotUse[] = []
  for (const segment of segments.sort((a, b) => a.start - b.start)) {
    const last = uses.at(-1)
    if (last && segment.start <= last.end + 0.001) {
      last.end = Math.max(last.end, segment.end)
      last.clipIds.push(...segment.clipIds)
    } else uses.push(segment)
  }
  return uses
}
