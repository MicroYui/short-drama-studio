import type { ExportClip } from './timeline'

export interface StudioLayerEffect { startTime: number; filters: string }

/** A dissolve introduces the next clip's first frame before its existing cut. */
export function buildStudioTransitionLayers(clips: ExportClip[], fps: number): Map<ExportClip, StudioLayerEffect> {
  const layers = new Map<ExportClip, StudioLayerEffect>()
  const media = clips.filter(clip => !clip.textStyle && (clip.type === 'video' || clip.type === 'image'))
  const supported = new Set(['none', 'fade-to-black', 'dissolve'])
  for (const clip of clips) {
    for (const edge of ['transitionIn', 'transitionOut'] as const) {
      const value = clip[edge]
      if (!value || value.type === 'none') continue
      if (!media.includes(clip) || !supported.has(value.type)) throw new Error('当前导出只支持画面淡入黑场、淡出黑场与相邻片段溶解。')
      if (!Number.isFinite(value.duration) || value.duration <= 0 || value.duration > clip.duration) throw new Error('转场时长须大于 0，且不超过片段时长。')
    }
    const inDuration = clip.transitionIn?.type === 'fade-to-black' ? clip.transitionIn.duration : 0
    const outDuration = clip.transitionOut?.type !== 'none' ? clip.transitionOut?.duration ?? 0 : 0
    if (inDuration + outDuration > clip.duration + 1e-7) throw new Error('片段的入场和出场转场不能重叠，请缩短转场时长。')
  }
  for (const clip of media) {
    let intro = 0
    if (clip.transitionIn?.type === 'dissolve') {
      const previous = media.find(item => item !== clip && item.trackIndex === clip.trackIndex
        && Math.abs(item.startTime + item.duration - clip.startTime) < 0.001 && item.transitionOut?.type === 'dissolve')
      if (!previous || Math.abs(previous.transitionOut!.duration - clip.transitionIn.duration) > 1e-7) throw new Error('溶解需要同轨相邻的两个片段，并使用相同的转场时长。')
      intro = previous.transitionOut!.duration
      if (media.some(item => item !== clip && item !== previous && item.startTime < clip.startTime
        && item.startTime + item.duration > clip.startTime - intro)) throw new Error('基本溶解区间同时覆盖了其他画面片段；请移开重叠画面或移除溶解。')
    }
    if (clip.transitionOut?.type === 'dissolve' && !media.some(item => item !== clip && item.trackIndex === clip.trackIndex
      && Math.abs(item.startTime - clip.startTime - clip.duration) < 0.001 && item.transitionIn?.type === 'dissolve')) {
      throw new Error('片段末尾的溶解没有匹配的下一片段，请重新选择相邻片段转场。')
    }
    const fadeIn = clip.transitionIn?.type === 'fade-to-black' ? clip.transitionIn.duration : 0
    const fadeOut = clip.transitionOut?.type === 'fade-to-black' ? clip.transitionOut.duration : 0
    if (!intro && !fadeIn && !fadeOut) continue
    const start = Math.ceil((clip.startTime - intro) * fps - 1e-7) / fps
    const hold = Math.ceil(clip.startTime * fps - 1e-7) / fps - start
    let filters = ''
    if (hold) filters += `,tpad=start_mode=clone:start_duration=${hold.toFixed(6)}`
    if (fadeIn || fadeOut) {
      // FFmpeg fade on RGBA dims both RGB and alpha. Change only RGB here so
      // existing PNG transparency survives and brightness is linear.
      const gainIn = fadeIn ? `min(1,max(0,(T-${hold.toFixed(6)})/${fadeIn.toFixed(6)}))` : '1'
      const gainOut = fadeOut ? `min(1,max(0,(${(hold + clip.duration).toFixed(6)}-T)/${fadeOut.toFixed(6)}))` : '1'
      const gain = `min(${gainIn},${gainOut})`
      filters += `,format=gbrap,geq=r='r(X,Y)*${gain}':g='g(X,Y)*${gain}':b='b(X,Y)*${gain}':a='alpha(X,Y)',format=rgba`
    }
    if (hold) filters += `,fade=t=in:st=0:d=${hold.toFixed(6)}:alpha=1`
    layers.set(clip, { startTime: start, filters })
  }
  return layers
}
