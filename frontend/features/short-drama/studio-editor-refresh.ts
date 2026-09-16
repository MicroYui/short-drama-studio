import type { EditorModel, EditorState, EditorUndoSnapshot } from '../../views/editor/editor-state'
import { mergeCurrentChanges } from './studio-editor-merge'

/** Install current project content without resetting the user's editor position. */
export function applyEditorRefresh(state: EditorState, editorModel: EditorModel, dirty = false): EditorState {
  const timeline = editorModel.timelines.find(item => item.id === editorModel.activeTimelineId) ?? editorModel.timelines[0]
  const duration = Math.max(0, ...(timeline?.clips.map(clip => clip.startTime + clip.duration) ?? []), ...(timeline?.subtitles.map(cue => cue.endTime) ?? []))
  const clipIds = new Set(timeline?.clips.map(clip => clip.id) ?? [])
  const subtitleIds = new Set(timeline?.subtitles.map(cue => cue.id) ?? [])
  const timelineIds = new Set(editorModel.timelines.map(item => item.id))
  const gap = state.session.selection.gap
  const validGap = gap && timeline?.tracks[gap.trackIndex] && gap.startTime >= 0 && gap.endTime > gap.startTime
    && !timeline.clips.some(clip => clip.trackIndex === gap.trackIndex && clip.startTime < gap.endTime && clip.startTime + clip.duration > gap.startTime)
  const before: EditorUndoSnapshot = { assets: state.editorModel.assets, bins: state.editorModel.bins, timelines: state.editorModel.timelines }
  const after: EditorUndoSnapshot = { assets: editorModel.assets, bins: editorModel.bins, timelines: editorModel.timelines }
  // Keep ordinary undo useful without rolling back unrelated incoming changes.
  const rebaseHistory = (snapshots: EditorUndoSnapshot[]) => snapshots.map(snapshot => mergeCurrentChanges(before, snapshot, after))
  return {
    ...state,
    editorModel,
    projectSync: { dirty },
    history: { undoStack: rebaseHistory(state.history.undoStack), redoStack: rebaseHistory(state.history.redoStack) },
    session: {
      ...state.session,
      selection: {
        ...state.session.selection,
        clipIds: new Set([...state.session.selection.clipIds].filter(id => clipIds.has(id))),
        subtitleId: state.session.selection.subtitleId && subtitleIds.has(state.session.selection.subtitleId) ? state.session.selection.subtitleId : null,
        editingSubtitleId: state.session.selection.editingSubtitleId && subtitleIds.has(state.session.selection.editingSubtitleId) ? state.session.selection.editingSubtitleId : null,
        gap: validGap ? gap : null,
      },
      transport: {
        ...state.session.transport,
        currentTime: Math.min(state.session.transport.currentTime, duration),
        timelineInOutMap: Object.fromEntries(Object.entries(state.session.transport.timelineInOutMap).filter(([id]) => timelineIds.has(id)).map(([id, range]) => {
          const target = editorModel.timelines.find(item => item.id === id)!
          const end = Math.max(0, ...target.clips.map(clip => clip.startTime + clip.duration), ...target.subtitles.map(cue => cue.endTime))
          const inPoint = range.inPoint === null ? null : Math.min(range.inPoint, end)
          const outPoint = range.outPoint === null ? null : Math.min(range.outPoint, end)
          return [id, inPoint !== null && outPoint !== null && inPoint >= outPoint ? { inPoint: null, outPoint: null } : { inPoint, outPoint }]
        })),
      },
      ui: {
        ...state.session.ui,
        openTimelineIds: new Set([...state.session.ui.openTimelineIds].filter(id => timelineIds.has(id))),
      },
    },
  }
}
