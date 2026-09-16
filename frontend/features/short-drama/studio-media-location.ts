import type { components } from '../../generated/backend-openapi'

export type StudioMediaUse = components['schemas']['StudioMediaUse']

/** One location in the project's existing content and saved editorial timeline. */
export interface StudioMediaLocation {
  episodeId: string
  shotId?: string | null
  timelineId?: string | null
  clipId?: string | null
  startSeconds?: number | null
}

export function mediaUseLocation(use: StudioMediaUse): StudioMediaLocation | null {
  if (use.kind === 'voice') return null
  if (use.kind === 'shot') return { episodeId: use.episode_id, shotId: use.shot_id ?? use.target_id }
  // For editorial uses the API's target_id is the exact clip ID, including
  // repeated uses of the same source shot and clips without a source shot.
  return {
    episodeId: use.episode_id,
    shotId: use.shot_id ?? null,
    timelineId: use.timeline_id ?? null,
    clipId: use.target_id,
    startSeconds: use.start_seconds ?? null,
  }
}
