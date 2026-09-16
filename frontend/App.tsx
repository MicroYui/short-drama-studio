import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { PythonSetup } from './components/PythonSetup'
import { StudioStartupFailure } from './components/StudioStartupFailure'
import { StudioWorkspace } from './features/short-drama/StudioWorkspace'
import { useBackend } from './hooks/use-backend'
import { logger } from './lib/logger'

export default function App() {
  return window.electronAPI ? <DesktopStudio /> : <StudioWorkspace />
}

function DesktopStudio() {
  const [pythonReady, setPythonReady] = useState<boolean | null>(null)
  const [startupAttempt, setStartupAttempt] = useState(0)
  const [starting, setStarting] = useState(true)
  const [startupError, setStartupError] = useState<string | null>(null)
  const [workspaceOpened, setWorkspaceOpened] = useState(false)
  const { connected, processStatus, isLoading } = useBackend()
  const workspaceReady = pythonReady === true && connected && !isLoading

  useEffect(() => {
    if (workspaceReady) setWorkspaceOpened(true)
  }, [workspaceReady])

  useEffect(() => {
    let cancelled = false
    const check = async () => {
      let stage = '检查本地运行环境'
      try {
        const result = await window.electronAPI.checkPythonReady()
        if (!cancelled) setPythonReady(result.ready)
        if (result.ready) {
          stage = '启动本地制片服务'
          await window.electronAPI.startPythonBackend()
        }
      } catch (error) {
        logger.error(`Failed to prepare Studio backend: ${error}`)
        if (!cancelled) setStartupError(`${stage}失败：${error instanceof Error ? error.message : String(error)}`)
      } finally {
        if (!cancelled) setStarting(false)
      }
    }
    void check()
    return () => { cancelled = true }
  }, [startupAttempt])

  const retryStartup = () => {
    setStartupError(null)
    setStarting(true)
    setStartupAttempt(value => value + 1)
  }

  // Keep the same workspace instance after its first successful connection.
  // Replacing it with a launch screen would discard every unsaved editor/chat input.
  if (workspaceOpened || workspaceReady) {
    return <StudioWorkspace backendStatus={processStatus ?? 'restarting'} onReconnect={() => window.electronAPI.startPythonBackend()} />
  }

  if (startupError || (pythonReady === true && processStatus === 'dead' && !starting)) {
    return <StudioStartupFailure reason={startupError ?? '本地服务已退出。可重试启动，或打开本次日志查看具体原因。'} onRetry={retryStartup} />
  }

  if (pythonReady === null) {
    return <LaunchStatus title="正在准备短剧工作台" detail="检查本地运行环境…" />
  }

  if (!pythonReady) {
    return <PythonSetup onReady={() => { setPythonReady(true); retryStartup() }} />
  }

  return <LaunchStatus title="正在打开工作台" detail="准备项目、素材和剪辑工具…" />
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
