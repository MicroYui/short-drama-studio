import { Component, type ReactNode, useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, Check, Download, Film, Loader2, Plus, Redo2, Save, Scissors, Sparkles, Trash2, Undo2, X } from 'lucide-react'
import { EditingSettingsProvider } from '../../contexts/AppSettingsContext'
import { projectSchema, type Project, type TimelineClip } from '../../types/project-model'
import { createInitialEditorState, type TimelineGapSelection } from '../../views/editor/editor-state'
import { createEditorStore, EditorStoreProvider, useEditorActions, useEditorStore, type EditorStoreApi } from '../../views/editor/editor-store'
import { getEditorModel, updatedProject } from '../../views/editor/editor-project-bridging'
import { selectActiveTimeline, selectClips, selectCurrentTime, selectIsPlaying, selectTotalDuration } from '../../views/editor/editor-selectors'
import { ProgramMonitor } from '../../views/editor/ProgramMonitor'
import { VideoEditorTimelineEditingPanel } from '../../views/editor/VideoEditorTimelineEditingPanel'
import { usePlaybackEngine } from '../../views/editor/usePlaybackEngine'
import { usePlaybackAudioSync } from '../../views/editor/usePlaybackAudioSync'
import { BUILT_IN_PRESETS } from '../../lib/keyboard-shortcuts'
import { parseSrt, exportSrt } from '../../lib/srt'
import type { ProjectAggregate } from './api'
import { StudioApi, StudioApiError, type StudioScope, type StudioExport, type StudioEditorial } from './studio-api'
import { mergeEditorChanges } from './studio-editor-merge'
import { prepareEditorAgentScope, editorGapRequest } from './studio-editor-selection'
import { assetGroups, mediaTitle, mediaUrl } from './studio-content'

interface Props {
  project: ProjectAggregate
  focusShotId?: string
  exportOnOpen?: boolean
  onProject: (project: ProjectAggregate) => void
  onClose: () => void
  onAsk: (message: string, scope: StudioScope) => void
  onError: (error: unknown) => void
  onDirty: (dirty: boolean) => void
  onNotice?: (notice: string) => void
  onAgentScopeReader?: (reader: (() => Promise<StudioScope>) | null) => void
}

function saveFile(text: string, name: string, type = 'application/json') {
  const link = document.createElement('a')
  link.href = URL.createObjectURL(new Blob([text], { type }))
  link.download = name
  link.click()
  setTimeout(() => URL.revokeObjectURL(link.href), 2000)
}

export function StudioEditor(props: Props) {
  const [loaded, setLoaded] = useState<{ base: Project; revision: number; store: EditorStoreApi; shotScope: StudioEditorial['scope'] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [clipChoice, setClipChoice] = useState<string | undefined>(undefined)
  useEffect(() => {
    let active = true
    document.querySelectorAll<HTMLMediaElement>('video,audio').forEach(media => media.pause())
    setLoaded(null)
    const request = props.focusShotId ? StudioApi.shotEditorial(props.project.document.project.id, props.focusShotId, clipChoice) : StudioApi.editorial(props.project.document.project.id)
    void request.then(result => {
      const base = projectSchema.parse(result.project)
      if (active) setLoaded({ base, revision: result.revision, shotScope: result.scope, store: createEditorStore(createInitialEditorState(getEditorModel(base))) })
    }).catch(reason => { if (active) setError(String(reason)) })
    return () => { active = false }
  }, [props.project.document.project.id, props.focusShotId, clipChoice])
  if (!loaded) return <div className="sd-modal-backdrop"><div className="sd-small-modal"><button className="sd-icon" onClick={props.onClose} aria-label="关闭剪辑"><X size={18} /></button>{error ? <p>{error}</p> : <p><Loader2 className="sd-spin" />正在打开剪辑…</p>}</div></div>
  if (loaded.shotScope && loaded.shotScope.occurrences.length > 1 && !clipChoice) return <div className="sd-modal-backdrop"><div className="sd-small-modal"><h2>选择这个镜头的使用位置</h2><p className="sd-hint">这个镜头在成片中出现了多次。本次只修改你选择的一处。</p>{loaded.shotScope.occurrences.map((item, index) => <button className="sd-button" key={item.anchor_clip_id} onClick={() => setClipChoice(item.anchor_clip_id)}>第 {index + 1} 次 · 成片 {item.start_seconds.toFixed(2)}–{(item.start_seconds + item.duration_seconds).toFixed(2)} 秒</button>)}<button className="sd-button" onClick={props.onClose}>返回镜头</button></div></div>
  return <EditorBoundary onClose={props.onClose}><EditorStoreProvider store={loaded.store}><EditingSettingsProvider><EditorBody {...props} {...loaded} /></EditingSettingsProvider></EditorStoreProvider></EditorBoundary>
}

class EditorBoundary extends Component<{ onClose: () => void; children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null }
  static getDerivedStateFromError(error: unknown) { return { error: error instanceof Error ? error.message : String(error) } }
  render() { return this.state.error ? <div className="sd-modal-backdrop"><div className="sd-small-modal"><h2>剪辑暂时无法显示</h2><p>{this.state.error}</p><p>已保存的项目仍在本机。</p><button className="sd-button" onClick={this.props.onClose}>返回作品</button></div></div> : this.props.children }
}

