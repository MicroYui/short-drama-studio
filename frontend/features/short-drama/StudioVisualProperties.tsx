import { Trash2 } from 'lucide-react'
import { STUDIO_TITLE_STYLE } from '../../lib/studio-title-style'
import { DEFAULT_COLOR_CORRECTION, type ClipTransition, type ColorCorrection, type TimelineClip } from '../../types/project-model'
import { useEditorActions } from '../../views/editor/editor-store'
import { adjacentVisualClip, changeStudioTransition } from './studio-visual-editing'

const colorFields: [keyof ColorCorrection, string][] = [
  ['brightness', '亮度'], ['contrast', '对比度'], ['saturation', '饱和度'], ['exposure', '曝光'],
  ['temperature', '色温'], ['tint', '色调'], ['highlights', '高光'], ['shadows', '阴影'],
]

export function StudioColorProperties({ clip }: { clip: TimelineClip }) {
  const actions = useEditorActions()
  if (clip.type !== 'video' && clip.type !== 'image') return null
  const correction = { ...DEFAULT_COLOR_CORRECTION, ...clip.colorCorrection }
  const changed = Object.values(correction).some(value => value !== 0)
  return <details className="sd-editor-color"><summary>基础调色{changed ? ' · 已调整' : ''}</summary>
    {colorFields.map(([field, label]) => <label className="sd-field" key={field}>{label} {correction[field]}
      <input aria-label={`画面${label}`} type="range" min="-100" max="100" step="1" value={correction[field]}
        onChange={event => actions.setClipColorCorrectionField(clip.id, field, Number(event.target.value))} />
    </label>)}
    <button className="sd-text-button" disabled={!changed} onClick={() => actions.resetClipColorCorrection(clip.id)}>恢复原色</button>
  </details>
}

export function StudioTitleProperties({ clip }: { clip: TimelineClip }) {
  const actions = useEditorActions()
  const style = clip.textStyle ?? STUDIO_TITLE_STYLE
  return <><h3>标题</h3>
    <label className="sd-field">正文<textarea aria-label="标题正文" value={style.text} onChange={event => actions.setClipTextStyleField(clip.id, 'text', event.target.value)} /></label>
    <p className="sd-hint">换行可分成多行；可直接拖动画面中的标题。</p>
    <label className="sd-field">字号<input aria-label="标题字号" type="number" min="1" max="500" value={style.fontSize} onChange={event => actions.setClipTextStyleField(clip.id, 'fontSize', Math.min(500, Math.max(1, Number(event.target.value))))} /></label>
    <label className="sd-field">颜色<input aria-label="标题颜色" type="color" value={style.color} onChange={event => actions.setClipTextStyleField(clip.id, 'color', event.target.value)} /></label>
    <label className="sd-field">水平位置（%）<input aria-label="标题水平位置" type="number" min="0" max="100" step="1" value={style.positionX} onChange={event => actions.setClipTextPosition(clip.id, Number(event.target.value), style.positionY)} /></label>
    <label className="sd-field">垂直位置（%）<input aria-label="标题垂直位置" type="number" min="0" max="100" step="1" value={style.positionY} onChange={event => actions.setClipTextPosition(clip.id, style.positionX, Number(event.target.value))} /></label>
    <label className="sd-field">开始（秒）<input aria-label="标题开始秒数" type="number" min="0" step="0.1" value={clip.startTime} onChange={event => actions.setClipStartTime(clip.id, Number(event.target.value))} /></label>
    <label className="sd-field">持续（秒）<input aria-label="标题持续秒数" type="number" min="0.1" step="0.1" value={clip.duration} onChange={event => actions.setClipDuration(clip.id, Number(event.target.value))} /></label>
    <button className="sd-text-button" onClick={() => actions.updateClip(clip.id, { textStyle: { ...STUDIO_TITLE_STYLE, text: style.text, fontSize: style.fontSize, positionX: style.positionX, positionY: style.positionY, color: style.color } })}>恢复基本样式</button>
    <button className="sd-button" onClick={() => actions.deleteClips([clip.id])}><Trash2 size={13} />删除标题</button>
  </>
}

export function StudioTransitionProperties({ clip, clips }: { clip: TimelineClip; clips: TimelineClip[] }) {
  const actions = useEditorActions()
  if (clip.type !== 'video' && clip.type !== 'image') return null
  return <><h3>画面转场</h3>{(['transitionIn', 'transitionOut'] as const).map(edge => {
    const value = clip[edge]
    const peer = adjacentVisualClip(clips, clip, edge)
    const label = edge === 'transitionIn' ? '入场' : '出场'
    const change = (next: ClipTransition) => actions.setTimelineClips(changeStudioTransition(clips, clip.id, edge, next))
    return <div key={edge}>
      <label className="sd-field">{label}<select aria-label={`画面${label}转场`} value={value.type} onChange={event => {
        const type = event.target.value as ClipTransition['type']
        const duration = Math.min(value.duration || 0.5, clip.duration, type === 'dissolve' ? peer?.duration ?? clip.duration : clip.duration)
        change({ type, duration })
      }}><option value="none">无</option><option value="fade-to-black">{edge === 'transitionIn' ? '从黑场淡入' : '淡出至黑场'}</option><option value="dissolve" disabled={!peer}>与{edge === 'transitionIn' ? '上一' : '下一'}片段溶解</option>{!['none', 'fade-to-black', 'dissolve'].includes(value.type) && <option value={value.type}>未接通的效果 · {value.type}</option>}</select></label>
      {value.type !== 'none' && <label className="sd-field">{label}时长（秒）<input aria-label={`画面${label}转场秒数`} type="number" min="0.05" step="0.05" max={Math.min(clip.duration, value.type === 'dissolve' ? peer?.duration ?? clip.duration : clip.duration)} value={value.duration} onChange={event => change({ type: value.type, duration: Math.max(0.05, Math.min(Number(event.target.value), clip.duration, value.type === 'dissolve' ? peer?.duration ?? clip.duration : clip.duration)) })} /></label>}
    </div>
  })}{(clip.transitionIn.type === 'dissolve' || clip.transitionOut.type === 'dissolve') && <p className="sd-hint">溶解在前一片段末尾引入下一片段的首帧；切点后开始播放，不改变成片时长。过渡区间仅支持这两个画面，标题仍可叠加。</p>}</>
}
