import { Component, type ReactNode, useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, Download, Film, Loader2, Plus, Redo2, Save, Scissors, Sparkles, Trash2, Undo2, X } from 'lucide-react'
import { EditingSettingsProvider } from '../../contexts/AppSettingsContext'
import { projectSchema, type Project, type TimelineClip } from '../../types/project-model'
import { createInitialEditorState, type EditorModel, type TimelineGapSelection } from '../../views/editor/editor-state'
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
import { StudioApi, StudioApiError, type StudioScope, type StudioExport, type StudioEditorial, type StudioMutation } from './studio-api'
import { mergeEditorChanges } from './studio-editor-merge'
import { applyEditorRefresh } from './studio-editor-refresh'
import { StudioExportStatus } from './StudioExportStatus'
import { StudioColorProperties, StudioTitleProperties, StudioTransitionProperties } from './StudioVisualProperties'
import { addStudioTitle } from './studio-visual-editing'
import { studioExportRetryRequest } from './studio-export-status'
import { prepareEditorAgentScope, editorGapRequest } from './studio-editor-selection'
import { assetGroups, mediaTitle, mediaUrl } from './studio-content'
import { defaultStudioExportSize, studioAspectRatio, studioExportDimensions, studioSourceExportSize } from './studio-aspect-ratio'
import { createEditorDraftWriter, editorDraftKey, editorDraftStorage, requireExistingDraftTimelines, type EditorDraft } from './studio-editor-drafts'
import { createEditorSaveQueue, EditorSessionClosed, locateEditorState, locateRecoveredEditorState, savedShotUseSelection } from './studio-editor-save'
import './studio-editor-recovery.css'

const EXPORT_SIZE_PRESETS = [
  { value: '1280x720', label: '横屏 · 720p' },
  { value: '1920x1080', label: '横屏 · 1080p' },
  { value: '720x1280', label: '竖屏 · 720p' },
  { value: '1080x1920', label: '竖屏 · 1080p' },
  { value: '1080x1080', label: '方形 · 1080p' },
]

interface Props {
  project: ProjectAggregate
  focusShotId?: string
  exportOnOpen?: boolean
  initialSelection?: { timelineId: string; clipId?: string; startSeconds?: number }
  onEditorialSaved?: (episodeId: string, revision: number) => void
  onExportRecords: (episodeId: string, records: StudioExport[]) => void
  onProject: (project: ProjectAggregate) => void
  onClose: () => void
  onAsk: (message: string, scope: StudioScope) => void
  onError: (error: unknown) => void
  onDirty: (dirty: boolean) => void
  onNotice?: (notice: string) => void
  onAgentScopeReader?: (reader: (() => Promise<StudioScope>) | null) => void
}

interface LoadedEditor {
  base: Project
  revision: number
  store: EditorStoreApi
  shotScope: StudioEditorial['scope']
  defaultSubtitleFontFamily: string | null
  draftKey: string
  owner: string
  restoredDraft: EditorDraft | null
  recovery: EditorDraft | null
  recoveryNotice: string | null
  draftError: string | null
}

function initialEditorStore(project: Project, selection?: Props['initialSelection']): EditorStoreApi {
  return createEditorStore(locateEditorState(createInitialEditorState(getEditorModel(project)), selection))
}

function saveFile(text: string, name: string, type = 'application/json') {
  const link = document.createElement('a')
  link.href = URL.createObjectURL(new Blob([text], { type }))
  link.download = name
  link.click()
  setTimeout(() => URL.revokeObjectURL(link.href), 2000)
}

