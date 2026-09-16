"""Agent-callable video observation tools for temporal and boundary continuity."""

from __future__ import annotations

from short_drama.language_defaults import LANGUAGE_MODEL

import base64
import json
import math
import mimetypes
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import imageio_ffmpeg
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat
from pydantic import Field, JsonValue

from short_drama.media import MediaGenerationError, probe_media_with_ffmpeg
from short_drama.video_detail import extract_detail_frames, extract_source_audio, sample_dense_window
from short_drama.models import (
    ApprovalStatus,
    Asset,
    AssetMediaType,
    AssetVersion,
    DomainModel,
    Freshness,
    ObservationConfidence,
    ProjectDocument,
    QaFailureCategory,
    QaIssue,
    ReferencePlanEntry,
    SemanticReviewStatus,
    ShotPlan,
    Take,
    TechnicalCheck,
    VideoEditorialContext,
    VideoEditorialUse,
    VideoFrameSample,
    VideoInspectionReport,
    VideoInspectionRequest,
    VideoInspectionScope,
    VideoObservation,
)
from short_drama.providers.openai_compatible_language_client import (
    LanguageProviderClient,
    LanguageProviderError,
)


@dataclass(frozen=True)
class VideoProbe:
    duration_seconds: float
    width: int
    height: int
    fps: float
    audio_present: bool


