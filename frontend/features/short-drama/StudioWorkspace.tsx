import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowUpRight, Check, ChevronDown, Clapperboard, Download, FileText, Film, FolderOpen, Loader2, Menu, Plus, Scissors, Settings2, Sparkles, X } from 'lucide-react'
import { ShortDramaApi, type ProjectAggregate, type ProjectRecord } from './api'
import { StudioApi, unwrapApi, watchStudio, type StudioEpisodes, type StudioEvent, type StudioJob, type StudioWorker, type StudioScope, type StudioExport } from './studio-api'
import { assetGroups, currentEditorial, currentScript, durationLabel, mediaUrl, shotMedia } from './studio-content'
import { StudioScript } from './StudioScript'
import { StudioShots } from './StudioShots'
import { loadShotDrafts, persistShotDrafts, updateShotDraft, type ShotDraftUpdate } from './studio-shot-drafts'
import { StudioAssets } from './StudioAssets'
import { StudioAgent } from './StudioAgent'
import { StudioChangeReview } from './StudioChangeReview'
import type { StudioChangeSet } from './studio-api'
import { StudioSettings } from './StudioSettings'
import { useStudioAppearance } from './StudioAppearance'
import { StudioEditor } from './StudioEditor'
import { StudioEditorialPreview } from './StudioEditorialPreview'
import { StudioExportStatus } from './StudioExportStatus'
import { exportRecordsFromEvents, mergeProjectExports } from './studio-export-status'
import { receiveProjectSnapshot } from './studio-project-refresh'
import type { StudioMediaLocation } from './studio-media-location'
import { StudioProjectTransfer } from './StudioProjectTransfer'
import { studioAspectRatio } from './studio-aspect-ratio'
import { createStudioActivityChannel, refreshStudioActivity, studioEventsRefreshProject } from './studio-activity-refresh'
import type { BackendProcessStatus } from '../../hooks/use-backend'
import { acknowledgeSceneSave, loadSceneDrafts, persistSceneDrafts, sceneDraftChanged, updateSceneDraft, type SceneDraft, type SceneDraftUpdate } from './studio-scene-drafts'
import './studio-themes.css'
import './studio.css'
import './studio-review-team.css'
import './studio-method-progress.css'
import './studio-draft-status.css'

type Page = 'script' | 'shots' | 'film' | 'assets'
const pages = [{ id: 'script', title: '剧本', Icon: FileText }, { id: 'shots', title: '镜头', Icon: Film }, { id: 'film', title: '成片', Icon: Clapperboard }] as const

