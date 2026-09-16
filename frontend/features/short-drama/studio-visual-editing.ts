import type { ClipTransition, TimelineClip } from '../../types/project-model'
import { STUDIO_TITLE_STYLE } from '../../lib/studio-title-style'
import type { EditorState } from '../../views/editor/editor-state'
import { addTextClip, addTrack, updateClip } from '../../views/editor/editor-actions'
import { selectActiveTimeline, selectClips } from '../../views/editor/editor-selectors'

export function addStudioTitle(state: EditorState, startTime: number, duration: number, shotId?: string): EditorState {
  const timeline = selectActiveTimeline(state)
  if (!timeline) return state
  // The shared addTextClip starts at five seconds before its final duration is
  // applied. Reserve that entire window so it cannot trim a later user clip.
  let trackIndex = timeline.tracks.findIndex((track, index) => track.kind === 'video' && track.type !== 'subtitle'
    && !track.locked && track.enabled !== false && !timeline.clips.some(clip => clip.trackIndex === index
      && clip.startTime < startTime + Math.max(5, duration) && clip.startTime + clip.duration > startTime))
  let next = state
  if (trackIndex < 0) { trackIndex = timeline.tracks.length; next = addTrack(next, 'video') }
  next = addTextClip(next, { startTime, trackIndex, style: STUDIO_TITLE_STYLE })
  const added = selectClips(next).find(clip => next.session.selection.clipIds.has(clip.id))
  return added ? updateClip(next, added.id, { duration: Math.max(0.1, duration), ...(shotId ? { sourceShotId: shotId } : {}) }) : next
}

export function adjacentVisualClip(clips: TimelineClip[], clip: TimelineClip, edge: 'transitionIn' | 'transitionOut') {
  return clips.find(item => item.id !== clip.id && item.trackIndex === clip.trackIndex && (item.type === 'video' || item.type === 'image')
    && Math.abs(edge === 'transitionIn' ? item.startTime + item.duration - clip.startTime : item.startTime - clip.startTime - clip.duration) < 0.001)
}

/** Update both ends of a dissolve in one undo/save step; clear its old partner. */
export function changeStudioTransition(clips: TimelineClip[], id: string, edge: 'transitionIn' | 'transitionOut', value: ClipTransition): TimelineClip[] {
  const clip = clips.find(item => item.id === id)
  if (!clip) return clips
  const peer = adjacentVisualClip(clips, clip, edge)
  if (value.type === 'dissolve' && !peer) return clips
  const otherEdge = edge === 'transitionIn' ? 'transitionOut' : 'transitionIn'
  return clips.map(item => {
    if (item.id === id) return { ...item, [edge]: value }
    if (item.id === peer?.id) {
      if (value.type === 'dissolve') return { ...item, [otherEdge]: value }
      if (clip[edge]?.type === 'dissolve' && item[otherEdge]?.type === 'dissolve') return { ...item, [otherEdge]: { type: 'none', duration: 0 } }
    }
    return item
  })
}
