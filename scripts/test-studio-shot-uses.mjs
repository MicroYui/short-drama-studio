import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { test } from 'node:test'

const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
async function module(path) {
  const result = await build({ entryPoints: [path], bundle: true, platform: 'node', format: 'esm', write: false })
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`)
}
const { editorialShotUses } = await module('frontend/features/short-drama/studio-shot-uses.ts')
const { shotMedia, currentEditorial } = await module('frontend/features/short-drama/studio-content.ts')
const { projectSchema } = await module('frontend/types/project-model.ts')
const { createInitialEditorState } = await module('frontend/views/editor/editor-state.ts')
const { splitClipsAtTime, resizeClip } = await module('frontend/views/editor/editor-actions.ts')
const fixture = JSON.parse(readFileSync(new URL('../backend/tests/fixtures/studio-whole-group-uses.json', import.meta.url), 'utf8'))

// Complete ordinary editor presentation fields around the same backend media/ownership fixture.
function editorOf(item) {
  return { version: 2, id: item.name, name: item.name, createdAt: 0, updatedAt: 0, bins: {},
    assets: item.assets.map(asset => ({ name: asset.id, type: 'video', resolution: '', ...asset })),
    timelines: [{ name: 'Main', createdAt: 0, subtitles: [], ...item.timeline,
      tracks: (item.timeline.tracks ?? []).map(track => ({ muted: false, locked: false, ...track })),
      clips: item.timeline.clips.map(clip => ({ asset: null, trimEnd: 0, trackIndex: 0, speed: 1, reversed: false, ...clip })) }],
    activeTimelineId: item.timeline.id }
}

for (const item of fixture.cases) {
  test(`${item.name}: shared backend ranges drive duration, occurrences and editorial preview`, () => {
    const editor = editorOf(item)
    const project = { document: { project: {}, manifests: [], editorial_timeline: { revision: 9, project: editor } }, takes: item.takes }
    const before = structuredClone(project)
    assert.ok(currentEditorial(project), 'fixture must exercise the real editor schema and currentEditorial path')
    for (const [shotId, expected] of Object.entries(item.expected)) {
      const actual = editorialShotUses(editor, item.takes, shotId).map(use => ({
        anchor_clip_id: use.clipIds[0], start_seconds: use.start, duration_seconds: use.end - use.start,
      }))
      assert.equal(actual.length, expected.length, `${shotId}: occurrence count`)
      actual.forEach((use, index) => {
        assert.equal(use.anchor_clip_id, expected[index].anchor_clip_id)
        assert.ok(Math.abs(use.start_seconds - expected[index].start_seconds) < 1e-8, `${shotId}: film start`)
        assert.ok(Math.abs(use.duration_seconds - expected[index].duration_seconds) < 1e-8, `${shotId}: film duration`)
      })
      const media = shotMedia(project, { id: shotId, duration_seconds: 5 })
      assert.equal(media.hasEditorial, expected.length > 0, `${shotId}: actual local player instead of raw Take`)
      assert.equal(media.occurrenceCount, expected.length)
      if (expected.length) assert.ok(Math.abs(media.adoptedDuration - expected[0].duration_seconds) < 1e-8)
      else assert.equal(media.adoptedDuration, null)
    }
    assert.deepEqual(project, before, 'read projection must not rewrite clips, Take selections or windows')
  })
}

function trimmedGroup() {
  const item = structuredClone(fixture.cases.find(item => item.name === 'daily_umbrella_trimmed_declared'))
  assert.ok(item)
  const editor = projectSchema.parse(editorOf(item))
  const clip = editor.timelines[0].clips.find(clip => clip.sourceShotIds)
  assert.ok(clip, 'the real project parser must retain the declared relation')
  return { editor, clip, takes: item.takes, take: item.takes.find(take => take.id === clip.sourceTakeId) }
}

function assertRange(editor, takes, shotId, start, end) {
  const uses = editorialShotUses(editor, takes, shotId)
  assert.equal(uses.length, 1, shotId)
  assert.ok(Math.abs(uses[0].start - start) < 1e-8, `${shotId}: start`)
  assert.ok(Math.abs(uses[0].end - end) < 1e-8, `${shotId}: end`)
  return uses[0]
}

test('declared trimmed group survives actual film split, trim and project roundtrip', () => {
  const { editor, clip, takes } = trimmedGroup()
  const before = structuredClone(editor)
  const split = splitClipsAtTime(createInitialEditorState(editor), [clip.id], 17)
  const parts = split.editorModel.timelines[0].clips.filter(item => item.sourceTakeId === clip.sourceTakeId && item.type === 'video')
  assert.equal(parts.length, 2)
  for (const part of parts) assert.deepEqual(part.sourceShotIds, clip.sourceShotIds)
  const splitProject = projectSchema.parse({ ...editor, ...split.editorModel })
  assert.equal(assertRange(splitProject, takes, 'shot-umbrella-05', 14.715, 20.215).clipIds.length, 2)
  assertRange(splitProject, takes, 'shot-umbrella-06', 20.215, 24.795)
  const right = parts.find(item => item.id !== clip.id)
  assert.ok(right)
  const trimmed = resizeClip(split, { clipId: right.id, edge: 'end', deltaTime: -1 })
  const saved = projectSchema.parse(JSON.parse(JSON.stringify({ ...editor, ...trimmed.editorModel })))
  for (const part of saved.timelines[0].clips.filter(item => item.type === 'video' && item.sourceTakeId === clip.sourceTakeId)) {
    assert.deepEqual(part.sourceShotIds, clip.sourceShotIds)
  }
  assertRange(saved, takes, 'shot-umbrella-04', 14.59, 14.715)
  assertRange(saved, takes, 'shot-umbrella-05', 14.715, 20.215)
  assertRange(saved, takes, 'shot-umbrella-06', 20.215, 23.795)
  assert.deepEqual(saved.timelines[0].clips.filter(item => item.sourceTakeId !== clip.sourceTakeId || item.type !== 'video'),
    before.timelines[0].clips.filter(item => item.sourceTakeId !== clip.sourceTakeId || item.type !== 'video'))
  assert.deepEqual(editor, before, 'editor actions must not mutate the input project')
})

test('either materialized single-shot boundary overrides a multi-shot declaration', () => {
  for (const boundary of [{ sourceWindowStart: 4.875 }, { sourceWindowEnd: 15.08 }, { sourceWindowStart: 4.875, sourceWindowEnd: 15.08 }]) {
    const { editor, clip, takes } = trimmedGroup()
    Object.assign(clip, boundary)
    assertRange(editor, takes, 'shot-umbrella-04', 14.59, 24.795)
    for (const id of ['shot-umbrella-05', 'shot-umbrella-06']) assert.deepEqual(editorialShotUses(editor, takes, id), [])
  }
})

test('declared subset keeps its saved windows despite mutable Take selection and preserves other occurrences', () => {
  const { editor, clip, takes, take } = trimmedGroup()
  clip.sourceShotIds = ['shot-umbrella-04', 'shot-umbrella-05']
  take.selected_shot_ids = ['shot-umbrella-04']
  take.is_selected = false
  assertRange(editor, takes, 'shot-umbrella-05', 14.715, 20.215)
  assert.deepEqual(editorialShotUses(editor, takes, 'shot-umbrella-06'), [])
  const separate = { ...clip, id: 'separate-shot-05', sourceShotId: 'shot-umbrella-05', startTime: 40, duration: 1, trimStart: 5 }
  delete separate.sourceShotIds
  editor.timelines[0].clips.push(separate)
  const uses = editorialShotUses(editor, takes, 'shot-umbrella-05')
  assert.equal(uses.length, 2)
  assert.ok(Math.abs(uses[0].end - 20.215) < 1e-8)
  assert.deepEqual(uses[1], { start: 40, end: 41, clipIds: ['separate-shot-05'] })
})

test('declared source intersections map speed and reverse into film coordinates', () => {
  const { editor, clip, takes } = trimmedGroup()
  clip.speed = 2
  clip.duration /= 2
  clip.reversed = true
  assertRange(editor, takes, 'shot-umbrella-06', 14.59, 16.88)
  assertRange(editor, takes, 'shot-umbrella-05', 16.88, 19.63)
  assertRange(editor, takes, 'shot-umbrella-04', 19.63, 19.6925)
})

test('invalid declared relations remain readable as direct ownership instead of inventing other shot uses', () => {
  const invalid = [
    ({ clip }) => { clip.sourceShotIds = ['shot-umbrella-04', 'shot-umbrella-04'] },
    ({ clip }) => { clip.sourceShotIds = ['shot-umbrella-04', ' '] },
    ({ clip }) => { clip.sourceShotIds = ['shot-umbrella-05', 'shot-umbrella-06'] },
    ({ clip }) => { clip.sourceShotIds = ['shot-umbrella-04', 'unknown-shot'] },
    ({ editor, clip, take }) => {
      const asset = editor.assets.find(item => item.id === clip.assetId)
      asset.takes = [{ path: '/fixture/wrong-video.mp4' }, { path: take.media_path }]
      clip.takeIndex = 0
    },
    ({ take }) => { take.shot_windows[1].source_in_seconds = 4.9; take.shot_windows[1].duration_seconds = 5.6 },
    ({ take }) => { take.shot_windows[1].duration_seconds = 99 },
    ({ clip }) => { clip.duration += 0.05 },
    ({ clip }) => { clip.sourceShotIds = ['shot-umbrella-04', 'shot-umbrella-05']; clip.trimStart = 11; clip.duration = 1 },
  ]
  for (const mutate of invalid) {
    const context = trimmedGroup()
    mutate(context)
    const { editor, clip, takes } = context
    assertRange(editor, takes, 'shot-umbrella-04', clip.startTime, clip.startTime + clip.duration)
    for (const id of ['shot-umbrella-05', 'shot-umbrella-06']) assert.deepEqual(editorialShotUses(editor, takes, id), [])
  }
  const { editor, clip, takes, take } = trimmedGroup()
  const asset = editor.assets.find(item => item.id === clip.assetId)
  asset.path = '/fixture/other-active-video.mp4'
  asset.takes = [{ path: '/fixture/wrong-video.mp4' }, { path: take.media_path }]
  clip.takeIndex = 1
  assertRange(editor, takes, 'shot-umbrella-05', 14.715, 20.215)
})
