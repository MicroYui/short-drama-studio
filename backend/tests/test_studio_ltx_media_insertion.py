"""Actual LTX library insertion must save and render its original audio once."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from typing import cast

import imageio_ffmpeg
import numpy as np
from pydantic import JsonValue
import pytest

from short_drama.models import AssetKind
from short_drama.service import ShortDramaService
from short_drama.studio_editorial import EditorProject, read_editorial, validate_editorial
from short_drama.studio_export import start_export
from short_drama.studio_media import import_studio_media
from short_drama.studio_models import StudioExportRequest, StudioTimelineSave


@pytest.fixture(scope="module")
def insert_with_ltx(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, Path]:
    node = shutil.which("node")
    assert node is not None, "Use the installed Node runtime; do not install dependencies."
    root = Path(__file__).resolve().parents[2]
    bundle = tmp_path_factory.mktemp("ltx-insertion") / "insert.cjs"
    subprocess.run([node, "-e", """
const {createRequire} = require('node:module');
const {buildSync} = createRequire(require.resolve('vite'))('esbuild');
buildSync({stdin:{contents:`
import fs from 'node:fs';
import {projectSchema} from './frontend/types/project-model';
import {createInitialEditorState} from './frontend/views/editor/editor-state';
import {insertAssetsToTimeline} from './frontend/views/editor/editor-actions';
import {getEditorModel,updatedProject} from './frontend/views/editor/editor-project-bridging';
const project=projectSchema.parse(JSON.parse(fs.readFileSync(process.argv[2],'utf8')));
const state=createInitialEditorState(getEditorModel(project));
const next=insertAssetsToTimeline(state,{assets:[project.assets.find(asset=>asset.type==='video')],startTime:0});
fs.writeFileSync(process.argv[3],JSON.stringify(updatedProject(project,next.editorModel)));
`,resolveDir:process.cwd(),loader:'ts'},bundle:true,platform:'node',format:'cjs',outfile:process.argv[1]});
""", str(bundle)], cwd=root, check=True, capture_output=True, text=True)
    return node, bundle


@pytest.mark.parametrize("original_audio", [True, False], ids=["video-with-original-sound", "video-without-audio-stream"])
def test_ltx_video_library_insertion_saves_reopens_and_exports(
    tmp_path: Path, insert_with_ltx: tuple[str, Path], original_audio: bool,
) -> None:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    source = tmp_path / "video.mp4"
    command = [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x320:r=25:d=2"]
    if original_audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2", "-c:a", "aac", "-b:a", "192k"]
    else:
        command += ["-an"]
    subprocess.run([*command, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True, capture_output=True)
    service = ShortDramaService(database_path=tmp_path / "state.sqlite3", workspace_root=tmp_path / "media")
    project_id = service.initialize_fixture().document.project.id
    import_studio_media(service, project_id, source, name="抽屉视频", kind=AssetKind.REFERENCE, asset_id=None)
    project = EditorProject.model_validate(read_editorial(service, project_id).project)
    project.timelines[0].clips = []
    project.timelines[0].subtitles = []
    other = project.timelines[0].model_copy(deep=True, update={"id": "untouched", "name": "保留的备用时间线"})
    project.timelines.append(other)
    before, after = tmp_path / "before.json", tmp_path / "inserted.json"
    before.write_text(project.model_dump_json(), encoding="utf-8")
    node, bundle = insert_with_ltx
    subprocess.run([node, str(bundle), str(before), str(after)], check=True, capture_output=True, text=True)
    inserted = EditorProject.model_validate_json(after.read_text(encoding="utf-8"))
    video, audio = inserted.timelines[0].clips
    assert video.type == "video" and audio.type == "audio"
    assert video.assetId == audio.assetId
    assert (video.model_extra or {})["linkedClipIds"] == [audio.id]
    assert (audio.model_extra or {})["linkedClipIds"] == [video.id]
    unrelated = inserted.timelines[1].model_dump(mode="json")
    saved = service.studio.save_timeline(project_id, StudioTimelineSave(expected_revision=0,
        timeline={"project": cast(dict[str, JsonValue], inserted.model_dump(mode="json"))}))
    revision = cast(int, (saved.project.document.editorial_timeline or {})["revision"])
    reopened = EditorProject.model_validate(read_editorial(service, project_id).project)
    assert reopened.timelines[1].model_dump(mode="json") == unrelated
    assert reopened.timelines[0].clips[1].asset is not None and reopened.timelines[0].clips[1].asset.type == "video"
    # An audio view still uses the exact source window; accepting video assets
    # must not weaken the existing duration/binding contract.
    invalid = inserted.model_copy(deep=True)
    invalid.timelines[0].clips[1].duration = 3
    with pytest.raises(ValueError, match="超出了原素材时长"):
        validate_editorial(service, project_id, cast(dict[str, JsonValue], invalid.model_dump(mode="json")))
    result = start_export(service, project_id, StudioExportRequest(expected_revision=revision,
        width=320, height=320, fps=25, subtitles=False), background=False)
    assert result.status == "completed", result.error
    assert result.video_path and result.duration_seconds == 2
    output_pcm = subprocess.run([ffmpeg, "-v", "error", "-i", result.video_path,
        "-f", "s16le", "-ac", "2", "-ar", "48000", "pipe:1"], check=True, capture_output=True).stdout
    output = np.frombuffer(output_pcm, dtype="<i2").astype(np.float64)[9600:172800]
    if original_audio:
        source_pcm = subprocess.run([ffmpeg, "-v", "error", "-i", str(source),
            "-f", "s16le", "-ac", "2", "-ar", "48000", "pipe:1"], check=True, capture_output=True).stdout
        expected = np.frombuffer(source_pcm, dtype="<i2").astype(np.float64)[9600:172800]
        ratio = float(np.sqrt(np.mean(output ** 2)) / np.sqrt(np.mean(expected ** 2)))
        assert .95 < ratio < 1.05, f"Original sound must play once, measured RMS ratio={ratio}"
    else:
        assert float(np.max(np.abs(output))) == 0