export function StudioEditor(props: Props) {
  const [loaded, setLoaded] = useState<LoadedEditor | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [clipChoice, setClipChoice] = useState<string | undefined>(undefined)
  const initialSelection = useRef(props.initialSelection)
  useEffect(() => {
    let active = true
    document.querySelectorAll<HTMLMediaElement>('video,audio').forEach(media => media.pause())
    setLoaded(null)
    setError(null)
    const request = props.focusShotId ? StudioApi.shotEditorial(props.project.document.project.id, props.focusShotId, clipChoice) : StudioApi.editorial(props.project.document.project.id)
    void request.then(async result => {
      const base = projectSchema.parse(result.project)
      const owner = crypto.randomUUID()
      const draftKey = editorDraftKey({ projectId: props.project.document.project.parent_project_id ?? props.project.document.project.id,
        episodeId: props.project.document.project.id, shotId: result.scope?.shot_id,
        timelineId: base.activeTimelineId, anchorClipId: result.scope?.anchor_clip_id })
      let recovery: EditorDraft | null = null
      let draftError: string | null = null
      if (!active) return
      if (!(result.scope && result.scope.occurrences.length > 1 && !clipChoice)) {
        try {
          recovery = await editorDraftStorage.claim(draftKey, owner)
          if (recovery && JSON.stringify(getEditorModel(recovery.current)) === JSON.stringify(getEditorModel(base))) {
            await editorDraftStorage.write(draftKey, owner, null)
            recovery = null
          }
        } catch (reason) { draftError = `本机草稿暂不可用：${reason instanceof Error ? reason.message : String(reason)}` }
      }
      if (active) setLoaded({ base, revision: result.revision, shotScope: result.scope, defaultSubtitleFontFamily: result.default_subtitle_font_family ?? null, draftKey, owner, recovery,
        draftError, recoveryNotice: null, restoredDraft: null, store: initialEditorStore(base, initialSelection.current) })
      else await editorDraftStorage.release(draftKey, owner).catch(() => {})
    }).catch(reason => { if (active) setError(String(reason)) })
    return () => { active = false }
  }, [props.project.document.project.id, props.focusShotId, clipChoice])
  if (!loaded) return <div className="sd-modal-backdrop"><div className="sd-small-modal"><button className="sd-icon" onClick={props.onClose} aria-label="关闭剪辑"><X size={18} /></button>{error ? <p>{error}</p> : <p><Loader2 className="sd-spin" />正在打开剪辑…</p>}</div></div>
  if (loaded.shotScope && loaded.shotScope.occurrences.length > 1 && !clipChoice) return <div className="sd-modal-backdrop"><div className="sd-small-modal"><h2>选择这个镜头的使用位置</h2><p className="sd-hint">这个镜头在成片中出现了多次。本次只修改你选择的一处。</p>{loaded.shotScope.occurrences.map((item, index) => <button className="sd-button" key={item.anchor_clip_id} onClick={() => setClipChoice(item.anchor_clip_id)}>第 {index + 1} 次 · 成片 {item.start_seconds.toFixed(2)}–{(item.start_seconds + item.duration_seconds).toFixed(2)} 秒</button>)}<button className="sd-button" onClick={props.onClose}>返回镜头</button></div></div>
  if (loaded.recovery) return <div className="sd-modal-backdrop"><div className="sd-small-modal" role="dialog" aria-label="恢复剪辑草稿"><h2>发现未保存的剪辑草稿</h2><p>{loaded.shotScope ? '这次镜头使用位置' : '本集成片'}的草稿已保留在本机。继续编辑后仍按当前保存方式处理。</p>{loaded.recovery.revision !== loaded.revision && <p>正式剪辑已有更新。恢复后可查看草稿，再明确合并保存。</p>}{error && <p className="sd-inline-error">{error}</p>}<div className="sd-editor-actions"><button className="sd-button sd-primary" onClick={() => { try { requireExistingDraftTimelines(loaded.recovery!, loaded.base); const restored = locateRecoveredEditorState(createInitialEditorState(getEditorModel(loaded.recovery!.current)), initialSelection.current); setLoaded({ ...loaded, store: createEditorStore(restored.state), recoveryNotice: restored.notice, restoredDraft: loaded.recovery, recovery: null }) } catch (reason) { setError(String(reason)) } }}>继续编辑草稿</button><button className="sd-button" onClick={() => { void editorDraftStorage.write(loaded.draftKey, loaded.owner, null).then(written => { if (!written) throw new Error('草稿已在另一个窗口打开。'); setLoaded(current => current?.owner === loaded.owner ? { ...current, recovery: null } : current) }).catch(reason => setError(String(reason))) }}>放弃草稿</button><button className="sd-button" onClick={() => saveFile(JSON.stringify(loaded.recovery!.current, null, 2), '未保存剪辑草稿.json')}>下载本地副本</button><button className="sd-button" onClick={props.onClose}>返回</button></div></div></div>
  return <EditorBoundary onClose={props.onClose}><EditorStoreProvider store={loaded.store}><EditingSettingsProvider><EditorBody key={loaded.owner} {...props} {...loaded} /></EditingSettingsProvider></EditorStoreProvider></EditorBoundary>
}

class EditorBoundary extends Component<{ onClose: () => void; children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null }
  static getDerivedStateFromError(error: unknown) { return { error: error instanceof Error ? error.message : String(error) } }
  render() { return this.state.error ? <div className="sd-modal-backdrop"><div className="sd-small-modal"><h2>剪辑暂时无法显示</h2><p>{this.state.error}</p><p>已保存的项目仍在本机。</p><button className="sd-button" onClick={this.props.onClose}>返回作品</button></div></div> : this.props.children }
}

