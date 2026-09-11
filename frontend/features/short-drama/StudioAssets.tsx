import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowUpRight, FileWarning, Image, Music2, Plus, Search, Sparkles, Upload, X } from 'lucide-react'
import type { ProjectAggregate } from './api'
import { allCues, assetGroups, currentEditorial, mediaUrl } from './studio-content'
import { fileDetails, fileModificationPrompt, fileReadKey, fileReadLabel, libraryFileCards, projectMediaFiles, selectedFileScope, type LibraryFile, type MediaReadState } from './studio-media-library'
import { StudioApi, type StudioScope } from './studio-api'

const kinds = { all: '全部', CHARACTER: '角色', LOCATION: '场景', PROP: '道具', REFERENCE: '其他' }
const mediaKinds = { all: '全部', IMAGE: '图片', VIDEO: '视频', AUDIO: '音频' }
type Filter = keyof typeof kinds

function MediaPreview({ file, compact = false, read, onRead }: { file: LibraryFile; compact?: boolean; read?: MediaReadState; onRead: (state: MediaReadState) => void }) {
  const src = mediaUrl(file.localPath, file.externalUrl)
  if (file.missing || !src || read === 'error') return <div className="sd-media-cover"><FileWarning size={compact ? 23 : 32} /><span>{fileReadLabel(file, read)}</span></div>
  if (file.mediaType === 'IMAGE') return <img loading={compact ? 'lazy' : undefined} src={src} alt={file.title} onLoad={() => onRead('loaded')} onError={() => onRead('error')} />
  if (file.mediaType === 'VIDEO') return <video src={src} controls={!compact} preload="metadata" muted={compact} playsInline onLoadedMetadata={() => onRead('loaded')} onError={() => onRead('error')} />
  return compact ? <div className="sd-media-cover"><Music2 size={28} /><span>音频素材</span><audio src={src} preload="metadata" onLoadedMetadata={() => onRead('loaded')} onError={() => onRead('error')} /></div>
    : <audio controls src={src} preload="metadata" onLoadedMetadata={() => onRead('loaded')} onError={() => onRead('error')} />
}

