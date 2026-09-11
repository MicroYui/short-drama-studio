"""Real graphic placement, alpha compositing, timing, and sound export."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
from typing import cast

import imageio_ffmpeg
import pytest
from PIL import Image, ImageDraw
from pydantic import JsonValue

from short_drama.models import AssetKind
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorProject, read_editorial
from short_drama.studio_export import start_export
from short_drama.studio_media import import_studio_media, select_imported_video
from short_drama.studio_models import StudioExportRequest, StudioShotMediaSelect, StudioTimelineSave
from short_drama.tools import ToolCallStatus


@pytest.mark.parametrize("opacity, expected_red", [(0, 0), (50, 127)])
def test_video_opacity_exports_without_editor_metadata_and_preserves_audio(tmp_path: Path, opacity: int, expected_red: int) -> None:
    service = ShortDramaService(database_path=tmp_path/'state.sqlite3', workspace_root=tmp_path/'media')
    document = service.initialize_fixture().document
    pid = document.project.id
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    source = tmp_path/'opacity-source.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=red:s=480x270:r=24:d=1',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)], check=True)
    imported = import_studio_media(service, pid, source, name='透明度导出', kind=AssetKind.REFERENCE, asset_id=None)
    version = imported.document.assets[-1].versions[-1]
    select_imported_video(service, pid, document.shots[0].id,
        StudioShotMediaSelect(asset_version_id=version.id, expected_revision=document.shots[0].revision))
    current = read_editorial(service, pid)
    project = EditorProject.model_validate(current.project)
    for asset in project.assets:
        if asset.model_extra is not None:
            asset.model_extra.pop('prompt', None)
            asset.model_extra.pop('createdAt', None)
    for clip in project.timelines[0].clips:
        if clip.type == 'video':
            clip.opacity = opacity
            clip.muted = False
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision,
        timeline={'project':cast(dict[str, JsonValue], project.model_dump(mode='json'))}))
    saved = read_editorial(service, pid)
    exported = start_export(service, pid, StudioExportRequest(expected_revision=saved.revision, width=480, height=270), background=False)
    assert exported.status == 'completed', exported.error
    assert exported.video_path and exported.duration_seconds == 1
    assert read_editorial(service, pid) == saved
    frame = tmp_path/'opacity.png'
    subprocess.run([ffmpeg, '-v', 'error', '-y', '-ss', '0.4', '-i', exported.video_path, '-frames:v', '1', str(frame)], check=True)
    with Image.open(frame) as picture:
        red, green, blue = picture.convert('RGB').getpixel((240, 135))
        assert abs(red-expected_red) < 12 and green < 12 and blue < 12
    pcm = subprocess.run([ffmpeg, '-v', 'error', '-ss', '0.2', '-t', '0.3', '-i', exported.video_path,
        '-vn', '-f', 's16le', '-ac', '1', 'pipe:1'], capture_output=True, check=True).stdout
    from array import array
    samples = array('h'); samples.frombytes(pcm)
    assert samples and max(abs(value) for value in samples) > 1000


@pytest.mark.parametrize("scoped", [False, True])
def test_graphics_preserve_video_sound_timing_and_each_other(tmp_path: Path, scoped: bool) -> None:
    service = ShortDramaService(database_path=tmp_path/'state.sqlite3', workspace_root=tmp_path/'media')
    document = service.initialize_fixture().document
    pid = document.project.id
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    source = tmp_path/'picture.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=red:s=480x270:r=24:d=4',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=4', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)], check=True)
    imported = import_studio_media(service, pid, source, name='原画面', kind=AssetKind.REFERENCE, asset_id=None)
    media_version = imported.document.assets[-1].versions[-1]
    select_imported_video(service, pid, document.shots[0].id,
        StudioShotMediaSelect(asset_version_id=media_version.id, expected_revision=document.shots[0].revision))
    current = read_editorial(service, pid)
    project = EditorProject.model_validate(current.project)
    timeline = project.timelines[0]
    for clip in timeline.clips:
        if clip.type == 'video': clip.muted = False
    service.studio.save_timeline(pid, StudioTimelineSave(expected_revision=current.revision,
        timeline={'project':cast(dict[str, JsonValue], project.model_dump(mode='json'))}))
    initial = read_editorial(service,pid)
    original_clips = [(c.id,c.startTime,c.duration,c.trimStart) for c in timeline.clips]
    requests = []
    for index,color in enumerate(['yellow','blue']):
        path = tmp_path/f'{color}.png'
        with Image.new('RGBA',(480,270),(0,0,0,0)) as image:
            ImageDraw.Draw(image).rectangle((0,0,140,60),fill=color)
            image.save(path)
        added = import_studio_media(service,pid,path,name=color,kind=AssetKind.REFERENCE,asset_id=None)
        version = added.document.assets[-1].versions[-1]
        request = {'project_id':pid,'request':{'expected_revision':read_editorial(service,pid).revision,
            'overlay_id':f'graphic-{color}','asset_version_id':version.id,
            'start_seconds':.4 if index==0 else .8,'duration_seconds':1 if index==0 else .4,
            'shot_id':document.shots[0].id if scoped else None,
            'anchor_clip_id':timeline.clips[0].id if scoped else None}}
        result = service.tool_registry.invoke('timeline.add_asset_overlay',request)
        assert result.status == ToolCallStatus.SUCCEEDED, result.error
        requests.append(request)
    after = read_editorial(service,pid)
    new_timeline = EditorProject.model_validate(after.project).timelines[0]
    assert [(c.id,c.startTime,c.duration,c.trimStart) for c in new_timeline.clips if c.type != 'image'] == original_clips
    overlays = [c for c in new_timeline.clips if c.type=='image']
    assert len(overlays)==2 and len({c.trackIndex for c in overlays})==2
    repeated = service.tool_registry.invoke('timeline.add_asset_overlay',requests[0])
    assert repeated.status == ToolCallStatus.SUCCEEDED
    assert read_editorial(service,pid).revision == after.revision == initial.revision+2
    replacement_path = tmp_path/'replacement.png'
    with Image.new('RGBA',(480,270),(0,0,0,0)) as image:
        ImageDraw.Draw(image).rectangle((0,0,140,60),fill='lime')
        image.save(replacement_path)
    replacement = import_studio_media(service,pid,replacement_path,name='调整后的图版',kind=AssetKind.REFERENCE,asset_id=None)
    replacement_id = replacement.document.assets[-1].versions[-1].id
    revised_request = {**requests[0], 'request': {**requests[0]['request'], 'asset_version_id': replacement_id}}
    stale = service.tool_registry.invoke('timeline.add_asset_overlay', revised_request)
    assert stale.status == ToolCallStatus.FAILED
    assert read_editorial(service,pid).revision == after.revision
    revised_request['request']['expected_revision'] = after.revision
    changed = service.tool_registry.invoke('timeline.add_asset_overlay', revised_request)
    assert changed.status == ToolCallStatus.SUCCEEDED, changed.error
    after = read_editorial(service,pid)
    updated_timeline = EditorProject.model_validate(after.project).timelines[0]
    updated_overlays = [c for c in updated_timeline.clips if c.type=='image']
    assert len(updated_overlays)==2
    assert next(c for c in updated_overlays if c.id=='graphic-yellow').assetId==replacement_id
    assert [(c.id,c.trackIndex,c.startTime,c.duration) for c in updated_overlays] == [(c.id,c.trackIndex,c.startTime,c.duration) for c in overlays]
    assert service.tool_registry.invoke('timeline.add_asset_overlay', revised_request).status == ToolCallStatus.SUCCEEDED
    assert read_editorial(service,pid).revision == after.revision == initial.revision+3
    exported = start_export(service,pid,StudioExportRequest(expected_revision=after.revision,width=480,height=270),background=False)
    assert exported.status=='completed', exported.error
    assert exported.video_path and exported.duration_seconds==4
    for moment,expected in [(0.2,'red'),(.6,'lime'),(1.0,'blue'),(1.6,'red')]:
        frame = tmp_path/f'frame-{moment}.png'
        subprocess.run([ffmpeg,'-v','error','-y','-ss',str(moment),'-i',exported.video_path,'-frames:v','1',str(frame)],check=True)
        with Image.open(frame) as picture:
            red,green,blue = picture.convert('RGB').getpixel((20,20))
            if expected=='lime': assert green>190 and red<50 and blue<50
            elif expected=='blue': assert blue>190 and red<50 and green<50
            else: assert red>190 and green<50 and blue<50
            # Transparent pixels always reveal the original moving-picture layer.
            red,green,blue = picture.convert('RGB').getpixel((300,200))
            assert red>190 and green<50 and blue<50
    checks=json.loads(Path(exported.video_path).with_name('technical-check.json').read_text())
    assert all(checks['checks'].values())
    pcm=subprocess.run([ffmpeg,'-v','error','-ss','0.8','-t','0.3','-i',exported.video_path,'-vn','-f','s16le','-ac','1','pipe:1'],capture_output=True,check=True).stdout
    from array import array
    samples=array('h');samples.frombytes(pcm)
    assert samples and max(abs(value) for value in samples)>1000
