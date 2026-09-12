import type { Asset, AssetVersion, ProjectAggregate, Take } from './api'
import type { Project } from '../../types/project-model'
import type { StudioScope } from './studio-api'

/** Keep narrative state and prose; only strip an old work-item identifier prefix. */
export function mediaLabel(description: string): string {
  return description.split(/[|｜]/).map(part => part.trim().replace(/^wi-[a-z0-9-]+(?:\s+|$)/i, '').trim()).filter(Boolean).join('｜')
}

export interface AssetGroup { id: string; name: string; kind: Asset['kind']; subject: Asset; files: { asset: Asset; version: AssetVersion }[] }

export function assetGroups(project: ProjectAggregate, options: { allMedia?: boolean } = {}): AssetGroup[] {
  const { assets, shots } = project.document
  const packs = project.document.reference_packs ?? []
  const groups = new Map<string, AssetGroup>()
  const bound = new Set(shots.flatMap(shot => [...(shot.references ?? []), ...(shot.timeline_beats ?? []).flatMap(beat => beat.references ?? [])]).map(cue => cue.asset_version_id))
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
      if (options.allMedia) {
        if (version.media_type === 'TEXT_REFERENCE') continue
      } else {
        if (!(version.local_path || version.external_url)) continue
        if (version.id !== asset.current_version_id && version.id !== asset.approved_version_id && !bound.has(version.id) && !packVersions.has(version.id)) continue
      }
      group.files.push({ asset, version })
    }
  }
  return [...groups.values()]
}

export type LibraryMediaType = 'IMAGE' | 'VIDEO' | 'AUDIO'
export type MediaReadState = 'loaded' | 'error'
export interface LibraryFile {
  id: string
  groupId?: string
  owner: string
  title: string
  description: string
  mediaType: LibraryMediaType
  localPath?: string | null
  externalUrl?: string | null
  width?: number | null
  height?: number | null
  duration?: number | null
  source: string
  state: string
  contentState?: string
  warning?: string
  missing: boolean
  shotIds: string[]
  editorialUsed: boolean
  voiceUsed: boolean
  asset?: Asset
  version?: AssetVersion
  take?: Take
  relatedTakes?: Take[]
  relatedFiles?: { id: string; owner: string; title: string }[]
}

const originLabels: Record<AssetVersion['origin_type'], string> = {
  MANUAL: '手动创建', UPLOAD: '用户导入', LIBRARY_IMPORT: '素材库导入', LOCAL_DERIVED: '本地处理',
  AI_GENERATED: 'AI 生成', AI_EDITED: 'AI 编辑', FIXTURE: '示例素材',
}
const reviewLabels: Record<AssetVersion['approval_status'], string> = { DRAFT: '候选素材', APPROVED: '已确认素材', REJECTED: '已拒绝', SUPERSEDED: '已失效' }
const fileName = (path: string) => path.split(/[\\/]/).at(-1) || path

/** Pick a preview only; explicit inspection never changes the selected asset version. */
export function assetGroupPreview(project: ProjectAggregate, group: AssetGroup, files: LibraryFile[], reads: Readonly<Record<string, MediaReadState>> = {}, selectedFileId?: string | null): LibraryFile | undefined {
  const items = files.filter(file => file.groupId === group.id)
  const selected = items.find(file => file.id === selectedFileId)
  if (selected) return selected
  const usable = items.filter(file => !file.missing && reads[fileReadKey(file)] !== 'error'
    && file.version?.freshness === 'CURRENT' && ['DRAFT', 'APPROVED'].includes(file.version.approval_status))
  const current = usable.find(file => file.id === group.subject.current_version_id)
  if (current) return current
  const entries = (project.document.reference_packs ?? []).filter(pack => pack.subject_asset_id === group.id).flatMap(pack => pack.entries ?? [])
  const canonical = entries.filter(entry => entry.role === 'CANONICAL_MASTER').map(entry => usable.find(file => file.id === entry.asset_version_id)).find(Boolean)
  return canonical ?? usable.find(file => file.shotIds.length > 0 || file.editorialUsed || file.voiceUsed)
    ?? entries.map(entry => usable.find(file => file.id === entry.asset_version_id)).find(Boolean)
    ?? usable.find(file => file.id === file.asset?.current_version_id) ?? usable[0]
}

