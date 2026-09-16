import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import ts from 'typescript'

function compile(path, dependencies = {}) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8')
  let compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  for (const [name, url] of Object.entries(dependencies)) compiled = compiled.replaceAll(`'${name}'`, `'${url}'`)
  return `data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`
}
const mergeUrl = compile('../frontend/features/short-drama/studio-editor-merge.ts')
const { mergeEditorChanges } = await import(mergeUrl)
const { applyEditorRefresh } = await import(compile('../frontend/features/short-drama/studio-editor-refresh.ts', { './studio-editor-merge': mergeUrl }))

function model() {
  return { assets: [], bins: {}, activeTimelineId: 'timeline-1', timelines: [{ id: 'timeline-1', tracks: [{ type: 'video' }],
    clips: [{ id: 'clip-1', startTime: 0, duration: 10, trackIndex: 0, volume: 1 }],
    subtitles: [{ id: 'cue-1', text: '原字幕', startTime: 1, endTime: 4 }] }] }
}

function state() {
  return { editorModel: model(), projectSync: { dirty: false }, history: { undoStack: [], redoStack: [] }, session: {
    selection: { clipIds: new Set(['clip-1', 'deleted']), subtitleId: 'cue-1', editingSubtitleId: 'cue-1', gap: null },
    transport: { currentTime: 3.4, isPlaying: false, shuttleSpeed: 0, playingInOut: false, timelineInOutMap: { 'timeline-1': { inPoint: 1, outPoint: 6 } } },
    tools: { zoom: 1.7, snapEnabled: true }, ui: { openTimelineIds: new Set(['timeline-1', 'deleted']) },
  } }
}

test('clean refresh shows incoming captions while retaining playhead, valid selection, zoom and marks', () => {
  const before = state()
  const incoming = model()
  incoming.timelines[0].subtitles[0].text = 'Agent 新字幕'
  const result = applyEditorRefresh(before, incoming)
  assert.equal(result.editorModel.timelines[0].subtitles[0].text, 'Agent 新字幕')
  assert.equal(result.session.transport.currentTime, 3.4)
  assert.deepEqual([...result.session.selection.clipIds], ['clip-1'])
  assert.equal(result.session.selection.subtitleId, 'cue-1')
  assert.equal(result.session.selection.editingSubtitleId, 'cue-1')
  assert.equal(result.session.tools.zoom, 1.7)
  assert.deepEqual(result.session.transport.timelineInOutMap['timeline-1'], { inPoint: 1, outPoint: 6 })
  assert.deepEqual([...result.session.ui.openTimelineIds], ['timeline-1'])
  assert.equal(result.projectSync.dirty, false)
  assert.equal(before.editorModel.timelines[0].subtitles[0].text, '原字幕')
})

test('removed content clears invalid focus and clamps the playhead instead of selecting unrelated media', () => {
  const before = state()
  before.session.transport.currentTime = 9
  const incoming = model()
  incoming.timelines[0].clips = [{ id: 'clip-2', startTime: 0, duration: 2, trackIndex: 0 }]
  incoming.timelines[0].subtitles = []
  const result = applyEditorRefresh(before, incoming)
  assert.deepEqual([...result.session.selection.clipIds], [])
  assert.equal(result.session.selection.subtitleId, null)
  assert.equal(result.session.selection.editingSubtitleId, null)
  assert.equal(result.session.transport.currentTime, 2)
  assert.deepEqual(result.session.transport.timelineInOutMap['timeline-1'], { inPoint: 1, outPoint: 2 })
})

test('a gap selection survives only while the incoming timeline still has that gap', () => {
  const before = state()
  before.session.selection.gap = { trackIndex: 0, startTime: 10, endTime: 12 }
  assert.deepEqual(applyEditorRefresh(before, model()).session.selection.gap, before.session.selection.gap)
  const incoming = model()
  incoming.timelines[0].clips.push({ id: 'new-clip', startTime: 10, duration: 2, trackIndex: 0 })
  assert.equal(applyEditorRefresh(before, incoming).session.selection.gap, null)
})

test('explicit field merge preserves the user edit and receives unrelated Agent changes', () => {
  const base = model()
  const local = structuredClone(base)
  local.timelines[0].subtitles[0].text = '人工未保存字幕'
  const incoming = structuredClone(base)
  incoming.timelines[0].subtitles[0].text = 'Agent 同字段字幕'
  incoming.timelines[0].clips[0].volume = 0.4
  incoming.timelines[0].subtitles.push({ id: 'cue-2', text: '新增对白', startTime: 5, endTime: 7 })
  const merged = mergeEditorChanges(base, local, incoming)
  const before = state()
  before.editorModel = local
  const result = applyEditorRefresh(before, merged, true)
  assert.equal(result.editorModel.timelines[0].subtitles[0].text, '人工未保存字幕')
  assert.equal(result.editorModel.timelines[0].clips[0].volume, 0.4)
  assert.equal(result.editorModel.timelines[0].subtitles[1].text, '新增对白')
  assert.equal(result.projectSync.dirty, true)
})

test('ordinary undo snapshots keep unrelated incoming edits', () => {
  const before = state()
  const previous = model()
  previous.timelines[0].clips[0].volume = 0.7
  before.history.undoStack = [{ assets: previous.assets, bins: previous.bins, timelines: previous.timelines }]
  const incoming = model()
  incoming.timelines[0].subtitles[0].text = 'Agent 新字幕'
  const result = applyEditorRefresh(before, incoming)
  assert.equal(result.history.undoStack[0].timelines[0].clips[0].volume, 0.7)
  assert.equal(result.history.undoStack[0].timelines[0].subtitles[0].text, 'Agent 新字幕')
})
