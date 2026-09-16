import { useEffect, useState } from 'react'
import { AlertCircle, FolderOpen, RotateCw } from 'lucide-react'

export function StudioStartupFailure({ reason, onRetry }: { reason: string; onRetry: () => void }) {
  const [logPath, setLogPath] = useState('')
  const [logError, setLogError] = useState('')
  useEffect(() => {
    let active = true
    void window.electronAPI.getLogPath().then(result => { if (active) setLogPath(result.logPath) })
      .catch(error => { if (active) setLogError(String(error)) })
    return () => { active = false }
  }, [])
  const openLogs = async () => {
    try {
      const opened = await window.electronAPI.openLogFolder()
      setLogError(opened ? '' : '日志目录尚未创建。可先重试启动。')
    } catch (error) { setLogError(String(error)) }
  }
  return <div className="flex h-screen items-center justify-center bg-[#08090c] p-6 text-zinc-100">
    <section className="max-w-xl rounded-2xl border border-red-500/30 bg-red-950/20 p-6" role="alert">
      <AlertCircle className="h-9 w-9 text-red-400" />
      <h1 className="mt-4 text-lg font-semibold">本地制片核心未能启动</h1>
      <p className="mt-2 break-words text-sm leading-6 text-zinc-300">{reason}</p>
      <p className="mt-3 text-sm leading-6 text-zinc-400">已保存的项目和制作记录仍在本机。启动恢复后可继续使用；重试不会清空项目。</p>
      <div className="mt-5 flex flex-wrap gap-3">
        <button className="flex items-center gap-2 rounded-lg bg-zinc-100 px-4 py-2 text-sm font-semibold text-zinc-950" onClick={onRetry}><RotateCw size={15} />重试启动</button>
        <button className="flex items-center gap-2 rounded-lg border border-zinc-600 px-4 py-2 text-sm" onClick={() => void openLogs()}><FolderOpen size={15} />打开日志文件夹</button>
      </div>
      {logPath && <p className="mt-4 break-all text-xs leading-5 text-zinc-500">本次日志：{logPath}</p>}
      {logError && <p className="mt-2 text-sm text-red-300">{logError}</p>}
    </section>
  </div>
}
