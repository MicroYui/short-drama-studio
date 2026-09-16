import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../frontend/features/short-drama/studio-activity-refresh.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { createStudioActivityChannel, refreshStudioActivity, studioEventsRefreshProject } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }

test('a successful activity read restores connection after a backend interruption', async () => {
  const channel = createStudioActivityChannel('episode-a'), connections = [], applied = []
  const options = { isCurrent: () => true, apply: value => applied.push(value), connection: value => connections.push(value) }
  await assert.rejects(refreshStudioActivity(channel, { ...options, read: async () => { throw new Error('backend restarting') } }), /restarting/)
  await refreshStudioActivity(channel, { ...options, read: async () => ({ events: [{ kind: 'content.updated' }] }) })
  assert.deepEqual(connections, [false, true])
  assert.equal(studioEventsRefreshProject(applied[0].events), true)
})

test('returning to the same episode gets its own request while the older response is ignored', async () => {
  const old = createStudioActivityChannel('a'), latest = createStudioActivityChannel('a')
  let current = old
  const oldRead = deferred(), newRead = deferred(), applied = [], connections = []
  const oldPending = refreshStudioActivity(old, { isCurrent: () => current === old, read: () => oldRead.promise,
    apply: value => applied.push(value), connection: value => connections.push(value) })
  current = createStudioActivityChannel('b')
  current = latest
  const newPending = refreshStudioActivity(latest, { isCurrent: () => current === latest, read: () => newRead.promise,
    apply: value => applied.push(value), connection: value => connections.push(value) })
  newRead.resolve('latest a')
  await newPending
  oldRead.reject(new Error('old a disconnected'))
  await oldPending
  assert.deepEqual(applied, ['latest a'])
  assert.deepEqual(connections, [true])
})

test('SSE replay and polling coalesce into one active request and one trailing refresh', async () => {
  const channel = createStudioActivityChannel('a'), first = deferred(), applied = []
  let requests = 0
  const options = { isCurrent: () => true, read: async () => ++requests === 1 ? first.promise : 'trailing',
    apply: value => applied.push(value), connection: () => {} }
  const pending = refreshStudioActivity(channel, options)
  assert.equal(refreshStudioActivity(channel, options), pending)
  assert.equal(refreshStudioActivity(channel, options), pending)
  first.resolve('first')
  await pending
  assert.equal(requests, 2)
  assert.deepEqual(applied, ['first', 'trailing'])
})

test('content requires project refresh; export progress and terminal events use separate records', () => {
  for (const kind of ['content.updated', 'assets.updated', 'workflow.updated', 'change.proposed', 'change.decided']) {
    assert.equal(studioEventsRefreshProject([{ kind }]), true, kind)
  }
  assert.equal(studioEventsRefreshProject([{ kind: 'job.updated' }, { kind: 'worker.updated' }]), false)
  assert.equal(studioEventsRefreshProject([]), false)
  assert.equal(studioEventsRefreshProject([{ kind: 'export.updated' }]), false)
})
