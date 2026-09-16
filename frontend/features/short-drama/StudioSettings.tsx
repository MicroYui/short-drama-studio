import { useEffect, useState } from 'react'
import { Check, ExternalLink, Loader2, Save, X } from 'lucide-react'
import { ShortDramaApi, type AgentCapabilityCatalog, type H3ProviderConfig, type ImageProviderConfig, type LanguageProviderConfig, type ProjectAggregate } from './api'
import { StudioApi, studioRequest, unwrapApi } from './studio-api'

import { methodCategoryLabels, StudioMethodDetail } from './StudioMethodDetail'

import { StudioWorkflows } from './StudioWorkflows'
import { StudioAudioResources } from './StudioAudioResources'
import { StudioAppearance, type StudioTheme } from './StudioAppearance'

type ComfyConfig = { base_url: string; connected: boolean; message: string }

function ProviderCard({ title, model, initialUrl, configured, editableUrl, onSave, onValidate }: { title: string; model: string; initialUrl: string; configured: boolean; editableUrl: boolean; onSave: (key: string | null, url: string) => Promise<unknown>; onValidate: () => Promise<unknown> }) {
  const [url, setUrl] = useState(initialUrl)
  const [key, setKey] = useState('')
  const [state, setState] = useState('')
  const [busy, setBusy] = useState(false)
  const run = async (validate: boolean) => {
    setBusy(true); setState('')
    try { await onSave(key.trim() || null, url.trim()); setKey(''); if (validate) await onValidate(); setState(validate ? '连接验证通过' : '已保存') } catch (error) { setState(error instanceof Error ? error.message : '连接未完成') } finally { setBusy(false) }
  }
  return <section className="sd-provider-card"><div><h3>{title}</h3><span className="sd-provider-model">{model}</span><span className="sd-provider-configured">{configured ? '已配置凭据' : '尚未配置凭据'}</span></div><label className="sd-field">服务地址<input value={url} readOnly={!editableUrl} onChange={event => setUrl(event.target.value)} /></label><label className="sd-field">API Key<input type="password" autoComplete="off" value={key} placeholder={configured ? '已保存 · 留空继续使用' : '输入你的 API Key'} onChange={event => setKey(event.target.value)} /></label><div className="sd-provider-actions"><span role="status">{state}</span><button className="sd-button" disabled={busy} onClick={() => void run(false)}><Save size={13} />保存</button><button className="sd-button" disabled={busy} onClick={() => void run(true)}>{busy ? <Loader2 size={14} className="sd-spin" /> : <Check size={14} />}验证连接</button></div></section>
}

