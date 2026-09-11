import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowRight, Check, Film, ImagePlus, Save, Scissors, Sparkles, Upload, X, Play, Pause, Volume2, VolumeX, Maximize, Pencil, Plus, Trash2 } from 'lucide-react'
import type { ProjectAggregate, ShotPlan } from './api'
import { allCues, currentScript, durationLabel, mediaUrl, shotMedia } from './studio-content'
import { StudioApi, type ReferenceCue, type StudioScope } from './studio-api'
import { AssetPicker, ReferenceChip, ReferenceUsagePicker, ShotReferenceEditor } from './ShotReferenceEditor'
import { ShotSecondsInput } from './ShotSecondsInput'
import { StudioEditorialPreview } from './StudioEditorialPreview'
import { studioAspectRatio } from './studio-aspect-ratio'
import { mergeCurrentChanges } from './studio-editor-merge'

export interface ShotDraft { shot: ShotPlan; base: ShotPlan; revision: number }

interface Props { drafts: Record<string, ShotDraft>; onDraft: (id: string, draft: ShotDraft | null) => void; onScript: (sceneId: string | null) => void; project: ProjectAggregate; selectedShotId: string | null; selectShot: (id: string) => void; onProject: (project: ProjectAggregate) => void; onAsk: (text: string, scope: StudioScope) => void; onScope: (scope: StudioScope) => void; onEdit: (shotId: string) => void; onAssets: () => void; onError: (error: unknown) => void }

export function ShotPlayer({ project, shot }: { project: ProjectAggregate; shot: ShotPlan }) {
  const source = shotMedia(project, shot)
  const video = useRef<HTMLVideoElement>(null)
  const frame = useRef<HTMLDivElement>(null)
  const [playing, setPlaying] = useState(false)
  const [position, setPosition] = useState(0)
  const [muted, setMuted] = useState(false)
  useEffect(() => { setPosition(0); setPlaying(false) }, [source.path, source.sourceIn])
  if (source.hasEditorial) return <StudioEditorialPreview projectId={project.document.project.id} shotId={shot.id} revision={Number(project.document.editorial_timeline?.revision ?? 0)} aspectRatio={studioAspectRatio(project.document.project.aspect_ratio)} />
  if (!source.path) return <div className="sd-video-placeholder"><Film size={28} /><strong>这个镜头还没有视频</strong><span>完善分镜和参考图后，交给 Agent 生成。</span></div>
  const toggle = () => { if (video.current?.paused) void video.current.play(); else video.current?.pause() }
  return <div className="sd-shot-playback" ref={frame}><video ref={video} key={`${source.path}-${source.sourceIn}`} className="sd-shot-video" playsInline preload="metadata" muted={muted} poster={mediaUrl(source.thumbnail)} src={mediaUrl(source.path)} aria-label={`镜头 ${shot.sequence} 视频`} onClick={toggle}
    onLoadedMetadata={event => { event.currentTarget.currentTime = source.sourceIn }}
    onPlay={event => { const element = event.currentTarget; if (element.currentTime < source.sourceIn || element.currentTime >= source.sourceIn + source.duration - 0.05) element.currentTime = source.sourceIn; setPlaying(true) }}
    onPause={() => setPlaying(false)}
    onTimeUpdate={event => { const element = event.currentTarget; setPosition(Math.max(0, Math.min(source.duration, element.currentTime - source.sourceIn))); if (element.currentTime >= source.sourceIn + source.duration) element.pause() }} />
    <div className="sd-shot-playback-controls"><button aria-label={playing ? '暂停镜头' : '播放镜头'} onClick={toggle}>{playing ? <Pause size={17} /> : <Play size={17} />}</button><span>{position.toFixed(1)} / {source.duration.toFixed(1)} 秒</span><input type="range" aria-label="镜头播放位置" min={0} max={source.duration} step={0.01} value={position} onChange={event => { const next = Number(event.target.value); setPosition(next); if (video.current) video.current.currentTime = source.sourceIn + next }} /><button aria-label={muted ? '开启镜头声音' : '关闭镜头声音'} onClick={() => setMuted(value => !value)}>{muted ? <VolumeX size={16} /> : <Volume2 size={16} />}</button><button aria-label="全屏预览镜头" onClick={() => void frame.current?.requestFullscreen()}><Maximize size={16} /></button></div></div>
}

