import { useEffect, useRef, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { EditingSettingsProvider } from '../../contexts/AppSettingsContext'
import { projectSchema } from '../../types/project-model'
import { BUILT_IN_PRESETS } from '../../lib/keyboard-shortcuts'
import { ProgramMonitor } from '../../views/editor/ProgramMonitor'
import { createInitialEditorState } from '../../views/editor/editor-state'
import { getEditorModel } from '../../views/editor/editor-project-bridging'
import { createEditorStore, EditorStoreProvider, useEditorStore, type EditorStoreApi } from '../../views/editor/editor-store'
import { selectCurrentTime, selectIsPlaying } from '../../views/editor/editor-selectors'
import { usePlaybackEngine } from '../../views/editor/usePlaybackEngine'
import { usePlaybackAudioSync } from '../../views/editor/usePlaybackAudioSync'
import { StudioApi } from './studio-api'

/** The same playback model as the editor, including splits, sound and subtitles. */
export function StudioEditorialPreview({ projectId, shotId, revision, aspectRatio, showSubtitles = true }: { projectId: string; shotId?: string; revision: number; aspectRatio: number; showSubtitles?: boolean }) {
  const [store, setStore] = useState<EditorStoreApi | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    setStore(null); setError(null)
    const request = shotId ? StudioApi.shotEditorial(projectId, shotId) : StudioApi.editorial(projectId)
    void request.then(result => {
      const project = projectSchema.parse(result.project)
      if (!showSubtitles) for (const timeline of project.timelines) timeline.subtitles = []
      if (active) setStore(createEditorStore(createInitialEditorState(getEditorModel(project))))
    }).catch(reason => { if (active) setError(String(reason)) })
    return () => { active = false }
  }, [projectId, shotId, revision, showSubtitles])
  if (!store) return <div className="sd-video-placeholder">{error || <><Loader2 className="sd-spin" size={22} />载入当前剪辑…</>}</div>
  return <div className={shotId ? 'sd-current-shot-preview' : 'sd-current-film-preview'} aria-label={shotId ? '当前镜头剪辑预览' : '当前成片剪辑预览'}><EditorStoreProvider store={store}><EditingSettingsProvider><Preview aspectRatio={aspectRatio} /></EditingSettingsProvider></EditorStoreProvider></div>
}

function Preview({ aspectRatio }: { aspectRatio: number }) {
  const time = useEditorStore(selectCurrentTime)
  const playing = useEditorStore(selectIsPlaying)
  const playbackTimeRef = useRef(time)
  usePlaybackEngine({ playbackTimeRef })
  usePlaybackAudioSync({ playbackTimeRef })
  useEffect(() => { if (!playing) playbackTimeRef.current = time }, [time, playing])
  return <ProgramMonitor scaleSubtitles compositionAspectRatio={aspectRatio} playbackTimeRef={playbackTimeRef} setDraggingMarker={() => {}} kbLayout={BUILT_IN_PRESETS[0].layout} />
}
