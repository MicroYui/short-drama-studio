import { useEffect, useState } from 'react'
import { AlertCircle, Loader2 } from 'lucide-react'
import { PythonSetup } from './components/PythonSetup'
import { StudioWorkspace } from './features/short-drama/StudioWorkspace'
import { useBackend } from './hooks/use-backend'
import { logger } from './lib/logger'

export default function App() {
  return window.electronAPI ? <DesktopStudio /> : <StudioWorkspace />
}

function DesktopStudio() {
  const [pythonReady, setPythonReady] = useState<boolean | null>(null)
  const [backendStartRequested, setBackendStartRequested] = useState(false)
  const { connected, processStatus, isLoading } = useBackend()

  useEffect(() => {
    let cancelled = false
    const check = async () => {
      try {
        const result = await window.electronAPI.checkPythonReady()
        if (!cancelled) setPythonReady(result.ready)
      } catch (error) {
        logger.error(`Failed to check Python readiness: ${error}`)
        if (!cancelled) setPythonReady(true)
      }
    }
    void check()
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (pythonReady !== true || backendStartRequested) return
    setBackendStartRequested(true)
    void window.electronAPI.startPythonBackend().catch((error) => {
      logger.error(`Failed to start Python backend: ${error}`)
    })
  }, [backendStartRequested, pythonReady])

  if (pythonReady === null) {
    return <LaunchStatus title="正在准备短剧工作台" detail="检查本地运行环境…" />
  }

  if (!pythonReady) {
    return <PythonSetup onReady={() => setPythonReady(true)} />
  }

  if (processStatus === 'dead') {
    return (
      <div className="flex h-screen items-center justify-center bg-[#08090c] p-6 text-zinc-100">
        <div className="max-w-md rounded-2xl border border-red-500/30 bg-red-950/20 p-6 text-center">
          <AlertCircle className="mx-auto h-10 w-10 text-red-400" />
          <h1 className="mt-4 text-lg font-semibold">本地制片核心未能启动</h1>
          <p className="mt-2 text-sm leading-6 text-zinc-400">请重新启动应用。已保存的项目和制作记录仍在本机，可继续编辑。</p>
          <button className="mt-5 rounded-lg bg-zinc-100 px-4 py-2 text-sm font-semibold text-zinc-950" onClick={() => window.location.reload()}>重新加载</button>
        </div>
      </div>
    )
  }

  if (isLoading || !connected) {
    return <LaunchStatus title="正在打开工作台" detail="准备项目、素材和剪辑工具…" />
  }

  return <StudioWorkspace />
}

function LaunchStatus({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="flex h-screen items-center justify-center bg-[#08090c] text-zinc-100">
      <div className="text-center">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-500/10">
          <Loader2 className="h-7 w-7 animate-spin text-blue-400" />
        </div>
        <h1 className="mt-5 text-lg font-semibold">{title}</h1>
        <p className="mt-2 text-sm text-zinc-500">{detail}</p>
      </div>
    </div>
  )
}
