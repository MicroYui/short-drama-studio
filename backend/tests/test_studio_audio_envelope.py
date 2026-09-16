"""Inspection PCM follows the real export mixer's playback-time envelope."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import wave

import imageio_ffmpeg
import numpy as np
from numpy.typing import NDArray
import pytest

from short_drama.studio_audio import _edited_audio
from short_drama.studio_editorial import EditorClip


@pytest.fixture(scope="module")
def export_mixer(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, Path]:
    node = shutil.which("node")
    assert node is not None, "Existing Node runtime is required; do not install dependencies"
    root = Path(__file__).resolve().parents[2]
    output = tmp_path_factory.mktemp("real-audio-mixer") / "audio-mix.cjs"
    # Resolve the already installed bundler exactly as the product's JS checks do.
    subprocess.run([node, "-e", """
const { createRequire } = require('node:module');
const { buildSync } = createRequire(require.resolve('vite'))('esbuild');
buildSync({entryPoints:['electron/export/audio-mix.ts'],bundle:true,platform:'node',
  target:'node20',format:'cjs',outfile:process.argv[1]});
""", str(output)], cwd=root, check=True, capture_output=True, text=True)
    return node, output


def _read_pcm(path: Path) -> NDArray[np.float64]:
    with wave.open(str(path), "rb") as wav:
        assert wav.getnchannels() == 1 and wav.getframerate() == 16000
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float64)


@pytest.mark.parametrize("settings", [
    {"duration": 1., "trimStart": .2},
    {"duration": 2., "trimStart": .2, "volume": .7, "audioFadeInSeconds": .8, "audioFadeOutSeconds": .6},
    {"duration": 1., "trimStart": .2, "audioFadeInSeconds": 1., "audioFadeOutSeconds": 1.},
    {"duration": .9, "trimStart": .6, "speed": 1.5, "reversed": True, "volume": .8,
     "audioFadeInSeconds": .8, "audioFadeOutSeconds": .7,
     "audioEnvelopeOffsetSeconds": .4, "audioEnvelopeDurationSeconds": 2.},
    {"duration": 1.5, "trimStart": .2, "speed": .5, "reversed": True,
     "audioFadeInSeconds": .5, "audioFadeOutSeconds": .5},
])
@pytest.mark.parametrize("channels", [1, 2])
def test_inspection_fades_match_export_pcm(
    tmp_path: Path, export_mixer: tuple[str, Path], settings: dict[str, float | bool], channels: int,
) -> None:
    node, mixer = export_mixer
    source = tmp_path / "source.wav"
    time = np.arange(3 * 48000, dtype=np.float64) / 48000
    # Two actual frequencies make speed/reverse ordering observable as well.
    signal = np.rint(8000 * np.sin(2 * np.pi * np.where(time < 1.3, 440, 880) * time)).astype("<i2")
    if channels == 2:
        # Unequal channel amplitudes exercise actual downmix, not identical mono.
        signal = np.column_stack((signal, signal // 2)).reshape(-1)
    with wave.open(str(source), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(signal.tobytes())
    fields: dict[str, object] = {"id": "observed", "assetId": "source", "type": "audio",
        "startTime": 0, "duration": 2., "trimStart": 0, "trackIndex": 0, **settings}
    clip = EditorClip.model_validate(fields)
    inspected, start = _edited_audio(clip, source, tmp_path / "inspection")
    assert start == 0
    request = tmp_path / "request.json"
    request.write_text(json.dumps({**clip.model_dump(mode="json"), "path": str(source)}), encoding="utf-8")
    raw = tmp_path / "export.raw"
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([node, "-e", """
const fs = require('node:fs');
const {mixAudioToPcm} = require(process.argv[1]);
const clip = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
mixAudioToPcm([clip], clip.duration, process.argv[4], true).then(result => {
  fs.writeFileSync(process.argv[3], result.pcmBuffer);
}).catch(error => {console.error(error);process.exitCode=1});
""", str(mixer), str(request), str(raw), ffmpeg], check=True, capture_output=True, text=True)
    exported = tmp_path / "export-mono.wav"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "s16le", "-ar", "48000", "-ac", "2",
        "-i", str(raw), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(exported)],
        check=True, capture_output=True, text=True)
    actual, expected = _read_pcm(Path(inspected)), _read_pcm(exported)
    assert len(actual) == len(expected) == round(clip.duration * 16000)
    # Mixer rounds at stereo/48 kHz, inspection at mono/16 kHz. Their envelope
    # and waveform must agree within ordinary PCM rounding/resampling noise.
    error = np.abs(actual - expected)
    assert float(np.sqrt(np.mean(error ** 2))) < 2, settings
    assert float(np.max(error)) < 12, settings
    assert float(np.max(np.abs(actual))) > 1000


def test_video_without_audio_keeps_absent_stream_evidence(tmp_path: Path) -> None:
    source = tmp_path / "silent-video.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi",
        "-i", "color=c=blue:s=64x36:r=24:d=1", "-an", "-c:v", "libx264", str(source)],
        check=True, capture_output=True, text=True)
    clip = EditorClip(id="silent", assetId="source", type="video", startTime=0,
        duration=.5, trimStart=.2, trackIndex=0, audioFadeInSeconds=.2)
    path, start = _edited_audio(clip, source, tmp_path / "inspection")
    assert path == str(source) and start == .2
    assert not list((tmp_path / "inspection").glob("*.wav"))