export function StudioSettings({ project, theme, onTheme, onProject, onClose, onError }: { project: ProjectAggregate | null; theme: StudioTheme; onTheme: (theme: StudioTheme) => void; onProject: (project: ProjectAggregate) => void; onClose: () => void; onError: (error: unknown) => void }) {
  const [tab, setTab] = useState<'appearance' | 'methods' | 'providers' | 'advanced'>('appearance')
  const [language, setLanguage] = useState<LanguageProviderConfig | null>(null)
  const [image, setImage] = useState<ImageProviderConfig | null>(null)
  const [video, setVideo] = useState<H3ProviderConfig | null>(null)
  const videoCapabilities = video as (H3ProviderConfig & { supported_resolutions?: string[]; supported_aspect_ratios?: string[] }) | null
  const videoResolutions = videoCapabilities?.supported_resolutions?.join(' / ')
  const videoRatios = videoCapabilities?.supported_aspect_ratios?.filter(value => value !== 'adaptive').join('、')
  const [catalog, setCatalog] = useState<AgentCapabilityCatalog | null>(null)
  const [methodIds, setMethodIds] = useState<string[]>(project?.document.project.preferred_method_skill_ids ?? [])
  const [direction, setDirection] = useState(project?.document.project.creative_direction ?? '')
  const [aspect, setAspect] = useState(project?.document.project.aspect_ratio ?? '16:9')
  const [saved, setSaved] = useState(false)
  const [detailMethod, setDetailMethod] = useState<string | null>(null)
  const [methodCategory, setMethodCategory] = useState('all')
  const [comfy, setComfy] = useState<ComfyConfig | null>(null)
  const [comfyUrl, setComfyUrl] = useState('http://127.0.0.1:8188')
  const [comfyBusy, setComfyBusy] = useState(false)
  useEffect(() => {
    void Promise.allSettled([ShortDramaApi.getLanguageConfig().then(unwrapApi).then(setLanguage), ShortDramaApi.getImageConfig().then(unwrapApi).then(setImage), ShortDramaApi.getH3Config().then(unwrapApi).then(setVideo), ShortDramaApi.getAgentCapabilities().then(unwrapApi).then(setCatalog)]).then(results => { const failure = results.find(result => result.status === 'rejected'); if (failure?.status === 'rejected') onError(failure.reason) })
    void studioRequest<ComfyConfig>('/api/short-drama/studio/comfyui').then(result => { setComfy(result); setComfyUrl(result.base_url) }).catch(() => setComfy(null))
  }, [onError])
  const savePreferences = async () => { if (!project) return; try { onProject(await StudioApi.preferences(project.document.project.id, { method_skill_ids: methodIds, creative_direction: direction, aspect_ratio: aspect })); setSaved(true) } catch (error) { onError(error) } }
  const saveVideoConfig = async (key: string | null) => {
    setVideo(unwrapApi(await ShortDramaApi.updateH3Config(key)))
  }
  return <div className="sd-modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}><section className="sd-settings" role="dialog" aria-label="工作台设置"><header className="sd-modal-heading"><div><span className="sd-eyebrow">STUDIO SETTINGS</span><h2>工作台设置</h2></div><button className="sd-icon" aria-label="关闭设置" onClick={onClose}><X size={20} /></button></header><div className="sd-settings-tabs">{([['appearance', '外观'], ['providers', '模型与连接'], ['methods', '创作方法'], ['advanced', '高级工作流']] as const).map(([key, label]) => <button key={key} className={tab === key ? 'is-active' : ''} onClick={() => setTab(key)}>{label}</button>)}</div><div className="sd-settings-body">
    {tab === 'appearance' && <StudioAppearance theme={theme} onTheme={onTheme} />}
    {tab === 'providers' && <><p className="sd-hint">语言角色共享同一配置。图片和视频使用各自的服务；本机负责项目保存、剪辑与导出。</p>{language && <ProviderCard title="语言与 Agent" model={`${language.model} · ${language.reasoning_effort}`} initialUrl={language.base_url} configured={language.has_api_key} editableUrl onSave={(key, url) => ShortDramaApi.updateLanguageConfig(key, url).then(unwrapApi).then(setLanguage)} onValidate={() => ShortDramaApi.validateLanguageConfig().then(unwrapApi)} />}{image && <ProviderCard title="图片生成" model={image.model} initialUrl={image.base_url} configured={image.has_api_key} editableUrl onSave={(key, url) => ShortDramaApi.updateImageConfig(key, url).then(unwrapApi).then(setImage)} onValidate={() => ShortDramaApi.validateImageConfig().then(unwrapApi)} />}{video && <ProviderCard title="视频生成" model={`${video.model} · 默认 ${video.resolution}${videoResolutions ? ` · 可选 ${videoResolutions}` : ''}`} initialUrl={video.base_url} configured={video.has_api_key} editableUrl={false} onSave={saveVideoConfig} onValidate={() => ShortDramaApi.validateH3Config().then(unwrapApi)} />}<StudioAudioResources /></>}
    {tab === 'methods' && <><p className="sd-hint">默认由 Agent 按作品目标选择方法。你可以指定偏好，具体画风和内容以你的要求为准。</p><button className={`sd-method-auto ${!methodIds.length ? 'is-active' : ''}`} onClick={() => { setMethodIds([]); setSaved(false) }}><span>由 Agent 选择</span>{!methodIds.length && <Check size={15} />}</button><label className="sd-field">方法类型<select aria-label="方法类型" value={methodCategory} onChange={event => setMethodCategory(event.target.value)}><option value="all">全部方法</option>{Object.entries(methodCategoryLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><div className="sd-method-grid">{catalog?.production_methods.filter(method => methodCategory === 'all' || (method.category ?? 'creative') === methodCategory).map(method => <article className={methodIds.includes(method.id) ? 'sd-method-option is-active' : 'sd-method-option'} key={method.id}><button className="sd-method-toggle" aria-pressed={methodIds.includes(method.id)} onClick={() => { setMethodIds(previous => previous.includes(method.id) ? previous.filter(id => id !== method.id) : [...previous, method.id]); setSaved(false) }}><span className="sd-method-category">{methodCategoryLabels[method.category ?? 'creative']}</span><strong>{method.label}</strong><p>{method.description}</p>{Boolean(method.requirements?.length) && <span className="sd-method-connection">{method.category === 'software' ? '需外部软件连接' : '包含执行条件'}</span>}{methodIds.includes(method.id) && <Check size={15} />}</button><button className="sd-method-open" onClick={() => setDetailMethod(method.id)}>查看专业方法</button></article>)}</div>{detailMethod && <StudioMethodDetail id={detailMethod} selected={project?.document.project.preferred_method_skill_ids?.includes(detailMethod) ?? false} onClose={() => setDetailMethod(null)} />}<label className="sd-field">项目与剪辑画幅<select value={aspect} onChange={event => { setAspect(event.target.value); setSaved(false) }}><option value="16:9">16:9 · 横屏</option><option value="9:16">9:16 · 竖屏</option><option value="1:1">1:1 · 方形</option></select></label><p className="sd-hint sd-generation-capability">{videoResolutions && videoRatios ? `当前 H3 可选 ${videoResolutions}，支持 ${videoRatios}。` : '视频画幅和清晰度由本次生成请求确定。'}首尾帧生成的画幅跟随输入图片；具体素材以实际生成结果为准。</p><label className="sd-field">创作方向<textarea value={direction} onChange={event => { setDirection(event.target.value); setSaved(false) }} placeholder="例如：自然光、克制的镜头运动；对白保留口语感。" /></label><button className="sd-button sd-primary" disabled={!project} onClick={() => void savePreferences()}>{saved ? <Check size={15} /> : <Save size={15} />}{saved ? '已保存到当前集' : '保存创作设置'}</button></>}
    {tab === 'advanced' && <><div className="sd-advanced-intro"><span className="sd-eyebrow">COMFYUI</span><h3>需要更多处理能力时，再展开。</h3><p>连接自己的 ComfyUI 服务，用于超分、插帧或自定义工作流。输出可以回到项目资产继续使用。</p></div><label className="sd-field">ComfyUI 地址<input value={comfyUrl} onChange={event => setComfyUrl(event.target.value)} /></label><div className="sd-provider-actions"><span>{comfy?.message ?? '尚未连接 ComfyUI 服务'}</span><button className="sd-button" disabled={comfyBusy} onClick={() => { setComfyBusy(true); void studioRequest<ComfyConfig>('/api/short-drama/studio/comfyui', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ base_url: comfyUrl }) }).then(setComfy).catch(onError).finally(() => setComfyBusy(false)) }}>{comfyBusy ? <Loader2 size={14} className="sd-spin" /> : <Check size={14} />}保存并连接</button></div>{comfy?.connected && <a className="sd-button" href={comfy.base_url} target="_blank" rel="noreferrer"><ExternalLink size={14} />打开高级工作流</a>}{project && <StudioWorkflows project={project} connected={Boolean(comfy?.connected)} onProject={onProject} onError={onError} />}<p className="sd-hint">高级工作流使用单独配置的服务。连接后可将生成或处理结果导入项目资产，继续参与镜头制作。</p></>}
  </div></section></div>
}
