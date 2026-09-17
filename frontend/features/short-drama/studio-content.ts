import type { AssetVersion, ProjectAggregate, ShotPlan } from './api'
import { pathToFileUrl } from '../../lib/file-url'
import type { ReferenceCue } from './studio-api'
import { projectSchema, type Project } from '../../types/project-model'
import { mediaLabel } from './studio-media-library'
import { editorialShotUses } from './studio-shot-uses'
export { assetGroups, mediaLabel } from './studio-media-library'
export type { AssetGroup } from './studio-media-library'

const editorialCache = new WeakMap<object, Project | null>()
export function currentEditorial(project: ProjectAggregate): Project | null {
  const raw = project.document.editorial_timeline?.project
  if (!raw || typeof raw !== 'object') return null
  if (editorialCache.has(raw)) return editorialCache.get(raw) ?? null
  const parsed = projectSchema.safeParse(raw)
  const result = parsed.success ? parsed.data : null
  editorialCache.set(raw, result)
  return result
}

export const mediaUrl = (path: string | null | undefined, external?: string | null): string => path ? pathToFileUrl(path) : external || ''

export function mediaTitle(project: ProjectAggregate, version: AssetVersion): string {
  const entry = (project.document.reference_packs ?? []).flatMap(pack => pack.entries ?? []).find(item => item.asset_version_id === version.id)
  if (version.reference_sheet) return version.reference_sheet.strategy === 'CHARACTER_STANDARD_VIEWS' ? '角色参考卡' : '多视角参考卡'
  const angles: Record<string, string> = { FRONT: '正面', BACK: '背面', LEFT: '左侧', RIGHT: '右侧', LEFT_PROFILE: '左侧', RIGHT_PROFILE: '右侧', THREE_QUARTER: '四分之三侧面' }
  const frames: Record<string, string> = { FACE_CLOSE_UP: '脸部特写', FULL_BODY: '全身', HALF_BODY: '半身', WIDE: '全景' }
  const label = entry && [angles[entry.angle ?? ''], frames[entry.framing ?? '']].filter(Boolean).join(' · ')
  return label || mediaLabel(version.descriptor)
}
export const durationLabel = (seconds: number): string => `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`
export const currentScript = (project: ProjectAggregate) => project.document.script_versions.find(item => item.id === project.document.project.current_script_version_id) ?? project.document.script_versions.at(-1)
export const allCues = (shot: ShotPlan): ReferenceCue[] => [...(shot.references ?? []), ...(shot.timeline_beats ?? []).flatMap(beat => beat.references ?? [])]

export function resolveCue(project: ProjectAggregate, cue: ReferenceCue) {
  const asset = project.document.assets.find(item => item.versions.some(version => version.id === cue.asset_version_id))
  const version = asset?.versions.find(item => item.id === cue.asset_version_id)
  const subject = project.document.assets.find(item => item.id === cue.subject_asset_id)
  return { asset, version, name: subject?.name ?? asset?.name ?? cue.alias }
}

export function shotMedia(project: ProjectAggregate, shot: ShotPlan) {
  const manifests = project.document.manifests
  const manifest = manifests.find(item => item.id === project.document.project.current_manifest_id) ?? manifests.at(-1)
  const clip = manifest?.video_clips.find(item => item.shot_id === shot.id)
  const takes = project.takes.filter(item => item.shot_id === shot.id || (item.selected_shot_ids ?? []).includes(shot.id) || (item.shot_windows ?? []).some(window => window.shot_id === shot.id))
  const take = takes.find(item => item.is_selected && ((item.selected_shot_ids ?? []).includes(shot.id) || !(item.selected_shot_ids ?? []).length && item.shot_id === shot.id)) ?? project.takes.find(item => item.id === clip?.take_id) ?? takes.at(-1)
  const selectedClip = clip?.take_id === take?.id ? clip : undefined
  const window = take?.shot_windows?.find(item => item.shot_id === shot.id)
  const windowEnd = window ? window.source_in_seconds + window.duration_seconds : 0
  const hasFollowingWindow = take?.shot_windows?.some(item => item.shot_id !== shot.id && item.source_in_seconds + item.duration_seconds > windowEnd + 0.001)
  const materialDuration = window && take
    ? Math.max(0, Math.min(take.duration_seconds, hasFollowingWindow ? windowEnd : take.duration_seconds) - window.source_in_seconds)
    : selectedClip?.duration_seconds ?? Math.min(take?.duration_seconds ?? shot.duration_seconds, shot.duration_seconds)
  const editorial = currentEditorial(project)
  const occurrences = editorial ? editorialShotUses(editorial, project.takes, shot.id) : []
  const adoptedDuration = editorial ? occurrences[0] ? occurrences[0].end - occurrences[0].start : null : clip?.duration_seconds ?? null
  return {
    take,
    materialDuration: take ? materialDuration : null,
    adoptedDuration,
    occurrenceCount: occurrences.length,
    hasEditorial: Boolean(editorial && occurrences.length),
    path: take?.media_path ?? clip?.media_path,
    sourceIn: selectedClip?.source_in_seconds ?? window?.source_in_seconds ?? 0,
    duration: window ? materialDuration : selectedClip?.duration_seconds ?? Math.min(take?.duration_seconds ?? shot.duration_seconds, shot.duration_seconds),
    thumbnail: take?.thumbnail_path,
  }
}
