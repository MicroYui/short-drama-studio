/** Local backend adapter around LTX's existing timeline, filters and audio mixer. */
import fs from 'node:fs'
import path from 'node:path'
import { spawn } from 'node:child_process'
import { projectSchema } from '../../frontend/types/project-model'
import { type ExportClip } from './timeline'
import { buildStudioVideoFilterGraph } from './studio-video-filter'
import { mixAudioToPcm } from './audio-mix'
import { clipAudioDelegated } from '../../frontend/lib/clip-audio-source'
import { buildStudioTitleAss, studioAssFilter } from './studio-title'
import { buildStudioTransitionLayers } from './studio-transition'

async function ffmpeg(executable: string, args: string[]) {
  await new Promise<void>((resolve, reject) => {
    const child = spawn(executable, ['-hide_banner', '-loglevel', 'error', ...args], { stdio: ['ignore', 'ignore', 'pipe'] })
    let error = ''
    child.stderr.on('data', value => { error = (error + String(value)).slice(-8000) })
    child.on('error', reject)
    child.on('close', code => code === 0 ? resolve() : reject(new Error(error || `FFmpeg exited ${code}`)))
  })
}

async function main() {
  const input = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
  const project = projectSchema.parse(input.project)
  const timeline = project.timelines.find(item => item.id === project.activeTimelineId)!
  const hasSolo = timeline.tracks.some(track => track.solo)
  const folder = path.dirname(input.outputPath)
  const titles = new Map<string, string>()
  for (const clip of timeline.clips) {
    const track = timeline.tracks[clip.trackIndex]
    if (clip.type !== 'text' || !track || track.enabled === false || hasSolo && !track.solo) continue
    if (!clip.textStyle) throw new Error('标题缺少正文或样式。')
    const stem = path.join(folder, `title-${titles.size}`)
    fs.writeFileSync(`${stem}.ass`, buildStudioTitleAss(clip.textStyle, input.width, input.height))
    console.log(JSON.stringify({ progress: '渲染标题' }))
    await ffmpeg(input.ffmpegPath, ['-y', '-f', 'lavfi', '-i', `color=c=black@0:s=${input.width}x${input.height}:r=1,format=rgba`,
      '-vf', studioAssFilter(`${stem}.ass`), '-frames:v', '1', '-pix_fmt', 'rgba', `${stem}.png`])
    titles.set(clip.id, `${stem}.png`)
  }
  const sourceFor = (clip: typeof timeline.clips[number]) => {
    const asset = project.assets.find(item => item.id === clip.assetId)
    return clip.takeIndex === undefined ? asset : asset?.takes?.[clip.takeIndex] ?? asset
  }
  const clips: ExportClip[] = timeline.clips.flatMap(clip => {
    const track = timeline.tracks[clip.trackIndex]
    if (!track || track.enabled === false || hasSolo && !track.solo) return []
    const asset = project.assets.find(item => item.id === clip.assetId)
    if (!asset && clip.type !== 'text') throw new Error(`片段 ${clip.id} 缺少素材。`)
    const source = clip.takeIndex === undefined ? asset : asset?.takes?.[clip.takeIndex] ?? asset
    return [{ id: clip.id, path: titles.get(clip.id) ?? source!.path, type: clip.type === 'text' ? 'image' : clip.type, startTime: clip.startTime, duration: clip.duration,
      sourceType: asset?.type, audioDelegated: clipAudioDelegated(clip, timeline.clips, item => sourceFor(item)?.path ?? ''),
      trimStart: clip.trimStart, speed: clip.speed, reversed: clip.reversed, flipH: clip.flipH,
      flipV: clip.flipV, opacity: clip.opacity, trackIndex: clip.type === 'text' ? timeline.tracks.length + clip.trackIndex : clip.trackIndex,
      transitionIn: clip.transitionIn, transitionOut: clip.transitionOut, textStyle: clip.textStyle,
      colorCorrection: clip.colorCorrection,
      muted: track.muted || clip.muted, volume: clip.volume,
      audioFadeInSeconds: clip.audioFadeInSeconds, audioFadeOutSeconds: clip.audioFadeOutSeconds,
      audioEnvelopeOffsetSeconds: clip.audioEnvelopeOffsetSeconds, audioEnvelopeDurationSeconds: clip.audioEnvelopeDurationSeconds }]
  })
  if (!clips.some(clip => clip.type === 'video' || clip.type === 'image')) throw new Error('时间线尚无可导出的画面。')
  const duration = Math.max(...clips.map(clip => clip.startTime + clip.duration))
  const filterFile = path.join(folder, 'video-filter.txt')
  // Preserve fractional frame timestamps when copying H.264 into the final MP4.
  const video = path.join(folder, 'picture.mp4')
  const raw = path.join(folder, 'audio.raw')
  const layerEffects = buildStudioTransitionLayers(clips, input.fps)
  const { inputs, filterScript } = buildStudioVideoFilterGraph(clips, { width: input.width, height: input.height, fps: input.fps, duration, layerEffects })
  fs.writeFileSync(filterFile, filterScript)
  console.log(JSON.stringify({ progress: '渲染画面', clips: clips.length }))
  try {
    await ffmpeg(input.ffmpegPath, ['-y', ...inputs, '-filter_complex_threads', '1', '-filter_complex_script', filterFile, '-map', '[outv]', '-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p', video])
    console.log(JSON.stringify({ progress: '混合声音' }))
    const audio = await mixAudioToPcm(clips, duration, input.ffmpegPath, true)
    fs.writeFileSync(raw, audio.pcmBuffer)
    console.log(JSON.stringify({ progress: '写入成片' }))
    await ffmpeg(input.ffmpegPath, ['-y', '-i', video, '-f', 's16le', '-ar', String(audio.sampleRate), '-ac', String(audio.channels), '-i', raw, '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-t', String(duration), '-movflags', '+faststart', input.outputPath])
    console.log(JSON.stringify({ completed: true, duration_seconds: duration }))
  } finally {
    for (const file of [video, raw]) fs.rmSync(file, { force: true })
  }
}
main().catch(error => { console.error(String(error)); process.exitCode = 1 })
