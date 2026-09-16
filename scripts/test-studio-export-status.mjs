import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../frontend/features/short-drama/studio-export-status.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { isStudioExportActive, mergeStudioExports, studioExportRetryRequest, mergeProjectExports, exportRecordsFromEvents } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)

function record(extra = {}) {
  return { id: 'export-1', project_id: 'project-1', status: 'running', progress: '正在合成', created_at: '2026-09-15T12:00:00Z', updated_at: '2026-09-15T12:00:01Z',
    editorial_revision: 7, width: 720, height: 1280, fps: 30, subtitles: false, ...extra }
}

test('reopening restores the latest task including failed and cancelled outcomes', () => {
  const rows = mergeStudioExports([], [record({ status: 'completed' }), record({ id: 'export-2', status: 'failed', created_at: '2026-09-15T13:00:00Z' })])
  assert.equal(rows[0].id, 'export-2')
  assert.equal(rows[0].status, 'failed')
  assert.equal(isStudioExportActive(rows[0]), false)
  assert.equal(isStudioExportActive(record({ status: 'cancelling' })), true)
  assert.equal(isStudioExportActive(record({ status: 'cancelled' })), false)
})

test('stale snapshots cannot roll cancellation or completion back to running', () => {
  const cancelling = record({ status: 'cancelling' })
  assert.equal(mergeStudioExports([cancelling], [record()])[0].status, 'cancelling')
  const completed = record({ status: 'completed', updated_at: '2026-09-15T12:01:00Z' })
  assert.equal(mergeStudioExports([completed], [record({ status: 'cancelling' })])[0].status, 'completed')
  assert.equal(mergeStudioExports([record({ progress: '最后合成', updated_at: '2026-09-15T12:00:50Z' })], [record()])[0].progress, '最后合成')
})

test('retry keeps output settings but targets the current saved editorial revision', () => {
  assert.deepEqual(studioExportRetryRequest(record(), 42), { expected_revision: 42, width: 720, height: 1280, fps: 30, subtitles: false })
  assert.equal(studioExportRetryRequest(record({ subtitles: undefined }), 43).subtitles, true)
})

test('an older active task stays visible when many newer exports have finished', () => {
  const current = record({ id: 'older-active', created_at: '2026-09-14T00:00:00Z' })
  const newer = Array.from({ length: 25 }, (_, index) => record({ id: `completed-${index}`, status: 'completed', created_at: `2026-09-15T12:${String(index).padStart(2, '0')}:00Z` }))
  const result = mergeStudioExports([current], newer)
  assert.equal(result.length, 21)
  assert.equal(result.at(-1).id, 'older-active')
})

test('export progress changes only export records and preserves current content object identities', () => {
  const project = { document: { project: { id: 'project-1' }, shots: [{ id: 'shot-1', visual: '刚编辑的画面' }],
    editorial_timeline: { revision: 9 }, studio_exports: [record()] }, scene_drafts: { text: '正在输入' } }
  const updated = mergeProjectExports(project, [record({ progress: '编码 80%', updated_at: '2026-09-15T12:00:50Z' })])
  assert.notEqual(updated, project)
  assert.equal(updated.document.shots, project.document.shots)
  assert.equal(updated.document.editorial_timeline, project.document.editorial_timeline)
  assert.equal(updated.scene_drafts, project.scene_drafts)
  assert.equal(updated.document.studio_exports[0].progress, '编码 80%')
  assert.equal(mergeProjectExports(updated, updated.document.studio_exports), updated)
  assert.equal(mergeProjectExports(updated, [record({ project_id: 'another-episode' })]), updated)
})

test('small export events preserve terminal outcomes across later old snapshots and keep chronological film order', () => {
  const complete = record({ status: 'completed', output_path: '/film.mp4', updated_at: '2026-09-15T12:01:00Z' })
  const events = [{ kind: 'export.updated', data: complete }, { kind: 'project.updated', data: record() },
    { kind: 'export.updated', data: null }, { kind: 'export.updated', data: { id: 'incomplete' } }]
  assert.deepEqual(exportRecordsFromEvents(events), [complete])
  const project = { document: { project: { id: 'project-1' }, studio_exports: [] } }
  const current = mergeProjectExports(project, exportRecordsFromEvents(events))
  const next = mergeProjectExports(current, [record(), record({ id: 'export-2', created_at: '2026-09-15T13:00:00Z' })])
  assert.equal(next.document.studio_exports[0].status, 'completed')
  assert.equal(next.document.studio_exports.at(-1).id, 'export-2')
})

test('project record updates retain the last successful export beyond the status panel page', () => {
  const success = record({ id: 'last-success', status: 'completed', created_at: '2026-09-14T00:00:00Z' })
  const failed = Array.from({ length: 25 }, (_, index) => record({ id: `failed-${index}`, status: 'failed', created_at: `2026-09-15T12:${String(index).padStart(2, '0')}:00Z` }))
  const project = { document: { project: { id: 'project-1' }, studio_exports: [success, ...failed] } }
  const next = mergeProjectExports(project, [record({ id: 'failed-24', status: 'failed', created_at: failed[24].created_at, updated_at: '2026-09-15T12:25:00Z' })])
  assert.equal(next.document.studio_exports.length, 26)
  assert.equal(next.document.studio_exports.find(row => row.status === 'completed'), success)
  assert.equal(mergeStudioExports([], next.document.studio_exports).length, 20)
})
