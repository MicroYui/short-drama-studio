import { useEffect, useMemo, useRef, useState } from 'react'
import { AudioLines, FileAudio, Mic, Square, Trash2 } from 'lucide-react'
import { pathToFileUrl } from '../../lib/file-url'
import {
  ShortDramaApi,
  type Asset,
  type AssetVersion,
  type ProjectAggregate,
} from './api'

const secondaryButton = 'inline-flex items-center justify-center gap-2 rounded-lg border border-zinc-700 bg-zinc-900 px-3.5 py-2 text-sm font-medium text-zinc-200 transition hover:border-zinc-600 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40'
const dangerButton = 'inline-flex items-center justify-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm font-medium text-red-200 transition hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-40'
const field = 'w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-200 outline-none transition focus:border-blue-500/70'

type Props = {
  project: ProjectAggregate
  onProjectChange: (project: ProjectAggregate) => void
  onError: (message: string | null) => void
  onNotice: (message: string | null) => void
}

type AudioAsset = {
  asset: Asset
  version: AssetVersion
}

function approvedAudio(asset: Asset): AssetVersion | undefined {
  if (!asset.approved_version_id || asset.kind !== 'REFERENCE') return undefined
  return asset.versions.find((version) => (
    version.id === asset.approved_version_id
    && version.approval_status === 'APPROVED'
    && version.media_type === 'AUDIO'
    && Boolean(version.local_path || version.external_url)
  ))
}

function audioUrl(version: AssetVersion | undefined): string | null {
  if (!version) return null
  return version.local_path ? pathToFileUrl(version.local_path) : (version.external_url ?? null)
}

function recordingExtension(mimeType: string): string {
  if (mimeType.includes('ogg')) return 'ogg'
  if (mimeType.includes('mp4')) return 'm4a'
  return 'webm'
}

