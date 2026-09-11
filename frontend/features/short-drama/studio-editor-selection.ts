import type { EditorModel, EditorState, TimelineGapSelection } from '../../views/editor/editor-state'
import type { StudioEditorial, StudioScope } from './studio-api'

interface EditorHandoffOptions {
  state: EditorState
  revision: number
  shotScope?: StudioEditorial['scope']
  shotLabel?: string
  clipId?: string
  gap?: TimelineGapSelection
}

/** Snapshot only the active view and the actual target of this user action. */
export function editorAgentScope({ state, revision, shotScope, shotLabel, clipId, gap: explicitGap }: EditorHandoffOptions): StudioScope {
  const timeline = state.editorModel.timelines.find(item => item.id === state.editorModel.activeTimelineId)
  if (!timeline) throw new Error('当前剪辑时间线未找到，请重新打开剪辑。')
  const selection = state.session.selection
  const clips = timeline.clips.filter(clip => clipId ? clip.id === clipId : selection.clipIds.has(clip.id))
  if (clipId && !clips.length) throw new Error('选中的片段已经改变，请重新选择。')
  const subtitle = !clipId && !explicitGap ? timeline.subtitles.find(cue => cue.id === selection.subtitleId) : undefined
  const gap = explicitGap ?? (!clipId && !subtitle ? selection.gap : null)
  const focus = gap ? 'gap' : subtitle ? 'subtitle' : clips.length ? 'clips' : 'timeline'
  const shotIds = new Set(clips.map(clip => clip.sourceShotId).filter((id): id is string => !!id))
  const shotId = shotScope?.shot_id ?? (focus === 'clips' && shotIds.size === 1 && clips.every(clip => !!clip.sourceShotId) ? [...shotIds][0] : undefined)
  const markers = state.session.transport.timelineInOutMap[timeline.id]
  const label = shotScope
    ? `${shotLabel || '当前镜头'} · 成片 ${shotScope.film_start_seconds.toFixed(2)} 秒处`
    : subtitle ? '成片中选中的字幕' : clips.length ? `成片中选中的 ${clips.length} 个片段` : '本集剪辑'
  return {
    kind: shotId ? 'shot' : 'film', id: shotId, label,
    editor_selection: {
      timeline_id: timeline.id,
      editorial_revision: revision,
      coordinate_space: shotScope ? 'SHOT_LOCAL' : 'FILM',
      film_start_seconds: shotScope?.film_start_seconds ?? 0,
      anchor_clip_id: shotScope?.anchor_clip_id ?? null,
      focus,
      clips: clips.map(clip => ({
        id: clip.id, asset_id: clip.assetId, type: clip.type,
        source_shot_id: clip.sourceShotId, source_take_id: clip.sourceTakeId,
        track_index: clip.trackIndex, start_seconds: clip.startTime, duration_seconds: clip.duration,
        source_in_seconds: clip.trimStart,
        source_window_start_seconds: clip.sourceWindowStart,
        source_window_end_seconds: clip.sourceWindowEnd,
        speed: clip.speed, reversed: clip.reversed,
      })),
      subtitle_ids: subtitle ? [subtitle.id] : [],
      playhead_seconds: state.session.transport.currentTime,
      in_point_seconds: markers?.inPoint ?? null,
      out_point_seconds: markers?.outPoint ?? null,
      gap: gap ? { track_index: gap.trackIndex, start_seconds: gap.startTime, end_seconds: gap.endTime } : null,
    },
  }
}

export function editorGapRequest(gap: TimelineGapSelection, local: boolean): string {
  return `请在${local ? '当前镜头局部剪辑' : '本集剪辑'} ${gap.startTime.toFixed(2)}–${gap.endTime.toFixed(2)} 秒的空隙补充合适镜头。保留其他剪辑。`
}

export async function prepareEditorAgentScope(options: {
  readState: () => EditorState
  save: () => Promise<{ model: EditorModel; revision: number; shotScope?: StudioEditorial['scope'] }>
  shotLabel?: string
  clipId?: string
  gap?: TimelineGapSelection
}): Promise<StudioScope> {
  // Read at send/click time; later playback or selection changes cannot retarget this message.
  const selected = options.readState()
  const saved = await options.save()
  const oldId = selected.editorModel.activeTimelineId
  const markers = oldId ? selected.session.transport.timelineInOutMap[oldId] : undefined
  if (!saved.shotScope && !saved.model.timelines.some(item => item.id === oldId)) {
    throw new Error('发送时选中的时间线已经改变，请重新选择。')
  }
  // A local projection may get a new ID on adoption. Full-film timeline switches
  // during the save must not change the timeline this message was about.
  const model = saved.shotScope ? saved.model : { ...saved.model, activeTimelineId: oldId }
  const state = { ...selected, editorModel: model, session: { ...selected.session, transport: {
    ...selected.session.transport,
    timelineInOutMap: markers && model.activeTimelineId ? { [model.activeTimelineId]: markers } : {},
  } } }
  return editorAgentScope({ state, revision: saved.revision, shotScope: saved.shotScope,
    shotLabel: options.shotLabel, clipId: options.clipId, gap: options.gap })
}
