import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')

const bundled = await build({ entryPoints: ['frontend/features/short-drama/studio-shot-drafts.ts'], bundle: true, write: false, platform: 'node', format: 'esm' })
const { acknowledgeShotSave, loadShotDrafts, persistShotDrafts, updateShotDraft } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString('base64')}`)
function shot(extra = {}) { return { id: 'shot-1', revision: 1, duration_seconds: 4, visual: '门口', camera: '固定', timeline_beats: [{ start_seconds: 0, end_seconds: 4, visual: '抬头' }], ...extra } }
function draft(extra = {}) { return { editId: 'edit-1', shot: shot({ visual: '门口两个人' }), base: shot(), revision: 1, ...extra } }
function storage(initial = null) { let value = initial; return { getItem: () => value, setItem: (_key, next) => { value = next }, removeItem: () => { value = null } } }

test('two episodes recover independent shot edits and clear only the saved or cancelled draft', () => {
  let drafts = updateShotDraft({}, 'episode-1', 'shot-1', draft())
  drafts = updateShotDraft(drafts, 'episode-2', 'shot-1', draft({ editId: 'edit-2', shot: shot({ visual: '第二集' }) }))
  const target = storage()
  assert.equal(persistShotDrafts(drafts, target), true)
  assert.deepEqual(loadShotDrafts(target), drafts)
  drafts = updateShotDraft(loadShotDrafts(target), 'episode-1', 'shot-1', null)
  assert.equal(drafts['episode-1'], undefined)
  assert.equal(drafts['episode-2']['shot-1'].shot.visual, '第二集')
  assert.equal(persistShotDrafts(updateShotDraft(drafts, 'episode-2', 'shot-1', null), target), true)
  assert.equal(target.getItem(), null)
})

test('late save response merges only changes typed after submission, including nested edits', async () => {
  const submitted = draft()
  let drafts = updateShotDraft({}, 'episode-1', 'shot-1', submitted)
  let complete
  const request = new Promise(resolve => { complete = resolve })
  const pending = request.then(saved => { drafts = updateShotDraft(drafts, 'episode-1', 'shot-1', current => acknowledgeShotSave(current, submitted, saved)) })
  drafts = updateShotDraft(drafts, 'episode-1', 'shot-1', current => ({ ...current, shot: { ...current.shot, camera: '向右移动', timeline_beats: [{ ...current.shot.timeline_beats[0], visual: '微笑抬头' }] } }))
  drafts = updateShotDraft(drafts, 'episode-2', 'shot-1', draft({ editId: 'edit-2' }))
  const saved = shot({ ...submitted.shot, revision: 2, visual: '门口两个人。' })
  complete(saved)
  await pending
  const retained = drafts['episode-1']['shot-1']
  assert.equal(retained.shot.visual, saved.visual)
  assert.equal(retained.shot.camera, '向右移动')
  assert.equal(retained.shot.timeline_beats[0].visual, '微笑抬头')
  assert.equal(retained.revision, 2)
  assert.deepEqual(retained.base, saved)
  assert.equal(drafts['episode-2']['shot-1'].editId, 'edit-2')
})

test('old responses cannot close a cancelled or reopened shot editor', () => {
  const submitted = draft()
  const saved = shot({ ...submitted.shot, revision: 2 })
  assert.equal(acknowledgeShotSave(submitted, submitted, saved), null)
  assert.equal(acknowledgeShotSave(null, submitted, saved), null)
  const reopened = draft({ editId: 'edit-3' })
  assert.equal(acknowledgeShotSave(reopened, submitted, saved), reopened)
})

test('an older response arriving after a newer save never moves the confirmed baseline backwards', async () => {
  const first = draft()
  let current = first
  let finishFirst, finishSecond
  const firstResponse = new Promise(resolve => { finishFirst = resolve }).then(saved => { current = acknowledgeShotSave(current, first, saved) })
  // The first write reached the server. After leaving and reopening the page,
  // the user explicitly merges that current revision and sends another save.
  const firstSaved = shot({ ...first.shot, revision: 2 })
  current = { ...current, revision: 2, base: firstSaved, shot: { ...firstSaved, visual: '第二次保存的画面' } }
  const second = current
  const secondResponse = new Promise(resolve => { finishSecond = resolve }).then(saved => { current = acknowledgeShotSave(current, second, saved) })
  current = { ...current, shot: { ...current.shot, camera: '请求期间继续输入的运镜' } }
  const secondSaved = { ...second.shot, revision: 3 }
  finishSecond(secondSaved)
  await secondResponse
  const afterSecond = current
  assert.equal(current.revision, 3)
  assert.equal(current.shot.camera, '请求期间继续输入的运镜')
  finishFirst(firstSaved)
  await firstResponse
  assert.equal(current, afterSecond)
  assert.deepEqual(current.base, secondSaved)
  assert.equal(current.revision, 3)
})

test('equal revision acknowledgements preserve an already rebased or already confirmed draft', () => {
  const submitted = draft()
  const saved = shot({ ...submitted.shot, revision: 2 })
  const rebased = { ...submitted, revision: 2, base: saved, shot: { ...saved, camera: '显式合并后继续编辑' } }
  assert.equal(acknowledgeShotSave(rebased, submitted, saved), rebased)
  const firstAck = acknowledgeShotSave({ ...submitted, shot: { ...submitted.shot, camera: '保存期间的输入' } }, submitted, saved)
  assert.equal(firstAck.revision, saved.revision)
  assert.equal(acknowledgeShotSave(firstAck, submitted, saved), firstAck)
  // Restoring a persisted submitted snapshot has not advanced its baseline.
  const restored = JSON.parse(JSON.stringify(submitted))
  assert.equal(acknowledgeShotSave(restored, submitted, saved), null)
})

test('a same-revision acknowledgement may still confirm a baseline that has not advanced since submission', () => {
  const submitted = draft()
  const saved = { ...submitted.shot }
  assert.equal(acknowledgeShotSave(JSON.parse(JSON.stringify(submitted)), submitted, saved), null)
  const continuing = { ...submitted, shot: { ...submitted.shot, camera: '继续输入' } }
  const result = acknowledgeShotSave(continuing, submitted, { ...saved, visual: '服务端规范后的画面' })
  assert.equal(result.shot.camera, '继续输入')
  assert.equal(result.shot.visual, '服务端规范后的画面')
  assert.deepEqual(result.base, { ...saved, visual: '服务端规范后的画面' })
})

test('invalid cached entries are ignored and persistence failures leave live content intact', () => {
  const current = { 'episode-1': { 'shot-1': draft() } }
  const unavailable = { getItem: () => { throw new Error('unavailable') }, setItem: () => { throw new Error('quota') }, removeItem: () => { throw new Error('quota') } }
  assert.deepEqual(loadShotDrafts(unavailable), {})
  assert.equal(persistShotDrafts(current, unavailable), false)
  assert.equal(current['episode-1']['shot-1'].shot.visual, '门口两个人')
  assert.deepEqual(loadShotDrafts(storage(JSON.stringify({ 'episode-1': { 'shot-1': draft(), invalid: draft(), broken: { revision: 2 } } }))), current)
})
