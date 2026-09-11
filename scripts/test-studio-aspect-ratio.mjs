import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../frontend/features/short-drama/studio-aspect-ratio.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { studioAspectRatio, defaultStudioExportSize, studioSourceExportSize, studioExportDimensions } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)

test('all supported fixed H3 ratios preserve the composition in preview and export', () => {
  for (const [ratio, expected] of [
    ['21:9', '1680x720'], ['16:9', '1280x720'], ['4:3', '960x720'],
    ['1:1', '1080x1080'], ['3:4', '720x960'], ['9:16', '720x1280'],
  ]) {
    assert.equal(defaultStudioExportSize(ratio), expected)
    const [width, height] = expected.split('x').map(Number)
    assert.ok(Math.abs(width / height - studioAspectRatio(ratio)) < .0001)
    assert.equal(width % 2, 0)
    assert.equal(height % 2, 0)
  }
})

test('positive custom and decimal ratios are interpreted rather than replaced with widescreen', () => {
  assert.equal(studioAspectRatio(' 3 : 2 '), 1.5)
  assert.equal(defaultStudioExportSize('3:2'), '1080x720')
  assert.equal(studioAspectRatio('1.85:1'), 1.85)
  const [width, height] = defaultStudioExportSize('1.85:1').split('x').map(Number)
  assert.ok(Math.abs(width / height / 1.85 - 1) < .003)
})

test('missing or invalid legacy ratio keeps the existing widescreen fallback', () => {
  for (const ratio of [null, undefined, '', 'adaptive', '16:0', '-4:3', 'x:3', 'Infinity:1', '1:2:3']) {
    assert.equal(studioAspectRatio(ratio), 16 / 9)
    assert.equal(defaultStudioExportSize(ratio), '1280x720')
  }
})

function asset(id, width, height, extra = {}) {
  return { id, type: 'video', path: `/media/${id}.mp4`, width, height, ...extra }
}
function clip(id, extra = {}) {
  return { id, assetId: id, type: 'video', duration: 3, trackIndex: 0, opacity: 100, ...extra }
}
function timeline(clips, tracks = [{ enabled: true }]) { return { clips, tracks } }

test('automatic request omits both dimensions while explicit choices remain exact', () => {
  const request = { expected_revision: 8, ...studioExportDimensions('source'), fps: 24, subtitles: true }
  assert.deepEqual(JSON.parse(JSON.stringify(request)), { expected_revision: 8, fps: 24, subtitles: true })
  for (const size of ['1280x720', '720x1280', '1920x1080', '1080x1920', '1080x1080', '2560x1440', '4552x2560']) {
    const [width, height] = size.split('x').map(Number)
    assert.deepEqual(studioExportDimensions(size), { width, height })
  }
  for (const size of ['', '2560', '0x1440', '1920xNaN', '1920x255']) assert.throws(() => studioExportDimensions(size), /有效的导出尺寸/)
})

test('adopted 2K dimensions are retained and unused library videos or image overlays do not enlarge export', () => {
  const assets = [asset('adopted', 1440, 2560), asset('unused', 3840, 2160), asset('overlay', 5000, 5000, { type: 'image' })]
  assert.deepEqual(studioSourceExportSize('9:16', timeline([clip('adopted'), clip('overlay', { type: 'image' })]), assets),
    { size: '1440x2560', basis: 'video' })
  assert.deepEqual(studioSourceExportSize('16:9', timeline([clip('unused')]), assets),
    { size: '3840x2160', basis: 'video' })
})

test('per-use take dimensions follow the renderer rather than the asset active take or embedded stale snapshot', () => {
  const source = asset('source', 1280, 720, { activeTakeIndex: 0, takes: [
    { path: '/media/portrait.mp4', width: 1440, height: 2560 },
    { path: '/media/wide.mp4', width: 2560, height: 1440 },
  ] })
  const original = structuredClone(source)
  assert.equal(studioSourceExportSize('16:9', timeline([clip('source', { takeIndex: 1, asset: asset('stale', 7680, 4320) })]), [source]).size, '2560x1440')
  assert.equal(studioSourceExportSize('16:9', timeline([clip('source')]), [source]).size, '1280x720')
  assert.deepEqual(source, original)
})

test('only enabled, solo-compatible visible video clips contribute dimensions', () => {
  const assets = [asset('visible', 1280, 720), asset('large', 3840, 2160)]
  const tracks = [{ enabled: true, solo: true }, { enabled: true }, { enabled: false, solo: true }]
  const clips = [clip('visible'), clip('large', { trackIndex: 1 }), clip('large', { trackIndex: 2 }),
    clip('large', { opacity: 0 }), clip('large', { duration: 0 })]
  assert.equal(studioSourceExportSize('16:9', timeline(clips, tracks), assets).size, '1280x720')
})

test('unknown adopted source metadata is not replaced with another take or a 720p guess', () => {
  const source = asset('source', 1280, 720, { takes: [{ path: '/media/unknown.mp4' }] })
  assert.deepEqual(studioSourceExportSize('16:9', timeline([clip('source', { takeIndex: 0 })]), [source]),
    { size: null, basis: 'unknown' })
  assert.deepEqual(studioSourceExportSize('16:9', timeline([clip('missing')]), [source]),
    { size: null, basis: 'unknown' })
  assert.deepEqual(studioSourceExportSize('16:9', timeline([clip('source')]), [asset('source', NaN, 720)]),
    { size: null, basis: 'unknown' })
})

test('canvas contains every used source, follows the project ratio, and never silently clamps large material', () => {
  const assets = [asset('wide', 2560, 1440), asset('portrait', 1440, 2560)]
  const current = timeline([clip('wide'), clip('portrait')])
  assert.equal(studioSourceExportSize('1:1', current, assets).size, '2560x2560')
  assert.equal(studioSourceExportSize('16:9', current, assets).size, '4552x2560')
  assert.equal(studioSourceExportSize('21:9', timeline([clip('wide')]), [asset('wide', 1792, 768)]).size, '1792x768')
  const [width, height] = studioSourceExportSize('16:9', timeline([clip('odd')]), [asset('odd', 1921, 1080)]).size.split('x').map(Number)
  assert.ok(width >= 1921 && height >= 1080 && width % 2 === 0 && height % 2 === 0)
})

test('a timeline without visible videos uses only the existing project-ratio fallback', () => {
  assert.deepEqual(studioSourceExportSize('9:16', timeline([clip('image', { type: 'image' })]), [asset('image', 5000, 5000)]),
    { size: '720x1280', basis: 'fallback' })
  assert.deepEqual(studioSourceExportSize('1:1', null, []), { size: '1080x1080', basis: 'fallback' })
})
