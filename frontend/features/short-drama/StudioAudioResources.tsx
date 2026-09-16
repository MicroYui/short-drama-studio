import { useEffect, useState } from 'react'
import { Download, FolderOpen, Loader2, RefreshCw } from 'lucide-react'
import type { components } from '../../generated/backend-openapi'
import { studioRequest } from './studio-api'

type Resources = components['schemas']['AudioResources']
type Prepare = components['schemas']['AudioResourcePrepare']
const endpoint = '/api/short-drama/studio/audio-resources'
const labels = { ready: '本机缓存可用', not_prepared: '尚未准备', preparing: '正在准备', failed: '准备失败', disabled: '未启用' }

export function StudioAudioResources() {
  const [resources, setResources] = useState<Resources | null>(null)
  const [refresh, setRefresh] = useState(0)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async () => {
      try {
        const value = await studioRequest<Resources>(endpoint)
        if (!active) return
        setResources(value); setError('')
        if (value.resources?.some(item => item.status === 'preparing')) timer = setTimeout(() => void poll(), 2000)
      } catch (failure) { if (active) setError(failure instanceof Error ? failure.message : String(failure)) }
    }
    void poll()
    return () => { active = false; clearTimeout(timer) }
  }, [refresh])
  const prepare = async (request: Prepare) => {
    setPending(true); setError('')
    try {
      setResources(await studioRequest<Resources>(`${endpoint}/prepare`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request),
      }))
      setRefresh(value => value + 1)
    } catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)) }
    finally { setPending(false) }
  }
  const openLogs = async () => {
    try { if (!await window.electronAPI.openLogFolder()) setError('日志目录尚未创建。') }
    catch (failure) { setError(String(failure)) }
  }
  return <section className="sd-provider-card" aria-label="声音分析资源">
    <div><h3>声音分析资源</h3><span className="sd-provider-model">本机听审与字幕对齐 · 按需准备</span></div>
    <p className="sd-hint">{resources?.message ?? '检查本机缓存…播放、剪辑和导出无需准备这些资源。'}</p>
    {resources?.resources?.map(item => <div key={item.id} className="sd-provider-card">
      <div><strong>{item.label}</strong><span className="sd-provider-model">{item.model ?? '未配置'}</span><span role="status" className="sd-provider-configured">{labels[item.status]}</span></div>
      {item.error && <p className="sd-hint" style={{ overflowWrap: 'anywhere' }} role="alert">{item.error}</p>}
      {Boolean(item.missing_files?.length) && <p className="sd-hint">缺少：{item.missing_files?.join('、')}</p>}
      {item.can_prepare && item.status !== 'ready' && <button className="sd-button" disabled={pending || item.status === 'preparing'} onClick={() => void prepare({ resource: item.id })}>
        {item.status === 'preparing' ? <Loader2 size={14} className="sd-spin" /> : <Download size={14} />}{item.status === 'preparing' ? '正在准备，可关闭设置继续工作' : item.status === 'failed' ? '重试准备（需联网）' : '准备资源（需联网）'}
      </button>}
      {!item.can_prepare && item.status === 'not_prepared' && <p className="sd-hint">请补齐上面的本地模型目录，然后刷新状态。</p>}
    </div>)}
    {resources?.supported && <p className="sd-hint">仅点击“准备资源”时从 Hugging Face 下载所需文件。准备失败可重试，已下载的缓存会继续复用。</p>}
    <div className="sd-provider-actions"><span role="alert">{error}</span><button className="sd-button" onClick={() => setRefresh(value => value + 1)}><RefreshCw size={14} />刷新状态</button>
      {window.electronAPI && <button className="sd-button" onClick={() => void openLogs()}><FolderOpen size={14} />打开日志</button>}
    </div>
  </section>
}
