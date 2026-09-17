"""Resolve registered inspection PCM without treating it as the original Take file."""

from __future__ import annotations

from dataclasses import dataclass
import filecmp
import math
from pathlib import Path
import wave

from short_drama.models import AssetVersion, OriginType, Take, VideoInspectionReport
from short_drama.studio_editorial import EditorClip


@dataclass(frozen=True)
class InspectionAudioSource:
    inspection_id: str
    take_id: str
    shot_id: str
    audio_path: str
    take_path: str
    source_in_seconds: float
    source_out_seconds: float


def inspection_audio_source(
    *, project_id: str, clip: EditorClip, path: str, take: Take | None,
    shot_id: str, reports: list[VideoInspectionReport],
    imported_version: AssetVersion | None = None,
) -> InspectionAudioSource | None:
    """Validate a saved report's exact file and map WAV-local trims to Take time.

    This is provenance for an editorial use, not approval or a TAKE_SOURCE review.
    The caller must still decode ``path`` with the clip's WAV-local trimStart.
    """
    media_path = Path(path).expanduser().resolve()
    lookup_path = media_path
    if imported_version is not None:
        source = imported_version.source
        if (imported_version.origin_type != OriginType.UPLOAD or source is None
                or not source.source_uri or "://" in source.source_uri):
            return None
        lookup_path = Path(source.source_uri).expanduser().resolve()
    matches = [report for report in reports if report.project_id == project_id
        and report.audio_excerpt is not None
        and Path(report.audio_excerpt.audio_path).expanduser().resolve() == lookup_path]
    if not matches:
        return None
    # The ordinary local importer copies the file. Metadata alone cannot make a
    # different or edited asset inherit that recording's Take clock.
    if imported_version is not None and (not lookup_path.is_file() or not media_path.is_file()
            or not filecmp.cmp(lookup_path, media_path, shallow=False)):
        return None
    report = matches[-1]
    excerpt = report.audio_excerpt
    assert excerpt is not None

    def invalid(reason: str) -> ValueError:
        return ValueError(f"Saved audio clip {clip.id} inspection {report.id}: {reason}")

    if (take is None or take.project_id != project_id or report.take_id != take.id
            or shot_id not in take.covered_shot_ids() or report.shot_id not in take.covered_shot_ids()):
        raise invalid("audio excerpt does not match the current project, Take and shot binding")
    take_path = Path(take.media_path).expanduser().resolve()
    source_media = report.source_context.get("take_media")
    if not isinstance(source_media, dict):
        raise invalid("audio excerpt has no recorded original Take file")
    recorded_path = source_media.get("path")
    if not isinstance(recorded_path, str):
        raise invalid("audio excerpt has no recorded original Take file")
    if Path(recorded_path).expanduser().resolve() != take_path or not take_path.is_file():
        raise invalid("original Take file is missing or no longer matches the recorded source")
    stat = take_path.stat()
    if source_media.get("size") != stat.st_size or source_media.get("modified_ns") != stat.st_mtime_ns:
        raise invalid("original Take file changed after the audio excerpt was extracted")
    start, end, duration = excerpt.source_in_seconds, excerpt.source_out_seconds, excerpt.duration_seconds
    observed_end = report.observed_source_out_seconds
    values = (start, end, duration, take.duration_seconds, report.duration_seconds,
        report.observed_source_in_seconds, clip.trimStart, clip.duration, clip.speed)
    if (not all(math.isfinite(value) for value in values) or start < 0 or end <= start or duration <= 0
            or abs(end - start - duration) > .001 or end > min(take.duration_seconds, report.duration_seconds) + .05
            or observed_end is None or not math.isfinite(observed_end)
            or abs(report.observed_source_in_seconds - start) > .001 or abs(observed_end - end) > .001):
        raise invalid("audio excerpt has an invalid or inconsistent recorded source interval")
    local_end = clip.trimStart + clip.duration * clip.speed
    if clip.trimStart < 0 or clip.duration <= 0 or clip.speed <= 0 or local_end > duration + .001:
        raise invalid("clip trim exceeds the registered audio excerpt's local duration")
    if not media_path.is_file():
        raise invalid("registered audio excerpt file is missing")
    try:
        with wave.open(str(media_path), "rb") as wav:
            actual_duration = wav.getnframes() / wav.getframerate()
            consistent = (wav.getframerate() == excerpt.sample_rate and wav.getnchannels() == excerpt.channels
                and abs(actual_duration - duration) <= max(.001, 1 / wav.getframerate()))
    except (OSError, EOFError, wave.Error) as error:
        raise invalid("registered audio excerpt is not a readable PCM WAV") from error
    if not consistent or local_end > actual_duration + .001:
        raise invalid("registered audio excerpt file does not match its recorded duration or format")
    return InspectionAudioSource(report.id, take.id, shot_id, str(media_path), str(take_path),
        start + clip.trimStart, start + local_end)
