import type { Project } from '../../types/project-model'
import type { EditorModel, EditorState } from '../../views/editor/editor-state'

/** The save response already contains the actual first adopted use. */
export function savedShotUseSelection(snapshot: EditorModel, saved: Project, shotId: string): { timelineId: string; clipId?: string } {
  const timeline = saved.timelines.find(item => item.id === saved.activeTimelineId)
  if (!timeline) throw new Error('保存结果中没有当前时间线。')
  const submittedIds = new Set(snapshot.timelines.flatMap(item => item.clips).map(clip => clip.id))
  const clip = timeline.clips.filter(item => item.type === 'video' && item.sourceShotId === shotId && submittedIds.has(item.id))
    .sort((a, b) => a.startTime - b.startTime)[0]
  return { timelineId: timeline.id, clipId: clip?.id }
}

export function locateEditorState(state: EditorState, selection?: { timelineId: string; clipId?: string; startSeconds?: number }): EditorState {
  if (!selection) return state
  const timeline = state.editorModel.timelines.find(item => item.id === selection.timelineId)
  if (!timeline) throw new Error('要定位的剪辑时间线已不存在，请从当前使用位置重新打开。')
  const clip = selection.clipId ? timeline.clips.find(item => item.id === selection.clipId) : undefined
  if (selection.clipId && !clip) throw new Error('要定位的片段已变化，请从当前使用位置重新打开。')
  const end = Math.max(0, ...timeline.clips.map(item => item.startTime + item.duration))
  const position = selection.startSeconds ?? clip?.startTime ?? state.session.transport.currentTime
  return { ...state, editorModel: { ...state.editorModel, activeTimelineId: timeline.id }, session: { ...state.session,
    selection: { ...state.session.selection, clipIds: new Set(clip ? [clip.id] : []), subtitleId: null },
    ui: { ...state.session.ui, openTimelineIds: new Set([...state.session.ui.openTimelineIds, timeline.id]) },
    transport: { ...state.session.transport, currentTime: Math.min(end, Math.max(0, Number.isFinite(position) ? position : 0)) } } }
}

export function locateRecoveredEditorState(state: EditorState, selection?: { timelineId: string; clipId?: string; startSeconds?: number }): { state: EditorState; notice: string | null } {
  if (!selection) return { state, notice: null }
  const timeline = state.editorModel.timelines.find(item => item.id === selection.timelineId)
  if (!timeline) return { state, notice: '此前定位的时间线不在这份草稿中，已打开草稿当前时间线。' }
  if (selection.clipId && !timeline.clips.some(clip => clip.id === selection.clipId)) {
    return { state: locateEditorState(state, { ...selection, clipId: undefined }),
      notice: '此前定位的片段已在草稿中移除或替换。已恢复草稿，保留时间线与播放头；请重新选择片段。' }
  }
  return { state: locateEditorState(state, selection), notice: null }
}

export class EditorSessionClosed extends Error {
  constructor() { super('这次剪辑已关闭；保存结果不会应用到其他页面。') }
}

/** Serialize writes while allowing the active model to continue changing. */
export function createEditorSaveQueue<Model, Result>(options: {
  active: () => boolean
  dirty: () => boolean
  model: () => Model
  revision: () => number
  write: (model: Model, revision: number) => Promise<Result>
  acknowledge: (model: Model, result: Result) => Promise<void> | void
  pending: (saving: boolean) => void
}) {
  let running: Promise<number> | null = null
  const flush = (drain = true): Promise<number> => {
    if (!options.active()) return Promise.reject(new EditorSessionClosed())
    if (running) return running.then(revision => drain && options.dirty() ? flush(true) : revision)
    const run = async () => {
      do {
        if (!options.active()) throw new EditorSessionClosed()
        if (!options.dirty()) break
        const snapshot = options.model()
        const result = await options.write(snapshot, options.revision())
        if (!options.active()) throw new EditorSessionClosed()
        await options.acknowledge(snapshot, result)
      } while (drain && options.dirty())
      if (!options.active()) throw new EditorSessionClosed()
      return options.revision()
    }
    options.pending(true)
    running = run().finally(() => { running = null; if (options.active()) options.pending(false) })
    return running
  }
  return { flush, pending: () => running }
}
