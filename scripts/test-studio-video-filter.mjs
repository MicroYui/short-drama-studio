import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { after, test } from 'node:test'
import ts from 'typescript'

// Use an existing FFmpeg installation; this check never downloads a runtime.
const probe = spawnSync(fileURLToPath(new URL('../backend/.venv/bin/python', import.meta.url)), ['-c', 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())'], { encoding: 'utf8' })
const ffmpeg = process.env.FFMPEG_PATH || (probe.status === 0 ? probe.stdout.trim() : 'ffmpeg')
const temporary = mkdtempSync(join(tmpdir(), 'studio-video-filter-'))
after(() => rmSync(temporary, { recursive: true, force: true }))
const source = readFileSync(new URL('../electron/export/studio-video-filter.ts', import.meta.url), 'utf8')
const colorSource = readFileSync(new URL('../electron/export/studio-color.ts', import.meta.url), 'utf8')
const colorModule = ts.transpileModule(colorSource, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const colorUrl = `data:text/javascript;base64,${Buffer.from(colorModule).toString('base64')}`
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText.replace("'./studio-color'", JSON.stringify(colorUrl))
const { buildStudioVideoFilterGraph } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
const width = 64, height = 36, fps = 24

function run(args) {
  const result = spawnSync(ffmpeg, ['-hide_banner', '-loglevel', 'error', '-y', ...args], { maxBuffer: 8 * 1024 * 1024, timeout: 30000 })
  assert.equal(result.status, 0, result.error?.message || result.stderr?.toString())
  return result.stdout
}

function video(name, color, rate) {
  const path = join(temporary, `${name}.mp4`)
  run(['-f', 'lavfi', '-i', `color=c=${color}:s=${width}x${height}:r=${rate}:d=3`, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', path])
  return path
}

function clip(path, startFrame, frames, extra = {}) {
  return { path, type: 'video', trackIndex: 0, startTime: startFrame / fps, duration: frames / fps,
    trimStart: 0, speed: 1, reversed: false, flipH: false, flipV: false, opacity: 100, ...extra }
}

function render(clips, frames) {
  const { inputs, filterScript } = buildStudioVideoFilterGraph(clips, { width, height, fps, duration: frames / fps })
  const script = join(temporary, 'filter.txt')
  writeFileSync(script, filterScript)
  const raw = run([...inputs, '-filter_complex_threads', '1', '-filter_complex_script', script,
    '-map', '[outv]', '-an', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'])
  assert.equal(raw.length, frames * width * height * 3, 'export must retain the planned frame count')
  return frame => [...raw.subarray((frame * width * height + width * Math.floor(height / 2) + width / 2) * 3,
    (frame * width * height + width * Math.floor(height / 2) + width / 2) * 3 + 3)]
}

test('adjacent 24 fps cuts have no black or wrong-color frames at fractional-second boundaries', () => {
  const red = video('red', 'red', 24)
  const blue = video('blue', 'blue', 24)
  const clips = Array.from({ length: 12 }, (_, index) => clip(index % 2 ? blue : red, index * 13, 13))
  const pixel = render(clips, 156)
  for (let frame = 0; frame < 156; frame++) {
    const rgb = pixel(frame)
    const channel = Math.floor(frame / 13) % 2 ? 2 : 0
    assert.ok(rgb[channel] > 230 && rgb[1] < 20 && rgb[2 - channel] < 20,
      `unexpected pixel at frame ${frame}: ${rgb}`)
  }
})

test('fractional cuts preserve intentional gaps, mixed source rates, and timed PNG alpha', () => {
  const red = video('red-mixed', 'red', 30)
  const blue = video('blue-mixed', 'blue', 25)
  const overlay = join(temporary, 'overlay.png')
  run(['-f', 'lavfi', '-i', `color=c=white@0.5:s=${width}x${height},format=rgba`, '-frames:v', '1', overlay])
  const pixel = render([clip(red, 0, 48), clip(blue, 49, 25),
    clip(overlay, 13, 13, { type: 'image', trackIndex: 1 })], 74)
  for (let frame = 0; frame < 74; frame++) {
    const rgb = pixel(frame)
    if (frame === 48) assert.ok(rgb.every(value => value < 10), `planned gap at frame ${frame}: ${rgb}`)
    else if (frame >= 49) assert.ok(rgb[2] > 230 && rgb[0] < 20, `blue at frame ${frame}: ${rgb}`)
    else if (frame >= 13 && frame < 26) assert.ok(rgb[0] > 230 && rgb[1] > 110 && rgb[1] < 145,
      `PNG alpha at frame ${frame}: ${rgb}`)
    else assert.ok(rgb[0] > 230 && rgb[1] < 20, `red at frame ${frame}: ${rgb}`)
  }
})

test('a rounded container duration holds the final source frame until the adjacent quantized cut', () => {
  const red = join(temporary, '226-source-frames.mp4')
  run(['-f', 'lavfi', '-i', `color=c=red:s=${width}x${height}:r=24`, '-frames:v', '226',
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', red])
  const blue = video('after-rounded-duration', 'blue', 24)
  const clips = [clip(red, 0, 226, { duration: 9.42 }),
    clip(blue, 0, 24, { startTime: 9.42, duration: 250 / fps - 9.42 })]
  const before = structuredClone(clips)
  const pixel = render(clips, 250)
  assert.deepEqual(clips, before, 'render must not quantize the editable timeline or its audio times')
  for (let frame = 0; frame < 250; frame++) {
    const rgb = pixel(frame)
    const channel = frame < 227 ? 0 : 2
    assert.ok(rgb[channel] > 230 && rgb[1] < 20 && rgb[2 - channel] < 20,
      `rounded duration exposed a missing or wrong frame ${frame}: ${rgb}`)
  }
})
