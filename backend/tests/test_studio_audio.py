"""Audio inspection uses saved editorial cuts even after upstream content changes."""
from __future__ import annotations

from pathlib import Path
import subprocess
from typing import cast
import wave

import imageio_ffmpeg
import numpy as np
from numpy.typing import NDArray
from pydantic import JsonValue

from short_drama.models import AssetKind, AudioInspectionRequest
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorProject, EditorSubtitle, read_editorial
from short_drama.studio_media import import_studio_media, select_imported_video
from short_drama.studio_models import StudioShotMediaSelect, StudioTimelineSave


def test_audio_inspection_uses_saved_trim_speed_reverse_volume_and_subtitles(tmp_path: Path) -> None:
    service = ShortDramaService(database_path=tmp_path/'state.sqlite3', workspace_root=tmp_path/'media')
    document = service.initialize_fixture().document
    pid = document.project.id
    samples = np.arange(64000, dtype=np.float64) / 16000
    signal = (np.sin(2*np.pi*np.where(samples < 2, 440, 880)*samples)*4096).astype('<i2')
    sound = tmp_path/'two-tones.wav'
    with wave.open(str(sound), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000); wav.writeframes(signal.tobytes())
    video = tmp_path/'source.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v','error','-y','-f','lavfi','-i',
        'color=c=blue:s=320x180:r=24:d=4','-i',str(sound),'-c:v','libx264','-c:a','aac','-shortest',str(video)],check=True)
    imported = import_studio_media(service,pid,video,name='已有镜头',kind=AssetKind.REFERENCE,asset_id=None)
    version = imported.document.assets[-1].versions[-1]
    select_imported_video(service,pid,document.shots[0].id,
        StudioShotMediaSelect(asset_version_id=version.id,expected_revision=document.shots[0].revision))
    saved = read_editorial(service,pid)
    project = EditorProject.model_validate(saved.project)
    timeline = project.timelines[0]
    clip = next(item for item in timeline.clips if item.type=='video')
    clip.trimStart=1; clip.duration=1.5; clip.speed=2; clip.reversed=True; clip.muted=False; clip.volume=.5
    script = document.script_versions[-1]
    line = script.lines[0]
    timeline.subtitles=[EditorSubtitle(id='manual-subtitle',text=line.subtitle_text,
        startTime=.2,endTime=1.3,trackIndex=3)]
    service.studio.save_timeline(pid,StudioTimelineSave(expected_revision=saved.revision,
        timeline={'project':cast(dict[str,JsonValue],project.model_dump(mode='json'))}))
    # No legacy manifest exists; the saved editor document is the current film.
    before = service.get_project(pid).document
    assert before.project.current_manifest_id is None
    result = service.inspect_current_manifest_audio(pid,AudioInspectionRequest(semantic_review=False,speaker_continuity=False))
    report = result.report
    assert report.source_editorial_revision == read_editorial(service,pid).revision
    assert report.source_editorial_timeline_id == timeline.id
    assert len(report.clip_inspections)==1 and len(report.dialogue_checks)==1
    assert report.dialogue_checks[0].script_line_id == line.id
    inspected=report.clip_inspections[0]
    assert inspected.metrics is not None and abs(inspected.metrics.duration_seconds-1.5)<.1
    # The exporter's mono -> stereo -> inspection-mono conversion is included.
    assert -28 < inspected.metrics.peak_dbfs < -26
    assert inspected.extracted_audio_path
    with wave.open(inspected.extracted_audio_path,'rb') as wav:
        data=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2').astype(np.float64)
    def frequency(window: NDArray[np.float64]) -> float:
        return float(np.fft.rfftfreq(len(window),1/16000)[np.argmax(abs(np.fft.rfft(window)))])
    assert abs(frequency(data[1600:6400])-880)<15
    assert abs(frequency(data[-6400:-1600])-440)<15
    after=service.get_project(pid).document
    assert after.editorial_timeline == before.editorial_timeline
    assert after.manifests == before.manifests
    assert after.shots == before.shots
    timeline.tracks[clip.trackIndex].muted=True
    service.studio.save_timeline(pid,StudioTimelineSave(expected_revision=read_editorial(service,pid).revision,
        timeline={'project':cast(dict[str,JsonValue],project.model_dump(mode='json'))}))
    muted=service.inspect_current_manifest_audio(pid,AudioInspectionRequest(semantic_review=False))
    assert muted.report.clip_inspections == []


def test_audio_inspection_reads_saved_empty_timeline_without_a_legacy_manifest(tmp_path: Path) -> None:
    from short_drama.studio_audio import editorial_audio_input
    service = ShortDramaService(database_path=tmp_path/'state.sqlite3',workspace_root=tmp_path/'media')
    document=service.initialize_fixture().document
    assert editorial_audio_input(service,document,'2026-09-11T00:00:00Z') is None
    saved=read_editorial(service,document.project.id)
    project=EditorProject.model_validate(saved.project)
    service.studio.save_timeline(document.project.id,StudioTimelineSave(expected_revision=0,
        timeline={'project':cast(dict[str,JsonValue],project.model_dump(mode='json'))}))
    before=service.get_project(document.project.id).document
    result=service.inspect_current_manifest_audio(document.project.id,AudioInspectionRequest(semantic_review=False))
    assert result.report.clip_inspections == []
    assert result.report.source_editorial_revision == 1
    assert service.get_project(document.project.id).document.editorial_timeline==before.editorial_timeline
