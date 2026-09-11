import type { ExportClip } from './timeline'

/** Composite the current desktop timeline on one clock, retaining PNG alpha. */
export function buildStudioVideoFilterGraph(
  clips: ExportClip[],
  { width, height, fps, duration }: { width: number; height: number; fps: number; duration: number },
): { inputs: string[]; filterScript: string } {
  const visual = clips.filter(clip => clip.type === 'video' || clip.type === 'image')
    .sort((a, b) => a.trackIndex - b.trackIndex)
  const inputs = ['-f', 'lavfi', '-i', `color=c=black:s=${width}x${height}:r=${fps}:d=${duration.toFixed(6)}`]
  const filters = ['[0:v]format=rgba,setpts=PTS-STARTPTS[base]']
  let previous = 'base'
  visual.forEach((clip, index) => {
    const input = index + 1
    let chain: string
    if (clip.type === 'image') {
      inputs.push('-loop', '1', '-framerate', String(fps), '-t', clip.duration.toFixed(6), '-i', clip.path)
      chain = `[${input}:v]setpts=PTS-STARTPTS`
    } else {
      inputs.push('-i', clip.path)
      chain = `[${input}:v]trim=start=${clip.trimStart.toFixed(6)}:end=${(clip.trimStart + clip.duration * clip.speed).toFixed(6)},setpts=PTS-STARTPTS`
      if (clip.reversed) chain += ',reverse,setpts=PTS-STARTPTS'
      if (clip.speed !== 1) chain += `,setpts=PTS/${clip.speed.toFixed(6)}`
    }
    chain += `,format=rgba,scale=${width}:${height}:force_original_aspect_ratio=decrease,pad=${width}:${height}:(ow-iw)/2:(oh-ih)/2:color=black@0,setsar=1`
    if (clip.flipH) chain += ',hflip'
    if (clip.flipV) chain += ',vflip'
    if (clip.opacity !== 100) chain += `,colorchannelmixer=aa=${(clip.opacity / 100).toFixed(6)}`
    chain += `,setpts=PTS-STARTPTS+${clip.startTime.toFixed(6)}/TB[layer${index}]`
    filters.push(chain)
    const next = `composite${index}`
    filters.push(`[${previous}][layer${index}]overlay=0:0:eof_action=pass:repeatlast=0:format=auto:enable='gte(t,${clip.startTime.toFixed(6)})*lt(t,${(clip.startTime + clip.duration).toFixed(6)})'[${next}]`)
    previous = next
  })
  // Convert frame rate once, after all timed layers, so extra graphics do not
  // quantize each cut separately or lengthen the underlying film.
  filters.push(`[${previous}]fps=${fps},trim=duration=${duration.toFixed(6)},format=yuv420p[outv]`)
  return { inputs, filterScript: filters.join(';\n') }
}
