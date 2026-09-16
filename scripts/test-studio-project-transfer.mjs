import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../frontend/features/short-drama/studio-project-transfer.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { createProjectTransferApi, mergeProjectTransfers, readActiveProjectTransfers, projectTransferProgress, openProjectPackage } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
const row = (extra = {}) => ({ id: 'transfer-1', operation: 'collect', project_id: 'project-1', project_name: '原项目', status: 'running', stage: 'media', message: '复制媒体',
  processed_files: 1, total_files: 4, processed_bytes: 200, total_bytes: 1000, cancellable: true,
  created_at: '2026-09-16T12:00:00.000000+00:00', updated_at: '2026-09-16T12:00:01.000100+00:00', ...extra })
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }

test('an old pending GET cannot erase a later cancellation acknowledgement or finished package', async () => {
  const pending = deferred()
  let current = [row()]
  const reading = readActiveProjectTransfers(current, () => pending.promise).then(result => { current = mergeProjectTransfers(current, result.transfers) })
  current = mergeProjectTransfers(current, [row({ status: 'cancelling', cancellable: false, updated_at: '2026-09-16T12:00:02Z' })])
  pending.resolve(row())
  await reading
  assert.equal(current[0].status, 'cancelling')
  const result = { path: '/saved/project.zip', filename: 'project.zip', download_url: '/download' }
  current = mergeProjectTransfers(current, [row({ status: 'completed', stage: 'completed', package: result, updated_at: '2026-09-16T12:00:03Z' })])
  current = mergeProjectTransfers(current, [row({ status: 'running', updated_at: '2026-09-16T12:00:04Z' })])
  assert.equal(current[0].package, result)
  assert.equal(current[0].status, 'completed')
})

test('fresh stage counters can reset while microsecond-old snapshots cannot roll them back', () => {
  const before = row({ stage: 'trace', message: '第一集制作记录', processed_bytes: 3000 })
  const next = row({ stage: 'trace', message: '第二集制作记录', processed_files: 0, processed_bytes: 0, updated_at: '2026-09-16T12:00:01.000200+00:00' })
  const current = mergeProjectTransfers([before], [next])
  assert.equal(current[0], next)
  assert.equal(mergeProjectTransfers(current, [before])[0], next)
  assert.equal(mergeProjectTransfers([row({ processed_bytes: 400 })], [row()])[0].processed_bytes, 400)
})

test('history pages with shared timestamps remain distinct, sorted, and retain older active jobs', () => {
  const old = row({ id: 'old-active', created_at: '2026-09-15T00:00:00Z' })
  const recent = Array.from({ length: 25 }, (_, index) => row({ id: `recent-${String(index).padStart(2, '0')}`, status: 'completed' }))
  const first = mergeProjectTransfers([old], recent.slice(5))
  const next = mergeProjectTransfers(first, [recent[5], ...recent.slice(0, 5)])
  assert.equal(next.length, 26)
  assert.equal(next[0].id, 'recent-24')
  assert.equal(next.at(-1), old)
  assert.equal(new Set(next.map(record => record.id)).size, 26)
})

test('one failed GET keeps the other completion; terminal rows are no longer polled', async () => {
  const current = [row(), row({ id: 'failed-get' }), row({ id: 'finished', status: 'completed' })]
  const called = []
  const result = await readActiveProjectTransfers(current, async id => {
    called.push(id)
    if (id === 'failed-get') throw new Error('temporary connection failure')
    return row({ status: 'completed', imported: { project_id: 'new-project' } })
  })
  assert.deepEqual(called, ['transfer-1', 'failed-get'])
  assert.deepEqual(result.failedIds, ['failed-get'])
  assert.equal(mergeProjectTransfers(current, result.transfers).find(record => record.id === 'transfer-1').imported.project_id, 'new-project')
})

test('progress uses actual bytes when a single large file is still being copied, and leaves unknown totals indeterminate', () => {
  assert.equal(projectTransferProgress(row({ processed_files: 0, total_files: 1, processed_bytes: 300, total_bytes: 1000 })).ratio, .3)
  const unknown = projectTransferProgress(row({ total_files: null, total_bytes: null }))
  assert.equal(unknown.ratio, undefined)
  assert.equal(unknown.bytes, '已处理 200 B')
  assert.equal(projectTransferProgress(row({ total_bytes: 0, total_files: 0 })).ratio, undefined)
  assert.equal(projectTransferProgress(row({ total_bytes: null, total_files: 4, processed_files: 2 })).ratio, .5)
})

test('desktop reveals the existing package without downloading or reading its blob; web preserves download', async () => {
  const result = { path: '/saved/773MB.zip', filename: '作品.zip', download_url: '/download/zip' }
  const called = []
  await openProjectPackage(result, { reveal: async path => { called.push(['reveal', path]) }, download: async () => { throw new Error('Unexpected full ZIP download') } })
  assert.deepEqual(called, [['reveal', result.path]])
  await openProjectPackage(result, { download: async (url, filename) => { called.push(['download', url, filename]) } })
  assert.deepEqual(called[1], ['download', result.download_url, result.filename])
})

test('failed native file reveal reports the problem without silently falling back to a huge download', async () => {
  await assert.rejects(openProjectPackage({ path: '/missing.zip' }, { reveal: async () => { throw new Error('file missing') }, download: async () => { throw new Error('wrong fallback') } }), /file missing/)
})

test('client uses only background job routes, multipart original file and opaque pagination cursors', async () => {
  const calls = []
  const api = createProjectTransferApi(async (path, init) => { calls.push({ path, init }); return row() })
  const file = new File(['local zip bytes'], '原片.zip', { type: 'application/zip' })
  await api.collect('project/一')
  await api.import(file)
  const signal = new AbortController().signal
  await api.list('cursor+/=', signal)
  await api.get('transfer/一', signal)
  await api.cancel('transfer/一')
  assert.equal(calls[0].path, '/api/short-drama/studio/projects/project%2F%E4%B8%80/package-jobs')
  assert.equal(calls[1].path, '/api/short-drama/studio/projects/import-package-jobs')
  assert.equal(calls[1].init.body.get('file').name, file.name)
  assert.equal(await calls[1].init.body.get('file').text(), await file.text())
  assert.equal(calls[2].path, '/api/short-drama/studio/project-transfers?limit=20&before=cursor%2B%2F%3D')
  assert.equal(calls[2].init.signal, signal)
  assert.equal(calls[3].init.signal, signal)
  assert.equal(calls[4].path, '/api/short-drama/studio/project-transfers/transfer%2F%E4%B8%80/cancel')
  assert.equal(calls[4].init.method, 'POST')
})
