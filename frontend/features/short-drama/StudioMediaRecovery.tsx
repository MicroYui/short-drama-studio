import { useEffect, useRef, useState } from 'react'
import type { ProjectAggregate } from './api'
import { studioRequest } from './studio-api'
import './studio-media-recovery.css'

export function StudioMediaRecovery({ projectId, path, onProject, onNotice, onError }: {
  projectId: string; path: string; onProject: (project: ProjectAggregate) => void; onNotice: (notice: string) => void; onError: (error: unknown) => void
}) {
  const input = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const target = `${projectId}:${path}`
  const currentTarget = useRef(target)
  currentTarget.current = target
  useEffect(() => { currentTarget.current = target; return () => { currentTarget.current = '' } }, [target])
  return <div className="sd-media-recovery">
    <button className="sd-button" disabled={busy} onClick={() => input.current?.click()}>{busy ? '正在恢复文件…' : '重新定位原文件'}</button>
    <input ref={input} type="file" hidden accept="image/*,video/*,audio/*" onChange={event => {
      const file = event.target.files?.[0]; event.target.value = ''; if (!file) return
      const body = new FormData(); body.set('file', file); body.set('old_path', path)
      setBusy(true); onNotice('')
      void studioRequest<{ project: ProjectAggregate; notice: string }>(`/api/short-drama/studio/projects/${encodeURIComponent(projectId)}/media/relink`, { method: 'POST', body })
        .then(result => { if (currentTarget.current === target) onNotice(result.notice); onProject(result.project) }).catch(onError).finally(() => setBusy(false))
    }} />
    <p className="sd-hint">选择移动后的同一原文件，恢复各集中的引用和剪辑。更换画面或声音请使用导入替换。</p>
  </div>
}