export function StudioWorkspace({ backendStatus, onReconnect }: { backendStatus?: BackendProcessStatus; onReconnect?: () => Promise<void> } = {}) {
  const [projects, setProjects] = useState<ProjectRecord[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(() => localStorage.getItem('short-drama:selected-episode'))
  const [project, setProject] = useState<ProjectAggregate | null>(null)
  const [episodes, setEpisodes] = useState<StudioEpisodes | null>(null)
  const [page, setPage] = useState<Page>('script')
  const [shotId, setShotId] = useState<string | null>(null)
  const pendingShotFocus = useRef<{ episodeId: string; id: string } | null>(null)
  const [shotDrafts, setShotDrafts] = useState(loadShotDrafts)
  const [sceneDrafts, setSceneDrafts] = useState(loadSceneDrafts)
  const [draftStorageError, setDraftStorageError] = useState(false)
  const [savingScenes, setSavingScenes] = useState<ReadonlySet<string>>(() => new Set())
  const pendingSceneSaves = useRef(new Set<string>())
  const [scriptFocus, setScriptFocus] = useState<string | null>(null)
  const [events, setEvents] = useState<StudioEvent[]>([])
  const [jobs, setJobs] = useState<StudioJob[]>([])
  const [changes, setChanges] = useState<StudioChangeSet[]>([])
  const [reviewOpen, setReviewOpen] = useState(false)
  const [workers, setWorkers] = useState<StudioWorker[]>([])
  const [contextWindow, setContextWindow] = useState(258000)
  const [connected, setConnected] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const [scope, setScope] = useState<StudioScope>({ kind: 'episode', label: '当前集' })
  const [suggested, setSuggested] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [editorDirty, setEditorDirty] = useState(false)
  const [loading, setLoading] = useState(true)
  const [settings, setSettings] = useState(false)
  const [create, setCreate] = useState<'project' | 'episode' | null>(null)
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)
  const [editor, setEditor] = useState<{ episodeId?: string; shotId?: string; exportOnOpen?: boolean;
    initialSelection?: { timelineId: string; clipId?: string; startSeconds?: number } } | null>(null)
  const editorScopeReader = useRef<(() => Promise<StudioScope>) | null>(null)
  const setEditorScopeReader = useCallback((reader: (() => Promise<StudioScope>) | null) => { editorScopeReader.current = reader }, [])
  const prepareEditorScope = useCallback(async () => {
    if (!editorScopeReader.current) throw new Error('剪辑正在打开，请稍后发送。')
    return editorScopeReader.current()
  }, [])
  const [sidebar, setSidebar] = useState(false)
  const [agentVisible, setAgentVisible] = useState(false)
  const [renaming, setRenaming] = useState<string | null>(null)
  const cursor = useRef(0)
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const activityChannel = useRef(createStudioActivityChannel(selectedId))
  if (activityChannel.current.id !== selectedId) activityChannel.current = createStudioActivityChannel(selectedId)
  const currentIdRef = useRef(selectedId)
  currentIdRef.current = selectedId
  const hasProjectRef = useRef(false)
  hasProjectRef.current = Boolean(project)
  const onError = useCallback((value: unknown) => setError(value instanceof Error ? value.message : String(value)), [])
  const { theme, changeTheme } = useStudioAppearance(onError)
  const clearSuggested = useCallback(() => setSuggested(null), [])
  const refreshList = useCallback(async () => { const response = unwrapApi(await ShortDramaApi.listProjects()); setProjects(response.projects); return response.projects }, [])
  const refreshProject = useCallback(async () => {
    const channel = activityChannel.current
    const id = currentIdRef.current
    if (!id) return
    const [next, episodeList, textChanges] = await Promise.all([StudioApi.getProject(id), StudioApi.episodes(id), StudioApi.changes(id)])
    if (channel === activityChannel.current && id === currentIdRef.current) {
      setProject(current => receiveProjectSnapshot(current, next))
      setEpisodes(episodeList); setChanges(textChanges)
    }
  }, [])
  const onProject = useCallback((next: ProjectAggregate) => {
    if (next.document.project.id === currentIdRef.current) {
      setProject(current => receiveProjectSnapshot(current, next))
    }
  }, [])
  const onExportRecords = useCallback((episodeId: string, incoming: StudioExport[]) => {
    if (episodeId !== currentIdRef.current) return
    setProject(current => current?.document.project.id === episodeId ? mergeProjectExports(current, incoming) : current)
  }, [])
  const appendEvents = useCallback((incoming: StudioEvent[]) => {
    if (!incoming.length) return
    cursor.current = Math.max(cursor.current, ...incoming.map(event => event.id))
    setEvents(previous => [...new Map([...previous, ...incoming].map(event => [event.id, event])).values()].sort((a, b) => a.id - b.id))
    const exportRecords = exportRecordsFromEvents(incoming)
    if (exportRecords.length) setProject(current => current ? mergeProjectExports(current, exportRecords) : current)
    if (studioEventsRefreshProject(incoming)) {
      const channel = activityChannel.current
      if (refreshTimer.current) clearTimeout(refreshTimer.current)
      refreshTimer.current = setTimeout(() => {
        if (channel === activityChannel.current) void refreshProject().catch(value => {
          if (channel === activityChannel.current) onError(value)
        })
      }, 150)
    }
  }, [refreshProject, onError])
  const refreshActivity = useCallback(() => {
    const channel = activityChannel.current
    return refreshStudioActivity(channel, {
      isCurrent: () => channel === activityChannel.current,
      read: id => StudioApi.activity(id, cursor.current),
      apply: value => {
        setJobs(value.jobs); setWorkers(value.workers ?? []); setContextWindow(value.context_window_tokens ?? 258000); appendEvents(value.events)
      },
      connection: setConnected,
    })
  }, [appendEvents])
  useEffect(() => { void refreshList().then(list => { if (!currentIdRef.current && list.length) setSelectedId(list[0].id); if (!list.length) setSelectedId(null) }).catch(onError).finally(() => setLoading(false)) }, [refreshList, onError])
  useEffect(() => {
    setProject(null); setEpisodes(null); setEvents([]); setJobs([]); setWorkers([]); setChanges([]); setReviewOpen(false); cursor.current = 0; setConnected(false); setShotId(pendingShotFocus.current?.episodeId === selectedId ? pendingShotFocus.current.id : null); pendingShotFocus.current = null; setScope({ kind: 'episode', label: '当前集' })
    if (!selectedId) return
    localStorage.setItem('short-drama:selected-episode', selectedId)
    const abort = new AbortController()
    let reconnect: ReturnType<typeof setTimeout> | null = null
    const connect = async () => {
      try {
        await refreshProject(); await refreshActivity(); if (abort.signal.aborted) return
        setConnected(true)
        await watchStudio(selectedId, cursor.current, abort.signal, incoming => {
          if (abort.signal.aborted || selectedId !== currentIdRef.current) return
          setConnected(true)
          appendEvents(incoming)
          if (incoming.some(event => ['job.updated', 'worker.updated', 'model.started'].includes(event.kind))) void refreshActivity().catch(onError)
        })
      } catch (value) { if (!abort.signal.aborted && !hasProjectRef.current) onError(value) }
      if (!abort.signal.aborted) { setConnected(false); reconnect = setTimeout(() => { void connect() }, 2500) }
    }
    void connect()
    const poll = setInterval(() => { void refreshActivity().catch(() => {}) }, 4000)
    return () => { abort.abort(); if (reconnect) clearTimeout(reconnect); if (refreshTimer.current) clearTimeout(refreshTimer.current); clearInterval(poll) }
    // Project changes are delivered inside this connection, not a reason to restart it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, appendEvents, refreshProject, refreshActivity, onError])
  const episodeDrafts = selectedId ? shotDrafts[selectedId] ?? {} : {}
  const episodeSceneDrafts = selectedId ? sceneDrafts[selectedId] ?? {} : {}
  const episodeSavingScenes = new Set(Object.keys(episodeSceneDrafts).filter(id => savingScenes.has(JSON.stringify([selectedId, id]))))
  const pendingScenes = Object.entries(sceneDrafts).flatMap(([episodeId, drafts]) => Object.entries(drafts).filter(([, draft]) => sceneDraftChanged(draft)).map(([id]) => ({ episodeId, id, kind: 'script' as const })))
  const pendingShots = Object.entries(shotDrafts).flatMap(([episodeId, drafts]) => Object.entries(drafts).filter(([, draft]) => JSON.stringify(draft.shot) !== JSON.stringify(draft.base)).map(([id]) => ({ episodeId, id, kind: 'shots' as const })))
  const pendingDrafts = [...pendingScenes, ...pendingShots]
  const draftCount = pendingDrafts.length
  const draftLabel = [pendingScenes.length ? `${pendingScenes.length} 个场次` : '', pendingShots.length ? `${pendingShots.length} 个分镜` : ''].filter(Boolean).join('、')
  const onSceneDraft = (id: string, draft: SceneDraftUpdate) => {
    if (selectedId) setSceneDrafts(previous => updateSceneDraft(previous, selectedId, id, draft))
  }
  const onSaveScene = async (id: string, submitted: SceneDraft) => {
    if (!selectedId) return
    const episodeId = selectedId
    const key = JSON.stringify([episodeId, id])
    if (pendingSceneSaves.current.has(key)) return
    pendingSceneSaves.current.add(key)
    setSavingScenes(new Set(pendingSceneSaves.current))
    try {
      const result = await StudioApi.saveScene(episodeId, { scene_id: id, heading: submitted.heading, screenplay_text: submitted.text, expected_text: submitted.base })
      onProject(result.project)
      const saved = currentScript(result.project)?.structured_scenes.find(scene => scene.id === id)
      if (saved) setSceneDrafts(previous => updateSceneDraft(previous, episodeId, id, current => acknowledgeSceneSave(current, submitted, saved)))
    } catch (value) { onError(value) } finally {
      pendingSceneSaves.current.delete(key)
      setSavingScenes(new Set(pendingSceneSaves.current))
    }
  }
  useEffect(() => {
    const scenesSaved = persistSceneDrafts(sceneDrafts)
    const shotsSaved = persistShotDrafts(shotDrafts)
    setDraftStorageError(!scenesSaved || !shotsSaved)
  }, [sceneDrafts, shotDrafts])
  const onShotDraft = (id: string, draft: ShotDraftUpdate) => {
    if (!selectedId) return
    setShotDrafts(previous => updateShotDraft(previous, selectedId, id, draft))
  }
  useEffect(() => {
    if (!draftCount) return
    const warn = (event: BeforeUnloadEvent) => {
      const scenesSaved = persistSceneDrafts(sceneDrafts)
      const shotsSaved = persistShotDrafts(shotDrafts)
      if (!scenesSaved || !shotsSaved) { event.preventDefault(); event.returnValue = '' }
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [draftCount, sceneDrafts, shotDrafts])
  useEffect(() => {
    if (page !== 'script' || !scriptFocus || !project || project.document.project.id !== selectedId) return
    const timer = setTimeout(() => { document.getElementById(`script-${scriptFocus}`)?.scrollIntoView({ block: 'start', behavior: 'smooth' }); setScriptFocus(null) }, 100)
    return () => clearTimeout(timer)
  }, [page, scriptFocus, project, selectedId])
  const rootId = project?.document.project.parent_project_id ?? selectedId
  const root = projects.find(item => item.id === rootId)
  const active = jobs.some(job => ['queued', 'running', 'retrying', 'pausing'].includes(job.status))
  const ask = (text: string, nextScope: StudioScope) => { setScope(nextScope); setSuggested(text); setAgentVisible(true) }
  const selectShot = (id: string) => { setShotId(id); setPage('shots') }
  const locateMedia = (location: StudioMediaLocation) => {
    const { episodeId, shotId: locatedShot, timelineId, clipId, startSeconds } = location
    if (timelineId) {
      setEditor({ episodeId, initialSelection: { timelineId, clipId: clipId ?? undefined, startSeconds: startSeconds ?? undefined } })
      setPage('film')
    } else {
      setEditor(null)
      if (locatedShot && episodeId !== selectedId) pendingShotFocus.current = { episodeId, id: locatedShot }
      if (locatedShot) selectShot(locatedShot)
      else setPage('assets')
    }
    setSelectedId(episodeId)
  }
  const openPendingDraft = () => {
    const next = pendingDrafts.find(item => item.episodeId === selectedId) ?? pendingDrafts[0]
    if (!next) return
    if (next.kind === 'shots' && next.episodeId !== selectedId) pendingShotFocus.current = next
    setSelectedId(next.episodeId)
    setPage(next.kind)
    if (next.kind === 'script') setScriptFocus(next.id)
    else setShotId(next.id)
  }
  const reconnecting = Boolean((backendStatus && backendStatus !== 'alive') || (project && !connected))
  const reconnect = async () => {
    if (!onReconnect || restarting) return
    setRestarting(true)
    try { await onReconnect() } catch (value) { onError(value) } finally { setRestarting(false) }
  }
  const createItem = async () => {
    setCreating(true)
    try {
      const value = create === 'episode' && rootId ? await StudioApi.createEpisode(rootId, newName.trim() || `第 ${(episodes?.episodes.length ?? 1) + 1} 集`) : await StudioApi.createProject(newName.trim() || '未命名作品')
      await refreshList(); setSelectedId(value.document.project.id); setPage('script'); setCreate(null); setNewName(''); setError(null)
    } catch (value) { onError(value) } finally { setCreating(false) }
  }
  return <div className={`sd-app ${editor ? 'sd-editing' : ''} ${sidebar ? 'sd-sidebar-open' : ''} ${agentVisible ? 'sd-agent-open' : ''} ${reconnecting ? 'sd-reconnecting' : ''}`}>
    {draftStorageError && <div className="sd-connection-notice" role="alert">草稿暂时无法写入本机恢复缓存。当前输入仍保留，请保存正文或分镜后再退出。</div>}
    {reconnecting && <div className="sd-connection-notice" role="status"><Loader2 size={14} className="sd-spin" /><span>{backendStatus === 'dead' ? '本地制片服务已停止。当前编辑已保留，连接恢复后可继续保存。' : '正在重新连接制片服务。当前编辑已保留，连接恢复后可继续保存。'}</span>{backendStatus === 'dead' && onReconnect && <button className="sd-text-button" disabled={restarting} onClick={() => void reconnect()}>{restarting ? '正在启动…' : '重新连接'}</button>}</div>}
    <aside className="sd-sidebar"><div className="sd-brand"><span><Clapperboard size={19} /></span><strong>Short Drama<span>STUDIO</span></strong></div><div className="sd-project-select"><label>当前项目</label><select aria-label="选择项目" value={rootId ?? ''} onChange={event => { setSelectedId(event.target.value); setSidebar(false) }}>{projects.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}{!projects.length && <option value="">还没有项目</option>}</select><ChevronDown size={14} /></div><button className="sd-new-project" onClick={() => { setCreate('project'); setNewName('') }}><Plus size={15} />新建项目</button>
      <div className="sd-sidebar-section"><div className="sd-sidebar-heading">项目内容 <button className="sd-icon" title="添加一集" aria-label="添加一集" disabled={!rootId} onClick={() => { setCreate('episode'); setNewName(`第 ${(episodes?.episodes.length ?? 1) + 1} 集`) }}><Plus size={14} /></button></div>{episodes?.episodes.map(item => <button key={item.id} className={`sd-episode ${selectedId === item.id ? 'is-active' : ''}`} onClick={() => { setSelectedId(item.id); setSidebar(false) }}><span>{String(item.episode_number).padStart(2, '0')}</span><div>{item.episode_title}</div>{selectedId === item.id && <i />}</button>)}</div>
      <button className={`sd-assets-nav ${page === 'assets' ? 'is-active' : ''}`} disabled={!project} onClick={() => { setPage('assets'); setSidebar(false) }}><FolderOpen size={17} /><div>项目资产<small>角色、场景与素材</small></div><span>{project ? assetGroups(project).length : 0}</span></button><div className="sd-sidebar-bottom"><StudioProjectTransfer projectId={rootId} hasUnsavedEditor={editorDirty} onImported={refreshList} onOpen={id => { setEditor(null); setSelectedId(id); setPage('script'); setSidebar(false) }} onError={onError} /><div className="sd-sidebar-note"><span className="sd-status-dot" />项目保存在本机</div><button onClick={() => setSettings(true)}><Settings2 size={16} />设置与创作方法</button>{project && <button onClick={() => void StudioApi.downloadTrace(project.document.project.id).catch(onError)}><Download size={15} />导出制作记录</button>}</div></aside>
    <div className="sd-workspace"><header className="sd-topbar"><button className="sd-icon sd-mobile-menu" aria-label="打开项目侧栏" onClick={() => setSidebar(previous => !previous)}><Menu size={19} /></button><div className="sd-breadcrumb"><button className="sd-project-title" onClick={() => { if (root) setRenaming(root.name) }}>{root?.name ?? '我的工作台'}</button>{project && <><span>/</span><strong>{project.document.project.episode_title}</strong></>}</div><nav className="sd-main-nav" aria-label="创作内容">{pages.map(({ id, title, Icon }) => <button key={id} className={page === id ? 'is-active' : ''} onClick={() => { setPage(id); if (id === 'film') setScope({ kind: 'film', label: '本集成片' }) }} disabled={!project}><Icon size={15} />{title}</button>)}</nav><div className="sd-topbar-status">{draftCount ? <button className="sd-text-button sd-pending-drafts" title="查看待保存的草稿，包含其他集的编辑" onClick={openPendingDraft}>{draftLabel}待保存</button> : editorDirty ? <span>剪辑待保存</span> : active ? <><Loader2 size={13} className="sd-spin" />制作中</> : <><Check size={14} />已保存</>}</div><button className="sd-icon sd-agent-toggle" aria-label="显示制作助手" onClick={() => setAgentVisible(previous => !previous)}><Sparkles size={19} /></button></header>
      <div className="sd-content-scroll">{!project ? <div className="sd-empty-page"><div className="sd-start-orb">{loading || selectedId ? <Loader2 className="sd-spin" size={30} /> : <Clapperboard size={30} />}</div><h1>{loading || selectedId ? '正在打开作品…' : '让想法，成为作品。'}</h1><p>{loading || selectedId ? '载入剧本、镜头和项目素材。' : '从一句话出发，和 Agent 一起完成故事、镜头与成片。'}</p><button className="sd-button sd-primary" onClick={() => { setCreate('project'); setNewName('') }}><Plus size={16} />新建作品</button>{selectedId && error && <button className="sd-text-button" onClick={() => setSelectedId(projects[0]?.id ?? null)}>回到已有项目</button>}</div> : page === 'script' ? <StudioScript drafts={episodeSceneDrafts} savingSceneIds={episodeSavingScenes} onDraft={onSceneDraft} onSave={onSaveScene} project={project} onProject={onProject} onAsk={ask} onScope={setScope} onShots={() => setPage('shots')} onError={onError} /> : page === 'shots' ? <StudioShots drafts={episodeDrafts} onDraft={onShotDraft} onScript={id => { setPage('script'); setScriptFocus(id) }} project={project} selectedShotId={shotId} selectShot={selectShot} onProject={onProject} onAsk={ask} onScope={setScope} onEdit={id => { setScope({ kind: 'shot', id, label: `镜头 ${project.document.shots.find(item => item.id === id)?.sequence ?? ''}` }); setEditor({ shotId: id }) }} onAssets={() => setPage('assets')} onError={onError} /> : page === 'assets' ? <StudioAssets project={project} onProject={onProject} onAsk={ask} onScope={setScope} onShot={selectShot} onLocate={locateMedia} onError={onError} /> : <StudioFilm project={project} onExportRecords={onExportRecords} showExportStatus={!editor} onEdit={exportOnOpen => setEditor({ exportOnOpen })} onAsk={ask} onShot={selectShot} onError={onError} />}</div>
    </div>
    {project && <StudioAgent pendingChanges={changes.flatMap(change => change.items).filter(item => item.status === 'pending').length} onReview={() => setReviewOpen(true)} unsavedEdit={editorDirty} unsavedDesign={pendingShots.some(item => item.episodeId === selectedId)} key={`agent:${project.document.project.id}`} projectId={project.document.project.id} events={events} jobs={jobs} workers={workers} contextWindow={contextWindow} scope={scope} setScope={setScope} prepareScope={editor ? prepareEditorScope : undefined} connected={connected} suggested={suggested} clearSuggested={clearSuggested} onError={onError} onSent={() => void refreshActivity().catch(onError)} onClose={() => setAgentVisible(false)} />}
    {reviewOpen && project && <StudioChangeReview projectId={project.document.project.id} changes={changes.filter(change => change.items.some(item => item.status === 'pending') || change.id === changes.at(-1)?.id)} onChanged={(next, change) => { onProject(next); setChanges(previous => previous.map(item => item.id === change.id ? change : item)); void refreshActivity().catch(onError) }} onClose={() => setReviewOpen(false)} onError={onError} />}
    {error && <div className="sd-toast" role="alert"><span>{error}</span><button className="sd-icon" aria-label="关闭提示" onClick={() => setError(null)}><X size={16} /></button></div>}
    {settings && <StudioSettings project={project} theme={theme} onTheme={changeTheme} onProject={onProject} onClose={() => setSettings(false)} onError={onError} />}
    {notice && <div className="sd-toast sd-notice-toast" role="status">{notice}<button className="sd-icon" aria-label="关闭提示" onClick={() => setNotice(null)}><X size={16} /></button></div>}
    {editor && project && (!editor.episodeId || editor.episodeId === project.document.project.id) && <StudioEditor key={`editor:${project.document.project.id}`} onAgentScopeReader={setEditorScopeReader} onDirty={setEditorDirty} onNotice={setNotice} project={project} focusShotId={editor.shotId} exportOnOpen={editor.exportOnOpen} initialSelection={editor.initialSelection} onExportRecords={onExportRecords} onProject={onProject} onClose={() => { setEditor(null); setEditorDirty(false) }} onAsk={(text, nextScope) => ask(text, nextScope)} onError={onError} />}
    {create && <div className="sd-modal-backdrop"><form className="sd-small-modal" onSubmit={event => { event.preventDefault(); void createItem() }}><header className="sd-modal-heading"><h2>{create === 'project' ? '新建作品' : '添加一集'}</h2><button type="button" className="sd-icon" aria-label="关闭新建窗口" onClick={() => setCreate(null)}><X size={18} /></button></header><label className="sd-field">{create === 'project' ? '作品名称' : '这一集的名称'}<input autoFocus value={newName} onChange={event => setNewName(event.target.value)} placeholder={create === 'project' ? '给故事起个名字' : '第 2 集'} /></label><p className="sd-hint">{create === 'project' ? '稍后可以随时修改。' : '角色、场景与核心素材继续共享，剧本和剪辑独立。'}</p><button type="submit" className="sd-button sd-primary" disabled={creating}>{creating && <Loader2 size={15} className="sd-spin" />}{creating ? '正在创建…' : '开始创作'}</button></form></div>}
    {renaming !== null && <div className="sd-modal-backdrop"><form className="sd-small-modal" onSubmit={event => { event.preventDefault(); if (!rootId) return; void StudioApi.rename(rootId, renaming).then(async () => { setRenaming(null); await refreshList(); await refreshProject() }).catch(onError) }}><header className="sd-modal-heading"><h2>作品名称</h2><button type="button" className="sd-icon" aria-label="关闭修改名称" onClick={() => setRenaming(null)}><X size={18} /></button></header><label className="sd-field">名称<input autoFocus value={renaming} onChange={event => setRenaming(event.target.value)} /></label><button className="sd-button sd-primary" type="submit" disabled={!renaming.trim()}>保存名称</button></form></div>}
  </div>
}

function StudioFilm({ project, onExportRecords, showExportStatus, onEdit, onAsk, onShot, onError }: { project: ProjectAggregate; onExportRecords: (episodeId: string, records: StudioExport[]) => void; showExportStatus: boolean; onEdit: (exportOnOpen?: boolean) => void; onAsk: (text: string, scope: StudioScope) => void; onShot: (id: string) => void; onError: (error: unknown) => void }) {
  const record = project.exports[project.exports.length - 1]
  const exported = (project.document.studio_exports ?? []).filter(item => item.status === 'completed').slice(-1)[0] as StudioExport | undefined
  const editedSinceExport = Boolean(project.document.editorial_timeline && (!exported || Number(project.document.editorial_timeline.revision) !== exported.editorial_revision))
  const manifest = project.document.manifests.find(item => item.id === project.document.project.current_manifest_id) ?? project.document.manifests[project.document.manifests.length - 1]
  const [subtitled, setSubtitled] = useState(true)
  const path = exported ? (subtitled ? exported.video_path : exported.clean_video_path) : (subtitled ? record?.subtitled_video_path : record?.clean_video_path)
  const shots = project.document.shots
  const ready = shots.filter(shot => shotMedia(project, shot).path).length
  const editorial = currentEditorial(project)
  const timeline = editorial?.timelines.find(item => item.id === editorial.activeTimelineId)
  const editDuration = timeline ? Math.max(0, ...timeline.clips.map(clip => clip.startTime + clip.duration), ...timeline.subtitles.map(cue => cue.endTime)) : manifest?.duration_seconds
  const hasCurrentEdit = Boolean(timeline?.clips.some(clip => clip.type !== 'audio'))
  const plannedDuration = shots.reduce((sum, shot) => sum + shot.duration_seconds, 0)
  const script = currentScript(project)
  return <div className="sd-film-page"><header className="sd-page-header"><div><span className="sd-eyebrow">THE FILM</span><h1>让每个镜头，成为一个故事。</h1><p>{project.document.project.episode_title} · {ready}/{shots.length} 个镜头已有视频{` · 设计 ${durationLabel(plannedDuration)}`}{editDuration != null ? ` · 当前剪辑 ${Number(editDuration.toFixed(2))} 秒` : ''}</p></div><button className="sd-button sd-primary" disabled={!ready && !hasCurrentEdit} onClick={() => onEdit(true)}><Download size={15} />导出成片</button></header>
    {showExportStatus && <StudioExportStatus key={project.document.project.id} project={project} onRecords={onExportRecords} onError={onError} />}
    <div className="sd-film-stage">{hasCurrentEdit ? <StudioEditorialPreview projectId={project.document.project.id} revision={Number(project.document.editorial_timeline?.revision ?? 0)} aspectRatio={studioAspectRatio(project.document.project.aspect_ratio)} showSubtitles={subtitled} /> : path ? <video key={path} className="sd-film-video" controls playsInline preload="metadata" src={mediaUrl(path)} aria-label="完整成片预览" /> : <div className="sd-film-empty"><Clapperboard size={36} /><h2>{ready ? '镜头就绪，开始组接。' : '你的作品，将在这里播放。'}</h2><p>{ready ? '进入剪辑排列镜头，或让 Agent 按故事节奏完成初剪。' : 'Agent 完成镜头后，可以继续配音、组接和导出。'}</p><button className="sd-button" onClick={() => onAsk('根据当前剧本和已完成的镜头，推进并完成本集完整成片。保留我已经完成的剪辑。', { kind: 'film', label: '本集成片' })}><Sparkles size={15} />继续完成成片</button></div>}</div>
    <div className="sd-film-controls"><div className="sd-film-name"><span className="sd-status-dot" /><strong>{hasCurrentEdit ? '当前成片剪辑' : path ? '最近导出的成片' : '本集剪辑'}</strong>{hasCurrentEdit ? <span>{Number((editDuration ?? 0).toFixed(2))} 秒</span> : path && <span>{exported?.duration_seconds ? durationLabel(exported.duration_seconds) : manifest ? durationLabel(manifest.duration_seconds) : ''}</span>}</div><div>{(hasCurrentEdit || record || exported) && <label className="sd-checkbox"><input type="checkbox" checked={subtitled} onChange={event => setSubtitled(event.target.checked)} />字幕</label>}<button className="sd-button" onClick={() => onEdit()}><Scissors size={15} />剪辑成片</button>{path && <button className="sd-button" onClick={() => void StudioApi.downloadMedia(project.document.project.id, path, `${project.document.project.name}.mp4`).catch(onError)}><Download size={15} />{editedSinceExport ? '下载上次导出' : '下载视频'}</button>}</div></div>
    {editedSinceExport && <p className="sd-hint sd-film-edit-notice">当前预览已同步最新剪辑。上次导出的文件保持原样；下载或分享新文件前，请重新导出。</p>}<section className="sd-film-storyline"><div className="sd-section-label"><h3>故事顺序</h3><span>{script?.structured_scenes.length ?? 0} 场 · {shots.length} 个镜头</span></div><div className="sd-film-shot-strip">{shots.map(shot => { const source = shotMedia(project, shot); return <button key={shot.id} onClick={() => onShot(shot.id)}><div>{source.thumbnail ? <img src={mediaUrl(source.thumbnail)} alt={`镜头 ${shot.sequence}`} loading="lazy" /> : <Film size={22} />}<span>{String(shot.sequence).padStart(2, '0')}</span><small>{source.adoptedDuration != null ? `${Number(source.adoptedDuration.toFixed(2))} 秒` : source.path ? '尚未采用' : '待生成'}</small></div><p>{shot.narrative_purpose}</p></button>})}</div><p className="sd-hint">单镜头剪辑保存后同步到本集成片。设计时长保留原意，成片采用时长随实际裁切变化。</p></section>
    <div className="sd-film-next"><div><h3>再打磨一下？</h3><p>选定一个镜头，或告诉 Agent 整体节奏需要怎样调整。</p></div><button className="sd-text-button" onClick={() => onAsk('', { kind: 'film', label: '本集成片' })}>交给制作助手 <ArrowUpRight size={15} /></button></div>
  </div>
}