export function StudioShots({ drafts, onDraft, onScript, project, selectedShotId, selectShot, onProject, onAsk, onScope, onEdit, onAssets, onError }: Props) {
  const shots = project.document.shots
  const selected = shots.find(shot => shot.id === selectedShotId) ?? shots[0]
  const draft = selected ? drafts[selected.id] ?? null : null
  const setDraft = (next: ShotDraft | null | ((value: ShotDraft | null) => ShotDraft | null)) => {
    if (selected) onDraft(selected.id, typeof next === 'function' ? next(draft) : next)
  }
  const [saving, setSaving] = useState(false)
  const [picker, setPicker] = useState<ReferenceCue | 'new' | null>(null)
  const [purpose, setPurpose] = useState('整镜参考')
  const [usage, setUsage] = useState<NonNullable<ReferenceCue['usage']>>('GENERATION')
  const [pickerBeat, setPickerBeat] = useState<number | null>(null)
  const [invalidTimes, setInvalidTimes] = useState<Set<string>>(() => new Set())
  const timingValidity = useCallback((id: string, valid: boolean) => setInvalidTimes(previous => {
    if (previous.has(id) === !valid) return previous
    const next = new Set(previous)
    if (valid) next.delete(id); else next.add(id)
    return next
  }), [])
  const videoFile = useRef<HTMLInputElement>(null)
  useEffect(() => { setPicker(null); setInvalidTimes(new Set()) }, [selected?.id, !!draft])
  const shot = draft?.shot ?? selected
  if (!shot) return <div className="sd-empty-page"><div className="sd-start-orb"><Film size={28} /></div><h1>从故事，到镜头。</h1><p>每个镜头都包含分镜设计、使用的素材和生成视频。先让 Agent 将剧本转成镜头。</p><button className="sd-button sd-primary" onClick={() => onAsk('把当前剧本设计为完整分镜。每个镜头写清楚逐时段画面、动作、运镜和所用资产。', { kind: 'episode', label: '当前集' })}><Sparkles size={16} />设计完整分镜</button></div>
  const scope: StudioScope = { kind: 'shot', id: shot.id, label: `镜头 ${String(shot.sequence).padStart(2, '0')}` }
  const source = shotMedia(project, shot)
  const script = currentScript(project)
  const scene = script?.structured_scenes.find(item => item.id === shot.scene_id)
  const editing = Boolean(draft)
  const update = (change: (shot: ShotPlan) => ShotPlan) => {
    if (draft) setDraft({ ...draft, shot: change(structuredClone(draft.shot)) })
  }
  const field = (key: 'narrative_purpose' | 'framing' | 'audio_intent' | 'transition_out', value: string) => update(current => ({ ...current, [key]: value }))
  const updateDialogue = (current: ShotPlan) => {
    const ids = (current.timeline_beats ?? []).flatMap(beat => beat.dialogue_line_ids ?? [])
    current.dialogue_line_ids = ids.filter((id, index) => id !== ids[index - 1])
    return current
  }
  const changeDuration = (seconds: number) => update(current => {
    const ratio = seconds / current.duration_seconds
    const beats = current.timeline_beats ?? []
    for (const [index, beat] of beats.entries()) {
      beat.start_seconds = index === 0 ? 0 : beats[index - 1].end_seconds
      beat.end_seconds = index === beats.length - 1 ? seconds : Number((beat.end_seconds * ratio).toFixed(6))
    }
    current.duration_seconds = seconds
    return current
  })
  const changeBoundary = (index: number, seconds: number) => update(current => {
    const beats = current.timeline_beats ?? []
    if (index > 0) beats[index - 1].end_seconds = seconds
    if (index < beats.length) beats[index].start_seconds = seconds
    else current.duration_seconds = seconds
    return current
  })
  const save = async () => {
    if (!draft || invalidTimes.size) return false
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
    setSaving(true)
    try { const result = await StudioApi.saveShot(project.document.project.id, draft.shot, draft.revision); onProject(result.project); setDraft(null); return true } catch (error) { onError(error); return false } finally { setSaving(false) }
  }
  const changeText = (index: number | null, field: NonNullable<ReferenceCue['field']>, text: string, cues: ReferenceCue[]) => update(current => {
    if (index === null) {
      if (field === 'visual') current.visual = text
      if (field === 'camera') current.camera = text
      current.references = [...(current.references ?? []).filter(cue => cue.field !== field || cue.text_start == null), ...cues]
    } else {
      const beat = (current.timeline_beats ?? [])[index]
      if (field === 'performance') beat.performance = text || null; else beat[field] = text
      beat.references = [...(beat.references ?? []).filter(cue => cue.field !== field || cue.text_start == null), ...cues]
    }
    return current
  })
  const conflict = draft && selected.revision !== draft.revision
  const invalidText = (shot.timeline_beats ?? []).some(beat => !beat.visual.trim() || !beat.action.trim() || !beat.camera.trim() || !beat.framing.trim())
  const changedFields: [keyof ShotPlan, string][] = [['narrative_purpose', '叙事目的'], ['duration_seconds', '设计时长'], ['visual', '整体画面'], ['framing', '景别'], ['camera', '运镜'], ['timeline_beats', '分段描述'], ['references', '参考图片'], ['audio_intent', '声音设计'], ['continuity', '镜头衔接'], ['transition_out', '转场']]
  const changes = draft ? changedFields.filter(([key]) => JSON.stringify(draft.shot[key]) !== JSON.stringify(draft.base[key])).map(([, label]) => label) : []
  const cannotSave = saving || !!conflict || !!invalidTimes.size || invalidText || !changes.length
  const askSaved = (message: string) => { if (!draft) onAsk(message, scope); else void save().then(saved => { if (saved) onAsk(message, scope) }) }
  return <div className={`sd-shots-page ${editing ? 'sd-shot-is-editing' : 'sd-shot-is-reading'}`}>
    <header className="sd-page-header sd-compact-header"><div><span className="sd-eyebrow">SHOTS</span><h1>把故事，变成可见的画面。</h1><p>{shots.length} 个镜头 · 设计时长 {durationLabel(shots.reduce((total, item) => total + item.duration_seconds, 0))}</p></div><button className="sd-button" onClick={() => onAsk('检查并补齐本集镜头所需的参考素材，将具体图片绑定到对应镜头文案。', { kind: 'episode', label: '当前集' })}><Sparkles size={15} />补齐镜头素材</button></header>
    <div className="sd-shots-layout"><aside className="sd-shot-list" aria-label="本集镜头">{shots.map(item => { const media = shotMedia(project, item); return <button className={item.id === shot.id ? 'is-active' : ''} key={item.id} onClick={() => { selectShot(item.id); onScope({ kind: 'shot', id: item.id, label: `镜头 ${item.sequence}` }) }}><div className="sd-shot-thumb">{media.thumbnail ? <img src={mediaUrl(media.thumbnail)} alt={`镜头 ${item.sequence} 缩略图`} loading="lazy" /> : <Film size={20} />}<span>{durationLabel(item.duration_seconds)}</span></div><div><strong>镜头 {String(item.sequence).padStart(2, '0')}</strong>{media.path && <Check size={12} />}<p>{item.narrative_purpose || item.visual}</p></div></button>})}</aside>
    <main className="sd-shot-detail" aria-busy={saving} key={shot.id} onFocus={() => onScope(scope)}>
      <div className="sd-shot-heading">
        <div><span className="sd-eyebrow">{scene?.heading || '当前集'}</span><h2>镜头 {String(shot.sequence).padStart(2, '0')}</h2></div>
        <div className="sd-shot-heading-actions">
          {draft ? <><button className="sd-button" disabled={saving} onClick={() => setDraft(null)}>取消</button><button className="sd-button sd-primary" disabled={cannotSave} onClick={() => void save()}><Save size={14} />{saving ? '保存中…' : '保存分镜'}</button></> : <button className="sd-button sd-primary" onClick={() => setDraft({ shot: structuredClone(selected), base: structuredClone(selected), revision: selected.revision })}><Pencil size={14} />编辑分镜</button>}
          <button className="sd-button" disabled={!!draft && cannotSave} onClick={() => askSaved('')}><Sparkles size={14} />{draft ? '保存并交给 Agent' : '让 Agent 修改'}</button>
        </div>
      </div>
      {draft && <p className="sd-inline-notice sd-draft-notice" role="status">{changes.length ? `待保存：${changes.join('、')}。` : '编辑分镜 · 尚未修改字段。'}切换内容会保留草稿，保存后用于后续生成。{invalidText && <span>请填写每段的画面、动作、运镜和景别。</span>}</p>}
      <div className="sd-shot-timing">
        <ShotSecondsInput id={`${shot.id}-duration`} label="设计时长（秒）" value={shot.duration_seconds} min={0.1} readOnly={!editing} onChange={changeDuration} onValidityChange={timingValidity} />
        <p>{editing ? '修改设计时长会按比例调整分段边界，不改变已有视频。' : '分镜中的时间表示拍摄设计；视频裁切在“剪辑镜头”中调整。'}</p>
      </div>
      <div className="sd-shot-duration-facts"><span>本镜素材<strong>{source.materialDuration == null ? '尚未生成' : `${Number(source.materialDuration.toFixed(3))} 秒`}</strong></span><span>成片采用<strong>{source.adoptedDuration == null ? '尚未采用' : `${Number(source.adoptedDuration.toFixed(3))} 秒`}</strong></span>{source.occurrenceCount > 1 && <span>成片中有 {source.occurrenceCount} 处使用；当前预览第一处，剪辑时可选择。</span>}</div>
      {conflict && <p className="sd-inline-notice">镜头刚有更新，你的编辑仍保留。<button onClick={() => setDraft(null)}>载入最新镜头</button><button onClick={() => { if (draft) setDraft({ shot: mergeCurrentChanges(draft.base, draft.shot, selected), base: structuredClone(selected), revision: selected.revision }) }}>合并最新内容，保留我的改动</button></p>}
<div className={`sd-shot-player ${source.path ? '' : 'sd-shot-waiting'}`}><ShotPlayer project={project} shot={shot} /><div className="sd-video-actions"><span>{source.path ? source.take?.freshness === 'STALE' ? '已有视频 · 可按新设计更新' : '本镜视频' : '待生成'}{source.take?.provider === 'user-import' ? ' · 用户导入' : ''}</span><div><button onClick={() => videoFile.current?.click()} disabled={saving || editing}><Upload size={14} />{source.path ? '替换视频' : '导入视频'}</button><button disabled={editing} onClick={() => onAsk(`根据镜头 ${shot.sequence} 当前分镜和已绑定的参考图片${source.path ? '重新' : ''}生成视频。保留已有可用内容。`, scope)}><Sparkles size={14} />{source.path ? '重新生成' : '生成视频'}</button><button onClick={() => onEdit(shot.id)} disabled={!source.path || editing}><Scissors size={14} />剪辑镜头</button></div></div></div>
      <input hidden ref={videoFile} type="file" accept="video/*" onChange={event => { const file = event.target.files?.[0]; if (!file) return; setSaving(true); const oldIds = new Set(project.document.assets.flatMap(asset => asset.versions.map(version => version.id))); void StudioApi.importMedia(project.document.project.id, file, 'REFERENCE').then(result => { const version = result.document.assets.flatMap(asset => asset.versions).find(version => version.media_type === 'VIDEO' && !oldIds.has(version.id)); if (!version) throw new Error('导入视频未找到'); return StudioApi.selectShotMedia(project.document.project.id, shot.id, version.id, selected.revision) }).then(onProject).catch(onError).finally(() => { setSaving(false); if (videoFile.current) videoFile.current.value = '' }) }} />
      <section className="sd-shot-design"><div className="sd-section-label"><h3>分镜设计</h3><span>{editing ? '输入 @ 引用图片 · 悬停可预览' : '只读 · 悬停 @ 可预览图片'}</span></div><DesignText label="叙事目的" value={shot.narrative_purpose} editing={editing} onChange={value => field('narrative_purpose', value)} />
        <ShotReferenceEditor readOnly={!editing} project={project} text={shot.visual} cues={(shot.references ?? []).filter(cue => cue.field === 'visual' && cue.text_start != null)} field="visual" label="整体画面" scope="整镜画面" onChange={(text, cues) => changeText(null, 'visual', text, cues)} />
        <div className="sd-design-fields"><DesignText label="整体景别" value={shot.framing} editing={editing} onChange={value => field('framing', value)} /><ShotReferenceEditor readOnly={!editing} project={project} text={shot.camera} cues={(shot.references ?? []).filter(cue => cue.field === 'camera' && cue.text_start != null)} field="camera" label="整体运镜" scope="整镜运镜" onChange={(text, cues) => changeText(null, 'camera', text, cues)} /></div>
        <div className="sd-whole-references"><span>整镜参考</span>{(shot.references ?? []).filter(cue => cue.text_start == null).map((cue, index) => <ReferenceChip key={cue.id ?? `${cue.alias}-${index}`} project={project} cue={cue} onClick={editing ? () => { setPickerBeat(null); setPicker(cue); setPurpose(cue.purpose); setUsage(cue.usage ?? 'GENERATION') } : undefined} />)}{editing && <button className="sd-reference-add" onClick={() => { setPickerBeat(null); setPicker('new'); setPurpose('整镜参考'); setUsage('GENERATION') }}><ImagePlus size={14} />添加参考</button>}<button className="sd-text-button" onClick={onAssets}>全部资产 <ArrowRight size={13} /></button></div>
        {editing && !shot.timeline_beats?.length && <button className="sd-button" onClick={() => update(current => ({ ...current, timeline_beats: [{ start_seconds: 0, end_seconds: current.duration_seconds, visual: current.visual, framing: current.framing, camera: current.camera, action: '', dialogue_line_ids: [...current.dialogue_line_ids], references: [] }] }))}><Plus size={14} />添加分段描述</button>}
        <div className="sd-beats">{(shot.timeline_beats ?? []).map((beat, index, beats) => <section className="sd-beat" key={`${shot.id}-${index}`}>
          <div className="sd-beat-time">
            <ShotSecondsInput id={`${shot.id}-beat-${index}-start`} label="开始（秒）" ariaLabel={`分段 ${index + 1} 开始时间（秒）`} value={beat.start_seconds}
              min={index ? beats[index - 1].start_seconds + 0.000001 : 0} max={beat.end_seconds - 0.000001} readOnly={!editing || index === 0}
              onChange={seconds => changeBoundary(index, seconds)} onValidityChange={timingValidity} />
            <ShotSecondsInput id={`${shot.id}-beat-${index}-end`} label="结束（秒）" ariaLabel={`分段 ${index + 1} 结束时间（秒）`} value={beat.end_seconds}
              readOnly={!editing} min={beat.start_seconds + 0.000001} max={beats[index + 1] ? beats[index + 1].end_seconds - 0.000001 : undefined}
              onChange={seconds => changeBoundary(index + 1, seconds)} onValidityChange={timingValidity} />
            <i />
          </div>
          <div className="sd-beat-content"><ShotReferenceEditor readOnly={!editing} project={project} text={beat.visual} cues={(beat.references ?? []).filter(cue => (cue.field ?? 'visual') === 'visual' && cue.text_start != null)} field="visual" label="画面" scope={`${beat.start_seconds}–${beat.end_seconds} 秒`} onChange={(text, cues) => changeText(index, 'visual', text, cues)} />
          <ShotReferenceEditor readOnly={!editing} project={project} text={beat.action} cues={(beat.references ?? []).filter(cue => cue.field === 'action' && cue.text_start != null)} field="action" label="动作" scope={`${beat.start_seconds}–${beat.end_seconds} 秒`} onChange={(text, cues) => changeText(index, 'action', text, cues)} />
          <ShotReferenceEditor readOnly={!editing} project={project} text={beat.camera} cues={(beat.references ?? []).filter(cue => cue.field === 'camera' && cue.text_start != null)} field="camera" label="运镜" scope={`${beat.start_seconds}–${beat.end_seconds} 秒`} onChange={(text, cues) => changeText(index, 'camera', text, cues)} />
          <ShotReferenceEditor readOnly={!editing} project={project} text={beat.performance ?? ''} cues={(beat.references ?? []).filter(cue => cue.field === 'performance' && cue.text_start != null)} field="performance" label="表演" scope={`${beat.start_seconds}–${beat.end_seconds} 秒`} onChange={(text, cues) => changeText(index, 'performance', text, cues)} />
          <div className="sd-beat-meta sd-design-fields">{(['framing', 'emotion', 'sound'] as const).map(key => <DesignText key={key} label={{ framing: '景别', emotion: '情绪', sound: '声音' }[key]} value={beat[key] ?? ''} editing={editing} onChange={value => update(current => { const item = current.timeline_beats![index]; if (key === 'framing') item.framing = value; else item[key] = value || null; return current })} />)}</div>
          <div className="sd-beat-dialogue">{(beat.dialogue_line_ids ?? []).map(id => { const line = script?.lines.find(item => item.id === id); return line ? <blockquote key={id}><strong>{line.speaker}</strong>{line.spoken_text}</blockquote> : null })}
            {editing && <details><summary>选择本段对白</summary>{(script?.lines ?? []).filter(line => !scene || scene.line_ids.includes(line.id)).map(line => <label className="sd-checkbox" key={line.id}><input type="checkbox" checked={(beat.dialogue_line_ids ?? []).includes(line.id)} onChange={event => update(current => { const item = current.timeline_beats![index]; item.dialogue_line_ids = event.target.checked ? [...(item.dialogue_line_ids ?? []), line.id] : (item.dialogue_line_ids ?? []).filter(id => id !== line.id); return updateDialogue(current) })} /><span>{line.speaker}：{line.spoken_text}</span></label>)}</details>}
            <button className="sd-text-button" onClick={() => onScript(shot.scene_id ?? null)}>在剧本中编辑台词 <ArrowRight size={12} /></button>
          </div>
          {editing && <div className="sd-editor-actions"><button className="sd-text-button" disabled={beats.length >= 24} onClick={() => update(current => { const items = current.timeline_beats!; const item = items[index]; const middle = Number(((item.start_seconds + item.end_seconds) / 2).toFixed(6)); items.splice(index + 1, 0, { ...structuredClone(item), start_seconds: middle }); item.end_seconds = middle; return updateDialogue(current) })}><Plus size={12} />拆成两段描述</button>{beats.length > 1 && <button className="sd-text-button" onClick={() => update(current => { const items = current.timeline_beats!; const removed = items.splice(index, 1)[0]; if (index === 0) items[0].start_seconds = 0; else items[index - 1].end_seconds = removed.end_seconds; return updateDialogue(current) })}><Trash2 size={12} />删除这段描述</button>}</div>}

          {(beat.references ?? []).filter(cue => cue.text_start == null).map((cue, refIndex) => <ReferenceChip key={cue.id ?? `${cue.alias}-${refIndex}`} project={project} cue={cue} scope={`${beat.start_seconds}–${beat.end_seconds} 秒`} onClick={editing ? () => { setPickerBeat(index); setPicker(cue); setPurpose(cue.purpose); setUsage(cue.usage ?? 'GENERATION') } : undefined} />)}
        </div></section>)}</div>
        <details className="sd-continuity" open={editing || undefined}><summary>镜头衔接与声音</summary><DesignText label="声音设计" value={shot.audio_intent} editing={editing} onChange={value => field('audio_intent', value)} />{(Object.keys(shot.continuity) as (keyof ShotPlan['continuity'])[]).map(key => <DesignText key={key} label={{ character_positions: '人物位置', eyeline: '视线关系', prop_state_in: '开始时道具状态', prop_state_out: '结束时道具状态', action_in: '承接动作', action_out: '离场动作' }[key]} value={shot.continuity[key]} editing={editing} onChange={value => update(current => { current.continuity[key] = value; return current })} />)}<DesignText label="转场" value={shot.transition_out} editing={editing} onChange={value => field('transition_out', value)} /></details>
        {!allCues(shot).length && <p className="sd-hint">尚未选择参考图片。点击“编辑分镜”添加，或让 Agent 根据分镜绑定。</p>}
      </section>
      {editing && picker && <div className="sd-modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) setPicker(null) }}><div className="sd-reference-modal"><div className="sd-modal-heading"><h3>{picker === 'new' ? '添加整镜参考' : '更换参考图片'}</h3><button className="sd-icon" onClick={() => setPicker(null)} aria-label="关闭参考图片"><X size={18} /></button></div><label className="sd-field">用途<input value={purpose} onChange={event => setPurpose(event.target.value)} /></label><ReferenceUsagePicker value={usage} onChange={setUsage} /><AssetPicker project={project} onClose={() => setPicker(null)} onSelect={(group, version) => { update(current => { const cue: ReferenceCue = { ...(picker === 'new' ? {} : picker), id: picker === 'new' ? crypto.randomUUID() : picker.id, subject_asset_id: group.id, asset_version_id: version.id, alias: `${group.name.replace(/[^\p{L}\p{N}_-]/gu, '_')}_${version.id.slice(-5)}`, required_tags: [], purpose: purpose.trim() || '整镜参考', usage }; const owner = pickerBeat === null ? current : current.timeline_beats![pickerBeat]; owner.references = picker === 'new' ? [...(owner.references ?? []), cue] : (owner.references ?? []).map(item => item.id === picker.id ? cue : item); return current }); setPicker(null) }} />{picker !== 'new' && <button className="sd-button sd-danger" onClick={() => { update(current => { const owner = pickerBeat === null ? current : current.timeline_beats![pickerBeat]; owner.references = (owner.references ?? []).filter(item => item.id !== picker.id); return current }); setPicker(null) }}>解除这张图片的引用</button>}</div></div>}
    </main></div>
  </div>
}

function DesignText({ label, value, editing, onChange }: { label: string; value: string; editing: boolean; onChange: (value: string) => void }) {
  return <div className={`sd-design-text ${editing ? '' : 'sd-read-field'}`}><label>{label}</label>{editing ? <textarea aria-label={label} rows={Math.min(5, Math.max(2, Math.ceil(value.length / 48)))} value={value} onChange={event => onChange(event.target.value)} /> : <p>{value || '尚未填写'}</p>}</div>
}
