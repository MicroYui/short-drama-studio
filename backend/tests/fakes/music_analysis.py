"""Deterministic substitute for the local music-analysis service."""

from __future__ import annotations

from pathlib import Path

from short_drama.models import AudioStructureMap, MasterAudioBinding


class FakeMusicAnalysisService:
    def __init__(self, *, bpm: float = 120.0) -> None:
        self.bpm = bpm
        self.calls: list[tuple[Path, MasterAudioBinding, str]] = []

    def analyze(
        self,
        *,
        audio_path: Path,
        binding: MasterAudioBinding,
        created_at: str,
    ) -> AudioStructureMap:
        self.calls.append((audio_path, binding.model_copy(deep=True), created_at))
        origin = binding.time_origin_seconds
        return AudioStructureMap(
            asset_id=binding.asset_id,
            asset_version_id=binding.asset_version_id,
            source_in_seconds=binding.source_in_seconds,
            source_out_seconds=binding.source_out_seconds,
            time_origin_seconds=origin,
            duration_seconds=binding.duration_seconds,
            analysis_level="RHYTHMIC",
            analyzer="fake-music-analysis",
            bpm=self.bpm,
            beats=[origin, origin + min(0.5, binding.duration_seconds)],
            onsets=[origin],
            file_size_bytes=audio_path.stat().st_size,
            notes=["Deterministic fake analysis for service-boundary tests."],
            created_at=created_at,
        )


__all__ = ["FakeMusicAnalysisService"]
