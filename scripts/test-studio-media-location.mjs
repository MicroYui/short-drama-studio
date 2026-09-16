import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../frontend/features/short-drama/studio-media-location.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { mediaUseLocation } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)

function use(extra = {}) {
  return { episode_id: 'episode-1', episode_number: 1, episode_title: '第一集', kind: 'editorial',
    target_id: 'clip-first', shot_id: 'shot-repeated', timeline_id: 'timeline-main',
    start_seconds: 0, end_seconds: 4, label: '主时间线 · 画面', ...extra }
}

test('repeated uses of one shot preserve distinct clips and playhead positions', () => {
  const first = use()
  const later = use({ target_id: 'clip-later', start_seconds: 28.75, end_seconds: 32.75 })
  assert.deepEqual(mediaUseLocation(first), { episodeId: 'episode-1', shotId: 'shot-repeated',
    timelineId: 'timeline-main', clipId: 'clip-first', startSeconds: 0 })
  assert.deepEqual(mediaUseLocation(later), { episodeId: 'episode-1', shotId: 'shot-repeated',
    timelineId: 'timeline-main', clipId: 'clip-later', startSeconds: 28.75 })
})

test('another episode and inactive timeline remain the destination', () => {
  const input = use({ episode_id: 'episode-2', timeline_id: 'timeline-alt', target_id: 'clip-alt', start_seconds: 13.4 })
  const before = structuredClone(input)
  assert.deepEqual(mediaUseLocation(input), { episodeId: 'episode-2', shotId: 'shot-repeated',
    timelineId: 'timeline-alt', clipId: 'clip-alt', startSeconds: 13.4 })
  assert.deepEqual(input, before)
})

test('audio and imported media without a shot still locate their exact saved clip', () => {
  const input = use({ shot_id: null, target_id: 'clip-music', label: '主时间线 · 配乐', start_seconds: 1.25 })
  assert.deepEqual(mediaUseLocation(input), { episodeId: 'episode-1', shotId: null,
    timelineId: 'timeline-main', clipId: 'clip-music', startSeconds: 1.25 })
})

test('a storyboard reference opens its shot instead of misinterpreting its target as a clip', () => {
  assert.deepEqual(mediaUseLocation(use({ kind: 'shot', target_id: 'shot-reference', shot_id: null,
    timeline_id: null, start_seconds: null })), { episodeId: 'episode-1', shotId: 'shot-reference' })
})

test('character voice references do not pretend to be timeline uses', () => {
  assert.equal(mediaUseLocation(use({ kind: 'voice', target_id: 'character-1', shot_id: null,
    timeline_id: null, start_seconds: null })), null)
})

test('legacy editorial results keep their clip identity without inventing a timeline or time', () => {
  const input = use({ timeline_id: undefined, start_seconds: undefined })
  assert.deepEqual(mediaUseLocation(input), { episodeId: 'episode-1', shotId: 'shot-repeated',
    timelineId: null, clipId: 'clip-first', startSeconds: null })
})
