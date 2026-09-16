/** A linked audio clip owns the original sound even when its track is muted/disabled. */
export function clipAudioDelegated<T extends { id: string; type: string; linkedClipIds?: string[] }>(
  clip: T, allClips: readonly T[], sourcePath: (item: T) => string,
): boolean {
  if (clip.type !== 'video') return false
  const path = sourcePath(clip)
  return Boolean(path) && allClips.some(candidate => candidate.type === 'audio'
    && (clip.linkedClipIds?.includes(candidate.id) || candidate.linkedClipIds?.includes(clip.id))
    && sourcePath(candidate) === path)
}