function EditorBody({ base, revision, store, shotScope, ...props }: Props & { base: Project; revision: number; store: EditorStoreApi; shotScope: StudioEditorial['scope'] }) {
  const id = props.project.document.project.id
  const actions = useEditorActions()
  const clips = useEditorStore(selectClips)
  const timeline = useEditorStore(selectActiveTimeline)
  const currentTime = useEditorStore(selectCurrentTime)
  const isPlaying = useEditorStore(selectIsPlaying)
  const duration = useEditorStore(selectTotalDuration)
  const selectedIds = useEditorStore(state => state.session.selection.clipIds)
  const selectedSubtitleId = useEditorStore(state => state.session.selection.subtitleId)
  const assets = useEditorStore(state => state.editorModel.assets)
  const files = new Map(assetGroups(props.project).flatMap(group => group.files.map(file => [file.version.id, `${group.name} · ${mediaTitle(props.project, file.version)}`] as const)))
  const usedAssets = new Set(clips.map(clip => clip.assetId))
  const visibleAssets = assets.filter(asset => asset.type !== 'adjustment' && (typeof asset.sourceAssetVersionId !== 'string' || files.has(asset.sourceAssetVersionId) || usedAssets.has(asset.id)))
  const assetLabel = (asset: typeof assets[number]) => (typeof asset.sourceAssetVersionId === 'string' ? files.get(asset.sourceAssetVersionId) : null) || asset.prompt || '素材'
  const selected = clips.find(clip => selectedIds.has(clip.id))
  const selectedShot = props.project.document.shots.find(shot => shot.id === selected?.sourceShotId)
  const subtitle = timeline?.subtitles.find(cue => cue.id === selectedSubtitleId)
  const controlledAudio = clips.find(clip => clip.type === 'audio' && selected?.linkedClipIds?.includes(clip.id)) ?? selected
  const patchSelected = (patch: Partial<TimelineClip>) => { if (selected) actions.setTimelineClips(clips.map(clip => clip.id === selected.id || selected.linkedClipIds?.includes(clip.id) ? { ...clip, ...patch } : clip)) }
  const playbackTimeRef = useRef(currentTime)
  const revisionRef = useRef(revision)
  const needsAdoption = useRef(Boolean(shotScope && !shotScope.duration_seconds && clips.length))
  const savedRef = useRef(store.getState().state.editorModel)
  const pendingSave = useRef<Promise<number> | null>(null)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const conflictRef = useRef(false)
  const [saveStatus, setSaveStatus] = useState('已保存')
  const [saveError, setSaveError] = useState<string | null>(null)
  const [exportDialog, setExportDialog] = useState(Boolean(props.exportOnOpen))
  const [exporting, setExporting] = useState<StudioExport | null>(null)
  const [size, setSize] = useState(props.project.document.project.aspect_ratio === '9:16' ? '720x1280' : props.project.document.project.aspect_ratio === '1:1' ? '1080x1080' : '1280x720')
  const [includeSubtitles, setIncludeSubtitles] = useState(true)
  const [bladeShiftHeld, setBladeShiftHeld] = useState(false)
  const subtitleInput = useRef<HTMLInputElement>(null)
  const centerOnPlayheadRef = useRef(false)
  const selectedGapRef = useRef<{ trackIndex: number; startTime: number; endTime: number } | null>(null)
  const gapModeRef = useRef<'text-to-video' | 'image-to-video' | 'text-to-image' | null>(null)
  const clearGapRef = useRef<() => void>(() => {})
  const closeGapRef = useRef<() => void>(() => {})
  const timelineRef = useRef<HTMLDivElement | null>(null)
  const trackContainerRef = useRef<HTMLDivElement | null>(null)
  const trackHeadersRef = useRef<HTMLDivElement | null>(null)
  const rulerRef = useRef<HTMLDivElement | null>(null)
  const markerOrigin = useRef<'timeline' | 'scrubbar' | null>(null)
  const fitRef = useRef<() => void>(() => {})
  const [, setDraggingMarker] = useState<'timelineIn' | 'timelineOut' | null>(null)
  const kbLayout = BUILT_IN_PRESETS[0].layout
  usePlaybackEngine({ playbackTimeRef })
  usePlaybackAudioSync({ playbackTimeRef })
  useEffect(() => { if (!isPlaying) playbackTimeRef.current = currentTime }, [currentTime, isPlaying])

  const flush = useCallback((): Promise<number> => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
    if (pendingSave.current) return pendingSave.current
    if (conflictRef.current) return Promise.reject(new Error('请先处理剪辑保存冲突。当前编辑仍保留。'))
    const write = async () => {
      while (needsAdoption.current || revisionRef.current === 0 || savedRef.current !== store.getState().state.editorModel) {
        if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
        setSaveStatus('正在保存…')
        const snapshot = store.getState().state.editorModel
        const result = shotScope ? await StudioApi.saveShotTimeline(id, shotScope.shot_id, { project: updatedProject(base, snapshot) }, revisionRef.current, shotScope.anchor_clip_id)
          : await StudioApi.saveTimeline(id, { project: updatedProject(base, snapshot) }, revisionRef.current)
        needsAdoption.current = false
        if (result.notices && result.notices.length > 1) props.onNotice?.(result.notices.slice(1).join(' '))
        revisionRef.current = Number(result.project.document.editorial_timeline?.revision ?? revisionRef.current + 1)
        savedRef.current = snapshot
        props.onProject(result.project)
      }
      props.onDirty(false)
      setSaveStatus(shotScope ? '已保存并同步成片' : '已保存')
      return revisionRef.current
    }
    pendingSave.current = write().catch(reason => {
      setSaveStatus('保存未完成')
      setSaveError(reason instanceof Error ? reason.message : String(reason))
      if (reason instanceof StudioApiError && reason.status === 409) conflictRef.current = true
      throw reason
    }).finally(() => { pendingSave.current = null })
    return pendingSave.current
  }, [base, id, props.onProject, store, shotScope])

  const resolveConflict = async () => {
    const latest = shotScope ? await StudioApi.shotEditorial(id, shotScope.shot_id, shotScope.anchor_clip_id ?? undefined) : await StudioApi.editorial(id)
    const merged = projectSchema.parse(mergeEditorChanges(updatedProject(base, savedRef.current), updatedProject(base, store.getState().state.editorModel), projectSchema.parse(latest.project)))
    revisionRef.current = latest.revision
    conflictRef.current = false
    actions.loadEditorDocument(getEditorModel(merged))
    setSaveError(null)
    await flush()
  }

  useEffect(() => store.subscribe((next, previous) => {
    if (next.state.editorModel === previous.state.editorModel) return
    setSaveStatus('有修改')
    props.onDirty(true)
    if (saveTimer.current) clearTimeout(saveTimer.current)
    if (!shotScope) saveTimer.current = setTimeout(() => { void flush().catch(() => {}) }, 650)
  }), [store, flush, shotScope, props.onDirty])
  useEffect(() => () => { if (saveTimer.current) clearTimeout(saveTimer.current) }, [])
  useEffect(() => {
    const clip = store.getState().state.editorModel.timelines.flatMap(item => item.clips).find(item => Boolean(props.focusShotId) && item.type === 'video' && item.sourceShotId === props.focusShotId)
    if (clip) { actions.selectClip(clip.id); actions.setCurrentTime(clip.startTime); playbackTimeRef.current = clip.startTime }
    const timer = setTimeout(() => fitRef.current(), 120)
    return () => clearTimeout(timer)
  }, [actions, props.focusShotId, store])

  const close = () => { if (shotScope) props.onClose(); else void flush().then(props.onClose).catch(() => {}) }
  const saveAndClose = () => { void flush().then(() => { if (shotScope) props.onClose() }).catch(() => {}) }
  const prepareAgentScope = useCallback((clipId?: string, gap?: TimelineGapSelection): Promise<StudioScope> => {
    const shot = props.project.document.shots.find(item => item.id === shotScope?.shot_id)
    return prepareEditorAgentScope({
      readState: () => {
        const state = store.getState().state
        return { ...state, session: { ...state.session, selection: {
          ...state.session.selection, gap: selectedGapRef.current,
        } } }
      },
      save: async () => {
        const savedRevision = await flush()
        if (!shotScope) return { model: savedRef.current, revision: savedRevision }
        const current = await StudioApi.shotEditorial(id, shotScope.shot_id, shotScope.anchor_clip_id ?? undefined)
        return { model: getEditorModel(projectSchema.parse(current.project)), revision: current.revision, shotScope: current.scope }
      },
      shotLabel: shot ? `镜头 ${shot.sequence}` : undefined, clipId, gap,
    })
  }, [flush, id, shotScope, store, props.project.document.shots])
  useEffect(() => {
    props.onAgentScopeReader?.(() => prepareAgentScope())
    return () => props.onAgentScopeReader?.(null)
  }, [prepareAgentScope, props.onAgentScopeReader])
  const ask = (message: string, clip?: TimelineClip, gap?: TimelineGapSelection) => {
    if (clip) { clearGapRef.current(); actions.selectClip(clip.id) }
    if (gap) actions.setSelectedSubtitle()
    void prepareAgentScope(clip?.id, gap).then(scope => {
      if (shotScope) props.onClose()
      props.onAsk(message, scope)
    }).catch(props.onError)
  }
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Shift') setBladeShiftHeld(event.type === 'keydown')
      if (event.type !== 'keydown' || (event.target as HTMLElement)?.closest('input,textarea,select,[contenteditable=true]')) return
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') { event.preventDefault(); saveAndClose() }
      else if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') { event.preventDefault(); if (event.shiftKey) actions.redo(); else actions.undo() }
      else if (event.code === 'Space') { event.preventDefault(); actions.togglePlayPause() }
      else if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); if (subtitle) actions.deleteSubtitle(subtitle.id); else actions.deleteClips([...selectedIds]) }
    }
    const beforeUnload = (event: BeforeUnloadEvent) => { if (savedRef.current !== store.getState().state.editorModel) event.preventDefault() }
    window.addEventListener('keydown', key); window.addEventListener('keyup', key); window.addEventListener('beforeunload', beforeUnload)
    return () => { window.removeEventListener('keydown', key); window.removeEventListener('keyup', key); window.removeEventListener('beforeunload', beforeUnload) }
  }, [actions, selectedIds, subtitle, flush, store])
  useEffect(() => {
    if (!exporting || exporting.status !== 'running') return
    let active = true
    const timer = setInterval(() => { void StudioApi.exportStatus(id, exporting.id).then(async result => {
      if (!active) return
      setExporting(result)
      if (result.status !== 'running') props.onProject(await StudioApi.getProject(id))
    }).catch(props.onError) }, 1600)
    return () => { active = false; clearInterval(timer) }
  }, [exporting?.id, exporting?.status, id, props.onError, props.onProject])

  const beginExport = async () => {
    const savedRevision = await flush()
    const [width, height] = size.split('x').map(Number)
    setExporting(await StudioApi.exportTimeline(id, { expected_revision: savedRevision, width, height, fps: 24, subtitles: includeSubtitles }))
    setExportDialog(false)
  }
  const addSubtitle = () => {
    let trackIndex = timeline?.tracks.findIndex(track => track.type === 'subtitle') ?? -1
    if (trackIndex < 0) { trackIndex = timeline?.tracks.length ?? 0; actions.addSubtitleTrack() }
    actions.addSubtitle({ trackIndex, text: '新字幕', startTime: currentTime, endTime: currentTime + 3 })
  }
  const saveSrt = () => saveFile(exportSrt(timeline?.subtitles ?? []), `${base.name}.srt`, 'text/plain')
  const noGeneration = { generate: async () => ask('根据剪辑中所需内容生成并放入当前时间线。'), generateImage: async () => ask('为当前剪辑补充图片。'), videoPath: null, imagePath: null, isGenerating: false, progress: 0, statusMessage: '', cancel: () => {}, reset: () => {}, error: null }
  return <div className="sd-editor-modal" aria-busy={saveStatus === '正在保存…'} role="region" aria-label={shotScope ? '镜头剪辑' : '本集剪辑'}>
    <header className="sd-editor-toolbar"><button className="sd-button" onClick={close}><ArrowLeft size={15} />{shotScope ? '取消并返回镜头' : '返回作品'}</button><h2>{props.project.document.project.episode_title} · {props.focusShotId ? `镜头 ${props.project.document.shots.find(item => item.id === props.focusShotId)?.sequence ?? ''} 剪辑` : '成片剪辑'}</h2><button className="sd-button sd-editor-agent-button" onClick={() => ask('')}><Sparkles size={14} />制作助手</button><span aria-live="polite">{saveStatus}</span><button className="sd-icon" aria-label="撤销" onClick={actions.undo}><Undo2 size={16} /></button><button className="sd-icon" aria-label="重做" onClick={actions.redo}><Redo2 size={16} /></button><button className={`sd-button ${shotScope ? 'sd-primary' : ''}`} onClick={saveAndClose}><Save size={14} />{shotScope ? needsAdoption.current ? '保存并加入成片' : '保存镜头剪辑' : '保存'}</button>{!shotScope && <button className="sd-button sd-primary" disabled={exporting?.status === 'running'} onClick={() => setExportDialog(true)}><Download size={14} />导出</button>}</header>
    {shotScope && <div className="sd-editor-scope-note">只编辑这个镜头 · 从 0 秒开始 · 当前 {duration.toFixed(2)} 秒。保存后同步到成片，后续镜头按时长变化顺移；取消不保存本次修改。</div>}
    {saveError && <div className="sd-inline-error">{saveError}{conflictRef.current ? <button onClick={() => void resolveConflict().catch(props.onError)}>保留我的修改，合并保存</button> : <button onClick={() => { setSaveError(null); void flush().catch(() => {}) }}>重试保存</button>}<button onClick={() => saveFile(JSON.stringify(updatedProject(base, store.getState().state.editorModel), null, 2), '当前剪辑.json')}>保存本地副本</button></div>}
    <div className="sd-editor-body"><div className="sd-editor-preview"><aside className="sd-editor-library"><h3>{shotScope ? '本镜素材' : '本集素材'}</h3><p className="sd-hint">点击添加到播放头</p>{visibleAssets.map(asset => <button key={asset.id} data-asset-id={asset.id} title={`添加 ${assetLabel(asset)}`} onClick={() => { actions.insertAssetsToTimeline({ assets: [asset], startTime: currentTime }); if (shotScope) actions.setTimelineClips(store.getState().state.editorModel.timelines.flatMap(item => item.clips).map(clip => ({ ...clip, sourceShotId: shotScope.shot_id }))) }}>{asset.smallThumbnailPath || asset.type === 'image' ? <img src={mediaUrl(asset.smallThumbnailPath || asset.path)} alt="" /> : <Film size={21} />}<span>{assetLabel(asset)}</span><Plus size={12} /></button>)}</aside><div className="sd-editor-monitor"><ProgramMonitor scaleSubtitles compositionAspectRatio={props.project.document.project.aspect_ratio === '9:16' ? 9 / 16 : props.project.document.project.aspect_ratio === '1:1' ? 1 : 16 / 9} playbackTimeRef={playbackTimeRef} setDraggingMarker={setDraggingMarker} kbLayout={kbLayout} /></div><aside className="sd-editor-properties">
      {subtitle ? <><h3>字幕</h3><label className="sd-field">文字<textarea value={subtitle.text} onChange={event => actions.setSubtitleText(subtitle.id, event.target.value)} /></label><label className="sd-field">开始（秒）<input type="number" min="0" step="0.1" value={subtitle.startTime} onChange={event => actions.setSubtitleStart(subtitle.id, Number(event.target.value))} /></label><label className="sd-field">结束（秒）<input type="number" step="0.1" min={subtitle.startTime + 0.1} value={subtitle.endTime} onChange={event => actions.setSubtitleEnd(subtitle.id, Number(event.target.value))} /></label><button className="sd-button" onClick={() => actions.deleteSubtitle(subtitle.id)}><Trash2 size={13} />删除字幕</button></> : selected ? <><h3>{selectedShot ? `镜头 ${String(selectedShot.sequence).padStart(2, '0')}` : selected.asset?.prompt || '选中的片段'}</h3><label className="sd-field">时间线起点（秒）<input aria-label="片段时间线起点" type="number" min="0" step="0.1" value={selected.startTime} onChange={event => patchSelected({ startTime: Math.max(0, Number(event.target.value)) })} /></label><label className="sd-field">{shotScope ? '镜头素材内入点（秒）' : '素材入点（秒）'}<input aria-label="素材入点" type="number" min="0" step="0.1" value={selected.trimStart - (shotScope ? selected.sourceWindowStart ?? 0 : 0)} onChange={event => patchSelected({ trimStart: Math.max(0, Number(event.target.value)) + (shotScope ? selected.sourceWindowStart ?? 0 : 0) })} /></label><label className="sd-field">{shotScope ? '成片采用时长（秒）' : '片段时长（秒）'}<input aria-label="片段时长" type="number" min="0.1" step="0.1" value={selected.duration} onChange={event => patchSelected({ duration: Math.max(0.1, Number(event.target.value)) })} /></label><label className="sd-field">音量 {Math.round((controlledAudio?.volume ?? 1) * 100)}%<input aria-label="片段音量" type="range" min="0" max="100" value={Math.round((controlledAudio?.volume ?? 1) * 100)} onChange={event => actions.setClipAudioLevel(selected.id, Number(event.target.value) / 100)} /></label><label className="sd-checkbox"><input type="checkbox" checked={controlledAudio?.muted ?? false} onChange={event => actions.setClipAudioMuted(selected.id, event.target.checked)} />静音</label><div className="sd-editor-actions"><button className="sd-button" onClick={() => actions.splitClipsAtTime([...selectedIds], currentTime)}><Scissors size={13} />在播放头拆分</button><button className="sd-button" onClick={() => actions.deleteClips([...selectedIds])}><Trash2 size={13} />移除</button></div><button className="sd-text-button" onClick={() => ask('修改这个镜头的视频内容，按我接下来的要求处理，并保留已有剪辑。', selected)}><Sparkles size={13} />交给 Agent 修改画面</button></> : <><h3>剪辑属性</h3><p className="sd-hint">选择时间线中的片段或字幕，编辑其内容。拖动片段两侧裁切，拖到其他轨道调整组接。</p></>}
      <div className="sd-editor-actions"><button className="sd-button" onClick={addSubtitle}><Plus size={13} />添加字幕</button><button className="sd-button" onClick={() => actions.addTrack('audio')}>添加声音轨</button></div></aside></div>
      <div className="sd-editor-timeline"><VideoEditorTimelineEditingPanel agentMode singleTimeline={!!shotScope} onAgentGap={gap => ask(editorGapRequest(gap, !!shotScope), undefined, gap)} currentProjectId={id} playbackTimeRef={playbackTimeRef} centerOnPlayheadRef={centerOnPlayheadRef} getMinZoom={() => Math.min(0.5, Math.max(0.01, (trackContainerRef.current?.clientWidth ?? 600) / (Math.max(1, duration) * 100)))} canUseIcLora={false} handleICLoraClip={clip => ask('调整这个镜头的画面风格。', clip)} kbLayout={kbLayout} handleExportTimelineXml={() => saveFile(JSON.stringify(updatedProject(base, store.getState().state.editorModel), null, 2), `${base.name}-剪辑.json`)} subtitleFileInputRef={subtitleInput} handleImportSrt={event => { const file = event.target.files?.[0]; if (file) void file.text().then(text => actions.importSrtCues(parseSrt(text))).catch(props.onError); event.target.value = '' }} handleExportSrt={saveSrt} gapGenerationApi={noGeneration} selectedGapRefBridge={selectedGapRef} gapGenerateModeRefBridge={gapModeRef} clearSelectedGapRefBridge={clearGapRef} closeSelectedGapRefBridge={closeGapRef} timelineRefBridge={timelineRef} trackContainerRefBridge={trackContainerRef} trackHeadersRefBridge={trackHeadersRef} rulerScrollRefBridge={rulerRef} markerDragOriginRef={markerOrigin} setDraggingMarker={setDraggingMarker} bladeShiftHeld={bladeShiftHeld} fitToViewRef={fitRef} onRevealAsset={assetId => { const button = [...document.querySelectorAll<HTMLButtonElement>('.sd-editor-library button[data-asset-id]')].find(item => item.dataset.assetId === assetId); button?.scrollIntoView({ block: 'nearest' }); button?.focus() }} onCreateVideoFromImage={clip => ask('以这个片段引用的图片生成视频。', clip)} onCaptureFrameForVideo={clip => ask('从这个片段提取合适画面作为参考，再生成相邻镜头。', clip)} onCreateVideoFromAudio={clip => ask('按这个声音片段设计并生成对应画面。', clip)} handleRegenerate={(_assetId, clipId) => ask('重新生成这个镜头，保留当前剪辑。', clips.find(clip => clip.id === clipId))} handleRetakeClip={clip => ask('根据我的要求重新生成这个镜头的指定部分。', clip)} handleCancelRegeneration={() => {}} isRegenerating={false} regenProgress={0} /></div></div>
    {exporting && <div className="sd-export-status" aria-live="polite">{exporting.status === 'running' ? <Loader2 size={15} className="sd-spin" /> : exporting.status === 'completed' ? <Check size={15} /> : <X size={15} />}<span>{exporting.progress}{exporting.error ? `：${exporting.error}` : ''}</span>{exporting.video_path && <button className="sd-button" onClick={() => void StudioApi.downloadMedia(id, exporting.video_path!, `${base.name}.mp4`).catch(props.onError)}><Download size={14} />保存视频</button>}</div>}
    {exportDialog && <div className="sd-modal-backdrop"><div className="sd-small-modal"><header className="sd-modal-heading"><h2>导出本集成片</h2><button className="sd-icon" aria-label="关闭导出" onClick={() => setExportDialog(false)}><X size={18} /></button></header><label className="sd-field">尺寸<select value={size} onChange={event => setSize(event.target.value)}><option value="1280x720">横屏 · 720p</option><option value="1920x1080">横屏 · 1080p</option><option value="720x1280">竖屏 · 720p</option><option value="1080x1920">竖屏 · 1080p</option><option value="1080x1080">方形 · 1080p</option></select></label><label className="sd-checkbox"><input type="checkbox" checked={includeSubtitles} onChange={event => setIncludeSubtitles(event.target.checked)} />包含字幕</label><p className="sd-hint">MP4 · 24 fps · H.264。使用当前保存的时间线，原始素材保留。</p><button className="sd-button sd-primary" onClick={() => void beginExport().catch(props.onError)}><Download size={15} />开始导出</button></div></div>}
  </div>
}
