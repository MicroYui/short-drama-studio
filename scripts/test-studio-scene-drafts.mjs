import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../frontend/features/short-drama/studio-scene-drafts.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { acknowledgeSceneSave, loadSceneDrafts, persistSceneDrafts, sceneDraftChanged, updateSceneDraft } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)

function draft(overrides = {}) {
  return { editId: 'edit-1', heading: '走廊', text: '我回来了。', baseHeading: '走廊', base: '门开了。', ...overrides }
}

function storage(initial = null) {
  let value = initial
  return { getItem: () => value, setItem: (_key, next) => { value = next }, removeItem: () => { value = null } }
}

test('page/episode switches can return to the same scene draft without sharing edits between episodes', () => {
  let drafts = updateSceneDraft({}, 'episode-1', 'scene-1', draft())
  drafts = updateSceneDraft(drafts, 'episode-2', 'scene-1', draft({ editId: 'edit-2', text: '另一集的正文' }))
  drafts = updateSceneDraft(drafts, 'episode-1', 'scene-2', draft({ editId: 'edit-3', text: '第二场的正文' }))
  const saved = storage()
  persistSceneDrafts(drafts, saved)
  const restored = loadSceneDrafts(saved)
  assert.deepEqual(restored, drafts)
  assert.equal(restored['episode-1']['scene-1'].text, '我回来了。')
  assert.equal(restored['episode-2']['scene-1'].text, '另一集的正文')
  const next = updateSceneDraft(restored, 'episode-1', 'scene-1', null)
  assert.equal(next['episode-1']['scene-1'], undefined)
  assert.equal(next['episode-1']['scene-2'].text, '第二场的正文')
  assert.equal(next['episode-2']['scene-1'].text, '另一集的正文')
})

test('a completed save confirms its snapshot and preserves typing that happened while awaiting the response', async () => {
  const submitted = draft()
  let drafts = updateSceneDraft({}, 'episode-1', 'scene-1', submitted)
  let complete
  const request = new Promise(resolve => { complete = resolve })
  const pending = request.then(saved => {
    drafts = updateSceneDraft(drafts, 'episode-1', 'scene-1', current => acknowledgeSceneSave(current, submitted, saved))
  })
  drafts = updateSceneDraft(drafts, 'episode-1', 'scene-1', current => ({ ...current, text: '我回来了。门却又关上了。' }))
  // While the first episode saves, the user is free to work in another episode.
  drafts = updateSceneDraft(drafts, 'episode-2', 'scene-1', draft({ editId: 'edit-2', text: '另一集' }))
  complete({ heading: '走廊', screenplay_text: submitted.text })
  await pending
  assert.equal(drafts['episode-1']['scene-1'].text, '我回来了。门却又关上了。')
  assert.equal(drafts['episode-1']['scene-1'].base, submitted.text)
  assert.equal(sceneDraftChanged(drafts['episode-1']['scene-1']), true)
  assert.equal(drafts['episode-2']['scene-1'].text, '另一集')
})

test('successful unchanged save closes only its own editor, including server text normalization', () => {
  const submitted = draft()
  assert.equal(acknowledgeSceneSave(submitted, submitted, { heading: '走廊', screenplay_text: '我回来了。\n' }), null)
  assert.equal(acknowledgeSceneSave(null, submitted, { heading: '走廊', screenplay_text: submitted.text }), null)
  const reopened = draft({ editId: 'new-edit' })
  assert.equal(acknowledgeSceneSave(reopened, submitted, { heading: '走廊', screenplay_text: submitted.text }), reopened)
})

test('typing in one field preserves server-normalized content in the other field', () => {
  const submitted = draft({ heading: '  走廊  ' })
  const current = { ...submitted, text: '继续写入的正文' }
  const result = acknowledgeSceneSave(current, submitted, { heading: '走廊', screenplay_text: submitted.text })
  assert.equal(result.heading, '走廊')
  assert.equal(result.baseHeading, '走廊')
  assert.equal(result.text, '继续写入的正文')
  assert.equal(result.base, submitted.text)
})

