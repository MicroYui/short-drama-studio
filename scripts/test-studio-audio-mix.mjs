import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { after, test } from 'node:test'

// Compile the real mixer, editor actions and product export CLI with installed
// dependencies. All media is synthesized locally; no Provider or user file.
const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
const root = fileURLToPath(new URL('..', import.meta.url))
const temporary = mkdtempSync(join(tmpdir(), 'studio-audio-mix-'))
const output = process.env.STUDIO_AUDIO_TEST_OUTPUT_DIR ? resolve(process.env.STUDIO_AUDIO_TEST_OUTPUT_DIR) : temporary
mkdirSync(output, { recursive: true })
let ffmpeg = process.env.FFMPEG_PATH
if (!ffmpeg) {
  const existing = spawnSync(join(root, 'backend/.venv/bin/python'), ['-c', 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())'], { encoding: 'utf8' })
  ffmpeg = existing.status === 0 ? existing.stdout.trim() : 'ffmpeg'
}
const measurements = []
after(() => {
  if (process.env.STUDIO_AUDIO_TEST_OUTPUT_DIR) writeFileSync(join(output, 'audio-measurements.json'), JSON.stringify(measurements, null, 2) + '\n')
  rmSync(temporary, { recursive: true, force: true })
})
const compiled = join(temporary, 'audio-test.cjs')
await build({ stdin: { contents: `
  export { mixAudioToPcm } from './electron/export/audio-mix';
  export { clipAudioGain } from './frontend/lib/audio-envelope';
  export { clipAudioDelegated } from './frontend/lib/clip-audio-source';
  export { createInitialEditorState } from './frontend/views/editor/editor-state';
  export { splitClipsAtTime, resizeClip, setTimelineClips } from './frontend/views/editor/editor-actions';
  export { selectExportClipData } from './frontend/views/editor/editor-selectors';
  export { projectSchema, createDefaultTimeline } from './frontend/types/project-model';
  export { electronAPISchemas } from './shared/electron-api-schema';
`, resolveDir: root }, bundle: true, platform: 'node', target: 'node20', format: 'cjs', outfile: compiled })
const { mixAudioToPcm, clipAudioGain, clipAudioDelegated, createInitialEditorState, splitClipsAtTime, resizeClip, setTimelineClips,
  selectExportClipData, projectSchema, createDefaultTimeline, electronAPISchemas } = require(compiled)
const cli = join(temporary, 'studio-export.cjs')
await build({ entryPoints: [join(root, 'electron/export/studio-export-cli.ts')], bundle: true,
  platform: 'node', target: 'node20', format: 'cjs', outfile: cli })
const rate = 48000

function run(args, input) {
  const result = spawnSync(ffmpeg, ['-hide_banner', '-loglevel', 'error', '-y', ...args], {
    input, maxBuffer: 16 * 1024 * 1024, timeout: 30000,
  })
  assert.equal(result.status, 0, result.error?.message || result.stderr?.toString())
  return result.stdout
}

function tone(name, duration = 3, amplitude = 0.75) {
  const pcm = Buffer.alloc(Math.round(duration * rate) * 4)
  for (let frame = 0; frame < pcm.length / 4; frame++) {
    const left = Math.round(32767 * amplitude * Math.sin(2 * Math.PI * 1000 * frame / rate))
    pcm.writeInt16LE(left, frame * 4)
    pcm.writeInt16LE(Math.round(32767 * amplitude / 2 * Math.sin(2 * Math.PI * 1500 * frame / rate)), frame * 4 + 2)
  }
  const path = join(temporary, `${name}.wav`)
  run(['-f', 's16le', '-ar', String(rate), '-ac', '2', '-i', 'pipe:0', '-c:a', 'pcm_s16le', path], pcm)
  return { path, pcm, duration }
}

function movie(name, audio) {
  const path = join(temporary, `${name}.mov`)
  run(['-f', 'lavfi', '-i', `color=c=blue:s=64x36:r=24:d=${audio.duration}`, '-i', audio.path,
    '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'pcm_s16le', '-shortest', path])
  return path
}

function clip(path, extra = {}) {
  return { path, type: 'audio', startTime: 0, duration: 3, trimStart: 0, speed: 1, reversed: false,
    flipH: false, flipV: false, opacity: 100, trackIndex: 0, muted: false, volume: 1, ...extra }
}

function project(clips) {
  const timeline = createDefaultTimeline()
  timeline.id = 'timeline'
  const assets = clips.map((item, index) => ({ id: `asset-${index}`, type: item.type, path: item.path,
    resolution: '64x36', width: 64, height: 36, duration: 6 }))
  timeline.clips = clips.map((item, index) => ({ ...item, id: `clip-${index}`, assetId: assets[index].id, trimEnd: 0, asset: null }))
  return projectSchema.parse({ version: 2, id: 'audio-test', name: 'Audio regression', createdAt: 0,
    updatedAt: 0, bins: {}, assets, timelines: [timeline], activeTimelineId: timeline.id })
}

function sample(pcm, frame, channel = 0) { return pcm.readInt16LE((frame * 2 + channel) * 2) }
function rms(pcm, center, channel = 0) {
  let squared = 0, count = 0
  for (let frame = Math.round((center - 0.02) * rate); frame < Math.round((center + 0.02) * rate); frame++) {
    squared += sample(pcm, frame, channel) ** 2
    count++
  }
  return Math.sqrt(squared / count)
}
function peak(pcm) {
  let highest = 0
  for (let offset = 0; offset < pcm.length; offset += 2) highest = Math.max(highest, Math.abs(pcm.readInt16LE(offset)))
  return highest / 32768
}
function close(actual, expected, tolerance, message) {
  assert.ok(Math.abs(actual - expected) <= tolerance, `${message}: ${actual} vs ${expected}`)
}

function exportMovie(name, clips) {
  const directory = join(output, name)
  mkdirSync(directory, { recursive: true })
  const outputPath = join(directory, `${name}.mp4`)
  const inputPath = join(temporary, `${name}.json`)
  writeFileSync(inputPath, JSON.stringify({ project: project(clips), outputPath, ffmpegPath: ffmpeg, width: 64, height: 36, fps: 24 }))
  const result = spawnSync(process.execPath, [cli, inputPath], { encoding: 'utf8', timeout: 30000, maxBuffer: 1024 * 1024 })
  assert.equal(result.status, 0, result.stderr || result.stdout)
  const pcm = run(['-i', outputPath, '-map', '0:a', '-f', 's16le', '-ar', String(rate), '-ac', '2', 'pipe:1'])
  const floatPcm = run(['-i', outputPath, '-map', '0:a', '-f', 'f32le', '-ar', String(rate), '-ac', '2', 'pipe:1'])
  let decodedPeak = 0
  for (let offset = 0; offset < floatPcm.length; offset += 4) decodedPeak = Math.max(decodedPeak, Math.abs(floatPcm.readFloatLE(offset)))
  return { pcm, decodedPeak, outputPath, log: result.stdout }
}

test('ordinary audio and both legal full-scale Int16 values remain unchanged', async () => {
  const audio = tone('normal', 1, 0.4)
  const normal = await mixAudioToPcm([clip(audio.path, { duration: 1 })], 1, ffmpeg, true)
  assert.equal(normal.outputGain, 1)
  assert.deepEqual(normal.pcmBuffer, audio.pcm)
  const full = Buffer.alloc(rate * 4)
  for (let frame = 0; frame < rate; frame++) { full.writeInt16LE(32767, frame * 4); full.writeInt16LE(-32768, frame * 4 + 2) }
  const fullPath = join(temporary, 'full-scale.wav')
  run(['-f', 's16le', '-ar', String(rate), '-ac', '2', '-i', 'pipe:0', '-c:a', 'pcm_s16le', fullPath], full)
  const exact = await mixAudioToPcm([clip(fullPath, { duration: 1 })], 1, ffmpeg, true)
  assert.equal(exact.outputGain, 1)
  assert.deepEqual(exact.pcmBuffer, full)
  measurements.push({ case: 'normal_and_full_scale_unchanged', outputGain: exact.outputGain, byteExact: true })
})

test('linked native sound plays once; muting or disabling its audio track never revives video sound', async () => {
  const source = tone('linked-native', 1, 0.2)
  const videoPath = movie('linked-native', source)
  const p = project([clip(videoPath, { type: 'video', duration: 1 }), clip(videoPath, { duration: 1, trackIndex: 2 })])
  p.assets[1].type = 'video'
  p.timelines[0].clips[0].linkedClipIds = ['clip-1']
  p.timelines[0].clips[1].linkedClipIds = ['clip-0']
  const allClips = p.timelines[0].clips
  assert.equal(clipAudioDelegated(allClips[0], allClips, () => videoPath), true)
  assert.equal(clipAudioDelegated(allClips[1], allClips, () => videoPath), false)
  assert.equal(clipAudioDelegated(allClips[0], allClips, item => item.id), false, 'linking unrelated music keeps native audio')
  const data = selectExportClipData(createInitialEditorState(p))
  const ipc = electronAPISchemas.exportNative.input.parse({ clips: data, outputPath: 'unused.mp4', codec: 'h264', width: 64, height: 36, fps: 24, quality: 18 })
  assert.equal(ipc.clips[0].audioDelegated, true)
  assert.equal(ipc.clips[1].sourceType, 'video')
  const once = await mixAudioToPcm(ipc.clips, 1, ffmpeg, true)
  assert.deepEqual(once.pcmBuffer, source.pcm)
  for (const mode of ['muted', 'disabled', 'clip-muted', 'solo-video']) {
    const variant = structuredClone(p)
    if (mode === 'muted') variant.timelines[0].tracks[2].muted = true
    if (mode === 'disabled') variant.timelines[0].tracks[2].enabled = false
    if (mode === 'clip-muted') variant.timelines[0].clips[1].muted = true
    if (mode === 'solo-video') variant.timelines[0].tracks[0].solo = true
    const selected = selectExportClipData(createInitialEditorState(variant))
    assert.equal(selected[0].audioDelegated, true, mode)
    const result = await mixAudioToPcm(selected, 1, ffmpeg, true)
    assert.equal(peak(result.pcmBuffer), 0, mode)
  }
  measurements.push({ case: 'linked_native_once_and_audio_disabled', pcmExact: true, silentModes: 4 })
})

test('silent video audio references are skipped, while broken audio files still fail strict export', async () => {
  const silent = join(temporary, 'silent-video.mp4')
  run(['-f', 'lavfi', '-i', 'color=c=blue:s=64x36:r=24:d=1', '-an', '-c:v', 'libx264', silent])
  const result = await mixAudioToPcm([clip(silent, { sourceType: 'video', duration: 1 })], 1, ffmpeg, true)
  assert.equal(peak(result.pcmBuffer), 0)
  const broken = join(temporary, 'broken.wav')
  writeFileSync(broken, 'not a media file')
  await assert.rejects(mixAudioToPcm([clip(broken, { duration: 1 })], 1, ffmpeg, true), /PCM extraction failed/)
  await assert.rejects(mixAudioToPcm([clip(broken, { sourceType: 'video', duration: 1 })], 1, ffmpeg, true), /PCM extraction failed/)
})

test('overlapping stereo sources get one common gain without flattened peaks', async () => {
  const audio = tone('overlap', 1, 0.8)
  const mixed = await mixAudioToPcm([clip(audio.path, { duration: 1 }), clip(audio.path, { duration: 1 })], 1, ffmpeg, true)
  assert.ok(mixed.peakBeforeGain > 1.59 && mixed.outputGain < 0.62)
  assert.ok(peak(mixed.pcmBuffer) <= 0.95001)
  for (let offset = 0; offset < mixed.pcmBuffer.length; offset += 2) {
    close(mixed.pcmBuffer.readInt16LE(offset), 2 * audio.pcm.readInt16LE(offset) * mixed.outputGain, 0.50001, 'uniform gain')
  }
  close(rms(mixed.pcmBuffer, 0.5, 0) / rms(mixed.pcmBuffer, 0.5, 1), 2, 0.0001, 'stereo balance')
  measurements.push({ case: 'overlap_uniform_gain', peakBeforeGain: mixed.peakBeforeGain,
    outputGain: mixed.outputGain, pcmPeak: peak(mixed.pcmBuffer), stereoRatio: rms(mixed.pcmBuffer, 0.5) / rms(mixed.pcmBuffer, 0.5, 1) })
})

test('sample-accurate fades use the same local-time envelope as preview, including overlap', async () => {
  const audio = tone('fades')
  const settings = clip(audio.path, { volume: 0.7, audioFadeInSeconds: 1, audioFadeOutSeconds: 1 })
  const mixed = await mixAudioToPcm([settings], 3, ffmpeg, true)
  for (let frame = 0; frame < 3 * rate; frame += 29) {
    for (let channel = 0; channel < 2; channel++) {
      close(sample(mixed.pcmBuffer, frame, channel), sample(audio.pcm, frame, channel) * clipAudioGain(settings, frame / rate),
        0.50001, 'rendered gain equals preview envelope')
    }
  }
  const inRatio = rms(mixed.pcmBuffer, 0.25) / rms(mixed.pcmBuffer, 1.5)
  const outRatio = rms(mixed.pcmBuffer, 2.75) / rms(mixed.pcmBuffer, 1.5)
  close(inRatio, 0.25, 0.002, 'fade in')
  close(outRatio, 0.25, 0.002, 'fade out')
  const overlapping = await mixAudioToPcm([clip(audio.path, { duration: 1, audioFadeInSeconds: 1, audioFadeOutSeconds: 1 })], 1, ffmpeg, true)
  close(rms(overlapping.pcmBuffer, 0.25) / rms(audio.pcm, 0.25), 0.25, 0.002, 'overlapping fades take minimum')
  measurements.push({ case: 'fades_pcm', fadeInQuarterGain: inRatio, fadeOutQuarterGain: outRatio, previewAndOutputSampleAgreement: true })
})

test('splitting linked video/audio preserves the original fade and source windows', async () => {
  const audio = tone('split')
  const settings = clip(audio.path, { audioFadeInSeconds: 1, audioFadeOutSeconds: 1 })
  const p = project([settings, { ...settings, type: 'video', muted: true }])
  p.timelines[0].clips[0].linkedClipIds = ['clip-1']
  p.timelines[0].clips[1].linkedClipIds = ['clip-0']
  const initial = createInitialEditorState(p)
  const split = splitClipsAtTime(initial, ['clip-0'], 0.5)
  assert.equal(split.editorModel.timelines[0].clips.length, 4)
  const whole = await mixAudioToPcm(selectExportClipData(initial), 3, ffmpeg, true)
  const sliced = await mixAudioToPcm(selectExportClipData(split), 3, ffmpeg, true)
  let maxSplitSampleDifference = 0
  for (let offset = 0; offset < whole.pcmBuffer.length; offset += 2) {
    maxSplitSampleDifference = Math.max(maxSplitSampleDifference, Math.abs(sliced.pcmBuffer.readInt16LE(offset) - whole.pcmBuffer.readInt16LE(offset)))
  }
  assert.ok(maxSplitSampleDifference <= 1, 'a plain split must retain the envelope within Int16 rounding precision')
  const right = split.editorModel.timelines[0].clips.find(item => item.type === 'audio' && item.startTime === 0.5)
  assert.equal(right.audioEnvelopeOffsetSeconds, 0.5)
  const resized = resizeClip(split, { clipId: right.id, edge: 'end', deltaTime: 0.25 })
  assert.equal(resized.editorModel.timelines[0].clips.find(item => item.id === right.id).audioEnvelopeDurationSeconds, undefined)
  const dragged = setTimelineClips(split, clips => clips.map(item => item.id === right.id ? { ...item, duration: 2.75 } : item))
  assert.equal(dragged.editorModel.timelines[0].clips.find(item => item.id === right.id).audioEnvelopeDurationSeconds, undefined)
  const sped = createInitialEditorState(project([clip(audio.path, { duration: 1.5, speed: 2, audioFadeInSeconds: 1 })]))
  const spedParts = splitClipsAtTime(sped, ['clip-0'], 0.5).editorModel.timelines[0].clips
  assert.equal(spedParts[1].trimStart, 1, 'timeline split position must be converted into source time')
  assert.equal(spedParts[0].trimEnd, 2)
  // IPC validation must preserve the fields used by the desktop export path.
  const ipc = electronAPISchemas.exportNative.input.parse({ clips: selectExportClipData(split), outputPath: 'unused.mp4', codec: 'h264', width: 64, height: 36, fps: 24, quality: 18 })
  assert.equal(ipc.clips.find(item => item.startTime === 0.5).audioEnvelopeOffsetSeconds, 0.5)
  measurements.push({ case: 'linked_split', maxSplitSampleDifference, splitAtSeconds: 0.5, spedSourceSplitSeconds: spedParts[1].trimStart })
})

test('the actual MP4 exporter fades embedded video audio and AAC preserves the measured curve', () => {
  const audio = tone('video-fades', 3, 0.5)
  const path = movie('video-fades-source', audio)
  const rendered = exportMovie('video-audio-fades', [clip(path, { type: 'video', audioFadeInSeconds: 1, audioFadeOutSeconds: 1 })])
  const inRatio = rms(rendered.pcm, 0.25) / rms(rendered.pcm, 1.5)
  const outRatio = rms(rendered.pcm, 2.75) / rms(rendered.pcm, 1.5)
  close(inRatio, 0.25, 0.012, 'encoded video fade in')
  close(outRatio, 0.25, 0.012, 'encoded video fade out')
  close(rms(rendered.pcm, 1.5) / rms(rendered.pcm, 1.5, 1), 2, 0.035, 'encoded stereo balance')
  measurements.push({ case: 'actual_mp4_embedded_audio_fades', outputPath: rendered.outputPath,
    decodedPeak: rendered.decodedPeak, fadeInQuarterGain: inRatio, fadeOutQuarterGain: outRatio })
})

test('the actual MP4 exporter attenuates an overloaded video plus audio mix before AAC', () => {
  const audio = tone('video-overload', 3, 0.8)
  const path = movie('video-overload-source', audio)
  const fades = { audioFadeInSeconds: 0.1, audioFadeOutSeconds: 0.1 }
  const rendered = exportMovie('video-audio-overload', [clip(path, { type: 'video', ...fades }), clip(audio.path, fades)])
  assert.ok(rendered.log.includes('outputGain=0.593'), rendered.log)
  assert.ok(rendered.decodedPeak < 1, `decoded AAC exceeds full scale: ${rendered.decodedPeak}`)
  const stereoRatio = rms(rendered.pcm, 1.5) / rms(rendered.pcm, 1.5, 1)
  close(stereoRatio, 2, 0.035, 'encoded overloaded stereo balance')
  measurements.push({ case: 'actual_mp4_overload', outputPath: rendered.outputPath, decodedPeak: rendered.decodedPeak, stereoRatio })
})
