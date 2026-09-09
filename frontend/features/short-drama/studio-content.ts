import type { Asset, AssetVersion, ProjectAggregate, ShotPlan } from './api'
import { pathToFileUrl } from '../../lib/file-url'
import type { ReferenceCue } from './studio-api'
import { projectSchema, type Project } from '../../types/project-model'

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

/** Older experiment labels include work-item metadata; keep the descriptive title visible. */
export function mediaLabel(description: string): string {
  const parts = description.split(/[|｜]/).map(part => part.trim())
  const title = parts.length > 1 ? parts.find(part => /\p{Script=Han}/u.test(part) && !part.startsWith('wi-')) || parts[0] : description
  return title.replace(/^wi-[a-z0-9-]+\s*/i, '').split(/[；。\n]/)[0].slice(0, 64)
}
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

export interface AssetGroup { id: string; name: string; kind: Asset['kind']; subject: Asset; files: { asset: Asset; version: AssetVersion }[] }

export function assetGroups(project: ProjectAggregate): AssetGroup[] {
  const { assets, shots } = project.document
  const packs = project.document.reference_packs ?? []
  const groups = new Map<string, AssetGroup>()
  const bound = new Set(shots.flatMap(allCues).map(cue => cue.asset_version_id))
  const packVersions = new Set(packs.flatMap(pack => (pack.entries ?? []).map(entry => entry.asset_version_id)))
  for (const asset of assets) {
    const pack = packs.find(item => (item.entries ?? []).some(entry => asset.versions.some(version => version.id === entry.asset_version_id)))
    const subject = assets.find(item => item.id === pack?.subject_asset_id) ?? assets.find(item => item.bible_entity_id && item.bible_entity_id === asset.bible_entity_id) ?? asset
    let group = groups.get(subject.id)
    if (!group) {
      group = { id: subject.id, name: subject.name, kind: subject.kind, subject, files: [] }
      groups.set(subject.id, group)
    }
    for (const version of asset.versions) {
      if (!(version.local_path || version.external_url)) continue
      if (version.id !== asset.current_version_id && version.id !== asset.approved_version_id && !bound.has(version.id) && !packVersions.has(version.id)) continue
      group.files.push({ asset, version })
    }
  }
  return [...groups.values()]
}

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
  const materialDuration = window?.duration_seconds ?? selectedClip?.duration_seconds ?? Math.min(take?.duration_seconds ?? shot.duration_seconds, shot.duration_seconds)
  const editorial = currentEditorial(project)
  const timeline = editorial?.timelines.find(item => item.id === editorial.activeTimelineId)
  const adopted = (timeline?.clips ?? []).filter(item => item.type === 'video' && item.sourceShotId === shot.id).sort((a, b) => a.startTime - b.startTime)
  const occurrences: { start: number; end: number }[] = []
  for (const item of adopted) {
    const last = occurrences.at(-1)
    if (last && item.startTime <= last.end + 0.001) last.end = Math.max(last.end, item.startTime + item.duration)
    else occurrences.push({ start: item.startTime, end: item.startTime + item.duration })
  }
  const adoptedDuration = editorial ? occurrences[0] ? occurrences[0].end - occurrences[0].start : null : take ? materialDuration : null
  return {
    take,
    materialDuration: take ? materialDuration : null,
    adoptedDuration,
    occurrenceCount: occurrences.length,
    hasEditorial: Boolean(editorial && adopted.length),
    path: take?.media_path ?? clip?.media_path,
    sourceIn: selectedClip?.source_in_seconds ?? window?.source_in_seconds ?? 0,
    duration: selectedClip?.duration_seconds ?? window?.duration_seconds ?? Math.min(take?.duration_seconds ?? shot.duration_seconds, shot.duration_seconds),
    thumbnail: take?.thumbnail_path,
  }
}
