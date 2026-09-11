import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../frontend/features/short-drama/studio-editor-selection.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { editorAgentScope, editorGapRequest, prepareEditorAgentScope } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)

function clip(id, extra = {}) {
  return { id, type: 'video', assetId: `asset-${id}`, sourceShotId: 'shot-1', sourceTakeId: 'take-1',
    trackIndex: 0, startTime: 0, duration: 2, trimStart: 4, speed: 1, reversed: false,
    sourceWindowStart: 4, sourceWindowEnd: 8, ...extra }
}
function state(clips = [clip('clip-1')]) {
  return { editorModel: { activeTimelineId: 'timeline-1', timelines: [
    { id: 'timeline-1', clips, subtitles: [{ id: 'cue-1', startTime: .75, endTime: 2 }] },
    { id: 'other-timeline', clips: [clip('hidden')], subtitles: [] },
  ] }, session: { selection: { clipIds: new Set(clips.map(item => item.id)), subtitleId: null, gap: null },
    transport: { currentTime: 1.25, timelineInOutMap: { 'timeline-1': { inPoint: .5, outPoint: 1.8 } } } } }
}
function local(anchor, start) {
  return { shot_id: 'shot-1', anchor_clip_id: anchor, film_start_seconds: start, duration_seconds: 2, occurrences: [] }
}

test('same shot preserves distinct saved use positions and source time coordinates', () => {
  const input = state([clip('clip-1', { speed: 2, reversed: true })])
  const original = structuredClone(input)
  const first = editorAgentScope({ state: input, revision: 38, shotScope: local('anchor-a', 10) })
  const second = editorAgentScope({ state: input, revision: 39, shotScope: local('anchor-b', 30) })
  assert.equal(first.kind, 'shot')
  assert.equal(first.editor_selection.coordinate_space, 'SHOT_LOCAL')
  assert.equal(first.editor_selection.anchor_clip_id, 'anchor-a')
  assert.equal(second.editor_selection.anchor_clip_id, 'anchor-b')
  assert.equal(second.editor_selection.film_start_seconds, 30)
  assert.equal(second.editor_selection.editorial_revision, 39)
  assert.equal(second.editor_selection.clips[0].source_in_seconds, 4)
  assert.equal(second.editor_selection.clips[0].source_window_end_seconds, 8)
  assert.equal(second.editor_selection.clips[0].speed, 2)
  assert.equal(second.editor_selection.clips[0].reversed, true)
  assert.deepEqual(input, original)
})

test('toolbar carries multi-selection only from the active timeline', () => {
  const input = state([clip('one'), clip('two', { sourceShotId: 'shot-2', startTime: 10 })])
  input.session.selection.clipIds.add('hidden')
  const scope = editorAgentScope({ state: input, revision: 5 })
  assert.equal(scope.kind, 'film')
  assert.deepEqual(scope.editor_selection.clips.map(item => item.id), ['one', 'two'])
  assert.equal(scope.editor_selection.coordinate_space, 'FILM')
  assert.equal(scope.editor_selection.film_start_seconds, 0)
  assert.equal(scope.editor_selection.playhead_seconds, 1.25)
  assert.equal(scope.editor_selection.in_point_seconds, .5)
  assert.equal(scope.editor_selection.out_point_seconds, 1.8)
})

test('subtitle focus is explicit even if an old video selection remains', () => {
  const input = state()
  input.session.selection.subtitleId = 'cue-1'
  const scope = editorAgentScope({ state: input, revision: 5 })
  assert.equal(scope.kind, 'film')
  assert.equal(scope.editor_selection.focus, 'subtitle')
  assert.deepEqual(scope.editor_selection.subtitle_ids, ['cue-1'])
  assert.deepEqual(scope.editor_selection.clips.map(item => item.id), ['clip-1'])
})

