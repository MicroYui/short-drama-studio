import { useEffect, useRef, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { EditingSettingsProvider } from '../../contexts/AppSettingsContext'
import { projectSchema } from '../../types/project-model'
import { BUILT_IN_PRESETS } from '../../lib/keyboard-shortcuts'
import { ProgramMonitor } from '../../views/editor/ProgramMonitor'
import { getEditorModel } from '../../views/editor/editor-project-bridging'
import { EditorStoreProvider, useEditorStore } from '../../views/editor/editor-store'
import { selectCurrentTime, selectIsPlaying } from '../../views/editor/editor-selectors'
import { usePlaybackEngine } from '../../views/editor/usePlaybackEngine'
import { usePlaybackAudioSync } from '../../views/editor/usePlaybackAudioSync'
import { StudioApi } from './studio-api'
import { refreshPreviewSession, type StudioPreviewSession } from './studio-preview-refresh'

interface StudioEditorialPreviewProps { projectId: string; shotId?: string; revision: number; aspectRatio: number; showSubtitles?: boolean }

/** The same playback model as the editor, including splits, sound and subtitles. */
export function StudioEditorialPreview(props: StudioEditorialPreviewProps) {
  return <ScopedPreview key={JSON.stringify([props.projectId, props.shotId ?? null])} {...props} />
}

function ScopedPreview({ projectId, shotId, revision, aspectRatio, showSubtitles = true }: StudioEditorialPreviewProps) {
  const [session, setSession] = useState<StudioPreviewSession | null>(null)
  const sessionRef = useRef(session)
  const showSubtitlesRef = useRef(showSubtitles)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    showSubtitlesRef.current = showSubtitles
    const current = sessionRef.current
    if (!current) return
    sessionRef.current = refreshPreviewSession(current, current.sourceModel, current.revision, showSubtitles)
    setSession(sessionRef.current)
  }, [showSubtitles])
  useEffect(() => {
    let active = true
    setError(null)
    const request = shotId ? StudioApi.shotEditorial(projectId, shotId) : StudioApi.editorial(projectId)
    void request.then(result => {
      if (!active) return
      const project = projectSchema.parse(result.project)
      sessionRef.current = refreshPreviewSession(sessionRef.current, getEditorModel(project), result.revision, showSubtitlesRef.current, result.default_subtitle_font_family ?? null)
      setSession(sessionRef.current)
    }).catch(reason => { if (active) setError(String(reason)) })
    return () => { active = false }
  }, [projectId, shotId, revision])
  if (!session) return <div className="sd-video-placeholder">{error || <><Loader2 className="sd-spin" size={22} />载入当前剪辑…</>}</div>
  return <div className={shotId ? 'sd-current-shot-preview' : 'sd-current-film-preview'} aria-label={shotId ? '当前镜头剪辑预览' : '当前成片剪辑预览'}>
    {error && <div role="status">更新剪辑失败，继续显示已载入的内容：{error}</div>}
    <EditorStoreProvider key={session.sourceModel.activeTimelineId} store={session.store}><EditingSettingsProvider><Preview aspectRatio={aspectRatio} playbackTimeRef={session.playbackTimeRef} defaultSubtitleFontFamily={session.defaultSubtitleFontFamily} /></EditingSettingsProvider></EditorStoreProvider>
  </div>
}

function Preview({ aspectRatio, playbackTimeRef, defaultSubtitleFontFamily }: { aspectRatio: number; playbackTimeRef: { current: number }; defaultSubtitleFontFamily: string | null }) {
  const time = useEditorStore(selectCurrentTime)
  const playing = useEditorStore(selectIsPlaying)
  usePlaybackEngine({ playbackTimeRef })
  usePlaybackAudioSync({ playbackTimeRef })
  useEffect(() => { if (!playing) playbackTimeRef.current = time }, [time, playing])
  return <ProgramMonitor scaleSubtitles defaultSubtitleFontFamily={defaultSubtitleFontFamily} compositionAspectRatio={aspectRatio} playbackTimeRef={playbackTimeRef} setDraggingMarker={() => {}} kbLayout={BUILT_IN_PRESETS[0].layout} />
}