/** A read-only projection of canonical files, not a version browser or selection mutation. */
export function projectMediaFiles(project: ProjectAggregate, groups: AssetGroup[], editorial: Project | null = null): LibraryFile[] {
  const files: LibraryFile[] = []
  for (const group of groups) for (const { asset, version } of group.files) {
    if (version.media_type === 'TEXT_REFERENCE') continue
    const shotIds = project.document.shots.filter(shot => [...(shot.references ?? []), ...(shot.timeline_beats ?? []).flatMap(beat => beat.references ?? [])].some(cue => cue.asset_version_id === version.id)).map(shot => shot.id)
    files.push({
      id: version.id, groupId: group.id, owner: group.name, title: mediaLabel(version.descriptor) || group.name,
      description: version.descriptor, mediaType: version.media_type, localPath: version.local_path, externalUrl: version.external_url,
      width: version.width, height: version.height, duration: version.duration_seconds,
      source: [version.source?.source_label || originLabels[version.origin_type], version.source?.model].filter(Boolean).join(' · '),
      state: reviewLabels[version.approval_status], contentState: version.text_plate?.content_state?.name,
      warning: version.freshness === 'STALE' ? '来源已变化，需核对' : ['REJECTED', 'SUPERSEDED'].includes(version.approval_status) ? reviewLabels[version.approval_status] : undefined,
      missing: !(version.local_path || version.external_url) || version.file_size_bytes === 0,
      shotIds, editorialUsed: false,
      voiceUsed: (project.document.voice_references ?? []).some(binding => binding.asset_id === asset.id && asset.current_version_id === version.id),
      asset, version,
    })
  }
  for (const take of project.takes) {
    const shotIds = take.shot_windows?.length ? take.shot_windows.map(window => window.shot_id) : [take.shot_id]
    const existing = files.find(file => file.localPath && file.localPath === take.media_path)
    if (existing) {
      existing.take ??= take
      existing.relatedTakes = [...new Map([...(existing.relatedTakes ?? [existing.take]), take].map(item => [item.id, item])).values()]
      existing.shotIds = [...new Set([...existing.shotIds, ...shotIds])]
      existing.source = `${existing.source} · 视频来源 ${take.provider} · ${take.model}`
      existing.missing ||= take.availability === 'MISSING'
      existing.warning ||= take.freshness === 'STALE' ? '来源已变化，需核对' : take.qa_status === 'REJECTED' ? '视频检查未通过' : undefined
      existing.editorialUsed ||= !editorial && take.is_selected
      continue
    }
    const names = shotIds.map(id => project.document.shots.find(shot => shot.id === id)).filter(shot => !!shot).map(shot => String(shot.sequence).padStart(2, '0'))
    const title = `镜头 ${names.join('、') || take.shot_id} · 生成源视频 ${take.candidate_index}`
    files.push({ id: take.id, owner: names.length ? `镜头 ${names.join('、')}` : '生成源视频', title,
      description: project.document.shots.filter(shot => shotIds.includes(shot.id)).map(shot => `镜头 ${String(shot.sequence).padStart(2, '0')}：${shot.narrative_purpose}`).join('\n'),
      mediaType: 'VIDEO', localPath: take.media_path, width: take.width, height: take.height, duration: take.duration_seconds,
      source: `${take.provider} · ${take.model}`, state: take.qa_status === 'PASSED' ? '检查通过' : take.qa_status === 'REJECTED' ? '已拒绝' : '未记录检查',
      warning: take.freshness === 'STALE' ? '来源已变化，需核对' : take.qa_status === 'REJECTED' ? '已拒绝' : undefined,
      missing: take.availability === 'MISSING' || !take.media_path, shotIds, editorialUsed: !editorial && take.is_selected, voiceUsed: false, take, relatedTakes: [take],
    })
  }
  // Saved editing media can include an audio file which is not an AssetVersion.
  for (const asset of editorial?.assets ?? []) {
    if (asset.type === 'adjustment') continue
    for (const source of [{ path: asset.path, width: asset.width, height: asset.height }, ...(asset.takes ?? [])]) {
      if (!source.path || files.some(file => file.localPath === source.path)) continue
      files.push({ id: `editorial:${asset.id}:${source.path}`, owner: '剪辑素材', title: fileName(source.path), description: asset.prompt,
        mediaType: asset.type.toUpperCase() as LibraryMediaType, localPath: source.path,
        width: source.width, height: source.height, duration: asset.duration, source: '本集剪辑素材', state: '素材文件',
        missing: false, shotIds: [], editorialUsed: false, voiceUsed: false })
    }
  }
  for (const timeline of editorial?.timelines ?? []) for (const clip of timeline.clips) {
    const asset = editorial?.assets.find(item => item.id === clip.assetId) ?? clip.asset
    const path = clip.takeIndex != null ? asset?.takes?.[clip.takeIndex]?.path : asset?.path
    const file = files.find(item => path && item.localPath === path)
    if (file) { file.editorialUsed = true; if (clip.sourceShotId && !file.shotIds.includes(clip.sourceShotId)) file.shotIds.push(clip.sourceShotId) }
  }
  return files
}

