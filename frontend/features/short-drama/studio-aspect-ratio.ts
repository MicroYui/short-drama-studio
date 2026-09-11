import type { Asset, Timeline } from '../../types/project-model'

/** Shared interpretation of the saved project ratio for previews and export. */
export function studioAspectRatio(value: string | null | undefined): number {
  const parts = value?.split(':').map(part => Number(part.trim())) ?? []
  if (parts.length !== 2 || parts.some(part => !Number.isFinite(part) || part <= 0)) return 16 / 9
  const ratio = parts[0] / parts[1]
  return Number.isFinite(ratio) && ratio > 0 ? ratio : 16 / 9
}

export function defaultStudioExportSize(value: string | null | undefined): string {
  const ratio = studioAspectRatio(value)
  if (ratio === 1) return '1080x1080'
  const longEdge = Math.min(3840, 720 * Math.max(ratio, 1 / ratio))
  const width = ratio >= 1 ? longEdge : longEdge * ratio
  const height = ratio >= 1 ? longEdge / ratio : longEdge
  const even = (dimension: number) => Math.max(2, Math.round(dimension / 2) * 2)
  return `${even(width)}x${even(height)}`
}

export type StudioExportDimensions = { width: number; height: number } | { width?: never; height?: never }

/** An automatic export must reach the backend without a guessed pixel size. */
export function studioExportDimensions(selection: string): StudioExportDimensions {
  if (selection === 'source') return {}
  const dimensions = selection.split('x').map(Number)
  if (dimensions.length !== 2 || dimensions.some(value => !Number.isSafeInteger(value) || value < 256)) {
    throw new Error('请选择有效的导出尺寸，每边需为至少 256 像素的整数。')
  }
  return { width: dimensions[0], height: dimensions[1] }
}

/** UI estimate only; export probes the saved source files for the actual dimensions. */
export function studioSourceExportSize(
  value: string | null | undefined,
  timeline: Pick<Timeline, 'tracks' | 'clips'> | null | undefined,
  assets: Asset[],
): { size: string | null; basis: 'video' | 'fallback' | 'unknown' } {
  const hasSolo = timeline?.tracks.some(track => track.solo)
  const clips = timeline?.clips.filter(clip => {
    const track = timeline.tracks[clip.trackIndex]
    return clip.type === 'video' && clip.duration > 0 && clip.opacity > 0
      && track && track.enabled !== false && (!hasSolo || track.solo)
  }) ?? []
  if (!clips.length) return { size: defaultStudioExportSize(value), basis: 'fallback' }
  let width = 0
  let height = 0
  for (const clip of clips) {
    // Match the export renderer's source choice, including per-use takeIndex.
    const asset = assets.find(item => item.id === clip.assetId)
    const source = clip.takeIndex === undefined ? asset : asset?.takes?.[clip.takeIndex] ?? asset
    const dimensions = source?.path === asset?.path && source?.width == null && source?.height == null ? asset : source
    if (!source?.path || !dimensions?.width || !dimensions.height
      || !Number.isFinite(dimensions.width) || !Number.isFinite(dimensions.height)
      || dimensions.width <= 0 || dimensions.height <= 0) return { size: null, basis: 'unknown' }
    width = Math.max(width, dimensions.width)
    height = Math.max(height, dimensions.height)
  }
  const ratio = studioAspectRatio(value)
  if (width / height < ratio) width = height * ratio
  else height = width / ratio
  // Never clamp a source estimate to a lower resolution; the backend reports limits.
  return { size: `${Math.ceil(width / 2) * 2}x${Math.ceil(height / 2) * 2}`, basis: 'video' }
}
