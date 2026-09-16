import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { after, test } from 'node:test'

const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
const root = fileURLToPath(new URL('..', import.meta.url))
const temporary = mkdtempSync(join(tmpdir(), 'studio-visual-'))
const output = process.env.STUDIO_VISUAL_TEST_OUTPUT_DIR ? resolve(process.env.STUDIO_VISUAL_TEST_OUTPUT_DIR) : temporary
mkdirSync(output, { recursive: true })
const probe = spawnSync(join(root, 'backend/.venv/bin/python'), ['-c', 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())'], { encoding: 'utf8' })
const ffmpeg = process.env.FFMPEG_PATH || (probe.status === 0 ? probe.stdout.trim() : 'ffmpeg')
const measurements = []
after(() => {
  if (process.env.STUDIO_VISUAL_TEST_OUTPUT_DIR) writeFileSync(join(output, 'visual-measurements.json'), JSON.stringify(measurements, null, 2) + '\n')
  rmSync(temporary, { recursive: true, force: true })
})
const module = join(temporary, 'visual-test.cjs')
await build({ stdin: { contents: `
  export { buildStudioTitleAss, studioAssFilter } from './electron/export/studio-title';
  export { buildStudioTransitionLayers } from './electron/export/studio-transition';
  export { buildStudioVideoFilterGraph } from './electron/export/studio-video-filter';
  export { STUDIO_TITLE_STYLE } from './frontend/lib/studio-title-style';
  export { changeStudioTransition, addStudioTitle } from './frontend/features/short-drama/studio-visual-editing';
  export { createInitialEditorState } from './frontend/views/editor/editor-state';
  export { getEditorModel } from './frontend/views/editor/editor-project-bridging';
  export { projectSchema, createDefaultTimeline, DEFAULT_COLOR_CORRECTION } from './frontend/types/project-model';
  export { getClipEffectStyles } from './frontend/views/editor/video-editor-utils';
`, resolveDir: root }, bundle: true, platform: 'node', target: 'node20', format: 'cjs', outfile: module })
const { buildStudioTitleAss, studioAssFilter, buildStudioTransitionLayers, buildStudioVideoFilterGraph,
  STUDIO_TITLE_STYLE, changeStudioTransition, addStudioTitle, createInitialEditorState, getEditorModel, projectSchema, createDefaultTimeline, DEFAULT_COLOR_CORRECTION, getClipEffectStyles } = require(module)
const cli = join(temporary, 'studio-export.cjs')
await build({ entryPoints: [join(root, 'electron/export/studio-export-cli.ts')], bundle: true,
  platform: 'node', target: 'node20', format: 'cjs', outfile: cli })
const width = 320, height = 180, fps = 24
function run(args) {
  const result = spawnSync(ffmpeg, ['-hide_banner', '-loglevel', 'error', '-y', ...args], { maxBuffer: 80 * 1024 * 1024, timeout: 30000 })
  assert.equal(result.status, 0, result.error?.message || result.stderr?.toString())
  return result.stdout
}
function clip(id, path = '', start = 0, duration = 2, extra = {}) {
  return { id, assetId: id, asset: null, path, type: 'video', startTime: start, duration, trimStart: 0, trimEnd: 0,
    speed: 1, reversed: false, flipH: false, flipV: false, opacity: 100, trackIndex: 0,
    muted: true, volume: 1, transitionIn: { type: 'none', duration: 0 }, transitionOut: { type: 'none', duration: 0 }, ...extra }
}
function color(name, value) {
  const path = join(output, `${name}.mp4`)
  run(['-f', 'lavfi', '-i', `color=c=${value}:s=${width}x${height}:r=${fps}:d=3`, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', path])
  return path
}
function pixel(raw, frame, x = 10, y = 10) {
  const offset = (frame * width * height + y * width + x) * 3
  return [...raw.subarray(offset, offset + 3)]
}
function render(clips, seconds, outputFps = fps) {
  const effect = buildStudioTransitionLayers(clips, outputFps)
  const graph = buildStudioVideoFilterGraph(clips, { width, height, fps: outputFps, duration: seconds, layerEffects: effect })
  const script = join(temporary, 'filter.txt')
  writeFileSync(script, graph.filterScript)
  const raw = run([...graph.inputs, '-filter_complex_threads', '1', '-filter_complex_script', script,
    '-map', '[outv]', '-an', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'])
  assert.equal(raw.length, Math.round(seconds * outputFps) * width * height * 3)
  return raw
}

// Independent CSS filter reference over encoded RGB; actual FFmpeg output is
// decoded below. This catches a missing export field, operation order and a
// second application after partial-video replacement, not just filter strings.
function cssPixel(rgb, filter) {
  const clamp = value => Math.max(0, Math.min(255, value))
  const mix = matrix => rgb = matrix.map(row => clamp(row.reduce((sum, value, index) => sum + value * rgb[index], 0)))
  for (const match of (filter || '').matchAll(/([a-z-]+)\(([-\d.]+)(?:deg)?\)/g)) {
    const amount = Number(match[2])
    if (match[1] === 'brightness') rgb = rgb.map(value => clamp(value * amount))
    else if (match[1] === 'contrast') rgb = rgb.map(value => clamp((value - 127.5) * amount + 127.5))
    else if (match[1] === 'saturate') {
      const luminance = rgb[0] * .213 + rgb[1] * .715 + rgb[2] * .072
      rgb = rgb.map(value => clamp(luminance + (value - luminance) * amount))
    } else if (match[1] === 'sepia') {
      const sepia = [[.393,.769,.189],[.349,.686,.168],[.272,.534,.131]]
      mix(sepia.map((row, i) => row.map((value, j) => value * amount + (i === j ? 1 - amount : 0))))
    } else if (match[1] === 'hue-rotate') {
      const c = Math.cos(amount * Math.PI / 180), s = Math.sin(amount * Math.PI / 180)
      const identity = [[1,0,0],[0,1,0],[0,0,1]]
      const hue = [[-.213,-.715,.928],[.143,.140,-.283],[-.787,.715,.072]]
      mix(identity.map((row, i) => row.map((value, j) => [.213,.715,.072][j] * (1-c) + value*c + hue[i][j]*s)))
    } else assert.fail(`Unhandled CSS filter ${match[1]}`)
  }
  return rgb
}

test('all eight saved color controls render like the preview, including clipping and combined corrections', () => {
  const source = color('color-correction-source', '0x648CB4')
  const original = pixel(render([clip('source', source, 0, 0.5)], 0.5), 6)
  const cases = Object.keys(DEFAULT_COLOR_CORRECTION).flatMap(key => [-100, 100].map(value => ({ [key]: value })))
  cases.push({ brightness: 100, exposure: -100 }, { brightness: 25, contrast: 35, saturation: -30, temperature: 65, tint: -40, exposure: 15, highlights: -30, shadows: 20 })
  for (const values of cases) {
    const corrected = clip('corrected', source, 0, 0.5, { colorCorrection: { ...DEFAULT_COLOR_CORRECTION, ...values } })
    const expected = cssPixel(original, getClipEffectStyles(corrected).filter)
    const actual = pixel(render([corrected], 0.5), 6)
    assert.ok(actual.every((value, index) => Math.abs(value - expected[index]) < 10), JSON.stringify({ values, original, expected, actual }))
    measurements.push({ color_controls: values, expected_rgb: expected, exported_rgb: actual })
  }
  const opacity = clip('opacity', source, 0, 0.5, { colorCorrection: { ...DEFAULT_COLOR_CORRECTION, brightness: 40 }, opacity: 50 })
  const expected = cssPixel(original, getClipEffectStyles(opacity).filter).map(value => value * .5)
  const actual = pixel(render([opacity], 0.5), 6)
  assert.ok(actual.every((value, index) => Math.abs(value - expected[index]) < 10), JSON.stringify({ expected, actual }))
})

test('current project color values reach the actual MP4 adapter without altering adjacent footage', () => {
  const source = color('color-cli-source', '0x648CB4')
  const timeline = createDefaultTimeline()
  timeline.clips = [clip('source', source, 0, .5), clip('changed', source, .5, .5,
    { assetId: 'source', colorCorrection: { ...DEFAULT_COLOR_CORRECTION, brightness: 40, saturation: -100 } }),
    clip('after', source, 1, .5, { assetId: 'source' })]
  const project = projectSchema.parse({ version: 2, id: 'color-test', name: '调色', createdAt: 0, updatedAt: 0, bins: {}, activeTimelineId: timeline.id, timelines: [timeline],
    assets: [{ id: 'source', type: 'video', path: source, duration: 3, resolution: '320x180' }] })
  const outputPath = join(output, 'color-correction.mp4'), input = join(temporary, 'color-input.json')
  writeFileSync(input, JSON.stringify({ project, outputPath, ffmpegPath: ffmpeg, width, height, fps }))
  const result = spawnSync(process.execPath, [cli, input], { encoding: 'utf8', timeout: 30000 })
  assert.equal(result.status, 0, result.error?.message || result.stderr)
  const raw = run(['-i', outputPath, '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'])
  assert.equal(raw.length, 36 * width * height * 3)
  const before = pixel(raw, 6), changed = pixel(raw, 18), after = pixel(raw, 30)
  const expected = cssPixel(before, getClipEffectStyles(timeline.clips[1]).filter)
  assert.ok(changed.every((value, index) => Math.abs(value - expected[index]) < 10))
  assert.ok(after.every((value, index) => Math.abs(value - before[index]) < 3))
  measurements.push({ color_export: outputPath, frames: 36, before, changed, after })
})

test('dissolve edits update both adjacent ends and preserve unrelated clips', () => {
  const clips = [clip('a'), clip('b', '', 2), clip('c', '', 5)]
  const changed = changeStudioTransition(clips, 'a', 'transitionOut', { type: 'dissolve', duration: 0.4 })
  assert.deepEqual(changed[1].transitionIn, changed[0].transitionOut)
  assert.equal(changed[2], clips[2])
  const cleared = changeStudioTransition(changed, 'b', 'transitionIn', { type: 'fade-to-black', duration: 0.3 })
  assert.equal(cleared[0].transitionOut.type, 'none')
  assert.equal(cleared[1].transitionIn.type, 'fade-to-black')
  assert.equal(changeStudioTransition(clips, 'c', 'transitionOut', { type: 'dissolve', duration: 0.4 }), clips)
})

test('adding a short title preserves later footage even when the shared title starts at five seconds', () => {
  const timeline = createDefaultTimeline()
  timeline.tracks = timeline.tracks.slice(0, 1)
  timeline.clips = [clip('later', '', 4, 2)]
  const project = projectSchema.parse({ version: 2, id: 'add-title', name: 'Title', createdAt: 0, updatedAt: 0, bins: {}, assets: [], activeTimelineId: timeline.id, timelines: [timeline] })
  const state = createInitialEditorState(getEditorModel(project))
  const updated = addStudioTitle(state, 0, 3)
  assert.deepEqual(updated.editorModel.timelines[0].clips.find(clip => clip.id === 'later'), state.editorModel.timelines[0].clips[0])
  const title = updated.editorModel.timelines[0].clips.find(clip => clip.type === 'text')
  assert.equal(title.duration, 3)
  assert.equal(title.trackIndex, 1)
})

test('Chinese title has real glyphs, a transparent background and the requested position', () => {
  const style = { ...STUDIO_TITLE_STYLE, text: '借路\n第二集', fontSize: 108, positionX: 65, positionY: 35, color: '#FFFF00' }
  const ass = join(temporary, 'title.ass'), png = join(output, 'chinese-title.png')
  writeFileSync(ass, buildStudioTitleAss(style, width, height))
  run(['-f', 'lavfi', '-i', `color=c=black@0:s=${width}x${height}:r=1,format=rgba`, '-vf', studioAssFilter(ass), '-frames:v', '1', png])
  const rgba = run(['-i', png, '-frames:v', '1', '-pix_fmt', 'rgba', '-f', 'rawvideo', 'pipe:1'])
  assert.equal(rgba[3], 0, 'canvas must remain transparent')
  const points = []
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const i = (y * width + x) * 4
    if (rgba[i + 3] > 180) { assert.ok(rgba[i] > 200 && rgba[i + 1] > 200 && rgba[i + 2] < 40); points.push([x, y]) }
  }
  assert.ok(points.length > 120, `missing glyphs: ${points.length} opaque pixels`)
  const bounds = { x: Math.min(...points.map(p => p[0])), y: Math.min(...points.map(p => p[1])),
    right: Math.max(...points.map(p => p[0])), bottom: Math.max(...points.map(p => p[1])) }
  assert.ok(Math.abs((bounds.x + bounds.right) / 2 - width * 0.65) < 10, JSON.stringify(bounds))
  assert.ok(Math.abs((bounds.y + bounds.bottom) / 2 - height * 0.35) < 10, JSON.stringify(bounds))
  measurements.push({ title: '借路 / 第二集', opaque_pixels: points.length, bounds })
})

test('real fade and dissolve frames keep brightness, duration, and cut boundaries', () => {
  const a = clip('red', color('red', 'red'), 0, 2, { transitionIn: { type: 'fade-to-black', duration: 0.5 }, transitionOut: { type: 'dissolve', duration: 0.5 } })
  const b = clip('blue', color('blue', 'blue'), 2, 2, { transitionIn: { type: 'dissolve', duration: 0.5 }, transitionOut: { type: 'fade-to-black', duration: 0.5 } })
  const raw = render([a, b], 4)
  for (let frame = 0; frame < 96; frame++) {
    const time = frame / fps, rgb = pixel(raw, frame)
    let red = 0, blue = 0
    if (time < 0.5) red = 255 * time / 0.5
    else if (time < 1.5) red = 255
    else if (time < 2) { blue = 255 * (time - 1.5) / 0.5; red = 255 - blue }
    else if (time < 3.5) blue = 255
    else blue = 255 * (4 - time) / 0.5
    assert.ok(Math.abs(rgb[0] - red) < 18 && Math.abs(rgb[2] - blue) < 18 && rgb[1] < 10, `frame ${frame}: ${rgb}, expected ${red},0,${blue}`)
  }
  assert.equal(getClipEffectStyles(a, 0.25).filter, 'brightness(0.5)')
  assert.equal(getClipEffectStyles(a, 0.25).opacity, undefined, 'preview must not dim the fade twice')
  measurements.push({ frames: 96, dissolve_midpoint: pixel(raw, 42), cut: pixel(raw, 48), first: pixel(raw, 0), last: pixel(raw, 95) })
})

test('unimplemented effects and broken or overlapping dissolves fail explicitly', () => {
  assert.throws(() => buildStudioTransitionLayers([clip('a', '', 0, 2, { transitionIn: { type: 'wipe-left', duration: 0.5 } })], fps), /只支持/)
  assert.throws(() => buildStudioTransitionLayers([clip('a', '', 0, 2, { transitionOut: { type: 'dissolve', duration: 0.5 } })], fps), /没有匹配/)
  assert.throws(() => buildStudioTitleAss({ ...STUDIO_TITLE_STYLE, shadowBlur: 4 }, width, height), /尚未接通/)
})

test('25 fps fade retains the actual final selected source frame', () => {
  const source = join(output, 'distinct-last-frame.mp4')
  run(['-f', 'lavfi', '-i', `color=c=red:s=${width}x${height}:r=25:d=3`,
    '-vf', "drawbox=x=0:y=0:w=iw:h=ih:color=lime:t=fill:enable='eq(n,49)'",
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', source])
  const raw = render([clip('last-frame', source, 0, 2,
    { transitionOut: { type: 'fade-to-black', duration: 0.4 } })], 2, 25)
  const before = pixel(raw, 48), last = pixel(raw, 49)
  assert.ok(before[0] > 35 && before[1] < 10, `penultimate frame: ${before}`)
  assert.ok(last[1] > 15 && last[1] < 35 && last[0] < 10, `actual last frame: ${last}`)
  measurements.push({ output_fps: 25, frames: 50, distinct_final_frame: last, penultimate_frame: before })
})

test('actual MP4 export preserves timed Chinese title, dissolve and final frame', () => {
  const red = color('film-red', 'red'), blue = color('film-blue', 'blue')
  const timeline = createDefaultTimeline()
  timeline.clips = [clip('red', red, 0, 2, { transitionIn: { type: 'fade-to-black', duration: 0.5 }, transitionOut: { type: 'dissolve', duration: 0.5 } }),
    clip('blue', blue, 2, 2, { transitionIn: { type: 'dissolve', duration: 0.5 }, transitionOut: { type: 'fade-to-black', duration: 0.5 } }),
    clip('title', '', 13 / fps, 37 / fps, { assetId: null, type: 'text', trackIndex: 1, textStyle: { ...STUDIO_TITLE_STYLE, text: '借路 · 第二集', fontSize: 144, color: '#FFFF00' } })]
  const project = projectSchema.parse({ version: 2, id: 'visual-test', name: '中文标题与基本转场', createdAt: 0, updatedAt: 0, bins: {}, activeTimelineId: timeline.id, timelines: [timeline],
    assets: [{ id: 'red', type: 'video', path: red, duration: 3, resolution: '320x180' }, { id: 'blue', type: 'video', path: blue, duration: 3, resolution: '320x180' }] })
  const outputPath = join(output, 'chinese-title-transitions.mp4'), input = join(temporary, 'input.json')
  writeFileSync(input, JSON.stringify({ project, outputPath, ffmpegPath: ffmpeg, width, height, fps }))
  const result = spawnSync(process.execPath, [cli, input], { encoding: 'utf8', timeout: 30000 })
  assert.equal(result.status, 0, result.error?.message || result.stderr)
  const raw = run(['-i', outputPath, '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'])
  assert.equal(raw.length, 96 * width * height * 3)
  function yellow(frame) {
    let count = 0
    for (let y = 50; y < 125; y++) for (let x = 25; x < 295; x++) { const [r, g, b] = pixel(raw, frame, x, y); if (r > 150 && g > 150 && b < 80) count++ }
    return count
  }
  assert.equal(yellow(12), 0)
  assert.ok(yellow(13) > 150)
  assert.ok(yellow(49) > 150)
  assert.equal(yellow(50), 0)
  assert.ok(pixel(raw, 42)[0] > 110 && pixel(raw, 42)[2] > 110)
  measurements.push({ exported_mp4: outputPath, frames: 96, title_start_frame: 13, title_end_frame_exclusive: 50, title_pixels: yellow(13) })
  if (process.env.STUDIO_VISUAL_TEST_OUTPUT_DIR) {
    run(['-i', outputPath, '-vf', "select='eq(n,12)+eq(n,13)+eq(n,42)+eq(n,48)+eq(n,50)+eq(n,95)',tile=3x2", '-frames:v', '1', join(output, 'title-transition-frames.png')])
    writeFileSync(join(output, 'editorial-input.json'), readFileSync(input))
  }
})
