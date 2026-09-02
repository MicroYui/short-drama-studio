"""Agent-callable video observation tools for temporal and boundary continuity."""

from __future__ import annotations

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
from pydantic import Field

from short_drama.media import MediaGenerationError, probe_media_with_ffmpeg
from short_drama.models import (
    ApprovalStatus,
    Asset,
    AssetMediaType,
    AssetVersion,
    DomainModel,
    ObservationConfidence,
    ProjectDocument,
    QaFailureCategory,
    QaIssue,
    ReferencePlanEntry,
    SemanticReviewStatus,
    ShotPlan,
    Take,
    TechnicalCheck,
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

    candidates: list[tuple[Asset, AssetVersion]] = []
    for binding in plan.bindings:
        resolved = version_index.get(binding.target_id)
        if resolved is not None:
            candidates.append(resolved)
    for asset_id in subject_ids:
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
        if version is not None:
            candidates.append((asset, version))

    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for asset, version in candidates:
        if (
            version.id in seen
            or version.approval_status != ApprovalStatus.APPROVED
            or version.media_type != AssetMediaType.IMAGE
        ):
            continue
        url: str | None = None
        if version.local_path is not None:
            path = Path(version.local_path).expanduser().resolve()
            if path.is_file() and path.stat().st_size > 0:
                url = _image_data_url(path)
        elif version.external_url is not None:
            url = version.external_url
        if url is None:
            continue
        result.append(
            (
                f"{asset.kind.value} canonical reference: {asset.name}; "
                f"approved version={version.id}; descriptor={version.descriptor}",
                url,
            )
        )
        seen.add(version.id)
        if len(result) >= 8:
            break
    return result


def semantic_reference_images(
    document: ProjectDocument,
    reference_plan: ReferencePlanEntry | None,
) -> list[tuple[str, str]]:
    """Return the same bounded canonical reference set used by video inspection."""

    return _semantic_reference_images(document, reference_plan)


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
    ) -> list[VideoFrameSample]:
        output_dir.mkdir(parents=True, exist_ok=True)
        candidate_count = min(
            72,
            max(max_frames * 3, math.ceil(probe.duration_seconds * 2.0)),
        )
        sample_fps = min(
            8.0,
            max(0.5, candidate_count / probe.duration_seconds),
        )
        with tempfile.TemporaryDirectory(prefix="short-drama-frames-") as temp_value:
            temp_dir = Path(temp_value)
            pattern = temp_dir / "candidate-%04d.jpg"
            _run_ffmpeg(
                [
                    "-y",
                    "-i",
                    str(media_path),
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
                raise MediaGenerationError(
                    f"Adaptive sampling produced fewer than two frames for {media_path}"
                )
            change_scores = self._change_scores(candidates)
            selected, uniform_indices, change_indices = self._select_indices(
                change_scores,
                max_frames=max_frames,
            )
            samples: list[VideoFrameSample] = []
            for sequence, index in enumerate(selected, start=1):
                timestamp = min(
                    probe.duration_seconds,
                    max(0.0, index / sample_fps),
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
    ) -> VideoInspectionReport:
        report_id = f"video-inspection-{uuid4().hex}"
        report_dir = self.workspace_root / document.project.id / "inspections" / report_id
        frames_dir = report_dir / "frames"
        report_dir.mkdir(parents=True, exist_ok=True)

        media_path = Path(take.media_path).expanduser().resolve()
        probe = self.probe_video(media_path)
        current_samples = self.sample_video(
            media_path=media_path,
            take_id=take.id,
            probe=probe,
            output_dir=frames_dir,
            max_frames=request.max_frames,
        )
        contact_sheet = report_dir / "intra-shot-contact-sheet.jpg"
        self._build_contact_sheet(
            current_samples,
            contact_sheet,
            title=f"{shot.id} · {take.id} · intra-shot timeline",
        )

        scopes = [VideoInspectionScope.INTRA_SHOT]
        boundary_sheet: Path | None = None
        all_samples = list(current_samples)
        if previous_take is not None and previous_shot is not None:
            previous_probe = self.probe_video(
                Path(previous_take.media_path).expanduser().resolve()
            )
            previous_boundary = self._extract_exact_samples(
                media_path=Path(previous_take.media_path).expanduser().resolve(),
                take_id=previous_take.id,
                duration_seconds=previous_probe.duration_seconds,
                timestamps=[
                    max(0.0, previous_probe.duration_seconds - 0.4),
                    max(
                        0.0,
                        previous_probe.duration_seconds
                        - max(0.05, 1.1 / previous_probe.fps),
                    ),
                ],
                output_dir=frames_dir,
                prefix="previous-boundary",
                reason="PREVIOUS_SHOT_END",
            )
            current_boundary = self._extract_exact_samples(
                media_path=media_path,
                take_id=take.id,
                duration_seconds=probe.duration_seconds,
                timestamps=[
                    min(0.05, max(0.0, probe.duration_seconds - 0.01)),
                    min(0.4, max(0.0, probe.duration_seconds - 0.01)),
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
                name="adaptive-sampling",
                passed=len(current_samples) >= 2,
                detail=(
                    f"Selected {len(current_samples)} frames from the full temporal span; "
                    "coverage and high-change samples are both retained."
                ),
            ),
        ]
        status = SemanticReviewStatus.NOT_REQUESTED
        observations: list[VideoObservation] = []
        issues: list[QaIssue] = []
        warnings: list[str] = []
        semantic_model: str | None = None
        summary = (
            "已完成媒体探测与适应性抽帧；该报告只提供可观察证据，不替代导演决策。"
        )
        if request.semantic_review:
            if language_client is None:
                status = SemanticReviewStatus.UNAVAILABLE
                warnings.append(
                    "GPT-5.6-sol 未配置；已保留抽帧与接触表，语义内容检查为 UNKNOWN。"
                )
            else:
                semantic_model = "gpt-5.6-sol"
                try:
                    review = self._semantic_review(
                        document=document,
                        shot=shot,
                        take=take,
                        previous_shot=previous_shot,
                        previous_take=previous_take,
                        request=request,
                        samples=all_samples,
                        contact_sheet=contact_sheet,
                        boundary_sheet=boundary_sheet,
                        client=language_client,
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
            scopes=scopes,
            duration_seconds=probe.duration_seconds,
            width=probe.width,
            height=probe.height,
            fps=probe.fps,
            audio_present=probe.audio_present,
            samples=all_samples,
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
    def _extract_exact_samples(
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
        contact_sheet: Path,
        boundary_sheet: Path | None,
        client: LanguageProviderClient,
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
        instructions = (
            "You are a video observation tool for a film-director agent. Return JSON only and "
            "match the supplied schema. Inspect the chronological contact sheets for visible "
            "intra-shot drift and, when provided, the previous-to-current shot boundary. Separate "
            "observation from inference. Do not invent a required action, release, prop state, "
            "screen direction, or camera intention that is absent from the supplied ShotPlan, "
            "continuity state, Bible, or user focus. Sparse frames cannot prove that an action never "
            "happened; lower confidence or omit the issue. Report an issue only when visible evidence "
            "conflicts with declared intent or with chronological state. The report is advisory input "
            "to another Agent, never a hard gate and never a repair decision. Use only LOW, MEDIUM, "
            "HIGH, or CRITICAL for issue severity. Calibrate severity by the consequence for the "
            "finished story, not by whether every requested directing detail was reproduced: HIGH "
            "means a central story action or chronological state is missing or contradicted, or a "
            "clear identity, contact, orientation, or spatial-continuity break makes the take unsafe "
            "to cut into the episode. MEDIUM means the main story beat and continuity remain readable "
            "but a secondary action, environmental motion, coverage preference, framing target, or "
            "polish detail was not achieved. Do not promote a secondary deviation to HIGH merely "
            "because it appears in the ShotPlan. Use stable generic QA categories rather than "
            "scene-specific fields. When canonical asset references are supplied, compare the visible "
            "character anchors declared by the project, required location relationships, and prop appearance against "
            "the relevant reference; report clear drift, but do not require details that are hidden "
            "or too small to judge."
        )
        reference_images = _semantic_reference_images(document, reference_plan)
        image_map: list[dict[str, object]] = [
            {
                "index": 1,
                "role": "CURRENT_TAKE_TIMELINE",
                "description": "chronological adaptive samples from the current take",
            }
        ]
        next_image_index = 2
        if boundary_sheet is not None:
            image_map.append(
                {
                    "index": next_image_index,
                    "role": "SHOT_BOUNDARY",
                    "description": "previous-shot end followed by current-shot start",
                }
            )
            next_image_index += 1
        for label, _url in reference_images:
            image_map.append(
                {
                    "index": next_image_index,
                    "role": "CANONICAL_ASSET_REFERENCE",
                    "description": label,
                }
            )
            next_image_index += 1
        payload = {
            "output_schema": SemanticVideoReview.model_json_schema(),
            "current_shot": shot.model_dump(mode="json"),
            "current_take": {
                "id": take.id,
                "provider": take.provider,
                "model": take.model,
                "route": take.route.value,
            },
            "previous_shot": (
                previous_shot.model_dump(mode="json") if previous_shot is not None else None
            ),
            "previous_take_id": previous_take.id if previous_take is not None else None,
            "bible": current_bible.model_dump(mode="json"),
            "reference_plan": (
                reference_plan.model_dump(mode="json")
                if reference_plan is not None
                else None
            ),
            "samples": [sample.model_dump(mode="json") for sample in samples],
            "allowed_issue_categories": [item.value for item in QaFailureCategory],
            "confidence_values": [item.value for item in ObservationConfidence],
            "review_focus": request.review_focus,
            "image_map": image_map,
            "notes": (
                "Frame labels are chronological within each sheet. VISUAL_CHANGE marks locally "
                "large image change, not an error by itself."
            ),
        }
        image_urls = [_image_data_url(contact_sheet)]
        if boundary_sheet is not None:
            image_urls.append(_image_data_url(boundary_sheet))
        image_urls.extend(url for _label, url in reference_images)
        output = client.generate_text(
            instructions=instructions,
            input_text=json.dumps(payload, ensure_ascii=False, indent=2),
            image_urls=image_urls,
            max_output_tokens=4000,
        )
        return SemanticVideoReview.model_validate_json(_strip_json_fence(output))