/** File cards deduplicate physical sources; core-asset entries retain exact identities. */
export function libraryFileCards(files: LibraryFile[]): LibraryFile[] {
  const cards = new Map<string, LibraryFile>()
  for (const file of files) {
    const key = `${file.mediaType}:${file.localPath || file.externalUrl || file.id}`
    const related = { id: file.id, owner: file.owner, title: file.title }
    const current = cards.get(key)
    if (!current) cards.set(key, { ...file, shotIds: [...file.shotIds], relatedFiles: [related] })
    else {
      current.relatedFiles?.push(related)
      current.shotIds = [...new Set([...current.shotIds, ...file.shotIds])]
      current.editorialUsed ||= file.editorialUsed
      current.voiceUsed ||= file.voiceUsed
      if (!current.take && file.take) { current.take = file.take; current.source = `${current.source} · ${file.take.provider} · ${file.take.model}` }
    }
  }
  return [...cards.values()]
}

export function fileReadKey(file: LibraryFile): string {
  return JSON.stringify([file.id, file.localPath, file.externalUrl, file.version?.file_size_bytes])
}

export function fileReadLabel(file: LibraryFile, read?: MediaReadState): string {
  if (file.missing) return '文件缺失或为空'
  if (read === 'error') return '无法读取文件'
  return read === 'loaded' ? '可预览' : '尚未验证读取'
}

export function fileDetails(file: LibraryFile): string {
  return [file.width && file.height ? `${file.width} × ${file.height}` : null,
    file.duration != null ? `${file.duration.toFixed(2)} 秒` : null].filter(Boolean).join(' · ')
}

export function fileModificationPrompt(file: LibraryFile): string {
  return `修改「${file.owner}」的这份素材：${mediaLabel(file.description) || file.title}${file.contentState ? `\n具体状态：${file.contentState}` : ''}`
}

export function selectedFileScope(file: LibraryFile): StudioScope {
  return file.version ? { kind: 'asset', id: file.groupId ?? file.asset?.id, label: file.title, asset_version_id: file.version.id, ...(file.take ? { take_id: file.take.id } : {}) }
    : file.take ? { kind: 'shot', id: file.take.shot_id, label: file.title, take_id: file.take.id }
      : { kind: 'film', label: file.title }
}