export function StudioAssets({ project, onProject, onAsk, onScope, onShot, onError }: { project: ProjectAggregate; onProject: (project: ProjectAggregate) => void; onAsk: (text: string, scope: StudioScope) => void; onScope: (scope: StudioScope) => void; onShot: (id: string) => void; onError: (error: unknown) => void }) {
  const [view, setView] = useState<'objects' | 'files'>('objects')
  const [filter, setFilter] = useState<Filter>('all')
  const [mediaFilter, setMediaFilter] = useState<keyof typeof mediaKinds>('all')
  const [search, setSearch] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedFileId, setSelectedFileId] = useState<string | null>(null)
  const [reads, setReads] = useState<Record<string, MediaReadState>>({})
  const [importKind, setImportKind] = useState('REFERENCE')
  const [importing, setImporting] = useState(false)
  const [replaceId, setReplaceId] = useState<string | undefined>()
  const [replaceVersionId, setReplaceVersionId] = useState<string | undefined>()
  const input = useRef<HTMLInputElement>(null)
  const groups = useMemo(() => assetGroups(project, { allMedia: true }), [project])
  const files = useMemo(() => projectMediaFiles(project, groups, currentEditorial(project)), [project, groups])
  const fileCards = useMemo(() => libraryFileCards(files), [files])
  const query = search.toLowerCase().trim()
  const visible = groups.filter(group => (filter === 'all' || group.kind === filter) && `${group.name} ${group.files.map(file => file.version.descriptor).join(' ')}`.toLowerCase().includes(query))
  const visibleFiles = fileCards.filter(file => (mediaFilter === 'all' || file.mediaType === mediaFilter) && `${file.owner} ${file.description} ${file.title} ${file.contentState ?? ''} ${file.relatedFiles?.map(item => `${item.owner} ${item.title}`).join(' ') ?? ''}`.toLowerCase().includes(query))
  const selected = groups.find(group => group.id === selectedId)
  const groupFiles = selected ? files.filter(file => file.groupId === selected.id) : []
  const file = files.find(item => item.id === selectedFileId) ?? groupFiles.find(item => item.asset?.current_version_id === item.id) ?? groupFiles[0]
  const sharedFiles = fileCards.find(card => card.relatedFiles?.some(item => item.id === file?.id))?.relatedFiles?.filter(item => item.id !== file?.id) ?? []
  const detailOpen = Boolean(selected || file)
  const usage = (id: string) => project.document.shots.filter(shot => allCues(shot).some(cue => cue.subject_asset_id === id || groups.find(group => group.id === id)?.files.some(file => file.version.id === cue.asset_version_id)))
  const fileShots = file ? project.document.shots.filter(shot => file.shotIds.includes(shot.id)) : selected ? usage(selected.id) : []
  useEffect(() => { setSelectedId(null); setSelectedFileId(null); setReads({}); setSearch('') }, [project.document.project.id])
  const close = () => { setSelectedId(null); setSelectedFileId(null) }
  const chooseFile = (item: LibraryFile) => { setSelectedFileId(item.id); setSelectedId(item.groupId ?? null); onScope(selectedFileScope(item)) }
  const read = (item: LibraryFile) => reads[fileReadKey(item)]
  const markRead = (item: LibraryFile, state: MediaReadState) => setReads(previous => previous[fileReadKey(item)] === state ? previous : { ...previous, [fileReadKey(item)]: state })
  const preview = (item: LibraryFile, compact = false) => <MediaPreview key={fileReadKey(item)} file={item} compact={compact} read={read(item)} onRead={state => markRead(item, state)} />
  const detailName = selected?.name ?? file?.owner ?? '素材详情'
  const count = fileCards.filter(item => !item.missing && read(item) !== 'error').length

  return <div className="sd-assets-page">
    <header className="sd-page-header"><div><span className="sd-eyebrow">PROJECT LIBRARY</span><h1>项目资产</h1><p>查看角色、场景与道具设定，或浏览项目中的图片、视频和音频。</p></div><div className="sd-asset-import"><select aria-label="导入素材类别" value={importKind} onChange={event => setImportKind(event.target.value)}>{Object.entries(kinds).filter(([key]) => key !== 'all').map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select><button className="sd-button sd-primary" disabled={importing} onClick={() => { setReplaceId(undefined); setReplaceVersionId(undefined); input.current?.click() }}><Upload size={15} />{importing ? '正在导入…' : '导入素材'}</button></div></header>
    <input hidden ref={input} type="file" accept="image/*,video/*,audio/*" multiple={!replaceId} onChange={event => {
      const uploads = [...(event.target.files ?? [])]; if (!uploads.length) return
      setImporting(true)
      void (async () => { for (const upload of uploads) onProject(await StudioApi.importMedia(project.document.project.id, upload, importKind, replaceId, replaceVersionId)) })().catch(onError).finally(() => { setImporting(false); if (input.current) input.current.value = '' })
    }} />
    <div className="sd-library-view" role="group" aria-label="资产查看方式">
      <button className={view === 'objects' ? 'is-active' : ''} aria-pressed={view === 'objects'} onClick={() => setView('objects')}>核心资产 <span>{groups.length}</span></button>
      <button className={view === 'files' ? 'is-active' : ''} aria-pressed={view === 'files'} onClick={() => setView('files')}>所有媒体 <span>{count}</span></button>
    </div>
    <div className="sd-library-toolbar"><div className="sd-filter-tabs">{view === 'objects' ? Object.entries(kinds).map(([key, label]) => <button key={key} className={filter === key ? 'is-active' : ''} onClick={() => setFilter(key as Filter)}>{label}</button>) : Object.entries(mediaKinds).map(([key, label]) => <button key={key} className={mediaFilter === key ? 'is-active' : ''} onClick={() => setMediaFilter(key as keyof typeof mediaKinds)}>{label}</button>)}</div><label className="sd-search"><Search size={15} /><input aria-label="搜索项目资产" placeholder="搜索名称、状态或素材说明" value={search} onChange={event => setSearch(event.target.value)} /></label></div>
    <div className="sd-asset-grid">{view === 'objects' ? <>
      {visible.map(group => {
        const items = files.filter(item => item.groupId === group.id)
        const picture = items.find(item => item.id === group.subject.current_version_id) ?? items.find(item => item.asset?.current_version_id === item.id) ?? items[0]
        const uses = usage(group.id)
        const uniqueCards = libraryFileCards(items)
        const saved = uniqueCards.filter(item => !item.missing && read(item) !== 'error').length
        const pending = uniqueCards.filter(item => item.missing || read(item) === 'error').length
        return <button className="sd-asset-card" key={group.id} onClick={() => { setSelectedId(group.id); setSelectedFileId(picture?.id ?? null); onScope(picture ? selectedFileScope(picture) : { kind: 'asset', id: group.id, label: group.name }) }}><div className="sd-asset-cover" data-kind={group.kind}>{picture ? preview(picture, true) : <div className="sd-media-cover"><Image size={28} /><span>等待素材</span></div>}<span className="sd-asset-kind">{kinds[group.kind]}</span><span className="sd-asset-open"><ArrowUpRight size={15} /></span></div><div className="sd-asset-card-label"><strong>{group.name}</strong><span>{saved ? `${saved} 份文件` : '等待素材'}{pending ? ` · ${pending} 份待补` : ''}</span><p>{uses.length ? `用于本集 ${uses.length} 个镜头` : '项目共享资产'}</p></div></button>
      })}
      <button className="sd-asset-add-card" onClick={() => onAsk('根据当前剧本和镜头，补齐缺少的角色、场景和道具素材，优先复用已有资产。', { kind: 'episode', label: '当前集所需资产' })}><span><Plus size={24} /></span><strong>让 Agent 补齐资产</strong><small>根据剧本与镜头自动准备</small></button>
    </> : visibleFiles.map(item => <button className="sd-asset-card sd-file-card" key={item.id} onClick={() => chooseFile(item)}><div className="sd-asset-cover">{preview(item, true)}<span className="sd-asset-kind">{mediaKinds[item.mediaType]}</span><span className="sd-asset-open"><ArrowUpRight size={15} /></span></div><div className="sd-asset-card-label"><strong title={item.title}>{item.title}</strong><p>{[...new Set(item.relatedFiles?.map(reference => reference.owner) ?? [item.owner])].join('、')}</p><span className={item.warning || item.missing || read(item) === 'error' ? 'sd-file-warning' : ''}>{item.warning || item.state} · {fileReadLabel(item, read(item))}</span><p>{fileDetails(item) || item.source}</p><p>{item.editorialUsed ? '剪辑中使用' : item.shotIds.length ? `关联 ${item.shotIds.length} 个镜头` : item.voiceUsed ? '角色声音参考' : '当前内容未引用'}</p></div></button>)}</div>
    {view === 'files' && !visibleFiles.length && <p className="sd-hint sd-library-empty">还没有匹配的媒体文件。文字设定保留在核心资产中。</p>}

    {detailOpen && <div className="sd-modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) close() }}><div className="sd-asset-detail" role="dialog" aria-label={`${detailName} 素材详情`}><header className="sd-modal-heading"><div><span className="sd-eyebrow">{selected ? `${kinds[selected.kind]} · 全项目共享` : '项目媒体'}</span><h2>{detailName}</h2></div><button className="sd-icon" aria-label="关闭资产详情" onClick={close}><X size={20} /></button></header>
      <div className="sd-asset-detail-body"><div><div className="sd-asset-large">{file ? preview(file) : <p>等待素材</p>}</div>
        {file && <div className="sd-file-caption"><strong>{file.title}</strong><span>{file.warning || file.state} · {fileReadLabel(file, read(file))}</span></div>}
        <div className="sd-asset-filmstrip">{groupFiles.map(item => <button key={item.id} className={file?.id === item.id ? 'is-active' : ''} onClick={() => chooseFile(item)} title={item.title} aria-label={`查看素材：${item.title}`} aria-pressed={file?.id === item.id}>{preview(item, true)}<span>{item.contentState || item.title}</span></button>)}</div>
      </div><div className="sd-asset-info"><h3>素材说明</h3><p className="sd-file-description">{file?.description || selected?.subject.brief?.identity_anchor || '还没有素材说明。'}</p>
        {file?.contentState && <><h3>具体剧情状态</h3><p>{file.contentState}</p></>}
        {sharedFiles.length > 0 && <><h3>同一文件也关联</h3><p className="sd-file-description">{sharedFiles.map(item => `${item.owner}：${item.title}`).join('\n')}</p></>}
        {(file?.relatedTakes?.length ?? 0) > 1 && <><h3>同一视频的镜头关联</h3><p className="sd-file-description">{file?.relatedTakes?.map(take => {
          const ids = take.shot_windows?.length ? take.shot_windows.map(window => window.shot_id) : [take.shot_id]
          const names = ids.map(id => project.document.shots.find(shot => shot.id === id)?.sequence).filter(sequence => sequence != null)
          return `镜头 ${names.join('、') || '未显示'} · ${take.provider} · ${take.model}`
        }).join('\n')}</p></>}
        {file && <><h3>文件信息</h3><p>{file.source}<br />{fileDetails(file)}<br />{file.state} · {fileReadLabel(file, read(file))}{file.warning && <><br /><span className="sd-file-warning">{file.warning}</span></>}</p>{file.version?.review_notes && <><h3>已记录的检查说明</h3><p>{file.version.review_notes}</p></>}</>}
        {selected?.subject.brief && <><h3>保持一致</h3><p>{selected.subject.brief.identity_anchor}</p><ul>{selected.subject.brief.visual_attributes.map(item => <li key={item}>{item}</li>)}</ul></>}
        <h3>{file?.take ? '源视频覆盖的镜头' : '这份素材用在哪里'}</h3>
        <div className="sd-asset-usage">{fileShots.map(shot => <button key={shot.id} onClick={() => { close(); onShot(shot.id) }}><span>镜头 {String(shot.sequence).padStart(2, '0')}</span><p>{shot.narrative_purpose}</p><ArrowUpRight size={14} /></button>)}{file?.take?.shot_windows?.map(window => <p className="sd-hint" key={window.shot_id}>镜头 {project.document.shots.find(shot => shot.id === window.shot_id)?.sequence ?? window.shot_id}：源视频 {window.source_in_seconds.toFixed(2)}–{(window.source_in_seconds + window.duration_seconds).toFixed(2)} 秒</p>)}{file?.editorialUsed && <p className="sd-hint">这份文件已用于本集剪辑。</p>}{file?.voiceUsed && <p className="sd-hint">这份文件作为角色声音参考。</p>}{!fileShots.length && !file?.editorialUsed && !file?.voiceUsed && <p className="sd-hint">当前内容尚未引用这份素材。</p>}</div>
        <div className="sd-asset-detail-actions">{selected && <button className="sd-button" disabled={importing} onClick={() => { setReplaceId(file?.asset?.id ?? selected.subject.id); setReplaceVersionId(file?.version?.id); input.current?.click() }}><Upload size={14} />导入替换这份素材</button>}{(selected || file?.take) && <button className="sd-button sd-primary" onClick={() => { close(); onAsk(file ? fileModificationPrompt(file) : `修改「${selected?.name}」的素材。`, file ? selectedFileScope(file) : { kind: 'asset', id: selected?.id, label: selected?.name ?? '项目资产' }) }}><Sparkles size={14} />让 Agent 修改</button>}</div>
        {selected && <p className="sd-hint sd-file-action-hint">导入替换会更新引用这份文件的内容。仅查看或切换预览不会更换素材。</p>}
      </div></div>
    </div></div>}
  </div>
}