function EditorBody({ base, revision, store, shotScope, draftKey, owner, restoredDraft, recoveryNotice, defaultSubtitleFontFamily: initialSubtitleFontFamily, draftError: initialDraftError, ...props }: Props & LoadedEditor) {
  const [defaultSubtitleFontFamily, setDefaultSubtitleFontFamily] = useState(initialSubtitleFontFamily)
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
  const baseRef = useRef(restoredDraft?.base ?? base)
  const revisionRef = useRef(restoredDraft?.revision ?? revision)
  const shotScopeRef = useRef(shotScope)
  const activeDraftKey = useRef(draftKey)
  const needsAdoption = useRef(Boolean(shotScope && !shotScope.duration_seconds && clips.length))
  const savedRef = useRef(restoredDraft ? getEditorModel(restoredDraft.base) : store.getState().state.editorModel)
  const retainedRef = useRef(store.getState().state.editorModel)
  const alive = useRef(true)
  const propsRef = useRef(props)
  propsRef.current = props
  const currentIncomingRevision = useRef(revision)
  currentIncomingRevision.current = Math.max(currentIncomingRevision.current, Number(props.project.document.editorial_timeline?.revision ?? 0))
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const conflictRef = useRef(Boolean(restoredDraft && restoredDraft.revision !== revision))
  const applyingRemote = useRef(false)
  const [saveStatus, setSaveStatus] = useState(restoredDraft ? '草稿已恢复，尚未保存' : '已保存')
  const [saveError, setSaveError] = useState<string | null>(conflictRef.current ? '正式剪辑已有更新。草稿仍保留，请明确合并后保存。' : null)
  const [saving, setSaving] = useState(false)
  const [closing, setClosing] = useState(false)
  const [draftError, setDraftError] = useState<string | null>(initialDraftError)
  const [draftStatus, setDraftStatus] = useState(restoredDraft ? '本机草稿已恢复' : '')
  const [libraryOpen, setLibraryOpen] = useState(false)
  const [exportDialog, setExportDialog] = useState(Boolean(props.exportOnOpen))
  const [exporting, setExporting] = useState<StudioExport | null>(null)
  const [exportBusy, setExportBusy] = useState(false)
  const [startingExport, setStartingExport] = useState(false)
  const [fps, setFps] = useState(24)
  const defaultExportSize = defaultStudioExportSize(props.project.document.project.aspect_ratio)
  const [size, setSize] = useState('source')
  const sourceExportSize = studioSourceExportSize(props.project.document.project.aspect_ratio, timeline, assets)
  const customExportSizes = [...new Set([sourceExportSize.size, defaultExportSize, size])].filter((value): value is string =>
    Boolean(value && value !== 'source' && !EXPORT_SIZE_PRESETS.some(preset => preset.value === value)
      && value.split('x').every(dimension => Number.isSafeInteger(Number(dimension)) && Number(dimension) >= 256)))
  const sourceExportHint = sourceExportSize.basis === 'unknown'
    ? '导出时读取当前采用的视频文件，确认实际尺寸。'
    : sourceExportSize.basis === 'fallback'
      ? `当前无可见视频，按本集画幅导出 ${sourceExportSize.size?.replace('x', ' × ')}。`
      : `按当前采用视频估计为 ${sourceExportSize.size?.replace('x', ' × ')}，最终尺寸以实际文件为准。`
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

  const writer = useRef(createEditorDraftWriter(value => editorDraftStorage.write(activeDraftKey.current, owner, value)))
  const persistDraft = useCallback(async () => {
    const snapshot = store.getState().state.editorModel
    const dirty = savedRef.current !== snapshot || needsAdoption.current
    const draft: EditorDraft | null = dirty ? { version: 1, revision: revisionRef.current, updatedAt: Date.now(),
      base: updatedProject(baseRef.current, savedRef.current), current: updatedProject(baseRef.current, snapshot) } : null
    if (alive.current && dirty) setDraftStatus('正在保留本机草稿…')
    try {
      await writer.current.put(draft)
      retainedRef.current = snapshot
      if (alive.current && snapshot === store.getState().state.editorModel) {
        setDraftError(null)
        setDraftStatus(dirty ? '草稿已保留在本机' : '')
      }
    } catch (reason) {
      if (alive.current) { setDraftStatus(''); setDraftError(`未能保留本机草稿：${reason instanceof Error ? reason.message : String(reason)}。当前编辑仍可继续，请保存或下载本地副本。`) }
    }
  }, [store])

  const queue = useRef<ReturnType<typeof createEditorSaveQueue<EditorModel, StudioMutation>> | null>(null)
  if (!queue.current) queue.current = createEditorSaveQueue({
    active: () => alive.current,
    dirty: () => needsAdoption.current || revisionRef.current === 0 || savedRef.current !== store.getState().state.editorModel,
    model: () => store.getState().state.editorModel,
    revision: () => revisionRef.current,
    write: (snapshot, expectedRevision) => {
      if (conflictRef.current) return Promise.reject(new Error('请先处理剪辑保存冲突。当前编辑仍保留。'))
      setSaveStatus('正在保存…')
      return shotScope ? StudioApi.saveShotTimeline(id, shotScope.shot_id, { project: updatedProject(baseRef.current, snapshot) }, expectedRevision, shotScopeRef.current?.anchor_clip_id)
        : StudioApi.saveTimeline(id, { project: updatedProject(baseRef.current, snapshot) }, expectedRevision)
    },
    acknowledge: async (snapshot, result) => {
      const adopted = needsAdoption.current
      needsAdoption.current = false
      revisionRef.current = Number(result.project.document.editorial_timeline?.revision ?? revisionRef.current + 1)
      savedRef.current = snapshot
      if (result.notices && result.notices.length > 1) propsRef.current.onNotice?.(result.notices.slice(1).join(' '))
      if (propsRef.current.onEditorialSaved) propsRef.current.onEditorialSaved(id, revisionRef.current)
      else if (revisionRef.current >= currentIncomingRevision.current) propsRef.current.onProject(result.project)
      await persistDraft()
      if (adopted && shotScope && alive.current) {
        // A first adoption replaces the preview's unadopted recovery identity
        // with the actual use, while later edits can stay open in this session.
        try {
          const latestProject = projectSchema.parse(result.project.document.editorial_timeline?.project)
          const adoptedUse = savedShotUseSelection(snapshot, latestProject, shotScope.shot_id)
          shotScopeRef.current = { ...shotScope, anchor_clip_id: adoptedUse.clipId ?? null }
          const nextKey = editorDraftKey({ projectId: propsRef.current.project.document.project.parent_project_id ?? id,
            episodeId: id, shotId: shotScope.shot_id, timelineId: adoptedUse.timelineId,
            anchorClipId: adoptedUse.clipId })
          if (nextKey !== activeDraftKey.current) {
            await writer.current.flush()
            const previousKey = activeDraftKey.current
            const relocation = editorDraftStorage.relocate(previousKey, nextKey, owner)
            activeDraftKey.current = nextKey
            try { await relocation }
            catch (reason) { activeDraftKey.current = previousKey; throw reason }
            await persistDraft()
          }
        } catch (reason) {
          if (alive.current) setDraftError(`镜头已保存；采用位置草稿同步未完成：${reason instanceof Error ? reason.message : String(reason)}。请保存后续修改或下载本地副本。`)
        }
      }
    },
    pending: setSaving,
  })
  const flush = useCallback((drain = true): Promise<number> => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
    if (conflictRef.current) return Promise.reject(new Error('请先处理剪辑保存冲突。当前编辑仍保留。'))
    return queue.current!.flush(drain).then(savedRevision => {
      if (!alive.current) throw new EditorSessionClosed()
      const dirty = savedRef.current !== store.getState().state.editorModel || needsAdoption.current
      propsRef.current.onDirty(dirty)
      setSaveError(null)
      setSaveStatus(dirty ? '已保存；后续修改尚未保存' : shotScope ? '已保存并同步成片' : '已保存')
      return savedRevision
    }).catch(reason => {
      if (alive.current && !(reason instanceof EditorSessionClosed)) {
        setSaveStatus('保存未完成')
        setSaveError(reason instanceof Error ? reason.message : String(reason))
        if (reason instanceof StudioApiError && reason.status === 409) conflictRef.current = true
      }
      throw reason
    })
  }, [shotScope])

  const installCurrent = useCallback((latest: StudioEditorial, project: Project, dirty = false) => {
    const model = getEditorModel(project)
    revisionRef.current = latest.revision
    setDefaultSubtitleFontFamily(latest.default_subtitle_font_family ?? null)
    baseRef.current = projectSchema.parse(latest.project)
    savedRef.current = dirty ? getEditorModel(baseRef.current) : model
    applyingRemote.current = true
    try { store.getState().setStateWithoutHistory(state => applyEditorRefresh(state, model, dirty)) }
    finally { applyingRemote.current = false }
    const state = store.getState().state
    playbackTimeRef.current = state.session.transport.currentTime
    selectedGapRef.current = state.session.selection.gap
    conflictRef.current = false
    setSaveError(null)
    props.onDirty(dirty)
    setSaveStatus(dirty ? '已合并，待保存' : '已同步最新剪辑')
    void persistDraft()
  }, [store, props.onDirty, persistDraft])

  const incomingRevision = Number(props.project.document.editorial_timeline?.revision ?? 0)
  useEffect(() => {
    if (incomingRevision <= revisionRef.current) return
    let active = true
    const refresh = async () => {
      // A content event can arrive before our own save response. Confirm that
      // response first so our successful write is not mistaken for a conflict.
      if (queue.current?.pending()) await queue.current.pending()!.catch(() => {})
      if (!active || incomingRevision <= revisionRef.current) return
      if (saveTimer.current) clearTimeout(saveTimer.current)
      conflictRef.current = true
      const latest = shotScope ? await StudioApi.shotEditorial(id, shotScope.shot_id, shotScopeRef.current?.anchor_clip_id ?? undefined) : await StudioApi.editorial(id)
      if (!active) return
      if (savedRef.current !== store.getState().state.editorModel || needsAdoption.current) {
        setSaveStatus('有新内容待合并')
        setSaveError('Agent 更新了剪辑。你的未保存修改仍保留，合并后可继续保存。')
        return
      }
      installCurrent(latest, projectSchema.parse(latest.project))
    }
    void refresh().catch(reason => {
      if (!active) return
      setSaveStatus('等待同步最新剪辑')
      setSaveError(`读取最新剪辑未完成：${reason instanceof Error ? reason.message : String(reason)}。当前编辑仍保留。`)
    })
    return () => { active = false }
  }, [incomingRevision, id, shotScope, store, installCurrent])

  const resolveConflict = async () => {
    const latest = shotScope ? await StudioApi.shotEditorial(id, shotScope.shot_id, shotScopeRef.current?.anchor_clip_id ?? undefined) : await StudioApi.editorial(id)
    if (!alive.current) return
    const latestProject = projectSchema.parse(latest.project)
    requireExistingDraftTimelines({ version: 1, revision: revisionRef.current, updatedAt: Date.now(),
      base: updatedProject(baseRef.current, savedRef.current), current: updatedProject(baseRef.current, store.getState().state.editorModel) }, latestProject)
    const merged = projectSchema.parse(mergeEditorChanges(updatedProject(baseRef.current, savedRef.current), updatedProject(baseRef.current, store.getState().state.editorModel), latestProject))
    installCurrent(latest, merged, true)
    await flush()
  }

  useEffect(() => store.subscribe((next, previous) => {
    if (applyingRemote.current || next.state.editorModel === previous.state.editorModel) return
    setSaveStatus(queue.current?.pending() ? '正在保存，仍有修改' : '有修改')
    props.onDirty(true)
    void persistDraft()
    if (saveTimer.current) clearTimeout(saveTimer.current)
    if (!shotScope && !conflictRef.current) saveTimer.current = setTimeout(() => { void flush().catch(() => {}) }, 650)
  }), [store, flush, shotScope, props.onDirty, persistDraft])
  useEffect(() => {
    alive.current = true
    if (restoredDraft) {
      propsRef.current.onDirty(true)
      void persistDraft()
      if (!shotScope && !conflictRef.current) saveTimer.current = setTimeout(() => { void flush().catch(() => {}) }, 650)
    }
    return () => {
      alive.current = false
      if (saveTimer.current) clearTimeout(saveTimer.current)
      void writer.current.flush().catch(() => {}).then(() => {
        // React StrictMode immediately reactivates the same mounted session.
        if (!alive.current) return editorDraftStorage.release(activeDraftKey.current, owner)
      }).catch(() => {})
    }
  }, [draftKey, owner, flush, persistDraft, restoredDraft, shotScope])
  useEffect(() => {
    const clip = store.getState().state.editorModel.timelines.flatMap(item => item.clips).find(item => Boolean(props.focusShotId) && item.type === 'video' && item.sourceShotId === props.focusShotId)
    if (clip && !props.initialSelection) { actions.selectClip(clip.id); actions.setCurrentTime(clip.startTime); playbackTimeRef.current = clip.startTime }
    const timer = setTimeout(() => fitRef.current(), 120)
    return () => clearTimeout(timer)
  }, [actions, props.focusShotId, store])

  const close = () => {
    if (closing || shotScope && queue.current?.pending()) return
    setClosing(true)
    const finish = shotScope
      ? writer.current.flush().catch(() => {}).then(() => editorDraftStorage.release(activeDraftKey.current, owner, true))
      : flush().then(() => writer.current.flush())
    void finish.then(() => { if (alive.current) propsRef.current.onClose() }).catch(reason => {
      if (alive.current) { setClosing(false); propsRef.current.onError(reason) }
    })
  }
  const saveAndClose = () => {
    if (queue.current?.pending() || closing) return
    void flush(!shotScope).then(() => {
      if (alive.current && shotScope && savedRef.current === store.getState().state.editorModel) propsRef.current.onClose()
    }).catch(() => {})
  }

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
        const current = await StudioApi.shotEditorial(id, shotScope.shot_id, shotScopeRef.current?.anchor_clip_id ?? undefined)
        if (!alive.current) throw new EditorSessionClosed()
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
      if (!alive.current) return
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
    const beforeUnload = (event: BeforeUnloadEvent) => {
      const model = store.getState().state.editorModel
      if (savedRef.current !== model && retainedRef.current !== model) event.preventDefault()
    }
    window.addEventListener('keydown', key); window.addEventListener('keyup', key); window.addEventListener('beforeunload', beforeUnload)
    return () => { window.removeEventListener('keydown', key); window.removeEventListener('keyup', key); window.removeEventListener('beforeunload', beforeUnload) }
  }, [actions, selectedIds, subtitle, flush, store])
  const beginExport = async () => {
    if (startingExport || exportBusy) return
    setStartingExport(true)
    try {
      const dimensions = studioExportDimensions(size)
      const savedRevision = await flush()
      setExporting(await StudioApi.exportTimeline(id, { expected_revision: savedRevision, ...dimensions, fps, subtitles: includeSubtitles }))
      setExportDialog(false)
    } finally { setStartingExport(false) }
  }
  const retryExport = async (record: StudioExport) => StudioApi.exportTimeline(id, studioExportRetryRequest(record, await flush()))
  const addSubtitle = () => {
    let trackIndex = timeline?.tracks.findIndex(track => track.type === 'subtitle') ?? -1
    if (trackIndex < 0) { trackIndex = timeline?.tracks.length ?? 0; actions.addSubtitleTrack() }
    actions.addSubtitle({ trackIndex, text: '新字幕', startTime: currentTime, endTime: currentTime + 3 })
  }
  const addTitle = () => {
    const titleDuration = duration > currentTime ? Math.min(3, duration - currentTime) : 3
    store.getState().setStateWithHistory(state => addStudioTitle(state, currentTime, titleDuration, shotScope?.shot_id))
  }
  const saveSrt = () => saveFile(exportSrt(timeline?.subtitles ?? []), `${base.name}.srt`, 'text/plain')
  const noGeneration = { generate: async () => ask('根据剪辑中所需内容生成并放入当前时间线。'), generateImage: async () => ask('为当前剪辑补充图片。'), videoPath: null, imagePath: null, isGenerating: false, progress: 0, statusMessage: '', cancel: () => {}, reset: () => {}, error: null }
  return <div className={`sd-editor-modal ${libraryOpen ? 'sd-editor-library-open' : ''}`} role="region" aria-label={shotScope ? '镜头剪辑' : '本集剪辑'}>
    <header className="sd-editor-toolbar"><button className="sd-button" disabled={closing || Boolean(shotScope && saving)} onClick={close}><ArrowLeft size={15} />{shotScope ? '取消并返回镜头' : '返回作品'}</button><h2>{props.project.document.project.episode_title} · {props.focusShotId ? `镜头 ${props.project.document.shots.find(item => item.id === props.focusShotId)?.sequence ?? ''} 剪辑` : '成片剪辑'}</h2><button className="sd-button sd-editor-agent-button" onClick={() => ask('')}><Sparkles size={14} />制作助手</button><button className="sd-button sd-editor-library-toggle" onClick={() => setLibraryOpen(value => !value)} aria-expanded={libraryOpen}>素材</button><span aria-live="polite" aria-busy={saving}>{saveStatus}</span><button className="sd-icon" aria-label="撤销" onClick={actions.undo}><Undo2 size={16} /></button><button className="sd-icon" aria-label="重做" onClick={actions.redo}><Redo2 size={16} /></button><button className={`sd-button ${shotScope ? 'sd-primary' : ''}`} disabled={saving || closing} onClick={saveAndClose}><Save size={14} />{shotScope ? needsAdoption.current ? '保存并加入成片' : '保存镜头剪辑' : '保存'}</button>{!shotScope && <button className="sd-button sd-primary" disabled={exportBusy || startingExport} onClick={() => setExportDialog(true)}><Download size={14} />导出</button>}</header>
    {shotScope && <div className="sd-editor-scope-note">只编辑这个镜头 · 从 0 秒开始 · 当前 {duration.toFixed(2)} 秒。保存后同步到成片，后续镜头按时长变化顺移；取消不保存本次修改。</div>}
    {draftStatus && <div className="sd-editor-draft-status" role="status">{draftStatus}</div>}
    {recoveryNotice && <div className="sd-editor-draft-status" role="status">{recoveryNotice}</div>}
    {draftError && <div className="sd-inline-error" role="alert">{draftError}<button onClick={() => saveFile(JSON.stringify(updatedProject(baseRef.current, store.getState().state.editorModel), null, 2), '当前剪辑草稿.json')}>下载本地副本</button></div>}
    {libraryOpen && <button className="sd-editor-library-backdrop" aria-label="关闭素材抽屉" onClick={() => setLibraryOpen(false)} />}
    {saveError && <div className="sd-inline-error">{saveError}{conflictRef.current ? <button onClick={() => void resolveConflict().catch(props.onError)}>保留我的修改，合并保存</button> : <button onClick={() => { setSaveError(null); void flush().catch(() => {}) }}>重试保存</button>}<button onClick={() => saveFile(JSON.stringify(updatedProject(base, store.getState().state.editorModel), null, 2), '当前剪辑.json')}>保存本地副本</button></div>}
    <div className="sd-editor-body"><div className="sd-editor-preview"><aside className="sd-editor-library" aria-label="剪辑素材"><button className="sd-icon sd-editor-library-close" onClick={() => setLibraryOpen(false)} aria-label="关闭素材"><X size={16} /></button><h3>{shotScope ? '本镜素材' : '本集素材'}</h3><p className="sd-hint">点击添加到播放头</p>{visibleAssets.map(asset => <button key={asset.id} data-asset-id={asset.id} title={`添加 ${assetLabel(asset)}`} onClick={() => { actions.insertAssetsToTimeline({ assets: [asset], startTime: currentTime }); if (shotScope) actions.setTimelineClips(store.getState().state.editorModel.timelines.flatMap(item => item.clips).map(clip => ({ ...clip, sourceShotId: shotScope.shot_id }))); setLibraryOpen(false) }}>{asset.smallThumbnailPath || asset.type === 'image' ? <img src={mediaUrl(asset.smallThumbnailPath || asset.path)} alt="" /> : <Film size={21} />}<span>{assetLabel(asset)}</span><Plus size={12} /></button>)}</aside><div className="sd-editor-monitor"><ProgramMonitor scaleSubtitles defaultSubtitleFontFamily={defaultSubtitleFontFamily} compositionAspectRatio={studioAspectRatio(props.project.document.project.aspect_ratio)} playbackTimeRef={playbackTimeRef} setDraggingMarker={setDraggingMarker} kbLayout={kbLayout} /></div><aside className="sd-editor-properties">
      {subtitle ? <><h3>字幕</h3><label className="sd-field">文字<textarea value={subtitle.text} onChange={event => actions.setSubtitleText(subtitle.id, event.target.value)} /></label><label className="sd-field">开始（秒）<input type="number" min="0" step="0.1" value={subtitle.startTime} onChange={event => actions.setSubtitleStart(subtitle.id, Number(event.target.value))} /></label><label className="sd-field">结束（秒）<input type="number" step="0.1" min={subtitle.startTime + 0.1} value={subtitle.endTime} onChange={event => actions.setSubtitleEnd(subtitle.id, Number(event.target.value))} /></label><button className="sd-button" onClick={() => actions.deleteSubtitle(subtitle.id)}><Trash2 size={13} />删除字幕</button></> : selected?.type === 'text' ? <StudioTitleProperties clip={selected} /> : selected ? <><h3>{selectedShot ? `镜头 ${String(selectedShot.sequence).padStart(2, '0')}` : selected.asset?.prompt || '选中的片段'}</h3><label className="sd-field">时间线起点（秒）<input aria-label="片段时间线起点" type="number" min="0" step="0.1" value={selected.startTime} onChange={event => patchSelected({ startTime: Math.max(0, Number(event.target.value)) })} /></label><label className="sd-field">{shotScope ? '镜头素材内入点（秒）' : '素材入点（秒）'}<input aria-label="素材入点" type="number" min="0" step="0.1" value={selected.trimStart - (shotScope ? selected.sourceWindowStart ?? 0 : 0)} onChange={event => patchSelected({ trimStart: Math.max(0, Number(event.target.value)) + (shotScope ? selected.sourceWindowStart ?? 0 : 0) })} /></label><label className="sd-field">{shotScope ? '成片采用时长（秒）' : '片段时长（秒）'}<input aria-label="片段时长" type="number" min="0.1" step="0.1" value={selected.duration} onChange={event => patchSelected({ duration: Math.max(0.1, Number(event.target.value)) })} /></label><label className="sd-field">音量 {Math.round((controlledAudio?.volume ?? 1) * 100)}%<input aria-label="片段音量" type="range" min="0" max="100" value={Math.round((controlledAudio?.volume ?? 1) * 100)} onChange={event => actions.setClipAudioLevel(selected.id, Number(event.target.value) / 100)} /></label><label className="sd-field">声音淡入（秒）<input aria-label="声音淡入秒数" type="number" min="0" max={selected.duration} step="0.1" value={controlledAudio?.audioFadeInSeconds ?? 0} onChange={event => patchSelected({ audioFadeInSeconds: Math.min(selected.duration, Math.max(0, Number(event.target.value))), audioEnvelopeOffsetSeconds: undefined, audioEnvelopeDurationSeconds: undefined })} /></label><label className="sd-field">声音淡出（秒）<input aria-label="声音淡出秒数" type="number" min="0" max={selected.duration} step="0.1" value={controlledAudio?.audioFadeOutSeconds ?? 0} onChange={event => patchSelected({ audioFadeOutSeconds: Math.min(selected.duration, Math.max(0, Number(event.target.value))), audioEnvelopeOffsetSeconds: undefined, audioEnvelopeDurationSeconds: undefined })} /></label><label className="sd-checkbox"><input type="checkbox" checked={controlledAudio?.muted ?? false} onChange={event => actions.setClipAudioMuted(selected.id, event.target.checked)} />静音</label><StudioColorProperties clip={selected} /><StudioTransitionProperties clip={selected} clips={clips} /><div className="sd-editor-actions"><button className="sd-button" onClick={() => actions.splitClipsAtTime([...selectedIds], currentTime)}><Scissors size={13} />在播放头拆分</button><button className="sd-button" onClick={() => actions.deleteClips([...selectedIds])}><Trash2 size={13} />移除</button></div><button className="sd-text-button" onClick={() => ask('修改这个镜头的视频内容，按我接下来的要求处理，并保留已有剪辑。', selected)}><Sparkles size={13} />交给 Agent 修改画面</button></> : <><h3>剪辑属性</h3><p className="sd-hint">选择时间线中的片段或字幕，编辑其内容。拖动片段两侧裁切，拖到其他轨道调整组接。</p></>}
      <div className="sd-editor-actions"><button className="sd-button" onClick={addTitle}><Plus size={13} />添加标题</button><button className="sd-button" onClick={addSubtitle}><Plus size={13} />添加字幕</button><button className="sd-button" onClick={() => actions.addTrack('audio')}>添加声音轨</button></div></aside></div>
      <div className="sd-editor-timeline"><VideoEditorTimelineEditingPanel agentMode singleTimeline={!!shotScope} onAgentGap={gap => ask(editorGapRequest(gap, !!shotScope), undefined, gap)} currentProjectId={id} playbackTimeRef={playbackTimeRef} centerOnPlayheadRef={centerOnPlayheadRef} getMinZoom={() => Math.min(0.5, Math.max(0.01, (trackContainerRef.current?.clientWidth ?? 600) / (Math.max(1, duration) * 100)))} canUseIcLora={false} handleICLoraClip={clip => ask('调整这个镜头的画面风格。', clip)} kbLayout={kbLayout} handleExportTimelineXml={() => saveFile(JSON.stringify(updatedProject(base, store.getState().state.editorModel), null, 2), `${base.name}-剪辑.json`)} subtitleFileInputRef={subtitleInput} handleImportSrt={event => { const file = event.target.files?.[0]; if (file) void file.text().then(text => actions.importSrtCues(parseSrt(text))).catch(props.onError); event.target.value = '' }} handleExportSrt={saveSrt} gapGenerationApi={noGeneration} selectedGapRefBridge={selectedGapRef} gapGenerateModeRefBridge={gapModeRef} clearSelectedGapRefBridge={clearGapRef} closeSelectedGapRefBridge={closeGapRef} timelineRefBridge={timelineRef} trackContainerRefBridge={trackContainerRef} trackHeadersRefBridge={trackHeadersRef} rulerScrollRefBridge={rulerRef} markerDragOriginRef={markerOrigin} setDraggingMarker={setDraggingMarker} bladeShiftHeld={bladeShiftHeld} fitToViewRef={fitRef} onRevealAsset={assetId => { setLibraryOpen(true); requestAnimationFrame(() => { const button = [...document.querySelectorAll<HTMLButtonElement>('.sd-editor-library button[data-asset-id]')].find(item => item.dataset.assetId === assetId); button?.scrollIntoView({ block: 'nearest' }); button?.focus() }) }} onCreateVideoFromImage={clip => ask('以这个片段引用的图片生成视频。', clip)} onCaptureFrameForVideo={clip => ask('从这个片段提取合适画面作为参考，再生成相邻镜头。', clip)} onCreateVideoFromAudio={clip => ask('按这个声音片段设计并生成对应画面。', clip)} handleRegenerate={(_assetId, clipId) => ask('重新生成这个镜头，保留当前剪辑。', clips.find(clip => clip.id === clipId))} handleRetakeClip={clip => ask('根据我的要求重新生成这个镜头的指定部分。', clip)} handleCancelRegeneration={() => {}} isRegenerating={false} regenProgress={0} /></div></div>
    {!shotScope && <StudioExportStatus key={id} project={props.project} createdExport={exporting} onRecords={props.onExportRecords} onError={props.onError} onRetry={retryExport} onBusyChange={setExportBusy} />}
    {exportDialog && <div className="sd-modal-backdrop"><div className="sd-small-modal">
      <header className="sd-modal-heading"><h2>导出本集成片</h2><button className="sd-icon" aria-label="关闭导出" onClick={() => setExportDialog(false)}><X size={18} /></button></header>
      <label className="sd-field">尺寸<select value={size} onChange={event => setSize(event.target.value)}>
        <option value="source">跟随素材</option>
        {customExportSizes.map(value => <option key={value} value={value}>{value === sourceExportSize.size && sourceExportSize.basis === 'video' ? '当前素材' : value === defaultExportSize ? '本集画幅' : '指定尺寸'} · {value.replace('x', ' × ')}</option>)}
        {EXPORT_SIZE_PRESETS.map(preset => <option key={preset.value} value={preset.value}>{preset.label}</option>)}
      </select></label>
      {size === 'source' && <p className="sd-hint">{sourceExportHint}</p>}
      <label className="sd-checkbox"><input type="checkbox" checked={includeSubtitles} onChange={event => setIncludeSubtitles(event.target.checked)} />包含字幕</label>
      <label className="sd-field">帧率<select aria-label="导出帧率" value={fps} onChange={event => setFps(Number(event.target.value))}>{[24, 25, 30, 48, 50, 60].map(value => <option key={value} value={value}>{value} fps</option>)}</select></label>
      <p className="sd-hint">MP4 · {fps} fps · H.264。使用当前保存的时间线，原始素材保留。</p>
      <button className="sd-button sd-primary" disabled={startingExport || exportBusy} onClick={() => void beginExport().catch(props.onError)}><Download size={15} />{startingExport ? '正在开始…' : '开始导出'}</button>
    </div></div>}
  </div>
}
