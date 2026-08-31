import { useEffect, useMemo, useState } from 'react'
import {
  AudioLines,
  Check,
  CircleX,
  FileUp,
  Image as ImageIcon,
  KeyRound,
  Link,
  Loader2,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Sparkles,
  WandSparkles,
} from 'lucide-react'
import { pathToFileUrl } from '../../lib/file-url'
import {
  ShortDramaApi,
  type Asset,
  type AssetCoverageResponse,
  type AssetKind,
  type AssetMediaType,
  type AssetVersion,
  type ImageGenerationTask,
  type ImageProviderConfig,
  type ImageTaskCreateRequest,
  type LanguageProviderConfig,
  type ProjectAggregate,
  type StockAssetCandidate,
  type StockAssetImportMode,
  type StockAssetProvider,
  type StockProviderConfig,
} from './api'
import { VoiceReferencePanel } from './VoiceReferencePanel'

const primaryButton = 'inline-flex items-center justify-center gap-2 rounded-lg bg-blue-500 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-blue-400 disabled:cursor-not-allowed disabled:opacity-40'
const secondaryButton = 'inline-flex items-center justify-center gap-2 rounded-lg border border-zinc-700 bg-zinc-900 px-3.5 py-2 text-sm font-medium text-zinc-200 transition hover:border-zinc-600 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40'
const subtleButton = 'inline-flex items-center justify-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-zinc-300 transition hover:bg-zinc-800 hover:text-white disabled:cursor-not-allowed disabled:opacity-40'
const field = 'w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-200 outline-none transition placeholder:text-zinc-700 focus:border-blue-500/70'

type ResultLike<T> =
  | { ok: true; data: T }
  | { ok: false; error: { message: string } }

type Props = {
  project: ProjectAggregate
  onProjectChange: (project: ProjectAggregate) => void
  onError: (message: string | null) => void
  onNotice: (message: string | null) => void
}

