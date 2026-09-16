import type { ExportClip } from './timeline'
import { buildStudioColorFilters } from './studio-color'

/** Composite the current desktop timeline on one clock, retaining PNG alpha. */
export function buildStudioVideoFilterGraph(
  clips: ExportClip[],
  { width, height, fps, duration, layerEffects }: { width: number; height: number; fps: number; duration: number;
    layerEffects?: ReadonlyMap<ExportClip, { startTime: number; filters: string }> },
): { inputs: string[]; filterScript: string } {
  const visual = clips.filter(clip => clip.type === 'video' || clip.type === 'image')
    .sort((a, b) => a.trackIndex - b.trackIndex || a.startTime - b.startTime)
  // The final fps filter needs one lookahead frame; trim removes it after
  // conversion so the requested final frame is retained as well.
  const inputs = ['-f', 'lavfi', '-i', `color=c=black:s=${width}x${height}:r=${fps}:d=${(duration + 1 / fps).toFixed(6)}`]
  const filters = ['[0:v]format=rgba,setpts=PTS-STARTPTS[base]']
  let previous = 'base'
  visual.forEach((clip, index) => {
    const input = index + 1
    // Overlay runs on the base frame clock. Decimal seconds rounded to six
    // places can enable an adjacent clip one frame late (for example 13/24).
    const effect = layerEffects?.get(clip)
    const startFrame = Math.ceil((effect?.startTime ?? clip.startTime) * fps - 1e-7)
    const endFrame = Math.ceil((clip.startTime + clip.duration) * fps - 1e-7)
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
    // Normalize before scale's framesync. At EOF, scale can report the last
    // frame's timestamp; a later fps filter then drops that actual final frame.
    if (effect) chain += `,fps=${fps}`
    chain += buildStudioColorFilters(clip.colorCorrection)
    chain += `,format=rgba,scale=${width}:${height}:force_original_aspect_ratio=decrease,pad=${width}:${height}:(ow-iw)/2:(oh-ih)/2:color=black@0,setsar=1`
    if (clip.flipH) chain += ',hflip'
    if (clip.flipV) chain += ',vflip'
    if (clip.opacity !== 100) chain += `,colorchannelmixer=aa=${(clip.opacity / 100).toFixed(6)}`
    chain += effect?.filters ?? ''
    chain += `,setpts=PTS-STARTPTS+${startFrame}/${fps}/TB[layer${index}]`
    filters.push(chain)
    const next = `composite${index}`
    // fps inside a transition can change framesync's n origin.
    // Compare the timestamp on the same integer frame grid in that case.
    const clock = layerEffects?.size ? `t*${fps}+0.0000001` : 'n'
    // A decimal media duration can end between output frames (9.42s at 24fps).
    // Keep its last decoded picture through this clip's quantized half-open
    // interval; passing EOF through would reveal one frame of the black base.
    // The enable range still ends at endFrame, preserving intentional gaps.
    filters.push(`[${previous}][layer${index}]overlay=0:0:eof_action=repeat:repeatlast=1:format=auto:enable='gte(${clock},${startFrame})*lt(${clock},${endFrame})'[${next}]`)
    previous = next
  })
  // Convert frame rate once, after all timed layers, so extra graphics do not
  // quantize each cut separately or lengthen the underlying film.
  filters.push(`[${previous}]fps=${fps},trim=duration=${duration.toFixed(6)},format=yuv420p[outv]`)
  return { inputs, filterScript: filters.join(';\n') }
}