class SemanticVideoReview(DomainModel):
    observations: list[VideoObservation] = Field(
        default_factory=list[VideoObservation],
        max_length=24,
    )
    issues: list[QaIssue] = Field(default_factory=list[QaIssue], max_length=24)
    summary: str = Field(min_length=1, max_length=3000)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    )
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _run_ffmpeg(args: list[str]) -> None:
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    result = subprocess.run(
        [executable, "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown ffmpeg failure"
        raise MediaGenerationError(detail)


def _strip_json_fence(value: str) -> str:
    text = value.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _image_data_url(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    mime_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime_type};base64,{encoded}"


def image_data_url(path: Path) -> str:
    """Expose local inspection artifacts to other multimodal Agent tools."""

    return _image_data_url(path)


def _semantic_reference_images(
    document: ProjectDocument,
    reference_plan: ReferencePlanEntry | None,
) -> list[tuple[str, str]]:
    return [(label, url) for _version_id, label, url in semantic_reference_entries(document, reference_plan)]


def selected_generation_reference_ids(document: ProjectDocument, plan: ReferencePlanEntry) -> list[str]:
    shot = next((item for item in document.shots if item.id == plan.shot_id), None)
    cues = [] if shot is None else [*shot.references, *(cue for beat in shot.timeline_beats for cue in beat.references)]
    selected = [cue.asset_version_id for cue in cues if cue.usage == "GENERATION" and cue.asset_version_id is not None]
    explicit_subjects = {cue.subject_asset_id for cue in cues}
    # Current prose selects both the version and its use. An old plan can still
    # bind an earlier image of an object now used only in editorial, or replaced
    # by a different generation image. It must not revive either old choice.
    superseded_versions = {version.id for asset in document.assets if asset.id in explicit_subjects
                           for version in asset.versions}
    superseded_versions.update(entry.asset_version_id for pack in document.reference_packs
                               if pack.subject_asset_id in explicit_subjects for entry in pack.entries)
    superseded_versions.update(cue.asset_version_id for cue in cues if cue.asset_version_id is not None)
    return list(dict.fromkeys([*selected, *(binding.target_id for binding in plan.bindings
                                          if binding.target_id not in superseded_versions)]))


def semantic_reference_entries(
    document: ProjectDocument,
    reference_plan: ReferencePlanEntry | None,
    *,
    load_images: bool = True,
) -> list[tuple[str, str, str]]:
    """Observe selected generation media first; canonical images only fill missing subjects."""
    if reference_plan is None:
        return []
    plan = reference_plan
    asset_by_id = {asset.id: asset for asset in document.assets}
    version_index = {
        version.id: (asset, version)
        for asset in document.assets
        for version in asset.versions
    }
    subject_ids = {
        requirement.subject_asset_id for requirement in plan.requirements
    }
    subject_ids.update(beat.actor_asset_id for beat in plan.action_beats)
    for interaction in plan.interactions:
        subject_ids.add(interaction.actor_asset_id)
        subject_ids.add(interaction.target_asset_id)

    shot = next((item for item in document.shots if item.id == plan.shot_id), None)
    cues = [] if shot is None else [*shot.references, *(cue for beat in shot.timeline_beats for cue in beat.references)]
    generation_ids = set(selected_generation_reference_ids(document, plan))
    candidates: list[tuple[Asset, AssetVersion, str]] = []
    # Explicit editorial use or a not-yet-available generation choice must not
    # be replaced with an older canonical image during visual inspection.
    selected_subjects = {cue.subject_asset_id for cue in cues}
    for cue in cues:
        if cue.usage != "GENERATION" or cue.asset_version_id is None:
            continue
        resolved = version_index.get(cue.asset_version_id)
        if resolved is not None:
            asset, version = resolved
            candidates.append((asset, version, f"selected GENERATION reference; purpose={cue.purpose}"))
            selected_subjects.add(cue.subject_asset_id)
    for binding in plan.bindings:
        if binding.target_id not in generation_ids:
            continue
        resolved = version_index.get(binding.target_id)
        if resolved is not None:
            asset, version = resolved
            candidates.append((asset, version, f"selected GENERATION binding; purpose={binding.purpose}"))
            selected_subjects.add(asset.id)
    for asset_id in sorted(subject_ids - selected_subjects):
        asset = asset_by_id.get(asset_id)
        if asset is None or asset.approved_version_id is None:
            continue
        version = next(
            (
                item
                for item in asset.versions
                if item.id == asset.approved_version_id
            ),
            None,
        )
        if version is not None and version.approval_status == ApprovalStatus.APPROVED:
            candidates.append((asset, version, "canonical reference fallback"))

    result: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for asset, version, purpose in candidates:
        if (
            version.id in seen
            or version.approval_status == ApprovalStatus.REJECTED
            or version.freshness != Freshness.CURRENT
            or version.media_type != AssetMediaType.IMAGE
        ):
            continue
        url: str | None = None
        if version.local_path is not None:
            path = Path(version.local_path).expanduser().resolve()
            if path.is_file() and path.stat().st_size > 0:
                url = _image_data_url(path) if load_images else str(path)
        if url is None and version.external_url is not None:
            url = version.external_url
        if url is None:
            continue
        result.append(
            (
                version.id,
                f"{asset.kind.value} {purpose}: {asset.name}; shot={plan.shot_id}; "
                f"version={version.id}; state={version.approval_status.value}; descriptor={version.descriptor}",
                url,
            )
        )
        seen.add(version.id)
    return result


def semantic_reference_images(
    document: ProjectDocument,
    reference_plan: ReferencePlanEntry | None,
) -> list[tuple[str, str]]:
    """Return the selected, available generation references used by video inspection."""

    return _semantic_reference_images(document, reference_plan)


def inspection_shots(document: ProjectDocument, take: Take, start: float, end: float) -> list[ShotPlan]:
    ids = [window.shot_id for window in take.shot_windows
           if window.source_out_seconds > start and window.source_in_seconds < end]
    if not take.shot_windows:
        ids = [take.shot_id]
    by_id = {shot.id: shot for shot in document.shots}
    if any(shot_id not in by_id for shot_id in ids):
        raise ValueError("A grouped Take is missing a declared source shot for inspection")
    return [by_id[shot_id] for shot_id in ids]


def inspection_source_context(
    document: ProjectDocument, take: Take, shots: list[ShotPlan], previous_take: Take | None = None,
) -> dict[str, JsonValue]:
    """Small revision/file snapshot for ordinary report reuse, never a quality verdict."""
    ids = {shot.id for shot in shots}
    plans = [plan for plan in document.reference_plan if plan.shot_id in ids]
    versions = {version.id: version for asset in document.assets for version in asset.versions}
    reference_ids = list(dict.fromkeys(version_id for plan in plans
                                      for version_id, _label, _url in semantic_reference_entries(document, plan, load_images=False)))

    def media_state(path_value: str | None) -> dict[str, JsonValue]:
        if path_value is None:
            return {}
        path = Path(path_value).expanduser().resolve()
        if not path.is_file():
            return {"path": str(path), "available": False}
        stat = path.stat()
        return {"path": str(path), "size": stat.st_size, "modified_ns": stat.st_mtime_ns}

    return {
        "take_media": media_state(take.media_path),
        "shot_revisions": {shot.id: shot.revision for shot in shots},
        "shot_windows": [window.model_dump(mode="json") for window in take.shot_windows],
        "bible_version_id": document.project.current_bible_version_id,
        "reference_plans": [plan.model_dump(mode="json") for plan in plans],
        "unobserved_reference_version_ids": [version_id for plan in plans
                                              for version_id in selected_generation_reference_ids(document, plan)
                                              if version_id not in reference_ids
                                              and (version_id not in versions or versions[version_id].media_type == AssetMediaType.IMAGE)],
        "reference_media": [{"id": version_id, "media": media_state(versions[version_id].local_path),
                             "external_url": versions[version_id].external_url}
                            for version_id in reference_ids],
        "previous_take_id": previous_take.id if previous_take is not None else None,
        "previous_media": media_state(previous_take.media_path) if previous_take is not None else None,
        "previous_shot_revisions": {shot.id: shot.revision for shot in document.shots
                                    if previous_take is not None and shot.id in previous_take.covered_shot_ids()},
        "previous_shot_windows": [window.model_dump(mode="json") for window in previous_take.shot_windows]
        if previous_take is not None else [],
    }


def inspection_matches_request(
    report: VideoInspectionReport, *, document: ProjectDocument, take: Take,
    request: VideoInspectionRequest, previous_take: Take | None = None,
) -> bool:
    if request.scope != report.observation_scope:
        return False
    if request.scope == "EDITORIAL":
        from short_drama.observation_sources import video_report_source_status

        context = report.editorial_context
        if context is None or request.include_previous_boundary != context.include_previous_boundary or (request.clip_id is not None and context.current.clip_id != request.clip_id) or (
                request.timeline_id is not None and context.current.timeline_id != request.timeline_id):
            return False
        status, _reasons = video_report_source_status(document, take, report,
            {item.id: item for item in [take, previous_take] if item is not None})
        if status != "CURRENT":
            return False
        request = request.model_copy(update={"source_in_seconds": context.current.source_in_seconds,
                                             "source_out_seconds": context.current.source_out_seconds})
    end = request.source_out_seconds if request.source_out_seconds is not None else take.duration_seconds
    shots = inspection_shots(document, take, request.source_in_seconds, end)
    return (
        report.take_id == take.id
        and report.semantic_status == SemanticReviewStatus.COMPLETE
        and report.review_focus == request.review_focus
        and report.dense_sample_fps == request.dense_sample_fps
        and [frame.requested_timestamp_seconds for frame in report.detail_frames] == list(dict.fromkeys(request.keyframe_times_seconds))
        and (not request.keyframe_times_seconds or report.keyframe_max_dimension == request.keyframe_max_dimension)
        and all(Path(frame.image_path).is_file() for frame in report.detail_frames)
        and (not request.include_audio_excerpt or not report.audio_present
             or (report.audio_excerpt is not None and Path(report.audio_excerpt.audio_path).is_file()))
        and abs(report.observed_source_in_seconds - request.source_in_seconds) < 0.001
        and report.observed_source_out_seconds is not None
        and abs(report.observed_source_out_seconds - end) < 0.05
        and report.covered_shot_ids == [shot.id for shot in shots]
        and bool(report.source_context)
        and report.source_context == inspection_source_context(document, take, shots, previous_take)
        and Path(report.contact_sheet_path).is_file()
        and (previous_take is None or (report.boundary_contact_sheet_path is not None
                                      and Path(report.boundary_contact_sheet_path).is_file()))
    )


def boundary_frame_image_url(take: Take, source_end_seconds: float) -> str | None:
    """Read a current boundary without a model round-trip or an older inspection."""
    try:
        with tempfile.TemporaryDirectory(prefix="studio-input-boundary-") as temporary:
            directory = Path(temporary)
            inspector = VideoInspector(directory)
            media_path = Path(take.media_path).expanduser().resolve()
            probe = inspector.probe_video(media_path)
            samples = inspector.extract_exact_samples(
                media_path=media_path, take_id=take.id, duration_seconds=probe.duration_seconds,
                timestamps=[max(0.0, min(source_end_seconds, probe.duration_seconds) - max(0.05, 1.1 / probe.fps))],
                output_dir=directory, prefix="selected-previous", reason="SELECTED_PREVIOUS_SHOT_END",
            )
            return image_data_url(Path(samples[0].image_path))
    except (MediaGenerationError, OSError, ValueError):
        return None


class VideoInspector:
    """Provides probe, adaptive sampling, and semantic observation as separate tools."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root

    def probe_video(self, media_path: Path) -> VideoProbe:
        if not media_path.is_file() or media_path.stat().st_size == 0:
            raise MediaGenerationError(f"Video does not exist or is empty: {media_path}")
        metadata = probe_media_with_ffmpeg(media_path)
        try:
            width_text, height_text = metadata["size"].split("x", maxsplit=1)
            duration = float(metadata["duration"])
            fps = float(metadata["fps"])
            width = int(width_text)
            height = int(height_text)
        except (KeyError, ValueError) as exc:
            raise MediaGenerationError(
                f"Could not parse video metadata for {media_path}"
            ) from exc
        if duration <= 0 or fps <= 0 or width <= 0 or height <= 0:
            raise MediaGenerationError(f"Video metadata is incomplete for {media_path}")
        return VideoProbe(
            duration_seconds=duration,
            width=width,
            height=height,
            fps=fps,
            audio_present=metadata.get("audio") == "present",
        )

    def sample_video(
        self,
        *,
        media_path: Path,
        take_id: str,
        probe: VideoProbe,
        output_dir: Path,
        max_frames: int,
        source_in_seconds: float = 0.0,
        source_out_seconds: float | None = None,
    ) -> list[VideoFrameSample]:
        output_dir.mkdir(parents=True, exist_ok=True)
        end = source_out_seconds if source_out_seconds is not None else probe.duration_seconds
        if source_in_seconds >= probe.duration_seconds or end > probe.duration_seconds + 0.05 or end <= source_in_seconds:
            raise ValueError("Inspection source range must lie within the available Take")
        end = min(end, probe.duration_seconds)
        span = end - source_in_seconds
        candidate_count = min(
            72,
            max(max_frames * 3, math.ceil(span * 2.0)),
        )
        sample_fps = min(
            8.0,
            max(0.5, candidate_count / span),
        )
        with tempfile.TemporaryDirectory(prefix="short-drama-frames-") as temp_value:
            temp_dir = Path(temp_value)
            pattern = temp_dir / "candidate-%04d.jpg"
            _run_ffmpeg(
                [
                    "-y",
                    "-ss",
                    f"{source_in_seconds:.6f}",
                    "-i",
                    str(media_path),
                    "-t",
                    f"{span:.6f}",
                    "-an",
                    "-vf",
                    f"fps={sample_fps:.6f},scale=360:-2:flags=lanczos",
                    "-frames:v",
                    str(candidate_count),
                    "-q:v",
                    "3",
                    str(pattern),
                ]
            )
            candidates = sorted(temp_dir.glob("candidate-*.jpg"))
            if len(candidates) < 2:
                return self.extract_exact_samples(
                    media_path=media_path, take_id=take_id, duration_seconds=probe.duration_seconds,
                    timestamps=[source_in_seconds, max(source_in_seconds, end - min(0.001, span / 2))],
                    output_dir=output_dir, prefix="range-boundary", reason="SOURCE_RANGE_BOUNDARY",
                )
            change_scores = self._change_scores(candidates)
            selected, uniform_indices, change_indices = self._select_indices(
                change_scores,
                max_frames=max_frames,
            )
            samples: list[VideoFrameSample] = []
            for sequence, index in enumerate(selected, start=1):
                timestamp = min(
                    end,
                    source_in_seconds + index / sample_fps,
                )
                destination = output_dir / (
                    f"frame-{sequence:02d}-{round(timestamp * 1000):06d}ms.jpg"
                )
                shutil.copy2(candidates[index], destination)
                reasons: list[str] = []
                if index == 0:
                    reasons.append("BOUNDARY_START")
                if index == len(candidates) - 1:
                    reasons.append("BOUNDARY_END")
                if index in uniform_indices:
                    reasons.append("UNIFORM_COVERAGE")
                if index in change_indices:
                    reasons.append("VISUAL_CHANGE")
                if not reasons:
                    reasons.append("TEMPORAL_COVERAGE")
                samples.append(
                    VideoFrameSample(
                        source_take_id=take_id,
                        timestamp_seconds=round(timestamp, 3),
                        image_path=str(destination.resolve()),
                        reasons=reasons,
                        visual_change_score=round(change_scores[index], 4),
                    )
                )
            return samples

    def inspect_video(
        self,
        *,
        document: ProjectDocument,
        shot: ShotPlan,
        take: Take,
        previous_shot: ShotPlan | None,
        previous_take: Take | None,
        request: VideoInspectionRequest,
        language_client: LanguageProviderClient | None,
        created_at: str,
        editorial_context: VideoEditorialContext | None = None,
    ) -> VideoInspectionReport:
        report_id = f"video-inspection-{uuid4().hex}"
        report_dir = self.workspace_root / document.project.id / "inspections" / report_id
        frames_dir = report_dir / "frames"
        report_dir.mkdir(parents=True, exist_ok=True)

        media_path = Path(take.media_path).expanduser().resolve()
        probe = self.probe_video(media_path)
        source_end = request.source_out_seconds if request.source_out_seconds is not None else probe.duration_seconds
        if request.source_in_seconds >= probe.duration_seconds or source_end > probe.duration_seconds + 0.05:
            raise ValueError("Inspection source range must lie within the available Take")
        source_end = min(source_end, probe.duration_seconds)
        if (request.dense_sample_fps is not None or request.include_audio_excerpt) and source_end - request.source_in_seconds > 3.000001:
            raise ValueError("Dense sampling/audio excerpt requires an adopted interval of at most 3 seconds; use TAKE with an explicit shorter source range")
        if any(value < request.source_in_seconds or value >= source_end for value in request.keyframe_times_seconds):
            raise ValueError("Detail keyframe times must lie inside the adopted source interval")
        source_context = inspection_source_context(
            document, take, inspection_shots(document, take, request.source_in_seconds, source_end), previous_take,
        )
        if request.dense_sample_fps is not None:
            current_samples = sample_dense_window(source=media_path, take_id=take.id, output_dir=frames_dir,
                start=request.source_in_seconds, end=source_end, fps=request.dense_sample_fps)
        else:
            current_samples = self.sample_video(
                media_path=media_path, take_id=take.id, probe=probe, output_dir=frames_dir,
                max_frames=request.max_frames, source_in_seconds=request.source_in_seconds, source_out_seconds=source_end)
        if editorial_context is not None and editorial_context.current.reversed:
            current_samples.reverse()
        detail_frames = extract_detail_frames(source=media_path, take_id=take.id, output_dir=frames_dir,
            times=request.keyframe_times_seconds, start=request.source_in_seconds, end=source_end,
            source_fps=probe.fps, max_dimension=request.keyframe_max_dimension)
        audio_excerpt = extract_source_audio(source=media_path, destination=report_dir / "source-audio.wav",
            start=request.source_in_seconds, end=source_end) if request.include_audio_excerpt and probe.audio_present else None
        contact_sheet = report_dir / "intra-shot-contact-sheet.jpg"
        self._build_contact_sheet(
            current_samples,
            contact_sheet,
            title=f"{shot.id} · {take.id} · intra-shot timeline",
        )

        scopes = [VideoInspectionScope.INTRA_SHOT]
        boundary_sheet: Path | None = None
        all_samples = list(current_samples)

        def edge_times(use: VideoEditorialUse, *, end: bool, fps: float) -> list[float]:
            endpoint = use.playback_end_source_seconds if end else use.playback_start_source_seconds
            direction = -1 if use.reversed else 1
            distances = [.4 * use.speed, max(.05 * use.speed, 1.1 / fps)] if end else [max(.05 * use.speed, 1.1 / fps), .4 * use.speed]
            return [min(use.source_out_seconds - min(.001, (use.source_out_seconds - use.source_in_seconds) / 2),
                        max(use.source_in_seconds, endpoint + direction * (-value if end else value))) for value in distances]

        if previous_take is not None and previous_shot is not None:
            previous_probe = self.probe_video(
                Path(previous_take.media_path).expanduser().resolve()
            )
            previous_window = next((window for window in previous_take.shot_windows if window.shot_id == previous_shot.id), None)
            previous_end = min(previous_probe.duration_seconds, previous_window.source_out_seconds) if previous_window is not None else previous_probe.duration_seconds
            previous_boundary = self.extract_exact_samples(
                media_path=Path(previous_take.media_path).expanduser().resolve(),
                take_id=previous_take.id,
                duration_seconds=previous_probe.duration_seconds,
                timestamps=edge_times(editorial_context.previous, end=True, fps=previous_probe.fps)
                if editorial_context is not None and editorial_context.previous is not None else [
                    max(0.0, previous_end - 0.4),
                    max(
                        0.0,
                        previous_end
                        - max(0.05, 1.1 / previous_probe.fps),
                    ),
                ],
                output_dir=frames_dir,
                prefix="previous-boundary",
                reason="PREVIOUS_SHOT_END",
            )
            current_boundary = self.extract_exact_samples(
                media_path=media_path,
                take_id=take.id,
                duration_seconds=probe.duration_seconds,
                timestamps=edge_times(editorial_context.current, end=False, fps=probe.fps)
                if editorial_context is not None else [
                    min(request.source_in_seconds + 0.05, max(request.source_in_seconds, source_end - 0.01)),
                    min(request.source_in_seconds + 0.4, max(request.source_in_seconds, source_end - 0.01)),
                ],
                output_dir=frames_dir,
                prefix="current-boundary",
                reason="CURRENT_SHOT_START",
            )
            boundary_samples = [*previous_boundary, *current_boundary]
            all_samples.extend(boundary_samples)
            boundary_sheet = report_dir / "shot-boundary-contact-sheet.jpg"
            self._build_contact_sheet(
                boundary_samples,
                boundary_sheet,
                title=f"{previous_shot.id} -> {shot.id} · boundary continuity",
                columns=4,
            )
            scopes.append(VideoInspectionScope.SHOT_BOUNDARY)

        checks = [
            TechnicalCheck(
                name="media-decodable",
                passed=True,
                detail=f"{probe.width}x{probe.height}, {probe.fps:.3f} fps",
            ),
            TechnicalCheck(
                name="duration-positive",
                passed=True,
                detail=f"{probe.duration_seconds:.3f} seconds",
            ),
            TechnicalCheck(
                name="dense-sampling" if request.dense_sample_fps is not None else "adaptive-sampling",
                passed=len(current_samples) >= 1,
                detail=(
                    f"Selected {len(current_samples)} frames from source {request.source_in_seconds:.3f}–{source_end:.3f}s; "
                    + (f"Requested {request.dense_sample_fps:g} fps; returned frames carry actual source PTS."
                       if request.dense_sample_fps is not None else "Coverage and high-change samples are both retained.")
                ),
            ),
        ]
        status = SemanticReviewStatus.NOT_REQUESTED
        observations: list[VideoObservation] = []
        issues: list[QaIssue] = []
        warnings: list[str] = []
        if request.include_audio_excerpt:
            warnings.append("已保存该源区间实际声音，可独立播放；本报告未进行听审。" if audio_excerpt is not None
                            else "源视频没有音轨；未创建声音片段，也未进行听审。")
        if request.dense_sample_fps is not None:
            warnings.append("密集抽帧仍是离散静态画面，不能据此确认连续动作细节、对白或音画同步。")
        missing_references = source_context.get("unobserved_reference_version_ids")
        if missing_references:
            warnings.append("部分选中生成参考未能提供可观察图片：" + json.dumps(missing_references, ensure_ascii=False))
        if request.review_focus is not None or request.source_in_seconds > 0 or source_end < probe.duration_seconds - 0.05:
            warnings.append("本报告仅覆盖所记录的源区间和关注点，不能作为整组全部内容的检查结论。")
        semantic_model: str | None = None
        summary = (
            "已完成媒体探测与抽帧；该报告只提供可观察证据，不替代导演决策。"
        )
        if request.semantic_review:
            if language_client is None:
                status = SemanticReviewStatus.UNAVAILABLE
                warnings.append(
                    "语言模型 未配置；已保留抽帧与接触表，语义内容检查为 UNKNOWN。"
                )
            else:
                semantic_model = LANGUAGE_MODEL
                try:
                    review = self._semantic_review(
                        document=document,
                        shot=shot,
                        take=take,
                        previous_shot=previous_shot,
                        previous_take=previous_take,
                        request=request,
                        samples=all_samples,
                        detail_frames=detail_frames,
                        contact_sheet=contact_sheet,
                        boundary_sheet=boundary_sheet,
                        client=language_client,
                        editorial_context=editorial_context,
                    )
                    status = SemanticReviewStatus.COMPLETE
                    observations = review.observations
                    issues = review.issues
                    summary = review.summary
                except (LanguageProviderError, ValueError) as exc:
                    status = SemanticReviewStatus.FAILED
                    warnings.append(f"语义视频检查失败，抽帧证据仍可使用：{exc}")

        return VideoInspectionReport(
            id=report_id,
            project_id=document.project.id,
            shot_id=shot.id,
            take_id=take.id,
            previous_take_id=previous_take.id if previous_take is not None else None,
            review_focus=request.review_focus,
            observed_source_in_seconds=request.source_in_seconds,
            observed_source_out_seconds=min(source_end, probe.duration_seconds),
            covered_shot_ids=[item.id for item in inspection_shots(document, take, request.source_in_seconds, source_end)],
            full_take_coverage=(request.review_focus is None and request.source_in_seconds == 0
                                and source_end >= probe.duration_seconds - 0.05 and request.scope == "TAKE"),
            source_context=source_context,
            observation_scope=request.scope,
            editorial_context=editorial_context,
            scopes=scopes,
            duration_seconds=probe.duration_seconds,
            width=probe.width,
            height=probe.height,
            fps=probe.fps,
            audio_present=probe.audio_present,
            samples=all_samples,
            dense_sample_fps=request.dense_sample_fps,
            detail_frames=detail_frames,
            keyframe_max_dimension=request.keyframe_max_dimension,
            audio_excerpt=audio_excerpt,
            contact_sheet_path=str(contact_sheet.resolve()),
            boundary_contact_sheet_path=(
                str(boundary_sheet.resolve()) if boundary_sheet is not None else None
            ),
            technical_checks=checks,
            semantic_status=status,
            semantic_model=semantic_model,
            observations=observations,
            issues=issues,
            summary=summary,
            warnings=warnings,
            created_at=created_at,
        )

    @staticmethod
    def _change_scores(paths: list[Path]) -> list[float]:
        scores = [0.0]
        with Image.open(paths[0]) as first:
            previous = first.convert("RGB").resize((64, 64))
        for path in paths[1:]:
            with Image.open(path) as image:
                current = image.convert("RGB").resize((64, 64))
            difference = ImageChops.difference(previous, current)
            mean = ImageStat.Stat(difference).mean
            scores.append(min(1.0, sum(mean) / (3.0 * 255.0)))
            previous = current
        return scores

    @staticmethod
    def _select_indices(
        change_scores: list[float],
        *,
        max_frames: int,
    ) -> tuple[list[int], set[int], set[int]]:
        count = len(change_scores)
        if count <= max_frames:
            indices = list(range(count))
            return indices, set(indices), set()
        coverage_count = max(2, min(max_frames, math.ceil(max_frames * 0.6)))
        uniform = {
            round(position * (count - 1) / (coverage_count - 1))
            for position in range(coverage_count)
        }
        selected = set(uniform)
        change_selected: set[int] = set()
        ranked = sorted(
            range(1, count - 1),
            key=lambda index: (change_scores[index], -index),
            reverse=True,
        )
        for index in ranked:
            if len(selected) >= max_frames:
                break
            if change_scores[index] < 0.01:
                break
            if index in selected:
                continue
            selected.add(index)
            change_selected.add(index)
        if len(selected) < max_frames:
            for index in range(count):
                if len(selected) >= max_frames:
                    break
                selected.add(index)
        return sorted(selected), uniform, change_selected

    @staticmethod
    def extract_exact_samples(
        *,
        media_path: Path,
        take_id: str,
        duration_seconds: float,
        timestamps: list[float],
        output_dir: Path,
        prefix: str,
        reason: str,
    ) -> list[VideoFrameSample]:
        samples: list[VideoFrameSample] = []
        for index, raw_timestamp in enumerate(dict.fromkeys(timestamps), start=1):
            timestamp = min(
                max(0.0, raw_timestamp),
                max(0.0, duration_seconds - 0.001),
            )
            destination = output_dir / f"{prefix}-{index}-{round(timestamp * 1000):06d}ms.jpg"
            _run_ffmpeg(
                [
                    "-y",
                    "-i",
                    str(media_path),
                    "-ss",
                    f"{timestamp:.6f}",
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=360:-2:flags=lanczos,format=yuvj420p",
                    "-q:v",
                    "3",
                    str(destination),
                ]
            )
            if not destination.is_file() or destination.stat().st_size == 0:
                raise MediaGenerationError(
                    f"Could not extract boundary frame at {timestamp:.3f}s"
                )
            samples.append(
                VideoFrameSample(
                    source_take_id=take_id,
                    timestamp_seconds=round(timestamp, 3),
                    image_path=str(destination.resolve()),
                    reasons=[reason],
                )
            )
        return samples

    @staticmethod
    def _build_contact_sheet(
        samples: list[VideoFrameSample],
        destination: Path,
        *,
        title: str,
        columns: int = 4,
    ) -> None:
        cell_width = 252
        cell_height = 470
        title_height = 52
        rows = math.ceil(len(samples) / columns)
        canvas = Image.new(
            "RGB",
            (columns * cell_width, title_height + rows * cell_height),
            (10, 12, 17),
        )
        draw = ImageDraw.Draw(canvas)
        title_font = _font(20)
        label_font = _font(14)
        draw.text((14, 14), title, fill=(235, 240, 248), font=title_font)
        for index, sample in enumerate(samples):
            column = index % columns
            row = index // columns
            x = column * cell_width
            y = title_height + row * cell_height
            with Image.open(sample.image_path) as source:
                image = source.convert("RGB")
                image.thumbnail((cell_width - 16, cell_height - 62))
            image_x = x + (cell_width - image.width) // 2
            image_y = y + 42
            canvas.paste(image, (image_x, image_y))
            reasons = ",".join(sample.reasons)
            label = f"{index + 1:02d}  {sample.timestamp_seconds:.3f}s  {reasons}"
            draw.text((x + 8, y + 10), label, fill=(170, 195, 225), font=label_font)
            draw.rectangle(
                (x + 4, y + 4, x + cell_width - 4, y + cell_height - 4),
                outline=(45, 55, 70),
                width=1,
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(destination, format="JPEG", quality=90)

    @staticmethod
    def _semantic_review(
        *,
        document: ProjectDocument,
        shot: ShotPlan,
        take: Take,
        previous_shot: ShotPlan | None,
        previous_take: Take | None,
        request: VideoInspectionRequest,
        samples: list[VideoFrameSample],
        detail_frames: list[VideoFrameSample],
        contact_sheet: Path,
        boundary_sheet: Path | None,
        client: LanguageProviderClient,
        editorial_context: VideoEditorialContext | None = None,
    ) -> SemanticVideoReview:
        current_bible = next(
            (
                item
                for item in document.bible_versions
                if item.id == document.project.current_bible_version_id
            ),
            document.bible_versions[-1],
        )
        reference_plan = next(
            (item for item in document.reference_plan if item.shot_id == shot.id),
            None,
        )
        source_end = request.source_out_seconds if request.source_out_seconds is not None else take.duration_seconds
        covered_shots = inspection_shots(document, take, request.source_in_seconds, source_end)
        covered_ids = {item.id for item in covered_shots}
        reference_plans = [item for item in document.reference_plan if item.shot_id in covered_ids]
        instructions = (
            "你是供电影导演 Agent 调用的视频观察 Tool。只返回 JSON，并严格匹配所给 schema。"
            "检查按时间顺序排列的接触表，识别镜头内部可见的漂移；如果提供了前后镜头边界，也一并"
            "检查。把观察与推断分开。不要虚构 ShotPlan、连续性状态、Bible 或用户关注点中未声明的"
            "必要动作、释放动作、道具状态、画面方向或机位意图。稀疏抽帧无法证明某个动作从未发生；"
            "此时应降低置信度或不报告该问题。只有可见证据与已声明意图或时间顺序状态冲突时，才报告"
            "问题。报告只是提供给另一个 Agent 的建议性输入，不是硬门禁，也不替代修复决策。问题"
            "严重度只使用 LOW、MEDIUM、HIGH 或 CRITICAL，并根据它对成片故事的影响校准，而不是"
            "根据每个导演细节是否都被复现来判断：HIGH 表示核心叙事动作或时间顺序状态缺失、被"
            "否定，或者清晰的人物身份、接触关系、朝向或空间连续性断裂，使该 Take 不适合直接剪入"
            "本集；MEDIUM 表示主要叙事节拍和连续性仍然可读，但次要动作、环境运动、覆盖偏好、构图"
            "目标或润色细节没有实现。不要仅因为某项次要偏差写在 ShotPlan 中，就把它提升为 HIGH。"
            "使用稳定、通用的 QA 分类，不要创建场景专属字段。提供标准资产参考图时，将项目声明且"
            "在画面中可见的人物锚点、必要场景关系和道具外观与对应参考图比较；报告清晰漂移，但不要"
            "要求判断被遮挡或小到无法辨认的细节。"
            "inspection_target.scope 为 GENERATION_UNIT 时，当前 Take 是完整镜头组，"
            "按 shot_windows 和 current_shots 逐段观察，并以组的 requested_duration_seconds "
            "核对总时长；不能拿第一个编辑镜头的时长判断整组超长，已声明的组内切镜不等于漂移。"
            "当前输入只有静态图像，不能据此验证对白、声线或音画同步；未观察音频记为 UNKNOWN，"
            "不能仅由缺少音频观察证据推定实际音频错误或缺失。"
            "observed_source_range 是本次真正观察的源区间；review_focus 非空时仅回答该关注点。"
            "不得对区间之外或未观察的内容下结论，也不得把局部检查称为全片已通过。"
        )
        reference_images: list[tuple[str, str]] = []
        seen_reference_ids: set[str] = set()
        for plan in reference_plans:
            for version_id, label, url in semantic_reference_entries(document, plan):
                if version_id not in seen_reference_ids:
                    reference_images.append((label, url))
                    seen_reference_ids.add(version_id)
        image_map: list[dict[str, object]] = [
            {
                "index": 1,
                "role": "CURRENT_TAKE_TIMELINE",
                "description": "当前 Take 的定向密集抽帧" if request.dense_sample_fps is not None else "当前 Take 的按时间顺序自适应抽帧",
            }
        ]
        next_image_index = 2
        if boundary_sheet is not None:
            image_map.append(
                {
                    "index": next_image_index,
                    "role": "SHOT_BOUNDARY",
                    "description": "前一镜头结束帧，随后是当前镜头起始帧",
                }
            )
            next_image_index += 1
        for label, _url in reference_images:
            image_map.append(
                {
                    "index": next_image_index,
                    "role": "SELECTED_GENERATION_REFERENCE",
                    "description": label,
                }
            )
            next_image_index += 1
        for frame in detail_frames:
            image_map.append({"index": next_image_index, "role": "DETAIL_KEYFRAME",
                "description": f"实际源帧 {frame.timestamp_seconds:.6f}s，{frame.width}x{frame.height}；用于局部细节观察"})
            next_image_index += 1
        payload = {
            "output_schema": SemanticVideoReview.model_json_schema(),
            "inspection_target": {
                "scope": "GENERATION_UNIT" if take.shot_windows else "SHOT",
                "generation_unit_id": take.generation_unit_id,
                "requested_duration_seconds": max(window.source_out_seconds for window in take.shot_windows)
                if take.shot_windows else shot.duration_seconds,
                "shot_windows": [window.model_dump(mode="json") for window in take.shot_windows],
                "observed_source_range": {"start_seconds": request.source_in_seconds, "end_seconds": source_end},
            },
            "current_shot": None if take.shot_windows else shot.model_dump(mode="json"),
            "current_shots": [item.model_dump(mode="json") for item in covered_shots]
            if take.shot_windows else [],
            "current_take": {
                "id": take.id,
                "provider": take.provider,
                "model": take.model,
                "route": take.route.value,
                "actual_duration_seconds": take.duration_seconds,
            },
            "previous_shot": (
                previous_shot.model_dump(mode="json") if previous_shot is not None else None
            ),
            "previous_take_id": previous_take.id if previous_take is not None else None,
            "bible": current_bible.model_dump(mode="json"),
            "reference_plan": (
                reference_plan.model_dump(mode="json")
                if reference_plan is not None and not take.shot_windows
                else None
            ),
            "reference_plans": [item.model_dump(mode="json") for item in reference_plans]
            if take.shot_windows else [],
            "samples": [sample.model_dump(mode="json") for sample in samples],
            "detail_frames": [sample.model_dump(mode="json") for sample in detail_frames],
            "dense_sample_fps": request.dense_sample_fps,
            "allowed_issue_categories": [item.value for item in QaFailureCategory],
            "confidence_values": [item.value for item in ObservationConfidence],
            "review_focus": request.review_focus,
            "observation_scope": request.scope,
            "editorial_context": editorial_context.model_dump(mode="json") if editorial_context else None,
            "unobserved_reference_version_ids": [version_id for plan in reference_plans
                                                  for version_id in selected_generation_reference_ids(document, plan)
                                                  if version_id not in seen_reference_ids
                                                  and any(version.id == version_id and version.media_type == AssetMediaType.IMAGE
                                                          for asset in document.assets for version in asset.versions)],
            "image_map": image_map,
            "notes": (
                "每张接触表中的帧标签按时间顺序排列。VISUAL_CHANGE 只标记局部画面"
                "变化较大，本身不代表错误。"
            ),
        }
        image_urls = [_image_data_url(contact_sheet)]
        if boundary_sheet is not None:
            image_urls.append(_image_data_url(boundary_sheet))
        image_urls.extend(url for _label, url in reference_images)
        image_urls.extend(_image_data_url(Path(frame.image_path)) for frame in detail_frames)
        output = client.generate_text(
            instructions=instructions,
            input_text=json.dumps(payload, ensure_ascii=False, indent=2),
            image_urls=image_urls,
            # The same allowance covers xhigh reasoning and the structured review.
            # Match asset inspection so reasoning does not consume the whole response.
            max_output_tokens=16000,
        )
        return SemanticVideoReview.model_validate_json(_strip_json_fence(output))
