import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, Download, FolderArchive, FolderOpen, Loader2, RefreshCw, Upload, X } from 'lucide-react'
import { downloadBackendFile, studioRequest } from './studio-api'
import { createProjectTransferApi, isProjectTransferActive, mergeProjectTransfers, openProjectPackage, projectTransferProgress, readActiveProjectTransfers, transferBytes, type ProjectTransfer } from './studio-project-transfer'
import './studio-project-transfer.css'

const api = createProjectTransferApi(studioRequest)
const statusLabels: Record<ProjectTransfer['status'], string> = { queued: '等待处理', running: '正在处理', cancelling: '正在取消', cancelled: '已取消', completed: '已完成', failed: '未完成' }
const stageLabels: Record<string, string> = { snapshot: '整理已保存的内容', media: '收集媒体', trace: '收集制作记录', validating: '检查项目包', extracting: '解压文件', committing: '保存导入项目' }

export function StudioProjectTransfer({ projectId, hasUnsavedEditor, onImported, onOpen, onError }: {
  projectId: string | null; hasUnsavedEditor: boolean; onImported: () => Promise<unknown>
  onOpen: (projectId: string) => void; onError: (error: unknown) => void
}) {
  const [open, setOpen] = useState(false)
  const [records, setRecords] = useState<ProjectTransfer[]>([])
  const [pending, setPending] = useState<{ operation: 'collect' | 'import'; filename?: string; size?: number } | null>(null)
  const [pendingActions, setPendingActions] = useState<string[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [listError, setListError] = useState(false)
  const [pollError, setPollError] = useState(false)
  const [pageError, setPageError] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const recordsRef = useRef(records)
  const mounted = useRef(true)
  const panelSession = useRef(0)
  const callbacks = useRef({ onImported, onError })
  const notifiedImports = useRef(new Set<string>())
  recordsRef.current = records
  callbacks.current = { onImported, onError }
  const uploading = pending?.operation === 'import'
  const syncError = listError || pollError
  const isDesktop = typeof window.electronAPI?.showItemInFolder === 'function'
  const receive = useCallback((incoming: ProjectTransfer[]) => {
    if (mounted.current) setRecords(current => mergeProjectTransfers(current, incoming))
  }, [])
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])

  useEffect(() => {
    if (!uploading) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [uploading])

  useEffect(() => {
    if (!open) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !uploading) { setOpen(false); trigger.current?.focus() }
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [open, uploading])

  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    const session = ++panelSession.current
    // Reopening starts at the most recent page; retain known active jobs even
    // when they have fallen beyond that page. All history remains on the server.
    setRecords(current => current.filter(isProjectTransferActive))
    setLoading(true); setLoadingMore(false); setPageError(false); setNextCursor(null)
    let timer: ReturnType<typeof setTimeout> | undefined
    const load = async () => {
      try {
        const page = await api.list(undefined, controller.signal)
        if (controller.signal.aborted || panelSession.current !== session) return
        receive(page.transfers); setNextCursor(page.next_cursor ?? null); setListError(false)
      } catch {
        if (controller.signal.aborted) return
        setListError(true)
        timer = setTimeout(() => { void load() }, 2500)
      } finally { if (!controller.signal.aborted) setLoading(false) }
    }
    void load()
    return () => { controller.abort(); if (timer) clearTimeout(timer); panelSession.current += 1 }
  }, [open, refresh, receive])

  const activeIds = records.filter(isProjectTransferActive).map(record => record.id).join(',')
  useEffect(() => {
    if (!open || !activeIds) { setPollError(false); return }
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async () => {
      const result = await readActiveProjectTransfers(recordsRef.current, id => api.get(id, controller.signal))
      if (controller.signal.aborted) return
      receive(result.transfers); setPollError(result.failedIds.length > 0)
      timer = setTimeout(() => { void poll() }, result.failedIds.length ? 2500 : 900)
    }
    timer = setTimeout(() => { void poll() }, 900)
    return () => { controller.abort(); if (timer) clearTimeout(timer) }
  }, [open, activeIds, receive])

  useEffect(() => {
    const completed = records.filter(record => record.status === 'completed' && record.imported && !notifiedImports.current.has(record.id))
    if (!completed.length) return
    completed.forEach(record => notifiedImports.current.add(record.id))
    void callbacks.current.onImported().catch(error => { if (mounted.current) callbacks.current.onError(error) })
  }, [records])

  const collect = async () => {
    if (!projectId || pending) return
    setPending({ operation: 'collect' })
    try { receive([await api.collect(projectId)]) }
    catch (error) { callbacks.current.onError(error); if (mounted.current) setRefresh(value => value + 1) }
    finally { if (mounted.current) setPending(null) }
  }
  const importPackage = async (file: File) => {
    if (pending) return
    setPending({ operation: 'import', filename: file.name, size: file.size })
    try { receive([await api.import(file)]) }
    catch (error) { callbacks.current.onError(error); if (mounted.current) setRefresh(value => value + 1) }
    finally { if (mounted.current) setPending(null) }
  }
  const loadMore = async () => {
    if (!nextCursor || loadingMore) return
    const session = panelSession.current
    setLoadingMore(true); setPageError(false)
    try {
      const page = await api.list(nextCursor)
      if (!mounted.current || panelSession.current !== session) return
      receive(page.transfers); setNextCursor(page.next_cursor ?? null)
    } catch { if (mounted.current && panelSession.current === session) setPageError(true) }
    finally { if (mounted.current && panelSession.current === session) setLoadingMore(false) }
  }
  const cancel = async (record: ProjectTransfer) => {
    setPendingActions(current => [...current, record.id])
    try { receive([await api.cancel(record.id)]) }
    catch (error) { callbacks.current.onError(error) }
    finally { if (mounted.current) setPendingActions(current => current.filter(id => id !== record.id)) }
  }
  const openPackage = async (record: ProjectTransfer) => {
    if (!record.package) return
    setPendingActions(current => [...current, record.id])
    try {
      await openProjectPackage(record.package, {
        reveal: isDesktop ? path => window.electronAPI.showItemInFolder({ filePath: path }) : undefined,
        download: downloadBackendFile,
      })
    } catch (error) { callbacks.current.onError(error) }
    finally { if (mounted.current) setPendingActions(current => current.filter(id => id !== record.id)) }
  }
  const close = () => { if (!uploading) { setOpen(false); trigger.current?.focus() } }

  return <>
    <button ref={trigger} onClick={() => setOpen(true)}><FolderArchive size={15} />项目收集与迁移</button>
    {open && <div className="sd-modal-backdrop"><section className="sd-small-modal sd-project-transfer" role="dialog" aria-modal="true" aria-label="项目收集与迁移">
      <header className="sd-modal-heading"><h2>项目收集与迁移</h2><button className="sd-icon" disabled={uploading} aria-label="关闭项目迁移" onClick={close}><X size={18} /></button></header>
      <p className="sd-hint">收集各集已保存的内容、剪辑、引用媒体和完整制作记录，在另一台电脑继续制作。</p>
      <div className="sd-transfer-create">
        <button className="sd-button" disabled={!projectId || Boolean(pending)} onClick={() => void collect()}><FolderArchive size={15} />{pending?.operation === 'collect' ? '正在创建收集任务…' : '收集当前项目'}</button>
        <button className="sd-button" disabled={Boolean(pending)} onClick={() => input.current?.click()}><Upload size={15} />导入项目包</button>
        <input ref={input} type="file" accept=".zip,application/zip" hidden onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void importPackage(file) }} />
      </div>
      {hasUnsavedEditor && <p className="sd-hint">当前有剪辑待保存，项目包会使用上次保存的内容。</p>}
      {uploading ? <div className="sd-transfer-upload" role="status"><strong><Loader2 size={15} className="sd-spin" />正在发送项目包</strong><p>{pending.filename} · {transferBytes(pending.size ?? 0)}</p><progress aria-label="项目包发送中，等待后台确认" /><p>尚未收到后台任务确认，请保持页面和应用打开。收到确认后即可关闭面板。</p></div>
        : <p className="sd-transfer-background sd-hint">任务创建后可关闭面板继续编辑；重新打开可找回进度和结果。请保持应用运行。</p>}
      <div className="sd-transfer-list-heading"><h3>最近任务</h3><button className="sd-text-button" disabled={loading} onClick={() => setRefresh(value => value + 1)}><RefreshCw size={13} />{syncError ? '重新连接' : '刷新记录'}</button></div>
      {syncError && <p className="sd-inline-notice" role="status">状态暂时无法更新，正在重新连接。已创建的任务不会因为连接中断而重新提交。</p>}
      {loading && <p className="sd-hint" role="status">正在读取任务记录…</p>}
      {!loading && !records.length && !syncError && <p className="sd-transfer-empty">还没有收集或导入记录。</p>}
      <ol className="sd-transfer-list">{records.map(record => {
        const active = isProjectTransferActive(record)
        const progress = projectTransferProgress(record)
        const missing = record.package?.missing_media ?? record.imported?.missing_media ?? []
        const actionPending = pendingActions.includes(record.id)
        const stage = record.stage === 'committing' && record.operation === 'collect' ? '完成项目包' : stageLabels[record.stage] ?? '当前阶段'
        return <li className="sd-transfer-record" key={record.id} data-status={record.status}>
          <div className="sd-transfer-record-heading"><div>{record.status === 'completed' ? <Check size={15} /> : active ? <Loader2 size={15} className="sd-spin" /> : <X size={15} />}<strong>{record.operation === 'collect' ? '收集' : '导入'} · {record.project_name}</strong></div><span>{statusLabels[record.status]}</span></div>
          <time dateTime={record.created_at}>{new Date(record.created_at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' })}</time>
          <p className="sd-transfer-message">{record.message}</p>
          {active && record.status !== 'queued' && <div className="sd-transfer-progress"><div><span>{stage}{record.stage !== 'committing' ? ' · 本阶段进度' : ''}</span>{progress.ratio !== undefined && record.stage !== 'committing' && <span>{Math.floor(progress.ratio * 100)}%</span>}</div><progress value={record.stage === 'committing' ? undefined : progress.ratio} max={1} aria-label={`${record.project_name}：${stage}进度`} /><div><span>{progress.files}</span><span>{progress.bytes}</span></div></div>}
          {record.stage === 'committing' && active && <p className="sd-hint">正在{stage}，此阶段无法取消。</p>}
          {record.error && <p className="sd-transfer-error">{record.error}</p>}
          {record.package && <p className="sd-hint">已收集 {record.package.episode_count} 集、{record.package.media_count} 份媒体。<span className="sd-transfer-filename">{record.package.filename}</span></p>}
          {record.imported && <p className="sd-hint">{record.imported.notice} 共 {record.imported.episode_ids.length} 集、{record.imported.media_count} 份媒体。</p>}
          <div className="sd-transfer-actions">
            {active && record.cancellable && <button className="sd-button" disabled={actionPending} onClick={() => void cancel(record)}><X size={13} />{actionPending ? '正在请求取消…' : '取消任务'}</button>}
            {record.status === 'completed' && record.package && <button className="sd-button sd-primary" disabled={actionPending} onClick={() => void openPackage(record)}>{isDesktop ? <FolderOpen size={14} /> : <Download size={14} />}{actionPending ? '正在打开…' : isDesktop ? '在文件夹中显示' : '下载项目包'}</button>}
            {record.status === 'completed' && record.imported && <button className="sd-button sd-primary" disabled={hasUnsavedEditor || uploading} onClick={() => { if (record.imported) { onOpen(record.imported.project_id); setOpen(false) } }}>打开导入的项目</button>}
          </div>
          {record.imported && hasUnsavedEditor && <p className="sd-hint">保存当前剪辑后即可打开。</p>}
          {missing.length > 0 && <details><summary>{missing.length} 份媒体缺失</summary><ul>{missing.map((item, index) => <li key={`${item.source_path}:${index}`}>{item.source_path}：{item.reason ?? '原文件未找到'}</li>)}</ul><p className="sd-hint">内容和剪辑仍保留，找回原文件后可在项目资产中重新定位。</p></details>}
        </li>
      })}</ol>
      {nextCursor && <button className="sd-button sd-transfer-more" disabled={loadingMore} onClick={() => void loadMore()}>{loadingMore ? '正在读取…' : pageError ? '重试读取更早记录' : '查看更多记录'}</button>}
      {pageError && <p className="sd-hint">更早的记录暂时无法读取，当前记录仍保留。</p>}
      <p className="sd-hint">同一项目已存在时不会覆盖。导入保留制作记录，原先正在执行的制作任务不会自动重启。</p>
    </section></div>}
  </>
}
