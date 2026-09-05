"""Provider-neutral local music analysis for audio-led production planning."""

from __future__ import annotations

import importlib
import mimetypes
import wave
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from short_drama.media import MediaGenerationError, probe_media_with_ffmpeg
from short_drama.models import (
    AudioEnergyPoint,
    AudioKeyMoment,
    AudioStructureMap,
    MasterAudioBinding,
)


class MusicAnalysisError(RuntimeError):
    """Raised when the selected local Master Audio cannot be inspected."""


class MusicAnalysisService(Protocol):
    def analyze(
        self,
        *,
        audio_path: Path,
        binding: MasterAudioBinding,
        created_at: str,
    ) -> AudioStructureMap: ...


class LocalMusicAnalysisService:
    """Use optional librosa when installed, otherwise report verified metadata only."""

    analyzer = "local-music-analysis-v1"

    def analyze(
        self,
        *,
        audio_path: Path,
        binding: MasterAudioBinding,
        created_at: str,
    ) -> AudioStructureMap:
        path = audio_path.expanduser().resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            raise MusicAnalysisError(f"Master Audio file is missing or empty: {path}")
        try:
            metadata = probe_media_with_ffmpeg(path)
        except (MediaGenerationError, OSError, ValueError) as exc:
            raise MusicAnalysisError(str(exc)) from exc
        if metadata["audio"] != "present":
            raise MusicAnalysisError("Master Audio asset contains no decodable audio stream")
        media_duration = float(metadata["duration"])
        if binding.source_out_seconds > media_duration + 0.05:
            raise MusicAnalysisError(
                "Master Audio source window exceeds the verified media duration"
            )

        sample_rate, channels = _wav_stream_metadata(path)
        basic = AudioStructureMap(
            asset_id=binding.asset_id,
            asset_version_id=binding.asset_version_id,
            source_in_seconds=binding.source_in_seconds,
            source_out_seconds=binding.source_out_seconds,
            time_origin_seconds=binding.time_origin_seconds,
            duration_seconds=binding.duration_seconds,
            analysis_level="BASIC_METADATA",
            analyzer=self.analyzer,
            sample_rate_hz=sample_rate,
            channels=channels,
            file_size_bytes=path.stat().st_size,
            mime_type=mimetypes.guess_type(path.name)[0],
            notes=[
                f"Verified decodable audio duration: {media_duration:.3f}s.",
                "Rhythmic fields are empty because no compatible local analysis library is available.",
            ],
            created_at=created_at,
        )

        try:
            librosa: Any = importlib.import_module("librosa")
        except ImportError:
            return basic
        try:
            return _analyze_with_librosa(
                librosa=librosa,
                audio_path=path,
                binding=binding,
                basic=basic,
            )
        except Exception as exc:
            # Optional analysis must not turn measured metadata into invented rhythm.
            basic.notes.append(
                f"Optional rhythmic analysis was unavailable: {type(exc).__name__}: {exc}"
            )
            return basic


def _wav_stream_metadata(path: Path) -> tuple[int | None, int | None]:
    try:
        with wave.open(str(path), "rb") as source:
            return source.getframerate(), source.getnchannels()
    except (OSError, wave.Error):
        return None, None


def _analyze_with_librosa(
    *,
    librosa: Any,
    audio_path: Path,
    binding: MasterAudioBinding,
    basic: AudioStructureMap,
) -> AudioStructureMap:
    audio, sample_rate = librosa.load(
        str(audio_path),
        sr=None,
        mono=True,
        offset=binding.source_in_seconds,
        duration=binding.duration_seconds,
    )
    signal = np.asarray(audio, dtype=np.float32)
    if signal.size == 0:
        basic.notes.append("Selected Master Audio window decoded to no samples.")
        return basic

    tempo, beat_frames = librosa.beat.beat_track(y=signal, sr=sample_rate)
    beat_times = librosa.frames_to_time(beat_frames, sr=sample_rate)
    onset_frames = librosa.onset.onset_detect(y=signal, sr=sample_rate)
    onset_times = librosa.frames_to_time(onset_frames, sr=sample_rate)
    rms = np.asarray(librosa.feature.rms(y=signal)[0], dtype=np.float64)
    rms_times = np.asarray(
        librosa.frames_to_time(np.arange(rms.size), sr=sample_rate),
        dtype=np.float64,
    )
    energy: list[AudioEnergyPoint] = []
    if rms.size:
        maximum = float(np.max(rms))
        sample_count = min(120, rms.size)
        for index in np.linspace(0, rms.size - 1, sample_count, dtype=int):
            energy.append(
                AudioEnergyPoint(
                    time_seconds=(
                        binding.time_origin_seconds + float(rms_times[index])
                    ),
                    normalized_energy=(
                        0.0 if maximum <= 0.0 else float(rms[index]) / maximum
                    ),
                )
            )

    onset_envelope = np.asarray(
        librosa.onset.onset_strength(y=signal, sr=sample_rate),
        dtype=np.float64,
    )
    key_moments: list[AudioKeyMoment] = []
    if onset_envelope.size:
        envelope_times = librosa.frames_to_time(
            np.arange(onset_envelope.size),
            sr=sample_rate,
        )
        count = min(12, onset_envelope.size)
        strongest = np.argsort(onset_envelope)[-count:]
        for index in sorted(strongest, key=lambda item: float(envelope_times[item])):
            key_moments.append(
                AudioKeyMoment(
                    time_seconds=(
                        binding.time_origin_seconds + float(envelope_times[index])
                    ),
                    kind="ONSET_PEAK",
                    description="Measured transient suitable for editorial consideration.",
                    strength=float(onset_envelope[index]),
                )
            )

    tempo_value = float(np.atleast_1d(tempo)[0])
    return basic.model_copy(
        update={
            "analysis_level": "RHYTHMIC",
            "bpm": tempo_value if tempo_value > 0.0 else None,
            "beats": [
                binding.time_origin_seconds + float(value) for value in beat_times
            ],
            "onsets": [
                binding.time_origin_seconds + float(value) for value in onset_times
            ],
            "energy": energy,
            "key_moments": key_moments,
            "sample_rate_hz": int(sample_rate),
            "channels": 1,
            "notes": [
                "BPM, beat, onset, energy, and key-moment values were measured locally with librosa.",
                "Semantic section labels were not inferred; sections remain empty.",
            ],
        }
    )


__all__ = [
    "LocalMusicAnalysisService",
    "MusicAnalysisError",
    "MusicAnalysisService",
]
