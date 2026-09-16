import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { test } from 'node:test'

const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
async function module(path) {
  const result = await build({ entryPoints: [path], bundle: true, platform: 'node', format: 'esm', write: false })
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`)
}
const { createEditorSaveQueue, EditorSessionClosed, locateEditorState, locateRecoveredEditorState, savedShotUseSelection } = await module('frontend/features/short-drama/studio-editor-save.ts')
const { editorDraftKey, createEditorDraftWriter, parseEditorDraft, requireExistingDraftTimelines } = await module('frontend/features/short-drama/studio-editor-drafts.ts')
const { mergeEditorChanges } = await module('frontend/features/short-drama/studio-editor-merge.ts')
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const tick = () => new Promise(resolve => setImmediate(resolve))

function saveFixture() {
  const state = { active: true, model: { text: 'first' }, saved: null, revision: 4, calls: [], acknowledgements: [], saving: [] }
  const queue = createEditorSaveQueue({ active: () => state.active, dirty: () => state.model !== state.saved,
    model: () => state.model, revision: () => state.revision,
    write: (model, revision) => { const wait = deferred(); state.calls.push({ model, revision, wait }); return wait.promise },
    acknowledge: (model, revision) => { state.acknowledgements.push(model); state.saved = model; state.revision = revision },
    pending: value => state.saving.push(value) })
  return { state, queue }
}

test('saving continues editing and sends only the newest subsequent model at the acknowledged revision', async () => {
  const { state, queue } = saveFixture()
  const result = queue.flush()
  const first = state.model
  state.model = { text: 'intermediate' }
  state.model = { text: 'latest typing' }
  const secondWaiter = queue.flush()
  assert.equal(state.calls.length, 1)
  state.calls[0].wait.resolve(5)
  await tick()
  assert.equal(state.saved, first)
  assert.equal(state.model.text, 'latest typing')
  assert.equal(state.calls.length, 2)
  assert.equal(state.calls[1].revision, 5)
  assert.equal(state.calls[1].model, state.model)
  state.calls[1].wait.resolve(6)
  assert.equal(await result, 6)
  assert.equal(await secondWaiter, 6)
  assert.equal(state.saved, state.model)
  assert.deepEqual(state.saving, [true, false])
})

test('a local explicit save acknowledges one snapshot and leaves later edits unsaved', async () => {
  const { state, queue } = saveFixture()
  const result = queue.flush(false)
  const submitted = state.model
  state.model = { text: 'edited during save' }
  state.calls[0].wait.resolve(5)
  assert.equal(await result, 5)
  assert.equal(state.calls.length, 1)
  assert.equal(state.saved, submitted)
  assert.notEqual(state.saved, state.model)
})

test('a closed editor ignores late responses and never launches its queued subsequent edit', async () => {
  const { state, queue } = saveFixture()
  const result = queue.flush()
  state.model = { text: 'not yet sent' }
  state.active = false
  state.calls[0].wait.resolve(5)
  await assert.rejects(result, EditorSessionClosed)
  assert.equal(state.calls.length, 1)
  assert.equal(state.acknowledgements.length, 0)
  assert.equal(state.revision, 4)
})

test('a failed request retains the current edit and can retry at the unacknowledged revision', async () => {
  const { state, queue } = saveFixture()
  const result = queue.flush()
  state.calls[0].wait.reject(new Error('offline'))
  await assert.rejects(result, /offline/)
  assert.equal(state.saved, null)
  const retry = queue.flush()
  assert.equal(state.calls[1].revision, 4)
  state.calls[1].wait.resolve(5)
  assert.equal(await retry, 5)
})

test('draft keys separate project, episode and actual local shot use while FILM covers all timelines', () => {
  const film = { projectId: 'series', episodeId: 'episode' }
  assert.equal(editorDraftKey(film), editorDraftKey({ ...film, timelineId: 'other' }))
  const shot = { ...film, shotId: 'shot', timelineId: 'timeline', anchorClipId: 'use-1' }
  const keys = [film, { ...film, episodeId: 'other' }, { ...film, projectId: 'other' }, shot,
    { ...shot, anchorClipId: 'use-2' }, { ...shot, timelineId: 'other' }, { ...shot, anchorClipId: null }].map(editorDraftKey)
  assert.equal(new Set(keys).size, keys.length)
})

test('first adoption uses its acknowledged clip in the actual saved timeline without another network read', () => {
  const snapshot = { timelines: [{ id: 'preview', clips: [{ id: 'new-video' }, { id: 'new-audio' }] }] }
  const saved = { activeTimelineId: 'actual', timelines: [{ id: 'other', clips: [{ id: 'new-video', type: 'video', sourceShotId: 'shot', startTime: 0 }] },
    { id: 'actual', clips: [{ id: 'old-use', type: 'video', sourceShotId: 'shot', startTime: 0 },
      { id: 'new-audio', type: 'audio', sourceShotId: 'shot', startTime: 4 }, { id: 'new-video', type: 'video', sourceShotId: 'shot', startTime: 4 }] }] }
  assert.deepEqual(savedShotUseSelection(snapshot, saved, 'shot'), { timelineId: 'actual', clipId: 'new-video' })
  assert.deepEqual(savedShotUseSelection(snapshot, saved, 'different-shot'), { timelineId: 'actual', clipId: undefined })
  assert.throws(() => savedShotUseSelection(snapshot, { ...saved, activeTimelineId: 'removed' }, 'shot'), /没有当前时间线/)
})

test('draft writes coalesce intermediate edits and a save clear cannot erase a newer pending draft', async () => {
  const calls = []
  const writer = createEditorDraftWriter(value => { const wait = deferred(); calls.push({ value, wait }); return wait.promise })
  const first = { revision: 1, current: 'first' }, latest = { revision: 2, current: 'after acknowledged save' }
  const pending = writer.put(first)
  writer.put(null)
  writer.put(latest)
  assert.equal(calls.length, 1)
  calls[0].wait.resolve(true)
  await tick()
  assert.equal(calls.length, 2)
  assert.equal(calls[1].value, latest)
  calls[1].wait.resolve(true)
  await pending
})

test('draft quota and ownership errors are propagated instead of reporting persistence', async () => {
  const quota = createEditorDraftWriter(async () => { throw new Error('QuotaExceededError') })
  await assert.rejects(quota.put({ revision: 1 }), /QuotaExceeded/)
  const old = createEditorDraftWriter(async () => false)
  await assert.rejects(old.put(null), /另一个窗口/)
})

function project() {
  return { version: 2, id: 'episode', name: '测试', createdAt: 0, updatedAt: 0, assets: [], bins: {}, activeTimelineId: 'main',
    timelines: [{ id: 'main', name: '成片', createdAt: 0, tracks: [], clips: [], subtitles: [] }] }
}

test('persisted recovery validates the existing project schema without silently discarding damaged data', () => {
  const value = { version: 1, revision: 4, updatedAt: 100, base: project(), current: project() }
  assert.deepEqual(parseEditorDraft(JSON.parse(JSON.stringify(value))), value)
  assert.throws(() => parseEditorDraft({ ...value, current: { id: 'missing-content' } }))
  assert.throws(() => parseEditorDraft({ ...value, version: 2 }), /无法读取/)
})

test('recovery keeps original merge base, retains unrelated remote edits, and rejects a deleted target timeline', () => {
  const base = project(), current = project(), latest = project()
  current.timelines[0].name = '本机剪辑名称'
  latest.name = 'Agent 更新的项目名'
  const draft = { version: 1, revision: 1, updatedAt: 0, base, current }
  requireExistingDraftTimelines(draft, latest)
  const merged = mergeEditorChanges(base, current, latest)
  assert.equal(merged.timelines[0].name, current.timelines[0].name)
  assert.equal(merged.name, latest.name)
  latest.timelines = []
  assert.throws(() => requireExistingDraftTimelines(draft, latest), /已被移除/)
})

test('initial usage location picks the precise timeline and clip once without modifying other content', () => {
  const state = { editorModel: { ...project(), timelines: [...project().timelines,
    { id: 'alternate', clips: [{ id: 'exact-use', startTime: 11, duration: 2 }] }] },
    session: { selection: { clipIds: new Set(), subtitleId: null }, transport: { currentTime: 0 }, ui: { openTimelineIds: new Set(['main']) } } }
  const next = locateEditorState(state, { timelineId: 'alternate', clipId: 'exact-use', startSeconds: 11 })
  assert.equal(next.editorModel.activeTimelineId, 'alternate')
  assert.equal(next.session.transport.currentTime, 11)
  assert.deepEqual([...next.session.selection.clipIds], ['exact-use'])
  assert.deepEqual([...next.session.ui.openTimelineIds], ['main', 'alternate'])
  assert.deepEqual([...state.session.ui.openTimelineIds], ['main'])
  assert.equal(state.editorModel.activeTimelineId, 'main')
  assert.throws(() => locateEditorState(state, { timelineId: 'gone' }), /已不存在/)
  assert.throws(() => locateEditorState(state, { timelineId: 'alternate', clipId: 'gone' }), /已变化/)
})

test('a recovered draft opens when its former usage clip was replaced, keeping valid timeline and playhead', () => {
  const state = { editorModel: { ...project(), timelines: [{ id: 'main', clips: [{ id: 'inserted-video', startTime: 5, duration: 3 }] }] },
    session: { selection: { clipIds: new Set(), subtitleId: null }, transport: { currentTime: 0 }, ui: { openTimelineIds: new Set(['other']) } } }
  const recovered = locateRecoveredEditorState(state, { timelineId: 'main', clipId: 'old-video', startSeconds: 5 })
  assert.equal(recovered.state.editorModel.activeTimelineId, 'main')
  assert.equal(recovered.state.session.transport.currentTime, 5)
  assert.equal(recovered.state.session.selection.clipIds.size, 0)
  assert.deepEqual([...recovered.state.session.ui.openTimelineIds], ['other', 'main'])
  assert.match(recovered.notice, /移除或替换/)
  assert.equal(recovered.state.editorModel.timelines[0].clips[0].id, 'inserted-video')
  const removedTimeline = locateRecoveredEditorState(state, { timelineId: 'old-timeline', clipId: 'old-video', startSeconds: 5 })
  assert.equal(removedTimeline.state, state)
  assert.match(removedTimeline.notice, /草稿当前时间线/)
  assert.throws(() => locateEditorState(state, { timelineId: 'main', clipId: 'old-video' }), /已变化/)
})
