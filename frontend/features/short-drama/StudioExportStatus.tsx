import { useCallback, useEffect, useState } from 'react'
import { Check, Download, Loader2, RotateCcw, X } from 'lucide-react'
import type { ProjectAggregate } from './api'
import { StudioApi, type StudioExport } from './studio-api'
import { isStudioExportActive, mergeStudioExports, studioExportRetryRequest } from './studio-export-status'
import './studio-export-status.css'

interface Props {
  project: ProjectAggregate
  createdExport?: StudioExport | null
  onRecords: (episodeId: string, records: StudioExport[]) => void
  onError: (error: unknown) => void
  onRetry?: (record: StudioExport) => Promise<StudioExport>
  onBusyChange?: (busy: boolean) => void
}

const statusLabels: Record<string, string> = { running: '正在导出', cancelling: '正在取消', cancelled: '已取消', failed: '导出失败', completed: '导出完成' }

export function StudioExportStatus({ project, createdExport, onRecords, onError, onRetry, onBusyChange }: Props) {
  const id = project.document.project.id
  const stored = (project.document.studio_exports ?? []) as StudioExport[]
  const [records, setRecords] = useState(() => mergeStudioExports([], stored))
  const [syncError, setSyncError] = useState(false)
  const [pendingAction, setPendingAction] = useState<string | null>(null)
  const update = useCallback((incoming: StudioExport[]) => setRecords(current => mergeStudioExports(current, incoming)), [])
  const receive = useCallback((incoming: StudioExport[]) => { update(incoming); onRecords(id, incoming) }, [id, update, onRecords])
  useEffect(() => { update(stored) }, [project.document.studio_exports, update])
  useEffect(() => { if (createdExport) receive([createdExport]) }, [createdExport, receive])
  useEffect(() => {
    let active = true
    void StudioApi.exports(id).then(result => { if (active) { receive(result.exports); setSyncError(false) } }).catch(() => { if (active) setSyncError(true) })
    return () => { active = false }
  }, [id, receive])
  const activeIds = records.filter(isStudioExportActive).map(record => record.id).join(',')
  useEffect(() => { onBusyChange?.(Boolean(activeIds)) }, [activeIds, onBusyChange])
  useEffect(() => {
    if (!activeIds && !syncError) return
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async () => {
      try {
        const incoming = activeIds ? await Promise.all(activeIds.split(',').map(exportId => StudioApi.exportStatus(id, exportId))) : (await StudioApi.exports(id)).exports
        if (!active) return
        receive(incoming)
        setSyncError(false)
      } catch { if (active) setSyncError(true) }
      if (active) timer = setTimeout(() => { void poll() }, 1600)
    }
    timer = setTimeout(() => { void poll() }, 1600)
    return () => { active = false; if (timer) clearTimeout(timer) }
  }, [activeIds, syncError, id, receive])

  const cancel = async (record: StudioExport) => {
    setPendingAction(record.id)
    try { receive([await StudioApi.cancelExport(id, record.id)]) } catch (error) { onError(error) } finally { setPendingAction(null) }
  }
  const retry = async (record: StudioExport) => {
    setPendingAction(record.id)
    try {
      const next = onRetry ? await onRetry(record) : await StudioApi.exportTimeline(id, studioExportRetryRequest(record, Number(project.document.editorial_timeline?.revision ?? 0)))
      receive([next])
    } catch (error) { onError(error) } finally { setPendingAction(null) }
  }
  const shown = records.filter((record, index) => index === 0 || isStudioExportActive(record))
  if (!shown.length && !syncError) return null
  return <section className="sd-export-jobs" aria-label="最近导出" aria-live="polite">
    {shown.map(record => <div className="sd-export-job" key={record.id}>
      {isStudioExportActive(record) ? <Loader2 size={15} className="sd-spin" /> : record.status === 'completed' ? <Check size={15} /> : <X size={15} />}
      <div className="sd-export-job-detail"><strong>{statusLabels[record.status] ?? record.status}</strong><span>{record.progress} · {record.width} × {record.height} · {record.fps} fps</span>{record.error && <p>{record.error}</p>}</div>
      <div className="sd-export-job-actions">
        {isStudioExportActive(record) && <button className="sd-button" disabled={pendingAction !== null || record.status === 'cancelling'} onClick={() => void cancel(record)}><X size={13} />{record.status === 'cancelling' ? '正在停止…' : '取消导出'}</button>}
        {['failed', 'cancelled'].includes(record.status) && <button className="sd-button" disabled={pendingAction !== null || Boolean(activeIds)} onClick={() => void retry(record)}><RotateCcw size={13} />按当前剪辑重试</button>}
        {record.status === 'completed' && record.video_path && <button className="sd-button" onClick={() => void StudioApi.downloadMedia(id, record.video_path!, `${project.document.project.name}.mp4`).catch(onError)}><Download size={13} />下载视频</button>}
      </div>
    </div>)}
    {syncError && <p className="sd-hint">导出状态暂时无法更新，正在重新连接。</p>}
  </section>
}