test('reviewing a newer remote text or title while saving keeps that explicit baseline after the old response', async () => {
  for (const reviewed of [{ base: 'Agent 刚写入的最新正文' }, { baseHeading: 'Agent 刚更新的标题' }]) {
    const submitted = draft()
    let current = submitted
    let complete
    const request = new Promise(resolve => { complete = resolve })
    const pending = request.then(saved => { current = acknowledgeSceneSave(current, submitted, saved) })
    // Matches the existing “我已对照最新正文” action, followed by more typing.
    current = { ...current, ...reviewed, text: '对照后继续输入的正文' }
    const afterReview = current
    complete({ heading: '走廊', screenplay_text: submitted.text })
    await pending
    assert.equal(current, afterReview)
    assert.equal(current.text, '对照后继续输入的正文')
    assert.equal(sceneDraftChanged(current), true)
  }
})

test('a repeated old acknowledgement cannot replace the baseline from a more recent confirmation', () => {
  const first = draft()
  const afterFirst = acknowledgeSceneSave({ ...first, text: '第二次提交的正文' }, first, { heading: first.heading, screenplay_text: first.text })
  const second = afterFirst
  const current = acknowledgeSceneSave({ ...second, text: '第二次提交后继续输入' }, second, { heading: second.heading, screenplay_text: second.text })
  assert.equal(current.base, second.text)
  assert.equal(acknowledgeSceneSave(current, first, { heading: first.heading, screenplay_text: first.text }), current)
  // A restored same-request draft remains eligible for its ordinary ack.
  assert.equal(acknowledgeSceneSave(JSON.parse(JSON.stringify(first)), first, { heading: first.heading, screenplay_text: first.text }), null)
})

test('pending status counts real text/title changes, not merely opening an editor', () => {
  assert.equal(sceneDraftChanged(draft({ text: '门开了。' })), false)
  assert.equal(sceneDraftChanged(draft({ text: '门开了。', heading: '新的场次标题' })), true)
  assert.equal(sceneDraftChanged(draft()), true)
})

test('removing the final saved/cancelled draft removes the persisted cache', () => {
  const saved = storage()
  const drafts = updateSceneDraft({}, 'episode-1', 'scene-1', draft())
  persistSceneDrafts(drafts, saved)
  assert.ok(saved.getItem())
  persistSceneDrafts(updateSceneDraft(drafts, 'episode-1', 'scene-1', null), saved)
  assert.equal(saved.getItem(), null)
})

test('unavailable or partial storage reports persistence failure without discarding live edits', () => {
  const unavailable = { getItem: () => { throw new Error('storage unavailable') }, setItem: () => { throw new Error('quota exceeded') }, removeItem: () => { throw new Error('storage unavailable') } }
  assert.deepEqual(loadSceneDrafts(unavailable), {})
  assert.deepEqual(loadSceneDrafts(storage('{broken')), {})
  const partial = storage(JSON.stringify({ invalid: null, 'episode-1': { bad: { text: 'incomplete' }, valid: draft() } }))
  assert.deepEqual(loadSceneDrafts(partial), { 'episode-1': { valid: draft() } })
  const drafts = updateSceneDraft({}, 'episode-1', 'scene-1', draft())
  assert.equal(persistSceneDrafts(drafts, unavailable), false)
  assert.equal(drafts['episode-1']['scene-1'].text, '我回来了。')
})

test('legacy tab drafts migrate once to persistent storage and survive a fresh browser session', () => {
  const priorLocal = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  const priorSession = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage')
  const drafts = updateSceneDraft({}, 'episode-1', 'scene-1', draft())
  const local = storage()
  const session = storage(JSON.stringify(drafts))
  try {
    Object.defineProperty(globalThis, 'localStorage', { value: local, configurable: true })
    Object.defineProperty(globalThis, 'sessionStorage', { value: session, configurable: true })
    assert.deepEqual(loadSceneDrafts(), drafts)
    assert.equal(persistSceneDrafts(loadSceneDrafts()), true)
    assert.equal(session.getItem(), null)
    Object.defineProperty(globalThis, 'sessionStorage', { value: storage(), configurable: true })
    assert.deepEqual(loadSceneDrafts(), drafts)
    assert.equal(persistSceneDrafts({}), true)
    assert.deepEqual(loadSceneDrafts(), {})
  } finally {
    if (priorLocal) Object.defineProperty(globalThis, 'localStorage', priorLocal); else delete globalThis.localStorage
    if (priorSession) Object.defineProperty(globalThis, 'sessionStorage', priorSession); else delete globalThis.sessionStorage
  }
})