function Badge({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: 'neutral' | 'blue' | 'green' | 'amber' | 'red' }) {
  const tones = {
    neutral: 'border-zinc-700 bg-zinc-800/70 text-zinc-300',
    blue: 'border-blue-500/30 bg-blue-500/10 text-blue-300',
    green: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
    amber: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
    red: 'border-red-500/30 bg-red-500/10 text-red-300',
  }
  return <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${tones[tone]}`}>{children}</span>
}

function statusTone(status: string): 'neutral' | 'blue' | 'green' | 'amber' | 'red' {
  if (status === 'SUCCEEDED' || status === 'APPROVED') return 'green'
  if (status === 'FAILED' || status === 'REJECTED') return 'red'
  if (status === 'SUBMIT_UNKNOWN' || status === 'OUTPUT_MISSING') return 'amber'
  if (status === 'SUBMITTING' || status === 'QUEUED') return 'blue'
  return 'neutral'
}

function currentVersion(asset: Asset): AssetVersion {
  return asset.versions.find((item) => item.id === asset.current_version_id) ?? asset.versions[asset.versions.length - 1]
}

function versionUrl(version: AssetVersion | undefined): string | null {
  if (!version) return null
  if (version.local_path) return pathToFileUrl(version.local_path)
  return version.external_url ?? null
}

function defaultPrompt(asset: Asset): string {
  const descriptor = currentVersion(asset).descriptor
  if (asset.kind === 'CHARACTER') return `基于以下设定生成一张单幅真人全身身份母版：${descriptor}。正面略带四分之三角度，完整展示脸、发型、体型、鞋和整套服装，背景干净中性，竖屏9:16。不要宫格、拼贴、多人、文字、logo或水印。`
  if (asset.kind === 'LOCATION') return `基于以下设定生成一张单幅真人空场 Master Wide：${descriptor}。清楚交代总布局、摄影轴线、入口、出口、通道、门窗、桌椅和主光源，竖屏9:16。不要人物、拼贴、多个冲突机位、文字、logo或水印。`
  if (asset.kind === 'PROP') return `基于以下设定生成一张单幅三分之四角度产品母版：${descriptor}。清楚展示真实尺度、形状、颜色、部件数量和材质，背景干净中性。不要完整人物、场景、额外部件、文字、logo或水印。`
  return `真人都市短剧的高质量写实参考素材：${descriptor}。保持所有已批准身份与空间关系，单幅构图，无文字、logo或水印。`
}

function defaultDescriptor(asset: Asset): string {
  return `${asset.name} · GPT Image 2 首批可审片候选`
}

function stockOrientation(asset: Asset): 'ANY' | 'PORTRAIT' | 'LANDSCAPE' | 'SQUARE' {
  if (asset.kind === 'CHARACTER') return 'PORTRAIT'
  if (asset.kind === 'LOCATION') return 'LANDSCAPE'
  if (asset.kind === 'PROP') return 'SQUARE'
  return 'ANY'
}

function money(amount: number | null | undefined): string {
  return amount === null || amount === undefined ? '—' : `$${amount.toFixed(5)}`
}

export function AssetWorkbenchPanel({ project, onProjectChange, onError, onNotice }: Props) {
  const projectId = project.document.project.id
  const [selectedAssetId, setSelectedAssetId] = useState(project.document.assets[0]?.id ?? '')
  const [selectedVersionId, setSelectedVersionId] = useState('')
  const [config, setConfig] = useState<ImageProviderConfig | null>(null)
  const [languageConfig, setLanguageConfig] = useState<LanguageProviderConfig | null>(null)
  const [stockConfigs, setStockConfigs] = useState<StockProviderConfig[]>([])
  const [coverage, setCoverage] = useState<AssetCoverageResponse | null>(null)
  const [tasks, setTasks] = useState<ImageGenerationTask[]>([])
  const [apiKey, setApiKey] = useState('')
  const [imageBaseUrl, setImageBaseUrl] = useState('https://api.openai.com/v1')
  const [languageApiKey, setLanguageApiKey] = useState('')
  const [languageBaseUrl, setLanguageBaseUrl] = useState('https://api.openai.com/v1')
  const [stockProvider, setStockProvider] = useState<StockAssetProvider>('OPENVERSE')
  const [stockApiKey, setStockApiKey] = useState('')
  const [stockBaseUrl, setStockBaseUrl] = useState('https://api.openverse.org/v1/images')
  const [stockImportMode, setStockImportMode] = useState<StockAssetImportMode>('AUTO')
  const [stockQuery, setStockQuery] = useState('')
  const [stockCandidates, setStockCandidates] = useState<StockAssetCandidate[]>([])
  const [stockWarnings, setStockWarnings] = useState<string[]>([])
  const [prompt, setPrompt] = useState('')
  const [descriptor, setDescriptor] = useState('')
  const [operation, setOperation] = useState<'GENERATE' | 'EDIT'>('GENERATE')
  const [size, setSize] = useState('768x1344')
  const [background, setBackground] = useState<'opaque' | 'transparent'>('opaque')
  const [selectedInputs, setSelectedInputs] = useState<string[]>([])
  const [primaryInputVersionId, setPrimaryInputVersionId] = useState('')
  const [maskVersionId, setMaskVersionId] = useState('')
  const [maskFeatherPixels, setMaskFeatherPixels] = useState(8)
  const [externalUrl, setExternalUrl] = useState('')
  const [externalMediaType, setExternalMediaType] = useState<AssetMediaType>('IMAGE')
  const [reviewNotes, setReviewNotes] = useState('符合角色、场景或道具设定，可作为后续 H3 正式参考。')
  const [busy, setBusy] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [newAssetKind, setNewAssetKind] = useState<AssetKind>('REFERENCE')
  const [newAssetName, setNewAssetName] = useState('')
  const [newAssetDescriptor, setNewAssetDescriptor] = useState('')

  const asset = project.document.assets.find((item) => item.id === selectedAssetId) ?? project.document.assets[0]
  const version = asset?.versions.find((item) => item.id === selectedVersionId) ?? (asset ? currentVersion(asset) : undefined)
  const imageVersions = useMemo(
    () => project.document.assets.flatMap((item) => item.versions.map((candidate) => ({ asset: item, version: candidate })))
      .filter((item) => item.version.media_type === 'IMAGE' && Boolean(item.version.local_path || item.version.external_url)),
    [project],
  )
  const referencePacks = project.document.reference_packs ?? []
  const referenceEntryCount = useMemo(
    () => referencePacks.reduce((sum, pack) => sum + (pack.entries ?? []).length, 0),
    [project.document.reference_packs],
  )

  const loadConfig = async () => {
    const result = await ShortDramaApi.getImageConfig()
    if (!result.ok) { onError(result.error.message); return }
    setConfig(result.data)
    setImageBaseUrl(result.data.base_url)
  }

  const loadLanguageConfig = async () => {
    const result = await ShortDramaApi.getLanguageConfig()
    if (!result.ok) { onError(result.error.message); return }
    setLanguageConfig(result.data)
    setLanguageBaseUrl(result.data.base_url)
  }

  const loadStockConfigs = async () => {
    const result = await ShortDramaApi.getStockProviderConfigs()
    if (!result.ok) { onError(result.error.message); return }
    setStockConfigs(result.data.providers)
    const selected = result.data.providers.find((item) => item.provider === stockProvider)
    if (selected) setStockBaseUrl(selected.base_url)
  }

  const loadTasks = async () => {
    const result = await ShortDramaApi.listImageTasks(projectId, asset?.id)
    if (!result.ok) { onError(result.error.message); return }
    setTasks(result.data.tasks)
  }

  const loadCoverage = async () => {
    const result = await ShortDramaApi.inspectAssetCoverage(projectId)
    if (!result.ok) { onError(result.error.message); return }
    setCoverage(result.data)
  }

  useEffect(() => { void loadConfig() }, [])
  useEffect(() => { void loadLanguageConfig() }, [])
  useEffect(() => { void loadStockConfigs() }, [])
  useEffect(() => { void loadTasks() }, [projectId, asset?.id])
  useEffect(() => { void loadCoverage() }, [projectId, referenceEntryCount])
  useEffect(() => {
    if (!asset) return
    const active = currentVersion(asset)
    setSelectedVersionId(active.id)
    setPrompt(defaultPrompt(asset))
    setDescriptor(defaultDescriptor(asset))
    const isProp = asset.kind === 'PROP'
    setSize(isProp ? '1024x1024' : '768x1344')
    setBackground('opaque')
    setOperation('GENERATE')
    setSelectedInputs([])
    setPrimaryInputVersionId('')
    setMaskVersionId('')
    setMaskFeatherPixels(8)
    setStockCandidates([])
    setStockWarnings([])
    setStockImportMode(asset.kind === 'CHARACTER' ? 'DERIVE' : 'AUTO')
  }, [asset?.id])

  const perform = async <T,>(label: string, action: () => Promise<ResultLike<T>>, onSuccess: (value: T) => void) => {
    setBusy(label)
    onError(null)
    const result = await action()
    setBusy(null)
    if (!result.ok) { onError(result.error.message); return false }
    onSuccess(result.data)
    return true
  }

  const saveKey = () => void perform('保存 GPT Image 2 连接', () => ShortDramaApi.updateImageConfig(apiKey || null, imageBaseUrl.trim()), (value) => {
    const normalized = value.base_url !== imageBaseUrl.trim().replace(/\/$/, '')
    setConfig(value)
    setImageBaseUrl(value.base_url)
    setApiKey('')
    onNotice(normalized ? 'GPT Image 2 连接已保存；旧 Azure api-version 已自动升级' : 'GPT Image 2 的地址与 API Key 已保存在本机配置中')
  })

  const validateKey = () => void perform('验证 GPT Image 2 连接', () => ShortDramaApi.validateImageConfig(), (value) => {
    setConfig(value)
    onNotice(value.base_url.includes('.openai.azure.com') ? 'Azure GPT Image 凭证与部署 endpoint 无生成探测通过' : 'GPT Image 2 凭证与模型访问验证通过')
  })

  const saveLanguageConnection = () => void perform('保存 GPT-5.6-sol 连接', () => ShortDramaApi.updateLanguageConfig(languageApiKey || null, languageBaseUrl.trim()), (value) => {
    setLanguageConfig(value)
    setLanguageBaseUrl(value.base_url)
    setLanguageApiKey('')
    onNotice('GPT-5.6-sol 的 Base URL 与 API Key 已保存在本机配置中')
  })

  const validateLanguageConnection = () => void perform('验证 GPT-5.6-sol 连接', () => ShortDramaApi.validateLanguageConfig(), (value) => {
    setLanguageConfig(value)
    onNotice('GPT-5.6-sol 兼容地址与固定模型验证通过')
  })

  const switchStockProvider = (provider: StockAssetProvider) => {
    setStockProvider(provider)
    setStockApiKey('')
    setStockCandidates([])
    setStockWarnings([])
    const next = stockConfigs.find((item) => item.provider === provider)
    if (next) setStockBaseUrl(next.base_url)
  }

  const saveStockConnection = () => void perform('保存素材库连接', () => (
    ShortDramaApi.updateStockProviderConfig(stockProvider, stockApiKey || null, stockBaseUrl.trim())
  ), (value) => {
    setStockConfigs((current) => [
      ...current.filter((item) => item.provider !== value.provider),
      value,
    ])
    setStockBaseUrl(value.base_url)
    setStockApiKey('')
    onNotice(`${value.provider} 素材库连接已保存`)
  })

  const searchStock = () => {
    if (!asset || !stockQuery.trim()) { onError('请输入素材搜索词'); return }
    void perform('搜索免费素材库', () => ShortDramaApi.searchStockAssets({
      provider: stockProvider,
      query: stockQuery.trim(),
      asset_kind: asset.kind,
      orientation: stockOrientation(asset),
      page: 1,
      limit: 12,
      commercial_and_modifiable_only: true,
    }), (value) => {
      setStockCandidates(value.candidates)
      setStockWarnings(value.warnings ?? [])
      onNotice(`找到 ${value.candidates.length} 个候选；选择、上传或忽略均可`)
    })
  }

  const importStock = (candidate: StockAssetCandidate) => {
    if (!asset) return
    void perform('导入素材库候选', () => ShortDramaApi.importStockAsset(projectId, {
      target_asset_id: asset.id,
      candidate,
      descriptor: `真实素材候选：${candidate.title}；建议用途 ${candidate.recommended_use}`,
      parent_version_id: version?.media_type === 'TEXT_REFERENCE' ? null : (version?.id ?? null),
      import_mode: stockImportMode,
    }), (value) => {
      onProjectChange(value.project)
      setSelectedVersionId(value.version.id)
      onNotice('素材及其作者、来源页和许可信息已登记为候选 AssetVersion')
    })
  }

  const createAsset = () => {
    if (!newAssetName.trim() || !newAssetDescriptor.trim()) { onError('请填写资产名称和说明'); return }
    void perform('创建资产', () => ShortDramaApi.createAsset(projectId, {
      kind: newAssetKind,
      name: newAssetName.trim(),
      descriptor: newAssetDescriptor.trim(),
    }), (value) => {
      onProjectChange(value.project)
      setSelectedAssetId(value.asset.id)
      setShowCreate(false)
      setNewAssetName('')
      setNewAssetDescriptor('')
      onNotice('新资产已建立；首个版本保持候选状态')
    })
  }

  const importLocal = async () => {
    if (!asset) return
    const paths = await window.electronAPI?.showOpenFileDialog({
      title: `导入到 ${asset.name}`,
      filters: [
        { name: '参考媒体', extensions: ['png', 'jpg', 'jpeg', 'webp', 'mp4', 'mov', 'm4v', 'webm', 'wav', 'mp3', 'm4a', 'aac', 'flac'] },
      ],
      properties: [],
    })
    const localPath = paths?.[0]
    if (!localPath) return
    await perform('复制并登记本地素材', () => ShortDramaApi.importLocalAssetVersion(projectId, asset.id, {
      local_path: localPath,
      descriptor: descriptor || defaultDescriptor(asset),
      parent_version_id: version?.media_type === 'TEXT_REFERENCE' ? null : version?.id,
    }), (value) => {
      onProjectChange(value.project)
      setSelectedVersionId(value.version.id)
      onNotice('本地素材已成为新的候选 AssetVersion')
    })
  }

  const importUrl = () => {
    if (!asset || !externalUrl.trim()) { onError('请输入外部素材 URL'); return }
    void perform('登记外部素材链接', () => ShortDramaApi.importUrlAssetVersion(projectId, asset.id, {
      external_url: externalUrl.trim(),
      descriptor: descriptor || defaultDescriptor(asset),
      media_type: externalMediaType,
      parent_version_id: version?.media_type === 'TEXT_REFERENCE' ? null : version?.id,
    }), (value) => {
      onProjectChange(value.project)
      setSelectedVersionId(value.version.id)
      setExternalUrl('')
      onNotice('外部链接已成为新的候选 AssetVersion')
    })
  }

  const runTaskToTerminal = async (task: ImageGenerationTask) => {
    let current = task
    if (current.status === 'QUEUED') {
      const prepared = await ShortDramaApi.advanceImageTask(current.id)
      if (!prepared.ok) { onError(prepared.error.message); return }
      current = prepared.data.task
      onProjectChange(prepared.data.project)
    }
    if (current.status === 'SUBMITTING') {
      const completed = await ShortDramaApi.advanceImageTask(current.id)
      if (!completed.ok) { onError(completed.error.message); return }
      current = completed.data.task
      onProjectChange(completed.data.project)
    }
    await loadTasks()
    if (current.status === 'SUCCEEDED') {
      setSelectedVersionId(current.output_version_id)
      onNotice('GPT Image 2 已生成候选；用户批准前不会进入 H3 正式路线')
    } else if (current.status === 'SUBMIT_UNKNOWN') {
      onError('提交结果未知，系统没有自动重试；请检查账单后再决定是否建立重试任务。')
    } else if (current.status !== 'QUEUED' && current.status !== 'SUBMITTING') {
      onError(current.error_message ?? `图像任务结束于 ${current.status}`)
    }
  }

  const createImage = () => {
    if (!asset) return
    if (!config?.can_generate) { onError('请先保存并验证 OpenAI API Key'); return }
    const parentVersionId = operation === 'EDIT' ? version?.id : null
    const inputVersionIds = operation === 'EDIT'
      ? (selectedInputs.length ? selectedInputs : (version && version.id !== maskVersionId ? [version.id] : []))
      : []
    if (operation === 'EDIT' && !inputVersionIds.length) { onError('修整至少需要一张非蒙版输入图'); return }
    const resolvedPrimaryInput = operation === 'EDIT'
      ? (primaryInputVersionId || inputVersionIds[0] || null)
      : null
    const body: ImageTaskCreateRequest = {
      operation,
      prompt,
      descriptor,
      parent_version_id: parentVersionId,
      input_version_ids: inputVersionIds,
      primary_input_version_id: resolvedPrimaryInput,
      mask_version_id: operation === 'EDIT' ? (maskVersionId || null) : null,
      mask_feather_pixels: maskFeatherPixels,
      size,
      quality: 'high',
      background,
      output_format: 'png',
    }
    setBusy(operation === 'EDIT' ? 'GPT Image 2 正在修整资产' : 'GPT Image 2 正在生成资产')
    onError(null)
    void ShortDramaApi.enqueueImageTask(projectId, asset.id, body).then(async (queued) => {
      if (!queued.ok) { setBusy(null); onError(queued.error.message); return }
      onProjectChange(queued.data.project)
      await runTaskToTerminal(queued.data.task)
      setBusy(null)
    })
  }

  const toggleEditInput = (versionId: string, checked: boolean) => {
    if (checked && maskVersionId === versionId) setMaskVersionId('')
    const next = checked
      ? [...selectedInputs, versionId]
      : selectedInputs.filter((id) => id !== versionId)
    setSelectedInputs(next)
    if (checked && !primaryInputVersionId) setPrimaryInputVersionId(versionId)
    if (!checked && primaryInputVersionId === versionId) setPrimaryInputVersionId(next[0] ?? '')
  }

  const selectEditMask = (versionId: string) => {
    const nextInputs = selectedInputs.filter((id) => id !== versionId)
    setSelectedInputs(nextInputs)
    setMaskVersionId(versionId)
    if (!primaryInputVersionId || primaryInputVersionId === versionId) {
      setPrimaryInputVersionId(nextInputs[0] ?? '')
    }
  }

  const review = (decision: 'APPROVED' | 'REJECTED') => {
    if (!asset || !version) return
    void perform(decision === 'APPROVED' ? '批准 AssetVersion' : '拒绝 AssetVersion', () => (
      ShortDramaApi.reviewAssetVersion(projectId, asset.id, version.id, decision, reviewNotes)
    ), (value) => {
      onProjectChange(value.project)
      onNotice(decision === 'APPROVED' ? '该版本已成为正式批准资产' : '该候选已记录为拒绝')
    })
  }

  const taskAction = async (task: ImageGenerationTask, action: 'continue' | 'retry' | 'recover' | 'cancel') => {
    setBusy('更新图像任务')
    onError(null)
    const result = action === 'retry'
      ? await ShortDramaApi.retryImageTask(task.id)
      : action === 'recover'
        ? await ShortDramaApi.recoverImageTaskOutput(task.id)
        : action === 'cancel'
          ? await ShortDramaApi.cancelImageTask(task.id)
          : await ShortDramaApi.advanceImageTask(task.id)
    if (!result.ok) { setBusy(null); onError(result.error.message); return }
    onProjectChange(result.data.project)
    if (action === 'retry') await runTaskToTerminal(result.data.task)
    else if (action === 'continue' && result.data.task.status === 'SUBMITTING') await runTaskToTerminal(result.data.task)
    else await loadTasks()
    setBusy(null)
  }

  if (!asset || !version) return null
  const previewUrl = versionUrl(version)
  const azureImageEndpoint = imageBaseUrl.includes('.openai.azure.com') || imageBaseUrl.includes('/openai/deployments/')
  const stockConfig = stockConfigs.find((item) => item.provider === stockProvider)

  return (
    <div className="space-y-6">
      <section className="flex flex-col gap-4 rounded-2xl border border-blue-500/20 bg-gradient-to-br from-blue-500/10 via-zinc-950 to-cyan-500/5 p-5 xl:flex-row xl:items-center xl:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2"><Badge tone="blue">Phase 12 · Asset Agent</Badge><Badge>GPT Image 2</Badge><Badge>同源母版谱系</Badge></div>
          <h2 className="mt-3 text-xl font-semibold text-white">Agent 管理的可复用制片资产包</h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-400">免费素材库、用户上传、跨项目资产库、GPT Image 新生成和同母版派生共用 AssetVersion。AUTO_DRAFT 会按镜头需求检索最小参考集，只补齐缺失视图；建议可忽略，不形成用户审批门。</p>
        </div>
        <button className={secondaryButton} onClick={() => setShowCreate((value) => !value)}><Plus className="h-4 w-4" />新建资产</button>
      </section>

      <VoiceReferencePanel
        project={project}
        onProjectChange={onProjectChange}
        onError={onError}
        onNotice={onNotice}
      />

      <section className="rounded-xl border border-cyan-500/20 bg-cyan-500/5 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2"><Sparkles className="h-4 w-4 text-cyan-300" /><h3 className="text-sm font-medium text-cyan-100">资产 Agent tools</h3></div>
            <p className="mt-1 text-xs leading-5 text-zinc-500">search_stock / search_assets → inspect_asset_coverage → ensure_asset_view → review_and_register → select_shot_references</p>
          </div>
          <button className={subtleButton} onClick={() => void loadCoverage()}><RefreshCw className="h-3.5 w-3.5" />刷新覆盖</button>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <div className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3"><p className="text-[10px] uppercase tracking-[0.14em] text-zinc-600">资产包</p><p className="mt-1 text-lg font-semibold text-white">{referencePacks.length}</p></div>
          <div className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3"><p className="text-[10px] uppercase tracking-[0.14em] text-zinc-600">已登记视图</p><p className="mt-1 text-lg font-semibold text-white">{referenceEntryCount}</p></div>
          <div className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3"><p className="text-[10px] uppercase tracking-[0.14em] text-zinc-600">待补覆盖</p><p className={`mt-1 text-lg font-semibold ${coverage?.ready ? 'text-emerald-300' : 'text-amber-300'}`}>{coverage ? coverage.missing_views.length : '—'}</p></div>
        </div>
        {coverage && coverage.missing_views.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {coverage.missing_views.slice(0, 6).map((need) => {
              const subject = project.document.assets.find((item) => item.id === need.subject_asset_id)
              return <Badge key={`${need.subject_asset_id}-${need.role}-${need.angle}-${need.framing}`} tone="amber">{subject?.name ?? need.subject_asset_id} · {need.role} · {need.framing}</Badge>
            })}
          </div>
        )}
      </section>

      <section className="space-y-3 rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
        <div className="grid gap-3 xl:grid-cols-[170px_minmax(260px,1fr)_minmax(220px,0.8fr)_auto_auto] xl:items-end">
          <div className="rounded-lg border border-zinc-800 bg-zinc-950/70 px-3 py-2"><p className="text-[10px] uppercase tracking-[0.14em] text-zinc-600">Image Provider</p><p className="mt-1 text-xs text-zinc-300">{config?.model ?? 'gpt-image-2'} · {config?.can_generate ? 'Ready' : '未配置'}</p></div>
          <div><label className="text-xs text-zinc-500">Base URL / Azure 完整 endpoint</label><input className={`${field} mt-1`} value={imageBaseUrl} onChange={(event) => setImageBaseUrl(event.target.value)} placeholder="https://api.openai.com/v1" /></div>
          <div><label className="text-xs text-zinc-500">API Key</label><input className={`${field} mt-1`} value={apiKey} onChange={(event) => setApiKey(event.target.value)} type="password" placeholder={config?.has_api_key ? `已配置 · ${config.key_source}` : '输入密钥'} /></div>
          <button className={secondaryButton} disabled={!imageBaseUrl.trim() || Boolean(busy)} onClick={saveKey}><KeyRound className="h-4 w-4" />保存</button>
          <button className={secondaryButton} disabled={!config?.has_api_key || Boolean(busy)} onClick={validateKey}><RefreshCw className="h-4 w-4" />验证</button>
        </div>
        <div className="grid gap-3 border-t border-zinc-800 pt-3 xl:grid-cols-[170px_minmax(260px,1fr)_minmax(220px,0.8fr)_auto_auto] xl:items-end">
          <div className="rounded-lg border border-zinc-800 bg-zinc-950/70 px-3 py-2"><p className="text-[10px] uppercase tracking-[0.14em] text-zinc-600">Language Provider</p><p className="mt-1 text-xs text-zinc-300">{languageConfig?.model ?? 'gpt-5.6-sol'} · {languageConfig?.can_connect ? 'Ready' : '未配置'}</p></div>
          <div><label className="text-xs text-zinc-500">Base URL</label><input className={`${field} mt-1`} value={languageBaseUrl} onChange={(event) => setLanguageBaseUrl(event.target.value)} placeholder="OpenAI-compatible /v1" /></div>
          <div><label className="text-xs text-zinc-500">API Key</label><input className={`${field} mt-1`} value={languageApiKey} onChange={(event) => setLanguageApiKey(event.target.value)} type="password" placeholder={languageConfig?.has_api_key ? `已配置 · ${languageConfig.key_source}` : '输入密钥'} /></div>
          <button className={secondaryButton} disabled={!languageBaseUrl.trim() || Boolean(busy)} onClick={saveLanguageConnection}><KeyRound className="h-4 w-4" />保存</button>
          <button className={secondaryButton} disabled={!languageConfig?.can_connect || Boolean(busy)} onClick={validateLanguageConnection}><RefreshCw className="h-4 w-4" />验证</button>
        </div>
        <div className="grid gap-3 border-t border-zinc-800 pt-3 xl:grid-cols-[170px_minmax(260px,1fr)_minmax(220px,0.8fr)_auto_auto] xl:items-end">
          <div><label className="text-xs text-zinc-500">免费素材库</label><select className={`${field} mt-1`} value={stockProvider} onChange={(event) => switchStockProvider(event.target.value as StockAssetProvider)}><option value="OPENVERSE">Openverse</option><option value="PEXELS">Pexels</option><option value="PIXABAY">Pixabay</option></select></div>
          <div><label className="text-xs text-zinc-500">API Base URL</label><input className={`${field} mt-1`} value={stockBaseUrl} onChange={(event) => setStockBaseUrl(event.target.value)} /></div>
          <div><label className="text-xs text-zinc-500">API Key</label><input className={`${field} mt-1`} value={stockApiKey} onChange={(event) => setStockApiKey(event.target.value)} type="password" placeholder={stockConfig?.has_api_key ? `已配置 · ${stockConfig.key_source}` : stockProvider === 'OPENVERSE' ? 'Openverse 可留空' : '输入免费 API Key'} /></div>
          <button className={secondaryButton} disabled={!stockBaseUrl.trim() || Boolean(busy)} onClick={saveStockConnection}><KeyRound className="h-4 w-4" />保存</button>
          <Badge tone={stockConfig?.can_search ? 'green' : 'amber'}>{stockConfig?.can_search ? 'Ready' : '需要 Key'}</Badge>
        </div>
        <p className="text-[11px] leading-5 text-zinc-600">Openverse 无 Key 可搜索；Pexels 与 Pixabay 可填各自免费 API Key。搜索只返回候选和来源信息，不会自动批准资产，也不会调用 GPT Image 或 H3。</p>
      </section>

      {showCreate && (
        <section className="rounded-xl border border-zinc-700 bg-zinc-900 p-4">
          <div className="grid gap-3 md:grid-cols-[160px_1fr_1.4fr_auto]">
            <select className={field} value={newAssetKind} onChange={(event) => setNewAssetKind(event.target.value as AssetKind)}><option value="CHARACTER">角色</option><option value="LOCATION">场景</option><option value="PROP">道具</option><option value="REFERENCE">参考媒体</option></select>
            <input className={field} value={newAssetName} onChange={(event) => setNewAssetName(event.target.value)} placeholder="资产名称" />
            <input className={field} value={newAssetDescriptor} onChange={(event) => setNewAssetDescriptor(event.target.value)} placeholder="稳定外观或用途说明" />
            <button className={primaryButton} onClick={createAsset}>创建</button>
          </div>
        </section>
      )}

      <div className="grid gap-5 xl:grid-cols-[280px_minmax(0,1fr)_390px]">
        <aside className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-3">
          <div className="flex items-center justify-between px-2 py-2"><p className="text-xs font-semibold uppercase tracking-[0.15em] text-zinc-500">Assets</p><Badge tone="blue">{project.document.assets.length}</Badge></div>
          <div className="mt-1 space-y-1">
            {project.document.assets.map((item) => {
              const active = currentVersion(item)
              return <button key={item.id} onClick={() => setSelectedAssetId(item.id)} className={`w-full rounded-lg border p-3 text-left transition ${item.id === asset.id ? 'border-blue-500/40 bg-blue-500/10' : 'border-transparent hover:border-zinc-800 hover:bg-zinc-950/60'}`}><div className="flex items-center justify-between gap-2"><span className="truncate text-sm font-medium text-zinc-100">{item.name}</span><Badge>{item.kind}</Badge></div><p className="mt-1 line-clamp-2 text-[11px] leading-4 text-zinc-500">{active.descriptor}</p><div className="mt-2 flex items-center gap-2"><Badge tone={statusTone(active.approval_status)}>v{active.version} · {active.approval_status}</Badge>{item.approved_version_id && <Check className="h-3.5 w-3.5 text-emerald-400" />}</div></button>
            })}
          </div>
        </aside>

        <section className="min-w-0 space-y-5">
          <div className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900/60">
            <div className="flex min-h-[460px] items-center justify-center bg-[#050608] p-4">
              {previewUrl && version.media_type === 'IMAGE'
                ? <img src={previewUrl} alt={asset.name} className="max-h-[620px] max-w-full rounded-lg object-contain shadow-2xl" />
                : previewUrl && version.media_type === 'AUDIO'
                  ? <div className="w-full max-w-xl rounded-xl border border-zinc-800 bg-zinc-950 p-5"><AudioLines className="mx-auto h-10 w-10 text-cyan-400" /><p className="mt-3 text-center text-sm text-zinc-300">{asset.name}</p><audio className="mt-4 w-full" controls preload="metadata" src={previewUrl} /></div>
                  : previewUrl && version.media_type === 'VIDEO'
                    ? <video src={previewUrl} controls className="max-h-[620px] max-w-full rounded-lg object-contain shadow-2xl" />
                    : <div className="text-center"><ImageIcon className="mx-auto h-10 w-10 text-zinc-700" /><p className="mt-3 text-sm text-zinc-500">当前版本还没有可预览媒体</p><p className="mt-1 text-xs text-zinc-700">可上传、导入 URL 或使用对应生成工具</p></div>}
            </div>
            <div className="border-t border-zinc-800 p-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="font-medium text-white">{asset.name}</h3><p className="mt-1 text-xs text-zinc-500">v{version.version} · {version.origin_type} · {version.media_type}{version.width && version.height ? ` · ${version.width}×${version.height}` : ''}</p></div><div className="flex gap-2"><Badge tone={statusTone(version.approval_status)}>{version.approval_status}</Badge>{asset.approved_version_id === version.id && <Badge tone="green">正式版本</Badge>}</div></div><p className="mt-3 text-sm leading-6 text-zinc-300">{version.descriptor}</p>{version.source && <div className="mt-2 text-xs leading-5 text-zinc-600"><p>来源：{version.source.source_label}{version.source.creator ? ` · ${version.source.creator}` : ''}{version.source.license_name ? ` · ${version.source.license_name}` : ''}{version.source.model ? ` · ${version.source.model}` : ''}{version.parent_version_id ? ` · 派生自 v${asset.versions.find((item) => item.id === version.parent_version_id)?.version ?? '?'}` : ''}</p>{version.source.source_uri && <a className="text-cyan-500 hover:text-cyan-400" href={version.source.source_uri} target="_blank" rel="noreferrer">查看原始来源</a>}</div>}</div>
          </div>

          <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
            <div className="flex items-center justify-between"><h3 className="text-sm font-medium text-white">版本历史</h3><span className="text-xs text-zinc-600">最新工作版本与正式批准版本分离</span></div>
            <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {[...asset.versions].reverse().map((item) => <button key={item.id} onClick={() => setSelectedVersionId(item.id)} className={`rounded-lg border p-3 text-left ${item.id === version.id ? 'border-blue-500/40 bg-blue-500/10' : 'border-zinc-800 bg-zinc-950/50 hover:border-zinc-700'}`}><div className="flex items-center justify-between"><span className="text-xs font-medium text-zinc-200">v{item.version}</span><Badge tone={statusTone(item.approval_status)}>{item.approval_status}</Badge></div><p className="mt-2 line-clamp-2 text-[11px] leading-4 text-zinc-500">{item.descriptor}</p><p className="mt-2 text-[10px] text-zinc-700">{item.origin_type}{item.parent_version_id ? ' · 派生版本' : ''}</p></button>)}
            </div>
          </div>
        </section>

        <aside className="space-y-5">
          <section className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
            <div className="flex items-center gap-2"><FileUp className="h-4 w-4 text-blue-400" /><h3 className="text-sm font-medium text-white">导入素材</h3></div>
            <button className={`${secondaryButton} mt-3 w-full`} onClick={() => void importLocal()} disabled={Boolean(busy)}><FileUp className="h-4 w-4" />选择本地文件</button>
            <div className="mt-3 grid grid-cols-[1fr_100px] gap-2"><input className={field} value={externalUrl} onChange={(event) => setExternalUrl(event.target.value)} placeholder="https://..." /><select className={field} value={externalMediaType} onChange={(event) => setExternalMediaType(event.target.value as AssetMediaType)}><option value="IMAGE">图片</option><option value="VIDEO">视频</option><option value="AUDIO">音频</option></select></div>
            <button className={`${subtleButton} mt-2`} onClick={importUrl}><Link className="h-3.5 w-3.5" />导入外部链接</button>
          </section>

          <section className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
            <div className="flex items-center justify-between gap-2"><div className="flex items-center gap-2"><Search className="h-4 w-4 text-cyan-400" /><h3 className="text-sm font-medium text-white">免费真实素材</h3></div><Badge tone={stockConfig?.can_search ? 'green' : 'amber'}>{stockProvider}</Badge></div>
            <p className="mt-2 text-[11px] leading-5 text-zinc-600">Agent 可自动选择，用户也可手动换图。人物默认作为真实摄影与造型依据，再生成新虚构身份；拥有授权的上传图可保留身份。</p>
            <div className="mt-3 grid grid-cols-[1fr_110px_auto] gap-2"><input className={field} value={stockQuery} onChange={(event) => setStockQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') searchStock() }} placeholder="例如 Asian woman workwear portrait" /><select className={field} value={stockImportMode} onChange={(event) => setStockImportMode(event.target.value as StockAssetImportMode)}><option value="AUTO">自动判断</option><option value="DIRECT">直接候选</option><option value="DERIVE">继续派生</option></select><button className={secondaryButton} disabled={Boolean(busy) || !stockConfig?.can_search} onClick={searchStock}><Search className="h-4 w-4" /></button></div>
            {stockWarnings.length > 0 && <div className="mt-3 rounded-lg border border-amber-500/15 bg-amber-500/5 p-2 text-[10px] leading-4 text-amber-200/70">{stockWarnings.join(' ')}</div>}
            {stockCandidates.length > 0 && <div className="mt-3 max-h-[620px] space-y-3 overflow-y-auto pr-1">{stockCandidates.map((candidate) => <div key={`${candidate.provider}-${candidate.provider_asset_id}`} className="overflow-hidden rounded-lg border border-zinc-800 bg-zinc-950/70"><img src={candidate.preview_url} alt={candidate.title} className="h-44 w-full object-cover" loading="lazy" /><div className="p-3"><p className="line-clamp-2 text-xs font-medium text-zinc-200">{candidate.title}</p><p className="mt-1 text-[10px] text-zinc-600">{candidate.creator ?? '未知作者'} · {candidate.license_name}{candidate.width && candidate.height ? ` · ${candidate.width}×${candidate.height}` : ''}</p><p className="mt-2 text-[10px] leading-4 text-zinc-500">{candidate.recommended_use === 'VISUAL_GROUNDING_NEW_IDENTITY' ? '建议：真实视觉母版 → 新虚构身份' : '建议：可直接使用或继续派生'}</p><div className="mt-3 flex items-center justify-between gap-2"><a className="text-[10px] text-cyan-400 hover:text-cyan-300" href={candidate.source_page_url} target="_blank" rel="noreferrer">查看来源与许可</a><button className={subtleButton} onClick={() => importStock(candidate)}>导入候选</button></div></div></div>)}</div>}
          </section>

          {version.media_type !== 'AUDIO' && <section className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
            <div className="flex items-center justify-between"><div className="flex items-center gap-2"><WandSparkles className="h-4 w-4 text-violet-400" /><h3 className="text-sm font-medium text-white">GPT Image 2</h3></div><Badge tone={config?.can_generate ? 'green' : 'amber'}>{config?.can_generate ? 'Ready' : '未配置'}</Badge></div>
            <div className="mt-3 grid grid-cols-2 gap-2"><button className={`${operation === 'GENERATE' ? primaryButton : secondaryButton} py-2`} onClick={() => { setOperation('GENERATE'); setSelectedInputs([]); setPrimaryInputVersionId(''); setMaskVersionId('') }}>新生成</button><button className={`${operation === 'EDIT' ? primaryButton : secondaryButton} py-2`} onClick={() => { const initial = version.media_type === 'IMAGE' ? version.id : ''; setOperation('EDIT'); setSelectedInputs(initial ? [initial] : []); setPrimaryInputVersionId(initial); setMaskVersionId('') }}>修整/派生</button></div>
            <label className="mt-3 block text-xs text-zinc-500">提示词</label><textarea className={`${field} mt-1 min-h-44 resize-y leading-5`} value={prompt} onChange={(event) => setPrompt(event.target.value)} />
            <label className="mt-3 block text-xs text-zinc-500">候选说明</label><input className={`${field} mt-1`} value={descriptor} onChange={(event) => setDescriptor(event.target.value)} />
            <div className="mt-3 grid grid-cols-2 gap-2"><input className={field} value={size} onChange={(event) => setSize(event.target.value)} /><select className={field} value={background} onChange={(event) => setBackground(event.target.value as 'opaque' | 'transparent')}><option value="opaque">不透明背景</option><option value="transparent" disabled={azureImageEndpoint}>透明背景{azureImageEndpoint ? '（Azure 不支持）' : ''}</option></select></div>
            {operation === 'EDIT' && <div className="mt-3 rounded-lg border border-zinc-800 bg-zinc-950/60 p-3"><p className="text-xs text-zinc-500">编辑输入（最多 16 张）</p><p className="mt-1 text-[11px] leading-5 text-zinc-700">主画布决定物理空间；可选 PNG 蒙版中透明区域允许生成，非透明区域会在返回后从主画布确定性恢复。蒙版与其他建议一样可不使用。</p><label className="mt-2 flex cursor-pointer items-center gap-1 text-[10px] text-zinc-500"><input type="radio" name="primary-image-input" checked={!primaryInputVersionId} onChange={() => setPrimaryInputVersionId('')} />不指定主画布</label><label className="mt-1 flex cursor-pointer items-center gap-1 text-[10px] text-zinc-500"><input type="radio" name="mask-image-input" checked={!maskVersionId} onChange={() => setMaskVersionId('')} />不使用蒙版</label><div className="mt-2 max-h-52 space-y-1 overflow-y-auto">{imageVersions.map((item) => { const selected = selectedInputs.includes(item.version.id); const maskEligible = item.version.mime_type === 'image/png' && Boolean(item.version.local_path); return <div key={item.version.id} className="flex items-center gap-2 rounded px-2 py-1 text-xs text-zinc-400 hover:bg-zinc-900"><label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2"><input type="checkbox" checked={selected} onChange={(event) => toggleEditInput(item.version.id, event.target.checked)} /><span className="truncate">{item.asset.name} · v{item.version.version}</span></label>{selected && <label className="flex cursor-pointer items-center gap-1 text-[10px] text-blue-300"><input type="radio" name="primary-image-input" checked={primaryInputVersionId === item.version.id} onChange={() => setPrimaryInputVersionId(item.version.id)} />主画布</label>}{maskEligible && <label className="flex cursor-pointer items-center gap-1 text-[10px] text-violet-300"><input type="radio" name="mask-image-input" checked={maskVersionId === item.version.id} onChange={() => selectEditMask(item.version.id)} />蒙版</label>}</div> })}</div>{maskVersionId && <label className="mt-2 flex items-center justify-between gap-3 text-[10px] text-zinc-500"><span>蒙版内侧羽化像素</span><input className="w-20 rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-right text-xs text-zinc-300" type="number" min={0} max={64} value={maskFeatherPixels} onChange={(event) => setMaskFeatherPixels(Math.min(64, Math.max(0, Number(event.target.value) || 0)))} /></label>}</div>}
            <button className={`${primaryButton} mt-4 w-full py-3`} onClick={createImage} disabled={Boolean(busy) || !prompt.trim() || !descriptor.trim()}>{busy?.includes('GPT Image') ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}{operation === 'EDIT' ? '生成修整候选' : '生成新候选'}</button>
          </section>}

          <section className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
            <h3 className="text-sm font-medium text-white">人工审片</h3><textarea className={`${field} mt-3 min-h-20 resize-y`} value={reviewNotes} onChange={(event) => setReviewNotes(event.target.value)} /><div className="mt-3 grid grid-cols-2 gap-2"><button className="inline-flex items-center justify-center gap-2 rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-40" disabled={!previewUrl || version.approval_status === 'APPROVED'} onClick={() => review('APPROVED')}><Check className="h-4 w-4" />批准</button><button className="inline-flex items-center justify-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm font-medium text-red-200 hover:bg-red-500/20" disabled={version.approval_status === 'REJECTED'} onClick={() => review('REJECTED')}><CircleX className="h-4 w-4" />拒绝</button></div>
          </section>
        </aside>
      </div>

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
        <div className="flex items-center justify-between"><div><h3 className="text-sm font-medium text-white">图像任务</h3><p className="mt-1 text-xs text-zinc-600">同步 API 也先持久化任务；提交未知时不会自动重复付费。</p></div><button className={subtleButton} onClick={() => void loadTasks()}><RefreshCw className="h-3.5 w-3.5" />刷新</button></div>
        <div className="mt-3 space-y-2">
          {tasks.length === 0 && <p className="rounded-lg border border-dashed border-zinc-800 py-6 text-center text-xs text-zinc-600">当前资产还没有图像任务</p>}
          {tasks.map((task) => <div key={task.id} className="flex flex-col gap-3 rounded-lg border border-zinc-800 bg-zinc-950/60 p-3 lg:flex-row lg:items-center lg:justify-between"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><Badge tone={statusTone(task.status)}>{task.status}</Badge><span className="text-xs text-zinc-300">{task.operation} · v{task.output_version_number} · attempt {task.attempt}</span><span className="text-xs text-zinc-600">{money(task.cost?.estimated_amount)}</span></div><p className="mt-1 truncate text-xs text-zinc-600">{task.error_message ?? task.request.prompt}</p></div><div className="flex shrink-0 gap-1">{task.status === 'QUEUED' && <><button className={subtleButton} onClick={() => void taskAction(task, 'continue')}><Sparkles className="h-3.5 w-3.5" />运行</button><button className={subtleButton} onClick={() => void taskAction(task, 'cancel')}>取消</button></>}{task.status === 'SUBMITTING' && <button className={subtleButton} onClick={() => void taskAction(task, 'continue')}><Sparkles className="h-3.5 w-3.5" />继续提交</button>}{task.status === 'OUTPUT_MISSING' && <button className={subtleButton} onClick={() => void taskAction(task, 'recover')}><RefreshCw className="h-3.5 w-3.5" />恢复产物</button>}{['FAILED', 'CANCELLED', 'SUBMIT_UNKNOWN', 'OUTPUT_MISSING'].includes(task.status) && <button className={subtleButton} onClick={() => void taskAction(task, 'retry')}><RotateCcw className="h-3.5 w-3.5" />建立重试</button>}</div></div>)}
        </div>
      </section>

      {busy && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 backdrop-blur-sm"><div className="rounded-xl border border-zinc-700 bg-zinc-900 px-6 py-5 shadow-2xl"><div className="flex items-center gap-3 text-sm font-medium text-white"><Loader2 className="h-5 w-5 animate-spin text-blue-400" />{busy}</div><p className="mt-2 max-w-sm text-xs leading-5 text-zinc-500">付费调用前已持久化任务。请保持窗口开启，生成完成后媒体会落到项目资产目录。</p></div></div>}
    </div>
  )
}
