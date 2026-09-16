import { createInitialEditorState, type EditorModel } from '../../views/editor/editor-state'
import { createEditorStore, type EditorStoreApi } from '../../views/editor/editor-store'
import { applyEditorRefresh } from './studio-editor-refresh'

export interface StudioPreviewSession {
  store: EditorStoreApi
  playbackTimeRef: { current: number }
  sourceModel: EditorModel
  revision: number
  defaultSubtitleFontFamily: string | null
}

/** Content refreshes keep the monitor and its media pool alive within one timeline. */
export function refreshPreviewSession(current: StudioPreviewSession | null, sourceModel: EditorModel, revision: number, showSubtitles: boolean, defaultSubtitleFontFamily: string | null = current?.defaultSubtitleFontFamily ?? null): StudioPreviewSession {
  if (current && revision < current.revision) return current
  const editorModel = showSubtitles ? sourceModel : {
    ...sourceModel,
    timelines: sourceModel.timelines.map(timeline => ({ ...timeline, subtitles: [] })),
  }
  if (!current || current.sourceModel.activeTimelineId !== sourceModel.activeTimelineId) {
    return { store: createEditorStore(createInitialEditorState(editorModel)), playbackTimeRef: { current: 0 }, sourceModel, revision, defaultSubtitleFontFamily }
  }
  current.store.getState().setStateWithoutHistory(state => {
    // The animation clock can be ahead of the transport's throttled state update.
    const atTime = state.session.transport.isPlaying ? current.playbackTimeRef.current : state.session.transport.currentTime
    const next = applyEditorRefresh({ ...state, session: { ...state.session, transport: { ...state.session.transport, currentTime: atTime } } }, editorModel)
    current.playbackTimeRef.current = next.session.transport.currentTime
    return next
  })
  return { ...current, sourceModel, revision, defaultSubtitleFontFamily }
}