export function VoiceReferencePanel({ project, onProjectChange, onError, onNotice }: Props) {
  const projectId = project.document.project.id
  const [busyCharacterId, setBusyCharacterId] = useState<string | null>(null)
  const [recordingCharacterId, setRecordingCharacterId] = useState<string | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const recordingStartedAtRef = useRef(0)
  const recordingTimerRef = useRef<number | null>(null)

  const bible = project.document.bible_versions.find(
    (item) => item.id === project.document.project.current_bible_version_id,
  )
  const characters = bible?.characters ?? []
  const bindings = project.document.voice_references ?? []
  const approvedAudioAssets = useMemo<AudioAsset[]>(() => (
    project.document.assets.flatMap((asset) => {
      const version = approvedAudio(asset)
      return version ? [{ asset, version }] : []
    })
  ), [project.document.assets])

  const releaseRecorder = () => {
    if (recordingTimerRef.current !== null) {
      window.clearTimeout(recordingTimerRef.current)
      recordingTimerRef.current = null
    }
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    recorderRef.current = null
    setRecordingCharacterId(null)
  }

  useEffect(() => () => {
    if (recordingTimerRef.current !== null) window.clearTimeout(recordingTimerRef.current)
    const recorder = recorderRef.current
    if (recorder && recorder.state !== 'inactive') {
      recorder.onstop = null
      recorder.stop()
    }
    streamRef.current?.getTracks().forEach((track) => track.stop())
  }, [])

  const uploadVoice = async (characterId: string, characterName: string, file: File) => {
    setBusyCharacterId(characterId)
    onError(null)
    const result = await ShortDramaApi.uploadCharacterVoiceReference(
      projectId,
      characterId,
      file,
      `${characterName}用户声线参考，仅用于音色与说话方式`,
    )
    setBusyCharacterId(null)
    if (!result.ok) {
      onError(result.error.message)
      return
    }
    onProjectChange(result.data.project)
    onNotice(`${characterName}的声线参考已保存并绑定；有该角色台词的镜头会优先交给 H3 Reference 路线。`)
  }

  const updateBinding = async (
    characterId: string,
    characterName: string,
    assetId: string | null,
  ) => {
    setBusyCharacterId(characterId)
    onError(null)
    const result = await ShortDramaApi.updateCharacterVoiceReference(
      projectId,
      characterId,
      assetId,
    )
    setBusyCharacterId(null)
    if (!result.ok) {
      onError(result.error.message)
      return
    }
    onProjectChange(result.data.project)
    onNotice(assetId ? `${characterName}已绑定现有声线资产。` : `${characterName}已恢复使用 H3 原生声音。`)
  }

  const startRecording = async (characterId: string, characterName: string) => {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      onError('当前桌面环境不支持麦克风录音，请改为上传音频文件。')
      return
    }
    onError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const preferredMimeType = [
        'audio/webm;codecs=opus',
        'audio/webm',
        'audio/ogg;codecs=opus',
        'audio/mp4',
      ].find((candidate) => MediaRecorder.isTypeSupported(candidate))
      const recorder = preferredMimeType
        ? new MediaRecorder(stream, { mimeType: preferredMimeType })
        : new MediaRecorder(stream)
      streamRef.current = stream
      recorderRef.current = recorder
      chunksRef.current = []
      recordingStartedAtRef.current = Date.now()
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data)
      }
      recorder.onerror = () => {
        recorder.onstop = null
        releaseRecorder()
        onError('麦克风录音失败，请检查系统权限或改为上传音频文件。')
      }
      recorder.onstop = () => {
        const elapsedMs = Date.now() - recordingStartedAtRef.current
        const mimeType = recorder.mimeType || preferredMimeType || 'audio/webm'
        const blob = new Blob(chunksRef.current, { type: mimeType })
        releaseRecorder()
        if (elapsedMs < 1900) {
          onError('声线参考至少需要录制 2 秒。')
          return
        }
        const file = new File(
          [blob],
          `${characterName}-voice-${Date.now()}.${recordingExtension(mimeType)}`,
          { type: mimeType },
        )
        void uploadVoice(characterId, characterName, file)
      }
      recorder.start(250)
      setRecordingCharacterId(characterId)
      recordingTimerRef.current = window.setTimeout(() => {
        if (recorder.state !== 'inactive') recorder.stop()
      }, 14_000)
    } catch (error) {
      releaseRecorder()
      onError(error instanceof Error ? error.message : '无法访问麦克风。')
    }
  }

  const stopRecording = () => {
    const recorder = recorderRef.current
    if (recorder && recorder.state !== 'inactive') recorder.stop()
  }

  if (characters.length === 0) return null

  return (
    <section className="rounded-xl border border-cyan-500/20 bg-cyan-500/[0.04] p-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <AudioLines className="h-4 w-4 text-cyan-300" />
            <h3 className="text-sm font-medium text-white">角色声线参考（可选）</h3>
          </div>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-zinc-500">
            可上传或录制 2–15 秒清晰人声。系统会转为 H3 可用 WAV；未绑定时继续使用 H3 原生声音，不会阻断自动生成。
          </p>
        </div>
        <span className="text-[11px] text-zinc-600">仅作为输入，不做声音 QA</span>
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        {characters.map((character) => {
          const binding = bindings.find((item) => item.character_id === character.id)
          const boundAsset = binding
            ? project.document.assets.find((item) => item.id === binding.asset_id)
            : undefined
          const boundVersion = boundAsset ? approvedAudio(boundAsset) : undefined
          const preview = audioUrl(boundVersion)
          const busy = busyCharacterId === character.id
          const recording = recordingCharacterId === character.id
          const anotherRecording = recordingCharacterId !== null && !recording
          return (
            <article key={character.id} className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium text-zinc-100">{character.name}</p>
                  <p className="mt-0.5 text-[11px] text-zinc-600">
                    {boundAsset ? `当前：${boundAsset.name}` : '当前：H3 原生声音'}
                  </p>
                </div>
                <span className={`rounded-full border px-2 py-0.5 text-[10px] ${boundAsset ? 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300' : 'border-zinc-700 text-zinc-500'}`}>
                  {boundAsset ? '已绑定' : '可选'}
                </span>
              </div>

              {preview && (
                <audio className="mt-3 h-9 w-full" controls preload="metadata" src={preview}>
                  <track kind="captions" />
                </audio>
              )}

              <label className="mt-3 block text-[11px] text-zinc-500">使用已有的已批准音频资产</label>
              <select
                className={`${field} mt-1`}
                value={binding?.asset_id ?? ''}
                disabled={busy || recordingCharacterId !== null}
                onChange={(event) => void updateBinding(
                  character.id,
                  character.name,
                  event.target.value || null,
                )}
              >
                <option value="">H3 原生声音</option>
                {approvedAudioAssets.map(({ asset, version }) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.name} · v{version.version}
                  </option>
                ))}
              </select>

              <div className="mt-3 grid grid-cols-2 gap-2">
                <label className={`${secondaryButton} cursor-pointer ${busy || recordingCharacterId !== null ? 'pointer-events-none opacity-40' : ''}`}>
                  <FileAudio className="h-4 w-4" />上传音频
                  <input
                    className="hidden"
                    type="file"
                    accept="audio/wav,audio/mpeg,audio/mp4,audio/aac,audio/flac,audio/ogg,audio/webm,.m4a,.aac,.flac,.ogg,.webm"
                    disabled={busy || recordingCharacterId !== null}
                    onChange={(event) => {
                      const file = event.target.files?.[0]
                      event.target.value = ''
                      if (file) void uploadVoice(character.id, character.name, file)
                    }}
                  />
                </label>
                <button
                  type="button"
                  className={recording ? dangerButton : secondaryButton}
                  disabled={busy || anotherRecording}
                  onClick={() => recording
                    ? stopRecording()
                    : void startRecording(character.id, character.name)}
                >
                  {recording ? <Square className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
                  {recording ? '停止并保存' : '麦克风录制'}
                </button>
              </div>
              {recording && <p className="mt-2 text-[11px] text-red-300">正在录制，最长 14 秒；请只说清晰人声。</p>}
              {binding && (
                <button
                  type="button"
                  className="mt-2 inline-flex items-center gap-1.5 text-xs text-zinc-500 transition hover:text-red-300 disabled:opacity-40"
                  disabled={busy || recordingCharacterId !== null}
                  onClick={() => void updateBinding(character.id, character.name, null)}
                >
                  <Trash2 className="h-3.5 w-3.5" />解绑并恢复原生声音
                </button>
              )}
            </article>
          )
        })}
      </div>
      <p className="mt-3 text-[11px] leading-5 text-zinc-600">
        有对白的镜头会把对应角色音频作为 reference_audio 候选；若导演 Agent 为画面质量选择了互斥的关键帧路线，本镜头可以不使用该声线输入。
      </p>
    </section>
  )
}