for (const type of ['image', 'audio']) {
  test(`${type} context-menu action keeps its actual media without a source shot`, () => {
    const input = state([clip('one'), clip('source', { type, sourceShotId: undefined, sourceTakeId: undefined })])
    input.session.selection.subtitleId = 'cue-1'
    const scope = editorAgentScope({ state: input, revision: 6, clipId: 'source' })
    assert.equal(scope.kind, 'film')
    assert.equal(scope.editor_selection.focus, 'clips')
    assert.deepEqual(scope.editor_selection.subtitle_ids, [])
    assert.deepEqual(scope.editor_selection.clips.map(item => [item.id, item.asset_id, item.type]), [['source', 'asset-source', type]])
  })
}

test('local gap uses local times with the exact use-position offset', () => {
  const gap = { trackIndex: 2, startTime: .4, endTime: 1.1 }
  const scope = editorAgentScope({ state: state(), revision: 7, shotScope: local('anchor-b', 30), gap })
  assert.equal(scope.editor_selection.focus, 'gap')
  assert.deepEqual(scope.editor_selection.gap, { track_index: 2, start_seconds: .4, end_seconds: 1.1 })
  assert.equal(scope.editor_selection.film_start_seconds + scope.editor_selection.gap.start_seconds, 30.4)
  assert.match(editorGapRequest(gap, true), /当前镜头局部剪辑 0.40–1.10/)
  assert.match(editorGapRequest(gap, false), /本集剪辑 0.40–1.10/)
})

test('an explicit deleted target cannot silently become a whole-film action', () => {
  assert.throws(() => editorAgentScope({ state: state(), revision: 7, clipId: 'deleted' }), /重新选择/)
})

test('direct chat reads current selection at send and keeps it while saving', async () => {
  let current = state([clip('one'), clip('two')])
  let completeSave
  const saved = new Promise(resolve => { completeSave = resolve })
  const reader = () => prepareEditorAgentScope({ readState: () => current, save: () => saved })
  // The editor changes after reader registration, before the user presses Send.
  current.session.selection.clipIds = new Set(['two'])
  current.session.transport.currentTime = 1.6
  const pending = reader()
  current = state([clip('one'), clip('two')])
  current.session.selection.clipIds = new Set(['one'])
  const updated = state([clip('one'), clip('two', { trimStart: 5, duration: 1.5 })]).editorModel
  updated.timelines[0].id = updated.activeTimelineId = 'saved-local-timeline'
  completeSave({ model: updated, revision: 42, shotScope: local('saved-anchor', 32) })
  const result = await pending
  assert.deepEqual(result.editor_selection.clips.map(item => item.id), ['two'])
  assert.equal(result.editor_selection.playhead_seconds, 1.6)
  assert.equal(result.editor_selection.editorial_revision, 42)
  assert.equal(result.editor_selection.anchor_clip_id, 'saved-anchor')
  assert.equal(result.editor_selection.film_start_seconds, 32)
  assert.equal(result.editor_selection.clips[0].source_in_seconds, 5)
  assert.equal(result.editor_selection.in_point_seconds, .5)
  assert.equal(result.editor_selection.out_point_seconds, 1.8)
})

test('failed save does not produce a sendable selection from unsaved content', async () => {
  await assert.rejects(() => prepareEditorAgentScope({ readState: () => state(),
    save: async () => { throw new Error('保存冲突') } }), /保存冲突/)
})

test('switching full-film timelines during a save does not retarget the message', async () => {
  const clicked = state([clip('one')])
  const savedModel = structuredClone(clicked.editorModel)
  savedModel.activeTimelineId = 'other-timeline'
  const scope = await prepareEditorAgentScope({ readState: () => clicked,
    save: async () => ({ model: savedModel, revision: 43 }) })
  assert.equal(scope.editor_selection.timeline_id, 'timeline-1')
  assert.deepEqual(scope.editor_selection.clips.map(item => item.id), ['one'])
  assert.equal(scope.editor_selection.playhead_seconds, 1.25)
  assert.equal(scope.editor_selection.in_point_seconds, .5)
  assert.equal(savedModel.activeTimelineId, 'other-timeline')
})
