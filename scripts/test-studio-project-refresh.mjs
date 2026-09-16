import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
const result = await build({entryPoints:['frontend/features/short-drama/studio-project-refresh.ts'],bundle:true,write:false,platform:'node',format:'esm'})
const { receiveProjectSnapshot } = await import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`)
function project(time, scenes, revision = 1) { return { document: { project: { id:'project-1',updated_at:time }, scenes,editorial_timeline:{revision},studio_exports:[] } } }
test('a late first-scene save cannot replace the completed second-scene save', () => {
  const initial = project('2026-09-16T03:00:00Z',{first:'原文1',second:'原文2'})
  const slow = project('2026-09-16T03:00:01Z',{first:'改后1',second:'原文2'})
  const fast = project('2026-09-16T03:00:02Z',{first:'改后1',second:'改后2'})
  const newest = receiveProjectSnapshot(initial, fast)
  assert.equal(receiveProjectSnapshot(newest, slow), newest)
  assert.deepEqual(newest.document.scenes,{first:'改后1',second:'改后2'})
})
test('newer editorial revisions stay current across old aggregate timestamps', () => {
  const current = project('2026-09-16T03:00:01Z',{first:'最新'},7)
  const old = project('2026-09-16T03:00:01Z',{first:'旧内容'},6)
  assert.equal(receiveProjectSnapshot(current, old),current)
  const next = project('2026-09-16T03:00:02Z',{first:'又更新'},8)
  assert.equal(receiveProjectSnapshot(current,next),next)
})
test('an old content response may still contribute an independent completed export', () => {
  const current = project('2026-09-16T03:00:02Z',{first:'最新'})
  const old = project('2026-09-16T03:00:01Z',{first:'旧内容'})
  old.document.studio_exports.push({id:'export-1',project_id:'project-1',status:'completed',created_at:'2026-09-16T03:00:01Z'})
  const merged=receiveProjectSnapshot(current,old)
  assert.equal(merged.document.scenes,current.document.scenes)
  assert.equal(merged.document.studio_exports[0].id,'export-1')
})
