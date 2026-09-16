import { useEffect, useRef, useState } from 'react'
import { FileAudio, Mic, Square } from 'lucide-react'
import { ShortDramaApi, type Asset, type ProjectAggregate } from './api'
import { mediaUrl } from './studio-content'
import { unwrapApi } from './studio-api'
import './studio-character-voice.css'

function usableAudio(asset: Asset) {
  if (asset.kind !== 'REFERENCE') return undefined
  return asset.versions.find(version => version.id === asset.current_version_id
    && version.media_type === 'AUDIO' && version.freshness === 'CURRENT'
    && ['DRAFT', 'APPROVED'].includes(version.approval_status)
    && Boolean(version.local_path || version.external_url)
    && (!version.local_path || /\.(wav|mp3)$/i.test(version.local_path))
    && version.file_size_bytes !== 0 && (version.file_size_bytes ?? 0) <= 15 * 1024 * 1024)
}

/** Manual control of the same voice reference used by production tools. */
export function StudioCharacterVoice({ project, characterId, onProject, onError }: {
  project: ProjectAggregate; characterId: string
  onProject: (project: ProjectAggregate) => void; onError: (error: unknown) => void
}) {
  const bible = project.document.bible_versions.find(item => item.id === project.document.project.current_bible_version_id)
  const character = bible?.characters.find(item => item.id === characterId)
  const binding = project.document.voice_references?.find(item => item.character_id === characterId)
  const assets = project.document.assets.flatMap(asset => {
    const version = usableAudio(asset)
    return version ? [{ asset, version }] : []
  })
  const boundAsset = project.document.assets.find(item => item.id === binding?.asset_id)
  const boundVersion = boundAsset?.versions.find(item => item.id === boundAsset.current_version_id)
  const [previewId, setPreviewId] = useState<string | null>(binding?.asset_id ?? null)
  const preview = assets.find(item => item.asset.id === previewId)?.version ?? (previewId === binding?.asset_id ? boundVersion : undefined)
  const [busy, setBusy] = useState(false)
  const [recording, setRecording] = useState(false)
  const [notice, setNotice] = useState('')
  const recorder = useRef<MediaRecorder | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mounted = useRef(true)
  const upload = useRef<HTMLInputElement>(null)
  const projectId = project.document.project.id
  useEffect(() => { setPreviewId(binding?.asset_id ?? null) }, [binding?.asset_id])
  const release = () => {
    if (timer.current) clearTimeout(timer.current)
    stream.current?.getTracks().forEach(track => track.stop())
    stream.current = null
    recorder.current = null
    if (mounted.current) setRecording(false)
  }
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      if (recorder.current) {
        recorder.current.onstop = null
        if (recorder.current.state !== 'inactive') recorder.current.stop()
      }
      release()
    }
  }, [])
  const saveUpload = async (file: File) => {
    if (!character) return
    setBusy(true); setNotice('')
    try {
      const result = unwrapApi(await ShortDramaApi.uploadCharacterVoiceReference(projectId, characterId, file, `${character.name}的声线参考`))
      onProject(result.project)
      if (mounted.current) setNotice('声音已保存并绑定。后续生成使用这份参考，已有视频保留。')
    } catch (error) { onError(error) }
    finally { if (mounted.current) setBusy(false) }
  }
  const bind = async (assetId: string | null) => {
    setBusy(true); setNotice('')
    try {
      const result = unwrapApi(await ShortDramaApi.updateCharacterVoiceReference(projectId, characterId, assetId))
      onProject(result.project)
      if (mounted.current) setNotice(assetId ? '角色声音已绑定，后续生成可使用。' : '已取消绑定，后续由生成模型决定角色声音。')
    } catch (error) { onError(error) }
    finally { if (mounted.current) setBusy(false) }
  }
  const stopRecording = () => {
    if (recorder.current && recorder.current.state !== 'inactive') recorder.current.stop()
  }
  const record = async () => {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      onError(new Error('当前环境不支持录音，请导入音频文件。')); return
    }
    setBusy(true)
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (!mounted.current) { media.getTracks().forEach(track => track.stop()); return }
      stream.current = media
      const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'].find(value => MediaRecorder.isTypeSupported(value))
      const next = new MediaRecorder(media, mime ? { mimeType: mime } : undefined)
      const chunks: BlobPart[] = []
      const started = Date.now()
      next.ondataavailable = event => { if (event.data.size) chunks.push(event.data) }
      next.onerror = () => { next.onstop = null; release(); onError(new Error('录音失败，请检查麦克风或导入音频。')) }
      next.onstop = () => {
        const type = next.mimeType || mime || 'audio/webm'
        release()
        if (Date.now() - started < 1900) { onError(new Error('请录制至少 2 秒清晰人声。')); return }
        const extension = type.includes('mp4') ? 'm4a' : type.includes('ogg') ? 'ogg' : 'webm'
        void saveUpload(new File([new Blob(chunks, { type })], `${character?.name ?? '角色'}-声线.${extension}`, { type }))
      }
      recorder.current = next
      next.start(); setRecording(true); setNotice('')
      timer.current = setTimeout(() => { if (next.state !== 'inactive') next.stop() }, 14000)
    } catch (error) { release(); onError(error) }
    finally { if (mounted.current) setBusy(false) }
  }
  if (!character) return null
  return <section className="sd-character-voice" aria-label={`${character.name}的角色声音`}>
    <h3>角色声音</h3>
    <p>{binding ? `当前声音：${boundAsset?.name ?? '引用素材暂不可用'}` : '尚未绑定，由生成模型决定角色声音。'}</p>
    <label className="sd-field">试听已有声音<select aria-label="选择角色声音" value={previewId ?? ''} disabled={busy || recording} onChange={event => setPreviewId(event.target.value || null)}>
      <option value="">选择声音素材</option>
      {binding && !assets.some(item => item.asset.id === binding.asset_id) && <option value={binding.asset_id} disabled>{boundAsset?.name ?? '已绑定声音'} · 暂不可用</option>}
      {assets.map(({ asset }) => <option key={asset.id} value={asset.id}>{asset.name}</option>)}
    </select></label>
    {preview && <audio aria-label="角色声音试听" controls preload="metadata" src={mediaUrl(preview.local_path, preview.external_url)} />}
    <div className="sd-asset-detail-actions">
      <button className="sd-button" disabled={busy || recording || !previewId || previewId === binding?.asset_id || !assets.some(item => item.asset.id === previewId)} onClick={() => void bind(previewId)}>用于这个角色</button>
      {binding && <button className="sd-text-button" disabled={busy || recording} onClick={() => void bind(null)}>取消声音绑定</button>}
      <button className="sd-button" disabled={busy || recording} onClick={() => upload.current?.click()}><FileAudio size={14} />导入声音并绑定</button>
      <button className="sd-button" disabled={busy} onClick={() => recording ? stopRecording() : void record()}>{recording ? <Square size={14} /> : <Mic size={14} />}{recording ? '停止并保存' : '录制声音'}</button>
    </div>
    <input ref={upload} hidden type="file" accept="audio/*,.m4a,.wav,.mp3,.flac,.ogg,.webm" onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void saveUpload(file) }} />
    <p className="sd-hint">{recording ? '正在录制，最长 14 秒；关闭详情将放弃这次录音。' : '选择后可先试听，再用于角色。导入或录制会直接保存并绑定；建议使用 2–14 秒清晰人声。'}</p>
    {notice && <p className="sd-hint" role="status">{notice}</p>}
  </section>
}
